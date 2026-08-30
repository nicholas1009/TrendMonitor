"""Official Hithink A-share trading calendar with a local auditable snapshot."""

from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")


class TradingCalendarStore:
    def __init__(self, path: str | Path, *, provider_factory: Callable[[], Any] | None = None):
        self.path = Path(path).resolve()
        self.provider_factory = provider_factory

    def load(self) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != 1 or payload.get("timezone") != "Asia/Shanghai":
            raise ValueError("invalid trading calendar snapshot")
        return payload

    def refresh(self, *, observed_at: datetime) -> dict[str, Any]:
        if self.provider_factory is None:
            raise ValueError("calendar provider is unavailable")
        raw = self.provider_factory().trading_days()
        data = raw.get("data")
        items = data.get("item", []) if isinstance(data, dict) else data if isinstance(data, list) else []
        open_days = sorted(
            {
                datetime.strptime(str(item["date"]), "%Y%m%d").date().isoformat()
                for item in items
                if isinstance(item, dict) and item.get("date")
            }
        )
        if not open_days:
            raise ValueError("official trading calendar is empty")
        payload = {
            "schema_version": 1,
            "provider": "hithink",
            "endpoint": "/api/a-share/calendar/trading-days",
            "timezone": "Asia/Shanghai",
            "fetched_at": observed_at.astimezone(SHANGHAI).isoformat(),
            "authoritative_through": observed_at.astimezone(SHANGHAI).date().isoformat(),
            "first_open_day": open_days[0],
            "last_open_day": open_days[-1],
            "open_days": open_days,
            "request_id": raw.get("request_id"),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return payload

    def is_trading_day(self, value: date, *, allow_network: bool, observed_at: datetime) -> tuple[bool, str]:
        if value.weekday() >= 5:
            return False, "WEEKEND"
        payload = self.load()
        if allow_network and (
            payload is None or payload.get("authoritative_through", "") < value.isoformat()
        ):
            payload = self.refresh(observed_at=observed_at)
        if payload is None or payload.get("authoritative_through", "") < value.isoformat():
            raise ValueError("CALENDAR_COVERAGE_UNAVAILABLE")
        return value.isoformat() in set(payload["open_days"]), "HITHINK_OFFICIAL_CALENDAR"
