"""
Supervisor — Typed event dataclasses.

Each event type has a dataclass with explicit fields.
Serialization via to_dict()/from_dict() for multiprocessing.Queue.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional, Union


@dataclass
class StatusStart:
    type: str = field(default="status_start", init=False)
    task_id: str = ""
    chat_id: int = 0
    original_message_id: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StatusUpdate:
    type: str = field(default="status_update", init=False)
    task_id: str = ""
    text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SendMessage:
    type: str = field(default="send_message", init=False)
    chat_id: int = 0
    text: str = ""
    task_id: str = ""
    reply_to_message_id: int = 0
    log_text: str = ""
    format: str = ""
    is_progress: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LLMUsage:
    type: str = field(default="llm_usage", init=False)
    task_id: str = ""
    ts: str = ""
    category: str = "other"
    model: str = ""
    usage: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TaskHeartbeat:
    type: str = field(default="task_heartbeat", init=False)
    task_id: str = ""
    phase: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TypingStart:
    type: str = field(default="typing_start", init=False)
    chat_id: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TaskDone:
    type: str = field(default="task_done", init=False)
    task_id: str = ""
    task_type: str = ""
    worker_id: int = -1
    cost_usd: float = 0.0
    total_rounds: int = 0
    ts: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TaskMetrics:
    type: str = field(default="task_metrics", init=False)
    task_id: str = ""
    task_type: str = ""
    duration_sec: float = 0.0
    tool_calls: int = 0
    tool_errors: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ReviewRequest:
    type: str = field(default="review_request", init=False)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RestartRequest:
    type: str = field(default="restart_request", init=False)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PromoteToStable:
    type: str = field(default="promote_to_stable", init=False)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ScheduleTask:
    type: str = field(default="schedule_task", init=False)
    description: str = ""
    context: str = ""
    depth: int = 0
    task_id: str = ""
    parent_task_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CancelTask:
    type: str = field(default="cancel_task", init=False)
    task_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SendPhoto:
    type: str = field(default="send_photo", init=False)
    chat_id: int = 0
    image_base64: str = ""
    caption: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ToggleEvolution:
    type: str = field(default="toggle_evolution", init=False)
    enabled: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ToggleConsciousness:
    type: str = field(default="toggle_consciousness", init=False)
    action: str = "status"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OwnerMessageInjected:
    type: str = field(default="owner_message_injected", init=False)
    task_id: str = ""
    text: str = ""
    ts: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Union of all event types for type hints
Event = Union[
    StatusStart, StatusUpdate, SendMessage, LLMUsage, TaskHeartbeat,
    TypingStart, TaskDone, TaskMetrics, ReviewRequest, RestartRequest,
    PromoteToStable, ScheduleTask, CancelTask, SendPhoto,
    ToggleEvolution, ToggleConsciousness, OwnerMessageInjected,
]

# Registry for from_dict() deserialization
_EVENT_CLASSES: Dict[str, type] = {
    "status_start": StatusStart,
    "status_update": StatusUpdate,
    "send_message": SendMessage,
    "llm_usage": LLMUsage,
    "task_heartbeat": TaskHeartbeat,
    "typing_start": TypingStart,
    "task_done": TaskDone,
    "task_metrics": TaskMetrics,
    "review_request": ReviewRequest,
    "restart_request": RestartRequest,
    "promote_to_stable": PromoteToStable,
    "schedule_task": ScheduleTask,
    "cancel_task": CancelTask,
    "send_photo": SendPhoto,
    "toggle_evolution": ToggleEvolution,
    "toggle_consciousness": ToggleConsciousness,
    "owner_message_injected": OwnerMessageInjected,
}


def from_dict(d: Dict[str, Any]) -> Optional[Event]:
    """Deserialize a dict back to a typed event.

    Returns None if the event type is unknown (allows gradual migration).
    Extra keys in the dict are silently ignored for forward compatibility.
    """
    event_type = str(d.get("type") or "")
    cls = _EVENT_CLASSES.get(event_type)
    if cls is None:
        return None
    # Only pass keys that match the dataclass fields
    import dataclasses
    field_names = {f.name for f in dataclasses.fields(cls)}
    filtered = {k: v for k, v in d.items() if k in field_names and k != "type"}
    return cls(**filtered)
