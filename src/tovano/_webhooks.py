"""Webhook signatures.

Tovano POSTs the task object to your ``webhook_url`` with the header::

    Tovano-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<raw body>">

signed with your project's webhook secret (App → Settings → Webhooks).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Optional, Union

from ._errors import WebhookVerificationError
from ._types import Task

__all__ = ["SIGNATURE_HEADER", "verify", "assert_valid", "construct_event", "sign"]

SIGNATURE_HEADER = "Tovano-Signature"

Body = Union[str, bytes, bytearray, memoryview]


def _bytes(body: Body) -> bytes:
    return body.encode("utf-8") if isinstance(body, str) else bytes(body)


def _hmac(secret: str, timestamp: int, body: Body) -> str:
    return hmac.new(secret.encode("utf-8"), f"{timestamp}.".encode() + _bytes(body), hashlib.sha256).hexdigest()


def assert_valid(
    body: Body,
    signature: Optional[str],
    secret: str,
    *,
    tolerance: int = 300,
    now: Optional[int] = None,
) -> None:
    """Raises :class:`WebhookVerificationError` unless the signature is valid.

    Pass the raw request body exactly as received, not re-serialised JSON.
    ``tolerance`` is the oldest accepted signature in seconds; 0 skips the age check.
    """
    if not secret:
        raise WebhookVerificationError("No webhook secret given.")
    if not signature:
        raise WebhookVerificationError("Missing Tovano-Signature header.")
    timestamp: Optional[int] = None
    candidates = []
    for part in signature.split(","):
        key, sep, value = part.strip().partition("=")
        if not sep:
            continue
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError:
                timestamp = None
        elif key == "v1" and value:
            candidates.append(value.strip().lower())
    if timestamp is None or not candidates:
        raise WebhookVerificationError("Malformed Tovano-Signature header.")
    current = int(time.time()) if now is None else now
    if tolerance > 0 and abs(current - timestamp) > tolerance:
        raise WebhookVerificationError("Webhook signature is too old (or the clock is off).")
    expected = _hmac(secret, timestamp, body)
    if not any(hmac.compare_digest(expected, c) for c in candidates):
        raise WebhookVerificationError("Webhook signature does not match. Check the secret and use the raw body.")


def verify(
    body: Body, signature: Optional[str], secret: str, *, tolerance: int = 300, now: Optional[int] = None
) -> bool:
    """True when the signature is valid. Never raises for a bad signature."""
    try:
        assert_valid(body, signature, secret, tolerance=tolerance, now=now)
        return True
    except WebhookVerificationError:
        return False


def construct_event(
    body: Body, signature: Optional[str], secret: str, *, tolerance: int = 300, now: Optional[int] = None
) -> Task:
    """Verifies the signature and returns the task. Raises :class:`WebhookVerificationError`."""
    assert_valid(body, signature, secret, tolerance=tolerance, now=now)
    try:
        return Task(json.loads(_bytes(body)))
    except (ValueError, TypeError) as e:
        raise WebhookVerificationError("Webhook body is not valid JSON.") from e


def sign(body: Body, secret: str, timestamp: Optional[int] = None) -> str:
    """Builds a ``Tovano-Signature`` header. Use it to test your webhook handler."""
    t = int(time.time()) if timestamp is None else timestamp
    return f"t={t},v1={_hmac(secret, t, body)}"
