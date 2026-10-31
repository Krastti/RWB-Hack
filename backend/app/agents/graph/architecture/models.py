"""Trusted ports and immutable inputs for the A-09 composition."""

from dataclasses import dataclass, field
import json
from typing import Protocol

NODES = ("proposal", "openapi", "data_model", "ui_spec")
OUTPUTS = {
    "architecture": "architecture.json", "openapi": "contracts/openapi.yaml",
    "data_model": "data-model.json", "ui_spec": "ui-spec.json",
    "generation_plan": "generation-plan.json",
}


class ArchitectureError(ValueError):
    """A stable code and a safe diagnostic addressed to the architect."""

    def __init__(self, code, message="Архитектор: архитектурный этап не зафиксирован."):
        super().__init__(code)
        self.message = message


def require(condition, code, message="Архитектор: архитектурный этап не зафиксирован."):
    if not condition:
        raise ArchitectureError(code, message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class PreparedArchitecture:
    inputs_json: str = field(repr=False)


@dataclass(frozen=True)
class NodeRequest:
    context: dict = field(repr=False)
    requirements_artifact: dict = field(repr=False)
    profile_content: str = field(repr=False)
    profile_checksum: str
    rules_text: str = field(repr=False)
    rules_checksum: str
    api_policy: dict = field(repr=False)
    naming_policy: dict = field(repr=False)
    attempt_id: str
    artifact_id: str | None
    artifact_version: int
    call_id: str
    openapi_artifact: dict | None = field(default=None, repr=False)


@dataclass(frozen=True)
class NodeReply:
    payload: dict | None = field(repr=False)
    usage: dict = field(repr=False)
    error_code: str | None = None
    retryable: bool = False


class ArchitectureNodes(Protocol):
    async def __call__(self, node: str, request: NodeRequest) -> NodeReply:
        """Execute the statically selected node once; never hide usage or retry."""
        ...


@dataclass(frozen=True)
class ArchitectureOutcome:
    result: dict = field(repr=False)
    project_tree: list[dict] = field(repr=False)

    @property
    def ready_for_generation(self):
        return self.result["status"] == "succeeded"
