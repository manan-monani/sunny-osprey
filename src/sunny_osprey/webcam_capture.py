"""
Webcam Capture Loop - Core pipeline for laptop camera monitoring.

This replaces Frigate + RTSP + NVDEC in the original pipeline.
Runs natively on Windows (no Docker) because Docker/WSL2 cannot
access the laptop webcam via USB passthrough.

Pipeline:
    WebcamCapture → MotionDetector → FrameBuffer → OllamaInference → AlertManager
"""

import json
import logging
import os
import sys
import time
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

import cv2
import numpy as np
import requests


# ── Allow running directly from project root ────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
# ─────────────────────────────────────────────────────────────────────────────


class WebcamPipeline:
    """
    Full surveillance pipeline using laptop webcam.

    Orchestrates motion detection → frame buffering → LLM inference → alerting.
    """

    def __init__(self, config_path: str = "sunny-osprey-config.yaml"):
        """
        Initialize the webcam pipeline.

        Args:
            config_path: Path to sunny-osprey-config.yaml
        """
        from sunny_osprey.config import SunnyOspreyConfig
        from sunny_osprey.motion_detector import MotionDetector
        from sunny_osprey.frame_buffer import FrameBuffer
        from sunny_osprey.ollama_inference import OllamaInferenceEngine
        from sunny_osprey.alert_manager import AlertManager

        self.logger = logging.getLogger(__name__)

        # Load config
        self.config_obj = SunnyOspreyConfig(config_path)
        self.config = self.config_obj.config

        # Webcam settings
        webcam_cfg = self.config.get("webcam", {})
        # A device index (0 = built-in webcam) or a stream URL such as rtsp://mediamtx:8554/webcam,
        # which is how the camera reaches the pipeline when it runs inside Docker.
        self.device_id = os.getenv("WEBCAM_SOURCE") or webcam_cfg.get("device_id", 0)
        if isinstance(self.device_id, str) and self.device_id.isdigit():
            self.device_id = int(self.device_id)
        self.fps = webcam_cfg.get("fps", 15)
        self.width = webcam_cfg.get("width", 1280)
        self.height = webcam_cfg.get("height", 720)

        # Motion settings
        motion_cfg = self.config.get("motion", {})
        self.cooldown = motion_cfg.get("cooldown_seconds", 12.0)
        self.clip_duration = motion_cfg.get("clip_duration_seconds", 5)

        # Dashboard API URL (for posting events)
        dashboard_cfg = self.config.get("dashboard", {})
        self.dashboard_url = os.getenv("DASHBOARD_URL") or dashboard_cfg.get(
            "url", "http://localhost:8080"
        )

        # Initialize components
        self.motion_detector = MotionDetector(
            min_area=motion_cfg.get("min_area", 1500),
            var_threshold=motion_cfg.get("sensitivity", 50),
            cooldown_seconds=self.cooldown,
        )

        self.frame_buffer = FrameBuffer(
            duration_seconds=float(self.clip_duration),
            fps=self.fps,
            post_event_frames=int(self.fps * 2),  # 2 seconds post-event
        )

        llm_config = self.config_obj.get_llm_config()
        prompt_file = llm_config.get("prompt_file", "prompt.txt")
        self.inference_engine = OllamaInferenceEngine(
            prompt_file=prompt_file,
            config=self.config,
        )

        alerts_cfg = self.config_obj.get_alerts_config()
        self.alert_manager = AlertManager(alerts_cfg)

        # State
        self.running = False
        self.cap: Optional[cv2.VideoCapture] = None
        self.inference_thread: Optional[threading.Thread] = None
        self._pending_inference = False  # Lock to prevent concurrent inferences

        # Stats
        self.events_processed = 0
        self.suspicious_count = 0
        self.start_time = time.time()

        self.logger.info("WebcamPipeline initialized ✅")

    def _init_camera(self) -> bool:
        """Initialize OpenCV video capture for webcam."""
        self.logger.info(f"Opening webcam device {self.device_id}...")
        if isinstance(self.device_id, str):
            self.cap = cv2.VideoCapture(self.device_id)  # network stream (FFmpeg backend)
        else:
            self.cap = cv2.VideoCapture(self.device_id, cv2.CAP_DSHOW)  # CAP_DSHOW = Windows

        if not self.cap.isOpened():
            # Fallback without DSHOW backend
            self.cap = cv2.VideoCapture(self.device_id)

        if not self.cap.isOpened():
            self.logger.error(
                f"❌ Cannot open webcam device {self.device_id}. "
                f"Check that no other app is using the camera."
            )
            return False

        # Configure resolution and FPS
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self.cap.set(cv2.CAP_PROP_FPS, self.fps)

        actual_w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = self.cap.get(cv2.CAP_PROP_FPS)

        self.logger.info(
            f"✅ Webcam opened: {actual_w}x{actual_h} @ {actual_fps:.0f}fps"
        )
        return True

    def _run_inference_async(self, frames, event_id: str):
        """
        Run LLM inference in a background thread so the capture loop isn't blocked.

        Args:
            frames: List of (PIL.Image, timestamp) tuples
            event_id: Unique event identifier
        """
        try:
            self.logger.info(f"🧠 Starting LLM inference for event {event_id}")
            result = self.inference_engine.analyze_frames(frames)

            if result:
                result["event_id"] = event_id
                result["timestamp"] = datetime.utcnow().isoformat()
                result["source"] = "webcam"

                is_suspicious = result.get("suspicious", "No").lower() == "yes"

                self.events_processed += 1
                if is_suspicious:
                    self.suspicious_count += 1

                self.logger.info(
                    f"📊 Event {event_id}: suspicious={result.get('suspicious')} | "
                    f"Total: {self.events_processed} | Suspicious: {self.suspicious_count}"
                )
                self.logger.info(f"Description: {result.get('description', '')}")

                # Post event to dashboard
                self._post_event_to_dashboard(result)

                # Send alert if suspicious
                self.alert_manager.send_incident(event_id, result)

        except Exception as e:
            self.logger.error(f"Inference error for event {event_id}: {e}")
        finally:
            self._pending_inference = False

    def _post_event_to_dashboard(self, result: Dict[str, Any]):
        """Send analysis result to the dashboard API."""
        try:
            resp = requests.post(
                f"{self.dashboard_url}/api/events",
                json=result,
                timeout=5,
            )
            if resp.status_code == 200:
                self.logger.debug(f"Event posted to dashboard ✅")
            else:
                self.logger.warning(
                    f"Dashboard returned {resp.status_code}: {resp.text[:100]}"
                )
        except requests.exceptions.ConnectionError:
            self.logger.debug(
                "Dashboard not reachable (is it running?). Continuing without UI sync."
            )
        except Exception as e:
            self.logger.warning(f"Failed to post event to dashboard: {e}")

    def _post_frame_to_dashboard(self, frame: np.ndarray):
        """Send the latest webcam frame to the dashboard for live preview."""
        try:
            _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
            import base64
            frame_b64 = base64.b64encode(buf.tobytes()).decode()
            requests.post(
                f"{self.dashboard_url}/api/frame",
                json={"frame": frame_b64},
                timeout=1,
            )
        except Exception:
            pass  # Non-critical — silently skip

    def run(self, show_preview: bool = True):
        """
        Start the main capture loop.

        Args:
            show_preview: Show a local OpenCV preview window (disable for headless).
        """
        self.logger.info("🚀 Starting Sunny Osprey Webcam Pipeline")
        self.logger.info("=" * 60)

        # Check Ollama connectivity
        self.logger.info("🔌 Checking Ollama connectivity...")
        self.inference_engine._initialize_model()

        # Open camera (a stream may not be published yet, so keep trying for URLs)
        while not self._init_camera():
            if not isinstance(self.device_id, str):
                return
            self.logger.info("Waiting for the camera stream (publish it at http://localhost:8889/webcam/publish)...")
            time.sleep(5)
        read_failures = 0

        self.running = True
        frame_interval = 1.0 / self.fps
        last_frame_time = 0.0
        dashboard_frame_interval = 0.5  # Push to dashboard at 2fps
        last_dashboard_push = 0.0

        self.logger.info("👁️  Monitoring started. Press Ctrl+C to stop.")
        self.logger.info(f"📋 Config: cooldown={self.cooldown}s, device={self.device_id}")

        try:
            while self.running:
                now = time.time()

                # Throttle to desired FPS
                if (now - last_frame_time) < frame_interval:
                    time.sleep(0.001)
                    continue

                ret, frame = self.cap.read()
                if not ret:
                    read_failures += 1
                    if read_failures % 50 == 0:
                        # Camera unplugged / stream ended: reopen instead of retrying a dead handle
                        self.logger.warning("Camera stopped delivering frames, reconnecting...")
                        self.cap.release()
                        time.sleep(2)
                        self._init_camera()
                    else:
                        self.logger.debug("Failed to read frame, retrying...")
                        time.sleep(0.1)
                    continue
                read_failures = 0

                last_frame_time = now

                # Add frame to rolling buffer
                clip_ready = self.frame_buffer.add(frame)

                # Detect motion (pre-filter)
                motion_detected, fg_mask = self.motion_detector.detect(frame)

                # If motion triggered AND no inference currently running
                if motion_detected and not self._pending_inference:
                    event_id = f"webcam_{uuid.uuid4().hex[:8]}"
                    self.logger.info(f"🎯 Motion trigger → scheduling inference [{event_id}]")

                    # Signal frame buffer to capture post-event frames too
                    self.frame_buffer.trigger()

                    # Get current frames from buffer (pre-event context)
                    clip_frames = self.frame_buffer.get_clip_frames(num_frames=10)

                    if clip_frames and self.frame_buffer.is_warmed_up:
                        self._pending_inference = True
                        self.inference_thread = threading.Thread(
                            target=self._run_inference_async,
                            args=(clip_frames, event_id),
                            daemon=True,
                        )
                        self.inference_thread.start()
                    else:
                        self.logger.debug(
                            "Buffer not warmed up yet, skipping inference"
                        )

                # Push frame to dashboard periodically
                if (now - last_dashboard_push) > dashboard_frame_interval:
                    self._post_frame_to_dashboard(frame)
                    last_dashboard_push = now

                # Show local preview window
                if show_preview:
                    debug_frame = self.motion_detector.draw_debug_overlay(
                        frame, fg_mask, motion_detected
                    )

                    # Show inference status
                    if self._pending_inference:
                        cv2.putText(
                            debug_frame, "⏳ Analyzing...", (10, 60),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 165, 255), 2,
                        )

                    cv2.imshow("Sunny Osprey - Webcam Monitor", debug_frame)

                    key = cv2.waitKey(1) & 0xFF
                    if key == ord("q") or key == 27:  # q or ESC to quit
                        self.logger.info("User requested stop (q/ESC)")
                        break
                    elif key == ord("r"):  # r to reset background model
                        self.motion_detector.reset_background()
                        self.logger.info("Background model reset by user")

        except KeyboardInterrupt:
            self.logger.info("\n⛔ Stopped by user (Ctrl+C)")
        finally:
            self.stop()

    def stop(self):
        """Release resources and stop the pipeline."""
        self.running = False
        if self.cap:
            self.cap.release()
        cv2.destroyAllWindows()

        uptime = time.time() - self.start_time
        self.logger.info("=" * 60)
        self.logger.info("📈 Session Summary:")
        self.logger.info(f"   Uptime: {uptime/60:.1f} minutes")
        self.logger.info(f"   Events processed: {self.events_processed}")
        self.logger.info(f"   Suspicious detections: {self.suspicious_count}")
        self.logger.info(f"   Motion triggers: {self.motion_detector.triggered_count}")
        self.logger.info("=" * 60)


def main():
    """Entry point for the webcam pipeline."""
    import argparse

    parser = argparse.ArgumentParser(description="Sunny Osprey Webcam Monitor")
    parser.add_argument(
        "--config",
        default="sunny-osprey-config.yaml",
        help="Path to config file",
    )
    parser.add_argument(
        "--no-preview",
        action="store_true",
        help="Run headless (no OpenCV preview window)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    pipeline = WebcamPipeline(config_path=args.config)
    pipeline.run(show_preview=not args.no_preview)


if __name__ == "__main__":
    main()
