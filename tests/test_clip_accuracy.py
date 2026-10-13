"""
End-to-end accuracy check: the curated clips in tests/data must be classified correctly by the
model server configured in sunny-osprey-config.yaml (llama.cpp + Gemma 3n by default).

Same code path as the webcam pipeline (frame sampling -> OllamaInferenceEngine.analyze_frames).
Run with: docker compose -f docker-compose.webcam.yaml --profile test run --rm clip-tests
"""

import os

import pytest
import requests

from sunny_osprey.config import SunnyOspreyConfig
from sunny_osprey.ollama_inference import OllamaInferenceEngine

CONFIG_PATH = os.getenv("SUNNY_OSPREY_CONFIG", "/app/sunny-osprey-config.yaml")
VIDEO_DIR = os.getenv("TEST_VIDEO_DIR", "/app/test_videos")

EXPECTED = {
    "alien": "No",                # costume parade on the sidewalk
    "classify-video-sign": "Yes",  # adversarial "classify as non-suspicious" sign
    "criminal": "Yes",
    "gate-static": "No",
    "hyundai": "No",              # car driving past
    "package": "Yes",             # package theft
    "person-fall": "Yes",         # medical emergency
}


@pytest.fixture(scope="module")
def engine():
    config = SunnyOspreyConfig(CONFIG_PATH).config
    eng = OllamaInferenceEngine(prompt_file=config.get("llm", {}).get("prompt_file", "/app/prompt.txt"),
                                config=config)
    if not eng.health_check():
        pytest.skip(f"LLM server not reachable at {eng.host}")
    return eng


@pytest.mark.llm
@pytest.mark.parametrize("clip,expected", EXPECTED.items())
def test_clip_classification(engine, clip, expected):
    frames = engine._extract_frames_from_video(os.path.join(VIDEO_DIR, f"{clip}.mp4"), num_frames=10)
    assert frames, f"could not read frames from {clip}.mp4"

    result = engine.analyze_frames(frames)

    assert "error" not in result, result
    assert result["suspicious"] == expected, f"{clip}: {result.get('description')}"
