"""
Main module for the Sunny Osprey project.

Supports two operational modes:
  1. webcam  - Laptop camera with Ollama LLM (default, 4GB VRAM)
  2. frigate - Original Frigate/MQTT pipeline with HuggingFace models

Usage:
  python -m sunny_osprey.main --mode webcam   (laptop webcam, Ollama)
  python -m sunny_osprey.main --mode frigate  (original pipeline)
"""

import argparse
import logging
import sys
import time
import os

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def run_webcam_pipeline(config_path: str):
    """Run the webcam-based monitoring pipeline."""
    from sunny_osprey.webcam_capture import WebcamPipeline

    print("🦅 Sunny Osprey — AI Security Monitor (Webcam Edition)")
    print("=" * 60)
    print(f"📋 Config: {config_path}")
    print(f"🎥 Mode: Laptop Webcam + Ollama (gemma3n:e2b)")
    print(f"🖥️  Dashboard: http://localhost:8080")
    print("=" * 60)

    try:
        pipeline = WebcamPipeline(config_path=config_path)
        pipeline.run(show_preview=True)
    except Exception as e:
        print(f"❌ Pipeline error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    return 0


def run_mqtt_processor(config_path: str = "/app/sunny-osprey-config.yaml"):
    """Run the original MQTT/Frigate event processor."""
    from sunny_osprey.mqtt_processor import FrigateEventProcessor
    from sunny_osprey.config import SunnyOspreyConfig

    print("🦅 Initializing Sunny Osprey — Frigate/MQTT Mode")
    print("=" * 60)

    config = SunnyOspreyConfig(config_path)
    mqtt_config = config.get_mqtt_config()
    frigate_config = config.get_frigate_config()
    llm_config = config.get_llm_config()

    mqtt_host = mqtt_config.get('host', 'mqtt')
    mqtt_port = mqtt_config.get('port', 1883)
    api_base_url = frigate_config.get('api_base_url', 'http://frigate:5000')
    prompt_file = llm_config.get('prompt_file', '/app/prompt.txt')

    # Choose LLM backend based on config
    backend = llm_config.get('backend', 'ollama')
    if backend == 'ollama':
        from sunny_osprey.ollama_inference import OllamaInferenceEngine
        print("🧠 Initializing Ollama inference engine...")
        llm_engine = OllamaInferenceEngine(prompt_file=prompt_file, config=config.config)
        llm_engine._initialize_model()
    else:
        from sunny_osprey.llm_inference import LLMInferenceEngine
        print("🧠 Initializing HuggingFace inference engine...")
        llm_engine = LLMInferenceEngine(prompt_file=prompt_file, config=llm_config)
        llm_engine._initialize_model()

    print("✅ LLM engine initialized")

    processor = FrigateEventProcessor(
        mqtt_host=mqtt_host,
        mqtt_port=mqtt_port,
        api_base_url=api_base_url,
        prompt_file=prompt_file,
        llm_engine=llm_engine,
        config=config,
    )

    print(f"📡 Connecting to MQTT at {mqtt_host}:{mqtt_port}...")

    while True:
        try:
            processor.start()
        except KeyboardInterrupt:
            print("\n⛔ Stopping...")
            processor.stop()
            break
        except Exception as e:
            print(f"Error: {e}")
            print("Retrying in 5 seconds...")
        finally:
            time.sleep(5)


def run_video_pipeline(config_path: str, input_path: str):
    """Run anomaly detection on a video file or folder."""
    from sunny_osprey.video_processor import VideoFileProcessor

    print("🦅 Sunny Osprey — AI Security Monitor (Video Mode)")
    print("=" * 60)
    print(f"📋 Config: {config_path}")
    print(f"🎬 Input:  {input_path}")
    print("=" * 60)

    if not input_path:
        print("❌ Error: Please specify a video file or directory using --input <path>")
        return 1

    processor = VideoFileProcessor(config_path=config_path)

    if os.path.isdir(input_path):
        results = processor.process_directory(input_path)
        print(f"\n✅ Completed batch analysis of {len(results)} videos.")
    elif os.path.isfile(input_path):
        res = processor.process_video(input_path)
        is_susp = res.get("suspicious", "No").lower() == "yes"
        badge = "🚨 [SUSPICIOUS]" if is_susp else "✅ [NORMAL]"
        print(f"\n{badge} Result: {res.get('anomaly_type', 'Unknown')} (Severity: {res.get('severity', 'low')})")
        print(f"Details: {res.get('description', '')}")
    else:
        print(f"❌ Error: Path not found: {input_path}")
        return 1
    return 0


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Sunny Osprey AI Security Monitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes:
  webcam   Run laptop webcam pipeline with Ollama LLM (default)
           Requires: Ollama running at localhost:11434
           Hardware: 4GB VRAM (RTX 2050 compatible)

  video    Run anomaly detection on pre-recorded video file or folder
           Usage: python -m sunny_osprey.main --mode video --input path/to/video.mp4

  frigate  Run original Frigate/MQTT pipeline with HuggingFace
           Requires: Frigate NVR + MQTT broker
           Hardware: 9GB+ VRAM required
        """,
    )
    parser.add_argument(
        "--mode",
        choices=["webcam", "video", "frigate"],
        default="webcam",
        help="Operation mode (default: webcam)",
    )
    parser.add_argument(
        "--input",
        dest="input_path",
        default=None,
        help="Path to video file or directory (required for --mode video)",
    )
    parser.add_argument(
        "--config",
        default="sunny-osprey-config.yaml",
        help="Path to configuration file",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_arguments()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    if args.mode == "webcam":
        sys.exit(run_webcam_pipeline(config_path=args.config))
    elif args.mode == "video":
        sys.exit(run_video_pipeline(config_path=args.config, input_path=args.input_path))
    else:
        run_mqtt_processor(config_path=args.config)
