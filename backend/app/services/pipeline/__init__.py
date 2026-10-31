"""One production domain composition over the existing runtime."""


def compose_pipeline(runtime, config, *, broker_url, transports=None):
    from .orchestrator import PipelineOrchestrator, pipeline_guard
    from ..runtime import compose_worker
    runtime.queue.replay_captured_results = True
    runtime.repository.guard = runtime.states.guard = pipeline_guard
    from ..runtime.recovery import RuntimeRecovery
    from ...models import StageAttempt
    from ..artifacts.models import ExecutionReceipt
    orchestrator = PipelineOrchestrator(runtime, config, transports=transports)
    technical_recovery = RuntimeRecovery(runtime.states, runtime.queue)
    def recover(command, execution):
        with execution.transaction() as session:
            attempt = session.get(StageAttempt, command.attempt_id)
            status = attempt.status
            confirmed = session.get(ExecutionReceipt, (attempt.id, 'checkpoint')) is not None
        if status == 'running':
            orchestrator(command, execution)
            with execution.transaction(recovery_complete=True):
                pass
        elif confirmed:
            with execution.transaction(recovery_complete=True):
                pass
        else:
            technical_recovery(command, execution)
    return compose_worker(runtime, broker_url=broker_url, orchestrator=orchestrator, recovery=recover)
