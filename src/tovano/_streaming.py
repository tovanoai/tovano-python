"""Server-sent events for streamed chat completions."""

from __future__ import annotations

import json
from typing import Any, AsyncIterable, AsyncIterator, Iterable, Iterator, Optional

import httpx

from ._errors import error_from_response
from ._types import ChatBilling, ChatCompletionChunk, ChatUsage, TovanoObject

__all__ = ["ChatCompletionStream", "AsyncChatCompletionStream", "ChatStreamResult"]


def iter_sse_data(lines: Iterable[str]) -> Iterator[str]:
    """Yields the ``data:`` payload of each event."""
    data: list[str] = []
    for line in lines:
        line = line.rstrip("\r\n")
        if line == "":
            if data:
                yield "\n".join(data)
            data = []
        elif line.startswith("data:"):
            data.append(line[6:] if line.startswith("data: ") else line[5:])
        # comments (":") and other fields are ignored
    if data:
        yield "\n".join(data)


async def aiter_sse_data(lines: AsyncIterable[str]) -> AsyncIterator[str]:
    data: list[str] = []
    async for line in lines:
        line = line.rstrip("\r\n")
        if line == "":
            if data:
                yield "\n".join(data)
            data = []
        elif line.startswith("data:"):
            data.append(line[6:] if line.startswith("data: ") else line[5:])
    if data:
        yield "\n".join(data)


class ChatStreamResult(TovanoObject):
    """The whole reply, assembled from a stream."""

    content: str
    finish_reason: Optional[str]
    usage: Optional[ChatUsage]
    tovano: Optional[ChatBilling]


class _StreamState:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.task_id: Optional[str] = response.headers.get("x-tovano-task-id")
        self.request_id: Optional[str] = response.headers.get("x-request-id")
        self.content = ""
        self.finish_reason: Optional[str] = None
        self.usage: Optional[ChatUsage] = None
        self.tovano: Optional[ChatBilling] = None
        self.done = False

    def handle(self, data: str) -> Optional[ChatCompletionChunk]:
        """Returns a chunk, or None for [DONE] / unparseable data. Raises API errors."""
        if data == "[DONE]":
            self.done = True
            return None
        try:
            raw: Any = json.loads(data)
        except ValueError:
            return None
        if isinstance(raw, dict) and raw.get("error"):
            err = raw["error"]
            raise error_from_response(502 if err.get("code") == "provider_error" else 500, err, self.response.headers)
        chunk = ChatCompletionChunk(raw)
        choices = chunk.get("choices") or []
        if choices:
            delta = choices[0].get("delta") or {}
            if delta.get("content"):
                self.content += delta["content"]
            if choices[0].get("finish_reason"):
                self.finish_reason = choices[0]["finish_reason"]
        if chunk.get("usage"):
            self.usage = chunk["usage"]
        if chunk.get("tovano"):
            self.tovano = chunk["tovano"]
        return chunk

    def result(self) -> ChatStreamResult:
        return ChatStreamResult(
            content=self.content, finish_reason=self.finish_reason, usage=self.usage, tovano=self.tovano
        )


class ChatCompletionStream:
    """A streamed chat completion.

    Iterate it for chunks, or call :meth:`final_text` / :meth:`final_result` to read it to
    the end. Use it as a context manager, or call :meth:`close`, to stop early: tokens already
    produced are charged.
    """

    def __init__(self, response: httpx.Response) -> None:
        self._state = _StreamState(response)
        self._iterated = False

    @property
    def response(self) -> httpx.Response:
        return self._state.response

    @property
    def task_id(self) -> Optional[str]:
        """The task that records this call (``X-Tovano-Task-Id``)."""
        return self._state.task_id

    @property
    def request_id(self) -> Optional[str]:
        return self._state.request_id

    def __iter__(self) -> Iterator[ChatCompletionChunk]:
        if self._iterated:
            raise RuntimeError("This stream was already read. A stream can be read once.")
        self._iterated = True
        try:
            for data in iter_sse_data(self.response.iter_lines()):
                chunk = self._state.handle(data)
                if self._state.done:
                    return
                if chunk is not None:
                    yield chunk
        finally:
            self.close()

    def close(self) -> None:
        """Stops the stream and frees the connection."""
        self.response.close()

    def final_result(self) -> ChatStreamResult:
        """Reads the stream to the end; returns the reply, usage and charge."""
        if not self._iterated:
            for _ in self:
                pass
        return self._state.result()

    def final_text(self) -> str:
        return self.final_result().content

    def __enter__(self) -> "ChatCompletionStream":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class AsyncChatCompletionStream:
    """The async version of :class:`ChatCompletionStream`. Iterate with ``async for``."""

    def __init__(self, response: httpx.Response) -> None:
        self._state = _StreamState(response)
        self._iterated = False

    @property
    def response(self) -> httpx.Response:
        return self._state.response

    @property
    def task_id(self) -> Optional[str]:
        return self._state.task_id

    @property
    def request_id(self) -> Optional[str]:
        return self._state.request_id

    async def __aiter__(self) -> AsyncIterator[ChatCompletionChunk]:
        if self._iterated:
            raise RuntimeError("This stream was already read. A stream can be read once.")
        self._iterated = True
        try:
            async for data in aiter_sse_data(self.response.aiter_lines()):
                chunk = self._state.handle(data)
                if self._state.done:
                    return
                if chunk is not None:
                    yield chunk
        finally:
            await self.close()

    async def close(self) -> None:
        await self.response.aclose()

    async def final_result(self) -> ChatStreamResult:
        if not self._iterated:
            async for _ in self:
                pass
        return self._state.result()

    async def final_text(self) -> str:
        return (await self.final_result()).content

    async def __aenter__(self) -> "AsyncChatCompletionStream":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()
