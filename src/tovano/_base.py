"""Shared configuration, headers and retry policy for the sync and async clients."""

from __future__ import annotations

import json
import os
import platform
import random
import uuid
from typing import Any, Dict, Mapping, Optional

import httpx

from ._errors import APIError, error_from_response, parse_retry_after
from ._version import __version__

DEFAULT_BASE_URL = "https://api.tovano.ai"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_RETRIES = 2
MAX_RETRY_AFTER = 60.0
INITIAL_BACKOFF = 0.5
MAX_BACKOFF = 8.0

NOT_GIVEN: Any = object()


class ClientConfig:
    def __init__(
        self,
        *,
        api_key: Optional[str],
        base_url: Optional[str],
        timeout: float,
        max_retries: int,
        customer: Optional[str],
        webhook_secret: Optional[str],
        default_headers: Optional[Mapping[str, str]],
        app_info: Optional[str],
    ) -> None:
        key = api_key or os.environ.get("TOVANO_API_KEY")
        if not key:
            raise ValueError("Missing API key: pass api_key=... or set TOVANO_API_KEY.")
        self.api_key = key
        url = (base_url or os.environ.get("TOVANO_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        if url.endswith("/v1"):
            url = url[:-3]
        self.base_url = url
        self.timeout = timeout
        self.max_retries = max_retries
        self.customer = customer
        self.webhook_secret = webhook_secret or os.environ.get("TOVANO_WEBHOOK_SECRET")
        self.default_headers = dict(default_headers or {})
        self.app_info = app_info
        self.user_agent = f"tovano-python/{__version__} python/{platform.python_version()}" + (
            f" {app_info}" if app_info else ""
        )

    def headers(
        self,
        method: str,
        *,
        stream: bool,
        has_body: bool,
        idempotency_key: Optional[str],
        customer: Optional[str],
        extra_headers: Optional[Mapping[str, str]],
    ) -> Dict[str, str]:
        h = {
            "accept": "text/event-stream" if stream else "application/json",
            "user-agent": self.user_agent,
            "x-tovano-sdk": f"python/{__version__}",
            **self.default_headers,
            "authorization": f"Bearer {self.api_key}",
        }
        if has_body:
            h["content-type"] = "application/json"
        if method == "POST":
            # A retry with the same key never runs a task twice.
            h["idempotency-key"] = idempotency_key or uuid.uuid4().hex
        tag = customer or self.customer
        if tag:
            h["x-tovano-customer"] = tag
        h.update(extra_headers or {})
        return h


def clean_params(params: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in (params or {}).items():
        if v is None or v is NOT_GIVEN:
            continue
        out[k] = ("true" if v else "false") if isinstance(v, bool) else v
    return out


def clean_body(body: Mapping[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in body.items() if v is not None and v is not NOT_GIVEN}


def should_retry(status: int) -> bool:
    return status == 408 or status == 429 or status >= 500


def backoff(attempt: int) -> float:
    base: float = min(INITIAL_BACKOFF * 2.0**attempt, MAX_BACKOFF)
    return base * (0.75 + random.random() * 0.25)


def retry_delay(response: httpx.Response, attempt: int) -> Optional[float]:
    """Seconds to wait before retrying this response, or None to give up."""
    wait = parse_retry_after(response.headers)
    if wait is None:
        return backoff(attempt)
    if wait > MAX_RETRY_AFTER:
        return None
    return wait


def api_error(response: httpx.Response, text: str) -> APIError:
    body: Optional[Mapping[str, Any]] = None
    try:
        parsed = json.loads(text) if text else None
        if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
            body = parsed["error"]
    except ValueError:
        pass
    if body is None:
        body = {"message": text[:500] or None}
    return error_from_response(response.status_code, body, response.headers)


def model_path(model_id: str) -> str:
    from urllib.parse import quote

    return "/v1/models/" + "/".join(quote(p, safe="") for p in model_id.split("/"))
