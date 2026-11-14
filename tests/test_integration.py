"""End-to-end tests against a real Tovano server with mock providers (see conftest.py)."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Dict, List

import pytest

from tovano import (
    AsyncTovano,
    AuthenticationError,
    ChatCompletionStream,
    ConflictError,
    InsufficientCreditsError,
    InvalidRequestError,
    NotFoundError,
    PaymentRequiredError,
    RateLimitError,
    SpendLimitReachedError,
    TaskFailedError,
    Tovano,
    TovanoError,
    webhooks,
)

pytestmark = pytest.mark.server

Account = Dict[str, Any]


def make_client(acc: Account, **kw: Any) -> Tovano:
    return Tovano(api_key=acc["api_key"], base_url=acc["base_url"], **kw)


# ------------------------------------------------------------------ models


def test_models(client: Tovano) -> None:
    models = client.models.list()
    image = next(m for m in models if m.id == "image/flux-1.1-pro")
    assert image.type == "image" and image.unit == "image" and image.price_micro == 48_000
    chat = next(m for m in models if m.id == "text/gpt-4.1-mini")
    assert chat.output_unit == "1m_output_tokens"
    assert [p.unit for p in chat.prices] == ["1m_input_tokens", "1m_output_tokens"]
    one = client.models.get("video/kling-2.1-pro")
    assert one.input_schema["required"] == ["prompt"]
    with pytest.raises(NotFoundError) as e:
        client.models.get("image/nope")
    assert e.value.status == 404 and e.value.request_id


# ------------------------------------------------------------------ tasks


def test_run_image_task_and_charge(client: Tovano) -> None:
    task = client.tasks.run(model="image/flux-1.1-pro", input={"prompt": "Blue ceramic mug", "n": 2}, interval=0.1)
    assert task.status == "completed"
    assert task.units == 2 and task.charged_micro == 96_000
    assert len(task.output.images) == 2
    assert task.output.images[0].url.startswith("http")
    b = client.balance()
    assert b.balance_micro == 10_000_000 - 96_000
    assert b.available_micro == b.balance_micro - b.reserved_micro


def test_create_get_wait_list(client: Tovano) -> None:
    created = client.tasks.create(model="audio/eleven-multilingual-v2", input={"text": "Hello there"})
    assert created.object == "task" and created.status in ("queued", "running")
    assert client.tasks.get(created.id).id == created.id
    done = client.tasks.wait(created.id, interval=0.1)
    assert done.status == "completed"
    page = client.tasks.list(model="audio/eleven-multilingual-v2", limit=5)
    assert page.object == "list" and created.id in [t.id for t in page]
    assert page.has_more is False


def test_iterate(client: Tovano) -> None:
    ids = {client.tasks.create(model="image/flux-1.1-pro", input={"prompt": f"p{i}"}).id for i in range(5)}
    first = client.tasks.list(limit=2)
    assert len(first) == 2 and first.has_more and first.next_cursor
    assert {t.id for t in client.tasks.iterate(limit=2)} == ids


def test_failed_task_is_not_charged(client: Tovano) -> None:
    failed = client.tasks.run(model="image/flux-1.1-pro", input={"prompt": "[fail] mug"}, interval=0.1)
    assert failed.status == "failed" and failed.charged_micro == 0
    assert "failed on purpose" in failed.error.message
    with pytest.raises(TaskFailedError):
        client.tasks.run(
            model="image/flux-1.1-pro", input={"prompt": "[fail] again"}, raise_on_failure=True, interval=0.1
        )
    assert client.balance().balance_micro == 10_000_000


def test_agent_job_steps(client: Tovano) -> None:
    job = client.tasks.run(model="agent/ad-kit", input={"product": "A blue mug"}, interval=0.1)
    assert job.status == "completed"
    assert len(job.steps) >= 2
    assert job.charged_micro == sum(s.charged_micro for s in job.steps)


def test_invalid_input(client: Tovano) -> None:
    with pytest.raises(InvalidRequestError) as e:
        client.tasks.create(model="video/kling-2.1-pro", input={"prompt": "x", "duration": 99})
    assert e.value.status == 422 and "duration" in (e.value.param or "")


def test_idempotency(client: Tovano) -> None:
    a = client.tasks.create(model="image/flux-1.1-pro", input={"prompt": "Same"}, idempotency_key="py-fixed-1")
    b = client.tasks.create(model="image/flux-1.1-pro", input={"prompt": "Same"}, idempotency_key="py-fixed-1")
    assert a.id == b.id
    with pytest.raises(ConflictError):
        client.tasks.create(model="image/flux-1.1-pro", input={"prompt": "Different"}, idempotency_key="py-fixed-1")


# ------------------------------------------------------------------ customers and usage


def test_customer_tags_and_usage(account: Account) -> None:
    client = make_client(account)
    client.tasks.run(model="image/flux-1.1-pro", input={"prompt": "a"}, customer="acme", interval=0.1)
    client.tasks.run(
        model="image/flux-1.1-pro", input={"prompt": "b"}, interval=0.1, extra_headers={"x-tovano-customer": "acme"}
    )
    globex = make_client(account, customer="globex")
    t = globex.tasks.run(model="image/flux-1.1-pro", input={"prompt": "c", "n": 3}, interval=0.1)
    assert t.customer == "globex"
    globex.chat.completions.create(model="text/gpt-4.1-mini", messages=[{"role": "user", "content": "hi"}])

    usage = client.usage(group_by="customer")
    assert usage.object == "usage" and usage.group_by == "customer"
    rows = {r.key: r for r in usage.data}
    assert rows["acme"].tasks == 2 and rows["acme"].charged_micro == 96_000
    assert rows["globex"].tasks == 2
    assert usage.total_micro == sum(r.charged_micro for r in usage.data)
    assert usage.from_
    assert all(x.customer == "acme" for x in client.tasks.list(customer="acme"))


# ------------------------------------------------------------------ chat

MESSAGES = [{"role": "user", "content": "Write one line about mugs."}]


def test_chat_completion(client: Tovano) -> None:
    res = client.chat.completions.create(model="text/gpt-4.1-mini", messages=MESSAGES)
    assert res.object == "chat.completion"
    assert res.choices[0].message.content
    assert res.usage.total_tokens == res.usage.prompt_tokens + res.usage.completion_tokens
    assert res.tovano.task_id.startswith("tsk_") and res.tovano.charged_micro > 0
    assert client.tasks.get(res.tovano.task_id).charged_micro == res.tovano.charged_micro


def test_chat_stream(client: Tovano) -> None:
    stream = client.chat.completions.create(model="text/gpt-4.1-mini", messages=MESSAGES, stream=True)
    assert isinstance(stream, ChatCompletionStream)
    assert stream.task_id and stream.task_id.startswith("tsk_")
    chunks = list(stream)
    text = "".join((c.choices[0].delta.content or "") for c in chunks if c.choices)
    assert text
    assert chunks[-1].usage.prompt_tokens > 0
    r = stream.final_result()
    assert r.content == text and r.finish_reason == "stop"
    assert client.tasks.get(stream.task_id).charged_micro == r.tovano.charged_micro


def test_chat_stream_stop_early(client: Tovano) -> None:
    with client.chat.completions.create(
        model="text/gpt-4.1-mini",
        messages=[{"role": "user", "content": "Write a long story."}],
        max_tokens=2000,
        stream=True,
    ) as stream:
        for n, _ in enumerate(stream, 1):
            if n >= 3:
                break
    assert n == 3


def test_chat_errors(client: Tovano) -> None:
    with pytest.raises(InvalidRequestError) as e:
        client.chat.completions.create(model="image/flux-1.1-pro", messages=MESSAGES, stream=True)
    assert e.value.param == "model"


# ------------------------------------------------------------------ billing errors


def test_insufficient_credits(new_account: Callable[..., Account]) -> None:
    client = make_client(new_account(balance_micro=0))
    with pytest.raises(InsufficientCreditsError) as e:
        client.tasks.create(model="image/flux-1.1-pro", input={"prompt": "x"})
    assert isinstance(e.value, PaymentRequiredError) and isinstance(e.value, TovanoError)
    assert e.value.status == 402 and e.value.code == "insufficient_credits"
    with pytest.raises(InsufficientCreditsError):
        client.chat.completions.create(model="text/gpt-4.1-mini", messages=MESSAGES)


def test_spend_limit(new_account: Callable[..., Account]) -> None:
    client = make_client(new_account(spend_limit_micro=60_000))
    client.tasks.run(model="image/flux-1.1-pro", input={"prompt": "first"}, interval=0.1)
    with pytest.raises(SpendLimitReachedError) as e:
        client.tasks.create(model="image/flux-1.1-pro", input={"prompt": "x"})
    assert e.value.status == 402 and e.value.code == "spend_limit_reached"
    assert client.balance().spend_limit_micro == 60_000


# ------------------------------------------------------------------ auth and rate limits


def test_bad_and_revoked_key(account: Account, control: Callable[..., Any]) -> None:
    with pytest.raises(AuthenticationError):
        Tovano(api_key="tvn_live_0000000000000000000000000000000x", base_url=account["base_url"]).balance()
    client = make_client(account)
    client.balance()
    control("/revoke", {"key_id": account["key_id"]})
    with pytest.raises(AuthenticationError) as e:
        client.balance()
    assert e.value.code == "invalid_api_key"


def test_rate_limit(account: Account, control: Callable[..., Any]) -> None:
    client = make_client(account, max_retries=0)
    control("/exhaust", {"key_id": account["key_id"]})
    with pytest.raises(RateLimitError) as e:
        client.balance()
    assert e.value.status == 429 and e.value.code == "rate_limited"
    assert 0 < (e.value.retry_after or 0) <= 60


# ------------------------------------------------------------------ webhooks


def test_webhook_delivery(account: Account) -> None:
    received: List[Dict[str, Any]] = []
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body = self.rfile.read(int(self.headers["content-length"]))
            received.append({"body": body, "sig": self.headers.get("tovano-signature")})
            self.send_response(200)
            self.end_headers()
            done.set()

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = make_client(account, webhook_secret=account["webhook_secret"])
        client.tasks.create(
            model="image/flux-1.1-pro",
            input={"prompt": "hook"},
            webhook_url=f"http://127.0.0.1:{server.server_port}/hook",
        )
        assert done.wait(20), "no webhook arrived"
    finally:
        server.shutdown()
    body, sig = received[0]["body"], received[0]["sig"]
    assert webhooks.verify(body, sig, account["webhook_secret"])
    assert not webhooks.verify(body, sig, "whsec_wrong")
    task = webhooks.construct_event(body, sig, account["webhook_secret"])
    assert task.status == "completed" and task.charged_micro == 48_000
    assert client.webhooks.verify(body, sig)


# ------------------------------------------------------------------ async


async def test_async_end_to_end(aclient: AsyncTovano) -> None:
    models = await aclient.models.list()
    assert any(m.id == "image/flux-1.1-pro" for m in models)
    task = await aclient.tasks.run(model="image/flux-1.1-pro", input={"prompt": "async mug"}, interval=0.1)
    assert task.status == "completed" and task.charged_micro == 48_000
    res = await aclient.chat.completions.create(model="text/gpt-4.1-mini", messages=MESSAGES)
    assert res.tovano.charged_micro > 0
    stream = await aclient.chat.completions.create(model="text/gpt-4.1-mini", messages=MESSAGES, stream=True)
    text = "".join([(c.choices[0].delta.content or "") async for c in stream if c.choices])
    assert text and (await stream.final_result()).content == text
    ids = [t.id async for t in aclient.tasks.iterate(limit=1)]
    assert task.id in ids
    b = await aclient.balance()
    assert b.balance_micro < 10_000_000
    u = await aclient.usage(group_by="model")
    assert any(r.key == "image/flux-1.1-pro" for r in u.data)


async def test_async_errors(new_account: Callable[..., Account]) -> None:
    acc = new_account(balance_micro=0)
    async with AsyncTovano(api_key=acc["api_key"], base_url=acc["base_url"]) as c:
        with pytest.raises(InsufficientCreditsError):
            await c.tasks.create(model="image/flux-1.1-pro", input={"prompt": "x"})
        with pytest.raises(NotFoundError):
            await c.tasks.get("tsk_missing")
