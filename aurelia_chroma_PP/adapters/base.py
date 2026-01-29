"""Input adapters normalize events into a shared schema."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

from adapters.schemas import Event


class InputAdapter(ABC):
    @abstractmethod
    def poll(self) -> Iterable[Event]:
        ...
