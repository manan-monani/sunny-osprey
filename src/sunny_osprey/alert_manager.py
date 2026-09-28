"""
Grafana Alert Module for Security Camera Analysis
"""

import json
import logging
import os
import requests
from typing import Dict, Any, Optional
from datetime import datetime, timezone
import asyncio
from .telegram_alert import TelegramAlert
from .grafana_irm_alert import GrafanaIRMAlert
from .ntfy_alert import NtfyAlert


def is_suspicious_activity_detected(llm_result: Dict[str, Any]) -> bool:
    """
    Check if suspicious activity was detected from LLM result.
    
    Handles both string values ("yes"/"no") and boolean values (true/false).
    Supports both old field name "is_unusual_or_suspicious_activity_detected" and new field name "suspicious".
    
    Args:
        llm_result: Dictionary containing LLM inference result
        
    Returns:
        bool: True if suspicious activity detected, False otherwise
    """
    # Check for new field name first, then fall back to old field name
    value = llm_result.get('suspicious') or llm_result.get('is_unusual_or_suspicious_activity_detected')
    
    # Handle None/empty values
    if value is None:
        return False
    
    # Handle boolean values
    if isinstance(value, bool):
        return value
    
    # Handle string values (case-insensitive)
    if isinstance(value, str):
        return value.lower() in ["yes", "true", "1"]
    
    # Handle numeric values
    if isinstance(value, (int, float)):
        return bool(value)
    
    # Default to False for any other type
    return False


class AlertManager:
    """Routes alerts to configured backends (ntfy, Telegram, Grafana IRM, or all)."""
    def __init__(self, config: Dict[str, Any] = None):
        raw_config = config or {}
        # Support either full config or alerts sub-dictionary
        self.config = raw_config.get('alerts', raw_config)
        self.logger = logging.getLogger(__name__)

        self.backend = os.getenv('ALERT_BACKEND', self.config.get('backend', 'all')).lower()
        
        telegram_config = self.config.get('telegram', {})
        grafana_config = self.config.get('grafana', {})
        ntfy_config = self.config.get('ntfy', {})
        
        self.backends = []
        
        # Determine active backends
        if self.backend in ['telegram', 'all', 'both']:
            tg = TelegramAlert(telegram_config)
            if tg.enabled:
                self.backends.append(tg)
                
        if self.backend in ['ntfy', 'all', 'both']:
            ntfy = NtfyAlert(ntfy_config)
            if ntfy.enabled:
                self.backends.append(ntfy)
                
        if self.backend in ['grafana']:
            graf = GrafanaIRMAlert(grafana_config)
            self.backends.append(graf)
            
        # Fallback if specific backend chosen
        if not self.backends:
            if self.backend == 'ntfy':
                self.backends.append(NtfyAlert(ntfy_config))
            elif self.backend == 'telegram':
                self.backends.append(TelegramAlert(telegram_config))
            else:
                self.backends.append(NtfyAlert(ntfy_config))
                
        self.logger.info(f"AlertManager initialized with {len(self.backends)} active backends")
        self.video_clip_base_url = os.getenv('VIDEO_CLIP_BASE_URL')

    def _prepare_incident_data(self, event_id: str, llm_result: Dict[str, Any]) -> Dict[str, Any]:
        video_url = f"{self.video_clip_base_url}?event_id={event_id}" if self.video_clip_base_url else ""
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        description = llm_result.get('description', 'No description available')
        
        # Check if this is a suspicious activity using the helper function
        is_suspicious = is_suspicious_activity_detected(llm_result)
        
        # Different decorations based on suspicious status
        if is_suspicious:
            start_art = "🚨 SECURITY ALERT 🚨"
            decorated_description = f"{start_art}\n{description}"
        else:
            start_art = "🏃 NORMAL ACTIVITY 🏃"
            decorated_description = f"{start_art}\n{description}"
        
        decorated_video_url = f"[Video Clip] {video_url}" if video_url else ""
        incident_data = {
            'event_id': event_id,
            'description': decorated_description,
            'video_url': decorated_video_url,
            'llm_result': llm_result,
            'is_suspicious': is_suspicious
        }
        return incident_data

    def send_incident(self, event_id: str, llm_result: dict) -> bool:
        incident_data = self._prepare_incident_data(event_id, llm_result)
        
        # Check if we should send this incident based on configuration
        send_all_activities = self.config.get('send_all_activities', False)
        is_suspicious = incident_data.get('is_suspicious', False)
        
        # Only send if it's suspicious OR if send_all_activities is enabled
        if not is_suspicious and not send_all_activities:
            logging.getLogger(__name__).info(f"Normal activity skipped for event {event_id} (send_all_activities: {send_all_activities})")
            return True  # Return True to indicate "successfully handled" (by skipping)
        
        # Log what we're actually doing
        if is_suspicious:
            logging.getLogger(__name__).info(f"Sending suspicious activity alert for event {event_id}")
        else:
            logging.getLogger(__name__).info(f"Sending normal activity notification for event {event_id} (send_all_activities: {send_all_activities})")
        
        success = True
        for backend in self.backends:
            try:
                res = backend.send_incident(incident_data)
                if not res:
                    success = False
            except Exception as e:
                self.logger.error(f"Error dispatching to backend {backend.__class__.__name__}: {e}")
                success = False
        return success 