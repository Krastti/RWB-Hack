"""Actual base bdf55b3 implementation writes the checkpoint consumed by current code.

The two fixture modules are byte-for-byte git show bdf55b34baade23c6e1f9c51e31dacdfe2bec590
copies (not a synthetic legacy hash). No Git/network dependency when running tests.
"""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from backend.app.agents.graph import AnalysisGraph, GraphError, open_checkpoints
from backend.app.agents.graph.fixtures.base_bdf55b3.analysis import AnalysisGraph as BaseGraph
from backend.app.agents.graph.test_graph import AnalysisGraphTests, FIRST, example


class CrossVersionCheckpointTests(AnalysisGraphTests):
    # Reuse the fixture setup, not inherited tests: see load_tests below.
    def legacy_outage(self):
        def accept_then_crash(run_id, attempt_id, result):
            original(run_id, attempt_id, result)
            raise OSError('crashed after durable receipt, before checkpoint')
        result = example('agent-result.waiting')
        usage = result['usage']
        usage.update(calls=1, input_tokens=100, output_tokens=200)
        usage['model_calls'] = [dict(call_id='a42e19f4-80a9-5f04-941c-6d3ae4437755',
            model='offline-fixture',outcome='succeeded',input_tokens=100,output_tokens=200,duration_seconds=0.1)]
        self.modify = lambda _: deepcopy(result)
        original = self.store.accept
        with open_checkpoints(self.path / 'legacy.sqlite') as saver:
            graph = BaseGraph(self.node,self.store,saver)
            with patch.object(self.store,'accept',side_effect=accept_then_crash):
                with self.assertRaises(OSError):graph.start(self.request,attempt_id=FIRST)
            saved = graph.snapshot(self.run_id)
        self.assertNotIn('input_hash_algorithm', saved)
        self.assertIsNotNone(self.store.result(self.run_id,FIRST))
        return saved, result

    def test_cross_version_receipt_recovery(self):
        before,result=self.legacy_outage()
        with open_checkpoints(self.path / 'legacy.sqlite') as saver:
            graph=AnalysisGraph(self.node,self.store,saver)
            state=graph.recover(self.run_id)
            self.assertEqual(state['status'],'waiting_for_input')
            self.assertEqual(len(state['artifact_refs']),3)
            self.assertEqual(state['attempts'][0]['usage'],result['usage'])
            self.assertEqual(state['questions'],result['questions'])
            self.assertEqual(state['questions_revision'],1)
            self.assertEqual(state['attempts'][0]['input_hash'],before['input_hash'])
            self.assertEqual(state['input_hash'],before['input_hash'])
            self.assertEqual(state['input_hash_algorithm'],'full-request-v1')
            self.assertEqual(graph.recover(self.run_id),state)
        self.assertEqual(len(self.calls),1)

    def test_legacy_changed_input_does_not_use_receipt(self):
        self.legacy_outage()
        with self.store.db:
            row=self.store._get('artifacts',self.run_id,self.initial['artifact_id'])
            payload=json.loads(row['content']);payload['requirements_text']+=' changed'
            row['content']=json.dumps(payload)
            from hashlib import sha256
            row['checksum']=sha256(row['content'].encode()).hexdigest();row['size_bytes']=len(row['content'].encode())
            self.store.db.execute('UPDATE artifacts SET value=? WHERE run=? AND id=?',
                (json.dumps(row),self.run_id,row['artifact_id']))
        with open_checkpoints(self.path / 'legacy.sqlite') as saver:
            state=AnalysisGraph(self.node,self.store,saver).recover(self.run_id)
            self.assertEqual(state['status'],'failed')
            self.assertEqual(state['attempts'][-1]['diagnostics'][0]['code'],'INPUT_SNAPSHOT_CHANGED')
        self.assertEqual(len(self.calls),1)
        self.assertIsNotNone(self.store.result(self.run_id,FIRST))

    def test_legacy_recovery_after_confirmed_answers_preserves_history(self):
        with open_checkpoints(self.path / 'legacy-answers.sqlite') as saver:
            graph = BaseGraph(self.node,self.store,saver)
            first = graph.start(self.request,attempt_id=FIRST)
            original = self.store.accept
            def crash(run_id,attempt_id,result):
                original(run_id,attempt_id,result)
                raise OSError('receipt saved')
            from backend.app.agents.graph.test_graph import SECOND
            with patch.object(self.store,'accept',side_effect=crash):
                with self.assertRaises(OSError):
                    graph.resume(self.run_id,questions_revision=1,
                        answers=example('analysis-input.resume')['answers'],attempt_id=SECOND)
            before = graph.snapshot(self.run_id)
        with open_checkpoints(self.path / 'legacy-answers.sqlite') as saver:
            graph = AnalysisGraph(self.node,self.store,saver)
            after = graph.recover(self.run_id)
            self.assertEqual(after['stage_status'],'succeeded')
            self.assertEqual(after['attempts'][0],first['attempts'][0])
            self.assertEqual(after['questions'],before['questions'])
            self.assertEqual(after['questions_revision'],before['questions_revision'])
            self.assertEqual(after['input_hash'],before['input_hash'])
            self.assertEqual(len(after['artifact_refs']),6)
            self.assertEqual(graph.recover(self.run_id),after)
        self.assertEqual(len(self.calls),2)

    def test_new_hash_version_is_pinned_and_missing_version_is_compatible(self):
        with open_checkpoints(self.path / 'new.sqlite') as saver:
            graph = self.graph(saver)
            state = self.waiting(graph)
            self.assertEqual(state['input_hash_algorithm'],'substantive-v2')
            self.assertEqual(state['attempts'][0]['input_hash_algorithm'],'substantive-v2')
            unversioned=deepcopy(state);unversioned.pop('input_hash_algorithm')
            self.assertEqual(graph._verified_inputs(unversioned)[2],'substantive-v2')
            for marker in (None, [], 'unknown', 'full-request-v1'):
                candidate=deepcopy(state);candidate['input_hash_algorithm']=marker
                with self.subTest(marker=marker):
                    with self.assertRaises(GraphError):graph._verified_inputs(candidate)
                    failed=graph._analyse(candidate)
                    self.assertEqual(failed['status'],'failed')
            self.assertEqual(len(self.calls),1)


def load_tests(loader, tests, pattern):
    suite=unittest.TestSuite()
    for name in CrossVersionCheckpointTests.__dict__:
        if name.startswith('test_'):suite.addTest(CrossVersionCheckpointTests(name))
    return suite
