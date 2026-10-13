"""
Gazeguard - AI-Powered Security Camera Monitor
Webcam Edition v2.0 - Adapted for RTX 2050 (4GB VRAM) + Ollama
"""

__version__ = "2.0.0"
__author__ = "Gazeguard Team"

# Use lazy imports to avoid loading heavy dependencies (transformers, torch)
# at import time. They are only needed for the HuggingFace backend.

__all__ = [
    "run_mqtt_processor",
    "FrigateEventProcessor",
]


def __getattr__(name):
    if name == "run_mqtt_processor":
        from .main import run_mqtt_processor
        return run_mqtt_processor
    if name == "FrigateEventProcessor":
        from .mqtt_processor import FrigateEventProcessor
        return FrigateEventProcessor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
