"""Typed objects for every response.

Each object is a ``dict`` with attribute access, so ``task.status`` and
``task["status"]`` both work, ``json.dumps(task)`` works, and fields the API adds
later are kept. Money is always an integer number of micro-dollars
(1 USD = 1_000_000) in fields ending in ``_micro``.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, Generic, Iterator, List, Literal, Optional, TypeVar, Union

__all__ = [
    "TovanoObject",
    "Price",
    "Model",
    "OutputFile",
    "TaskError",
    "TaskUsage",
    "Task",
    "Balance",
    "UsageRow",
    "UsageTotal",
    "Usage",
    "Page",
    "ChatMessage",
    "ChatUsage",
    "ChatBilling",
    "ChatCompletionMessage",
    "ChatCompletionChoice",
    "ChatCompletion",
    "ChatCompletionDelta",
    "ChatCompletionChunkChoice",
    "ChatCompletionChunk",
    "TaskStatus",
    "ModelType",
    "UsageGroupBy",
]

TaskStatus = Literal["queued", "running", "completed", "failed"]
ModelType = Literal["image", "video", "audio", "text", "agent"]
UsageGroupBy = Literal["model", "customer", "day", "project", "key"]

T = TypeVar("T")


class TovanoObject(Dict[str, Any]):
    """A dict with attribute access. Nested dicts become ``TovanoObject`` too."""

    # field name -> class for nested objects; a one-item list means "list of".
    _fields: ClassVar[Dict[str, Any]] = {}

    def __init__(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> None:
        super().__init__()
        for k, v in {**(data or {}), **kwargs}.items():
            self[k] = _convert(v, type(self)._fields.get(k))

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError:
            # Optional fields the API left out read as None.
            if name in _all_annotations(type(self)):
                return None
            raise AttributeError(f"{type(self).__name__} has no field {name!r}") from None

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value

    def __repr__(self) -> str:
        return f"{type(self).__name__}({dict.__repr__(self)})"

    def to_dict(self) -> Dict[str, Any]:
        """A plain ``dict`` copy, with nested objects as plain dicts too."""
        out: Dict[str, Any] = _plain(self)
        return out


def _all_annotations(cls: type) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for c in reversed(cls.__mro__):
        out.update(getattr(c, "__annotations__", {}))
    return out


def _convert(value: Any, kind: Any) -> Any:
    if isinstance(kind, list):
        if isinstance(value, list):
            return [_convert(v, kind[0]) for v in value]
        return value
    if isinstance(value, dict) and not isinstance(value, TovanoObject):
        return (kind or TovanoObject)(value)
    if isinstance(value, list):
        return [_convert(v, None) for v in value]
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


class Price(TovanoObject):
    unit: str
    price_micro: int


class Model(TovanoObject):
    """A model you can call, with its unit and price."""

    _fields = {"prices": [Price]}
    id: str
    """For example ``"image/flux-1.1-pro"``."""
    object: str
    type: ModelType
    name: str
    description: str
    unit: str
    """``image``, ``second``, ``minute``, ``1k_chars``, ``1m_input_tokens``, ``1m_output_tokens`` or ``run``."""
    price_micro: int
    output_unit: Optional[str]
    output_price_micro: Optional[int]
    prices: List[Price]
    available: bool
    simulated: Optional[bool]
    """True when the model answers with simulated output (development servers only)."""
    price_note: Optional[str]
    estimate_micro: Optional[int]
    input_schema: Optional[Dict[str, Any]]
    """JSON Schema of ``input``. Only on ``models.get()``."""
    created: int
    owned_by: str


class OutputFile(TovanoObject):
    """A file a model produced. ``url`` is signed and expires."""

    url: str
    file_id: str
    content_type: str
    size: int


class TaskError(TovanoObject):
    code: str
    message: str


class TaskUsage(TovanoObject):
    input_tokens: int
    output_tokens: int


class Task(TovanoObject):
    """A model run: status, output, units and charge."""

    id: str
    object: str
    model: str
    status: TaskStatus
    customer: Optional[str]
    """Your own end-customer id, if you sent one."""
    project_id: str
    parent_id: Optional[str]
    step: Optional[str]
    name: Optional[str]
    input: Dict[str, Any]
    output: Optional[TovanoObject]
    """Set when the task completed, e.g. ``output.images[0].url``."""
    unit: str
    units: float
    usage: Optional[TaskUsage]
    estimate_micro: int
    charged_micro: int
    """What the task cost. Always 0 for a failed task."""
    error: Optional[TaskError]
    source: str
    api_key_prefix: Optional[str]
    duration_ms: Optional[int]
    steps: Optional[List["Task"]]
    """Agent jobs: one task per step, each with its own charge."""
    created_at: str
    started_at: Optional[str]
    completed_at: Optional[str]


Task._fields = {"error": TaskError, "usage": TaskUsage, "steps": [Task]}


class Balance(TovanoObject):
    object: str
    balance_micro: int
    reserved_micro: int
    """Held for tasks that are still running."""
    available_micro: int
    """What you can spend now."""
    spent_this_month_micro: int
    spend_limit_micro: Optional[int]
    low_balance_alert_micro: Optional[int]
    currency: str


class UsageRow(TovanoObject):
    key: Optional[str]
    """The model id, customer tag, day, project id or key prefix."""
    type: Optional[str]
    tasks: int
    failed: int
    unit: Optional[str]
    units: Optional[float]
    charged_micro: int
    input_tokens: int
    output_tokens: int


class UsageTotal(TovanoObject):
    tasks: int
    failed: int
    charged_micro: int
    input_tokens: int
    output_tokens: int


class Usage(TovanoObject):
    _fields = {"data": [UsageRow], "total": UsageTotal}
    object: str
    group_by: UsageGroupBy
    to: str
    total_micro: int
    total_tasks: int
    total: UsageTotal
    data: List[UsageRow]

    @property
    def from_(self) -> str:
        """The start of the period (``from`` is a Python keyword)."""
        return str(self["from"])


class Page(TovanoObject, Generic[T]):
    """One page of a list. Iterate it to get the items."""

    object: str
    data: List[T]
    next_cursor: Optional[str]
    """Pass to ``cursor`` for the next page. None on the last page."""
    has_more: bool
    total: int

    def __iter__(self) -> Iterator[T]:  # type: ignore[override]
        return iter(self["data"])

    def __len__(self) -> int:
        return len(self["data"])


class ChatMessage(TovanoObject):
    role: str
    content: Union[str, List[Dict[str, Any]], None]


class ChatUsage(TovanoObject):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatBilling(TovanoObject):
    """Tovano's billing details for a chat call."""

    task_id: str
    charged_micro: int


class ChatCompletionMessage(TovanoObject):
    role: str
    content: str


class ChatCompletionChoice(TovanoObject):
    _fields = {"message": ChatCompletionMessage}
    index: int
    message: ChatCompletionMessage
    finish_reason: str


class ChatCompletion(TovanoObject):
    _fields = {"choices": [ChatCompletionChoice], "usage": ChatUsage, "tovano": ChatBilling}
    id: str
    object: str
    created: int
    model: str
    choices: List[ChatCompletionChoice]
    usage: ChatUsage
    tovano: ChatBilling


class ChatCompletionDelta(TovanoObject):
    role: Optional[str]
    content: Optional[str]


class ChatCompletionChunkChoice(TovanoObject):
    _fields = {"delta": ChatCompletionDelta}
    index: int
    delta: ChatCompletionDelta
    finish_reason: Optional[str]


class ChatCompletionChunk(TovanoObject):
    _fields = {"choices": [ChatCompletionChunkChoice], "usage": ChatUsage, "tovano": ChatBilling}
    id: str
    object: str
    created: int
    model: str
    choices: List[ChatCompletionChunkChoice]
    usage: Optional[ChatUsage]
    """Only on the last chunk, which has no choices."""
    tovano: Optional[ChatBilling]
