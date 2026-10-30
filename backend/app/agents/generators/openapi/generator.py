"""A single A-01 call followed by deterministic validation and C-02 packaging."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from typing import Protocol
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Resource

from contracts.agents.validate import Contracts, load, loads
from ....models.dto.llm import LLMResult

from .errors import OpenAPIError, require
from .render import MAX_ARTIFACT_BYTES, render_openapi
from .schema import draft_schema
from .policy import validate_api_policy

ROOT = Path(__file__).resolve().parents[5]
PROFILE_SCHEMA = ROOT / "templates/profiles/web-fastapi-angular/profile.schema.json"


class StructuredLLM(Protocol):
    async def generate(self, *, system: str, prompt: str, schema: dict) -> LLMResult: ...


@dataclass(frozen=True)
class OpenAPIPrompt:
    system: str = field(repr=False)
    prompt: str = field(repr=False)
    schema: dict = field(repr=False)
    context: dict = field(repr=False)
    requirements: dict = field(repr=False)
    api_policy: dict | None = field(repr=False)
    requirements_artifact_id: str
    max_calls: int
    max_output_tokens: int
    timeout_seconds: float
    max_artifact_bytes: int


@lru_cache(maxsize=1)
def _contracts():
    return Contracts()


def prepare_openapi(context: dict, requirements_artifact: dict, *, profile: dict,
                    api_policy: dict | None) -> OpenAPIPrompt:
    """Accept authorized requirements, a frozen profile and an approved API policy."""
    try:
        contracts = _contracts()
        context, artifact, profile = deepcopy(context), deepcopy(requirements_artifact), deepcopy(profile)
        json.dumps([context, artifact, profile], allow_nan=False)
        contracts.validate("agent-input", context)
        contracts.validate("artifact", artifact)
        schema = load(PROFILE_SCHEMA)
        registry = contracts.registry.with_resource(schema["$id"], Resource.from_contents(schema))
        require(Draft202012Validator(schema, registry=registry, format_checker=FormatChecker()).is_valid(profile),
                "OPENAPI_INVALID_PROFILE")
        require(context["profile_id"] == profile["profile_id"] == "web-fastapi-angular"
                and profile["contracts"]["openapi_version"] == "3.1.0", "OPENAPI_UNSUPPORTED_PROFILE")
        require(artifact["artifact_id"] in context["input_artifact_ids"]
                and artifact["kind"] == "requirements" and artifact["path"] == "requirements.json"
                and "content" in artifact, "OPENAPI_REQUIREMENTS_NOT_ALLOWED")
        grants = profile["ownership"]["agents"]["architecture"]
        require("contracts/openapi.yaml" in grants["paths"] or any(
            "contracts/openapi.yaml".startswith(directory + "/") for directory in grants["directories"]),
            "OPENAPI_OUTPUT_NOT_ALLOWED")
        requirements = loads(artifact["content"])
        contracts.validate("requirements", requirements)
        waiting = any(q["required"] and "answer" not in q for q in requirements["questions"])
        policy = None if waiting else validate_api_policy(contracts, api_policy, requirements, artifact["checksum"])
        limits = profile["limits"]
        tokens = min(context["limits"]["max_output_tokens"], limits["agent"]["max_output_tokens"])
        timeout = min(context["limits"].get("timeout_seconds", limits["agent"]["timeout_seconds"]),
                      limits["agent"]["timeout_seconds"], limits["model_call"]["timeout_seconds"])
        payload = {"contract_version": context["contract_version"], "requirements": requirements, "api_policy": policy,
                   "profile": {key: profile[key] for key in ("profile_id", "profile_version", "stack")}}
        return OpenAPIPrompt(
            system=Path(__file__).with_name("system.md").read_text(encoding="utf-8"),
            prompt=json.dumps(payload, ensure_ascii=False, allow_nan=False), schema=draft_schema(contracts),
            context=context, requirements=requirements, api_policy=policy, requirements_artifact_id=artifact["artifact_id"],
            max_calls=min(context["limits"]["max_calls"], limits["agent"]["max_calls"]),
            max_output_tokens=tokens, timeout_seconds=timeout,
            max_artifact_bytes=min(limits["artifacts"]["max_file_bytes"], MAX_ARTIFACT_BYTES),
        )
    except OpenAPIError:
        raise
    except Exception:
        raise OpenAPIError("OPENAPI_INVALID_INPUT") from None


async def generate_openapi(llm: StructuredLLM, prepared: OpenAPIPrompt, *, model: str,
                           attempt_id: str, artifact_id: str, artifact_version: int = 1) -> dict:
    """Return a C-02 AgentResult; no persistence, retries or Run transitions.

    Configure A-01 with prepared's limits and reserve the Run budget first.
    The worker supplies fresh artifact/attempt identities and checks ownership.
    """
    for value in (attempt_id, artifact_id):
        try:
            require(str(UUID(value)) == value, "OPENAPI_INVALID_OUTPUT_IDENTITY")
        except (ValueError, TypeError, AttributeError):
            raise OpenAPIError("OPENAPI_INVALID_OUTPUT_IDENTITY") from None
    require(artifact_id not in prepared.context["input_artifact_ids"]
            and type(artifact_version) is int and artifact_version > 0
            and isinstance(model, str) and bool(model.strip()), "OPENAPI_INVALID_OUTPUT_IDENTITY")
    start = time.monotonic()
    usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0,
             "duration_seconds": 0, "model_calls": []}

    def result(status, *, artifacts=None, assumptions=None, code=None, retryable=False):
        usage["duration_seconds"] = time.monotonic() - start
        value = {key: deepcopy(prepared.context[key]) for key in (
            "schema_version", "run_id", "stage_id", "attempt", "contract_version")}
        value.update(status=status, output_artifacts=artifacts or [],
                     questions=deepcopy(prepared.requirements["questions"]),
                     assumptions=deepcopy(prepared.requirements["assumptions"] if assumptions is None else assumptions),
                     usage=deepcopy(usage), diagnostics=[] if code is None else [{
                         "code": code, "message": "Контракт API не подготовлен; требуется устранить указанную причину.",
                         "severity": "error" if status == "failed" else "info",
                         "retryable": retryable, "related_fr_ids": []}])
        _contracts().validate("agent-result", value)
        return value

    if any(q["required"] and "answer" not in q for q in prepared.requirements["questions"]):
        return result("waiting_for_input", code="OPENAPI_WAITING_FOR_INPUT")
    if prepared.max_calls == 0:
        return result("failed", code="BUDGET_EXCEEDED")
    response = await llm.generate(system=prepared.system, prompt=prepared.prompt, schema=deepcopy(prepared.schema))
    observed = response.usage
    require(type(observed.calls) is int and observed.calls in {0, 1}, "OPENAPI_INVALID_LLM_USAGE")
    require(type(observed.latency_ms) is int and observed.latency_ms >= 0, "OPENAPI_INVALID_LLM_USAGE")
    require(all(v is None or type(v) is int and v >= 0 for v in (observed.tokens_in, observed.tokens_out)),
            "OPENAPI_INVALID_LLM_USAGE")
    require(response.error is not None or observed.calls == 1, "OPENAPI_INVALID_LLM_USAGE")
    if observed.calls:
        usage.update(calls=1, input_tokens=observed.tokens_in, output_tokens=observed.tokens_out,
                     model_calls=[{"call_id": str(uuid4()), "model": model,
                         "outcome": "succeeded" if response.ok else "timeout" if response.error.code == "LLM_TIMEOUT" else "failed",
                         "input_tokens": observed.tokens_in, "output_tokens": observed.tokens_out,
                         "duration_seconds": observed.latency_ms / 1000}])
    if not response.ok:
        code = response.error.code
        if not isinstance(code, str) or not code.startswith("LLM_") or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", code):
            code = "LLM_FAILURE"
        return result("failed", code=code, retryable=bool(response.error.retryable))
    if observed.tokens_out is not None and observed.tokens_out > prepared.max_output_tokens:
        usage["model_calls"][0]["outcome"] = "failed"
        return result("failed", code="BUDGET_EXCEEDED")
    try:
        content = render_openapi(response.data, prepared)
    except OpenAPIError as error:
        usage["model_calls"][0]["outcome"] = "failed"
        return result("failed", code=str(error))
    raw = content.encode("utf-8")
    artifact = {"artifact_id": artifact_id, "attempt_id": attempt_id, "path": "contracts/openapi.yaml",
                "kind": "openapi", "media_type": "application/yaml", "version": artifact_version,
                "schema_version": "1.0", "author_agent": "architecture", "content": content,
                "checksum": sha256(raw).hexdigest(), "size_bytes": len(raw),
                "related_fr_ids": sorted(f["id"] for f in prepared.requirements["functional_requirements"])}
    assumptions = yaml_assumptions(content)
    return result("succeeded", artifacts=[artifact], assumptions=assumptions)


def yaml_assumptions(content):
    # Only parse our own deterministic renderer output, never provider YAML.
    import yaml
    return yaml.safe_load(content)["x-assumptions"]
