"""Generate a UI specification from requirements and OpenAPI."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import Protocol

from jsonschema import Draft202012Validator, FormatChecker

from contracts.agents.validate import ContractError, Contracts, load, loads
from ....errors.llm import LLMError
from ....models.dto.llm import LLMResult

from .openapi import operation_index, parse_openapi
from .validation import (
    ROOT, UiSpecError, draft_schema, require, ui_spec_schema,
    validate_shape, validate_ui_spec as validate_payload,
)

PROMPT_VERSION = "1.1.0"
CATALOG_VERSION = "1.0.0"
REPO = ROOT.parents[4]
MAX_INPUT_BYTES = 1048576


class StructuredLLM(Protocol):
    async def generate(self, *, system: str, prompt: str, schema: dict) -> LLMResult: ...


@dataclass(frozen=True)
class PreparedUiSpec:
    system: str = field(repr=False)
    prompt: str = field(repr=False)
    schema_json: str = field(repr=False)
    inputs_json: str = field(repr=False)
    max_output_tokens: int
    timeout_seconds: float


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n"


def _artifact(contracts: Contracts, artifact: dict, context: dict, *, kind: str, path: str) -> dict:
    require(isinstance(artifact.get("content"), str), "UI_INPUT_NOT_RESOLVED")
    require(len(artifact["content"].encode("utf-8")) <= MAX_INPUT_BYTES, "UI_INPUT_TOO_LARGE")
    contracts.validate("artifact", artifact)
    require(artifact["artifact_id"] in context["input_artifact_ids"], "UI_INPUT_NOT_ALLOWED")
    require(artifact["kind"] == kind and artifact["path"] == path, "UI_INPUT_KIND_MISMATCH")
    if kind == "requirements":
        require(artifact["media_type"] == "application/json", "UI_REQUIREMENTS_MEDIA_TYPE")
        return loads(artifact["content"])
    return parse_openapi(artifact["content"], artifact["media_type"])


def prepare_ui_spec(
    context: dict, *, requirements_artifact: dict, openapi_artifact: dict, profile_content: str,
) -> PreparedUiSpec:
    """Prepare a request from resolved artifacts and a saved profile."""
    try:
        context, requirements_artifact, openapi_artifact = loads(_json(
            [context, requirements_artifact, openapi_artifact]))
        contracts = Contracts()
        contracts.validate("agent-input", context)
        require(isinstance(profile_content, str)
                and len(profile_content.encode("utf-8")) <= MAX_INPUT_BYTES, "UI_INVALID_PROFILE")
        profile = loads(profile_content)
        from backend.app.agents.profiles.loader import validate_profile_data
        validate_profile_data(profile)
        require(context["profile_id"] == profile["profile_id"], "PROFILE_CONTEXT_MISMATCH")
        require(profile["ownership"]["default"] == "deny"
                and "ui-spec.json" in profile["ownership"]["agents"]["architecture"]["paths"], "UI_OUTPUT_NOT_ALLOWED")
        require(profile["ui_kit"]["name"] == "Bootstrap"
                and set(profile["ui_kit"]["required_states"]) == {"loading", "empty", "error", "forbidden", "success"}
                and set(profile["ui_kit"]["responsive_widths_px"]) == {360, 768, 1280}, "UI_UNSUPPORTED_PROFILE")
        requirements = _artifact(contracts, requirements_artifact, context,
                                 kind="requirements", path="requirements.json")
        document = _artifact(contracts, openapi_artifact, context, kind="openapi", path="contracts/openapi.yaml")
        require(requirements_artifact["artifact_id"] != openapi_artifact["artifact_id"], "UI_DUPLICATE_INPUT")
        require(not any(q["required"] and "answer" not in q for q in requirements["questions"]),
                "UI_UNRESOLVED_QUESTIONS")
        operations = operation_index(document, contract_version=context["contract_version"],
                                     fr_ids={item["id"] for item in requirements["functional_requirements"]},
                                     role_ids={item["id"] for item in requirements["roles"]})
        require(context["limits"]["max_calls"] >= 1 and profile["limits"]["agent"]["max_calls"] >= 1,
                "UI_CALL_BUDGET_EXHAUSTED")
        catalog = load(ROOT / "catalog.json")
        schema = ui_spec_schema()
        component_ids = schema["$defs"]["component"]["properties"]["catalog_id"]["enum"]
        require(catalog["catalog_version"] == CATALOG_VERSION
                and [item["id"] for item in catalog["components"]] == component_ids,
                "UI_CATALOG_MISMATCH")
        metadata = {
            "schema_version": "1.0", "contract_version": context["contract_version"],
            "profile_id": profile["profile_id"], "profile_version": profile["profile_version"],
            "profile_checksum": sha256(profile_content.encode("utf-8")).hexdigest(),
            "prompt_version": PROMPT_VERSION, "catalog_version": CATALOG_VERSION,
            "framework": "Angular", "ui_kit": "Bootstrap",
            "sources": {key: {field: value[field] for field in ("artifact_id", "checksum")}
                        for key, value in [("requirements", requirements_artifact), ("openapi", openapi_artifact)]},
        }
        inputs = {"context": context, "metadata": metadata, "requirements": requirements,
                  "operations": operations, "profile": profile, "openapi": document}
        prompt = {"requirements": requirements, "openapi": document, "component_catalog": catalog,
                  "operation_security": {key: value["security"] for key, value in operations.items()},
                  "profile_policy": {key: profile[key] for key in ("profile_id", "profile_version", "stack", "ui_kit")},
                  "contract_version": context["contract_version"]}
        defaults = profile["limits"]["agent"]
        return PreparedUiSpec(
            system=(ROOT / "system.md").read_text(encoding="utf-8"), prompt=_json(prompt),
            schema_json=_json(draft_schema()), inputs_json=_json(inputs),
            max_output_tokens=min(context["limits"]["max_output_tokens"], defaults["max_output_tokens"]),
            timeout_seconds=min(context["limits"].get("timeout_seconds", defaults["timeout_seconds"]),
                                defaults["timeout_seconds"], profile["limits"]["model_call"]["timeout_seconds"]),
        )
    except UiSpecError:
        raise
    except (ContractError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise UiSpecError("UI_INVALID_INPUT") from None


def _validate(spec: dict, inputs: dict) -> None:
    validate_payload(spec, **{key: inputs[key] for key in ("metadata", "requirements", "operations", "profile", "openapi")})
    require(len(_json(spec).encode("utf-8")) <= inputs["profile"]["limits"]["artifacts"]["max_file_bytes"],
            "UI_ARTIFACT_TOO_LARGE")


def validate_ui_spec(spec: dict, prepared: PreparedUiSpec) -> None:
    _validate(spec, loads(prepared.inputs_json))


async def generate_ui_spec(llm: StructuredLLM, prepared: PreparedUiSpec) -> LLMResult:
    """Generate and validate the payload, preserving usage on rejection."""
    result = await llm.generate(system=prepared.system, prompt=prepared.prompt, schema=loads(prepared.schema_json))
    if not result.ok:
        return result
    try:
        validate_shape(result.data, draft_schema())
        inputs = loads(prepared.inputs_json)
        spec = {**inputs["metadata"], **result.data}
        _validate(spec, inputs)
        return LLMResult(data=spec, usage=result.usage)
    except UiSpecError as exc:
        return LLMResult(usage=result.usage,
                         error=LLMError(exc.code, "The UI specification failed validation.", False))


def build_agent_result(
    prepared: PreparedUiSpec, result: LLMResult, *, attempt_id: str, artifact_id: str,
    artifact_version: int, call_id: str, model: str, duration_seconds: float,
) -> dict:
    """Build a C-02 result using identities assigned by the worker."""
    inputs = loads(prepared.inputs_json)
    context = inputs["context"]
    contracts = Contracts()
    usage = result.usage
    require(type(usage.calls) is int and usage.calls in (0, 1), "UI_INVALID_CALL_COUNT")
    outputs = []
    if result.ok:
        _validate(result.data, inputs)
        content = _json(result.data)
        outputs.append({
            "artifact_id": artifact_id, "attempt_id": attempt_id, "path": "ui-spec.json", "kind": "ui_spec",
            "media_type": "application/json", "version": artifact_version, "schema_version": "1.0",
            "author_agent": "architecture", "checksum": sha256(content.encode("utf-8")).hexdigest(),
            "size_bytes": len(content.encode("utf-8")),
            "related_fr_ids": sorted(item["id"] for item in inputs["requirements"]["functional_requirements"]),
            "content": content,
        })
    outcome = "succeeded" if result.ok else "timeout" if result.error.code == "LLM_TIMEOUT" else "failed"
    calls = ([{"call_id": call_id, "model": model, "outcome": outcome,
               "input_tokens": usage.tokens_in, "output_tokens": usage.tokens_out,
               "duration_seconds": usage.latency_ms / 1000}] if usage.calls else [])
    envelope = {key: context[key] for key in ("schema_version", "run_id", "stage_id", "attempt", "contract_version")}
    envelope.update(
        status="succeeded" if result.ok else "failed", output_artifacts=outputs, questions=[], assumptions=[],
        usage={"calls": usage.calls, "input_tokens": usage.tokens_in if usage.calls else 0,
               "output_tokens": usage.tokens_out if usage.calls else 0,
               "duration_seconds": duration_seconds, "model_calls": calls},
        diagnostics=[] if result.ok else [{
            "code": result.error.code, "severity": "error", "message": "UI specification generation failed.",
            "retryable": result.error.retryable, "related_fr_ids": [],
        }],
    )
    try:
        _json(envelope).encode("utf-8")
        contracts.validate("agent-result", envelope)
    except (ContractError, ValueError, TypeError):
        raise UiSpecError("UI_INVALID_RESULT_METADATA") from None
    return envelope
