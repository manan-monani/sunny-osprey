"""
Standalone Video Analysis CLI for Sunny Osprey

Run anomaly detection on pre-recorded video files or folders:
    python scripts/analyze_video.py tests/data/criminal.mp4
    python scripts/analyze_video.py --dir tests/data/
    python scripts/analyze_video.py tests/data/package.mp4 --no-alert
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure package is on python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from sunny_osprey.video_processor import VideoFileProcessor


def format_result(result: dict) -> str:
    """Format an analysis result for pretty terminal output."""
    is_suspicious = result.get("suspicious", "No").lower() == "yes"
    anomaly_type = result.get("anomaly_type", "None")
    severity = result.get("severity", "low").upper()
    description = result.get("description", "No description")
    elapsed = result.get("processing_time_s", 0.0)
    video_name = result.get("video_file", "unknown")

    status_badge = "🚨 [SUSPICIOUS ACTIVITY]" if is_suspicious else "✅ [NORMAL ACTIVITY]"
    color_border = "=" * 65

    output = [
        color_border,
        f"🎬 Video: {video_name} ({elapsed}s processing)",
        f"Status: {status_badge}",
        f"Anomaly Type: {anomaly_type}",
        f"Severity: {severity}",
        f"Description: {description}",
        color_border,
    ]
    return "\n".join(output)


def main():
    parser = argparse.ArgumentParser(
        description="Sunny Osprey Video Anomaly Detector",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "video_path",
        nargs="?",
        help="Path to video file (.mp4, .avi, etc.) to analyze",
    )
    parser.add_argument(
        "-i", "--input",
        dest="input_path",
        help="Path to video file (.mp4, .avi, etc.) or folder",
    )
    parser.add_argument(
        "--dir",
        dest="directory",
        help="Path to directory containing multiple video files",
    )
    parser.add_argument(
        "--config",
        default="sunny-osprey-config.yaml",
        help="Configuration file (default: sunny-osprey-config.yaml)",
    )
    parser.add_argument(
        "--no-alert",
        action="store_true",
        help="Disable phone alerts for this run",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override Ollama vision model (e.g. moondream, llava-phi3)",
    )

    args = parser.parse_args()
    if args.input_path and not args.video_path:
        if os.path.isdir(args.input_path):
            args.directory = args.input_path
        else:
            args.video_path = args.input_path

    if not args.video_path and not args.directory:
        parser.print_help()
        sys.exit(1)

    print("🦅 Sunny Osprey — Video Anomaly Detection")
    print(f"📋 Config: {args.config}")
    processor = VideoFileProcessor(
        config_path=args.config,
        model_name=args.model,
    )

    notify = not args.no_alert

    if args.video_path:
        if not os.path.exists(args.video_path):
            print(f"❌ Error: Video file not found: {args.video_path}")
            sys.exit(1)
        res = processor.process_video(args.video_path, notify_alerts=notify)
        print(format_result(res))
    elif args.directory:
        if not os.path.isdir(args.directory):
            print(f"❌ Error: Directory not found: {args.directory}")
            sys.exit(1)
        results = processor.process_directory(args.directory, notify_alerts=notify)
        print("\n" + "=" * 65)
        print(f"📊 SUMMARY: Processed {len(results)} videos")
        print("=" * 65)
        for res in results:
            print(format_result(res))
            print()


if __name__ == "__main__":
    main()
