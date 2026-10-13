"""
Video File Processor for Gazeguard

Enables offline / pre-recorded video anomaly detection without live webcam.
Supports:
- Single video file analysis (.mp4, .avi, .mkv, .mov, etc.)
- Directory batch scanning
- Temporal keyframe extraction & montage generation
- Live dashboard streaming (posts to http://localhost:8080/api/events)
- Phone alarm alerts via AlertManager (ntfy / Telegram)
"""

import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import requests
from PIL import Image

from sunny_osprey.config import SunnyOspreyConfig
from sunny_osprey.ollama_inference import OllamaInferenceEngine
from sunny_osprey.alert_manager import AlertManager


class VideoFileProcessor:
    """Processes recorded video files through the Gazeguard anomaly detection pipeline."""

    def __init__(
        self,
        config_path: str = "gazeguard-config.yaml",
        prompt_file: Optional[str] = None,
        model_name: Optional[str] = None,
    ):
        self.logger = logging.getLogger(__name__)
        self.config_obj = SunnyOspreyConfig(config_path)
        self.config = self.config_obj.config

        # Allow model override
        if model_name:
            if "ollama" not in self.config:
                self.config["ollama"] = {}
            self.config["ollama"]["model"] = model_name

        llm_cfg = self.config_obj.get_llm_config()
        self.prompt_file = prompt_file or llm_cfg.get("prompt_file", "prompt.txt")

        # Initialize inference engine & alert manager
        self.inference_engine = OllamaInferenceEngine(
            prompt_file=self.prompt_file,
            config=self.config,
        )

        alerts_cfg = self.config_obj.get_alerts_config()
        self.alert_manager = AlertManager(alerts_cfg)

        # Dashboard URL
        dashboard_cfg = self.config.get("dashboard", {})
        self.dashboard_url = dashboard_cfg.get(
            "url", os.getenv("DASHBOARD_URL", "http://localhost:8080")
        )

    def extract_keyframes(
        self, video_path: str, num_frames: Optional[int] = None
    ) -> List[Tuple[Image.Image, float]]:
        """
        Extract evenly spaced keyframes across the video file.
        Dynamically adapts count: 4 frames (2x2 grid) for clips <= 6s, 6 frames for longer clips.
        """
        if not os.path.exists(video_path):
            self.logger.error(f"Video file not found: {video_path}")
            return []

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            self.logger.error(f"Cannot open video: {video_path}")
            return []

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        duration = total_frames / fps if fps > 0 else 0.0

        if total_frames <= 0:
            self.logger.error(f"Empty or unreadable video: {video_path}")
            cap.release()
            return []

        if num_frames is None:
            num_frames = 4 if duration <= 6.5 else 6

        # Calculate sample frame indices
        num_samples = min(num_frames, max(1, total_frames))
        if num_samples == 1:
            indices = [total_frames // 2]
        else:
            step = (total_frames - 1) / (num_samples - 1)
            indices = [int(i * step) for i in range(num_samples)]

        frames = []
        for idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret or frame is None:
                continue
            pil_img = Image.fromarray(frame[:, :, ::-1])  # BGR to RGB
            timestamp = round(idx / fps, 2)
            frames.append((pil_img, timestamp))

        cap.release()
        self.logger.info(
            f"Extracted {len(frames)} keyframes from {os.path.basename(video_path)} "
            f"({duration:.1f}s, {total_frames} frames @ {fps:.1f}fps)"
        )
        return frames

    def process_video(
        self, video_path: str, notify_alerts: bool = True
    ) -> Dict[str, Any]:
        """
        Run anomaly detection on a single video file.

        Args:
            video_path: Path to video file
            notify_alerts: Whether to dispatch phone alerts if suspicious

        Returns:
            Dictionary containing detection results and metadata
        """
        start_time = time.perf_counter()
        abs_path = os.path.abspath(video_path)
        file_name = os.path.basename(video_path)
        event_id = f"vid_{uuid.uuid4().hex[:8]}"

        self.logger.info(f"🎬 Processing video file: {file_name}")

        # Extract frames
        frames = self.extract_keyframes(abs_path)
        if not frames:
            return {
                "event_id": event_id,
                "video_file": file_name,
                "video_path": abs_path,
                "error": "Failed to extract frames from video",
                "suspicious": "No",
                "anomaly_type": "Unknown",
                "severity": "low",
                "description": "Video could not be decoded.",
            }

        # Run inference through Ollama
        result = self.inference_engine.analyze_frames(frames)
        elapsed = time.perf_counter() - start_time

        if not result:
            result = {
                "error": "Inference failed",
                "suspicious": "No",
                "anomaly_type": "None",
                "severity": "low",
                "description": "No response from vision model.",
            }

        # Enrich result
        result["event_id"] = event_id
        result["timestamp"] = datetime.utcnow().isoformat()
        result["video_file"] = file_name
        result["video_path"] = abs_path
        result["source"] = "video_file"
        result["processing_time_s"] = round(elapsed, 2)

        is_suspicious = result.get("suspicious", "No").lower() == "yes"

        # Post to dashboard if running
        self._post_to_dashboard(result)

        # Dispatch phone alarm alerts if suspicious
        if is_suspicious and notify_alerts:
            self.logger.info(f"🚨 Anomaly detected in {file_name}! Triggering phone alert...")
            self.alert_manager.send_incident(event_id, result)

        return result

    def process_directory(
        self, dir_path: str, notify_alerts: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Process all video files inside a directory.

        Args:
            dir_path: Directory containing video files
            notify_alerts: Whether to dispatch phone alerts on anomalies

        Returns:
            List of result dictionaries
        """
        path = Path(dir_path)
        if not path.is_dir():
            self.logger.error(f"Directory not found: {dir_path}")
            return []

        extensions = {".mp4", ".avi", ".mkv", ".mov", ".webm"}
        video_files = [f for f in sorted(path.iterdir()) if f.suffix.lower() in extensions]

        self.logger.info(f"📁 Found {len(video_files)} video files in {dir_path}")
        results = []

        for vf in video_files:
            res = self.process_video(str(vf), notify_alerts=notify_alerts)
            results.append(res)

        return results

    def _post_to_dashboard(self, result: Dict[str, Any]):
        """Post the video analysis event to the dashboard API."""
        try:
            resp = requests.post(
                f"{self.dashboard_url}/api/events",
                json=result,
                timeout=3,
            )
            if resp.status_code == 200:
                self.logger.debug(f"Video event posted to dashboard ✅")
        except Exception:
            self.logger.debug("Dashboard offline or unreachable; skipping UI push")
