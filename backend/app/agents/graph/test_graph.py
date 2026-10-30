"""Local graph checks with C-02 fixtures, durable SQLite and injected ports."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from backend.app.agents.graph import AnalysisGraph, GraphError, open_checkpoints


EXAMPLES = Path(__file__).resolve().parents[4] / "contracts/agents/examples"


def example(name):
    return json.loads((EXAMPLES / (name + ".json")).read_text(encoding="utf-8"))


FIRST = example("agent-result.waiting")["output_artifacts"][0]["attempt_id"]
SECOND = example("agent-result.succeeded")["output_artifacts"][0]["attempt_id"]


class FixtureStore:
    """Test port only; does not replace the platform artifact repository."""

    def __init__(self, path):
        self.db = sqlite3.connect(path)
        for table in ("artifacts", "results"):
            self.db.execute(f"CREATE TABLE IF NOT EXISTS {table} "
                            "(run TEXT, id TEXT, value TEXT, PRIMARY KEY(run,id))")

    def _get(self, table, run_id, item_id):
        row = self.db.execute(f"SELECT value FROM {table} WHERE run=? AND id=?",
                              (run_id, item_id)).fetchone()
        return json.loads(row[0]) if row else None

    def _put(self, table, run_id, item_id, value):
        previous = self._get(table, run_id, item_id)
        if previous is not None:
            if previous != value:
                raise GraphError("IMMUTABLE_ARTIFACT_CONFLICT")
            return
        self.db.execute(f"INSERT INTO {table} VALUES (?,?,?)",
                        (run_id, item_id, json.dumps(value)))

    def read(self, run_id, artifact_id):
        value = self._get("artifacts", run_id, artifact_id)
        if value is None:
            raise GraphError("ARTIFACT_UNAVAILABLE")
        return value

    def put(self, run_id, artifact):
        with self.db:
            self._put("artifacts", run_id, artifact["artifact_id"], artifact)

    def result(self, run_id, attempt_id):
        return self._get("results", run_id, attempt_id)

    def accept(self, run_id, attempt_id, result):
        with self.db:
            for artifact in result["output_artifacts"]:
                self._put("artifacts", run_id, artifact["artifact_id"], artifact)
            self._put("results", run_id, attempt_id, result)


class AnalysisGraphTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)
        self.store = FixtureStore(self.path / "artifacts.sqlite")
        self.addCleanup(lambda: self.store.db.close())
        self.request = example("agent-input.analysis")
        self.run_id = self.request["run_id"]
        content = json.dumps(example("analysis-input"), ensure_ascii=False)
        self.initial = {
            "artifact_id": self.request["input_artifact_ids"][0], "attempt_id": FIRST,
            "path": "inputs/analysis.json", "kind": "business_requirements",
            "media_type": "application/json", "version": 1, "schema_version": "1.0",
            "author_agent": "orchestrator", "related_fr_ids": [], "content": content,
            "checksum": sha256(content.encode()).hexdigest(), "size_bytes": len(content.encode()),
        }
        self.store.put(self.run_id, self.initial)
        self.calls = []
        self.modify = lambda result: result

    def node(self, request, *, attempt_id):
        self.calls.append((request["attempt"], attempt_id))
        name = "agent-result.waiting" if request["attempt"] == 1 else "agent-result.succeeded"
        return self.modify(example(name))

    def graph(self, saver):
        return AnalysisGraph(self.node, self.store, saver)

    def waiting(self, graph):
        state = graph.start(self.request, attempt_id=FIRST)
        self.assertEqual(state["status"], "waiting_for_input")
        return state

    def resume(self, graph, **changes):
        arguments = {"questions_revision": 1, "attempt_id": SECOND,
                     "answers": example("analysis-input.resume")["answers"]}
        arguments.update(changes)
        return graph.resume(self.run_id, **arguments)

    def test_restart_resume_and_duplicate_delivery(self):
        with open_checkpoints(self.path / "graph.sqlite") as saver:
            before = self.waiting(self.graph(saver))
        self.store.db.close()
        self.store = FixtureStore(self.path / "artifacts.sqlite")
        with open_checkpoints(self.path / "graph.sqlite") as saver:
            graph = self.graph(saver)
            self.assertEqual(graph.recover(self.run_id), before)
            self.assertEqual(len(self.calls), 1)
            done = self.resume(graph)
            self.assertEqual(done["status"], "running")
            self.assertEqual(done["stage_status"], "succeeded")
            self.assertEqual([a["status"] for a in done["attempts"]],
                             ["waiting_for_input", "succeeded"])
            self.assertEqual(len(done["artifact_refs"]), 6)
            self.assertEqual(done["questions_revision"], 1)
            self.assertEqual(done["request"]["attempt"], 2)
            self.assertEqual(done["questions"][0]["answer"],
                             example("analysis-input.resume")["answers"][0]["value"])
            self.assertTrue(all(a["started_at"] and a["finished_at"] for a in done["attempts"]))
            self.assertEqual(self.resume(graph), done)
            self.assertEqual(graph.start(self.request, attempt_id=FIRST), done)
            self.assertEqual(len(self.calls), 2)
            self.assertNotIn('"content"', json.dumps(done))

    def test_invalid_answers_leave_waiting_unchanged(self):
        with open_checkpoints(self.path / "graph.sqlite") as saver:
            graph = self.graph(saver)
            before = self.waiting(graph)
            invalid = [[], [{"question_id": str(uuid4()), "value": "ok"}],
                       [{"question_id": before["questions"][0]["question_id"], "value": "   "}],
                       example("analysis-input.resume")["answers"] * 2]
            for answers in invalid:
                with self.subTest(answers=answers), self.assertRaises(GraphError):
                    self.resume(graph, answers=answers)
                self.assertEqual(graph.snapshot(self.run_id), before)
            with self.assertRaisesRegex(GraphError, "QUESTIONS_REVISION_CONFLICT"):
                self.resume(graph, questions_revision=2)
            self.assertEqual(len(self.calls), 1)
            self.assertEqual(self.resume(graph)["stage_status"], "succeeded")

    def test_durable_receipt_avoids_second_model_call(self):
        self.store.accept(self.run_id, FIRST, example("agent-result.waiting"))
        with open_checkpoints(self.path / "graph.sqlite") as saver:
            self.waiting(self.graph(saver))
        self.assertEqual(self.calls, [])

    def test_optional_questions_do_not_pause_successful_analysis(self):
        def optional(result):
            result["status"] = "succeeded"
            result["questions"][0]["required"] = False
            for artifact in result["output_artifacts"]:
                if artifact["kind"] in {"requirements", "questions"}:
                    value = json.loads(artifact["content"])
                    value["questions"] = deepcopy(result["questions"])
                    artifact["content"] = json.dumps(value, ensure_ascii=False)
                    artifact["size_bytes"] = len(artifact["content"].encode())
                    artifact["checksum"] = sha256(artifact["content"].encode()).hexdigest()
            return result

        self.modify = optional
        with open_checkpoints(self.path / "graph.sqlite") as saver:
            graph = self.graph(saver)
            state = graph.start(self.request, attempt_id=FIRST)
            self.assertEqual(state["stage_status"], "succeeded")
            self.assertEqual(state["status"], "running")
            self.assertEqual(graph.recover(self.run_id), state)
            self.assertEqual(len(self.calls), 1)

    def test_new_question_revision_keeps_previously_confirmed_answer(self):
        with open_checkpoints(self.path / "graph.sqlite") as saver:
            graph = self.graph(saver)
            self.waiting(graph)

            def another_question(result):
                result["status"] = "waiting_for_input"
                question = deepcopy(result["questions"][0])
                question.pop("answer")
                question.update(question_id=str(uuid4()), question="Нужен ли аудит?",
                                reason="Влияет на хранение истории.")
                result["questions"].append(question)
                for artifact in result["output_artifacts"]:
                    if artifact["kind"] in {"requirements", "questions"}:
                        value = json.loads(artifact["content"])
                        value.update(questions=deepcopy(result["questions"]), questions_revision=2)
                        artifact["content"] = json.dumps(value, ensure_ascii=False)
                        artifact["size_bytes"] = len(artifact["content"].encode())
                        artifact["checksum"] = sha256(artifact["content"].encode()).hexdigest()
                return result

            self.modify = another_question
            state = self.resume(graph)
            self.assertEqual(state["status"], "waiting_for_input")
            self.assertEqual(state["questions_revision"], 2)
            self.assertIn("answer", state["questions"][0])
            self.assertNotIn("answer", state["questions"][1])
            self.assertEqual(len(state["attempts"]), 2)

    def test_profile_is_forwarded_without_assuming_a_generated_stack(self):
        self.request["profile_id"] = "web-fastapi-angular"
        with open_checkpoints(self.path / "graph.sqlite") as saver:
            state = self.waiting(self.graph(saver))
            self.assertEqual(state["profile_id"], "web-fastapi-angular")

    def test_checkpoint_failure_after_receipt_does_not_repeat_model(self):
        with open_checkpoints(self.path / "graph.sqlite") as saver:
            original = saver.put

            def fail_after_receipt(config, checkpoint, metadata, new_versions):
                if checkpoint["channel_values"].get("stage_status") == "waiting_for_input":
                    raise OSError("simulated checkpoint outage")
                return original(config, checkpoint, metadata, new_versions)

            with patch.object(saver, "put", side_effect=fail_after_receipt):
                with self.assertRaises(OSError):
                    self.graph(saver).start(self.request, attempt_id=FIRST)
        self.assertIsNotNone(self.store.result(self.run_id, FIRST))
        with open_checkpoints(self.path / "graph.sqlite") as saver:
            state = self.graph(saver).recover(self.run_id)
            self.assertEqual(state["status"], "waiting_for_input")
        self.assertEqual(len(self.calls), 1)

    def test_resume_preparation_can_recover_after_artifact_write(self):
        with open_checkpoints(self.path / "graph.sqlite") as saver:
            graph = self.graph(saver)
            self.waiting(graph)
            original = self.store.put

            def fail_after_write(run_id, artifact):
                original(run_id, artifact)
                raise OSError("simulated worker outage")

            with patch.object(self.store, "put", side_effect=fail_after_write):
                with self.assertRaises(OSError):
                    self.resume(graph)
        with open_checkpoints(self.path / "graph.sqlite") as saver:
            state = self.graph(saver).recover(self.run_id)
            self.assertEqual(state["request"]["attempt"], 2)
            self.assertEqual(state["stage_status"], "succeeded")
        self.assertEqual(len(self.calls), 2)

    def _answer_checkpoint_failure(self, *, replay):
        path = self.path / "graph.sqlite"
        with open_checkpoints(path) as saver:
            graph = self.graph(saver)
            before = self.waiting(graph)
            original = saver.put

            def fail_after_answers(config, checkpoint, metadata, new_versions):
                values = checkpoint["channel_values"]
                if values.get("resume_command") and values.get("stage_status") == "waiting_for_input":
                    raise OSError("simulated checkpoint outage after answers")
                return original(config, checkpoint, metadata, new_versions)

            with patch.object(saver, "put", side_effect=fail_after_answers):
                with self.assertRaises(OSError):
                    self.resume(graph)
        self.assertEqual(len(self.calls), 1)

        with open_checkpoints(path) as saver:
            graph = self.graph(saver)
            state = self.resume(graph) if replay else graph.recover(self.run_id)
            self.assertEqual(state["stage_status"], "succeeded")
            self.assertEqual(state["status"], "running")
            self.assertEqual(state["request"]["attempt"], 2)
            self.assertEqual(state["questions_revision"], 1)
            self.assertEqual(state["attempts"][0], before["attempts"][0])
            self.assertEqual(state["questions"][0]["answer"],
                             example("analysis-input.resume")["answers"][0]["value"])
            self.assertEqual([a["attempt_no"] for a in state["attempts"]], [1, 2])
            self.assertEqual(graph.recover(self.run_id), state)
            self.assertEqual(self.resume(graph), state)
        self.assertEqual(self.calls, [(1, FIRST), (2, SECOND)])

    def test_recover_after_answer_checkpoint_failure(self):
        self._answer_checkpoint_failure(replay=False)

    def test_redelivered_answer_after_checkpoint_failure(self):
        self._answer_checkpoint_failure(replay=True)

    def test_missing_checkpoint_fails_safely(self):
        with open_checkpoints(self.path / "graph.sqlite") as saver:
            with self.assertRaisesRegex(GraphError, "RESUME_STATE_UNAVAILABLE"):
                self.graph(saver).recover(self.run_id)

    def test_stale_result_is_rejected_without_attributing_its_usage(self):
        self.modify = lambda result: dict(result, run_id=str(uuid4()))
        with open_checkpoints(self.path / "graph.sqlite") as saver:
            state = self.graph(saver).start(self.request, attempt_id=FIRST)
            self.assertEqual(state["status"], "failed")
            self.assertEqual(state["artifact_refs"], [])
            self.assertIsNone(state["attempts"][0]["usage"])
        self.assertIsNone(self.store.result(self.run_id, FIRST))

    def test_output_checksum_failure_preserves_valid_usage(self):
        def corrupt(result):
            result["output_artifacts"][0]["checksum"] = "0" * 64
            return result

        self.modify = corrupt
        with open_checkpoints(self.path / "graph.sqlite") as saver:
            state = self.graph(saver).start(self.request, attempt_id=FIRST)
            self.assertEqual(state["status"], "failed")
            self.assertEqual(state["artifact_refs"], [])
            self.assertEqual(state["attempts"][0]["usage"], example("agent-result.waiting")["usage"])

    def test_answer_cannot_be_invented_by_model(self):
        def invent(result):
            result["questions"][0]["answer"] = "model invented this"
            return result

        self.modify = invent
        with open_checkpoints(self.path / "graph.sqlite") as saver:
            state = self.graph(saver).start(self.request, attempt_id=FIRST)
            self.assertEqual(state["status"], "failed")
            self.assertIsNone(self.store.result(self.run_id, FIRST))

    def test_checkpoint_cannot_be_volatile_or_relative(self):
        for path in (":memory:", "relative.sqlite"):
            with self.subTest(path=path), self.assertRaisesRegex(
                    ValueError, "CHECKPOINT_PATH_MUST_BE_ABSOLUTE"):
                with open_checkpoints(path):
                    pass


if __name__ == "__main__":
    unittest.main()
