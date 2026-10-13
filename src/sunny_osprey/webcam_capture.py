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

    def __init__(self, config_path: str = "gazeguard-config.yaml"):
        """
        Initialize the webcam pipeline.

        Args:
            config_path: Path to gazeguard-config.yaml
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
        # Frames are downscaled to this width right after capture (motion detection + buffer)
        self.process_width = webcam_cfg.get("process_width", 768)

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
        self._codec_warned = False
        self.frames_since_open = 0
        self.inference_thread: Optional[threading.Thread] = None
        self._pending_inference = False  # Lock to prevent concurrent inferences

        # Stats
        self.events_processed = 0
        self.suspicious_count = 0
        self.start_time = time.time()

        self.logger.info("WebcamPipeline initialized ✅")

    def _init_camera(self) -> bool:
        """Initialize OpenCV video capture for webcam."""
        is_stream = isinstance(self.device_id, str)
        # Streams are re-polled every few seconds while unpublished, so keep that quiet
        (self.logger.debug if is_stream else self.logger.info)(f"Opening webcam device {self.device_id}...")
        if is_stream:
            self.cap = cv2.VideoCapture(self.device_id)  # network stream (FFmpeg backend)
        else:
            self.cap = cv2.VideoCapture(self.device_id, cv2.CAP_DSHOW)  # CAP_DSHOW = Windows

        if not self.cap.isOpened():
            # Fallback without DSHOW backend
            self.cap = cv2.VideoCapture(self.device_id)

        if not self.cap.isOpened():
            if isinstance(self.device_id, str):
                return False  # stream not published yet; the caller keeps waiting
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

        if is_stream and actual_w == 0:
            self._warn_undecodable_stream()
            self.cap.release()
            return False
        self.frames_since_open = 0

        self.logger.info(
            f"✅ Webcam opened: {actual_w}x{actual_h} @ {actual_fps:.0f}fps"
        )
        return True

    def _warn_undecodable_stream(self):
        """Explain (once) a stream that opens but yields no frames, e.g. a browser publishing AV1."""
        if self._codec_warned:
            return
        fourcc = int(self.cap.get(cv2.CAP_PROP_FOURCC)) if self.cap else 0
        codec = "".join(chr((fourcc >> 8 * i) & 0xFF) for i in range(4)).strip(chr(0) + " ") or "unknown"
        self.logger.warning(
            f"⚠️ The camera stream (codec: {codec}) cannot be decoded. Re-publish it with H.264 using the "
            f"camera link opened by start.bat (it sets video-codec=h264); browsers default to AV1."
        )
        self._codec_warned = True

    def _wait_for_camera(self) -> bool:
        """Open the camera. Stream URLs are retried until published; returns False to give up."""
        logged = False
        while not self._init_camera():
            if not isinstance(self.device_id, str):
                return False
            if not logged:
                self.logger.info("⏳ Waiting for the camera stream (publish it with the camera link from start.bat)")
                logged = True
            time.sleep(5)
        return True

    def _downscale(self, frame: np.ndarray) -> np.ndarray:
        """Shrink a frame to process_width (keeping aspect ratio); smaller frames pass through."""
        h, w = frame.shape[:2]
        if w <= self.process_width:
            return frame
        new_h = int(h * self.process_width / w)
        return cv2.resize(frame, (self.process_width, new_h), interpolation=cv2.INTER_AREA)

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
        self.logger.info("🚀 Starting Gazeguard Webcam Pipeline")
        self.logger.info("=" * 60)

        # Check Ollama connectivity
        self.logger.info("🔌 Checking Ollama connectivity...")
        self.inference_engine._initialize_model()

        if not self._wait_for_camera():
            return
        read_failures = 0

        self.running = True
        frame_interval = 1.0 / self.fps
        last_frame_time = 0.0
        dashboard_frame_interval = 0.5  # Push to dashboard at 2fps
        last_dashboard_push = 0.0

        self.logger.info("👁️  Monitoring started. Press Ctrl+C to stop.")
        self.logger.info(f"📋 Config: cooldown={self.cooldown}s, device={self.device_id}")

        pending_event_id: Optional[str] = None  # motion seen, still collecting post-event frames

        try:
            while self.running:
                # Always read: frames left unread on a network stream queue up and add growing lag
                ret, frame = self.cap.read()
                if not ret:
                    read_failures += 1
                    if read_failures % 50 == 0:
                        if self.frames_since_open == 0 and isinstance(self.device_id, str):
                            self._warn_undecodable_stream()  # opened, but not one frame decoded
                        # Camera unplugged / stream ended: reopen instead of retrying a dead handle
                        self.logger.warning("Camera stopped delivering frames, reconnecting...")
                        self.cap.release()
                        time.sleep(2)
                        if not self._wait_for_camera():
                            break
                    else:
                        self.logger.debug("Failed to read frame, retrying...")
                        time.sleep(0.1)
                    continue
                read_failures = 0
                self.frames_since_open += 1
                self._codec_warned = False

                # Throttle processing to the configured FPS; surplus frames are dropped
                now = time.time()
                if (now - last_frame_time) < frame_interval:
                    continue
                last_frame_time = now

                # The model takes images of at most 768px, so keep frames that size from here on:
                # less RAM in the rolling buffer and much cheaper motion detection
                frame = self._downscale(frame)

                # Add frame to rolling buffer
                clip_ready = self.frame_buffer.add(frame)

                # Detect motion (pre-filter)
                motion_detected, fg_mask = self.motion_detector.detect(frame)
                if motion_detected and not self.frame_buffer.is_warmed_up:
                    # MOG2 sees the whole first frames as motion while it learns the background;
                    # don't let that start the cooldown and delay the first real event
                    self.motion_detector.last_trigger_time = 0.0

                # Motion starts an event; wait for the post-event frames so the model sees what
                # happened next, not only the (mostly empty) seconds before the trigger
                if (motion_detected and pending_event_id is None and not self._pending_inference
                        and self.frame_buffer.is_warmed_up):
                    pending_event_id = f"webcam_{uuid.uuid4().hex[:8]}"
                    self.logger.info(f"🎯 Motion trigger → capturing aftermath [{pending_event_id}]")
                    self.frame_buffer.trigger()

                if clip_ready and pending_event_id is not None:
                    clip_frames = self.frame_buffer.get_clip_frames(num_frames=10)
                    self.logger.info(f"🧠 Clip ready ({len(clip_frames)} frames) → inference [{pending_event_id}]")
                    self._pending_inference = True
                    self.inference_thread = threading.Thread(
                        target=self._run_inference_async,
                        args=(clip_frames, pending_event_id),
                        daemon=True,
                    )
                    self.inference_thread.start()
                    pending_event_id = None

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

                    cv2.imshow("Gazeguard - Webcam Monitor", debug_frame)

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

    parser = argparse.ArgumentParser(description="Gazeguard Webcam Monitor")
    parser.add_argument(
        "--config",
        default="gazeguard-config.yaml",
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
