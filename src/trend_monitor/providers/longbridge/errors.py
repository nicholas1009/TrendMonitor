"""Longbridge SDK error conversion into the shared error taxonomy."""

from __future__ import annotations

from trend_monitor.errors import ErrorCategory, TrendMonitorError


class LongbridgeProviderError(TrendMonitorError):
    __slots__ = ("provider_code",)

    def __init__(
        self,
        category: ErrorCategory,
        message: str,
        *,
        provider_code: int | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(category, message, details=details)
        self.provider_code = provider_code


def category_for_longbridge_error(code: int, message: str = "") -> ErrorCategory:
    normalized = message.lower()
    if code in {401003, 403201, 403203, 403205}:
        return ErrorCategory.AUTH_ERROR
    if code in {429001, 429002, 301606}:
        return ErrorCategory.RATE_LIMIT
    if code == 301604:
        return ErrorCategory.PERMISSION_ERROR
    if code == 301607:
        if any(word in normalized for word in ("permission", "quota", "access", "upper limit")):
            return ErrorCategory.PERMISSION_ERROR
        return ErrorCategory.INVALID_DATA
    if code == 301603:
        return ErrorCategory.EMPTY_DATA
    if code == 301600:
        return ErrorCategory.INVALID_DATA
    if code in {301602, 500000}:
        return ErrorCategory.NETWORK_ERROR
    return ErrorCategory.UNKNOWN_ERROR


def convert_sdk_exception(
    exc: Exception,
    *,
    secrets: tuple[str, ...] = (),
) -> LongbridgeProviderError:
    raw_code = getattr(exc, "code", None)
    code = int(raw_code) if isinstance(raw_code, int) else None
    message = str(getattr(exc, "message", "") or str(exc) or type(exc).__name__)
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[REDACTED]")
    category = (
        category_for_longbridge_error(code, message)
        if code is not None
        else ErrorCategory.NETWORK_ERROR
        if isinstance(exc, (OSError, TimeoutError, ConnectionError))
        or any(
            marker in message.lower()
            for marker in ("error sending request", "client error (connect)", "connection reset", "timed out")
        )
        else ErrorCategory.UNKNOWN_ERROR
    )
    return LongbridgeProviderError(
        category,
        message,
        provider_code=code,
        details={
            "exception_class": type(exc).__name__,
            "provider_code": code,
        },
    )
