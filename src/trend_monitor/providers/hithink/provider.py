from __future__ import annotations

import json
from json import JSONDecodeError
from typing import Any, Protocol
from urllib.parse import urlencode

from .config import load_api_key
from .errors import (
    ErrorCategory,
    HithinkProviderError,
    category_for_business_code,
    category_for_http_status,
)
from .transport import HttpResponse, UrllibTransport

RawResponse = dict[str, Any]


class Transport(Protocol):
    def get(self, url: str, headers: dict[str, str], timeout: float) -> HttpResponse: ...


class HithinkProvider:
    """Thin REST provider: request, raw response, and deterministic error mapping."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = "https://fuyao.aicubes.cn",
        timeout: float = 20.0,
        transport: Transport | None = None,
        dotenv_path: str = ".env",
    ) -> None:
        self._api_key = api_key or load_api_key(dotenv_path)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport or UrllibTransport()

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def get(self, path: str, **params: object) -> RawResponse:
        if not self._api_key:
            raise HithinkProviderError(
                ErrorCategory.AUTH_ERROR,
                "BLOCKED_BY_API_KEY: HITHINK_FINANCE_API_KEY is not configured",
            )
        query = urlencode(
            {key: value for key, value in params.items() if value is not None},
            safe=",",
        )
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"
        response = self.transport.get(
            url,
            {"X-api-key": self._api_key, "Accept": "application/json"},
            self.timeout,
        )
        return self._decode_response(response)

    def _decode_response(self, response: HttpResponse) -> RawResponse:
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, JSONDecodeError) as exc:
            category = category_for_http_status(response.status)
            if response.status < 400:
                category = ErrorCategory.INVALID_DATA
            raise HithinkProviderError(
                category,
                "provider returned a non-JSON response",
                http_status=response.status,
            ) from exc

        if not isinstance(payload, dict):
            raise HithinkProviderError(
                ErrorCategory.INVALID_DATA,
                "provider response must be a JSON object",
                http_status=response.status,
            )

        raw_code = payload.get("code")
        request_id = payload.get("request_id")
        if response.status >= 400:
            category = category_for_http_status(response.status)
            provider_code = raw_code if isinstance(raw_code, int) else None
            if provider_code is not None:
                category = category_for_business_code(provider_code)
            raise HithinkProviderError(
                category,
                str(payload.get("message") or "HTTP request rejected"),
                http_status=response.status,
                provider_code=provider_code,
                request_id=str(request_id) if request_id else None,
            )

        if not isinstance(raw_code, int):
            raise HithinkProviderError(
                ErrorCategory.INVALID_DATA,
                "response is missing integer field 'code'",
                http_status=response.status,
            )
        if raw_code != 0:
            raise HithinkProviderError(
                category_for_business_code(raw_code),
                str(payload.get("message") or "provider business error"),
                http_status=response.status,
                provider_code=raw_code,
                request_id=str(request_id) if request_id else None,
            )
        if "data" not in payload:
            raise HithinkProviderError(
                ErrorCategory.DATA_INCOMPLETE,
                "successful response is missing field 'data'",
                http_status=response.status,
                provider_code=raw_code,
                request_id=str(request_id) if request_id else None,
            )
        return payload

    def search_symbols(
        self, query: str, *, asset_type: str | None = None, limit: int = 10
    ) -> RawResponse:
        return self.get(
            "/api/meta/tickers/search", q=query, asset_type=asset_type, limit=limit
        )

    def stock_snapshot(self, thscodes: list[str] | tuple[str, ...]) -> RawResponse:
        return self.get(
            "/api/a-share/prices/snapshot", thscodes=",".join(thscodes)
        )

    def stock_history(
        self,
        thscode: str,
        *,
        start: int,
        end: int,
        interval: str = "1d",
        adjust: str = "none",
    ) -> RawResponse:
        return self.get(
            "/api/a-share/prices/historical",
            thscode=thscode,
            interval=interval,
            start=start,
            end=end,
            adjust=adjust,
        )

    def index_catalog(self, tag: str) -> RawResponse:
        return self.get("/api/a-share-index/catalog/ths-index-list", tag=tag)

    def index_snapshot(self, thscodes: list[str] | tuple[str, ...]) -> RawResponse:
        return self.get(
            "/api/a-share-index/prices/snapshot", thscodes=",".join(thscodes)
        )

    def index_history(
        self, thscode: str, *, start: int, end: int, interval: str = "1d"
    ) -> RawResponse:
        return self.get(
            "/api/a-share-index/prices/historical",
            thscode=thscode,
            interval=interval,
            start=start,
            end=end,
        )

    def index_constituents(self, thscode: str) -> RawResponse:
        return self.get(
            "/api/a-share-index/constituents/ths-stock-list", thscode=thscode
        )

    def fund_profile(self, thscode: str, *, fund_type: str = "exchange") -> RawResponse:
        return self.get(
            "/api/fund/profile/detail", fund_type=fund_type, thscode=thscode
        )

    def fund_snapshot(self, thscode: str) -> RawResponse:
        return self.get("/api/fund/market/snapshot", thscode=thscode)

    def fund_history(
        self, thscode: str, *, start: int, end: int, interval: str = "1d"
    ) -> RawResponse:
        return self.get(
            "/api/fund/market/historical",
            thscode=thscode,
            interval=interval,
            start=start,
            end=end,
        )

    def auction_snapshot(self, thscodes: list[str], *, stage: str = "final") -> RawResponse:
        return self.get(
            "/api/a-share/auction/snapshot",
            thscodes=",".join(thscodes),
            stage=stage,
        )

    def trading_days(self) -> RawResponse:
        return self.get("/api/a-share/calendar/trading-days")

    def special_data(self, capability: str, **params: object) -> RawResponse:
        allowed = {
            "limit-up-pool",
            "limit-down-pool",
            "limit-break-pool",
            "limit-up-ladder",
            "anomaly-analysis-list",
            "anomaly-analysis-stock",
            "skyrocket-list",
            "hot-stock-list",
            "dragon-tiger-list",
        }
        if capability not in allowed:
            raise HithinkProviderError(
                ErrorCategory.UNSUPPORTED, f"unsupported special-data capability: {capability}"
            )
        return self.get(f"/api/a-share/special-data/{capability}", **params)
