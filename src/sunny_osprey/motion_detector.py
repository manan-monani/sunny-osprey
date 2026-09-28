"""
Motion Detector using MOG2 Background Subtraction.

Replaces YOLO-NAS/Frigate as the lightweight pre-filter.
Runs entirely on CPU — zero GPU VRAM usage.
"""

import logging
import time
from typing import Optional, Tuple
import cv2
import numpy as np


class MotionDetector:
    """
    Detects motion in video frames using MOG2 background subtraction.

    This is the pre-filter that prevents sending every frame to the LLM.
    Only when significant motion is detected do we trigger LLM inference,
    mirroring what YOLO-NAS + Frigate does in the original pipeline.
    """

    def __init__(
        self,
        min_area: int = 1500,
        var_threshold: int = 50,
        history: int = 500,
        cooldown_seconds: float = 10.0,
        detect_shadows: bool = False,
    ):
        """
        Initialize the motion detector.

        Args:
            min_area: Minimum contour area in pixels to qualify as motion.
                      Lower = more sensitive. Tune based on camera resolution.
            var_threshold: MOG2 variance threshold. Lower = more sensitive.
            history: Number of frames to use for background model.
            cooldown_seconds: Minimum seconds between consecutive LLM triggers.
                             Prevents hammering the model with rapid events.
            detect_shadows: Whether MOG2 should detect shadows (slower).
        """
        self.min_area = min_area
        self.cooldown_seconds = cooldown_seconds
        self.last_trigger_time: float = 0.0

        self.logger = logging.getLogger(__name__)

        # Create MOG2 background subtractor
        # MOG2 adapts to gradual lighting changes (day/night cycle, etc.)
        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=history,
            varThreshold=var_threshold,
            detectShadows=detect_shadows,
        )

        # Morphological kernel to remove noise
        self.kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

        # Statistics
        self.total_frames = 0
        self.triggered_count = 0

        self.logger.info(
            f"MotionDetector initialized: min_area={min_area}, "
            f"cooldown={cooldown_seconds}s, var_threshold={var_threshold}"
        )

    def detect(self, frame: np.ndarray) -> Tuple[bool, np.ndarray]:
        """
        Analyze a frame for motion.

        Args:
            frame: BGR frame from OpenCV VideoCapture

        Returns:
            Tuple of (motion_detected, foreground_mask)
            motion_detected: True if significant motion found AND cooldown passed
            foreground_mask: Binary mask showing motion areas (for debug overlay)
        """
        self.total_frames += 1

        # Apply background subtraction
        fg_mask = self.bg_subtractor.apply(frame)

        # Clean up noise with morphological operations
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, self.kernel)
        fg_mask = cv2.dilate(fg_mask, self.kernel, iterations=2)

        # Find contours of motion regions
        contours, _ = cv2.findContours(
            fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        # Check if any contour exceeds our minimum area threshold
        motion_area = 0
        for contour in contours:
            area = cv2.contourArea(contour)
            if area > self.min_area:
                motion_area += area

        has_motion = motion_area > self.min_area

        if not has_motion:
            return False, fg_mask

        # Check cooldown — don't trigger LLM too frequently
        now = time.time()
        if (now - self.last_trigger_time) < self.cooldown_seconds:
            remaining = self.cooldown_seconds - (now - self.last_trigger_time)
            self.logger.debug(
                f"Motion detected but in cooldown ({remaining:.1f}s remaining). "
                f"Motion area: {motion_area:.0f}px²"
            )
            return False, fg_mask

        # Motion confirmed and cooldown passed — trigger!
        self.last_trigger_time = now
        self.triggered_count += 1
        self.logger.info(
            f"🚨 Motion triggered! Area: {motion_area:.0f}px² | "
            f"Total triggers: {self.triggered_count}"
        )
        return True, fg_mask

    def draw_debug_overlay(
        self, frame: np.ndarray, fg_mask: np.ndarray, motion_detected: bool
    ) -> np.ndarray:
        """
        Draw debug overlay on frame showing motion regions.

        Args:
            frame: Original BGR frame
            fg_mask: Foreground mask from detect()
            motion_detected: Whether motion was triggered

        Returns:
            Frame with debug visualization
        """
        debug_frame = frame.copy()

        # Draw contours on frame
        contours, _ = cv2.findContours(
            fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        color = (0, 0, 255) if motion_detected else (0, 165, 255)  # Red or Orange
        for contour in contours:
            if cv2.contourArea(contour) > self.min_area:
                x, y, w, h = cv2.boundingRect(contour)
                cv2.rectangle(debug_frame, (x, y), (x + w, y + h), color, 2)

        # Status overlay
        status_text = "MOTION DETECTED - LLM TRIGGERED" if motion_detected else "Monitoring..."
        status_color = (0, 0, 255) if motion_detected else (0, 255, 0)
        cv2.putText(
            debug_frame, status_text, (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX, 0.7, status_color, 2
        )

        # Stats
        stats = f"Frames: {self.total_frames} | Triggers: {self.triggered_count}"
        cv2.putText(
            debug_frame, stats, (10, frame.shape[0] - 10),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1
        )

        return debug_frame

    def reset_background(self):
        """Reset the background model (useful after scene changes)."""
        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=500,
            varThreshold=50,
            detectShadows=False,
        )
        self.logger.info("Background model reset")

    @property
    def stats(self) -> dict:
        """Get detector statistics."""
        return {
            "total_frames": self.total_frames,
            "triggered_count": self.triggered_count,
            "last_trigger_time": self.last_trigger_time,
            "cooldown_seconds": self.cooldown_seconds,
            "min_area": self.min_area,
        }
