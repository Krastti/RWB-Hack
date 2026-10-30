"""Offline A-01/C-02 example; the explicit HTTPX fixture is not a real LLM run."""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx

from ...llm import LLMAdapter, LLMConfig
from contracts.agents.validate import load

from .generator import ROOT, generate_openapi, prepare_openapi


def example_input(*, waiting=False):
    examples = ROOT / "contracts/agents/examples"
    context = load(examples / "agent-input.architecture.json")
    bundle = load(examples / ("agent-result.waiting.json" if waiting else "agent-result.succeeded.json"))
    artifact = next(item for item in bundle["output_artifacts"] if item["kind"] == "requirements")
    context["input_artifact_ids"] = [artifact["artifact_id"]]
    profile = load(ROOT / "templates/profiles/web-fastapi-angular/profile.json")
    return context, artifact, profile


async def main(*, result_json=False, waiting=False):
    context, artifact, profile = example_input(waiting=waiting)
    prepared = prepare_openapi(context, artifact, profile=profile)
    draft = load(Path(__file__).with_name("examples") / "draft.json")

    def reply(request):
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(draft, ensure_ascii=False),
        }}]})

    config = LLMConfig(
        base_url="https://example.invalid/v1", model="offline-fixture", api_key="offline-example-key",
        max_output_tokens=prepared.max_output_tokens, timeout_seconds=prepared.timeout_seconds,
        response_format="json_object",
    )
    result = await generate_openapi(
        LLMAdapter(config, transport=httpx.MockTransport(reply)), prepared, model=config.model,
        attempt_id=str(uuid4()), artifact_id=str(uuid4()),
    )
    if result_json or waiting:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result["status"] == "succeeded":
        print(result["output_artifacts"][0]["content"], end="")
    if result["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", action="store_true", help="Print the C-02 envelope instead of YAML")
    parser.add_argument("--waiting", action="store_true", help="Show the no-call clarification gate")
    args = parser.parse_args()
    asyncio.run(main(result_json=args.result, waiting=args.waiting))
