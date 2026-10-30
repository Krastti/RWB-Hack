"""JSON-only state for the analysis part of the pipeline (C-02/C-03)."""

from typing import Any, NotRequired, Protocol, TypedDict


class GraphError(ValueError):
    """A safe diagnostic code, without model responses or business input."""


class AnalysisNode(Protocol):
    def __call__(self, request: dict, *, attempt_id: str) -> dict:
        """Return C-02 AgentResult; use attempt_id for side-effect deduplication."""
        ...


class ArtifactStore(Protocol):
    def read(self, run_id: str, artifact_id: str) -> dict:
        """Resolve an owned immutable artifact, including its verified content."""
        ...

    def put(self, run_id: str, artifact: dict) -> None:
        """Durably save an artifact; same UUID/same bytes is an idempotent no-op."""
        ...

    def result(self, run_id: str, attempt_id: str) -> dict | None:
        """Return the durable accepted result receipt, or None if absent."""
        ...

    def accept(self, run_id: str, attempt_id: str, result: dict) -> None:
        """Atomically save outputs and an immutable result receipt, or replay it.

        Verify ownership and reject UUID reuse with different bytes. A failed
        operation must not expose a partial receipt. This is the artifact/bridge
        adapter's responsibility, separate from the graph checkpoint transaction.
        """
        ...


class AnalysisState(TypedDict):
    state_version: int
    run_id: str
    stage_id: str
    contract_version: str
    profile_id: str
    initial_request: dict
    initial_attempt_id: str
    request: dict
    attempt_id: str
    attempt_started_at: str | None
    input_hash: str
    input_hash_algorithm: NotRequired[str]
    status: str
    stage_status: str
    questions_revision: int
    questions: list[dict]
    attempts: list[dict]
    artifact_refs: list[dict]
    resume_command: dict[str, Any] | None
