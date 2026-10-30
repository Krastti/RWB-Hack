"""Offline A-22 demonstration with actual A-01 and explicit manual HTTP fixtures."""
import argparse
import ast
import asyncio
from copy import deepcopy
import json
from uuid import uuid4

import httpx

from . import BatchExecutor, FileSpec, GenerationError
from ..context import ContextBuilder
from ..context.example import fixture_inputs, FixtureResolver, OWNER
from ..profiles import ProfileLoader
from ..llm import LLMConfig


class ExampleStore:
    """In-memory example only; production must implement atomic durable/fenced writes."""
    def __init__(self):
        self.fingerprints, self.receipts, self.files = {}, {}, {}

    def open_attempt(self, *, owner_id, run_id, attempt_id, fingerprint):
        key = owner_id, run_id, attempt_id
        if key in self.fingerprints and self.fingerprints[key] != fingerprint:
            raise GenerationError("GENERATION_ATTEMPT_CONFLICT")
        self.fingerprints[key] = fingerprint

    def receipt(self, *, owner_id, run_id, attempt_id, batch_id):
        return deepcopy(self.receipts.get((owner_id, run_id, attempt_id, batch_id)))

    def accept(self, *, owner_id, run_id, attempt_id, batch_id, receipt):
        key = owner_id, run_id, attempt_id, batch_id
        if key in self.receipts and self.receipts[key] != receipt:
            raise GenerationError("GENERATION_RECEIPT_CONFLICT")
        staged = deepcopy(self.files)
        for artifact in receipt["artifacts"]:
            file_key = run_id, artifact["artifact_id"]
            if file_key in staged and staged[file_key] != artifact:
                raise GenerationError("GENERATION_FILE_CONFLICT")
            staged[file_key] = deepcopy(artifact)
        self.files = staged
        self.receipts[key] = deepcopy(receipt)


class ExampleLedger:
    """Finite local example ledger, never selected by BatchExecutor automatically."""
    def __init__(self):
        self.calls, self.reservations = {}, {}

    def reserve(self, **reservation):
        scope = reservation['run_id'], reservation['attempt_id']
        if len(self.calls.get(scope, {})) >= reservation['agent_limits']['max_calls']:
            raise GenerationError('BUDGET_EXCEEDED')
        self.reservations[reservation['call_id']] = reservation

    def finish(self, *, run_id, attempt_id, call_id, result):
        reservation = self.reservations[call_id]
        if result.usage.calls:
            self.calls.setdefault((run_id, attempt_id), {})[call_id] = {
                'call_id': call_id, 'model': reservation['model'],
                'outcome': 'succeeded' if result.ok else 'failed',
                'input_tokens': result.usage.tokens_in, 'output_tokens': result.usage.tokens_out,
                'duration_seconds': result.usage.latency_ms / 1000,
            }

    def abandon(self, *, run_id, attempt_id, call_id):
        self.calls.setdefault((run_id, attempt_id), {})[call_id] = {
            'call_id': call_id, 'model': self.reservations[call_id]['model'], 'outcome': 'cancelled',
            'input_tokens': None, 'output_tokens': None, 'duration_seconds': 0,
        }

    def usage(self, *, run_id, attempt_id):
        calls = list(self.calls.get((run_id, attempt_id), {}).values())
        def total(key):
            return None if any(c[key] is None for c in calls) else sum(c[key] for c in calls)
        return {'calls': len(calls), 'model_calls': deepcopy(calls),
                'input_tokens': total('input_tokens'), 'output_tokens': total('output_tokens'),
                'duration_seconds': sum(c['duration_seconds'] for c in calls)}


async def example(*, fail_second=False):
    fixtures = fixture_inputs()
    artifacts = [fixtures[k] for k in ('requirements', 'openapi', 'data_model')]
    config = LLMConfig(base_url='https://example.invalid/v1', model='manual-fixture', api_key='example-only',
                       max_output_tokens=2048, timeout_seconds=30)
    profile = ProfileLoader().load('web-fastapi-angular')
    request = {'schema_version': '1.0', 'run_id': artifacts[0].run_id, 'stage_id': 'backend-services',
               'attempt': 1, 'contract_version': '1', 'profile_id': profile.profile_id,
               'input_artifact_ids': [a.metadata['artifact_id'] for a in artifacts],
               'limits': {'max_calls': 4, 'max_output_tokens': 3000, 'timeout_seconds': 60}}
    execution = {'prompt': b'Generate the requested Python service files.', 'template': b'',
                 'model_configuration': json.dumps({k: v for k, v in vars(config).items() if k != 'api_key'}).encode(),
                 'compiler_configuration': b'{"language":"python"}'}
    context = ContextBuilder(FixtureResolver(artifacts), max_context_bytes=262144).prepare(
        request, owner_id=OWNER, owner_agent='backend', profile=profile,
        references={a.metadata['artifact_id']: a.metadata for a in artifacts}, execution_inputs=execution)
    sources = {'backend/app/services/ownership.py': 'def is_owner(actor_id, owner_id):\n    return actor_id == owner_id\n',
               'backend/app/services/description.py': 'def has_description(value):\n    return isinstance(value, str) and bool(value.strip())\n'}
    files = tuple(FileSpec(path, 'source', 'text/x-python', 1, 512) for path in sources)
    plan = {'schema_version': '1.0', 'contract_version': '1', 'profile_id': profile.profile_id,
            'tasks': [{'task_id': 'services', 'stage_id': request['stage_id'], 'owner_agent': 'backend',
                       'depends_on': [], 'input_artifact_ids': request['input_artifact_ids'],
                       'output_paths': list(sources), 'output_directories': [], 'related_fr_ids': ['FR-001']}]}
    seen = []
    def handler(req):
        sent = json.loads(req.content)
        data = json.loads(sent['messages'][1]['content'])
        seen.append(data)
        if len(seen) == 2:
            assert data['accepted_files'][0]['content'] == next(iter(sources.values()))
        paths = data['batch_paths']
        return httpx.Response(200, json={'choices': [{'finish_reason': 'length' if fail_second and len(seen) == 2 else 'stop',
            'message': {'content': json.dumps({'files': [{'path': p, 'content': sources[p], 'complete': True} for p in paths]})}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 40, 'total_tokens': 140}})
    def validate(spec, content, prepared, accepted):
        ast.parse(content, filename=spec.path)
        if content != sources[spec.path]:
            raise GenerationError('EXAMPLE_SUBJECT_MISMATCH')
    store, ledger = ExampleStore(), ExampleLedger()
    executor = BatchExecutor(config=config, store=store, ledger=ledger, max_files_per_batch=1,
                             max_request_bytes=262144, validate_file=validate, transport=httpx.MockTransport(handler))
    args = dict(request=request, owner_id=OWNER, attempt_id=str(uuid4()), task_id='services',
                plan=plan, profile=profile, context=context, files=files)
    first = await executor.run(**args)
    assert first.result['status'] == ('failed' if fail_second else 'succeeded')
    assert len(store.files) == (1 if fail_second else 2)
    assert first.result['usage']['calls'] == 2
    if not fail_second:
        replay = await executor.run(**args)
        assert replay.result == first.result and len(seen) == 2
    return first


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fail-second', action='store_true')
    args = parser.parse_args()
    outcome = asyncio.run(example(fail_second=args.fail_second))
    print(json.dumps({'result': outcome.result, 'remaining_paths': outcome.remaining_paths}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
