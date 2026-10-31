"""Offline integration and contract-boundary regression tests for A-09."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest

from backend.app.agents.graph.architecture.example import example_inputs, fixture_transports
from backend.app.agents.graph.architecture.models import ArchitectureError, NodeReply, NODES
from backend.app.agents.graph.architecture.naming import validate_names
from backend.app.agents.graph.architecture.runtime import RuntimeNodes
from backend.app.agents.graph.architecture.stage import ArchitectureExecutionError, ArchitectureStage
from backend.app.agents.llm import LLMConfig
from contracts.agents.validate import loads


def setup_stage(wrapper=None):
    prepared, reservation = example_inputs()
    runtime = RuntimeNodes(LLMConfig(base_url="https://example.invalid/v1", model="offline-fixture",
        api_key="offline-placeholder", max_output_tokens=4000, timeout_seconds=30), transports=fixture_transports())
    return prepared, reservation, ArchitectureStage(wrapper(runtime) if wrapper else runtime)


def run(stage, prepared, reservation):
    return asyncio.run(stage.run(prepared, reservation=reservation))


def test_success():
    prepared, reservation, stage = setup_stage()
    outcome = run(stage, prepared, reservation)
    assert outcome.ready_for_generation
    assert len(outcome.result["output_artifacts"]) == 5
    assert [x["call_id"] for x in outcome.result["usage"]["model_calls"]] == [reservation["call_ids"][n] for n in NODES]


@pytest.mark.parametrize("failed", NODES)
def test_node_failure_preserves_usage_and_no_partial_outputs(failed):
    def wrap(runtime):
        async def call(name, request):
            reply = await runtime(name, request)
            return replace(reply, error_code="TEST_FAILURE") if name == failed else reply
        return call
    prepared, reservation, stage = setup_stage(wrap)
    outcome = run(stage, prepared, reservation)
    assert outcome.result["status"] == "failed"
    assert outcome.result["output_artifacts"] == [] and outcome.project_tree == []
    assert outcome.result["usage"]["calls"] == NODES.index(failed) + 1


def test_unreserved_call_rejected():
    def wrap(runtime):
        async def call(name, request):
            reply = await runtime(name, request)
            if name == "openapi":
                usage = deepcopy(reply.usage)
                usage["model_calls"][0]["call_id"] = str(uuid4())
                return replace(reply, usage=usage)
            return reply
        return call
    prepared, reservation, stage = setup_stage(wrap)
    with pytest.raises(ArchitectureExecutionError) as exc:
        run(stage, prepared, reservation)
    assert exc.value.node == "openapi" and exc.value.partial_usage["calls"] == 1


def test_cancellation_propagates():
    def wrap(runtime):
        async def call(name, request):
            raise asyncio.CancelledError()
        return call
    prepared, reservation, stage = setup_stage(wrap)
    with pytest.raises(asyncio.CancelledError):
        run(stage, prepared, reservation)


def test_contract_constraints():
    prepared, reservation, stage = setup_stage()
    outcome = run(stage, prepared, reservation)
    import yaml
    artifacts = {a["kind"]: a for a in outcome.result["output_artifacts"]}
    document = yaml.safe_load(artifacts["openapi"]["content"])
    model = loads(artifacts["data_model"]["content"])
    policy = loads(prepared.inputs_json)["naming_policy"]
    broken = deepcopy(document)
    del broken["components"]["schemas"]["Ticket"]["properties"]["status"]["maxLength"]
    with pytest.raises(ArchitectureError, match="ARCHITECTURE_FIELD_CONSTRAINT_MISMATCH"):
        validate_names(broken, model, policy)
    broken = deepcopy(document)
    broken["components"]["schemas"]["TicketCreate"]["required"] = []
    with pytest.raises(ArchitectureError, match="ARCHITECTURE_FIELD_REQUIRED_MISMATCH"):
        validate_names(broken, model, policy)


def test_tree_plan_disagreement_fails_without_outputs():
    def wrap(runtime):
        async def call(name, request):
            reply = await runtime(name, request)
            if name == "proposal":
                payload = deepcopy(reply.payload)
                payload["generation_plan"]["tasks"] = [t for t in payload["generation_plan"]["tasks"] if t["owner_agent"] != "frontend"]
                for task in payload["generation_plan"]["tasks"]:
                    task["depends_on"] = [x for x in task["depends_on"] if x not in {"frontend", "frontend-template"}]
                return replace(reply, payload=payload)
            return reply
        return call
    prepared, reservation, stage = setup_stage(wrap)
    outcome = run(stage, prepared, reservation)
    assert outcome.result["status"] == "failed" and not outcome.result["output_artifacts"]
    assert outcome.result["diagnostics"][0]["code"] == "ARCHITECTURE_TREE_PLAN_MISMATCH"


def test_incompatible_data_model_fails_without_partial_results():
    import hashlib
    import json
    def wrap(runtime):
        async def call(name, request):
            reply = await runtime(name, request)
            if name == "data_model":
                artifact = deepcopy(reply.payload)
                model = json.loads(artifact["content"])
                field = next(f for e in model["entities"] for f in e["fields"] if f["name"] == "status")
                field["max_length"] = 2
                content = json.dumps(model, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
                artifact.update(content=content, size_bytes=len(content.encode()), checksum=hashlib.sha256(content.encode()).hexdigest())
                return replace(reply, payload=artifact)
            return reply
        return call
    prepared, reservation, stage = setup_stage(wrap)
    outcome = run(stage, prepared, reservation)
    assert outcome.result["status"] == "failed"
    assert outcome.result["diagnostics"][0]["code"] == "ARCHITECTURE_FIELD_CONSTRAINT_MISMATCH"
    assert outcome.result["usage"]["calls"] == 3
    assert not outcome.result["output_artifacts"] and not outcome.project_tree


def test_put_request_checks_required_fields():
    prepared, reservation, stage = setup_stage()
    outcome = run(stage, prepared, reservation)
    import yaml
    artifacts = {a["kind"]: a for a in outcome.result["output_artifacts"]}
    document = yaml.safe_load(artifacts["openapi"]["content"])
    model = loads(artifacts["data_model"]["content"])
    policy = loads(prepared.inputs_json)["naming_policy"]
    path = next(p for p in document["paths"].values() if "post" in p)
    path["put"] = path.pop("post")
    document["components"]["schemas"]["TicketCreate"]["required"] = []
    with pytest.raises(ArchitectureError, match="ARCHITECTURE_FIELD_REQUIRED_MISMATCH"):
        validate_names(document, model, policy)
