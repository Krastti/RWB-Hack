from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LLMUsage:
    tokens_in: int | None = None
    tokens_out: int | None = None
    total_tokens: int | None = None
    latency_ms: int = 0
    calls: int = 0
