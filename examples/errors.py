"""python examples/errors.py — typed errors. 429 and 5xx are retried for you; these are what's left."""

from _client import client

from tovano import (
    APIConnectionError,
    InsufficientCreditsError,
    InvalidRequestError,
    NotFoundError,
    RateLimitError,
    RunFailedError,
    SpendLimitReachedError,
    TovanoError,
)


def try_it(label, fn):
    try:
        fn()
        print(f"{label}: ok")
    except InsufficientCreditsError:
        print(f"{label}: out of credits, top up in the dashboard")
    except SpendLimitReachedError:
        print(f"{label}: monthly spend limit reached")
    except RateLimitError as e:
        print(f"{label}: slow down, retry in {e.retry_after}s")
    except NotFoundError as e:
        print(f"{label}: not found ({e.message})")
    except InvalidRequestError as e:
        print(f"{label}: invalid {e.param}: {e.message}")
    except RunFailedError as e:
        print(f"{label}: run {e.run.id} failed, not charged: {e.message}")
    except APIConnectionError as e:
        print(f"{label}: network problem: {e.message}")
    except TovanoError as e:
        print(f"{label}: {e.status} {e.code} (request {e.request_id})")


try_it("unknown model", lambda: client.runs.create(model="image/nope", input={"prompt": "x"}))
# A failed run is returned (not raised) and never charged. Ask for an error instead:
try_it(
    "failing run",
    lambda: client.runs.run(model="image/flux-1.1-pro", input={"prompt": "[fail] test"}, raise_on_failure=True),
)
# Retries and timeouts are configurable per client or per call:
try_it("custom retries", lambda: client.balance(max_retries=5, timeout=10))
