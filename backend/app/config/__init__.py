"""Application configuration."""

from pathlib import Path

from dotenv import load_dotenv

# Shared repository configuration; deployment environment takes precedence.
load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)

from .llm import LLMConfig

from .auth import AuthConfig

__all__ = ["LLMConfig", "AuthConfig"]
