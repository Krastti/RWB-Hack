"""Deterministic analysis → wait → new analysis attempt, with durable boundaries."""

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from uuid import UUID, uuid5

from jsonschema import Draft202012Validator, FormatChecker
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from contracts.agents.validate import ContractError, Contracts, loads, walk

from .state import AnalysisNode, AnalysisState, ArtifactStore, GraphError


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise GraphError(code)


def _uuid(value: str) -> str:
    try:
        _require(str(UUID(value)) == value, "INVALID_UUID")
    except (ValueError, TypeError, AttributeError):
        raise GraphError("INVALID_UUID") from None
    return value


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _reference(artifact: dict) -> dict:
    return {k: deepcopy(v) for k, v in artifact.items() if k not in {"content", "storage_ref"}}


class AnalysisGraph:
    """One analysis node per Run, called under the worker's external lease.

    Node and artifact ports must be trusted adapters, never functions selected by
    model output. Successful analysis leaves Run running for downstream stages;
    this component cannot declare the whole generated project completed.
    """

    def __init__(self, node: AnalysisNode, artifacts: ArtifactStore,
                 checkpointer: BaseCheckpointSaver, *, stage_id: str = "analysis"):
        _require(checkpointer is not None, "CHECKPOINTER_REQUIRED")
        self.node, self.artifacts = node, artifacts
        self.stage_id = stage_id
        self.contracts = Contracts()
        self._usage_schema = Draft202012Validator(
            {"$ref": self.contracts.schemas["common"]["$id"] + "#/$defs/usage"},
            registry=self.contracts.registry, format_checker=FormatChecker(),
        )
        builder = StateGraph(AnalysisState)
        builder.add_node("begin_attempt", self._begin_attempt)
        builder.add_node("analyse", self._analyse)
        builder.add_node("await_answers", self._await_answers)
        builder.add_node("prepare_resume", self._prepare_resume)
        builder.add_edge(START, "begin_attempt")
        builder.add_edge("begin_attempt", "analyse")
        builder.add_conditional_edges(
            "analyse", lambda s: "await_answers" if s["status"] == "waiting_for_input" else END,
        )
        builder.add_edge("await_answers", "prepare_resume")
        builder.add_edge("prepare_resume", "begin_attempt")
        self._graph = builder.compile(checkpointer=checkpointer)

    def _validate(self, name: str, value: dict) -> None:
        try:
            _json(value)
            self.contracts.validate(name, value)
        except (ContractError, TypeError, ValueError):
            raise GraphError("INVALID_" + name.upper().replace("-", "_")) from None

    def _artifact(self, run_id: str, artifact_id: str) -> dict:
        artifact = deepcopy(self.artifacts.read(run_id, artifact_id))
        self._validate("artifact", artifact)
        _require(artifact["artifact_id"] == artifact_id and "content" in artifact,
                 "ARTIFACT_UNAVAILABLE")
        return artifact

    def _inputs(self, request: dict) -> tuple[dict, str]:
        artifacts = [self._artifact(request["run_id"], aid)
                     for aid in request["input_artifact_ids"]]
        sources = [a for a in artifacts if a["kind"] == "business_requirements"]
        _require(len(sources) == 1, "ANALYSIS_INPUT_REQUIRED")
        source = loads(sources[0]["content"])
        self._validate("analysis-input", source)
        for key in ("previous_requirements_artifact_id", "analysis_rules_artifact_id"):
            _require(source[key] is None or source[key] in request["input_artifact_ids"],
                     "INPUT_REFERENCE_NOT_ALLOWED")
        digest = sha256(_json({"request": request,
                               "artifacts": [_reference(a) for a in artifacts]}).encode()).hexdigest()
        return source, digest

    def _config(self, run_id: str) -> dict:
        return {"configurable": {"thread_id": _uuid(run_id)}, "recursion_limit": 12}

    def snapshot(self, run_id: str) -> AnalysisState:
        """Read the latest durable state, without running a node or exposing bytes."""
        snapshot = self._graph.get_state(self._config(run_id))
        _require(bool(snapshot.values), "RESUME_STATE_UNAVAILABLE")
        state = snapshot.values
        _require(state.get("state_version") == 1 and state.get("run_id") == run_id
                 and state.get("stage_id") == self.stage_id, "INCOMPATIBLE_CHECKPOINT")
        return deepcopy(state)

    def start(self, request: dict, *, attempt_id: str) -> AnalysisState:
        """Start once, or recover the same initial command on repeated delivery."""
        request = deepcopy(request)
        self._validate("agent-input", request)
        _uuid(attempt_id)
        _require(request["stage_id"] == self.stage_id, "STAGE_NOT_ALLOWED")
        config = self._config(request["run_id"])
        existing = self._graph.get_state(config)
        if existing.values:
            state = self.snapshot(request["run_id"])
            _require(state["initial_request"] == request
                     and state["initial_attempt_id"] == attempt_id, "RUN_STATE_CONFLICT")
            return self.recover(request["run_id"])
        source, digest = self._inputs(request)
        _require(request["attempt"] == 1 and not source["answers"] and source["questions_revision"] == 0
                 and source["previous_requirements_artifact_id"] is None,
                 "INITIAL_ANALYSIS_INPUT_REQUIRED")
        state: AnalysisState = {
            "state_version": 1, "run_id": request["run_id"],
            "stage_id": request["stage_id"], "contract_version": request["contract_version"],
            "profile_id": request["profile_id"], "initial_request": request,
            "initial_attempt_id": attempt_id, "request": request,
            "attempt_id": attempt_id, "input_hash": digest,
            "attempt_started_at": None,
            "status": "running", "stage_status": "pending",
            "questions_revision": 0, "questions": [], "attempts": [],
            "artifact_refs": [], "resume_command": None,
        }
        self._graph.invoke(state, config, durability="sync")
        return self.snapshot(request["run_id"])

    def recover(self, run_id: str) -> AnalysisState:
        """Continue pending checkpoint work; waiting/completed nodes are not rerun."""
        state = self.snapshot(run_id)
        snapshot = self._graph.get_state(self._config(run_id))
        # A completed task's writes can survive a failed checkpoint put. Its
        # next tuple is empty until LangGraph finalizes those pending writes.
        if (snapshot.next or snapshot.tasks) and not any(task.interrupts for task in snapshot.tasks):
            self._graph.invoke(None, self._config(run_id), durability="sync")
            return self.snapshot(run_id)
        return state

    def _check_answers(self, state: AnalysisState, command: dict) -> None:
        _require(type(command) is dict and set(command) == {
            "questions_revision", "answers", "attempt_id"}, "INVALID_ANSWERS")
        _uuid(command["attempt_id"])
        _require(command["attempt_id"] not in {
            state["attempt_id"], *(a["attempt_id"] for a in state["attempts"])},
            "ATTEMPT_ID_REUSED")
        _require(type(command["questions_revision"]) is int
                 and command["questions_revision"] == state["questions_revision"],
                 "QUESTIONS_REVISION_CONFLICT")
        answers = command["answers"]
        _require(type(answers) is list and bool(answers), "INVALID_ANSWERS")
        known = {q["question_id"]: q for q in state["questions"]}
        seen = set()
        for answer in answers:
            _require(type(answer) is dict and set(answer) == {"question_id", "value"},
                     "INVALID_ANSWERS")
            qid, value = answer["question_id"], answer["value"]
            _require(isinstance(qid, str) and qid in known and qid not in seen,
                     "INVALID_ANSWERS")
            _require(isinstance(value, str) and bool(value.strip()) and len(value) <= 10000,
                     "INVALID_ANSWERS")
            _require("answer" not in known[qid] or known[qid]["answer"] == value,
                     "CONFIRMED_ANSWER_CONFLICT")
            seen.add(qid)
        _require(all(not q["required"] or "answer" in q or q["question_id"] in seen
                     for q in state["questions"]), "INCOMPLETE_REQUIRED_ANSWERS")

    def resume(self, run_id: str, *, questions_revision: int,
               answers: list[dict], attempt_id: str) -> AnalysisState:
        """Apply one accepted full answer package; repeat delivery is a no-op.

        B-12/B-13 authenticate and atomically register the next attempt and outbox
        first; attempt_id is that persisted attempt UUID, not a request-local UUID.
        """
        command = deepcopy({"questions_revision": questions_revision,
                            "answers": answers, "attempt_id": attempt_id})
        state = self.snapshot(run_id)
        if state["resume_command"] == command:
            return self.recover(run_id)
        _require(state["status"] == "waiting_for_input", "RUN_STATE_CONFLICT")
        self._check_answers(state, command)
        self._graph.invoke(Command(resume=command), self._config(run_id), durability="sync")
        return self.snapshot(run_id)

    def _await_answers(self, state: AnalysisState) -> dict:
        # No model call or storage write before interrupt: this node is replayed.
        command = interrupt({"run_id": state["run_id"], "stage_id": state["stage_id"],
                             "questions_revision": state["questions_revision"],
                             "questions": state["questions"]})
        self._check_answers(state, command)
        return {"resume_command": command}

    def _prepare_resume(self, state: AnalysisState) -> dict:
        command = state["resume_command"]
        source, _ = self._inputs(state["request"])
        by_id = {a["question_id"]: a["value"] for a in command["answers"]}
        questions = deepcopy(state["questions"])
        for question in questions:
            if question["question_id"] in by_id:
                question["answer"] = by_id[question["question_id"]]
        previous = next(a for a in reversed(state["artifact_refs"])
                        if a["kind"] == "requirements")
        source.update(questions_revision=state["questions_revision"],
                      answers=[{"question_id": q["question_id"], "value": q["answer"]}
                               for q in questions if "answer" in q],
                      previous_requirements_artifact_id=previous["artifact_id"])
        self._validate("analysis-input", source)
        content = _json(source)
        attempt = state["request"]["attempt"] + 1
        artifact = {
            "artifact_id": str(uuid5(UUID(command["attempt_id"]), "analysis-input")),
            "attempt_id": command["attempt_id"], "path": "inputs/analysis.json",
            "kind": "business_requirements", "media_type": "application/json",
            "version": attempt, "schema_version": "1.0", "author_agent": "orchestrator",
            "checksum": sha256(content.encode()).hexdigest(),
            "size_bytes": len(content.encode()), "related_fr_ids": [], "content": content,
        }
        self._validate("artifact", artifact)
        self.artifacts.put(state["run_id"], artifact)
        request = deepcopy(state["request"])
        request.update(attempt=attempt, input_artifact_ids=list(dict.fromkeys(
            [artifact["artifact_id"], previous["artifact_id"]]
            + ([source["analysis_rules_artifact_id"]] if source["analysis_rules_artifact_id"] else []))))
        _, digest = self._inputs(request)
        return {"request": request, "attempt_id": command["attempt_id"],
                "input_hash": digest, "questions": questions,
                "status": "running", "stage_status": "pending", "attempt_started_at": None}

    def _begin_attempt(self, state: AnalysisState) -> dict:
        return {"stage_status": "running",
                "attempt_started_at": datetime.now(timezone.utc).isoformat()}

    def _analyse(self, state: AnalysisState) -> dict:
        result = None
        try:
            _, digest = self._inputs(state["request"])
            _require(digest == state["input_hash"], "INPUT_SNAPSHOT_CHANGED")
            result = self.artifacts.result(state["run_id"], state["attempt_id"])
            cached = result is not None
            if not cached:
                result = self.node(deepcopy(state["request"]), attempt_id=state["attempt_id"])
            return self._accept_analysis(state, deepcopy(result), cached)
        except (GraphError, ContractError) as error:
            code = str(error) if isinstance(error, GraphError) else "INVALID_ANALYSIS_RESULT"
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code):
                code = "GRAPH_VALIDATION_FAILED"
            record = {"attempt_id": state["attempt_id"],
                      "attempt_no": state["request"]["attempt"],
                      "input_hash": state["input_hash"], "status": "failed",
                      "started_at": state["attempt_started_at"],
                      "finished_at": datetime.now(timezone.utc).isoformat(),
                      "artifacts": [], "usage": self._observed_usage(state, result),
                      "diagnostics": [{"code": code, "severity": "error"}]}
            return {"status": "failed", "stage_status": "failed",
                    "attempts": state["attempts"] + [record]}

    def _observed_usage(self, state: AnalysisState, result) -> dict | None:
        if not isinstance(result, dict) or any(result.get(k) != state["request"][k]
                for k in ("run_id", "stage_id", "attempt", "contract_version")):
            return None
        usage = result.get("usage")
        try:
            _json(usage)
        except (TypeError, ValueError):
            return None
        if not self._usage_schema.is_valid(usage):
            return None
        return deepcopy(usage)

    def _accept_analysis(self, state: AnalysisState, result: dict, cached: bool) -> dict:
        request = state["request"]
        source, digest = self._inputs(request)
        _require(digest == state["input_hash"], "INPUT_SNAPSHOT_CHANGED")
        self._validate("agent-result", result)
        _require(all(result[k] == request[k] for k in (
            "run_id", "stage_id", "attempt", "contract_version")), "RESULT_CONTEXT_MISMATCH")
        outputs = result["output_artifacts"]
        used_ids = {a["artifact_id"] for a in state["artifact_refs"]} | set(request["input_artifact_ids"])
        for artifact in outputs:
            _require(artifact["attempt_id"] == state["attempt_id"]
                     and artifact["author_agent"] == "analysis"
                     and artifact["artifact_id"] not in used_ids, "RESULT_ARTIFACT_CONFLICT")
        resolved = []
        for artifact in outputs:
            full = (artifact if "content" in artifact else
                    self._artifact(state["run_id"], artifact["artifact_id"]))
            _require(_reference(full) == _reference(artifact), "ARTIFACT_METADATA_MISMATCH")
            resolved.append(full)
        questions, revision = self._validate_analysis(state, source, result, resolved)
        if not cached:
            self.artifacts.accept(state["run_id"], state["attempt_id"], result)
        references = [_reference(a) for a in outputs]
        status = {"waiting_for_input": "waiting_for_input", "failed": "failed",
                  "cancelled": "failed", "skipped": "failed"}.get(result["status"], "running")
        if result["status"] == "failed" and any(
                d["code"] == "BUDGET_EXCEEDED" for d in result["diagnostics"]):
            status = "budget_exceeded"
        record = {"attempt_id": state["attempt_id"], "attempt_no": request["attempt"],
                  "input_hash": digest, "status": result["status"], "artifacts": references,
                  "started_at": state["attempt_started_at"],
                  "finished_at": datetime.now(timezone.utc).isoformat(),
                  "usage": result["usage"], "diagnostics": result["diagnostics"]}
        return {"status": status, "stage_status": result["status"],
                "questions": questions, "questions_revision": revision,
                "artifact_refs": state["artifact_refs"] + references,
                "attempts": state["attempts"] + [record]}

    def _validate_analysis(self, state: AnalysisState, source: dict,
                           result: dict, outputs: list[dict]) -> tuple[list[dict], int]:
        questions = result["questions"]
        if result["status"] not in {"succeeded", "waiting_for_input"}:
            return deepcopy(state["questions"]), state["questions_revision"]
        answers = {q["question_id"]: q["answer"] for q in state["questions"] if "answer" in q}
        for q in questions:
            _require(q.get("answer") == answers.get(q["question_id"]), "UNCONFIRMED_ANSWER")
        _require(answers.keys() <= {q["question_id"] for q in questions}, "CONFIRMED_QUESTION_LOST")
        pending = [q for q in questions if q["required"] and "answer" not in q]
        _require(len(pending) <= state["request"]["limits"].get("max_critical_questions", 3),
                 "TOO_MANY_REQUIRED_QUESTIONS")
        kinds = {a["kind"]: a for a in outputs}
        _require(len(kinds) == len(outputs) and {"requirements", "questions", "system_analysis"} <= kinds.keys(),
                 "ANALYSIS_OUTPUTS_REQUIRED")
        reqs = loads(kinds["requirements"]["content"])
        qdoc = loads(kinds["questions"]["content"])
        _require(reqs["questions"] == questions == qdoc["questions"]
                 and reqs["assumptions"] == result["assumptions"], "ANALYSIS_RESULT_MISMATCH")
        # Answers alone do not create a new question-set revision.
        definition = lambda qs: sorted(
            [{k: v for k, v in q.items() if k != "answer"} for q in qs],
            key=lambda q: q["question_id"])
        revision = state["questions_revision"] + (definition(questions) != definition(state["questions"]))
        _require(reqs["questions_revision"] == qdoc["questions_revision"] == revision,
                 "QUESTIONS_REVISION_CONFLICT")
        previous_id = source["previous_requirements_artifact_id"]
        if previous_id:
            previous = loads(self._artifact(state["run_id"], previous_id)["content"])
            _require({f["id"] for f in previous["functional_requirements"]} <= {
                f["id"] for f in reqs["functional_requirements"]}, "FR_ID_LOST")
        for item in walk(reqs):
            if isinstance(item, dict) and set(item) == {"source", "quote", "question_id"}:
                original = (source["requirements_text"] if item["source"] == "requirements"
                            else answers.get(item["question_id"], ""))
                _require(item["quote"] in original, "UNSUPPORTED_EVIDENCE")
        return deepcopy(questions), revision
