from __future__ import annotations

from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .errors import ErrorCategory, HithinkProviderError


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: bytes


class UrllibTransport:
    def get(self, url: str, headers: dict[str, str], timeout: float) -> HttpResponse:
        request = Request(url, method="GET", headers=headers)
        try:
            with urlopen(request, timeout=timeout) as response:
                return HttpResponse(status=response.status, body=response.read())
        except HTTPError as exc:
            return HttpResponse(status=exc.code, body=exc.read())
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", None) or str(exc)
            raise HithinkProviderError(
                ErrorCategory.NETWORK_ERROR, f"request failed: {reason}"
            ) from exc
