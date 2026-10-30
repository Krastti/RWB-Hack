"""Run from the repository root: python -m backend.app.agents.llm.example."""

import asyncio
from dataclasses import asdict
import json
import os

from . import LLMAdapter, LLMConfig


async def main() -> None:
    key = os.environ.get("LLM_API_KEY")
    if not key:
        raise SystemExit("Set LLM_API_KEY in the process environment.")
    config = LLMConfig(
        base_url=os.environ.get("LLM_BASE_URL", "http://45.146.131.213:14000/v1"),
        model=os.environ.get("LLM_MODEL", "qwen3.8-27b"),
        api_key=key,
        response_format=os.environ.get("LLM_RESPONSE_FORMAT", "json_schema"),
    )
    # Demonstration schema only; not an AgentResult contract or business result.
    result = await LLMAdapter(config).generate(
        system="Extract the project name from the user's text.",
        prompt="The project is called Parking Helper.",
        schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    if not result.ok:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
