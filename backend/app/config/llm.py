from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Literal

import httpx


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    model: str
    api_key: str = field(repr=False)
    timeout_seconds: float = 60.0
    max_output_tokens: int = 2048
    response_format: Literal["json_schema", "json_object"] = "json_schema"

    def __post_init__(self) -> None:
        try:
            url = httpx.URL(self.base_url)
            valid = (
                url.scheme in ("http", "https") and bool(url.host)
                and not url.userinfo and not url.query and not url.fragment
                and bool(self.model.strip()) and bool(self.api_key.strip())
                and self.api_key.isascii() and not any(ord(c) < 33 or ord(c) == 127 for c in self.api_key)
                and math.isfinite(self.timeout_seconds) and self.timeout_seconds > 0
                and type(self.max_output_tokens) is int and self.max_output_tokens > 0
                and self.response_format in ("json_schema", "json_object")
            )
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise ValueError("Invalid LLM configuration") from None
