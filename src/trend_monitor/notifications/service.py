"""Notification policy execution, deduplication, delivery, and isolation."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Iterable
from uuid import uuid4
from zoneinfo import ZoneInfo

from trend_monitor.schemas.notification import (
    NotificationEvent,
    NotificationRecord,
    NotificationSeverity,
    NotificationStatus,
)

from .bark import BarkAdapter
from .config import BarkConfig, NotificationPolicyConfig
from .policy import NotificationPolicy
from .store import NotificationStore


SHANGHAI = ZoneInfo("Asia/Shanghai")


class NotificationService:
    def __init__(
        self,
        *,
        bark_config: BarkConfig,
        policy_config: NotificationPolicyConfig,
        policy: NotificationPolicy,
        adapter: BarkAdapter,
        store: NotificationStore,
        now: Callable[[], datetime] | None = None,
    ):
        self.bark_config = bark_config
        self.policy_config = policy_config
        self.policy = policy
        self.adapter = adapter
        self.store = store
        self.now = now or (lambda: datetime.now(SHANGHAI))

    def _record(
        self,
        event: NotificationEvent,
        *,
        status: NotificationStatus,
        attempts: int,
        error_category: str | None = None,
    ) -> dict[str, Any]:
        created = self.now()
        record = NotificationRecord(
            notification_id=uuid4().hex,
            event_key=event.event_key,
            event_type=event.event_type,
            instrument_id=event.instrument_id,
            trading_date=event.trading_date,
            period_end=event.period_end,
            rules_version=event.rules_version,
            severity=event.severity,
            status=status,
            attempts=attempts,
            created_at=created.isoformat(),
            sent_at=created.isoformat() if status is NotificationStatus.SENT else None,
            execution_mode=event.execution_mode,
            source_result_id=event.source_result_id,
            error_category=error_category,
        )
        self.store.append(record)
        return record.to_dict()

    def process_events(
        self,
        events: Iterable[NotificationEvent],
        *,
        dry_run: bool = False,
        explicit_test_send: bool = False,
    ) -> dict[str, Any]:
        records: list[dict[str, Any]] = []
        for event in events:
            if self.store.sent(event.event_key):
                records.append(
                    self._record(event, status=NotificationStatus.SKIPPED_DUPLICATE, attempts=0)
                )
                continue

            config_error = self.bark_config.validation_error()
            if config_error:
                records.append(
                    self._record(
                        event,
                        status=NotificationStatus.FAILED,
                        attempts=0,
                        error_category=config_error,
                    )
                )
                continue

            is_error = event.severity is NotificationSeverity.ERROR
            if event.execution_mode == "CATCH_UP":
                allowed = (
                    self.policy_config.catch_up_error_notifications
                    if is_error
                    else self.policy_config.catch_up_risk_notifications
                )
                if not allowed:
                    records.append(
                        self._record(event, status=NotificationStatus.SKIPPED_POLICY, attempts=0)
                    )
                    continue

            if not self.bark_config.enabled and not explicit_test_send:
                records.append(
                    self._record(event, status=NotificationStatus.SKIPPED_DISABLED, attempts=0)
                )
                continue
            if dry_run:
                records.append(
                    self._record(event, status=NotificationStatus.WOULD_SEND, attempts=0)
                )
                continue

            result = self.adapter.send(title=event.title, body=event.body, group=event.group)
            records.append(
                self._record(
                    event,
                    status=result.status,
                    attempts=result.attempts,
                    error_category=result.error_category,
                )
            )

        statuses = [item["status"] for item in records]
        if not statuses:
            aggregate = NotificationStatus.SKIPPED_POLICY.value
        elif NotificationStatus.FAILED.value in statuses:
            aggregate = NotificationStatus.FAILED.value
        elif NotificationStatus.SENT.value in statuses:
            aggregate = NotificationStatus.SENT.value
        elif NotificationStatus.WOULD_SEND.value in statuses:
            aggregate = NotificationStatus.WOULD_SEND.value
        elif all(value == NotificationStatus.SKIPPED_DUPLICATE.value for value in statuses):
            aggregate = NotificationStatus.SKIPPED_DUPLICATE.value
        elif all(value == NotificationStatus.SKIPPED_DISABLED.value for value in statuses):
            aggregate = NotificationStatus.SKIPPED_DISABLED.value
        else:
            aggregate = NotificationStatus.SKIPPED_POLICY.value
        return {"status": aggregate, "event_count": len(records), "records": records}

    def process_combined(
        self,
        current: dict[str, Any],
        previous: dict[str, Any] | None,
        combined: dict[str, Any],
        *,
        source_result_id: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        events = self.policy.evaluate_combined(
            current,
            previous,
            combined,
            source_result_id=source_result_id,
        )
        return self.process_events(events, dry_run=dry_run)

    def process_runtime_failure(
        self, record: dict[str, Any], *, dry_run: bool = False
    ) -> dict[str, Any]:
        return self.process_events(
            self.policy.evaluate_runtime_failure(record),
            dry_run=dry_run,
        )

    def process_auction_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        source_result_id: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return self.process_events(
            self.policy.evaluate_auction_snapshot(
                snapshot,
                source_result_id=source_result_id,
            ),
            dry_run=dry_run,
        )

    def process_auction_failure(
        self,
        record: dict[str, Any],
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return self.process_events(
            self.policy.evaluate_auction_failure(record),
            dry_run=dry_run,
        )

    def process_test(self, *, send: bool) -> dict[str, Any]:
        event = self.policy.test_event(created_at=self.now().isoformat())
        return self.process_events(
            (event,),
            dry_run=not send,
            explicit_test_send=send,
        )
