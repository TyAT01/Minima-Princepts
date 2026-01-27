"""Shared event schemas for all input adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


@dataclass(slots=True)
class Event:
    source: str
    user_id: str
    username: str
    text: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OutputMessage:
    text: str
    intent: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
