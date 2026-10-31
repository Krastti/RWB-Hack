"""Generate an artifact using an offline model response."""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx

from contracts.agents.validate import Contracts, load
from backend.app.agents.llm import LLMAdapter, LLMConfig

from . import build_data_model_prompt, generate_data_model, make_data_model_artifact
from .generator import ROOT, PROFILE


async def main():
    examples = ROOT / "contracts/agents/examples"
    context = load(examples / "agent-input.architecture.json")
    result = load(examples / "agent-result.succeeded.json")
    requirements = next(a for a in result["output_artifacts"] if a["kind"] == "requirements")
    prepared = build_data_model_prompt(context, requirements, profile=load(PROFILE / "profile.json"))
    fixture = load(Path(__file__).with_name("examples") / "data-model.json")

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(fixture)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
        })

    adapter = LLMAdapter(LLMConfig(
        base_url="https://example.invalid/v1", model="offline-fixture", api_key="example-only",
        timeout_seconds=prepared.timeout_seconds, max_output_tokens=prepared.max_output_tokens,
        response_format="json_object",
    ), transport=httpx.MockTransport(respond))
    generated = await generate_data_model(adapter, prepared)
    if not generated.ok:
        raise RuntimeError(generated.error.code)
    artifact = make_data_model_artifact(generated.data, prepared,
                                       artifact_id=str(uuid4()), attempt_id=str(uuid4()))
    Contracts().validate("artifact", artifact)
    print(artifact["content"], end="")


if __name__ == "__main__":
    asyncio.run(main())
