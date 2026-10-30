from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..error import LLMError
from .llm_usage import LLMUsage


@dataclass(frozen=True)
class LLMResult:
    data: Any = field(default=None, repr=False)
    usage: LLMUsage = field(default_factory=LLMUsage)
    error: LLMError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None
