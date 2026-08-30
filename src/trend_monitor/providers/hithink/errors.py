from __future__ import annotations

from trend_monitor.errors import ErrorCategory, TrendMonitorError


class HithinkProviderError(TrendMonitorError):
    """Provider details layered on the common data-layer exception."""

    __slots__ = ("http_status", "provider_code", "request_id")

    def __init__(
        self,
        category: ErrorCategory,
        message: str,
        http_status: int | None = None,
        provider_code: int | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(category, message)
        self.http_status = http_status
        self.provider_code = provider_code
        self.request_id = request_id

    def __str__(self) -> str:
        details = [self.category.value, self.message]
        if self.http_status is not None:
            details.append(f"http_status={self.http_status}")
        if self.provider_code is not None:
            details.append(f"provider_code={self.provider_code}")
        if self.request_id:
            details.append(f"request_id={self.request_id}")
        return ": ".join(details)


def category_for_business_code(code: int) -> ErrorCategory:
    if code in {2001, 2003}:
        return ErrorCategory.AUTH_ERROR
    if code == 4001:
        return ErrorCategory.RATE_LIMIT
    if code == 3004:
        return ErrorCategory.UNSUPPORTED
    if code in {3001, 3002}:
        return ErrorCategory.EMPTY_DATA
    if 1000 <= code < 2000:
        return ErrorCategory.INVALID_DATA
    if 5000 <= code < 6000:
        return ErrorCategory.NETWORK_ERROR
    return ErrorCategory.UNKNOWN_ERROR


def category_for_http_status(status: int) -> ErrorCategory:
    if status in {401, 403}:
        return ErrorCategory.AUTH_ERROR
    if status == 429:
        return ErrorCategory.RATE_LIMIT
    if status == 404:
        return ErrorCategory.UNSUPPORTED
    if status >= 500:
        return ErrorCategory.NETWORK_ERROR
    if status >= 400:
        return ErrorCategory.INVALID_DATA
    return ErrorCategory.UNKNOWN_ERROR
