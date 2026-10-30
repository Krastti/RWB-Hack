"""Prepare model input, validate the response and serialize its artifact."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
import json
from pathlib import Path
from typing import Protocol

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Resource

from backend.app.errors.llm import LLMError
from backend.app.models.dto.llm import LLMResult
from contracts.agents.validate import ContractError, Contracts, load, loads

from .validation import DataModelError, data_model_schema, require, validate_data_model

ROOT = Path(__file__).resolve().parents[5]
PROFILE = ROOT / "templates/profiles/web-fastapi-angular"


class StructuredLLM(Protocol):
    async def generate(self, *, system: str, prompt: str, schema: dict) -> LLMResult: ...


@dataclass(frozen=True)
class DataModelPrompt:
    system: str = field(repr=False)
    prompt: str = field(repr=False)
    context: dict = field(repr=False)
    requirements: dict = field(repr=False)
    requirements_artifact_id: str
    profile_version: str
    max_output_tokens: int
    timeout_seconds: float
    max_file_bytes: int


def build_data_model_prompt(context: dict, requirements_artifact: dict, *, profile: dict) -> DataModelPrompt:
    """Prepare authorized requirements and a pinned profile for generation."""
    try:
        json.dumps([context, requirements_artifact, profile], allow_nan=False)
    except (ValueError, TypeError):
        raise DataModelError("DATA_MODEL_INVALID_JSON") from None
    contracts = Contracts()
    contracts.validate("agent-input", context)
    contracts.validate("artifact", requirements_artifact)
    require(requirements_artifact["kind"] == "requirements" and "content" in requirements_artifact,
            "DATA_MODEL_REQUIREMENTS_REQUIRED")
    require(requirements_artifact["artifact_id"] in context["input_artifact_ids"],
            "DATA_MODEL_INPUT_NOT_ALLOWED")
    schema = load(PROFILE / "profile.schema.json")
    registry = contracts.registry.with_resource(schema["$id"], Resource.from_contents(schema))
    validator = Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())
    require(not any(validator.iter_errors(profile)), "DATA_MODEL_INVALID_PROFILE")
    require(context["profile_id"] == profile["profile_id"] == "web-fastapi-angular" and
            profile["profile_version"] == "1.0.0" and profile["stack"]["database"] == "PostgreSQL" and
            profile["stack"]["migrations"] == "Alembic", "DATA_MODEL_UNSUPPORTED_PROFILE")
    require("data-model.json" in profile["ownership"]["agents"]["architecture"]["paths"],
            "DATA_MODEL_OUTPUT_NOT_ALLOWED")
    defaults = profile["limits"]["agent"]
    require(min(context["limits"]["max_calls"], defaults["max_calls"]) > 0,
            "DATA_MODEL_CALL_BUDGET_EXHAUSTED")
    requirements = loads(requirements_artifact["content"])
    require(not any(q["required"] and "answer" not in q for q in requirements["questions"]),
            "DATA_MODEL_REQUIRES_ANSWERS")
    payload = {
        "context": {
            key: context[key]
            for key in ("run_id", "stage_id", "attempt", "contract_version", "profile_id")
        },
        "requirements_artifact_id": requirements_artifact["artifact_id"],
        "requirements": requirements,
        "profile": {k: profile[k] for k in ("profile_id", "profile_version", "stack")},
    }
    return DataModelPrompt(
        system=Path(__file__).with_name("system.md").read_text(encoding="utf-8"),
        prompt=json.dumps(payload, ensure_ascii=False, allow_nan=False),
        context=deepcopy(context), requirements=deepcopy(requirements),
        requirements_artifact_id=requirements_artifact["artifact_id"],
        profile_version=profile["profile_version"],
        max_output_tokens=min(context["limits"]["max_output_tokens"], defaults["max_output_tokens"]),
        timeout_seconds=min(context["limits"].get("timeout_seconds", defaults["timeout_seconds"]),
                            defaults["timeout_seconds"], profile["limits"]["model_call"]["timeout_seconds"]),
        max_file_bytes=profile["limits"]["artifacts"]["max_file_bytes"],
    )


def _validate(model: dict, prepared: DataModelPrompt) -> str:
    try:
        content = json.dumps(model, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        size = len(content.encode("utf-8"))
    except (ValueError, TypeError):
        raise DataModelError("DATA_MODEL_INVALID_JSON") from None
    require(size <= prepared.max_file_bytes, "DATA_MODEL_FILE_BUDGET_EXCEEDED")
    validate_data_model(model, requirements=prepared.requirements,
                        requirements_artifact_id=prepared.requirements_artifact_id,
                        contract_version=prepared.context["contract_version"],
                        profile_id=prepared.context["profile_id"])
    return content


async def generate_data_model(llm: StructuredLLM, prepared: DataModelPrompt) -> LLMResult:
    """Generate a validated model, preserving usage on failure."""
    result = await llm.generate(system=prepared.system, prompt=prepared.prompt, schema=data_model_schema())
    if not result.ok:
        return result
    try:
        require(result.usage.calls <= 1, "DATA_MODEL_CALL_BUDGET_EXCEEDED")
        require(result.usage.tokens_out is None or result.usage.tokens_out <= prepared.max_output_tokens,
                "DATA_MODEL_OUTPUT_BUDGET_EXCEEDED")
        _validate(result.data, prepared)
    except ContractError as exc:
        code = str(exc) if isinstance(exc, DataModelError) else "DATA_MODEL_INVALID"
        data = None
        if isinstance(exc, DataModelError) and exc.unresolved_items:
            data = {"unresolved_items": exc.unresolved_items}
        return LLMResult(data=data, usage=result.usage,
                         error=LLMError(code, "The data model failed validation.", False))
    return result


def make_data_model_artifact(model: dict, prepared: DataModelPrompt, *,
                             artifact_id: str, attempt_id: str, version: int = 1) -> dict:
    """Serialize a validated model with caller-reserved artifact IDs."""
    content = _validate(model, prepared)
    require(artifact_id not in prepared.context["input_artifact_ids"], "DATA_MODEL_ARTIFACT_ID_REUSED")
    artifact = {
        "artifact_id": artifact_id, "attempt_id": attempt_id, "path": "data-model.json",
        "kind": "data_model", "media_type": "application/json", "version": version,
        "schema_version": "1.0", "author_agent": "architecture",
        "checksum": sha256(content.encode("utf-8")).hexdigest(), "size_bytes": len(content.encode("utf-8")),
        "related_fr_ids": sorted(item["fr_id"] for item in model["fr_coverage"]),
        "content": content,
    }
    Contracts().validate("artifact", artifact)
    return artifact
