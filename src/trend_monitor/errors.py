"""Shared data-layer error categories and exceptions."""

from __future__ import annotations

from enum import StrEnum


class ErrorCategory(StrEnum):
    AUTH_ERROR = "AUTH_ERROR"
    PERMISSION_ERROR = "PERMISSION_ERROR"
    RATE_LIMIT = "RATE_LIMIT"
    NETWORK_ERROR = "NETWORK_ERROR"
    UNSUPPORTED = "UNSUPPORTED"
    EMPTY_DATA = "EMPTY_DATA"
    INVALID_DATA = "INVALID_DATA"
    DATA_INCOMPLETE = "DATA_INCOMPLETE"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"
    UNMAPPED = "UNMAPPED"
    DATA_CONFLICT = "DATA_CONFLICT"
    CACHE_INVALID = "CACHE_INVALID"


class TrendMonitorError(RuntimeError):
    """Base exception for deterministic data-layer failures."""

    def __init__(
        self,
        category: ErrorCategory,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        return f"{self.category.value}: {self.message}"
