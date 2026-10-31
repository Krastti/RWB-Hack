"""Explicit offline responses through all four real adapters; no real LLM or DB."""

import argparse
import asyncio
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

import httpx

from contracts.agents.validate import load, load_profile_rules
from ...llm import LLMConfig
from .inputs import ROOT
from .models import NODES, OUTPUTS
from .runtime import RuntimeNodes
from .stage import ArchitectureStage
from .inputs import prepare_architecture


def example_inputs(*, waiting=False):
    examples = ROOT / "contracts/agents/examples"
    context = load(examples / "agent-input.architecture.json")
    context["limits"].update(max_calls=4, max_output_tokens=16000)
    bundle = load(examples / ("agent-result.waiting.json" if waiting else "agent-result.succeeded.json"))
    requirements = next(a for a in bundle["output_artifacts"] if a["kind"] == "requirements")
    context["input_artifact_ids"] = [requirements["artifact_id"]]
    profile_bytes = (ROOT / "templates/profiles/web-fastapi-angular/profile.json").read_bytes()
    rules = load_profile_rules(json.loads(profile_bytes))
    api_policy = None if waiting else load(ROOT / "backend/app/agents/generators/openapi/examples/api-policy.json")
    prepared = prepare_architecture(context, requirements, profile_bytes=profile_bytes,
        profile_checksum=sha256(profile_bytes).hexdigest(), rules_text=rules,
        rules_checksum=sha256(rules.encode()).hexdigest(), api_policy=api_policy,
        naming_policy=None if waiting else load(Path(__file__).with_name("examples") / "naming-policy.json"))
    reservation = {"attempt_id": str(uuid4()),
        "artifacts": {kind: {"artifact_id": str(uuid4()), "version": 1} for kind in OUTPUTS},
        "call_ids": {node: str(uuid4()) for node in NODES}}
    return prepared, reservation


def fixture_transports():
    design = load(ROOT / "contracts/agents/examples/architecture.json")
    drafts = {
        "proposal": {key: design[key] for key in ("components", "decisions", "integrations")},
        "openapi": load(ROOT / "backend/app/agents/generators/openapi/examples/draft.json"),
        "data_model": load(ROOT / "backend/app/agents/generators/data_model/examples/data-model.json"),
        "ui_spec": load(Path(__file__).with_name("examples") / "ui-draft.json"),
    }
    for name in ("Ticket", "TicketStatusUpdate"):
        drafts["openapi"]["schemas"][name]["properties"]["status"]["maxLength"] = 32
    def transport(node):
        def reply(request):
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps(drafts[node], ensure_ascii=False)}}]})
        return httpx.MockTransport(reply)
    return {node: transport(node) for node in NODES}


async def run_example(*, waiting=False):
    prepared, reservation = example_inputs(waiting=waiting)
    # Waiting exercises the gate without importing or reading dependency fixtures.
    nodes = RuntimeNodes(LLMConfig(base_url="https://example.invalid/v1", model="offline-fixture",
        api_key="offline-placeholder", max_output_tokens=4000, timeout_seconds=30),
        transports={} if waiting else fixture_transports())
    return await ArchitectureStage(nodes).run(prepared, reservation=reservation)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--waiting", action="store_true")
    args = parser.parse_args()
    try:
        outcome = asyncio.run(run_example(waiting=args.waiting))
    except FileNotFoundError:
        raise SystemExit("Нужны принятые модули/fixtures A-05–A-08; см. README рядом с A-09.") from None
    print(json.dumps(outcome.result, ensure_ascii=False, indent=2))
    if outcome.result["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
