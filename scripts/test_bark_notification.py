#!/usr/bin/env python3
"""Explicit manual Bark channel test; never sends unless --send is present."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from trend_monitor.notifications import (  # noqa: E402
    BarkAdapter,
    BarkConfig,
    NotificationPolicy,
    NotificationPolicyConfig,
    NotificationService,
    NotificationStore,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--send",
        action="store_true",
        help="send exactly one event_type=TEST notification through Bark",
    )
    args = parser.parse_args()
    policy_config = NotificationPolicyConfig.load(
        PROJECT_ROOT / "config" / "notification_policy.json"
    )
    bark_config = BarkConfig.load(PROJECT_ROOT / ".env")
    service = NotificationService(
        bark_config=bark_config,
        policy_config=policy_config,
        policy=NotificationPolicy(policy_config),
        adapter=BarkAdapter(bark_config, policy_config),
        store=NotificationStore(PROJECT_ROOT / "data" / "notifications"),
    )
    result = service.process_test(send=args.send)
    output = {
        "event_type": "TEST",
        "send_requested": args.send,
        "config": bark_config.safe_summary(),
        "status": result["status"],
        "attempts": sum(int(item.get("attempts", 0)) for item in result["records"]),
        "bark_channel": "VERIFIED" if result["status"] == "SENT" else "NOT_VERIFIED",
        "secret_exposed": False,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if args.send:
        return 0 if result["status"] == "SENT" else 2
    return 0 if result["status"] in {"WOULD_SEND", "SKIPPED_DISABLED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
