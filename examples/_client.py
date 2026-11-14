"""Shared by the examples. With TOVANO_API_KEY set, the examples call the real API.

Without it they run in offline mode: a simulated Tovano API answers in memory, with the same
shapes, prices and timings, so you can see what each call does at no cost.
"""

import os

import httpx
from _simulator import simulated_transport

from tovano import AsyncTovano, Tovano

OFFLINE = not os.environ.get("TOVANO_API_KEY")

if OFFLINE:
    print("Offline mode: simulated responses. Set TOVANO_API_KEY to call the real API.\n")
    _transport = simulated_transport()
    client = Tovano(api_key="tvn_live_offline", http_client=httpx.Client(transport=_transport))
else:
    client = Tovano()


def async_client() -> AsyncTovano:
    if not OFFLINE:
        return AsyncTovano()

    async def handle(request: httpx.Request) -> httpx.Response:
        response = _transport.handle_request(request)
        await response.aread()
        return response

    return AsyncTovano(api_key="tvn_live_offline", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))
