"""
Frame Buffer - Rolling circular buffer for video clip extraction.

Maintains a sliding window of recent frames. When motion is triggered,
extracts 10 evenly-spaced frames from the buffer for LLM analysis,
mirroring the original pipeline's "10 representative frames per event" design.
"""

import logging
import time
from collections import deque
from typing import List, Optional, Tuple
import cv2
import numpy as np
from PIL import Image

JPEG_QUALITY = 90  # visually lossless at the <=768px the model sees


class FrameBuffer:
    """
    Rolling circular buffer that holds the last N seconds of video frames.

    When a motion event is triggered, we sample 10 frames from this buffer
    to represent the event clip — matching the original Gazeguard design
    that sent "10 evenly spaced frames" to Gemma 3n.
    """

    def __init__(
        self,
        duration_seconds: float = 5.0,
        fps: int = 15,
        post_event_frames: int = 30,
    ):
        """
        Initialize the frame buffer.

        Args:
            duration_seconds: How many seconds of video to buffer (pre-event).
            fps: Expected camera FPS (used to calculate buffer capacity).
            post_event_frames: Extra frames to capture AFTER trigger fires
                               (captures the "aftermath" of the event).
        """
        self.fps = fps
        self.duration_seconds = duration_seconds
        self.post_event_frames = post_event_frames
        self.capacity = int(fps * duration_seconds)

        # The circular buffer stores (timestamp, JPEG bytes) tuples
        self.buffer: deque = deque(maxlen=self.capacity)

        # Post-event capture state
        self._post_trigger_remaining = 0
        self._triggered = False
        self._trigger_time: Optional[float] = None

        # Timestamps of frames collected after the trigger (the "after" part of the event)
        self._post_trigger_frames: List[float] = []

        self.logger = logging.getLogger(__name__)
        self.logger.info(
            f"FrameBuffer initialized: {duration_seconds}s @ {fps}fps "
            f"= {self.capacity} frames capacity"
        )

    def add(self, frame: np.ndarray) -> bool:
        """
        Add a frame to the buffer.

        Args:
            frame: BGR numpy array from OpenCV

        Returns:
            True if a post-trigger capture just completed (clip is ready)
        """
        timestamp = time.time()

        # Frames are kept JPEG-encoded (~100 KB instead of ~1 MB raw at 768x432); only the
        # 10 frames picked for the model are ever decoded again
        ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            return False
        self.buffer.append((timestamp, jpeg))

        # If we're in post-trigger collection mode (only timestamps: the frames live in the buffer)
        if self._triggered and self._post_trigger_remaining > 0:
            self._post_trigger_frames.append(timestamp)
            self._post_trigger_remaining -= 1

            if self._post_trigger_remaining == 0:
                self._triggered = False
                self.logger.debug(
                    f"Post-trigger capture complete: {len(self._post_trigger_frames)} frames"
                )
                return True  # Signal that clip is ready

        return False

    def trigger(self):
        """
        Signal that a motion event has been detected.
        Starts collecting post-event frames.
        """
        self._triggered = True
        self._trigger_time = time.time()
        self._post_trigger_remaining = self.post_event_frames
        self._post_trigger_frames = []
        self.logger.debug(
            f"Trigger set — will collect {self.post_event_frames} post-event frames"
        )

    def get_clip_frames(self, num_frames: int = 10) -> List[Tuple[Image.Image, float]]:
        """
        Extract N evenly-spaced frames from the current buffer.

        Combines pre-event frames (from circular buffer) with post-event frames
        for a complete representation of the event.

        Args:
            num_frames: Number of frames to extract (default: 10, matching original)

        Returns:
            List of (PIL.Image, timestamp) tuples, ordered chronologically.
        """
        # Post-event frames are the newest entries of the circular buffer, so the buffer alone
        # holds the whole clip (pre-event context followed by the aftermath).
        all_frames = list(self.buffer)

        if not all_frames:
            self.logger.warning("Frame buffer is empty, cannot extract clip")
            return []

        total = len(all_frames)
        if total <= num_frames:
            selected = all_frames
        else:
            # Evenly-spaced indices across the full buffer
            step = total / num_frames
            indices = [int(i * step) for i in range(num_frames)]
            selected = [all_frames[i] for i in indices]

        # Convert to PIL Images, with timestamps in seconds since the clip start (the montage
        # labels them "T=0.0s", "T=0.7s", ... for the model; wall-clock epochs are meaningless there)
        start = selected[0][0]
        result = []
        for ts, jpeg in selected:
            frame = cv2.imdecode(jpeg, cv2.IMREAD_COLOR)
            pil_img = Image.fromarray(frame[:, :, ::-1])  # BGR → RGB
            result.append((pil_img, round(ts - start, 2)))

        self.logger.debug(
            f"Extracted {len(result)} frames from buffer "
            f"(total available: {total})"
        )
        return result

    def clear_post_trigger(self):
        """Clear post-trigger state after processing."""
        self._post_trigger_frames = []
        self._trigger_time = None

    @property
    def is_triggered(self) -> bool:
        """Whether we're currently collecting post-trigger frames."""
        return self._triggered

    @property
    def frame_count(self) -> int:
        """Current number of frames in the pre-event buffer."""
        return len(self.buffer)

    @property
    def is_warmed_up(self) -> bool:
        """True once the buffer has enough frames for a good clip."""
        return len(self.buffer) >= min(30, self.capacity // 2)

    @property
    def stats(self) -> dict:
        """Get buffer statistics."""
        return {
            "capacity": self.capacity,
            "current_frames": len(self.buffer),
            "post_trigger_frames": len(self._post_trigger_frames),
            "is_triggered": self._triggered,
            "is_warmed_up": self.is_warmed_up,
            "duration_seconds": self.duration_seconds,
        }
