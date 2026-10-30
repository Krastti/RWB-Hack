"""Application errors."""

from .projects import InvalidProjectInput, ProjectNotFound

from .llm import LLMError

from .auth import Unauthorized

__all__ = ['LLMError', "InvalidProjectInput", "ProjectNotFound", "Unauthorized"]
