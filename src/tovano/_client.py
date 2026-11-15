"""The Tovano clients: :class:`Tovano` (sync) and :class:`AsyncTovano` (asyncio)."""

from __future__ import annotations

import asyncio
import time
from typing import (
    Any,
    AsyncIterator,
    Dict,
    Iterable,
    Iterator,
    List,
    Literal,
    Mapping,
    Optional,
    Union,
    overload,
)
from urllib.parse import quote

import httpx

from . import _webhooks
from ._base import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    ClientConfig,
    api_error,
    backoff,
    clean_body,
    clean_params,
    model_path,
    retry_delay,
    should_retry,
)
from ._errors import APIConnectionError, APITimeoutError, RunFailedError, RunTimeoutError
from ._streaming import AsyncChatCompletionStream, ChatCompletionStream
from ._types import Balance, ChatCompletion, Model, Page, Run, Usage

__all__ = ["Tovano", "AsyncTovano"]

Messages = Iterable[Mapping[str, Any]]
_FINISHED = ("completed", "failed")


class _Options:
    """Per-call options every method accepts as keyword arguments."""

    __slots__ = ("idempotency_key", "customer", "extra_headers", "timeout", "max_retries")

    def __init__(
        self,
        idempotency_key: Optional[str] = None,
        customer: Optional[str] = None,
        extra_headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> None:
        self.idempotency_key = idempotency_key
        self.customer = customer
        self.extra_headers = extra_headers
        self.timeout = timeout
        self.max_retries = max_retries


def _run_body(
    model: str, input: Mapping[str, Any], customer: Optional[str], webhook_url: Optional[str]
) -> Dict[str, Any]:
    return clean_body({"model": model, "input": dict(input), "customer": customer, "webhook_url": webhook_url})


def _chat_body(
    model: str,
    messages: Messages,
    stream: bool,
    extra: Mapping[str, Any],
) -> Dict[str, Any]:
    body = clean_body({"model": model, "messages": [dict(m) for m in messages], **extra})
    body["stream"] = stream
    if stream:
        body["stream_options"] = {"include_usage": True, **(extra.get("stream_options") or {})}
    return body


# ---------------------------------------------------------------- sync


class _SyncTransport:
    def __init__(self, config: ClientConfig, http_client: Optional[httpx.Client]) -> None:
        self.config = config
        self._own = http_client is None
        self.http = http_client or httpx.Client()

    def send(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        body: Optional[Mapping[str, Any]] = None,
        opts: _Options,
        stream: bool = False,
    ) -> httpx.Response:
        c = self.config
        headers = c.headers(
            method,
            stream=stream,
            has_body=body is not None,
            idempotency_key=opts.idempotency_key,
            customer=opts.customer,
            extra_headers=opts.extra_headers,
        )
        timeout = opts.timeout if opts.timeout is not None else c.timeout
        max_retries = opts.max_retries if opts.max_retries is not None else c.max_retries
        request = self.http.build_request(
            method, c.base_url + path, params=clean_params(params), json=body, headers=headers, timeout=timeout
        )
        attempt = 0
        while True:
            try:
                response = self.http.send(request, stream=stream)
            except httpx.TimeoutException as e:
                if attempt < max_retries:
                    time.sleep(backoff(attempt))
                    attempt += 1
                    continue
                raise APITimeoutError(
                    f"Request timed out after {timeout:g} s.", code="timeout", type="connection_error"
                ) from e
            except httpx.TransportError as e:
                if attempt < max_retries:
                    time.sleep(backoff(attempt))
                    attempt += 1
                    continue
                raise APIConnectionError(
                    f"Could not reach {c.base_url}: {e}", code="connection_error", type="connection_error"
                ) from e
            if response.is_success:
                return response
            text = response.read().decode("utf-8", "replace")
            response.close()
            if should_retry(response.status_code) and attempt < max_retries:
                wait = retry_delay(response, attempt)
                if wait is not None:
                    time.sleep(wait)
                    attempt += 1
                    continue
            raise api_error(response, text)

    def json(self, method: str, path: str, opts: _Options, **kw: Any) -> Any:
        return self.send(method, path, opts=opts, **kw).json()

    def close(self) -> None:
        if self._own:
            self.http.close()


class Models:
    def __init__(self, t: _SyncTransport) -> None:
        self._t = t

    def list(self, **opts: Any) -> List[Model]:
        """Every model you can call, with type, unit and price."""
        return [Model(m) for m in self._t.json("GET", "/v1/models", _Options(**opts))["data"]]

    def get(self, model_id: str, **opts: Any) -> Model:
        """One model, with the JSON Schema of its input."""
        return Model(self._t.json("GET", model_path(model_id), _Options(**opts)))


class Runs:
    def __init__(self, t: _SyncTransport) -> None:
        self._t = t

    def create(
        self,
        *,
        model: str,
        input: Mapping[str, Any],
        customer: Optional[str] = None,
        webhook_url: Optional[str] = None,
        **opts: Any,
    ) -> Run:
        """Starts a run. It runs in the background: :meth:`wait` for it, or receive a webhook."""
        body = _run_body(model, input, customer, webhook_url)
        return Run(self._t.json("POST", "/v1/runs", _Options(**opts), body=body))

    def get(self, run_id: str, **opts: Any) -> Run:
        return Run(self._t.json("GET", f"/v1/runs/{quote(run_id, safe='')}", _Options(**opts)))

    def list(
        self,
        *,
        status: Optional[str] = None,
        model: Optional[str] = None,
        customer: Optional[str] = None,
        type: Optional[str] = None,
        project_id: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        **opts: Any,
    ) -> "Page[Run]":
        """One page of runs, newest first."""
        params = dict(
            status=status, model=model, customer=customer, type=type, project_id=project_id, limit=limit, cursor=cursor
        )
        raw = self._t.json("GET", "/v1/runs", _Options(**opts), params=params)
        raw["data"] = [Run(t) for t in raw["data"]]
        return Page(raw)

    def iterate(self, **filters: Any) -> Iterator[Run]:
        """Every run matching the filters, newest first, fetching pages as needed."""
        cursor: Optional[str] = None
        while True:
            page = self.list(**filters, cursor=cursor)
            yield from page.data
            cursor = page.next_cursor
            if not cursor:
                return

    def wait(self, run_id: str, *, interval: float = 0.5, timeout: float = 600.0, **opts: Any) -> Run:
        """Polls until the run completes or fails. Raises :class:`RunTimeoutError` after ``timeout`` seconds."""
        deadline = time.monotonic() + timeout
        delay = interval
        while True:
            run = self.get(run_id, **opts)
            if run.status in _FINISHED:
                return run
            left = deadline - time.monotonic()
            if left <= 0:
                raise RunTimeoutError(run_id, timeout)
            time.sleep(min(delay, left))
            delay = min(delay * 1.5, 5.0)

    def run(
        self,
        *,
        model: str,
        input: Mapping[str, Any],
        customer: Optional[str] = None,
        webhook_url: Optional[str] = None,
        raise_on_failure: bool = False,
        wait_timeout: float = 600.0,
        interval: float = 0.5,
        **opts: Any,
    ) -> Run:
        """Creates a run and waits for it. A failed run is returned (uncharged) unless ``raise_on_failure``."""
        run = self.create(model=model, input=input, customer=customer, webhook_url=webhook_url, **opts)
        if run.status not in _FINISHED:
            wait_opts = {k: v for k, v in opts.items() if k != "idempotency_key"}
            run = self.wait(run.id, interval=interval, timeout=wait_timeout, **wait_opts)
        if raise_on_failure and run.status == "failed":
            raise RunFailedError(run)
        return run


class ChatCompletions:
    def __init__(self, t: _SyncTransport) -> None:
        self._t = t

    @overload
    def create(
        self, *, model: str, messages: Messages, stream: Literal[False] = False, **kwargs: Any
    ) -> ChatCompletion: ...

    @overload
    def create(
        self, *, model: str, messages: Messages, stream: Literal[True], **kwargs: Any
    ) -> ChatCompletionStream: ...

    def create(
        self,
        *,
        model: str,
        messages: Messages,
        stream: bool = False,
        max_tokens: Optional[int] = None,
        max_completion_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        stream_options: Optional[Mapping[str, Any]] = None,
        customer: Optional[str] = None,
        **opts: Any,
    ) -> Union[ChatCompletion, ChatCompletionStream]:
        """OpenAI-compatible chat completion. ``stream=True`` returns a :class:`ChatCompletionStream`."""
        extra = dict(
            max_tokens=max_tokens,
            max_completion_tokens=max_completion_tokens,
            temperature=temperature,
            stream_options=stream_options,
            customer=customer,
        )
        body = _chat_body(model, messages, stream, extra)
        if stream:
            return ChatCompletionStream(
                self._t.send("POST", "/v1/chat/completions", body=body, opts=_Options(**opts), stream=True)
            )
        return ChatCompletion(self._t.json("POST", "/v1/chat/completions", _Options(**opts), body=body))


class Chat:
    def __init__(self, t: _SyncTransport) -> None:
        self.completions = ChatCompletions(t)


class Webhooks:
    """Webhook helpers that use the client's ``webhook_secret`` by default."""

    def __init__(self, secret: Optional[str]) -> None:
        self._secret = secret

    def _key(self, secret: Optional[str]) -> str:
        s = secret or self._secret
        if not s:
            raise ValueError("No webhook secret: pass one, set webhook_secret=... or TOVANO_WEBHOOK_SECRET.")
        return s

    def verify(self, body: _webhooks.Body, signature: Optional[str], secret: Optional[str] = None, **kw: Any) -> bool:
        return _webhooks.verify(body, signature, self._key(secret), **kw)

    def construct_event(
        self, body: _webhooks.Body, signature: Optional[str], secret: Optional[str] = None, **kw: Any
    ) -> Run:
        return _webhooks.construct_event(body, signature, self._key(secret), **kw)

    def sign(self, body: _webhooks.Body, secret: Optional[str] = None, timestamp: Optional[int] = None) -> str:
        return _webhooks.sign(body, self._key(secret), timestamp)


class _ClientBase:
    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        customer: Optional[str] = None,
        webhook_secret: Optional[str] = None,
        default_headers: Optional[Mapping[str, str]] = None,
        app_info: Optional[str] = None,
    ) -> None:
        self._init_kwargs = dict(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            customer=customer,
            webhook_secret=webhook_secret,
            default_headers=default_headers,
            app_info=app_info,
        )
        self._config = ClientConfig(**self._init_kwargs)  # type: ignore[arg-type]
        self._init_kwargs["api_key"] = self._config.api_key
        self.webhooks = Webhooks(self._config.webhook_secret)

    @property
    def base_url(self) -> str:
        return self._config.base_url


class Tovano(_ClientBase):
    """The Tovano API client.

    >>> from tovano import Tovano
    >>> client = Tovano()  # reads TOVANO_API_KEY
    >>> run = client.runs.run(model="image/flux-1.1-pro", input={"prompt": "A blue mug"})

    Args:
        api_key: Default: the ``TOVANO_API_KEY`` environment variable.
        base_url: Default: ``TOVANO_BASE_URL``, else ``https://api.tovano.ai``.
        timeout: Seconds per attempt. Default 60.
        max_retries: Retries for connection errors, timeouts, 408, 429 and 5xx. Default 2.
        customer: Default end-customer tag for every call (``X-Tovano-Customer``).
        webhook_secret: Default secret for ``client.webhooks``. Default ``TOVANO_WEBHOOK_SECRET``.
        default_headers: Headers sent with every call.
        app_info: Your app's ``name/version``, added to the User-Agent.
        http_client: Your own ``httpx.Client`` (proxies, custom transports).
    """

    def __init__(self, *, http_client: Optional[httpx.Client] = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._http_client = http_client
        self._t = _SyncTransport(self._config, http_client)
        self.models = Models(self._t)
        self.runs = Runs(self._t)
        self.chat = Chat(self._t)

    def balance(self, **opts: Any) -> Balance:
        """Your organisation's balance: available, reserved and spent this month."""
        return Balance(self._t.json("GET", "/v1/balance", _Options(**opts)))

    def usage(
        self,
        *,
        group_by: Optional[str] = None,
        from_: Optional[str] = None,
        to: Optional[str] = None,
        customer: Optional[str] = None,
        model: Optional[str] = None,
        project_id: Optional[str] = None,
        **opts: Any,
    ) -> Usage:
        """Usage and spend grouped by model, customer, day, project or key. Default: this month by model.

        ``customer`` here filters the report; it is not a tag.
        """
        params = {
            "group_by": group_by,
            "from": from_,
            "to": to,
            "customer": customer,
            "model": model,
            "project_id": project_id,
        }
        return Usage(self._t.json("GET", "/v1/usage", _Options(**opts), params=params))

    def with_options(self, **kwargs: Any) -> "Tovano":
        """A copy of this client with some options changed. Shares the HTTP connection pool."""
        return Tovano(**{**self._init_kwargs, **kwargs, "http_client": self._t.http})

    def close(self) -> None:
        self._t.close()

    def __enter__(self) -> "Tovano":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# ---------------------------------------------------------------- async


class _AsyncTransport:
    def __init__(self, config: ClientConfig, http_client: Optional[httpx.AsyncClient]) -> None:
        self.config = config
        self._own = http_client is None
        self.http = http_client or httpx.AsyncClient()

    async def send(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        body: Optional[Mapping[str, Any]] = None,
        opts: _Options,
        stream: bool = False,
    ) -> httpx.Response:
        c = self.config
        headers = c.headers(
            method,
            stream=stream,
            has_body=body is not None,
            idempotency_key=opts.idempotency_key,
            customer=opts.customer,
            extra_headers=opts.extra_headers,
        )
        timeout = opts.timeout if opts.timeout is not None else c.timeout
        max_retries = opts.max_retries if opts.max_retries is not None else c.max_retries
        request = self.http.build_request(
            method, c.base_url + path, params=clean_params(params), json=body, headers=headers, timeout=timeout
        )
        attempt = 0
        while True:
            try:
                response = await self.http.send(request, stream=stream)
            except httpx.TimeoutException as e:
                if attempt < max_retries:
                    await asyncio.sleep(backoff(attempt))
                    attempt += 1
                    continue
                raise APITimeoutError(
                    f"Request timed out after {timeout:g} s.", code="timeout", type="connection_error"
                ) from e
            except httpx.TransportError as e:
                if attempt < max_retries:
                    await asyncio.sleep(backoff(attempt))
                    attempt += 1
                    continue
                raise APIConnectionError(
                    f"Could not reach {c.base_url}: {e}", code="connection_error", type="connection_error"
                ) from e
            if response.is_success:
                return response
            text = (await response.aread()).decode("utf-8", "replace")
            await response.aclose()
            if should_retry(response.status_code) and attempt < max_retries:
                wait = retry_delay(response, attempt)
                if wait is not None:
                    await asyncio.sleep(wait)
                    attempt += 1
                    continue
            raise api_error(response, text)

    async def json(self, method: str, path: str, opts: _Options, **kw: Any) -> Any:
        return (await self.send(method, path, opts=opts, **kw)).json()

    async def close(self) -> None:
        if self._own:
            await self.http.aclose()


class AsyncModels:
    def __init__(self, t: _AsyncTransport) -> None:
        self._t = t

    async def list(self, **opts: Any) -> List[Model]:
        return [Model(m) for m in (await self._t.json("GET", "/v1/models", _Options(**opts)))["data"]]

    async def get(self, model_id: str, **opts: Any) -> Model:
        return Model(await self._t.json("GET", model_path(model_id), _Options(**opts)))


class AsyncRuns:
    def __init__(self, t: _AsyncTransport) -> None:
        self._t = t

    async def create(
        self,
        *,
        model: str,
        input: Mapping[str, Any],
        customer: Optional[str] = None,
        webhook_url: Optional[str] = None,
        **opts: Any,
    ) -> Run:
        body = _run_body(model, input, customer, webhook_url)
        return Run(await self._t.json("POST", "/v1/runs", _Options(**opts), body=body))

    async def get(self, run_id: str, **opts: Any) -> Run:
        return Run(await self._t.json("GET", f"/v1/runs/{quote(run_id, safe='')}", _Options(**opts)))

    async def list(
        self,
        *,
        status: Optional[str] = None,
        model: Optional[str] = None,
        customer: Optional[str] = None,
        type: Optional[str] = None,
        project_id: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        **opts: Any,
    ) -> "Page[Run]":
        params = dict(
            status=status, model=model, customer=customer, type=type, project_id=project_id, limit=limit, cursor=cursor
        )
        raw = await self._t.json("GET", "/v1/runs", _Options(**opts), params=params)
        raw["data"] = [Run(t) for t in raw["data"]]
        return Page(raw)

    async def iterate(self, **filters: Any) -> AsyncIterator[Run]:
        cursor: Optional[str] = None
        while True:
            page = await self.list(**filters, cursor=cursor)
            for t in page.data:
                yield t
            cursor = page.next_cursor
            if not cursor:
                return

    async def wait(self, run_id: str, *, interval: float = 0.5, timeout: float = 600.0, **opts: Any) -> Run:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        delay = interval
        while True:
            run = await self.get(run_id, **opts)
            if run.status in _FINISHED:
                return run
            left = deadline - loop.time()
            if left <= 0:
                raise RunTimeoutError(run_id, timeout)
            await asyncio.sleep(min(delay, left))
            delay = min(delay * 1.5, 5.0)

    async def run(
        self,
        *,
        model: str,
        input: Mapping[str, Any],
        customer: Optional[str] = None,
        webhook_url: Optional[str] = None,
        raise_on_failure: bool = False,
        wait_timeout: float = 600.0,
        interval: float = 0.5,
        **opts: Any,
    ) -> Run:
        run = await self.create(model=model, input=input, customer=customer, webhook_url=webhook_url, **opts)
        if run.status not in _FINISHED:
            wait_opts = {k: v for k, v in opts.items() if k != "idempotency_key"}
            run = await self.wait(run.id, interval=interval, timeout=wait_timeout, **wait_opts)
        if raise_on_failure and run.status == "failed":
            raise RunFailedError(run)
        return run


class AsyncChatCompletions:
    def __init__(self, t: _AsyncTransport) -> None:
        self._t = t

    @overload
    async def create(
        self, *, model: str, messages: Messages, stream: Literal[False] = False, **kwargs: Any
    ) -> ChatCompletion: ...

    @overload
    async def create(
        self, *, model: str, messages: Messages, stream: Literal[True], **kwargs: Any
    ) -> AsyncChatCompletionStream: ...

    async def create(
        self,
        *,
        model: str,
        messages: Messages,
        stream: bool = False,
        max_tokens: Optional[int] = None,
        max_completion_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        stream_options: Optional[Mapping[str, Any]] = None,
        customer: Optional[str] = None,
        **opts: Any,
    ) -> Union[ChatCompletion, AsyncChatCompletionStream]:
        extra = dict(
            max_tokens=max_tokens,
            max_completion_tokens=max_completion_tokens,
            temperature=temperature,
            stream_options=stream_options,
            customer=customer,
        )
        body = _chat_body(model, messages, stream, extra)
        if stream:
            response = await self._t.send("POST", "/v1/chat/completions", body=body, opts=_Options(**opts), stream=True)
            return AsyncChatCompletionStream(response)
        return ChatCompletion(await self._t.json("POST", "/v1/chat/completions", _Options(**opts), body=body))


class AsyncChat:
    def __init__(self, t: _AsyncTransport) -> None:
        self.completions = AsyncChatCompletions(t)


class AsyncTovano(_ClientBase):
    """The asyncio version of :class:`Tovano`. Same arguments; ``http_client`` is an ``httpx.AsyncClient``.

    >>> async with AsyncTovano() as client:
    ...     run = await client.runs.run(model="image/flux-1.1-pro", input={"prompt": "A blue mug"})
    """

    def __init__(self, *, http_client: Optional[httpx.AsyncClient] = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._t = _AsyncTransport(self._config, http_client)
        self.models = AsyncModels(self._t)
        self.runs = AsyncRuns(self._t)
        self.chat = AsyncChat(self._t)

    async def balance(self, **opts: Any) -> Balance:
        return Balance(await self._t.json("GET", "/v1/balance", _Options(**opts)))

    async def usage(
        self,
        *,
        group_by: Optional[str] = None,
        from_: Optional[str] = None,
        to: Optional[str] = None,
        customer: Optional[str] = None,
        model: Optional[str] = None,
        project_id: Optional[str] = None,
        **opts: Any,
    ) -> Usage:
        params = {
            "group_by": group_by,
            "from": from_,
            "to": to,
            "customer": customer,
            "model": model,
            "project_id": project_id,
        }
        return Usage(await self._t.json("GET", "/v1/usage", _Options(**opts), params=params))

    def with_options(self, **kwargs: Any) -> "AsyncTovano":
        return AsyncTovano(**{**self._init_kwargs, **kwargs, "http_client": self._t.http})

    async def close(self) -> None:
        await self._t.close()

    async def __aenter__(self) -> "AsyncTovano":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()
