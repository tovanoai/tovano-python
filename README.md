# tovano

The official Python client for [Tovano](https://tovano.ai): one API key, one prepaid balance and one invoice for image, video, audio and text models, charged per unit.

| | |
|---|---|
| Python | 3.9 to 3.14 |
| Clients | `Tovano` (sync) and `AsyncTovano` (asyncio) |
| Dependencies | `httpx` only |
| Built in | Retries with backoff, idempotency keys, streaming, typed errors and objects, webhook verification |

## Install

```bash
pip install "git+https://github.com/tovanoai/tovano-python"
```

```bash
uv add tovano
```

Or straight from GitHub:

```bash
pip install git+https://github.com/tovanoai/tovano-python
```

## Quick start

```python
from tovano import Tovano, format_usd

client = Tovano()  # reads TOVANO_API_KEY

run = client.runs.run(
    model="image/flux-1.1-pro",
    input={"prompt": "Product photo of a blue ceramic mug", "n": 2},
)
print(run.status, run.units, format_usd(run.charged_micro))  # completed 2 $0.096
print(run.output.images[0].url)
```

Money is always an integer number of micro-dollars (1 USD = 1_000_000) in fields ending in `_micro`. `format_usd` turns it into text; `usd_to_micro` and `micro_to_usd` convert exactly (with `Decimal`).

Responses are typed objects that are also dicts: `run.status` and `run["status"]` both work, and `json.dumps(run)` works.

## Try it without a key

Every example in [`examples/`](./examples) runs in **offline mode** when `TOVANO_API_KEY` is not set: a simulated API answers with the same shapes and prices, at no cost.

```bash
git clone https://github.com/tovanoai/tovano-python && cd tovano-python && uv sync
uv run python examples/quickstart.py
```

| Example | Shows |
|---|---|
| `quickstart.py` | Balance, an image run, charge |
| `models.py` | The catalogue with prices |
| `chat.py` / `chat_stream.py` | OpenAI-compatible chat, with streaming |
| `async_batch.py` | Several models at once with asyncio |
| `agent_job.py` | An agent job with one charge per step |
| `customer_usage.py` | Customer tags and spend per customer |
| `errors.py` | Typed errors, retries, failed runs |
| `webhook_server.py` | Receive and verify webhooks (standard library) |

## Client options

```python
client = Tovano(
    api_key="tvn_live_...",  # default: TOVANO_API_KEY
    base_url="https://api.tovano.ai",  # default: TOVANO_BASE_URL, else https://api.tovano.ai
    max_retries=2,  # connection errors, timeouts, 408, 429, 5xx
    timeout=60.0,  # seconds per attempt
    customer="acme",  # default customer tag for every call
    webhook_secret="whsec_...",  # default: TOVANO_WEBHOOK_SECRET
    app_info="my-app/1.2.0",
    http_client=None,  # your own httpx.Client (proxies, transports)
)
```

Every method also takes per-call keyword options: `idempotency_key`, `customer` (header), `extra_headers`, `timeout`, `max_retries`. Use `with Tovano() as client:` or `client.close()` to free connections.

## Async

```python
import asyncio
from tovano import AsyncTovano


async def main():
    async with AsyncTovano() as client:
        a, b = await asyncio.gather(
            client.runs.run(model="image/flux-1.1-pro", input={"prompt": "Mug"}),
            client.runs.run(model="audio/eleven-multilingual-v2", input={"text": "Hello"}),
        )


asyncio.run(main())
```

`AsyncTovano` has the same methods as `Tovano`, as coroutines.

## Runs

```python
run = client.runs.create(
    model="video/kling-2.1-pro",
    input={"prompt": "Waves on a beach", "duration": 5},
    webhook_url="https://example.com/tovano-webhook",  # optional
)
done = client.runs.wait(run.id, timeout=120)
if done.status == "failed":
    print(done.error.message, "(not charged)")
```

| Method | What it does |
|---|---|
| `runs.create(model=, input=, customer=, webhook_url=)` | Starts a run. POSTs get a random `Idempotency-Key`, so a retry never runs twice. |
| `runs.get(run_id)` | Status, output, units and charge |
| `runs.wait(run_id, interval=0.5, timeout=600)` | Polls until done. Raises `RunTimeoutError`. |
| `runs.run(..., raise_on_failure=False)` | `create` + `wait` |
| `runs.list(status=, model=, customer=, type=, limit=, cursor=)` | One `Page` (iterable), newest first |
| `runs.iterate(**filters)` | Every match, paging for you |

## Chat (OpenAI-compatible)

```python
res = client.chat.completions.create(
    model="text/gpt-4.1-mini",
    messages=[{"role": "user", "content": "Hello"}],
)
print(res.choices[0].message.content, res.usage.total_tokens, res.tovano.charged_micro)

with client.chat.completions.create(model="text/gpt-4.1-mini", messages=messages, stream=True) as stream:
    for chunk in stream:
        if chunk.choices:
            print(chunk.choices[0].delta.content or "", end="")
    result = stream.final_result()  # content, finish_reason, usage, tovano
```

Leaving the `with` block early stops the stream; tokens already produced are charged. `stream.run_id` is the run that records the call. Async: `async for chunk in await client.chat.completions.create(..., stream=True)`.

Any OpenAI client works too: set its base URL to `https://api.tovano.ai/v1` and use your Tovano key.

## Models, balance and usage

```python
models = client.models.list()  # type, unit, prices
model = client.models.get("image/flux-1.1-pro")  # plus input_schema (JSON Schema)
balance = client.balance()  # available_micro, reserved_micro, spent_this_month_micro
usage = client.usage(group_by="customer", from_="2026-11-01")
```

`group_by` is `model`, `customer`, `day`, `project` or `key`. `from_` has an underscore because `from` is a Python keyword.

## Customer tags

```python
client.runs.create(model=model, input=inp, customer="acme")  # on a run or chat call
client.balance(customer="acme")  # any call, as an option
acme = client.with_options(customer="acme")  # a client that tags every call
```

## Webhooks

```python
from tovano import webhooks, WebhookVerificationError

run = webhooks.construct_event(
    raw_body,  # the raw bytes, as received
    request.headers["Tovano-Signature"],
    os.environ["TOVANO_WEBHOOK_SECRET"],
)
```

The header is `t=<unix>,v1=<hex HMAC-SHA256 of "t.body">`. Signatures older than 5 minutes are rejected (`tolerance=` changes it). `webhooks.verify()` returns a bool; `webhooks.sign()` builds a header for your tests. `client.webhooks.*` uses the client's `webhook_secret`.

## Errors

Every failed call raises a `TovanoError` with `status`, `code`, `type`, `param`, `request_id` and `headers`.

| Class | Status | Code | Meaning |
|---|---|---|---|
| `AuthenticationError` | 401 | `invalid_api_key` | Missing, invalid or revoked key |
| `InsufficientCreditsError` | 402 | `insufficient_credits` | Add credits in the dashboard |
| `SpendLimitReachedError` | 402 | `spend_limit_reached` | Monthly spend limit reached |
| `PermissionDeniedError` | 403 | `forbidden` | |
| `NotFoundError` | 404 | `not_found` | Unknown model or run |
| `ConflictError` | 409 | `conflict` | Idempotency-Key reused with another body |
| `InvalidRequestError` | 422 | `invalid_request` | `param` names the field |
| `RateLimitError` | 429 | `rate_limited` | Retried for you; `retry_after` in seconds |
| `InternalServerError` | 5xx | `internal_error` | Retried for you |
| `ProviderError` | 502 | `provider_error` | The model provider failed; not charged |
| `APIConnectionError` / `APITimeoutError` | 0 | | No response |
| `RunTimeoutError` | | `run_timeout` | `runs.wait()` gave up; the run keeps running |
| `RunFailedError` | | | `runs.run(raise_on_failure=True)` |

`InsufficientCreditsError` and `SpendLimitReachedError` both subclass `PaymentRequiredError`.

**Retries:** connection errors, timeouts, 408, 429 and 5xx are retried `max_retries` times with exponential backoff and jitter. `Retry-After` is honoured up to 60 seconds. The same `Idempotency-Key` is sent on every attempt.

## Development

```bash
uv sync
uv run pytest        # unit tests, plus integration tests against a local Tovano server when available
uv run mypy
uv run ruff check .
uv build
```

## License

MIT
