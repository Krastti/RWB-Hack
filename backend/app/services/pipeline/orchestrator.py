"""Production AnalysisGraph → A-09 under existing commands, lifecycle and fencing."""
from copy import deepcopy
from hashlib import sha256
import asyncio
import json
from uuid import UUID, uuid4, uuid5
from sqlalchemy import select
from contracts.agents.validate import Contracts
from ...models import Run, Project
from ...agents.profiles import ProfileLoader
from ...agents.context import ContextBuilder
from ...agents.graph import AnalysisGraph
from ...agents.graph.architecture import ArchitectureStage, RuntimeNodes, prepare_architecture
from ...agents.graph.architecture.models import NODES, OUTPUTS
from ..artifacts import PostgreSQLArtifactStore
from ..artifacts.budget import PostgreSQLBudgetLedger
from ..artifacts.models import ModelCall
from ..run_control.models import AttemptState, now
from ..lifecycle.models import LifecycleError
from backend.app.services.pipeline.accounting import AccountedCalls, active_accounting
from .analysis import AnalysisNode, make_artifact, canonical
from .checkpoints import PostgreSQLCheckpoints


def pipeline_guard(record, change):
    if record.context['profile_id'] not in {'web-fastapi-angular', 'russtech-fullstack'}:
        raise LifecycleError(409, 'UNSUPPORTED_RUN_PROFILE', 'Unsupported execution profile')
    if change.kind == 'register_attempt':
        if not any(a['attempt_id'] == str(change.attempt_id) and a['input_state'] for a in record.data['attempts']):
            raise LifecycleError(409, 'RESUME_STATE_UNAVAILABLE', 'Frozen input required')
    if change.kind == 'attempt' and change.target in {'succeeded', 'waiting_for_input'}:
        source = next(a for a in record.data['attempts'] if a['attempt_id'] == str(change.attempt_id))
        result = source.get('accepted_result')
        checkpoint = record.data['saved_state'].get('pipeline_confirmed', {}).get(str(change.attempt_id))
        if not result or not checkpoint or result['status'] != change.target:
            raise LifecycleError(409, 'CHECKPOINT_REQUIRED', 'Accepted result and checkpoint required')
        Contracts().validate('agent-result', result)
    if change.kind == 'run' and change.target == 'completed':
        raise LifecycleError(409, 'DOMAIN_GUARD_REQUIRED', 'Generation and assembly remain pending')
    if change.kind == 'run' and change.target == 'waiting_for_input':
        if not any(q['required'] and not q.get('answer') for q in record.data['run']['questions']):
            raise LifecycleError(409, 'RUN_STATE_CONFLICT', 'Required questions missing')


class PipelineOrchestrator:
    def __init__(self, runtime, config, *, transports=None):
        self.runtime, self.config = runtime, config
        self.transports = dict(transports or {})

    def __call__(self, command, execution):
        repo = self.runtime.repository
        with execution.transaction() as s:
            run = s.get(Run, command.run_id)
            frozen = repo.frozen_input(s, run)
            record, _ = repo.load(s, run)
            source = next(a for a in record.attempts if a.attempt_id == command.attempt_id)
            owner = record.owner_id
        snapshot = frozen['profile_snapshot']
        profile = ProfileLoader().restore(snapshot, expected_checksum=snapshot['checksum'],
                                          expected_rules_checksum=snapshot['rules_checksum'])
        store = PostgreSQLArtifactStore(repo.sessions, owner_id=owner, execution=execution, profile=profile,
                                        owner_agent=command.stage_id, conditions={'has_integrations': True, 'has_android': False})
        ledger = PostgreSQLBudgetLedger(store)
        limits = profile.data['limits']['agent']
        for key in ('max_calls', 'max_output_tokens'):
            limits[key] = min(limits[key], frozen['limits'][key])
        calls = AccountedCalls(ledger, str(command.run_id), str(command.attempt_id), limits)
        token = active_accounting.set(calls)
        try:
            if command.stage_id == 'analysis':
                graph = AnalysisGraph(AnalysisNode(store, self.config, transport=self.transports.get('analysis')),
                                      store, PostgreSQLCheckpoints(store))
                if command.attempt_no > 1 and (source.input_state.get('schema_version') != 'clarification-input/1'
                        or graph.snapshot(str(command.run_id))['stage_status'] == 'failed'):
                    old = graph.snapshot(str(command.run_id))
                    # A pre-result crash may leave LangGraph running after C-03 has
                    # durably failed that attempt. Only that terminal lifecycle
                    # record authorizes replacing its pending graph task.
                    previous_failed = any(a.stage_id == 'analysis' and a.status == 'failed'
                        and a.attempt_no == old['request']['attempt'] for a in record.run.stages)
                    state = graph.retry(str(command.run_id), attempt_id=str(command.attempt_id),
                                        attempt_no=command.attempt_no, previous_attempt_failed=previous_failed)
                elif source.input_state.get('schema_version') == 'clarification-input/1':
                    state = graph.resume(str(command.run_id), questions_revision=source.input_state['questions_revision'],
                        answers=source.input_state['answers'], attempt_id=str(command.attempt_id))
                else:
                    business = dict(schema_version='1.0', requirements_text=frozen['requirements_text'],
                        questions_revision=0, answers=[], previous_requirements_artifact_id=None,
                        analysis_rules_artifact_id=None)
                    artifact = make_artifact(str(command.attempt_id), 'inputs/analysis.json', 'business_requirements',
                                             canonical(business), 'orchestrator', command.attempt_no)
                    store.put(str(command.run_id), artifact)
                    request = self.request(command, profile, limits, [artifact['artifact_id']])
                    state = graph.start(request, attempt_id=str(command.attempt_id))
                result = store.result(str(command.run_id), str(command.attempt_id))
                if result is None:
                    raise RuntimeError('ANALYSIS_RESULT_NOT_ACCEPTED')
                checkpoint = graph.snapshot(str(command.run_id))
                if checkpoint['attempt_id'] != str(command.attempt_id) or checkpoint['stage_status'] != result['status']:
                    raise RuntimeError('ANALYSIS_CHECKPOINT_NOT_CONFIRMED')
            elif command.stage_id == 'architecture':
                result = store.result(str(command.run_id), str(command.attempt_id))
                if result is None:
                    inputs = source.input_state
                    requirements = store.read(str(command.run_id), inputs['requirements_id'])
                    request = self.request(command, profile, limits, [requirements['artifact_id']])
                    from ...agents.graph.architecture import ArchitectureExecutionError
                    try:
                        # A-30 verifies owner, exact accepted bytes and frozen policies.
                        ContextBuilder(store, max_context_bytes=1048576).prepare(request, owner_id=str(owner),
                            owner_agent='architecture', profile=profile,
                            references={requirements['artifact_id']: {k:v for k,v in requirements.items() if k != 'content'}},
                            execution_inputs={'prompt': b'Produce the approved architecture bundle.', 'template': b'',
                                'model_configuration': canonical({k:v for k,v in vars(self.config).items() if k != 'api_key'}).encode(),
                                'compiler_configuration': canonical(profile.data['stack']).encode()})
                        policies = deepcopy(frozen['execution_policies'])
                        if set(policies) != {'api_policy', 'naming_policy'}:
                            raise ValueError('ARCHITECTURE_POLICIES_REQUIRED')
                        for policy in policies.values():
                            if policy.get('requirements_checksum') is None:
                                policy['requirements_checksum'] = requirements['checksum']
                        # Explicit trusted policy placeholders bind exact accepted
                        # wording without mutating the frozen policy snapshot.
                        accepted_requirements = json.loads(requirements['content'])
                        for operation in policies['api_policy']['operations']:
                            for index in operation['security_requirement_indices']:
                                if type(index) is not int or not 0 <= index < len(accepted_requirements['security_requirements']):
                                    raise ValueError('ARCHITECTURE_POLICY_SECURITY_BINDING_INVALID')
                                marker = '{{security_requirement:' + str(index) + '}}'
                                if marker in operation['authorization_rules']:
                                    text = accepted_requirements['security_requirements'][index]['text']
                                    operation['authorization_rules'] = [text if rule == marker else rule
                                        for rule in operation['authorization_rules']]
                        prepared = prepare_architecture(request, requirements,
                            profile_bytes=profile._raw, profile_checksum=profile.checksum,
                            rules_text=profile.rules_text, rules_checksum=profile.metadata['rules_checksum'],
                            api_policy=policies['api_policy'], naming_policy=policies['naming_policy'])
                        outcome = asyncio.run(ArchitectureStage(RuntimeNodes(self.config, transports=self.transports)).run(
                            prepared, reservation=inputs['reservation']))
                        result = outcome.result
                    except (ValueError, ArchitectureExecutionError) as error:
                        import re
                        code = str(error)
                        if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', code):
                            code = 'ARCHITECTURE_INPUT_REJECTED'
                        result = {k: request[k] for k in ('schema_version', 'run_id', 'stage_id', 'attempt', 'contract_version')}
                        result.update(status='failed', output_artifacts=[], questions=[], assumptions=[],
                            usage=ledger.usage(run_id=str(command.run_id), attempt_id=str(command.attempt_id)),
                            diagnostics=[dict(code=code, message='Architecture could not accept the configured inputs or model response',
                                severity='error', retryable=False, related_fr_ids=[])])
                    store.accept(str(command.run_id), str(command.attempt_id), result)
                # A-09 is ephemeral; its accepted bundle is its durable replay boundary.
                checkpoint = {'stage_status': result['status'], 'artifact_ids': [a['artifact_id'] for a in result['output_artifacts']]}
            else:
                raise RuntimeError('PIPELINE_STAGE_NOT_REGISTERED')
        finally:
            active_accounting.reset(token)
        with execution.transaction() as s:
            store._save_receipt(s, str(command.run_id), str(command.attempt_id), 'checkpoint',
                {'result_checksum': sha256(canonical(result).encode()).hexdigest()})
            run = s.get(Run, command.run_id)
            control = repo.control_in(s, run)
            def boundary(record):
                saved = deepcopy(record.saved_state)
                saved.setdefault('pipeline_confirmed', {})[str(command.attempt_id)] = checkpoint
                # Totals are derived from unique physical rows, not stage aggregates.
                rows = list(s.scalars(select(ModelCall).where(ModelCall.run_id == run.id)))
                record.run.metrics.model_calls = len(rows)
                for field, target in [('input_tokens', 'tokens_in'), ('output_tokens', 'tokens_out')]:
                    values = [(r.payload.get('usage') or {}).get(field) for r in rows]
                    record.tokens_complete[target] = all(v is not None for v in values)
                    setattr(record.run.metrics, target, sum(v for v in values if v is not None))
                record.run.metrics.attempts = len(record.attempts)
                record.run.metrics.duration_ms = max(0, int((now() - record.run.created_at).total_seconds() * 1000))
                budget_reason = next((d['code'][7:] for d in result['diagnostics']
                                      if d['code'].startswith('BUDGET_')), None)
                if budget_reason:
                    record.run.stop_reason = budget_reason
                if command.stage_id == 'analysis':
                    from ..run_control.models import Question
                    record.run.questions = [Question.model_validate(q) for q in result['questions']]
                    req = next((a for a in result['output_artifacts'] if a['kind'] == 'requirements'), None)
                    if req:
                        record.run.questions_revision = json.loads(req['content'])['questions_revision']
                return saved
            control.finish_attempt(owner_id=owner, run_id=run.id, stage_id=command.stage_id,
                attempt_id=command.attempt_id, accepted_result=result, save_boundary=boundary)
            with self.runtime.states.transaction(s, run) as tx:
                if tx.snapshot.status == 'cancelling':
                    return
                if result['status'] in {'failed', 'cancelled'}:
                    budget_reason = next((d['code'][7:] for d in result['diagnostics']
                                          if d['code'].startswith('BUDGET_')), None)
                    tx.transition_run(expected='running', target='budget_exceeded' if budget_reason else 'failed',
                                      reason_code=budget_reason or 'PIPELINE_STAGE_FAILED')
                elif result['status'] == 'waiting_for_input':
                    tx.transition_run(expected='running', target='waiting_for_input', reason_code='ANALYSIS_QUESTIONS_REQUIRED')
            if command.stage_id == 'analysis' and result['status'] == 'succeeded' and run.status == 'running':
                self.register_architecture(s, run, result)

    @staticmethod
    def request(command, profile, limits, ids):
        return dict(schema_version='1.0', run_id=str(command.run_id), stage_id=command.stage_id,
            attempt=command.attempt_no, contract_version=command.contract_version, profile_id=profile.profile_id,
            input_artifact_ids=ids, limits=limits)

    def register_architecture(self, session, run, result):
        frozen = self.runtime.repository.frozen_input(session, run)
        digest = sha256(canonical({'analysis_result': result, 'frozen_inputs': frozen}).encode()).hexdigest()
        source = AttemptState(stage_id='architecture', attempt_no=1, input_hash=digest,
            input_state={'requirements_id': next(a['artifact_id'] for a in result['output_artifacts'] if a['kind'] == 'requirements'),
                'reservation': {'attempt_id': str(uuid4()),
                    'artifacts': {k: {'artifact_id': str(uuid4()), 'version': 1} for k in OUTPUTS},
                    'call_ids': {k: str(uuid4()) for k in NODES}}})
        source.input_state['reservation']['attempt_id'] = str(source.attempt_id)
        source.input_state['reservation']['call_ids'] = {node: str(uuid5(source.attempt_id, 'physical-call:' + str(i))) for i, node in enumerate(NODES)}
        with self.runtime.states.transaction(session, run) as tx:
            if any(a.stage_id == 'architecture' for a in tx.snapshot.attempts):
                return
            tx.update_data(lambda data: data['attempts'].append(source.model_dump(mode='json')))
            tx.register_attempt(stage_id='architecture', contract_version=result['contract_version'], input_hash=source.input_hash,
                                attempt_id=source.attempt_id, reason_code='ANALYSIS_ACCEPTED')
        self.runtime.queue.enqueue(session, source.attempt_id)
