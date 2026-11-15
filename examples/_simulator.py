"""A small in-memory stand-in for the Tovano API, used by the examples in offline mode.

It mirrors the real response shapes and prices of the Tovano catalogue. Not part of the package.
"""

from __future__ import annotations

import itertools
import json
import math
import time
from datetime import datetime, timezone
from typing import Any, Dict
from urllib.parse import unquote

import httpx

_MODELS = [
    dict(id="image/flux-1.1-pro", type="image", name="FLUX1.1 [pro]", unit="image", price_micro=48_000),
    dict(id="image/flux-schnell", type="image", name="FLUX.1 [schnell]", unit="image", price_micro=4_000),
    dict(id="image/imagen-4", type="image", name="Imagen 4", unit="image", price_micro=48_000),
    dict(id="image/gpt-image-1", type="image", name="GPT Image 1", unit="image", price_micro=50_000),
    dict(id="image/seedream-4", type="image", name="Seedream 4.0", unit="image", price_micro=36_000),
    dict(id="video/kling-2.1-pro", type="video", name="Kling 2.1 Pro", unit="second", price_micro=120_000),
    dict(id="video/veo-3-fast", type="video", name="Veo 3 Fast", unit="second", price_micro=480_000),
    dict(id="video/seedance-1-pro", type="video", name="Seedance 1.0 Pro", unit="second", price_micro=75_000),
    dict(
        id="audio/eleven-multilingual-v2",
        type="audio",
        name="Eleven Multilingual v2",
        unit="1k_chars",
        price_micro=120_000,
    ),
    dict(id="audio/whisper-large-v3", type="audio", name="Whisper large-v3", unit="minute", price_micro=7_000),
    *[
        dict(
            id=mid,
            type="text",
            name=name,
            unit="1m_input_tokens",
            price_micro=inp,
            output_unit="1m_output_tokens",
            output_price_micro=out,
        )
        for mid, name, inp, out in [
            ("text/gpt-4.1-mini", "GPT-4.1 mini", 480_000, 1_920_000),
            ("text/gpt-4.1", "GPT-4.1", 2_400_000, 9_600_000),
            ("text/claude-sonnet-4-5", "Claude Sonnet 4.5", 3_600_000, 18_000_000),
            ("text/llama-3.3-70b", "Llama 3.3 70B", 720_000, 720_000),
        ]
    ],
    dict(id="agent/ad-kit", type="agent", name="Ad Kit", unit="run", price_micro=0),
]


def _price(model_id: str, key: str = "price_micro") -> int:
    m = next((x for x in _MODELS if x["id"] == model_id), None)
    return int(m.get(key, 0)) if m else 0


for m in _MODELS:
    m.update(
        object="model",
        description=f"{m['name']} (simulated)",
        available=True,
        created=1_763_000_000,
        owned_by="tovano",
        prices=[{"unit": m["unit"], "price_micro": m["price_micro"]}]
        + ([{"unit": m["output_unit"], "price_micro": m["output_price_micro"]}] if "output_unit" in m else []),
    )

REPLY = (
    "Here are three captions: 1. Your morning, glazed in blue. "
    "2. Hand-thrown, coffee-approved. 3. The mug you reach for first."
)


def simulated_transport(start_balance_micro: int = 25_000_000) -> httpx.MockTransport:
    state: Dict[str, Any] = {"balance": start_balance_micro}
    runs: Dict[str, Dict[str, Any]] = {}
    counter = itertools.count(1)

    def new_id(prefix: str) -> str:
        return f"{prefix}_sim{next(counter):06d}"

    def now() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def reply(body: Any, status: int = 200) -> httpx.Response:
        return httpx.Response(status, json=body, headers={"x-request-id": new_id("req")})

    def fail(status: int, code: str, message: str) -> httpx.Response:
        return reply({"error": {"type": "invalid_request_error", "code": code, "message": message}}, status)

    def file(name: str, ctype: str) -> Dict[str, Any]:
        return {
            "url": f"https://api.tovano.ai/files/example/{name}",
            "file_id": new_id("fil"),
            "content_type": ctype,
            "size": 48_213,
        }

    def finish(t: Dict[str, Any]) -> Dict[str, Any]:
        if t["status"] in ("completed", "failed") or time.time() < t["_ready"]:
            return t
        inp = t["input"]
        prompt = str(inp.get("prompt") or inp.get("text") or inp.get("product") or "")
        if "[fail]" in prompt:
            t.update(
                status="failed",
                error={"code": "provider_error", "message": "The model failed (simulated). You were not charged."},
                completed_at=now(),
            )
            return t
        model = t["model"]
        units, price, output = 1.0, 0, {}
        if model.startswith("image/"):
            units, price = float(inp.get("n", 1)), _price(model)
            output = {
                "images": [
                    dict(file(f"image-{i + 1}.png", "image/png"), width=1024, height=1024) for i in range(int(units))
                ]
            }
        elif model.startswith("video/"):
            units, price = float(inp.get("duration", 5)), _price(model)
            output = {
                "video": dict(file("clip.mp4", "video/mp4"), width=1280, height=720, duration=units, has_audio=False)
            }
        elif model.startswith("audio/"):
            units, price = len(str(inp.get("text", ""))) / 1000, _price(model)
            output = {
                "audio": dict(file("speech.wav", "audio/wav"), duration_ms=2400),
                "characters": len(inp.get("text", "")),
            }
        elif model.startswith("agent/"):
            t["steps"] = [
                {
                    "id": new_id("run"),
                    "object": "run",
                    "status": "completed",
                    "model": "text/gpt-4.1-mini",
                    "name": "copy",
                    "charged_micro": 190,
                },
                {
                    "id": new_id("run"),
                    "object": "run",
                    "status": "completed",
                    "model": "image/flux-schnell",
                    "name": "image",
                    "charged_micro": 4_000,
                },
            ]
            output = {
                "headline": f"Meet {prompt}",
                "body": "Made by hand, glazed in deep blue, built for slow mornings.",
                "image": file("ad.png", "image/png"),
            }
            price = 4_190
        charged = round(units * price)
        state["balance"] -= charged
        t.update(status="completed", output=output, units=units, charged_micro=charged, completed_at=now())
        return t

    def view(t: Dict[str, Any]) -> Dict[str, Any]:
        v = {k: val for k, val in finish(t).items() if not k.startswith("_")}
        if v["status"] == "queued":
            v["status"] = "running"
        return v

    def handler(request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else {}
        customer = body.get("customer") or request.headers.get("x-tovano-customer")
        time.sleep(0.04)  # network latency

        if method == "GET" and path == "/v1/models":
            return reply({"object": "list", "data": _MODELS})
        if method == "GET" and path.startswith("/v1/models/"):
            mid = unquote(path[len("/v1/models/") :])
            m = next((x for x in _MODELS if x["id"] == mid), None)
            return reply({**m, "input_schema": {"type": "object"}}) if m else fail(404, "not_found", "Model not found.")
        if method == "GET" and path == "/v1/balance":
            b = state["balance"]
            return reply(
                {
                    "object": "balance",
                    "balance_micro": b,
                    "reserved_micro": 0,
                    "available_micro": b,
                    "spent_this_month_micro": start_balance_micro - b,
                    "spend_limit_micro": None,
                    "low_balance_alert_micro": 1_000_000,
                    "currency": "usd",
                }
            )
        if method == "POST" and path == "/v1/runs":
            m = next((x for x in _MODELS if x["id"] == body.get("model")), None)
            if not m:
                return fail(404, "not_found", "Model not found.")
            if state["balance"] <= 0:
                return fail(402, "insufficient_credits", "Add credits to continue.")
            t = {
                "id": new_id("run"),
                "object": "run",
                "model": m["id"],
                "status": "queued",
                "customer": customer,
                "input": body.get("input") or {},
                "output": None,
                "unit": m["unit"],
                "units": 0,
                "estimate_micro": 0,
                "charged_micro": 0,
                "error": None,
                "source": "api",
                "created_at": now(),
                "completed_at": None,
                "_ready": time.time() + 0.6,
            }
            runs[t["id"]] = t
            return reply({k: v for k, v in t.items() if not k.startswith("_")}, 201)
        if method == "GET" and path.startswith("/v1/runs/"):
            t = runs.get(path[len("/v1/runs/") :])
            return reply(view(t)) if t else fail(404, "not_found", "Run not found.")
        if method == "GET" and path == "/v1/runs":
            data = [view(t) for t in reversed(list(runs.values()))]
            return reply({"object": "list", "data": data, "next_cursor": None, "has_more": False, "total": len(data)})
        if method == "GET" and path == "/v1/usage":
            by = request.url.params.get("group_by", "model")
            groups: Dict[str, Dict[str, Any]] = {}
            for t in runs.values():
                finish(t)
                key = str(t["customer"] if by == "customer" else t["model"])
                g = groups.setdefault(
                    key,
                    {
                        "key": key,
                        "type": None,
                        "runs": 0,
                        "failed": 0,
                        "unit": None,
                        "units": None,
                        "charged_micro": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                    },
                )
                g["runs"] += 1
                g["charged_micro"] += t["charged_micro"]
            total = sum(g["charged_micro"] for g in groups.values())
            return reply(
                {
                    "object": "usage",
                    "group_by": by,
                    "from": "2026-11-01T00:00:00.000Z",
                    "to": now(),
                    "total_micro": total,
                    "total_runs": len(runs),
                    "total": {
                        "runs": len(runs),
                        "failed": 0,
                        "charged_micro": total,
                        "input_tokens": 0,
                        "output_tokens": 0,
                    },
                    "data": list(groups.values()),
                }
            )
        if method == "POST" and path == "/v1/chat/completions":
            prompt_tokens = max(1, math.ceil(len(json.dumps(body.get("messages", []))) / 4))
            completion_tokens = max(1, math.ceil(len(REPLY) / 4))
            usage = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            }
            m = str(body.get("model"))
            charged = round(
                (prompt_tokens * _price(m) + completion_tokens * _price(m, "output_price_micro")) / 1_000_000
            )
            state["balance"] -= charged
            run_id = new_id("run")
            base = {"id": f"chatcmpl-{run_id}", "created": int(time.time()), "model": body.get("model")}
            billing = {"run_id": run_id, "charged_micro": charged}
            if not body.get("stream"):
                return reply(
                    {
                        **base,
                        "object": "chat.completion",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": REPLY},
                                "finish_reason": "stop",
                                "logprobs": None,
                            }
                        ],
                        "usage": usage,
                        "tovano": billing,
                    }
                )

            def chunk(delta: Dict[str, Any], finish_reason: Any = None) -> Dict[str, Any]:
                return {
                    **base,
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
                }

            events = [chunk({"role": "assistant", "content": ""})]
            events += [chunk({"content": w + " "}) for w in REPLY.split(" ")]
            events += [
                chunk({}, "stop"),
                {**base, "object": "chat.completion.chunk", "choices": [], "usage": usage, "tovano": billing},
            ]
            sse = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream", "x-tovano-run-id": run_id},
                stream=httpx.ByteStream(sse.encode()),
            )
        return fail(404, "not_found", "Not found.")

    return httpx.MockTransport(handler)
