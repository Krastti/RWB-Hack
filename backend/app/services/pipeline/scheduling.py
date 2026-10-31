"""Sequential DAG dispatch under the same Run lock as checkpoint confirmation."""
import json
from copy import deepcopy
from hashlib import sha256
from sqlalchemy import select
from ...models import StageAttempt
from ..artifacts.models import ExecutionReceipt
from ..artifacts.store import metadata
from ..run_control.models import AttemptState, now
from .analysis import canonical


def confirmed(session, run_id):
    latest = {}
    for attempt in session.scalars(select(StageAttempt).where(StageAttempt.run_id == run_id)):
        if attempt.stage_id not in latest or latest[attempt.stage_id].attempt_no < attempt.attempt_no:
            latest[attempt.stage_id] = attempt
    outputs = {}
    for attempt in latest.values():
        if attempt.status != 'succeeded':
            continue
        result = session.get(ExecutionReceipt, (attempt.id, 'result'))
        checkpoint = session.get(ExecutionReceipt, (attempt.id, 'checkpoint'))
        if (not result or not checkpoint or result.payload['status'] != 'succeeded' or
                checkpoint.payload.get('result_checksum') != sha256(canonical(result.payload).encode()).hexdigest()):
            raise ValueError('SCHEDULER_CHECKPOINT_REQUIRED')
        for artifact in result.payload['output_artifacts']:
            if artifact['author_agent'] == 'orchestrator':
                continue
            if artifact['path'] in outputs:
                raise ValueError('SCHEDULER_OUTPUT_COLLISION')
            outputs[artifact['path']] = deepcopy(artifact)
    return latest, outputs


def register_next(runtime, session, run, profile, config):
    if profile.profile_id != 'web-fastapi-angular' or run.status != 'running':
        return
    latest, outputs = confirmed(session, run.id)
    if any(a.status in {'pending', 'running'} for a in latest.values()):
        return
    plan = json.loads(outputs['generation-plan.json']['content'])
    architecture = json.loads(outputs['architecture.json']['content'])
    conditions = dict(has_integrations=bool(architecture['integrations']), has_android=False)
    profile.validate_plan(plan, conditions=conditions)
    successful = {name for name, attempt in latest.items() if attempt.status == 'succeeded'}
    for task in plan['tasks']:
        if task['stage_id'] in latest or not set(task['depends_on']) <= successful:
            continue
        # The frozen A-09 references form a closed set. Subject generators also
        # receive accepted scaffold/model interfaces, excluding the large lockfile.
        ids = set(task['input_artifact_ids']) | {outputs['generation-plan.json']['artifact_id']}
        if task['owner_agent'] in {'backend', 'frontend', 'integrations', 'migrations'}:
            ids.update(a['artifact_id'] for a in outputs.values()
                       if a['path'].endswith(('.py', '.ts')) or a['path'] == 'backend/pyproject.toml')
        accepted = sorted((metadata(a) for a in outputs.values()), key=lambda a: a['path'])
        from .generation import implementation_fingerprint
        state = dict(schema_version='generation-task/1', plan=plan, task=deepcopy(task),
                     implementation_fingerprint=implementation_fingerprint(),
                     model_configuration={k:v for k,v in vars(config).items() if k != 'api_key'},
                     conditions=conditions, input_ids=sorted(ids), accepted=accepted,
                     created_at=now().isoformat().replace('+00:00', 'Z'))
        source = AttemptState(stage_id=task['stage_id'], attempt_no=1,
                              input_hash=sha256(canonical(state).encode()).hexdigest(), input_state=state)
        with runtime.states.transaction(session, run) as tx:
            tx.update_data(lambda data: data['attempts'].append(source.model_dump(mode='json')))
            tx.register_attempt(stage_id=source.stage_id, contract_version=plan['contract_version'],
                input_hash=source.input_hash, attempt_id=source.attempt_id, reason_code='DEPENDENCIES_ACCEPTED')
        runtime.queue.enqueue(session, source.attempt_id)
        return
    if not {t['stage_id'] for t in plan['tasks']} <= successful:
        raise ValueError('SCHEDULER_UNRESOLVED_DEPENDENCY')
