"""Provider-neutral results and a Chat Completions transport for A-01."""

from .adapter import LLMAdapter, LLMConfig, LLMError, LLMResult, LLMUsage

__all__ = ["LLMAdapter", "LLMConfig", "LLMError", "LLMResult", "LLMUsage"]
