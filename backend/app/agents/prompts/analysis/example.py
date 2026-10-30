"""Offline illustration through the actual A-01 adapter; no model or network.

The hand-authored draft is returned by an HTTPX in-memory transport. This verifies
the adapter boundary and local contract, not model quality or a ready pipeline.
"""

import argparse
import asyncio
import json

import httpx

from backend.app.agents.llm import LLMAdapter, LLMConfig
from .prompt import TEMPLATES, build_analysis_prompt, generate_analysis
from .render import render_system_analysis


async def main(resume: bool = False):
    examples = TEMPLATES / "examples"
    def read(name):
        return json.loads((examples / name).read_text(encoding="utf-8"))

    suffix = ".resume" if resume else ""
    prepared = build_analysis_prompt(
        read(f"agent-input{suffix}.json"), read(f"analysis-input{suffix}.json"),
        previous_requirements=read("draft.json")["requirements"] if resume else None,
    )

    def reply(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps(read("draft.answered.json" if resume else "draft.json"), ensure_ascii=False),
            }}],
        })

    adapter = LLMAdapter(LLMConfig(
        base_url="https://example.invalid/v1", model="offline-fixture", api_key="example-only",
        timeout_seconds=prepared.timeout_seconds, max_output_tokens=prepared.max_output_tokens,
        response_format="json_object",
    ), transport=httpx.MockTransport(reply))
    result = await generate_analysis(adapter, prepared)
    if not result.ok:
        raise RuntimeError(result.error.code)
    print(render_system_analysis(result.data, prepared), end="")
    # Production A-03 preserves result.usage even on failure. No invented token
    # counts: the fixture deliberately omits provider usage, so tokens stay None.


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true", help="Use the confirmed-answer fixture")
    asyncio.run(main(parser.parse_args().resume))
