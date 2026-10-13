"""
Automated Detection Quality Benchmark for Gazeguard

Runs all test videos through the vision pipeline and compares model output
against ground truth expectations.

Usage:
    python scripts/test_detection.py
"""

import json
import os
import sys
import time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from sunny_osprey.video_processor import VideoFileProcessor


# Ground truth expectations based on project benchmark
BENCHMARK_CASES = [
    {
        "video": "criminal.mp4",
        "expected_suspicious": True,
        "description_keywords": ["intruder", "trespass", "criminal", "stealth", "burglary"],
    },
    {
        "video": "package.mp4",
        "expected_suspicious": True,
        "description_keywords": ["package", "theft", "stealing", "take", "porch"],
    },
    {
        "video": "person-fall.mp4",
        "expected_suspicious": True,
        "description_keywords": ["fall", "collapse", "ground", "emergency"],
    },
    {
        "video": "classify-video-sign.mp4",
        "expected_suspicious": True,
        "description_keywords": ["sign", "tamper", "alter", "suspicious"],
    },
    {
        "video": "gate-static.mp4",
        "expected_suspicious": False,
        "description_keywords": ["static", "empty", "no", "normal"],
    },
    {
        "video": "hyundai.mp4",
        "expected_suspicious": False,
        "description_keywords": ["vehicle", "car", "drive", "normal", "routine"],
    },
    {
        "video": "alien.mp4",
        "expected_suspicious": False,
        "description_keywords": ["normal", "no motion", "costume", "static"],
    },
]


def run_benchmark():
    print("=" * 75)
    print("📹 Gazeguard — Anomaly Detection Quality Benchmark")
    print("=" * 75)

    processor = VideoFileProcessor()
    test_data_dir = os.path.join(os.path.dirname(__file__), "..", "tests", "data")

    total = 0
    passed = 0
    results_table = []

    for case in BENCHMARK_CASES:
        video_filename = case["video"]
        video_path = os.path.join(test_data_dir, video_filename)

        if not os.path.exists(video_path):
            print(f"⚠️ Skipping {video_filename} (not found)")
            continue

        total += 1
        print(f"\n🔍 Testing [{total}/{len(BENCHMARK_CASES)}]: {video_filename} ...")
        t0 = time.perf_counter()
        
        # Run inference without triggering phone alert during benchmark
        res = processor.process_video(video_path, notify_alerts=False)
        dt = time.perf_counter() - t0

        model_susp = res.get("suspicious", "No").lower() == "yes"
        expected_susp = case["expected_suspicious"]
        anomaly_type = res.get("anomaly_type", "Unknown")
        description = res.get("description", "")

        is_match = (model_susp == expected_susp)
        if is_match:
            passed += 1
            verdict = "✅ PASS"
        else:
            verdict = "❌ FAIL"

        results_table.append({
            "video": video_filename,
            "expected": "Yes" if expected_susp else "No",
            "model": "Yes" if model_susp else "No",
            "type": anomaly_type,
            "time": f"{dt:.2f}s",
            "verdict": verdict,
            "description": description[:70] + "..." if len(description) > 70 else description,
        })

        print(f"   Verdict: {verdict} | Model: {res.get('suspicious')} (Expected: {'Yes' if expected_susp else 'No'}) | Time: {dt:.2f}s")
        print(f"   Anomaly Type: {anomaly_type}")
        print(f"   Description: {description}")

    # Summary Report Table
    print("\n" + "=" * 75)
    print("📋 BENCHMARK RESULTS SUMMARY")
    print("=" * 75)
    print(f"{'Video':<26} | {'Expected':<8} | {'Model':<6} | {'Type':<22} | {'Time':<6} | {'Verdict'}")
    print("-" * 75)
    for r in results_table:
        print(f"{r['video']:<26} | {r['expected']:<8} | {r['model']:<6} | {r['type'][:22]:<22} | {r['time']:<6} | {r['verdict']}")
    print("-" * 75)
    acc = (passed / total * 100) if total > 0 else 0
    print(f"🎯 Total Score: {passed}/{total} passed ({acc:.1f}% accuracy)")
    print("=" * 75)


if __name__ == "__main__":
    run_benchmark()
