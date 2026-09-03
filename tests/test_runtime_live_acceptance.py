from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from trend_monitor.runtime.acceptance import (
    acceptance_status,
    evaluate_live_slot,
    evaluate_restart,
    evaluate_sleep_wake,
    load_manifest,
    parse_power_events,
    redact_payload,
    save_observation,
    trigger_delay_seconds,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")


def record(*, source="LAUNCHD", mode="LIVE_SCHEDULED", period="10:30", status="SUCCESS"):
    day = "2026-09-01"
    return {
        "run_id": f"{source}-{mode}-{period}",
        "scheduled_period": {
            "scheduled_at": f"{day}T{period[:2]}:{int(period[3:]) + 3:02d}:00+08:00"
        },
        "started_at": f"{day}T{period[:2]}:{int(period[3:]) + 3:02d}:41+08:00",
        "trading_date": day,
        "period_end": f"{day}T{period}:00+08:00",
        "execution_mode": mode,
        "status": status,
        "network_attempts": 5,
        "extra": {
            "trigger_source": source,
            "launchd_label": "com.trendmonitor.local.intraday" if source == "LAUNCHD" else None,
            "as_of_override": False,
            "no_network": False,
            "force": False,
        },
    }


class RuntimeLiveAcceptanceTests(unittest.TestCase):
    def test_evidence_parser(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.jsonl"
            path.write_text('{"run_id":"a"}\n{"run_id":"b"}\n', encoding="utf-8")
            self.assertEqual([item["run_id"] for item in load_manifest(path)], ["a", "b"])

    def test_trigger_delay(self):
        self.assertEqual(trigger_delay_seconds(record()), 41.0)

    def test_manual_cannot_pass_as_live(self):
        result = evaluate_live_slot([record(source="MANUAL")], period_time="10:30")
        self.assertEqual(result["status"], "PENDING")

    def test_catch_up_cannot_pass_as_live(self):
        result = evaluate_live_slot([record(mode="CATCH_UP")], period_time="10:30")
        self.assertEqual(result["status"], "PENDING")

    @patch("trend_monitor.runtime.acceptance.provider_evidence", return_value={"status": "PASS"})
    @patch("trend_monitor.runtime.acceptance.validate_combined_result", return_value={"status": "PASS", "checks": {"lookahead_safe": True}})
    def test_launchd_live_pass(self, _combined, _providers):
        result = evaluate_live_slot([record()], period_time="10:30")
        self.assertEqual((result["status"], result["trigger_source"]), ("PASS", "LAUNCHD"))

    def test_pending_and_failure_aggregation(self):
        self.assertEqual(acceptance_status([{"status": "PASS"}, {"status": "PENDING"}]), "PENDING")
        self.assertEqual(acceptance_status([{"status": "PASS"}, {"status": "FAIL"}]), "FAIL")
        self.assertEqual(acceptance_status([{"status": "PASS"}, {"status": "PASS"}]), "PASS")

    def test_boot_time_comparison_restart_pass(self):
        baseline = {
            "system": {
                "boot_time": "2026-08-30T12:30:00+09:00",
                "launchd": {"plist": {"sha256": "same", "mtime": "unchanged"}},
            }
        }
        system = {
            "boot_time": "2026-09-01T12:30:00+09:00",
            "console_login_time": "2026-09-01T12:31:00+09:00",
            "launchd": {
                "loaded": True,
                "last_exit_code": 0,
                "plist": {"sha256": "same", "mtime": "unchanged"},
            },
        }
        item = record(period="14:00")
        item["started_at"] = "2026-09-01T14:03:41+08:00"
        self.assertEqual(evaluate_restart([item], baseline=baseline, system=system)["status"], "PASS")

    def test_failed_pipeline_still_verifies_post_restart_launchd_trigger(self):
        baseline = {
            "system": {
                "boot_time": "2026-08-30T12:30:00+09:00",
                "launchd": {"plist": {"sha256": "same", "mtime": "unchanged"}},
            }
        }
        system = {
            "boot_time": "2026-09-03T06:30:00+09:00",
            "console_login_time": "2026-09-03T06:31:00+09:00",
            "launchd": {
                "loaded": True,
                "last_exit_code": 0,
                "plist": {"sha256": "same", "mtime": "unchanged"},
            },
        }
        item = record(period="10:30", status="FAILED")
        item["started_at"] = "2026-09-03T10:33:05+08:00"
        item["completed_at"] = "2026-09-03T10:34:16+08:00"

        result = evaluate_restart([item], baseline=baseline, system=system)

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["first_post_restart_run_status"], "FAILED")

    def test_power_event_parser_distinguishes_operator_sleep(self):
        text = (
            "2026-09-01 15:59:00 +0900 Sleep                Entering Sleep state due to 'Idle Sleep' Using AC\n"
            "2026-09-01 16:20:00 +0900 Wake                 Wake from Deep Idle due to HID Activity Using AC\n"
        )
        values = parse_power_events(text)
        self.assertEqual([item["event"] for item in values], ["SLEEP", "WAKE"])
        self.assertTrue(values[0]["operator_candidate"])

    def test_catch_up_failure_without_matching_sleep_boundary_is_pending(self):
        item = record(mode="CATCH_UP", status="FAILED")
        item["extra"]["missed_completed_period"] = True
        events = [
            {
                "event": "SLEEP",
                "timestamp": "2026-08-30T17:49:14+09:00",
                "operator_candidate": True,
                "message": "earlier sleep",
            },
            {
                "event": "WAKE",
                "timestamp": "2026-08-30T18:14:58+09:00",
                "operator_candidate": True,
                "message": "earlier wake",
            },
        ]
        result = evaluate_sleep_wake([item], events)
        self.assertEqual(
            (result["status"], result["reason"]),
            ("PENDING", "NO_SLEEP_BOUNDARY_CATCH_UP_EVIDENCE"),
        )

    def test_catch_up_failure_after_matching_wake_is_failure(self):
        item = record(mode="CATCH_UP", status="FAILED")
        item["extra"]["missed_completed_period"] = True
        item["started_at"] = "2026-09-01T11:00:00+08:00"
        events = [
            {
                "event": "SLEEP",
                "timestamp": "2026-09-01T10:00:00+08:00",
                "operator_candidate": True,
                "message": "test sleep",
            },
            {
                "event": "WAKE",
                "timestamp": "2026-09-01T10:45:00+08:00",
                "operator_candidate": True,
                "message": "test wake",
            },
        ]
        result = evaluate_sleep_wake([item], events)
        self.assertEqual(
            (result["status"], result["reason"]),
            ("FAIL", "LAUNCHD_CATCH_UP_FAILED"),
        )

    def test_secret_redaction(self):
        secret = "do-not-store-this"
        value = redact_payload({"note": f"token={secret}", "access_token": secret, "secret_audit": "PASS"}, [secret])
        serialized = json.dumps(value)
        self.assertNotIn(secret, serialized)
        self.assertEqual(value["access_token"], "[REDACTED]")
        self.assertEqual(value["secret_audit"], "PASS")

    def test_acceptance_evidence_is_append_only(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = {"observed_at": "2026-09-01T10:00:00+08:00", "acceptance_status": "PENDING"}
            second = {"observed_at": "2026-09-01T11:00:00+08:00", "acceptance_status": "PENDING"}
            one = save_observation(root, first)
            two = save_observation(root, second)
            self.assertNotEqual(one["evidence_path"], two["evidence_path"])
            self.assertEqual(len((root / "manifest.jsonl").read_text().splitlines()), 2)
            latest = json.loads((root / "runtime_live_acceptance_latest.json").read_text())
            self.assertEqual(latest["observed_at"], second["observed_at"])


if __name__ == "__main__":
    unittest.main()
