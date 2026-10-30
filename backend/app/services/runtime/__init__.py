"""B-11: real PostgreSQL HTTP/queue/lifecycle composition."""
from dataclasses import dataclass

from ...adapters.queue.contracts import QueueConfig
from ...workers.intake import RunIntakeDispatcher
from ..run_control import RunControlService
from ..run_control.models import ControlError, Limits
from .lifecycle import RunStateService, execution_guard
from .queue import RuntimeQueueStore
from .repository import PostgreSQLRuntimeRepository


@dataclass(frozen=True)
class RunRuntime:
    repository: PostgreSQLRuntimeRepository
    states: RunStateService
    queue: RuntimeQueueStore
    control: RunControlService
    intake: RunIntakeDispatcher


def compose_runtime(sessions, *, retry_limits: Limits, queue_config: QueueConfig | None = None,
                    guard=execution_guard, checkpoint_confirmed=None) -> RunRuntime:
    """One place defines SQL stores, transition policy and retry budget ceiling."""
    ceiling = retry_limits.model_copy(deep=True)
    queue = RuntimeQueueStore(sessions, queue_config or QueueConfig())
    repository = PostgreSQLRuntimeRepository(sessions, queue)
    repository.guard = guard
    states = RunStateService(repository, guard=guard, checkpoint_confirmed=checkpoint_confirmed)
    queue.states = states

    def authorize_retry(record, limits):
        if limits.max_calls > ceiling.max_calls or limits.max_output_tokens > ceiling.max_output_tokens:
            raise ControlError(409, "RUN_STATE_CONFLICT", "Retry limits exceed server policy")

    control = RunControlService(repository, authorize_retry=authorize_retry)
    intake = RunIntakeDispatcher(sessions, repository, states, queue)
    return RunRuntime(repository, states, queue, control, intake)


def compose_worker(runtime: RunRuntime, *, broker_url: str, orchestrator, recovery):
    """Requires actual AG orchestration/recovery; never installs a mock fallback."""
    from ...adapters.queue.celery import create_worker_app
    return create_worker_app(
        broker_url=broker_url, sessions=runtime.repository.sessions,
        orchestrator=orchestrator, recovery=recovery, config=runtime.queue.config,
        store=runtime.queue, before_dispatch=runtime.intake.dispatch,
    )
