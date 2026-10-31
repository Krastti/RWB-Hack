"""Offline example using the real adapter and a controlled HTTP response."""

import argparse
import asyncio
from hashlib import sha256
import json
from time import monotonic
from uuid import NAMESPACE_URL, uuid5

import httpx

from ...llm import LLMAdapter, LLMConfig
from .generator import REPO, ROOT, build_agent_result, generate_ui_spec, prepare_ui_spec


def identifier(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, "rwb-hack/a08-example/" + name))


def input_artifact(content: str, *, kind: str, path: str, author: str) -> dict:
    return {
        "artifact_id": identifier(kind), "attempt_id": identifier(author), "path": path,
        "kind": kind, "media_type": "application/json", "version": 1, "schema_version": "1.0",
        "author_agent": author, "checksum": sha256(content.encode("utf-8")).hexdigest(),
        "size_bytes": len(content.encode("utf-8")),
        "related_fr_ids": ["FR-001", "FR-002", "FR-003", "FR-004"], "content": content,
    }


def example_inputs() -> dict:
    requirements = input_artifact(
        (REPO / "contracts/agents/examples/requirements.answered.json").read_text(encoding="utf-8"),
        kind="requirements", path="requirements.json", author="analysis",
    )
    openapi = input_artifact((ROOT / "examples/openapi.json").read_text(encoding="utf-8"),
                             kind="openapi", path="contracts/openapi.yaml", author="architecture")
    return {
        "context": {"schema_version": "1.0", "run_id": identifier("run"), "stage_id": "architecture-ui",
                    "attempt": 1, "input_artifact_ids": [requirements["artifact_id"], openapi["artifact_id"]],
                    "contract_version": "1", "profile_id": "web-fastapi-angular",
                    "limits": {"max_calls": 1, "max_output_tokens": 12000, "timeout_seconds": 60}},
        "requirements_artifact": requirements, "openapi_artifact": openapi,
        "profile_content": (REPO / "templates/profiles/web-fastapi-angular/profile.json").read_text(encoding="utf-8"),
    }


async def run_example() -> dict:
    prepared = prepare_ui_spec(**example_inputs())

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {
                "content": (ROOT / "examples/draft.json").read_text(encoding="utf-8"),
            }}],
            "usage": {"prompt_tokens": 1200, "completion_tokens": 2400, "total_tokens": 3600},
        })

    adapter = LLMAdapter(LLMConfig(
        base_url="https://example.invalid/v1", model="offline-fixture", api_key="offline-placeholder",
        max_output_tokens=prepared.max_output_tokens, timeout_seconds=prepared.timeout_seconds,
    ), transport=httpx.MockTransport(respond))
    started = monotonic()
    result = await generate_ui_spec(adapter, prepared)
    return build_agent_result(
        prepared, result, attempt_id=identifier("ui-attempt"), artifact_id=identifier("ui-spec"),
        artifact_version=1, call_id=identifier("call"), model="offline-fixture",
        duration_seconds=monotonic() - started,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", action="store_true", help="Print the C-02 AgentResult")
    args = parser.parse_args()
    result = asyncio.run(run_example())
    if args.result or result["status"] != "succeeded":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["output_artifacts"][0]["content"], end="")
    if result["status"] != "succeeded":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
