"""Optional execution context; standalone A-01 has no database/graph dependency."""
from contextvars import ContextVar

active_accounting = ContextVar('pipeline_model_accounting', default=None)
