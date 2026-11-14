# Changelog

## 1.0.0 (2026-11-16)

First release.

- `Tovano` (sync) and `AsyncTovano` (asyncio) clients with the full `/v1` API: models, tasks (create, get, list, iterate, wait, run), chat completions with streaming, balance, usage.
- Default base URL `https://api.tovano.ai`.
- Typed response objects (dicts with attribute access) and typed errors, including `InsufficientCreditsError` and `SpendLimitReachedError` (402) and `RateLimitError` with `retry_after`.
- Retries with exponential backoff and jitter, `Retry-After` honoured, the same `Idempotency-Key` on every attempt.
- Customer tags per call or per client (`with_options`).
- Webhook verification with timestamp tolerance and multiple signatures; `webhooks.sign()` for tests.
- Python 3.9 to 3.14. One dependency: `httpx`. Fully typed (`py.typed`, mypy strict).
