"""
Phone Alarm Alert Test Utility for Gazeguard

Sends an immediate test alert to verify that your smartphone sounds the alarm!

Usage:
    python scripts/test_alert.py
    python scripts/test_alert.py --topic my-custom-topic
    python scripts/test_alert.py --backend ntfy
    python scripts/test_alert.py --backend telegram
"""

import argparse
import os
import sys
from datetime import datetime

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from sunny_osprey.alert_manager import AlertManager
from sunny_osprey.config import SunnyOspreyConfig


def main():
    parser = argparse.ArgumentParser(
        description="Test Phone Alarm Alert for Gazeguard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        default="gazeguard-config.yaml",
        help="Path to configuration file",
    )
    parser.add_argument(
        "--topic",
        default=None,
        help="Override ntfy topic name",
    )
    parser.add_argument(
        "--backend",
        choices=["ntfy", "telegram", "all"],
        default=None,
        help="Alert backend to test",
    )

    args = parser.parse_args()

    print("=" * 65)
    print("🚨 Gazeguard — Phone Alarm Test")
    print("=" * 65)

    config_obj = SunnyOspreyConfig(args.config)
    alerts_cfg = config_obj.get_alerts_config()

    if args.topic:
        if "ntfy" not in alerts_cfg:
            alerts_cfg["ntfy"] = {}
        alerts_cfg["ntfy"]["topic"] = args.topic

    if args.backend:
        alerts_cfg["backend"] = args.backend

    ntfy_topic = alerts_cfg.get("ntfy", {}).get("topic", "gazeguard-security-alert")
    print(f"📲 ntfy.sh Topic: {ntfy_topic}")
    print(f"👉 To receive on your phone:")
    print(f"   1. Install the 'ntfy' app from Google Play Store or Apple App Store.")
    print(f"   2. Open the app, tap '+' and subscribe to: {ntfy_topic}")
    print(f"   3. Ensure notification volume / alarm permission is enabled.")
    print("=" * 65)

    alert_mgr = AlertManager(alerts_cfg)

    test_event_id = f"test_{int(datetime.now().timestamp())}"
    sample_incident = {
        "event_id": test_event_id,
        "is_suspicious": True,
        "anomaly_type": "Intrusion / Trespassing",
        "severity": "critical",
        "description": "[TEST ALARM] Suspicious person detected attempting unauthorized entry. Please verify camera feed.",
        "llm_result": {
            "suspicious": "Yes",
            "anomaly_type": "Intrusion / Trespassing",
            "severity": "critical",
            "description": "[TEST ALARM] Suspicious person detected attempting unauthorized entry. Please verify camera feed.",
        }
    }

    print(f"🚀 Dispatching test alarm incident [{test_event_id}] ...")
    success = alert_mgr.send_incident(test_event_id, sample_incident["llm_result"])

    if success:
        print("\n✅ Alarm notification dispatched successfully!")
        print("🔔 Check your phone: your screen should wake with an urgent alarm notification!")
    else:
        print("\n⚠️ Notification dispatch returned false or had warnings.")


if __name__ == "__main__":
    main()
