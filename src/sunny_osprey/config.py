"""
Configuration management for Gazeguard.
"""

import os
import yaml
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
import logging
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


class SunnyOspreyConfig:
    """Configuration manager for Gazeguard."""
    
    def __init__(self, config_path: str = "/app/gazeguard-config.yaml"):
        """
        Initialize configuration from YAML file.
        
        Args:
            config_path: Path to the configuration YAML file
        """
        self.config_path = config_path
        
        # Load .env file if it exists
        self._load_env_file()
        
        self.config = self._load_config()
        self._setup_logging()
    
    def _load_env_file(self):
        """Load environment variables from .env file."""
        # Try to load .env file from current directory or config directory
        env_paths = [
            '.env',
            os.path.join(os.path.dirname(self.config_path), '.env'),
            '/app/.env'
        ]
        
        for env_path in env_paths:
            if os.path.exists(env_path):
                load_dotenv(env_path)
                print(f"✅ Loaded environment variables from {env_path}")
                break
        else:
            print("⚠️  No .env file found, using system environment variables")
    
    def _get_env_var(self, key: str, default: str = '') -> str:
        """
        Get environment variable with fallback to .env file.
        
        Args:
            key: Environment variable name
            default: Default value if not found
            
        Returns:
            Environment variable value or default
        """
        return os.getenv(key, default)
    
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from YAML file."""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r') as f:
                    config = yaml.safe_load(f)
                print(f"✅ Loaded configuration from {self.config_path}")
                return config or {}
            else:
                print(f"⚠️  Configuration file not found at {self.config_path}, using defaults")
                return self._get_default_config()
        except Exception as e:
            print(f"❌ Error loading configuration: {e}")
            print("Using default configuration")
            return self._get_default_config()
    
    def _get_default_config(self) -> Dict[str, Any]:
        """Get default configuration."""
        return {
            'mqtt': {
                'enabled': True,
                'host': self._get_env_var('MQTT_HOST', 'mqtt'),
                'port': int(self._get_env_var('MQTT_PORT', '1883')),
                'topic': 'frigate/events'
            },
            'frigate': {
                'api_base_url': self._get_env_var('FRIGATE_API_URL', 'http://frigate:5000')
            },
            'cameras': {
                'enabled_cameras': []
            },
            # ── Webcam (laptop camera) settings ────────────────────────────
            'webcam': {
                'device_id': 0,
                'fps': 15,
                'width': 1280,
                'height': 720,
            },
            # ── Motion detection (MOG2 pre-filter) ─────────────────────────
            'motion': {
                'min_area': 1500,           # Minimum pixel area to trigger
                'sensitivity': 50,          # MOG2 varThreshold (lower = more sensitive)
                'cooldown_seconds': 12.0,   # Seconds between LLM calls
                'clip_duration_seconds': 5, # Pre-event buffer duration
            },
            # ── Ollama LLM backend ──────────────────────────────────────────
            'ollama': {
                'host': self._get_env_var('OLLAMA_HOST', 'http://localhost:11434'),
                'model': self._get_env_var('OLLAMA_MODEL', 'gemma3n:e2b'),
                'timeout': 180,
            },
            'llm': {
                'backend': 'ollama',        # 'ollama' or 'huggingface'
                'prompt_file': '/app/prompt.txt',
                'model_name': 'gemma3n:e2b',
                'max_new_tokens': 500,
            },
            # ── Dashboard ───────────────────────────────────────────────────
            'dashboard': {
                'url': self._get_env_var('DASHBOARD_URL', 'http://localhost:8080'),
            },
            'alerts': {
                'send_all_activities': False,
                'telegram': {
                    'bot_token': self._get_env_var('TELEGRAM_BOT_TOKEN', ''),
                    'chat_id': self._get_env_var('TELEGRAM_CHAT_ID', '')
                },
                'grafana': {
                    'url': self._get_env_var('GRAFANA_URL', ''),
                    'api_key': self._get_env_var('GRAFANA_API_KEY', '')
                }
            },
            'logging': {
                'level': 'INFO',
                'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            },
            'version': '2.0.0'
        }
    
    def _setup_logging(self):
        """Setup logging based on configuration."""
        log_config = self.config.get('logging', {})
        level = getattr(logging, log_config.get('level', 'INFO').upper())
        format_str = log_config.get('format', '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        
        logging.basicConfig(
            level=level,
            format=format_str
        )
    
    def get_mqtt_config(self) -> Dict[str, Any]:
        """Get MQTT configuration."""
        return self.config.get('mqtt', {})
    
    def get_frigate_config(self) -> Dict[str, Any]:
        """Get Frigate configuration."""
        return self.config.get('frigate', {})
    
    def get_camera_config(self) -> Dict[str, Any]:
        """Get camera configuration."""
        return self.config.get('cameras', {})
    
    def get_llm_config(self) -> Dict[str, Any]:
        """Get LLM configuration."""
        return self.config.get('llm', {})

    def get_ollama_config(self) -> Dict[str, Any]:
        """Get Ollama configuration."""
        return self.config.get('ollama', {})

    def get_webcam_config(self) -> Dict[str, Any]:
        """Get webcam configuration."""
        return self.config.get('webcam', {})

    def get_motion_config(self) -> Dict[str, Any]:
        """Get motion detection configuration."""
        return self.config.get('motion', {})

    def get_dashboard_config(self) -> Dict[str, Any]:
        """Get dashboard configuration."""
        return self.config.get('dashboard', {})

    def get_alerts_config(self) -> Dict[str, Any]:
        """Get alerts configuration."""
        return self.config.get('alerts', {})
    
    def should_process_camera(self, camera_name: str) -> bool:
        """
        Check if events from a specific camera should be processed.
        
        Args:
            camera_name: Name of the camera
            
        Returns:
            True if camera should be processed, False otherwise
        """
        enabled_cameras = self.get_camera_config().get('enabled_cameras', [])
        
        # If no cameras are specified, process all cameras
        if not enabled_cameras:
            return True
        
        # Check if camera is in the enabled list
        return camera_name in enabled_cameras
    
    def should_skip_event(self, event_data: Dict[str, Any]) -> bool:
        """
        Check if an event should be skipped based on configuration.
        
        Args:
            event_data: Event data from Frigate
            
        Returns:
            True if event should be skipped, False otherwise
        """
        # Check camera filtering only
        camera_name = event_data.get('camera')
        if camera_name and not self.should_process_camera(camera_name):
            return True
        
        return False
    
    def get_mqtt_host(self) -> str:
        """Get MQTT host from config."""
        return self.get_mqtt_config().get('host', 'mqtt')
    
    def get_mqtt_port(self) -> int:
        """Get MQTT port from config."""
        return self.get_mqtt_config().get('port', 1883)
    
    def get_frigate_api_url(self) -> str:
        """Get Frigate API URL from config."""
        return self.get_frigate_config().get('api_base_url', 'http://frigate:5000')
    
    def get_prompt_file(self) -> str:
        """Get prompt file path from config."""
        return self.get_llm_config().get('prompt_file', '/app/prompt.txt')
    
    def reload(self):
        """Reload configuration from file."""
        self.config = self._load_config()
        self._setup_logging()
        print("✅ Configuration reloaded") 