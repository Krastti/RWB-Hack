import asyncio
import json
import ast
import unittest
from uuid import uuid4
from copy import deepcopy
import httpx
from backend.app.agents.generation.example import (example, fixture_inputs, LLMConfig, ProfileLoader, ContextBuilder, FixtureResolver, OWNER, FileSpec, GenerationError, ExampleStore, ExampleLedger, BatchExecutor)


def fixture(batch_size=1, validator=None):
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
        paths = data['batch_paths']
        return httpx.Response(200, json={'choices': [{'finish_reason': 'length' if False else 'stop',
            'message': {'content': json.dumps({'files': [{'path': p, 'content': sources[p], 'complete': True} for p in paths]})}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 40, 'total_tokens': 140}})
    def validate(spec, content, prepared, accepted):
        ast.parse(content, filename=spec.path)
        if content != sources[spec.path]:
            raise GenerationError('EXAMPLE_SUBJECT_MISMATCH')
    store, ledger = ExampleStore(), ExampleLedger()
    executor = BatchExecutor(config=config, store=store, ledger=ledger, max_files_per_batch=batch_size,
                             max_request_bytes=262144, validate_file=validator or validate, transport=httpx.MockTransport(handler))
    args = dict(request=request, owner_id=OWNER, attempt_id=str(uuid4()), task_id='services',
                plan=plan, profile=profile, context=context, files=files,
                model_configuration=execution["model_configuration"])
    return executor, args, store, ledger, seen

class GenerationRecoveryTests(unittest.TestCase):
    def run_async(self, executor, args):
        return asyncio.run(executor.run(**args))

    def test_successful_replay(self):
        asyncio.run(example())

    def test_recovery_after_second_batch_fails(self):
        outcome = asyncio.run(example(fail_second=True))
        self.assertTrue(outcome.remaining_paths)
        self.assertIn(outcome.remaining_paths[0], outcome.result['diagnostics'][0]['message'])

    def test_batch_validator_sees_all_candidates(self):
        observed = []
        def validate(spec, content, context, files):
            observed.append({f['path'] for f in files})
        executor, args, store, ledger, seen = fixture(2, validate)
        result = self.run_async(executor, args)
        self.assertEqual(result.result['status'], 'succeeded')
        self.assertEqual(observed, [set(s.path for s in args['files'])] * 2)

    def test_corrupt_receipt_and_missing_accounting_rejected(self):
        for corruption in ('content', 'call_id', 'missing_call_id', 'missing_ledger_entry'):
            with self.subTest(corruption=corruption):
                executor, args, store, ledger, seen = fixture()
                self.run_async(executor, args)
                receipt = next(iter(store.receipts.values()))
                if corruption == 'content':
                    receipt['artifacts'][0]['content'] += 'corrupt'
                elif corruption == 'call_id':
                    receipt['call_id'] = str(uuid4())
                elif corruption == 'missing_call_id':
                    receipt.pop('call_id')
                else:
                    ledger.calls[(args['request']['run_id'], args['attempt_id'])].pop(receipt['call_id'])
                result = self.run_async(executor, args)
                self.assertEqual(result.result['diagnostics'][0]['code'], 'GENERATION_RECEIPT_CONFLICT')
                self.assertEqual(len(seen), 2)

    def test_budget_exhaustion_preserves_first_batch(self):
        executor, args, store, ledger, seen = fixture()
        original = ledger.reserve
        def reserve(**kwargs):
            if seen:
                raise GenerationError('BUDGET_EXCEEDED')
            return original(**kwargs)
        ledger.reserve = reserve
        result = self.run_async(executor, args)
        self.assertEqual(result.result['diagnostics'][0]['code'], 'BUDGET_EXCEEDED')
        self.assertEqual(len(result.remaining_paths), 1)
        self.assertEqual(result.result['usage']['calls'], 1)

    def test_store_failure_after_call_is_accounted_and_retried(self):
        executor, args, store, ledger, seen = fixture()
        original = store.accept
        failed = []
        def accept(**kwargs):
            if not failed:
                failed.append(True)
                raise OSError('store unavailable')
            return original(**kwargs)
        store.accept = accept
        first = self.run_async(executor, args)
        self.assertEqual(first.result['diagnostics'][0]['code'], 'GENERATION_ACCEPTANCE_FAILED')
        self.assertEqual(first.result['usage']['calls'], 1)
        self.assertEqual(len(first.remaining_paths), 2)
        second = self.run_async(executor, args)
        self.assertEqual(second.result['status'], 'succeeded')
        self.assertEqual(second.result['usage']['calls'], 3)
        self.assertEqual(len(seen), 3)

    def test_retryable_provider_error_is_preserved(self):
        executor, args, store, ledger, seen = fixture()
        executor._transport = httpx.MockTransport(lambda request: httpx.Response(429))
        result = self.run_async(executor, args)
        self.assertEqual(result.result['diagnostics'][0]['code'], 'LLM_RATE_LIMITED')
        self.assertTrue(result.result['diagnostics'][0]['retryable'])

    def test_prompt_contains_task_and_request_size_matches_wire(self):
        executor, args, store, ledger, seen = fixture()
        lengths = []
        original = executor._transport
        def handler(request):
            data = json.loads(request.content)
            prompt = json.loads(data['messages'][1]['content'])
            self.assertEqual(prompt['task'], args['plan']['tasks'][0])
            schema = executor._schema(tuple(s for s in args['files'] if s.path in prompt['batch_paths']))
            from dataclasses import replace
            config = replace(executor._config, max_output_tokens=data['max_tokens'])
            lengths.append((len(request.content), executor._request_size(config, args['context'].system,
                                                                          data['messages'][1]['content'], schema)))
            return original.handler(request)
        executor._transport = httpx.MockTransport(handler)
        self.assertEqual(self.run_async(executor, args).result['status'], 'succeeded')
        self.assertTrue(lengths)
        self.assertTrue(all(actual == calculated for actual, calculated in lengths))

    def test_frozen_model_configuration_must_match(self):
        executor, args, store, ledger, seen = fixture()
        args['model_configuration'] = b'{}'
        with self.assertRaisesRegex(GenerationError, 'GENERATION_MODEL_CONFIGURATION_CHANGED'):
            self.run_async(executor, args)
        self.assertFalse(seen)

    def test_directory_only_inventory_is_rejected_before_dispatch(self):
        executor, args, store, ledger, seen = fixture()
        task = args['plan']['tasks'][0]
        task['output_paths'] = []
        task['output_directories'] = ['backend/app/services']
        with self.assertRaisesRegex(GenerationError, 'GENERATION_OUTPUT_INVENTORY_REQUIRED'):
            self.run_async(executor, args)
        self.assertFalse(seen)
        self.assertFalse(store.receipts)
        self.assertFalse(ledger.calls)

    def test_inventory_must_exactly_match_plan(self):
        for extra in (False, True):
            with self.subTest(extra=extra):
                executor, args, store, ledger, seen = fixture()
                task = args['plan']['tasks'][0]
                if extra:
                    task['output_paths'] = task['output_paths'][:1]
                    task['output_directories'] = ['backend/app/services']
                else:
                    args['files'] = args['files'][:1]
                with self.assertRaisesRegex(GenerationError, 'GENERATION_OUTPUT_INVENTORY_MISMATCH'):
                    self.run_async(executor, args)
                self.assertFalse(seen)
                self.assertFalse(ledger.calls)
