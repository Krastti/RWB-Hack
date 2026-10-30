"""Persistence models for platform contract C-01 v2.0.0."""

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runs import Run, StageAttempt, RunEvent, IdempotencyRecord
    from .base import Base
    from .user import User
    from .project import Project
    from .requirement_revision import RequirementRevision

__all__ = ["Base", "User", "Project", "RequirementRevision", "Run", "StageAttempt", "RunEvent", "IdempotencyRecord"]


def __getattr__(name: str):
    # Importing models.dto must not require the persistence dependencies.
    modules = {
        "Base": "base", "User": "user", "Project": "project",
        "RequirementRevision": "requirement_revision",
        "Run": "runs", "StageAttempt": "runs", "RunEvent": "runs",
        "IdempotencyRecord": "runs",
    }
    if name not in modules:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{modules[name]}", __name__), name)
    globals()[name] = value
    return value
