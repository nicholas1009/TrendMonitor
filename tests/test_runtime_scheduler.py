from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
import json
import io
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from trend_monitor.runtime import (
    ProcessLock,
    RuntimeConfig,
    RuntimeRunner,
    RuntimeSnapshotReader,
    RuntimeStore,
    due_periods,
    retry_action,
)
from trend_monitor.runtime.logging import runtime_logger
from trend_monitor.runtime.health import inspect_launch_agent
from trend_monitor.runtime.security import audit_dotenv
from trend_monitor.runtime.pipeline import (
    PipelineRefreshResult,
    RuntimeStageError,
    SubprocessMonitorPipeline,
    build_combined_result,
    classify_stage_failure,
)
from trend_monitor.runtime.schedule import period_identity
from trend_monitor.schemas.runtime import RuntimeRunRecord


ROOT = Path(__file__).resolve().parents[1]
CONFIG = RuntimeConfig.load(ROOT / "config" / "runtime_schedule.json", project_root=ROOT)
SHANGHAI = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 8, 28)


def periods(at: str, *, historical: bool = False):
    return due_periods(
        datetime.combine(DAY, datetime.strptime(at, "%H:%M").time(), tzinfo=SHANGHAI),
        trading_day=DAY,
        periods=CONFIG.raw["periods"],
        buffer_minutes=3,
        live_grace_minutes=10,
        closing_live_grace_minutes=20,
        historical_execution=historical,
    )


def source(period_end: str) -> dict:
    market = {
        "last_completed_bar_end": period_end,
        "rules_version": "market_60m_risk_v0.1",
        "risk_score": 2,
        "risk_light": "YELLOW",
        "risk_direction": "FLAT",
        "signal_confidence": "HIGH",
        "breadth": {"advance_count": 4},
        "persistent_weakness": {"count": 3, "points": 1},
        "downside_shocks": {"count": 1, "points": 1, "feature_unavailable": []},
        "weighted_support_distortion": False,
        "small_cap_stress": True,
        "broad_selloff_resonance": True,
        "strong_broad_weakness": False,
        "broad_repair": False,
        "repair_count": 0,
        "score_components": {
            "breadth_points": 1,
            "persistent_weakness_points": 1,
            "downside_shock_points": 0,
            "weighted_support_distortion_points": 0,
            "broad_repair_offset": 0,
        },
        "index_states": [{"instrument_id": str(i)} for i in range(8)],
        "data_quality": {"valid_index_count": 8, "preflight": {str(i): "PASS_WITH_DEGRADATION" for i in range(8)}},
    }
    market15 = {
        "60m_period_end": period_end,
        "rules_version": "market_15m_internal_v0.1",
        "market_internal_state": "INTERNAL_MIXED",
        "data_quality": {"lookahead_safe": True},
    }
    stocks = {}
    for instrument_id, symbol in (("stock.hengtong_optic", "600487"), ("stock.wus_printed_circuit", "002463")):
        stocks[instrument_id] = {
            "stock_60m": {
                "period_end": period_end,
                "rules_version": "stock_60m_risk_v0.1",
                "symbol": symbol,
                "name": instrument_id,
                "risk_score": 1,
                "risk_light": "YELLOW",
                "risk_direction": "FLAT",
                "confidence": "HIGH",
                "current_return": -0.01,
                "previous_return": -0.005,
                "two_period_return": -0.01495,
                "relative_return": -0.002,
                "market_relationship": "WEAKER_THAN_MARKET",
                "persistent_weakness": True,
                "downside_shock": False,
                "relative_weakness": False,
                "market_resonance": True,
                "repair_state": "NONE",
                "score_components": {
                    "persistent_weakness_points": 1,
                    "downside_shock_points": 0,
                    "relative_weakness_points": 0,
                    "market_resonance_points": 1,
                    "full_close_repair_offset": 0,
                },
                "data_quality": {"lookahead_safe": True},
            },
            "stock_15m": {
                "rules_version": "stock_15m_internal_v0.1",
                "classification": "MIXED",
                "direction_sequence": ["UP", "DOWN", "UP", "DOWN"],
                "joint_market_flags": ["JOINT_WEAKNESS"],
                "data_quality": {"lookahead_safe": True},
            },
        }
    return {
        "market": market,
        "market_15m": market15,
        "stocks": stocks,
        "source_ids": {
            "market_result_id": "market",
            "market_15m_result_id": "market15",
            "stock_result_ids": {key: key for key in stocks},
        },
        "source_safety": {
            "market_lookahead": True,
            "market_15m_lookahead": True,
            "stock_lookahead": True,
            "stock_score_immutable": True,
        },
    }


class StaticCalendar:
    def __init__(self, trading: bool):
        self.trading = trading

    def is_trading_day(self, value, *, allow_network, observed_at):
        return self.trading, "TEST_CALENDAR"


class FakeReader:
    def load_period(self, period_end):
        return source(period_end)


class FakePipeline:
    def __init__(self):
        self.calls = 0

    def refresh(self, *, as_of):
        self.calls += 1
        return PipelineRefreshResult(5, ({"status": "PASS"},))


class FailingPipeline:
    def __init__(self, category="PIPELINE_FAILED"):
        self.calls = 0
        self.category = category

    def refresh(self, *, as_of):
        self.calls += 1
        raise RuntimeStageError(
            self.category,
            "MARKET_15M_INTERNAL",
            "cached history coverage is incomplete",
        )


class FailureNotifier:
    def __init__(self):
        self.failure_calls = 0

    def process_runtime_failure(self, record, *, dry_run=False):
        self.failure_calls += 1
        return {"status": "SENT", "event_count": 1}


class ScheduleTests(unittest.TestCase):
    def test_trading_day_1030_resolution(self):
        self.assertEqual([p.period_end[11:16] for p in periods("10:33")], ["10:30"])

    def test_1130_resolution(self):
        self.assertEqual([p.period_end[11:16] for p in periods("11:33")], ["10:30", "11:30"])

    def test_1400_resolution(self):
        self.assertEqual([p.period_end[11:16] for p in periods("14:03")], ["10:30", "11:30", "14:00"])

    def test_1500_resolution(self):
        self.assertEqual([p.period_end[11:16] for p in periods("15:03")], ["10:30", "11:30", "14:00", "15:00"])

    def test_closing_period_uses_independent_provider_grace(self):
        self.assertEqual(periods("15:14")[-1].execution_mode, "LIVE_SCHEDULED")
        self.assertEqual(periods("15:23")[-1].execution_mode, "LIVE_SCHEDULED")
        self.assertEqual(periods("15:24")[-1].execution_mode, "CATCH_UP")

    def test_non_closing_periods_keep_existing_live_grace(self):
        self.assertEqual(periods("11:43")[-1].execution_mode, "LIVE_SCHEDULED")
        self.assertEqual(periods("11:44")[-1].execution_mode, "CATCH_UP")

    def test_lunch_has_no_extra_period(self):
        self.assertEqual([p.period_end[11:16] for p in periods("13:30")], ["10:30", "11:30"])

    def test_catch_up_marks_missed_periods(self):
        result = periods("14:50")
        self.assertEqual([p.execution_mode for p in result], ["CATCH_UP", "CATCH_UP", "CATCH_UP"])

    def test_china_timezone_not_host_timezone(self):
        tokyo = datetime(2026, 8, 28, 11, 33, tzinfo=ZoneInfo("Asia/Tokyo"))
        shanghai = tokyo.astimezone(SHANGHAI)
        self.assertEqual(shanghai.strftime("%H:%M"), "10:33")


class LockRetryTests(unittest.TestCase):
    def test_lock_blocks_concurrent_process(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "runner.lock"
            first = ProcessLock(path, stale_seconds=60)
            second = ProcessLock(path, stale_seconds=60)
            now = datetime.now(SHANGHAI)
            self.assertTrue(first.acquire(run_id="one", now=now))
            self.assertFalse(second.acquire(run_id="two", now=now))
            first.release()
            second.release()

    def test_stale_metadata_is_recovered(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "runner.lock"
            path.write_text(json.dumps({"pid": 999999, "created_at": (datetime.now(SHANGHAI) - timedelta(days=1)).isoformat()}), encoding="utf-8")
            lock = ProcessLock(path, stale_seconds=60)
            self.assertTrue(lock.acquire(run_id="new", now=datetime.now(SHANGHAI)))
            self.assertTrue(lock.previous_stale)
            lock.release()

    def test_transient_failure_retries(self):
        calls = []
        def action():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeStageError("NETWORK_ERROR", "TEST", "temporary")
            return "ok"
        result, attempts = retry_action(action, max_attempts=3, backoff_seconds=[0, 0], retryable_categories={"NETWORK_ERROR"}, sleeper=lambda _: None)
        self.assertEqual((result, attempts), ("ok", 3))

    def test_deterministic_failure_does_not_retry(self):
        calls = []
        def action():
            calls.append(1)
            raise RuntimeStageError("UNSUPPORTED", "TEST", "deterministic")
        with self.assertRaises(RuntimeStageError):
            retry_action(action, max_attempts=3, backoff_seconds=[0, 0], retryable_categories={"NETWORK_ERROR"}, sleeper=lambda _: None)
        self.assertEqual(len(calls), 1)

    def test_failure_classifier(self):
        self.assertEqual(classify_stage_failure("UNSUPPORTED period"), "UNSUPPORTED")
        self.assertEqual(classify_stage_failure("NETWORK_ERROR"), "NETWORK_ERROR")
        self.assertEqual(
            classify_stage_failure("TEMPORARY_PROVIDER_ERROR — closing bar not ready"),
            "TEMPORARY_PROVIDER_ERROR",
        )

    @patch("trend_monitor.runtime.pipeline.subprocess.run")
    def test_market_refresh_passes_as_of_and_records_failure_observability(self, run):
        raw = deepcopy(CONFIG.raw)
        raw["pipeline_stages"] = [
            {
                "name": "MARKET_DATA_REFRESH",
                "script": "scripts/verify_market_index_coverage.py",
            }
        ]
        raw["retry"] = {
            "max_attempts": 1,
            "backoff_seconds": [],
            "retryable_categories": raw["retry"]["retryable_categories"],
        }
        config = RuntimeConfig(raw, project_root=ROOT)
        run.return_value = type(
            "Completed",
            (),
            {
                "returncode": 1,
                "stdout": "coverage incomplete",
                "stderr": "provider detail",
            },
        )()
        stream = io.StringIO()
        logger = logging.getLogger(f"stage-failure-{id(self)}")
        logger.handlers.clear()
        logger.addHandler(logging.StreamHandler(stream))
        pipeline = SubprocessMonitorPipeline(ROOT, config, logger, secrets=())
        as_of = datetime(2026, 9, 3, 10, 33, 5, tzinfo=SHANGHAI)

        with self.assertRaises(RuntimeStageError) as raised:
            pipeline.refresh(as_of=as_of)

        error = raised.exception
        command = run.call_args.args[0]
        self.assertEqual(command[-2:], ["--as-of", as_of.isoformat()])
        self.assertEqual(error.exit_code, 1)
        self.assertIsNotNone(error.duration_seconds)
        self.assertEqual(error.stdout_tail, "coverage incomplete")
        self.assertEqual(error.stderr_tail, "provider detail")
        self.assertIn("status=FAILED", stream.getvalue())
        self.assertIn("exit_code=1", stream.getvalue())


class StoreSecurityTests(unittest.TestCase):
    def record(self, run_id="run"):
        return RuntimeRunRecord(
            run_id=run_id, scheduled_period=None, started_at="2026-08-28T10:33:00+08:00",
            completed_at="2026-08-28T10:33:01+08:00", duration_seconds=1, trading_date="2026-08-28",
            period_end=None, status="SUCCESS", network_attempts=0, market_result_id=None,
            market_15m_result_id=None, stock_result_ids={}, error_summary=None,
            rules_versions=CONFIG.rules_versions, execution_mode=None, notification_eligibility=None,
        )

    def test_append_only_manifest(self):
        with TemporaryDirectory() as tmp:
            store = RuntimeStore(tmp)
            store.append(self.record("one"), idempotency_key="one")
            store.append(self.record("two"), idempotency_key="two")
            self.assertEqual(len(store.entries()), 2)

    def test_duplicate_result_reuses_same_report(self):
        with TemporaryDirectory() as tmp:
            store = RuntimeStore(tmp)
            payload = {"period_end": "2026-08-28T10:30:00+08:00", "risk": 1, "generated_at": "one"}
            machine, human, digest = store.save_report(payload, "report", idempotency_key="key")
            record = self.record("one")
            record = RuntimeRunRecord(**{**record.to_dict(), "combined_result_id": machine})
            store.append(record, idempotency_key="key", result_sha256=digest, human_report_id=human)
            second = dict(payload, generated_at="two", execution_mode="CATCH_UP")
            self.assertEqual(store.save_report(second, "report", idempotency_key="key")[0], machine)

    def test_no_secret_in_rotating_log(self):
        with TemporaryDirectory() as tmp:
            secret = "super-secret-token"
            path = Path(tmp) / "runtime.log"
            logger = runtime_logger(path, secrets=(secret,), max_bytes=1000, backups=1)
            logger.error("access_token=%s", secret)
            for handler in logger.handlers:
                handler.flush()
            self.assertNotIn(secret, path.read_text(encoding="utf-8"))

    def test_env_permission_fails_closed(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "LONGBRIDGE_APP_KEY=a\nLONGBRIDGE_APP_SECRET=b\nLONGBRIDGE_ACCESS_TOKEN=c\nHITHINK_FINANCE_API_KEY=d\n",
                encoding="utf-8",
            )
            os.chmod(path, 0o644)
            result = audit_dotenv(path, CONFIG.raw["secret_keys"])
            self.assertEqual((result["status"], result["reason"]), ("FAIL", "ENV_PERMISSION_MUST_BE_0600"))


class LaunchAgentHealthTests(unittest.TestCase):
    class Store:
        @staticmethod
        def entries():
            return [
                {
                    "run_id": "launchd-observation",
                    "started_at": "2026-08-31T19:45:13+08:00",
                    "status": "SKIPPED_ALREADY_COMPLETED",
                    "execution_mode": "CATCH_UP",
                    "extra": {"trigger_source": "LAUNCHD"},
                }
            ]

    @staticmethod
    def command(returncode=0, stdout=""):
        return type(
            "CommandResult",
            (),
            {"returncode": returncode, "stdout": stdout, "stderr": ""},
        )()

    def setup_paths(self, root: Path) -> tuple[Path, Path]:
        installed = root / "Library" / "LaunchAgents" / "agent.plist"
        installed.parent.mkdir(parents=True)
        installed.write_text("plist", encoding="utf-8")
        program = root / "bin" / "uv"
        program.parent.mkdir(parents=True)
        program.write_text("uv", encoding="utf-8")
        (root / "logs" / "runtime").mkdir(parents=True)
        heartbeat = root / "data" / "runtime" / "launchd_heartbeat.json"
        heartbeat.parent.mkdir(parents=True)
        heartbeat.write_text(
            json.dumps(
                {
                    "observed_at": "2026-08-31T20:09:58+09:00",
                    "label": "com.trendmonitor.local.intraday",
                    "pid": 123,
                }
            ),
            encoding="utf-8",
        )
        return installed, program

    @patch("trend_monitor.runtime.health.subprocess.run")
    def test_loaded_launch_agent_reports_lifecycle_and_heartbeat(self, run):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            installed, program = self.setup_paths(root)
            launch_output = (
                "state = not running\n"
                f"program = {program}\n"
                f"working directory = {root.resolve()}\n"
                "runs = 71\n"
                "last exit code = 0\n"
                "run interval = 60 seconds\n"
            )
            run.side_effect = [
                self.command(stdout=launch_output),
                self.command(stdout='"com.trendmonitor.local.intraday" => enabled\n'),
            ]
            result = inspect_launch_agent(
                root,
                self.Store(),
                now=datetime(2026, 8, 31, 19, 10, tzinfo=SHANGHAI),
                installed_path=installed,
                uid=501,
            )
            self.assertEqual((result["status"], result["reason"]), ("PASS", None))
            self.assertTrue(result["loaded"])
            self.assertFalse(result["disabled"])
            self.assertEqual(result["run_interval_seconds"], 60)
            self.assertEqual(result["last_runner_heartbeat"]["status"], "OBSERVED")
            self.assertEqual(result["last_launch_observation"]["status"], "OBSERVED")

    @patch("trend_monitor.runtime.health.subprocess.run")
    def test_installed_but_unloaded_has_specific_reason(self, run):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            installed, _ = self.setup_paths(root)
            run.side_effect = [
                self.command(returncode=113),
                self.command(stdout="disabled services = {}\n"),
            ]
            result = inspect_launch_agent(
                root,
                self.Store(),
                now=datetime(2026, 8, 31, 19, 10, tzinfo=SHANGHAI),
                installed_path=installed,
                uid=501,
            )
            self.assertEqual(result["reason"], "LAUNCH_AGENT_NOT_LOADED")

    @patch("trend_monitor.runtime.health.subprocess.run")
    def test_disabled_launch_agent_has_specific_reason(self, run):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            installed, _ = self.setup_paths(root)
            run.side_effect = [
                self.command(returncode=113),
                self.command(stdout='"com.trendmonitor.local.intraday" => disabled\n'),
            ]
            result = inspect_launch_agent(
                root,
                self.Store(),
                now=datetime(2026, 8, 31, 19, 10, tzinfo=SHANGHAI),
                installed_path=installed,
                uid=501,
            )
            self.assertEqual(result["reason"], "LAUNCH_AGENT_DISABLED")


class SnapshotAndRunnerTests(unittest.TestCase):
    def test_real_replay_reader_and_lookahead(self):
        result = RuntimeSnapshotReader(ROOT).load_period("2026-08-28T15:00:00+08:00")
        period = periods("15:03", historical=True)[-1]
        combined = build_combined_result(result, scheduled_period=period, generated_at=datetime.now(SHANGHAI))
        self.assertTrue(combined["data"]["lookahead_safe"])
        self.assertEqual(combined["data"]["industry_context"], "DEFERRED")

    def test_combined_report_preserves_existing_risk_explanations(self):
        period = periods("10:33", historical=True)[0]
        combined = build_combined_result(
            source(period.period_end),
            scheduled_period=period,
            generated_at=datetime.now(SHANGHAI),
        )

        self.assertEqual(combined["market"]["score_components"]["breadth_points"], 1)
        self.assertEqual(combined["market"]["persistent_weakness"]["count"], 3)
        stock = combined["stocks"]["stock.hengtong_optic"]
        self.assertEqual(stock["score_components"]["market_resonance_points"], 1)
        self.assertEqual(stock["market_relationship"], "WEAKER_THAN_MARKET")
        self.assertEqual(stock["15m_joint_market_flags"], ["JOINT_WEAKNESS"])

    def _runner(self, tmp, *, trading=True, pipeline=None, notifier=None):
        logger = logging.getLogger(f"runtime-test-{tmp}")
        logger.handlers.clear(); logger.addHandler(logging.NullHandler())
        return RuntimeRunner(
            project_root=ROOT, config=CONFIG, calendar=StaticCalendar(trading),
            store=RuntimeStore(Path(tmp) / "runtime"), reader=FakeReader(),
            pipeline=pipeline, logger=logger, lock_path=Path(tmp) / "runner.lock",
            notifier=notifier,
        )

    def test_non_trading_day_skips_without_pipeline(self):
        with TemporaryDirectory() as tmp:
            pipeline = FakePipeline()
            result = self._runner(tmp, trading=False, pipeline=pipeline).run(
                as_of=datetime(2026, 8, 29, 10, 33, tzinfo=SHANGHAI)
            )
            self.assertEqual(result["status"], "SKIPPED_NON_TRADING_DAY")
            self.assertEqual(pipeline.calls, 0)

    def test_current_runner_and_duplicate_are_idempotent(self):
        with TemporaryDirectory() as tmp:
            pipeline = FakePipeline()
            runner = self._runner(tmp, pipeline=pipeline)
            now = datetime.now(SHANGHAI).replace(hour=10, minute=33, second=0, microsecond=0)
            first = runner.run(as_of=now)
            second = runner.run(as_of=now)
            self.assertEqual(first["results"][0]["status"], "SUCCESS_WITH_DEGRADATION")
            self.assertEqual(second["status"], "SKIPPED_ALREADY_COMPLETED")
            self.assertIsNotNone(second["combined_result_id"])
            self.assertEqual(second["market_result_id"], "market")
            self.assertEqual(pipeline.calls, 1)

    def test_historical_no_network_runner_catches_up(self):
        with TemporaryDirectory() as tmp:
            runner = self._runner(tmp, pipeline=None)
            result = runner.run(as_of=datetime(2026, 8, 28, 14, 50, tzinfo=SHANGHAI), no_network=True)
            self.assertEqual(len(result["results"]), 3)
            self.assertTrue(all(item["execution_mode"] == "CATCH_UP" for item in result["results"]))
            self.assertTrue(all(item["network_attempts"] == 0 for item in result["results"]))

    def test_frozen_rules_are_not_mutated(self):
        before = CONFIG.verify_frozen_rules()
        with TemporaryDirectory() as tmp:
            self._runner(tmp, pipeline=None).run(
                as_of=datetime(2026, 8, 28, 10, 33, tzinfo=SHANGHAI), no_network=True
            )
        self.assertEqual(CONFIG.verify_frozen_rules(), before)

    def test_runtime_failure_record_is_persisted(self):
        class BrokenReader:
            def load_period(self, period_end):
                raise ValueError("missing replay")
        with TemporaryDirectory() as tmp:
            logger = logging.getLogger(f"broken-{tmp}"); logger.handlers.clear(); logger.addHandler(logging.NullHandler())
            store = RuntimeStore(Path(tmp) / "runtime")
            runner = RuntimeRunner(
                project_root=ROOT, config=CONFIG, calendar=StaticCalendar(True), store=store,
                reader=BrokenReader(), pipeline=None, logger=logger, lock_path=Path(tmp) / "runner.lock",
            )
            result = runner.run(
                as_of=datetime(2026, 8, 28, 10, 33, tzinfo=SHANGHAI), no_network=True
            )
            self.assertEqual(result["results"][0]["status"], "FAILED")
            self.assertEqual(store.entries()[0]["error_summary"]["stage"], "COMBINED_RUNTIME_REPORT")

    def test_non_recoverable_failure_is_terminal_until_operator_force(self):
        with TemporaryDirectory() as tmp:
            pipeline = FailingPipeline()
            notifier = FailureNotifier()
            runner = self._runner(tmp, pipeline=pipeline, notifier=notifier)
            store = runner.store
            as_of = datetime(2026, 8, 28, 10, 33, tzinfo=SHANGHAI)

            first = runner.run(as_of=as_of)
            first_skip = runner.run(as_of=as_of)
            second_skip = runner.run(as_of=as_of)

            self.assertEqual(first["status"], "FAILED")
            self.assertFalse(first["error_summary"]["recoverable"])
            self.assertIsNotNone(first["idempotency_key"])
            self.assertEqual(first_skip["status"], "SKIPPED_TERMINAL_FAILURE")
            self.assertEqual(second_skip["status"], "SKIPPED_TERMINAL_FAILURE")
            self.assertEqual(
                first_skip["extra"]["skip_reason"],
                "NON_RECOVERABLE_FAILURE_ALREADY_RECORDED",
            )
            self.assertEqual(first_skip["skip_key"], second_skip["skip_key"])
            self.assertEqual(
                first_skip["extra"]["prior_terminal_failure_run_id"],
                first["run_id"],
            )
            self.assertEqual(second_skip["prior_terminal_failure_run_id"], first["run_id"])
            self.assertEqual(pipeline.calls, 1)
            self.assertEqual(notifier.failure_calls, 1)
            self.assertEqual(
                [item["status"] for item in store.entries()],
                ["FAILED", "SKIPPED_TERMINAL_FAILURE"],
            )

            forced = runner.run(as_of=as_of, force=True)
            self.assertEqual(forced["status"], "FAILED")
            self.assertEqual(pipeline.calls, 2)

    def test_legacy_terminal_failure_without_idempotency_key_is_detected(self):
        with TemporaryDirectory() as tmp:
            pipeline = FakePipeline()
            runner = self._runner(tmp, pipeline=pipeline)
            store = runner.store
            period = periods("10:33")[-1]
            legacy = RuntimeRunRecord(
                run_id="legacy-terminal",
                scheduled_period=period.to_dict(),
                started_at="2026-08-28T10:33:00+08:00",
                completed_at="2026-08-28T10:33:01+08:00",
                duration_seconds=1,
                trading_date=period.trading_date,
                period_end=period.period_end,
                status="FAILED",
                network_attempts=1,
                market_result_id=None,
                market_15m_result_id=None,
                stock_result_ids={},
                error_summary={
                    "stage": "MARKET_15M_INTERNAL",
                    "error_category": "PIPELINE_FAILED",
                    "retry_count": 0,
                    "recoverable": False,
                },
                rules_versions=CONFIG.rules_versions,
                execution_mode=period.execution_mode,
                notification_eligibility=period.notification_eligibility,
            )
            store.append(legacy)

            result = runner.run(
                as_of=datetime(2026, 8, 28, 10, 33, tzinfo=SHANGHAI)
            )

            self.assertEqual(result["status"], "SKIPPED_TERMINAL_FAILURE")
            self.assertEqual(result["extra"]["prior_terminal_failure_run_id"], "legacy-terminal")
            self.assertEqual(pipeline.calls, 0)

    def test_latest_terminal_failure_short_circuits_earlier_missing_periods(self):
        with TemporaryDirectory() as tmp:
            pipeline = FakePipeline()
            runner = self._runner(tmp, pipeline=pipeline)
            store = runner.store
            latest = periods("15:03")[-1]
            terminal = RuntimeRunRecord(
                run_id="latest-terminal",
                scheduled_period=latest.to_dict(),
                started_at="2026-08-28T15:03:00+08:00",
                completed_at="2026-08-28T15:03:01+08:00",
                duration_seconds=1,
                trading_date=latest.trading_date,
                period_end=latest.period_end,
                status="FAILED",
                network_attempts=1,
                market_result_id=None,
                market_15m_result_id=None,
                stock_result_ids={},
                error_summary={"recoverable": False},
                rules_versions=CONFIG.rules_versions,
                execution_mode=latest.execution_mode,
                notification_eligibility=latest.notification_eligibility,
            )
            store.append(terminal)

            result = runner.run(
                as_of=datetime(2026, 8, 28, 15, 3, tzinfo=SHANGHAI)
            )

            self.assertEqual(result["status"], "SKIPPED_TERMINAL_FAILURE")
            self.assertEqual(result["period_end"], latest.period_end)
            self.assertEqual(pipeline.calls, 0)

    def test_recoverable_failure_is_not_treated_as_terminal(self):
        with TemporaryDirectory() as tmp:
            pipeline = FailingPipeline("NETWORK_ERROR")
            runner = self._runner(tmp, pipeline=pipeline)
            as_of = datetime(2026, 8, 28, 10, 33, tzinfo=SHANGHAI)

            first = runner.run(as_of=as_of)
            second = runner.run(as_of=as_of)

            self.assertTrue(first["error_summary"]["recoverable"])
            self.assertTrue(second["error_summary"]["recoverable"])
            self.assertEqual(pipeline.calls, 2)


if __name__ == "__main__":
    unittest.main()
