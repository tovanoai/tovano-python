"""Official Python client for the Tovano API.

One API key, one prepaid balance and one invoice for image, video, audio and text models.

>>> from tovano import Tovano
>>> client = Tovano()  # reads TOVANO_API_KEY
>>> run = client.runs.run(model="image/flux-1.1-pro", input={"prompt": "A blue ceramic mug"})
>>> run.status, run.charged_micro
('completed', 48000)
"""

from . import _webhooks as webhooks
from ._client import AsyncTovano, Tovano
from ._errors import (
    APIConnectionError,
    APIError,
    APITimeoutError,
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
    TovanoError,
    WebhookVerificationError,
)
from ._money import MICRO_PER_USD, format_usd, micro_to_usd, usd_to_micro
from ._streaming import AsyncChatCompletionStream, ChatCompletionStream, ChatStreamResult
from ._types import (
    Balance,
    ChatCompletion,
    ChatCompletionChunk,
    Model,
    OutputFile,
    Page,
    Price,
    Run,
    RunError,
    TovanoObject,
    Usage,
    UsageRow,
)
from ._version import __version__

__all__ = [
    "__version__",
    "Tovano",
    "AsyncTovano",
    "webhooks",
    # errors
    "TovanoError",
    "APIError",
    "InvalidRequestError",
    "AuthenticationError",
    "PaymentRequiredError",
    "InsufficientCreditsError",
    "SpendLimitReachedError",
    "PermissionDeniedError",
    "NotFoundError",
    "ConflictError",
    "RateLimitError",
    "InternalServerError",
    "ProviderError",
    "APIConnectionError",
    "APITimeoutError",
    "RunTimeoutError",
    "RunFailedError",
    "WebhookVerificationError",
    # money
    "MICRO_PER_USD",
    "format_usd",
    "usd_to_micro",
    "micro_to_usd",
    # streaming
    "ChatCompletionStream",
    "AsyncChatCompletionStream",
    "ChatStreamResult",
    # types
    "TovanoObject",
    "Model",
    "Price",
    "Run",
    "RunError",
    "OutputFile",
    "Balance",
    "Usage",
    "UsageRow",
    "Page",
    "ChatCompletion",
    "ChatCompletionChunk",
]
