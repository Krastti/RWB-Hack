"""A-05: one bounded A-01 call, validated design proposal and deterministic plan."""

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Resource

from backend.app.agents.llm import LLMAdapter, LLMConfig, LLMError, LLMResult
from contracts.agents.validate import Contracts, ContractError, load, loads, validate_semantics, walk

from .planning import make_plan, make_tree
from .prompt import SYSTEM, response_schema


ROOT = Path(__file__).resolve().parents[5]
PROFILE_SCHEMA = ROOT / "templates/profiles/web-fastapi-angular/profile.schema.json"


def _require(condition, code):
    if not condition:
        raise ContractError(code)


def _copy_json(value):
    return loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _failure(error, *, fallback, usage=None):
    code = str(error)
    if not re.fullmatch(r"ARCHITECTURE_[A-Z0-9_]+", code):
        code = fallback
    arguments = {"error": LLMError(code, "Architecture input or proposal failed validation.")}
    if usage is not None:
        arguments["usage"] = usage
    return LLMResult(**arguments)


class ArchitectureNode:
    """Return an internal A-05 proposal, not a frozen C-02 architecture envelope.

    The caller authorizes resolved input artifacts and supplies trusted, pinned
    profile/rules bytes from A-29. This module never loads a user-selected path,
    persists artifacts, schedules a task, or declares the Run completed.
    """

    def __init__(self, config: LLMConfig, *, transport=None, stage_id="architecture"):
        self._config = config
        self._transport = transport
        self._stage_id = stage_id
        self._contracts = Contracts()
        self._schema = response_schema(self._contracts)
        self._validator = Draft202012Validator(self._schema, format_checker=FormatChecker())
        profile_schema = load(PROFILE_SCHEMA)
        registry = self._contracts.registry.with_resource(profile_schema["$id"], Resource.from_contents(profile_schema))
        self._profile_validator = Draft202012Validator(profile_schema, registry=registry,
                                                       format_checker=FormatChecker())

    async def generate(self, context: dict, *, requirements_artifact: dict,
                       profile_bytes: bytes, profile_checksum: str,
                       rules_text: str, rules_checksum: str) -> LLMResult:
        """No retries. Error results retain A-01 usage, including unknown tokens."""
        try:
            context, artifact = _copy_json(context), _copy_json(requirements_artifact)
            self._contracts.validate("agent-input", context)
            _require(type(profile_bytes) is bytes and sha256(profile_bytes).hexdigest() == profile_checksum,
                     "ARCHITECTURE_PROFILE_CHECKSUM_MISMATCH")
            profile = loads(profile_bytes.decode("utf-8"))
            _require(self._profile_validator.is_valid(profile), "ARCHITECTURE_INVALID_PROFILE")
            _require(context["profile_id"] == profile["profile_id"]
                     and context["stage_id"] == self._stage_id, "ARCHITECTURE_CONTEXT_MISMATCH")
            _require(isinstance(rules_text, str) and bool(rules_text.strip())
                     and sha256(rules_text.encode()).hexdigest() == rules_checksum,
                     "ARCHITECTURE_RULES_CHECKSUM_MISMATCH")
            _require(context["limits"]["max_calls"] > 0, "ARCHITECTURE_CALL_BUDGET_EXHAUSTED")
            self._contracts.validate("artifact", artifact)
            _require(artifact["artifact_id"] in context["input_artifact_ids"]
                     and artifact["kind"] == "requirements" and artifact["path"] == "requirements.json"
                     and artifact["author_agent"] == "analysis" and "content" in artifact,
                     "ARCHITECTURE_REQUIREMENTS_NOT_ALLOWED")
            _require(artifact["size_bytes"] <= profile["limits"]["artifacts"]["max_file_bytes"],
                     "ARCHITECTURE_INPUT_TOO_LARGE")
            requirements = loads(artifact["content"])
            self._contracts.validate("requirements", requirements)
            _require(not any(q["required"] and "answer" not in q for q in requirements["questions"]),
                     "ARCHITECTURE_BLOCKED_BY_QUESTIONS")
            _require(bool(requirements["functional_requirements"]), "ARCHITECTURE_EMPTY_REQUIREMENTS")
            defaults = profile["limits"]["agent"]
            limits = {
                "max_output_tokens": min(self._config.max_output_tokens, defaults["max_output_tokens"],
                                         context["limits"]["max_output_tokens"]),
                "timeout_seconds": min(self._config.timeout_seconds, defaults["timeout_seconds"],
                                       profile["limits"]["model_call"]["timeout_seconds"],
                                       context["limits"].get("timeout_seconds", defaults["timeout_seconds"])),
            }
            payload = {
                "context": {key: context[key] for key in ("run_id", "stage_id", "attempt", "contract_version")},
                "requirements": requirements,
                "profile_policy": {key: profile[key] for key in (
                    "profile_id", "profile_version", "stack", "ui_kit", "ownership", "required_artifacts")},
                "profile_rules": rules_text,
            }
        except (ContractError, ValueError, TypeError, KeyError) as error:
            return _failure(error, fallback="ARCHITECTURE_INVALID_INPUT")

        adapter = LLMAdapter(replace(self._config, **limits), transport=self._transport)
        result = await adapter.generate(system=SYSTEM, prompt=json.dumps(payload, ensure_ascii=False),
                                        schema=deepcopy(self._schema))
        if not result.ok:
            return result
        try:
            _require(result.usage.tokens_out is None or result.usage.tokens_out <= limits["max_output_tokens"],
                     "ARCHITECTURE_OUTPUT_BUDGET_EXCEEDED")
            design = _copy_json(result.data)
            _require(self._validator.is_valid(design), "ARCHITECTURE_INVALID_DESIGN")
            validate_semantics("architecture", design)
            fr_ids = {f["id"] for f in requirements["functional_requirements"]}
            for item in walk(design):
                if isinstance(item, dict) and "related_fr_ids" in item:
                    _require(set(item["related_fr_ids"]) <= fr_ids, "ARCHITECTURE_UNKNOWN_FR")
            _require({fr for c in design["components"] for fr in c["related_fr_ids"]} == fr_ids,
                     "ARCHITECTURE_UNCOVERED_FR")
            _require(not requirements["integrations"] or design["integrations"],
                     "ARCHITECTURE_INTEGRATIONS_LOST")
            plan = make_plan(context, artifact["artifact_id"], requirements, profile, design)
            self._contracts.validate("generation-plan", plan)
            tree = make_tree(profile, design)
            core = {"schema_version": "1.0", "contract_version": context["contract_version"],
                    "profile_id": context["profile_id"], "requirements_artifact_id": artifact["artifact_id"],
                    "purpose": "\n".join(g["text"] for g in requirements["product_goals"])
                               or "\n".join(f["text"] for f in requirements["functional_requirements"]),
                    "in_scope": [item["text"] for item in requirements["in_scope"]],
                    "out_of_scope": [item["text"] for item in requirements["out_of_scope"]], **design}
            proposal = {
                "draft_version": "a05/1.0", "run_id": context["run_id"],
                "stage_id": context["stage_id"], "attempt": context["attempt"],
                "contract_version": context["contract_version"], "profile_id": profile["profile_id"],
                "profile_version": profile["profile_version"], "profile_checksum": profile_checksum,
                "rules_checksum": rules_checksum, "requirements_checksum": artifact["checksum"],
                "architecture_core": core, "generation_plan": plan, "project_tree": tree,
                "ready_for_generation": False,
                "requires_contracts": ["openapi", "data_model", "ui_spec"],
            }
            return LLMResult(data=proposal, usage=result.usage)
        except (ContractError, ValueError, TypeError, KeyError) as error:
            return _failure(error, fallback="ARCHITECTURE_INVALID_DESIGN", usage=result.usage)
