"""Unit tests with httpx.MockTransport: headers, errors, retries, streaming, webhooks, money."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from decimal import Decimal
from typing import Any, Callable, Dict, List, Union

import httpx
import pytest

import tovano
from tovano import (
    APIConnectionError,
    APITimeoutError,
    AsyncTovano,
    AuthenticationError,
    ConflictError,
    InsufficientCreditsError,
    InternalServerError,
    InvalidRequestError,
    NotFoundError,
    PaymentRequiredError,
    PermissionDeniedError,
    ProviderError,
    RateLimitError,
    RunFailedError,
    RunTimeoutError,
    SpendLimitReachedError,
    Tovano,
    WebhookVerificationError,
    format_usd,
    micro_to_usd,
    usd_to_micro,
    webhooks,
)

Reply = Union[httpx.Response, Exception, Callable[[httpx.Request], httpx.Response]]


class Fake:
    def __init__(self, *replies: Reply) -> None:
        self.replies: List[Reply] = list(replies)
        self.requests: List[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        if callable(r) and not isinstance(r, httpx.Response):
            return r(request)
        return r

    def client(self, **kw: Any) -> Tovano:
        return Tovano(
            api_key="tvn_live_test",
            base_url="https://api.example.test",
            http_client=httpx.Client(transport=httpx.MockTransport(self)),
            **kw,
        )

    def aclient(self, **kw: Any) -> AsyncTovano:
        async def handler(request: httpx.Request) -> httpx.Response:
            return self(request)

        return AsyncTovano(
            api_key="tvn_live_test",
            base_url="https://api.example.test",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            **kw,
        )


def ok(body: Any, status: int = 200, headers: Dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, json=body, headers=headers or {})


def err(status: int, code: str, headers: Dict[str, str] | None = None, **extra: Any) -> httpx.Response:
    return httpx.Response(
        status,
        json={"error": {"type": "x", "code": code, "message": f"{code} happened", **extra}},
        headers={"x-request-id": "req_123", **(headers or {})},
    )


# ------------------------------------------------------------------ setup and headers


def test_needs_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TOVANO_API_KEY", raising=False)
    with pytest.raises(ValueError, match="Missing API key"):
        Tovano()


def test_default_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TOVANO_BASE_URL", raising=False)
    assert Tovano(api_key="k").base_url == "https://api.tovano.ai"
    assert Tovano(api_key="k", base_url="https://api.tovano.ai/v1/").base_url == "https://api.tovano.ai"
    monkeypatch.setenv("TOVANO_BASE_URL", "http://localhost:3000")
    assert Tovano(api_key="k").base_url == "http://localhost:3000"


def test_headers_customer_and_idempotency() -> None:
    f = Fake(ok({"id": "run_1", "status": "queued"}), ok({"id": "run_1"}))
    c = f.client(customer="default-cust", app_info="myapp/2.0")
    c.runs.create(model="image/flux-1.1-pro", input={"prompt": "x"}, customer="acme")
    c.runs.get("run_1")
    post, get = f.requests
    assert str(post.url) == "https://api.example.test/v1/runs"
    assert post.headers["authorization"] == "Bearer tvn_live_test"
    assert post.headers["user-agent"].startswith("tovano-python/")
    assert post.headers["user-agent"].endswith(" myapp/2.0")
    assert len(post.headers["idempotency-key"]) == 32
    assert json.loads(post.content) == {"model": "image/flux-1.1-pro", "input": {"prompt": "x"}, "customer": "acme"}
    assert get.headers["x-tovano-customer"] == "default-cust"
    assert "idempotency-key" not in get.headers


def test_paths_and_queries() -> None:
    f = Fake(
        ok({"id": "image/flux-1.1-pro"}), ok({"object": "list", "data": [], "next_cursor": None}), ok({"data": []})
    )
    c = f.client()
    c.models.get("image/flux-1.1-pro")
    c.runs.list(status="completed", customer="a b", limit=5)
    c.usage(group_by="customer", from_="2026-11-01")
    assert str(f.requests[0].url) == "https://api.example.test/v1/models/image/flux-1.1-pro"
    assert str(f.requests[1].url) == "https://api.example.test/v1/runs?status=completed&customer=a+b&limit=5"
    assert str(f.requests[2].url) == "https://api.example.test/v1/usage?group_by=customer&from=2026-11-01"


def test_per_call_options() -> None:
    f = Fake(ok({}))
    f.client().balance(customer="acme", extra_headers={"x-trace": "1"}, idempotency_key="ignored-for-get")
    r = f.requests[0]
    assert r.headers["x-tovano-customer"] == "acme"
    assert r.headers["x-trace"] == "1"


def test_with_options() -> None:
    f = Fake(ok({}))
    f.client().with_options(customer="other").balance()
    assert f.requests[0].headers["x-tovano-customer"] == "other"
    assert f.requests[0].headers["authorization"] == "Bearer tvn_live_test"


def test_objects_are_dicts_with_attributes() -> None:
    f = Fake(
        ok(
            {
                "id": "t",
                "status": "completed",
                "error": None,
                "output": {"images": [{"url": "u"}]},
                "steps": [{"id": "s"}],
            }
        )
    )
    t = f.client().runs.get("t")
    assert t.id == t["id"] == "t"
    assert t.output.images[0].url == "u"
    assert t.steps[0].id == "s"
    assert isinstance(t.steps[0], tovano.Run)
    assert t.customer is None  # declared but missing → None
    with pytest.raises(AttributeError):
        _ = t.no_such_field
    assert json.loads(json.dumps(t))["output"]["images"][0]["url"] == "u"
    assert t.to_dict()["steps"] == [{"id": "s"}]


# ------------------------------------------------------------------ errors


@pytest.mark.parametrize(
    "status,code,cls",
    [
        (401, "invalid_api_key", AuthenticationError),
        (402, "insufficient_credits", InsufficientCreditsError),
        (402, "spend_limit_reached", SpendLimitReachedError),
        (403, "forbidden", PermissionDeniedError),
        (404, "not_found", NotFoundError),
        (409, "conflict", ConflictError),
        (422, "invalid_request", InvalidRequestError),
    ],
)
def test_error_classes(status: int, code: str, cls: type) -> None:
    f = Fake(err(status, code, param="input.prompt"))
    with pytest.raises(cls) as e:
        f.client().balance()
    assert e.value.status == status
    assert e.value.code == code
    assert e.value.param == "input.prompt"
    assert e.value.request_id == "req_123"
    assert str(e.value) == f"{code} happened"
    assert len(f.requests) == 1  # 4xx is never retried
    if status == 402:
        assert isinstance(e.value, PaymentRequiredError)


def test_non_json_error_body() -> None:
    f = Fake(httpx.Response(502, text="Bad gateway from proxy"))
    with pytest.raises(ProviderError, match="Bad gateway from proxy"):
        f.client(max_retries=0).balance()


def test_internal_server_error() -> None:
    f = Fake(err(500, "internal_error"))
    with pytest.raises(InternalServerError):
        f.client(max_retries=0).balance()


# ------------------------------------------------------------------ retries


def test_retries_and_keeps_idempotency_key() -> None:
    f = Fake(
        err(429, "rate_limited", {"retry-after": "0"}),
        err(500, "internal_error", {"retry-after": "0"}),
        ok({"id": "run_1", "status": "queued"}),
    )
    t = f.client().runs.create(model="m", input={})
    assert t.id == "run_1"
    assert len(f.requests) == 3
    assert len({r.headers["idempotency-key"] for r in f.requests}) == 1


def test_gives_up_after_max_retries() -> None:
    f = Fake(*[err(429, "rate_limited", {"retry-after": "0"}) for _ in range(3)])
    with pytest.raises(RateLimitError):
        f.client(max_retries=1).balance()
    assert len(f.requests) == 2


def test_retry_after_on_error() -> None:
    f = Fake(err(429, "rate_limited", {"retry-after": "7"}))
    with pytest.raises(RateLimitError) as e:
        f.client(max_retries=0).balance()
    assert e.value.retry_after == 7


def test_long_retry_after_is_not_waited_for() -> None:
    f = Fake(err(429, "rate_limited", {"retry-after": "3600"}))
    started = time.monotonic()
    with pytest.raises(RateLimitError):
        f.client().balance()
    assert time.monotonic() - started < 1
    assert len(f.requests) == 1


def test_connection_error_is_retried_then_raised() -> None:
    f = Fake(httpx.ConnectError("refused"), ok({"balance_micro": 5}))
    assert f.client().balance().balance_micro == 5
    f2 = Fake(httpx.ConnectError("refused"))
    with pytest.raises(APIConnectionError):
        f2.client(max_retries=0).balance()


def test_timeout() -> None:
    f = Fake(httpx.ReadTimeout("slow"))
    with pytest.raises(APITimeoutError):
        f.client(max_retries=0).balance()


# ------------------------------------------------------------------ runs


def test_wait_polls_until_done() -> None:
    f = Fake(
        ok({"id": "t", "status": "queued"}),
        ok({"id": "t", "status": "running"}),
        ok({"id": "t", "status": "completed"}),
    )
    assert f.client().runs.wait("t", interval=0.001).status == "completed"
    assert len(f.requests) == 3


def test_wait_times_out() -> None:
    f = Fake(*[ok({"id": "t", "status": "running"}) for _ in range(50)])
    with pytest.raises(RunTimeoutError) as e:
        f.client().runs.wait("t", interval=0.005, timeout=0.03)
    assert e.value.run_id == "t"


def test_run_raise_on_failure() -> None:
    failed = {"id": "t", "status": "failed", "error": {"code": "provider_error", "message": "boom"}}
    f = Fake(ok({"id": "t", "status": "queued"}), ok(failed))
    with pytest.raises(RunFailedError, match="boom") as e:
        f.client().runs.run(model="m", input={}, raise_on_failure=True, interval=0.001)
    assert e.value.run.id == "t"


def test_iterate_pages() -> None:
    f = Fake(
        ok({"object": "list", "data": [{"id": "a"}, {"id": "b"}], "next_cursor": "c1", "has_more": True}),
        ok({"object": "list", "data": [{"id": "c"}], "next_cursor": None, "has_more": False}),
    )
    assert [t.id for t in f.client().runs.iterate(limit=2)] == ["a", "b", "c"]
    assert "cursor=c1" in str(f.requests[1].url)


# ------------------------------------------------------------------ streaming


def sse(*events: str) -> httpx.Response:
    body = "".join(f"data: {e}\n\n" for e in events).encode()
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream", "x-tovano-run-id": "run_s"},
        stream=httpx.ByteStream(body),
    )


def chunk(content: Union[str, None], finish: Union[str, None] = None) -> str:
    delta = {} if content is None else {"content": content}
    return json.dumps(
        {
            "id": "c",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
    )


USAGE = json.dumps(
    {
        "choices": [],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        "tovano": {"run_id": "run_s", "charged_micro": 11},
    }
)


def test_stream_iterates_and_collects() -> None:
    f = Fake(sse(chunk("Hel"), chunk("lo"), chunk(None, "stop"), USAGE, "[DONE]"))
    stream = f.client().chat.completions.create(model="m", messages=[], stream=True)
    assert stream.run_id == "run_s"
    text = "".join(c.choices[0].delta.content or "" for c in stream if c.choices)
    assert text == "Hello"
    body = json.loads(f.requests[0].content)
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert f.requests[0].headers["accept"] == "text/event-stream"
    r = stream.final_result()
    assert r.content == "Hello" and r.finish_reason == "stop"
    assert r.usage.total_tokens == 5 and r.tovano.charged_micro == 11


def test_stream_error_event() -> None:
    f = Fake(sse(chunk("x"), json.dumps({"error": {"code": "provider_error", "message": "boom"}})))
    with pytest.raises(ProviderError, match="boom"):
        f.client().chat.completions.create(model="m", messages=[], stream=True).final_text()


def test_stream_initial_error_and_retry() -> None:
    f = Fake(err(402, "insufficient_credits"))
    with pytest.raises(InsufficientCreditsError):
        f.client().chat.completions.create(model="m", messages=[], stream=True)
    f2 = Fake(err(503, "internal_error", {"retry-after": "0"}), sse(chunk("ok"), "[DONE]"))
    assert f2.client().chat.completions.create(model="m", messages=[], stream=True).final_text() == "ok"


def test_non_stream_sends_stream_false() -> None:
    f = Fake(
        ok({"object": "chat.completion", "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}}]})
    )
    res = f.client().chat.completions.create(model="m", messages=[{"role": "user", "content": "x"}], max_tokens=5)
    assert res.choices[0].message.content == "hi"
    body = json.loads(f.requests[0].content)
    assert body["stream"] is False and body["max_tokens"] == 5 and "temperature" not in body


async def test_async_client_and_stream() -> None:
    f = Fake(
        err(429, "rate_limited", {"retry-after": "0"}),
        ok({"object": "balance", "balance_micro": 7}),
        sse(chunk("a"), chunk("b"), "[DONE]"),
    )
    async with f.aclient() as c:
        assert (await c.balance()).balance_micro == 7
        stream = await c.chat.completions.create(model="m", messages=[], stream=True)
        parts = [ch.choices[0].delta.content async for ch in stream]
        assert parts == ["a", "b"]


async def test_async_errors() -> None:
    f = Fake(err(402, "spend_limit_reached"))
    async with f.aclient() as c:
        with pytest.raises(SpendLimitReachedError):
            await c.runs.create(model="m", input={})


# ------------------------------------------------------------------ webhooks

SECRET = "whsec_test"
BODY = json.dumps({"id": "run_1", "object": "run", "status": "completed"})


def test_webhook_sign_verify() -> None:
    sig = webhooks.sign(BODY, SECRET)
    assert webhooks.verify(BODY, sig, SECRET)
    assert webhooks.verify(BODY.encode(), sig, SECRET)
    run = webhooks.construct_event(BODY, sig, SECRET)
    assert run.id == "run_1" and isinstance(run, tovano.Run)


def test_webhook_matches_server_format() -> None:
    v1 = hmac.new(SECRET.encode(), b"1700000000.{}", hashlib.sha256).hexdigest()
    assert webhooks.verify("{}", f"t=1700000000,v1={v1}", SECRET, now=1700000000)


def test_webhook_rejects() -> None:
    sig = webhooks.sign(BODY, SECRET)
    assert not webhooks.verify(BODY, sig, "whsec_other")
    assert not webhooks.verify(BODY + " ", sig, SECRET)
    old = webhooks.sign(BODY, SECRET, int(time.time()) - 600)
    assert not webhooks.verify(BODY, old, SECRET)
    assert webhooks.verify(BODY, old, SECRET, tolerance=0)
    assert not webhooks.verify(BODY, None, SECRET)
    assert not webhooks.verify(BODY, "garbage", SECRET)
    with pytest.raises(WebhookVerificationError):
        webhooks.construct_event(BODY, "t=1,v1=00", SECRET)


def test_webhook_multiple_signatures() -> None:
    t = int(time.time())
    good = webhooks.sign(BODY, SECRET, t).split("v1=")[1]
    assert webhooks.verify(BODY, f"t={t},v1={'0' * 64},v1={good}", SECRET)


def test_client_webhooks_default_secret() -> None:
    c = Tovano(api_key="k", webhook_secret=SECRET)
    assert c.webhooks.verify(BODY, c.webhooks.sign(BODY))


# ------------------------------------------------------------------ money


def test_money() -> None:
    assert format_usd(84_120_000) == "$84.12"
    assert format_usd(2_500) == "$0.0025"
    assert format_usd(0) == "$0.00"
    assert format_usd(-1_000_000) == "-$1.00"
    assert format_usd(1_234_567_000_000) == "$1,234,567.00"
    assert usd_to_micro("12.50") == 12_500_000
    assert usd_to_micro(0.1 + 0.2) == 300_000
    assert micro_to_usd(2_500) == Decimal("0.0025")
