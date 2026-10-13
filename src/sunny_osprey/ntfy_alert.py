"""
Ntfy Alert Module for Sunny Osprey

Sends instant push notifications to smartphone via ntfy.sh.
Supports urgent priority (bypasses silent/DND, rings audible alarm on phone),
emoji tags, anomaly classification, and image/clip attachments.

App installation:
- Android: Install 'ntfy' from Google Play Store or F-Droid
- iOS: Install 'ntfy' from Apple App Store
- Subscribe to your topic (e.g. 'sunny-osprey-alerts-<your-topic>')
"""

import json
import logging
import os
import requests
from typing import Dict, Any, Optional
from datetime import datetime


class NtfyAlert:
    """Sends high-priority smartphone alerts with alarm via ntfy.sh."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.logger = logging.getLogger(__name__)

        # Topic name: use config, env var, or a default
        self.topic = self.config.get("topic") or os.getenv(
            "NTFY_TOPIC", "sunny-osprey-security-alerts"
        )
        # NTFY_SERVER wins so the same config works natively (localhost) and inside Docker (ntfy service)
        self.server = (
            os.getenv("NTFY_SERVER") or self.config.get("server") or "https://ntfy.sh"
        ).rstrip("/")
        self.enabled = bool(self.topic and self.topic.strip())

        if self.enabled:
            self.logger.info(f"NtfyAlert initialized with topic: {self.topic}")
        else:
            self.logger.warning("NtfyAlert disabled: no topic configured")

    def send_incident(self, incident_data: Dict[str, Any]) -> bool:
        """
        Send incident alert to the subscribed smartphone via ntfy.

        Args:
            incident_data: Dictionary containing event details:
                - event_id: unique event ID
                - is_suspicious: bool
                - description: text description
                - llm_result: dict with anomaly_type, severity, description
                - video_path / frame_path: optional local media paths

        Returns:
            bool: True if alert successfully dispatched, False otherwise
        """
        if not self.enabled:
            self.logger.debug("NtfyAlert is disabled, skipping")
            return False

        try:
            llm_result = incident_data.get("llm_result", {})
            event_id = incident_data.get("event_id", "unknown")
            is_suspicious = incident_data.get("is_suspicious", False)
            description = incident_data.get("description", "No description provided.")

            # Extract anomaly type & severity
            anomaly_type = (
                llm_result.get("anomaly_type")
                or incident_data.get("anomaly_type")
                or ("Suspicious Activity" if is_suspicious else "Normal Activity")
            )
            severity = (
                llm_result.get("severity")
                or incident_data.get("severity")
                or ("high" if is_suspicious else "low")
            ).lower()

            url = f"{self.server}/{self.topic}"

            # If suspicious, trigger urgent ALARM priority (5) to sound loudly on phone
            if is_suspicious:
                priority = "urgent"  # Level 5 in ntfy: rings alarm, vibrates, wakes screen
                tags = "rotating_light,warning,bell"
                title = f"SECURITY ALARM: {anomaly_type.upper()}"
            else:
                priority = "default"  # Level 3
                tags = "information_source,white_check_mark"
                title = f"Monitoring Notice: {anomaly_type}"

            # Ensure header is strictly ASCII safe
            title_clean = title.encode("ascii", "ignore").decode("ascii")

            # Format the body message
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            body_lines = [
                f"Time: {now_str}",
                f"Severity: {severity.upper()}",
                f"Event ID: {event_id}",
                f"Anomaly: {anomaly_type}",
                "",
                f"Details:",
                description,
            ]
            message_body = "\n".join(body_lines)

            headers = {
                "Title": title_clean,
                "Priority": priority,
                "Tags": tags,
            }

            # If an image or snapshot is available, send as attachment
            frame_path = incident_data.get("frame_path") or llm_result.get("frame_path")
            if frame_path and os.path.exists(frame_path):
                headers["Filename"] = os.path.basename(frame_path)
                with open(frame_path, "rb") as f:
                    resp = requests.post(url, data=f, headers=headers, timeout=10)
            else:
                resp = requests.post(
                    url,
                    data=message_body.encode("utf-8"),
                    headers=headers,
                    timeout=10,
                )

            if resp.status_code == 200:
                self.logger.info(
                    f"✅ Ntfy phone alert successfully sent for event {event_id} (Priority: {priority})"
                )
                return True
            else:
                self.logger.error(
                    f"❌ Ntfy push failed with status {resp.status_code}: {resp.text}"
                )
                return False

        except Exception as e:
            self.logger.error(f"Error sending ntfy phone alert: {e}")
            return False
