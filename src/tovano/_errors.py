"""Every failed call raises a subclass of :class:`TovanoError`.

Catch the class you care about, or check ``code``, the API's error code
(``"insufficient_credits"``, ``"rate_limited"``, ...).
"""

from __future__ import annotations

import email.utils
import time
from typing import Any, Mapping, Optional

__all__ = [
    "TovanoError",
    "APIError",
    "InvalidRequestError",
    "AuthenticationError",
    "PaymentRequiredError",
    "InsufficientCreditsError",
    "SpendLimitReachedError",
    "PermissionDeniedError",
    "NotFoundError",
    "ConflictError",
    "RateLimitError",
    "InternalServerError",
    "ProviderError",
    "APIConnectionError",
    "APITimeoutError",
    "RunTimeoutError",
    "RunFailedError",
    "WebhookVerificationError",
]


class TovanoError(Exception):
    """Base class of every error the SDK raises."""

    status: int
    """HTTP status. 0 when no response arrived (network error or timeout)."""
    code: str
    """The API's error code, such as ``insufficient_credits``."""
    type: str
    param: Optional[str]
    """The request field the error is about, when there is one."""
    request_id: Optional[str]
    """The ``X-Request-Id`` of the failed call. Quote it to support."""
    headers: Mapping[str, str]

    def __init__(
        self,
        message: str,
        *,
        status: int = 0,
        code: str = "unknown_error",
        type: str = "api_error",
        param: Optional[str] = None,
        request_id: Optional[str] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.type = type
        self.param = param
        self.request_id = request_id
        self.headers = headers or {}

    def __repr__(self) -> str:
        return f"{type(self).__name__}(status={self.status}, code={self.code!r}, message={self.message!r})"


class APIError(TovanoError):
    """The API answered with an error."""


class InvalidRequestError(APIError):
    """400 or 422: the request is invalid. ``param`` names the field."""


class AuthenticationError(APIError):
    """401: missing, invalid or revoked API key."""


class PaymentRequiredError(APIError):
    """402: the call needs credits. See the two subclasses."""


class InsufficientCreditsError(PaymentRequiredError):
    """402 ``insufficient_credits``: add credits in the dashboard."""


class SpendLimitReachedError(PaymentRequiredError):
    """402 ``spend_limit_reached``: the monthly spend limit is reached."""


class PermissionDeniedError(APIError):
    """403."""


class NotFoundError(APIError):
    """404: unknown model or run."""


class ConflictError(APIError):
    """409: an Idempotency-Key was reused with a different request."""


class RateLimitError(APIError):
    """429: too many requests. Retried automatically; raised when retries run out."""

    @property
    def retry_after(self) -> Optional[float]:
        """Seconds the server asked to wait, from ``Retry-After``."""
        return parse_retry_after(self.headers)


class InternalServerError(APIError):
    """500 and other 5xx answers."""


class ProviderError(APIError):
    """502 ``provider_error``: the model provider failed. You were not charged."""


class APIConnectionError(TovanoError):
    """No response: DNS, connection refused, connection reset."""


class APITimeoutError(APIConnectionError):
    """No response within ``timeout``."""


class RunTimeoutError(TovanoError):
    """``runs.wait()`` gave up before the run finished. The run keeps running."""

    def __init__(self, run_id: str, timeout: float) -> None:
        super().__init__(
            f"Run {run_id} did not finish within {timeout:g} s. It is still running.",
            code="run_timeout",
            type="client_error",
        )
        self.run_id = run_id


class RunFailedError(TovanoError):
    """``runs.run(..., raise_on_failure=True)``: the run failed. It was not charged."""

    def __init__(self, run: Any) -> None:
        err = run.get("error") or {}
        super().__init__(
            err.get("message") or f"Run {run['id']} failed.",
            code=err.get("code") or "run_failed",
            type="run_error",
        )
        self.run = run


class WebhookVerificationError(TovanoError):
    """A webhook's signature is missing, malformed, wrong or too old."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="invalid_signature", type="webhook_error")


def parse_retry_after(headers: Mapping[str, str]) -> Optional[float]:
    """``retry-after-ms``, else ``Retry-After`` as seconds or an HTTP date."""
    ms = headers.get("retry-after-ms")
    if ms:
        try:
            return max(0.0, float(ms) / 1000)
        except ValueError:
            pass
    value = headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_tz(value)
        if parsed is None:
            return None
        return max(0.0, email.utils.mktime_tz(parsed) - time.time())
    except (TypeError, ValueError, OverflowError):
        return None


_DEFAULT_CODES = {
    400: "invalid_request",
    401: "invalid_api_key",
    402: "insufficient_credits",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "invalid_request",
    429: "rate_limited",
    502: "provider_error",
}


def error_from_response(status: int, body: Optional[Mapping[str, Any]], headers: Mapping[str, str]) -> APIError:
    """Builds the right error class from an HTTP error response."""
    body = body or {}
    code = str(
        body.get("code") or _DEFAULT_CODES.get(status) or ("internal_error" if status >= 500 else "unknown_error")
    )
    kwargs: dict[str, Any] = dict(
        status=status,
        code=code,
        type=str(body.get("type") or "api_error"),
        param=body.get("param"),
        request_id=headers.get("x-request-id"),
        headers=dict(headers),
    )
    message = str(body.get("message") or f"HTTP {status}")
    cls: type[APIError]
    if status in (400, 422):
        cls = InvalidRequestError
    elif status == 401:
        cls = AuthenticationError
    elif status == 402:
        cls = {
            "spend_limit_reached": SpendLimitReachedError,
            "insufficient_credits": InsufficientCreditsError,
        }.get(code, PaymentRequiredError)
    elif status == 403:
        cls = PermissionDeniedError
    elif status == 404:
        cls = NotFoundError
    elif status == 409:
        cls = ConflictError
    elif status == 429:
        cls = RateLimitError
    elif status == 502 or code == "provider_error":
        cls = ProviderError
    elif status >= 500:
        cls = InternalServerError
    else:
        cls = APIError
    return cls(message, **kwargs)
