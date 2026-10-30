"""Immutable model-call DTOs for the LLM adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...errors.llm import LLMError


@dataclass(frozen=True)
class LLMUsage:
    tokens_in: int | None = None
    tokens_out: int | None = None
    total_tokens: int | None = None
    latency_ms: int = 0
    calls: int = 0


@dataclass(frozen=True)
class LLMResult:
    data: Any = field(default=None, repr=False)
    usage: LLMUsage = field(default_factory=LLMUsage)
    error: LLMError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None
