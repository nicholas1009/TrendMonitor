"""TASK_016 Hithink 09:25 final-auction runtime event."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time, timezone
import logging
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import uuid4

from trend_monitor.cache import RawCache
from trend_monitor.schemas import DataType, SourceTrace

from .lock import ProcessLock


AUCTION_EVENT_TYPE = "AUCTION_FINAL_SNAPSHOT"
AUCTION_ENDPOINT = "/api/a-share/auction/snapshot"
AUCTION_STAGE = "final"
AUCTION_VERSION = "auction_final_snapshot_v0.1"
AUCTION_INSTRUMENT_IDS = (
    "stock.hengtong_optic",
    "stock.wus_printed_circuit",
)
FINAL_AUCTION_PHASE = "closed"
FINAL_DATA_STATUS = "final"
AUCTION_START = time(9, 25)
# Provisional provider grace: Hithink returned final/matched through the old
# 09:27:59 boundary on 2026-09-03 and 2026-09-04.  Keep the wait finite while
# more live closed/final availability samples are collected.
AUCTION_FINAL_WAIT_WINDOW_END = time(9, 32, 59, 999999)

AUCTION_ITEM_FIELDS = (
    "thscode",
    "ticker",
    "name",
    "auction_price",
    "auction_pct",
    "auction_volume",
    "auction_amount",
    "auction_unmatched",
    "auction_turnover_pct",
    "auction_yesterday_ratio_pct",
    "auction_volume_ratio",
    "pre_close_price",
    "open_price",
    "last_price",
    "float_market_cap",
)
AUCTION_NUMERIC_FIELDS = frozenset(AUCTION_ITEM_FIELDS[3:])


@dataclass(frozen=True, slots=True)
class AuctionTarget:
    instrument_id: str
    symbol: str
    name: str


def resolve_auction_targets(registry: Any) -> tuple[AuctionTarget, ...]:
    """Use only the existing verified Hithink registry mappings."""

    targets: list[AuctionTarget] = []
    for instrument_id in AUCTION_INSTRUMENT_IDS:
        mapping = registry.resolve(instrument_id, "hithink")
        instrument = registry.get_instrument(instrument_id)
        if (
            mapping.provider_symbol is None
            or str(mapping.status) != "VERIFIED"
            or str(mapping.mapping_type) != "EXACT"
        ):
            raise ValueError(f"auction target is not an exact verified mapping: {instrument_id}")
        targets.append(
            AuctionTarget(
                instrument_id=instrument_id,
                symbol=mapping.provider_symbol,
                name=mapping.provider_name or instrument.display_name,
            )
        )
    return tuple(targets)


def parse_auction_snapshot(
    raw: dict[str, Any],
    *,
    expected_symbols: Iterable[str],
) -> dict[str, Any]:
    """Validate the official response without inventing or coercing values."""

    data = raw.get("data")
    if not isinstance(data, dict):
        raise ValueError("auction response data must be an object")
    if not isinstance(data.get("timestamp"), int) or isinstance(data.get("timestamp"), bool):
        raise ValueError("auction response timestamp must be an integer")
    phase = data.get("auction_phase")
    status = data.get("data_status")
    if not isinstance(phase, str) or not isinstance(status, str):
        raise ValueError("auction response is missing phase/status")
    items = data.get("item")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ValueError("auction response item must be an array of objects")

    expected = tuple(expected_symbols)
    by_symbol: dict[str, dict[str, Any]] = {}
    for item in items:
        symbol = item.get("thscode")
        if not isinstance(symbol, str):
            raise ValueError("auction item is missing thscode")
        if symbol in by_symbol:
            raise ValueError(f"auction response contains duplicate symbol: {symbol}")
        for field in ("ticker", "name"):
            if not isinstance(item.get(field), str):
                raise ValueError(f"auction item {symbol} is missing {field}")
        for field in AUCTION_NUMERIC_FIELDS:
            value = item.get(field)
            if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)):
                raise ValueError(f"auction item {symbol} has invalid {field}")
        by_symbol[symbol] = item

    missing = [symbol for symbol in expected if symbol not in by_symbol]
    final = phase == FINAL_AUCTION_PHASE and status == FINAL_DATA_STATUS and not missing
    return {
        "timestamp": data["timestamp"],
        "auction_phase": phase,
        "data_status": status,
        "items": [by_symbol[symbol] for symbol in expected if symbol in by_symbol],
        "missing_symbols": missing,
        "final": final,
    }


class AuctionRunner:
    """One bounded Auction attempt per existing launchd tick."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        calendar: Any,
        store: Any,
        provider_factory: Callable[[], Any],
        targets: Iterable[AuctionTarget],
        notifier: Any | None,
        logger: logging.Logger,
        lock_stale_seconds: int,
    ) -> None:
        self.root = Path(project_root).resolve()
        self.calendar = calendar
        self.store = store
        self.provider_factory = provider_factory
        self.targets = tuple(targets)
        self.notifier = notifier
        self.logger = logger
        self.lock_stale_seconds = lock_stale_seconds
        if tuple(target.symbol for target in self.targets) != (
            "600487.SH",
            "002463.SZ",
        ):
            raise ValueError("TASK_016 auction scope changed")

    @staticmethod
    def _key(trading_date: str) -> str:
        return f"{trading_date}|{AUCTION_EVENT_TYPE}"

    @staticmethod
    def _scheduled_at(as_of: datetime) -> str:
        return datetime.combine(as_of.date(), AUCTION_START, tzinfo=as_of.tzinfo).isoformat()

    @classmethod
    def _time_semantics(cls, as_of: datetime) -> dict[str, str]:
        return {
            "auction_market_time": cls._scheduled_at(as_of),
            "provider_observed_at": as_of.isoformat(),
        }

    def _notify_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        source_result_id: str,
        dry_run: bool,
    ) -> dict[str, Any]:
        if self.notifier is None:
            return {"status": "SKIPPED_DISABLED", "event_count": 0}
        try:
            return self.notifier.process_auction_snapshot(
                snapshot,
                source_result_id=source_result_id,
                dry_run=dry_run,
            )
        except Exception:
            self.logger.error(
                "stage=AUCTION_NOTIFICATION status=FAILED category=NOTIFICATION_INTERNAL_ERROR"
            )
            return {
                "status": "FAILED",
                "event_count": 0,
                "error_category": "NOTIFICATION_INTERNAL_ERROR",
            }

    def _notify_failure(
        self,
        record: dict[str, Any],
        *,
        dry_run: bool,
    ) -> dict[str, Any]:
        if self.notifier is None:
            return {"status": "SKIPPED_DISABLED", "event_count": 0}
        try:
            return self.notifier.process_auction_failure(record, dry_run=dry_run)
        except Exception:
            self.logger.error(
                "stage=AUCTION_NOTIFICATION status=FAILED category=NOTIFICATION_INTERNAL_ERROR"
            )
            return {
                "status": "FAILED",
                "event_count": 0,
                "error_category": "NOTIFICATION_INTERNAL_ERROR",
            }

    def _failure_record(
        self,
        *,
        as_of: datetime,
        execution_mode: str,
        parsed: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        trading_date = as_of.date().isoformat()
        incomplete_symbols = (
            parsed["missing_symbols"]
            if parsed and parsed["missing_symbols"]
            else [target.symbol for target in self.targets]
        )
        names = {
            target.symbol: target.name
            for target in self.targets
        }
        record = {
            "schema_version": 1,
            "run_id": uuid4().hex,
            "event_type": AUCTION_EVENT_TYPE,
            "event_version": AUCTION_VERSION,
            "idempotency_key": self._key(trading_date),
            "trading_date": trading_date,
            "scheduled_at": self._scheduled_at(as_of),
            **self._time_semantics(as_of),
            "execution_mode": execution_mode,
            "started_at": as_of.isoformat(),
            "completed_at": as_of.isoformat(),
            "status": "FAILED",
            "failure_reason": "DATA_NOT_READY",
            "provider": "HITHINK",
            "endpoint": AUCTION_ENDPOINT,
            "symbols": [target.symbol for target in self.targets],
            "stage": AUCTION_STAGE,
            "data_status": parsed.get("data_status") if parsed else None,
            "auction_phase": parsed.get("auction_phase") if parsed else None,
            "raw_snapshot_id": None,
            "raw_snapshot_path": None,
            "incomplete_symbols": incomplete_symbols,
            "incomplete_names": [names[symbol] for symbol in incomplete_symbols],
        }
        self.store.append_event(record)
        return record

    def _attempt_record(
        self,
        *,
        as_of: datetime,
        parsed: dict[str, Any] | None = None,
        error_category: str | None = None,
    ) -> dict[str, Any]:
        trading_date = as_of.date().isoformat()
        record = {
            "schema_version": 1,
            "run_id": uuid4().hex,
            "event_type": AUCTION_EVENT_TYPE,
            "event_version": AUCTION_VERSION,
            "idempotency_key": self._key(trading_date),
            "trading_date": trading_date,
            "scheduled_at": self._scheduled_at(as_of),
            **self._time_semantics(as_of),
            "execution_mode": "LIVE_SCHEDULED",
            "started_at": as_of.isoformat(),
            "completed_at": as_of.isoformat(),
            "status": "DATA_NOT_READY",
            "provider": "HITHINK",
            "endpoint": AUCTION_ENDPOINT,
            "symbols": [target.symbol for target in self.targets],
            "stage": AUCTION_STAGE,
            "data_status": parsed.get("data_status") if parsed else None,
            "auction_phase": parsed.get("auction_phase") if parsed else None,
            "error_category": error_category,
            "raw_snapshot_id": None,
            "raw_snapshot_path": None,
        }
        self.store.append_event(record)
        return record

    def run(
        self,
        *,
        as_of: datetime,
        no_network: bool = False,
        catch_up: bool = False,
        dry_run: bool = False,
        notification_dry_run: bool = False,
    ) -> dict[str, Any]:
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        trading_date = as_of.date().isoformat()
        current_time = as_of.timetz().replace(tzinfo=None)
        try:
            trading, source = self.calendar.is_trading_day(
                as_of.date(),
                allow_network=not no_network and not dry_run,
                observed_at=as_of,
            )
        except Exception as exc:
            self.logger.warning(
                "stage=AUCTION_TRADING_DAY_GATE status=FAILED category=%s",
                type(exc).__name__,
            )
            return {"status": "SKIPPED", "reason": "CALENDAR_UNAVAILABLE"}
        if not trading:
            return {"status": "SKIPPED", "reason": source, "trading_date": trading_date}
        if current_time < AUCTION_START:
            return {"status": "SKIPPED", "reason": "BEFORE_09_25", "trading_date": trading_date}

        key = self._key(trading_date)
        success = self.store.event_record(key, statuses={"SUCCESS"})
        if success is not None:
            return {
                "status": "SKIPPED",
                "reason": "ALREADY_SUCCESSFUL",
                "trading_date": trading_date,
                "raw_snapshot_path": success.get("raw_snapshot_path"),
            }
        attempted = self.store.event_record(key, statuses={"DATA_NOT_READY"})
        terminal = self.store.event_record(key, statuses={"FAILED"})
        terminal_has_evidence = terminal is not None and (
            attempted is not None or terminal.get("execution_mode") == "CATCH_UP"
        )
        if terminal_has_evidence and not catch_up:
            return {"status": "SKIPPED", "reason": "TERMINAL_FAILURE_RECORDED"}
        if dry_run:
            return {
                "status": "DRY_RUN",
                "reason": (
                    "ELIGIBLE"
                    if current_time <= AUCTION_FINAL_WAIT_WINDOW_END or catch_up
                    else "DEADLINE_EXPIRED"
                ),
                "trading_date": trading_date,
            }
        if no_network:
            return {"status": "SKIPPED", "reason": "NO_NETWORK"}

        execution_mode = "CATCH_UP" if catch_up else "LIVE_SCHEDULED"
        lock = ProcessLock(
            self.root / "data" / "runtime" / "auction.lock",
            stale_seconds=self.lock_stale_seconds,
        )
        run_id = uuid4().hex
        if not lock.acquire(run_id=run_id, now=as_of):
            return {"status": "SKIPPED", "reason": "LOCKED"}
        try:
            success = self.store.event_record(key, statuses={"SUCCESS"})
            if success is not None:
                return {"status": "SKIPPED", "reason": "ALREADY_SUCCESSFUL"}
            if current_time > AUCTION_FINAL_WAIT_WINDOW_END and not catch_up:
                attempted = self.store.event_record(key, statuses={"DATA_NOT_READY"})
                if attempted is None:
                    return {
                        "status": "SKIPPED",
                        "reason": "MISSED_AUTOMATIC_WINDOW",
                        "trading_date": trading_date,
                    }
                record = self._failure_record(
                    as_of=as_of,
                    execution_mode=execution_mode,
                )
                self.logger.error(
                    "stage=AUCTION_FETCH status=FAILED category=DATA_NOT_READY "
                    "recoverable=false retry=WINDOW_EXPIRED"
                )
                record["notification"] = self._notify_failure(
                    record,
                    dry_run=notification_dry_run,
                )
                return record

            symbols = [target.symbol for target in self.targets]
            try:
                raw = self.provider_factory().auction_snapshot(symbols, stage=AUCTION_STAGE)
                parsed = parse_auction_snapshot(raw, expected_symbols=symbols)
            except Exception as exc:
                if catch_up:
                    record = self._failure_record(
                        as_of=as_of,
                        execution_mode=execution_mode,
                    )
                    self.logger.error(
                        "stage=AUCTION_FETCH status=FAILED category=%s "
                        "recoverable=false retry=CATCH_UP_OPERATOR_REQUIRED",
                        type(exc).__name__,
                    )
                    record["notification"] = self._notify_failure(
                        record,
                        dry_run=notification_dry_run,
                    )
                    return record
                self.logger.warning(
                    "stage=AUCTION_FETCH status=NOT_READY category=%s "
                    "recoverable=true retry=NEXT_LAUNCHD_TICK",
                    type(exc).__name__,
                )
                attempt = self._attempt_record(
                    as_of=as_of,
                    error_category=type(exc).__name__,
                )
                return {
                    "status": "DATA_NOT_READY",
                    "retry": "NEXT_LAUNCHD_TICK",
                    "run_id": attempt["run_id"],
                }

            if not parsed["final"]:
                if catch_up:
                    record = self._failure_record(
                        as_of=as_of,
                        execution_mode=execution_mode,
                        parsed=parsed,
                    )
                    self.logger.error(
                        "stage=AUCTION_FETCH status=FAILED category=DATA_NOT_READY "
                        "recoverable=false retry=CATCH_UP_OPERATOR_REQUIRED"
                    )
                    record["notification"] = self._notify_failure(
                        record,
                        dry_run=notification_dry_run,
                    )
                    return record
                self.logger.warning(
                    "stage=AUCTION_FETCH status=NOT_READY category=DATA_NOT_READY "
                    "recoverable=true retry=NEXT_LAUNCHD_TICK auction_phase=%s "
                    "data_status=%s",
                    parsed["auction_phase"],
                    parsed["data_status"],
                )
                attempt = self._attempt_record(as_of=as_of, parsed=parsed)
                return {
                    "status": "DATA_NOT_READY",
                    "retry": "NEXT_LAUNCHD_TICK",
                    "auction_phase": parsed["auction_phase"],
                    "data_status": parsed["data_status"],
                    "run_id": attempt["run_id"],
                }

            fetched_at = as_of.astimezone(timezone.utc)
            raw_payload = {
                "provider": "HITHINK",
                "endpoint": AUCTION_ENDPOINT,
                "symbols": symbols,
                "stage": AUCTION_STAGE,
                "fetched_at": fetched_at.isoformat(),
                **self._time_semantics(as_of),
                "auction_phase": parsed["auction_phase"],
                "data_status": parsed["data_status"],
                "raw_response": raw,
            }
            cache_entry = RawCache(self.root / "data" / "raw").save(
                instrument_id="auction.final_snapshot",
                provider="hithink",
                provider_symbol=",".join(symbols),
                data_type=DataType.AUCTION,
                raw=raw_payload,
                fetched_at=fetched_at,
                source_timestamp=parsed["timestamp"],
            )
            traces = [
                SourceTrace(
                    provider="hithink",
                    provider_symbol=target.symbol,
                    raw_path=cache_entry.path,
                    fetched_at=cache_entry.fetched_at,
                    source_timestamp=parsed["timestamp"],
                )
                for target in self.targets
            ]
            completed_at = as_of.isoformat()
            record = {
                "schema_version": 1,
                "run_id": run_id,
                "event_type": AUCTION_EVENT_TYPE,
                "event_version": AUCTION_VERSION,
                "idempotency_key": key,
                "trading_date": trading_date,
                "scheduled_at": self._scheduled_at(as_of),
                **self._time_semantics(as_of),
                "execution_mode": execution_mode,
                "started_at": as_of.isoformat(),
                "completed_at": completed_at,
                "status": "SUCCESS",
                "provider": "HITHINK",
                "endpoint": AUCTION_ENDPOINT,
                "symbols": symbols,
                "stage": AUCTION_STAGE,
                "data_status": parsed["data_status"],
                "auction_phase": parsed["auction_phase"],
                "raw_snapshot_id": Path(cache_entry.path).stem,
                "raw_snapshot_path": cache_entry.path,
                "source_trace": [asdict(trace) for trace in traces],
            }
            runtime_path = self.store.append_event(record)
            notification_input = {
                **record,
                "items": parsed["items"],
            }
            record["runtime_manifest_path"] = runtime_path
            record["notification"] = self._notify_snapshot(
                notification_input,
                source_result_id=cache_entry.path,
                dry_run=notification_dry_run,
            )
            return record
        finally:
            lock.release()
