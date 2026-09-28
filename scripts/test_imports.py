"""
Smoke test for Sunny Osprey webcam edition modules.
Run: python scripts/test_imports.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

errors = []

def test(name, fn):
    try:
        fn()
        print(f"  ✅ {name}")
    except Exception as e:
        print(f"  ❌ {name}: {e}")
        errors.append((name, e))

print("\n🦅 Sunny Osprey — Module Import Test")
print("=" * 45)

test("MotionDetector import", lambda: __import__('sunny_osprey.motion_detector', fromlist=['MotionDetector']))
test("FrameBuffer import", lambda: __import__('sunny_osprey.frame_buffer', fromlist=['FrameBuffer']))
test("OllamaInferenceEngine import", lambda: __import__('sunny_osprey.ollama_inference', fromlist=['OllamaInferenceEngine']))
test("AlertManager import", lambda: __import__('sunny_osprey.alert_manager', fromlist=['AlertManager']))
test("SunnyOspreyConfig import", lambda: __import__('sunny_osprey.config', fromlist=['SunnyOspreyConfig']))

# Test config loading
def test_config():
    from sunny_osprey.config import SunnyOspreyConfig
    cfg = SunnyOspreyConfig('sunny-osprey-config.yaml')
    assert cfg.get_llm_config().get('backend') == 'ollama', "backend should be ollama"
    assert cfg.get_ollama_config().get('host') is not None, "ollama host missing"
    assert isinstance(cfg.get_webcam_config().get('device_id'), int), "device_id should be int"
    print(f"       backend={cfg.get_llm_config().get('backend')}, device={cfg.get_webcam_config().get('device_id')}, host={cfg.get_ollama_config().get('host')}")

test("Config loads correctly", test_config)

# Test motion detector instantiation
def test_motion():
    from sunny_osprey.motion_detector import MotionDetector
    md = MotionDetector(min_area=1500, cooldown_seconds=5.0)
    assert md.min_area == 1500
    assert md.cooldown_seconds == 5.0
    import numpy as np
    dummy_frame = (255 * __import__('numpy').random.rand(480, 640, 3)).astype('uint8')
    triggered, mask = md.detect(dummy_frame)
    assert isinstance(triggered, bool)

test("MotionDetector instantiation + detect()", test_motion)

# Test frame buffer
def test_buffer():
    from sunny_osprey.frame_buffer import FrameBuffer
    buf = FrameBuffer(duration_seconds=2.0, fps=15)
    import numpy as np
    for _ in range(10):
        buf.add((255 * np.random.rand(480, 640, 3)).astype('uint8'))
    assert buf.frame_count == 10
    frames = buf.get_clip_frames(num_frames=5)
    assert len(frames) <= 5

test("FrameBuffer add + sample", test_buffer)

# Test Ollama client (connectivity check — doesn't require model to be loaded)
def test_ollama_import():
    from sunny_osprey.ollama_inference import OllamaInferenceEngine
    engine = OllamaInferenceEngine(
        prompt_file='prompt.txt',
        config={'ollama': {'host': 'http://localhost:11434', 'model': 'gemma3n:e2b'}}
    )
    is_up = engine.health_check()
    status = "reachable ✅" if is_up else "not reachable yet (model still downloading)"
    print(f"       Ollama at localhost:11434: {status}")

test("OllamaInferenceEngine + health check", test_ollama_import)

print()
print("=" * 45)
if errors:
    print(f"❌ {len(errors)} test(s) failed:")
    for name, err in errors:
        print(f"   • {name}: {err}")
    sys.exit(1)
else:
    print("✅ All tests passed!")
    print()
    print("Next steps:")
    print("  1. Wait for Ollama to finish downloading gemma3n:e2b")
    print("     → docker logs sunny-osprey-ollama -f")
    print("  2. Open dashboard: http://localhost:8080")
    print("  3. Start webcam pipeline:")
    print("     → python src/sunny_osprey/webcam_capture.py")
