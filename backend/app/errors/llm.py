from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LLMError:
    code: str
    message: str
    retryable: bool = False
    http_status: int | None = None
