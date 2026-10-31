"""Validated public interpreter API."""
from ..model import validate_model
from .runtime import INTERPRETER_VERSION, invariants, step_validated


def step(model, state, actor, action):
    validate_model(model)
    return step_validated(model,state,actor,action)


__all__ = ['step','invariants','INTERPRETER_VERSION']
