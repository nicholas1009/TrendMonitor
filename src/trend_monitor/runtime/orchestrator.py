"""TASK_013 production runner state machine."""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
import time
from typing import Any
from uuid import uuid4

from trend_monitor.schemas.runtime import RuntimeRunRecord

from .lock import ProcessLock
from .pipeline import RuntimeStageError, build_combined_result, render_combined_report
from .schedule import due_periods, period_identity
from .security import audit_dotenv


class RuntimeRunner:
    def __init__(
        self,
        *,
        project_root: str | Path,
        config: Any,
        calendar: Any,
        store: Any,
        reader: Any,
        pipeline: Any | None,
        logger: logging.Logger,
        clock: Any = time.monotonic,
        lock_path: str | Path | None = None,
        invocation_metadata: dict[str, Any] | None = None,
        notifier: Any | None = None,
    ):
        self.root = Path(project_root).resolve()
        self.config = config
        self.calendar = calendar
        self.store = store
        self.reader = reader
        self.pipeline = pipeline
        self.logger = logger
        self.clock = clock
        self.lock_path = Path(lock_path).resolve() if lock_path else self.root / "data" / "runtime" / "runner.lock"
        self.invocation_metadata = dict(invocation_metadata or {"trigger_source": "MANUAL"})
        self.notifier = notifier

    def _notify_failure(self, record: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
        if self.notifier is None:
            return {"status": "SKIPPED_DISABLED", "event_count": 0}
        try:
            return self.notifier.process_runtime_failure(record, dry_run=dry_run)
        except Exception:
            self.logger.error("stage=NOTIFICATION status=FAILED category=NOTIFICATION_INTERNAL_ERROR")
            return {
                "status": "FAILED",
                "event_count": 0,
                "error_category": "NOTIFICATION_INTERNAL_ERROR",
            }

    def _notify_combined(
        self,
        *,
        source: dict[str, Any],
        combined: dict[str, Any],
        source_result_id: str,
        dry_run: bool,
    ) -> dict[str, Any]:
        if self.notifier is None:
            return {"status": "SKIPPED_DISABLED", "event_count": 0}
        previous = None
        load_previous = getattr(self.reader, "load_previous_period", None)
        if callable(load_previous):
            try:
                previous = load_previous(str(combined["period_end"]))
            except Exception:
                self.logger.warning(
                    "stage=NOTIFICATION_PREVIOUS_STATE status=UNAVAILABLE period_end=%s",
                    combined.get("period_end"),
                )
        try:
            return self.notifier.process_combined(
                source,
                previous,
                combined,
                source_result_id=source_result_id,
                dry_run=dry_run,
            )
        except Exception:
            self.logger.error("stage=NOTIFICATION status=FAILED category=NOTIFICATION_INTERNAL_ERROR")
            return {
                "status": "FAILED",
                "event_count": 0,
                "error_category": "NOTIFICATION_INTERNAL_ERROR",
            }

    def _record(
        self,
        *,
        run_id: str,
        started: datetime,
        completed: datetime,
        trading_date: str,
        status: str,
        scheduled_period: Any | None = None,
        attempts: int = 0,
        source_ids: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        combined_result_id: str | None = None,
        idempotency_key: str | None = None,
        result_sha256: str | None = None,
        human_report_id: str | None = None,
        skip_key: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        source_ids = source_ids or {}
        record_extra = dict(self.invocation_metadata)
        record_extra.update(extra or {})
        record = RuntimeRunRecord(
            run_id=run_id,
            scheduled_period=scheduled_period.to_dict() if scheduled_period else None,
            started_at=started.isoformat(),
            completed_at=completed.isoformat(),
            duration_seconds=max(0.0, (completed - started).total_seconds()),
            trading_date=trading_date,
            period_end=scheduled_period.period_end if scheduled_period else None,
            status=status,
            network_attempts=attempts,
            market_result_id=source_ids.get("market_result_id"),
            market_15m_result_id=source_ids.get("market_15m_result_id"),
            stock_result_ids=source_ids.get("stock_result_ids", {}),
            error_summary=error,
            rules_versions=self.config.rules_versions,
            execution_mode=scheduled_period.execution_mode if scheduled_period else None,
            notification_eligibility=(scheduled_period.notification_eligibility if scheduled_period else None),
            combined_result_id=combined_result_id,
            extra=record_extra,
        )
        self.store.append(
            record,
            idempotency_key=idempotency_key,
            result_sha256=result_sha256,
            human_report_id=human_report_id,
            skip_key=skip_key,
        )
        payload = record.to_dict()
        payload.update(
            {
                "idempotency_key": idempotency_key,
                "result_sha256": result_sha256,
                "human_report_id": human_report_id,
                "skip_key": skip_key,
            }
        )
        return payload

    def dry_run(self, *, as_of: datetime, no_network: bool) -> dict[str, Any]:
        hashes = self.config.verify_frozen_rules()
        env = audit_dotenv(self.root / ".env", self.config.raw["secret_keys"])
        launchd_template = self.root / "config" / "launchd" / "com.trendmonitor.local.intraday.plist"
        if env["status"] != "PASS":
            raise ValueError(str(env["reason"]))
        trading, source = self.calendar.is_trading_day(
            as_of.date(), allow_network=False, observed_at=as_of
        )
        periods = due_periods(
            as_of,
            trading_day=as_of.date(),
            periods=self.config.raw["periods"],
            buffer_minutes=int(self.config.raw["buffer_minutes"]),
            live_grace_minutes=int(self.config.raw["live_grace_minutes"]),
            historical_execution=no_network,
        ) if trading else ()
        return {
            "status": "DRY_RUN_PASS",
            "as_of": as_of.isoformat(),
            "timezone": "Asia/Shanghai",
            "trading_day": trading,
            "calendar_source": source,
            "due_periods": [item.to_dict() for item in periods],
            "credential_presence": env["credentials"],
            "env_mode": env["mode"],
            "launchd_template": "PASS" if launchd_template.is_file() else "FAIL",
            "frozen_rules": hashes,
            "invocation": self.invocation_metadata,
            "production_writes": False,
            "network_used": False,
        }

    def run(
        self,
        *,
        as_of: datetime,
        no_network: bool = False,
        force: bool = False,
        notification_dry_run: bool = False,
    ) -> dict[str, Any]:
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        as_of = as_of.astimezone(self.config.timezone)
        started = datetime.now(self.config.timezone)
        invocation = uuid4().hex
        self.config.verify_frozen_rules()
        env = audit_dotenv(self.root / ".env", self.config.raw["secret_keys"])
        if env["status"] != "PASS":
            error = {"stage": "SECURITY_GATE", "error_category": env["reason"], "retry_count": 0, "recoverable": False}
            record = self._record(
                run_id=invocation,
                started=started,
                completed=datetime.now(self.config.timezone),
                trading_date=as_of.date().isoformat(),
                status="FAILED",
                error=error,
            )
            record["notification"] = (
                {"status": "SKIPPED_POLICY", "event_count": 0, "reason": "SECURITY_GATE"}
                if env["reason"] == "ENV_PERMISSION_MUST_BE_0600"
                else self._notify_failure(record, dry_run=notification_dry_run)
            )
            return record
        try:
            trading, calendar_source = self.calendar.is_trading_day(
                as_of.date(), allow_network=not no_network, observed_at=as_of
            )
        except Exception as exc:
            record = self._record(
                run_id=invocation,
                started=started,
                completed=datetime.now(self.config.timezone),
                trading_date=as_of.date().isoformat(),
                status="FAILED",
                error={"stage": "TRADING_DAY_GATE", "error_category": "CALENDAR_UNAVAILABLE", "retry_count": 0, "recoverable": True, "message": str(exc)},
            )
            record["notification"] = self._notify_failure(
                record, dry_run=notification_dry_run
            )
            return record
        if not trading:
            skip_key = f"NON_TRADING|{as_of.date().isoformat()}"
            if self.store.skipped_already_recorded(skip_key):
                return {"status": "SKIPPED_NON_TRADING_DAY", "already_recorded": True}
            return self._record(
                run_id=invocation,
                started=started,
                completed=datetime.now(self.config.timezone),
                trading_date=as_of.date().isoformat(),
                status="SKIPPED_NON_TRADING_DAY",
                skip_key=skip_key,
                extra={"calendar_source": calendar_source},
            )
        historical = no_network or as_of.date() != datetime.now(self.config.timezone).date()
        periods = due_periods(
            as_of,
            trading_day=as_of.date(),
            periods=self.config.raw["periods"],
            buffer_minutes=int(self.config.raw["buffer_minutes"]),
            live_grace_minutes=int(self.config.raw["live_grace_minutes"]),
            historical_execution=historical,
        )
        if not periods:
            return {"status": "NO_DUE_PERIOD", "trading_date": as_of.date().isoformat()}
        lock = ProcessLock(self.lock_path, stale_seconds=int(self.config.raw["lock_stale_seconds"]))
        if not lock.acquire(run_id=invocation, now=started):
            return self._record(
                run_id=invocation,
                started=started,
                completed=datetime.now(self.config.timezone),
                trading_date=as_of.date().isoformat(),
                status="SKIPPED_ALREADY_RUNNING",
                scheduled_period=periods[-1],
                skip_key=f"ALREADY_RUNNING|{as_of.date().isoformat()}|{periods[-1].period_end}",
                extra={"lock": lock.blocking_metadata or {}},
            )
        try:
            targets = []
            terminal_failures = []
            for period in periods:
                key = period_identity(period, self.config.rules_versions)
                if force:
                    targets.append((period, key))
                    continue
                if self.store.completed(key) is not None:
                    continue
                terminal = self.store.terminal_failure(key)
                if terminal is not None:
                    terminal_failures.append((period, key, terminal))
                    continue
                targets.append((period, key))
            latest_period = periods[-1]
            latest_key = period_identity(latest_period, self.config.rules_versions)
            latest_terminal = next(
                (
                    terminal
                    for period, key, terminal in reversed(terminal_failures)
                    if key == latest_key
                ),
                None,
            )
            if latest_terminal is not None:
                skip_key = f"TERMINAL_FAILED|{latest_key}"
                skip_reason = "NON_RECOVERABLE_FAILURE_ALREADY_RECORDED"
                if self.store.skipped_already_recorded(skip_key):
                    return {
                        "status": "SKIPPED_TERMINAL_FAILURE",
                        "already_recorded": True,
                        "period_end": latest_period.period_end,
                        "skip_key": skip_key,
                        "skip_reason": skip_reason,
                        "prior_terminal_failure_run_id": latest_terminal.get("run_id"),
                    }
                return self._record(
                    run_id=invocation,
                    started=started,
                    completed=datetime.now(self.config.timezone),
                    trading_date=latest_period.trading_date,
                    status="SKIPPED_TERMINAL_FAILURE",
                    scheduled_period=latest_period,
                    idempotency_key=latest_key,
                    skip_key=skip_key,
                    extra={
                        "skip_reason": skip_reason,
                        "prior_terminal_failure_run_id": latest_terminal.get("run_id"),
                    },
                )
            if not targets:
                latest, key = periods[-1], period_identity(periods[-1], self.config.rules_versions)
                existing = self.store.completed(key) or {}
                skip_key = f"ALREADY_COMPLETED|{key}"
                if self.store.skipped_already_recorded(skip_key):
                    return {"status": "SKIPPED_ALREADY_COMPLETED", "already_recorded": True, "period_end": latest.period_end}
                return self._record(
                    run_id=invocation,
                    started=started,
                    completed=datetime.now(self.config.timezone),
                    trading_date=as_of.date().isoformat(),
                    status="SKIPPED_ALREADY_COMPLETED",
                    scheduled_period=latest,
                    source_ids={
                        "market_result_id": existing.get("market_result_id"),
                        "market_15m_result_id": existing.get("market_15m_result_id"),
                        "stock_result_ids": existing.get("stock_result_ids", {}),
                    },
                    combined_result_id=existing.get("combined_result_id"),
                    idempotency_key=key,
                    skip_key=skip_key,
                )
            refresh = None
            if not no_network:
                if self.pipeline is None:
                    raise ValueError("production pipeline is unavailable")
                refresh = self.pipeline.refresh(as_of=as_of)
            results = []
            for index, (period, key) in enumerate(targets):
                run_id = f"{invocation}-{index + 1}"
                try:
                    source = self.reader.load_period(period.period_end)
                    combined = build_combined_result(source, scheduled_period=period, generated_at=started)
                    machine, human, digest = self.store.save_report(
                        combined, render_combined_report(combined), idempotency_key=key
                    )
                    record = self._record(
                            run_id=run_id,
                            started=started,
                            completed=datetime.now(self.config.timezone),
                            trading_date=period.trading_date,
                            status=combined["status"],
                            scheduled_period=period,
                            attempts=refresh.attempts if refresh else 0,
                            source_ids=source["source_ids"],
                            combined_result_id=machine,
                            idempotency_key=key,
                            result_sha256=digest,
                            human_report_id=human,
                            extra={"missed_completed_period": period.execution_mode == "CATCH_UP", "stale_lock_recovered": lock.previous_stale},
                        )
                    record["notification"] = self._notify_combined(
                        source=source,
                        combined=combined,
                        source_result_id=machine,
                        dry_run=notification_dry_run,
                    )
                    results.append(record)
                except Exception as exc:
                    record = self._record(
                            run_id=run_id,
                            started=started,
                            completed=datetime.now(self.config.timezone),
                            trading_date=period.trading_date,
                            status="FAILED",
                            scheduled_period=period,
                            attempts=refresh.attempts if refresh else 0,
                            error={"stage": "COMBINED_RUNTIME_REPORT", "error_category": "DATA_INCOMPLETE", "retry_count": 0, "recoverable": True, "message": str(exc)},
                            idempotency_key=key,
                        )
                    record["notification"] = self._notify_failure(
                        record, dry_run=notification_dry_run
                    )
                    results.append(record)
            return {"status": "BATCH_COMPLETE", "results": results}
        except RuntimeStageError as exc:
            failed_period, failed_key = (
                targets[-1]
                if 'targets' in locals() and targets
                else (
                    periods[-1],
                    period_identity(periods[-1], self.config.rules_versions),
                )
            )
            record = self._record(
                run_id=invocation,
                started=started,
                completed=datetime.now(self.config.timezone),
                trading_date=as_of.date().isoformat(),
                status="FAILED",
                scheduled_period=failed_period,
                attempts=exc.attempts,
                error={
                    "stage": exc.stage,
                    "error_category": exc.category,
                    "retry_count": max(0, exc.attempts - 1),
                    "recoverable": exc.category in set(self.config.raw["retry"]["retryable_categories"]),
                    "message": str(exc),
                    "command": list(exc.command),
                    "exit_code": exc.exit_code,
                    "duration_seconds": exc.duration_seconds,
                    "stdout_tail": exc.stdout_tail,
                    "stderr_tail": exc.stderr_tail,
                },
                idempotency_key=failed_key,
            )
            record["notification"] = self._notify_failure(
                record, dry_run=notification_dry_run
            )
            return record
        except Exception as exc:
            failed_period, failed_key = (
                targets[-1]
                if 'targets' in locals() and targets
                else (
                    periods[-1],
                    period_identity(periods[-1], self.config.rules_versions),
                )
            )
            record = self._record(
                run_id=invocation,
                started=started,
                completed=datetime.now(self.config.timezone),
                trading_date=as_of.date().isoformat(),
                status="FAILED",
                scheduled_period=failed_period,
                error={"stage": "RUNTIME", "error_category": "SCHEMA_OR_CONTRACT_ERROR", "retry_count": 0, "recoverable": False, "message": str(exc)},
                idempotency_key=failed_key,
            )
            record["notification"] = self._notify_failure(
                record, dry_run=notification_dry_run
            )
            return record
        finally:
            lock.release()
