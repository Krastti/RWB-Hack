"""Build an A-01 request using C-02 schemas and the pinned C-05 profile."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urljoin, urldefrag

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from contracts.agents.validate import ContractError, Contracts, load, require, walk

if TYPE_CHECKING:
    from backend.app.models.dto.llm import LLMResult

ROOT = Path(__file__).resolve().parents[5]
TEMPLATES = ROOT / "templates" / "analysis"
PROFILE = ROOT / "templates" / "profiles" / "web-fastapi-angular"
PROMPT_VERSION = "1.0.1"


class StructuredLLM(Protocol):
    """The existing A-01 port; provider configuration belongs to its caller."""

    async def generate(self, *, system: str, prompt: str, schema: dict[str, Any]) -> LLMResult: ...


@dataclass(frozen=True)
class AnalysisPrompt:
    system: str = field(repr=False)
    prompt: str = field(repr=False)
    schema: dict[str, Any] = field(repr=False)
    context: dict[str, Any] = field(repr=False)
    business_input: dict[str, Any] = field(repr=False)
    previous_requirements: dict[str, Any] | None = field(repr=False)
    max_output_tokens: int
    timeout_seconds: float
    max_critical_questions: int
    profile_version: str
    has_analysis_rules: bool


@lru_cache(maxsize=1)
def _contracts() -> Contracts:
    return Contracts()


def _draft_schema() -> dict[str, Any]:
    """Inline known local schemas; preserve $ref siblings and never fetch URLs."""
    draft = load(TEMPLATES / "draft.schema.json")
    schemas = [*_contracts().schemas.values(), draft]
    registry = Registry().with_resources(
        (s["$id"], Resource.from_contents(s)) for s in schemas
    )

    def expand(node, base, active=()):
        if isinstance(node, list):
            return [expand(item, base, active) for item in node]
        if not isinstance(node, dict):
            return node
        base = urljoin(base, node.get("$id", ""))
        siblings = {k: v for k, v in node.items() if k not in {"$ref", "$id", "$schema", "$defs"}}
        result = {key: expand(value, base, active) for key, value in siblings.items()}
        if "$ref" in node:
            target = urljoin(base, node["$ref"])
            require(target not in active, "ANALYSIS_SCHEMA_CYCLE")
            resolved = registry.resolver(base_uri=base).lookup(node["$ref"])
            value = expand(resolved.contents, urldefrag(target)[0], (*active, target))
            return {"allOf": [value, result]} if result else value
        return result

    schema = expand(draft, draft["$id"])
    schema["$schema"] = draft["$schema"]
    Draft202012Validator.check_schema(schema)
    return schema


def build_analysis_prompt(
    context: dict[str, Any],
    business_input: dict[str, Any],
    *,
    previous_requirements: dict[str, Any] | None = None,
    analysis_rules: str | None = None,
    frozen_profile: dict | None = None,
) -> AnalysisPrompt:
    """Inputs are already resolved/authorized by the worker. No writes or calls."""
    contracts = _contracts()
    contracts.validate("agent-input", context)
    contracts.validate("analysis-input", business_input)
    from ...profiles.loader import validate_profile_data
    profile = validate_profile_data(frozen_profile) if frozen_profile is not None else load(PROFILE / "profile.json")
    require(context["stage_id"] == "analysis" and context["profile_id"] == profile["profile_id"],
            "ANALYSIS_UNSUPPORTED_CONTEXT")
    require(context["limits"]["max_calls"] > 0, "ANALYSIS_CALL_BUDGET_EXHAUSTED")
    for key, value in (("previous_requirements_artifact_id", previous_requirements),
                       ("analysis_rules_artifact_id", analysis_rules)):
        artifact_id = business_input[key]
        require((artifact_id is None) == (value is None), "ANALYSIS_UNRESOLVED_INPUT")
        require(artifact_id is None or artifact_id in context["input_artifact_ids"],
                "ANALYSIS_INPUT_NOT_ALLOWED")
    if analysis_rules is not None:
        require(isinstance(analysis_rules, str) and bool(analysis_rules.strip()), "ANALYSIS_EMPTY_RULES")
    if previous_requirements is not None:
        contracts.validate("requirements", previous_requirements)
        require(business_input["questions_revision"] == previous_requirements["questions_revision"],
                "ANALYSIS_STALE_QUESTIONS")
    else:
        require(business_input["questions_revision"] == 0, "ANALYSIS_MISSING_HISTORY")
    old_questions = {q["question_id"]: q for q in (previous_requirements or {}).get("questions", [])}
    for answer in business_input["answers"]:
        previous = old_questions.get(answer["question_id"])
        require(previous is not None, "ANALYSIS_UNKNOWN_ANSWER")
        require("answer" not in previous or previous["answer"] == answer["value"],
                "ANALYSIS_ANSWER_CONFLICT")

    defaults = profile["limits"]["agent"]
    max_questions = min(defaults["max_critical_questions"],
                        context["limits"].get("max_critical_questions", defaults["max_critical_questions"]))
    answered = {a["question_id"] for a in business_input["answers"]}
    unresolved = [q for q in old_questions.values() if q["required"] and "answer" not in q
                  and q["question_id"] not in answered]
    require(len(unresolved) <= max_questions, "ANALYSIS_QUESTION_BUDGET_CONFLICT")
    policy = {k: profile[k] for k in ("profile_id", "profile_version", "stack", "ui_kit")}
    payload = {
        "prompt_version": PROMPT_VERSION,
        "context": {k: context[k] for k in ("run_id", "stage_id", "attempt", "contract_version")},
        "max_critical_questions": max_questions,
        "profile_policy": policy,
        "business_input": business_input,
        "previous_requirements": previous_requirements,
        "analysis_rules": analysis_rules,
        "template": (TEMPLATES / "system-analysis.md.tmpl").read_text(encoding="utf-8"),
    }
    return AnalysisPrompt(
        system=Path(__file__).with_name("system.md").read_text(encoding="utf-8"),
        prompt=json.dumps(payload, ensure_ascii=False, allow_nan=False),
        schema=_draft_schema(), context=copy.deepcopy(context),
        business_input=copy.deepcopy(business_input),
        previous_requirements=copy.deepcopy(previous_requirements),
        max_output_tokens=min(defaults["max_output_tokens"], context["limits"]["max_output_tokens"]),
        timeout_seconds=min(profile["limits"]["model_call"]["timeout_seconds"],
                            defaults["timeout_seconds"],
                            context["limits"].get("timeout_seconds", defaults["timeout_seconds"])),
        max_critical_questions=max_questions, profile_version=profile["profile_version"],
        has_analysis_rules=analysis_rules is not None,
    )


async def generate_analysis(llm: StructuredLLM, prepared: AnalysisPrompt) -> LLMResult:
    """Validate the analysis before exposing an A-01 success to the caller.

    The caller must configure A-01 with prepared.max_output_tokens/timeout_seconds
    and reserve the run budget before calling. No retries or run state changes.
    """
    from backend.app.errors.llm import LLMError
    from backend.app.models.dto.llm import LLMResult

    result = await llm.generate(system=prepared.system, prompt=prepared.prompt, schema=prepared.schema)
    if not result.ok:
        return result
    try:
        validate_draft(result.data, prepared)
    except ContractError as error:
        feedback = [str(error)]
        if str(error) == 'ANALYSIS_UNSUPPORTED_EVIDENCE':
            confirmed = {a['question_id']: a['value'] for a in prepared.business_input['answers']}
            confirmed.update({q['question_id']: q['answer'] for q in (prepared.previous_requirements or {}).get('questions', []) if 'answer' in q})
            def check(value, path=''):
                if isinstance(value, dict):
                    if value.get('source') in {'requirements', 'answer'} and 'quote' in value:
                        source = prepared.business_input['requirements_text'] if value['source'] == 'requirements' else confirmed.get(value.get('question_id'), '')
                        if value['quote'] not in source:
                            feedback.append(path + '/quote: replace with an exact substring of the original source; do not prepend IDs or paraphrase')
                    for key, child in value.items():
                        check(child, path + '/' + str(key))
                elif isinstance(value, list):
                    for index, child in enumerate(value):
                        check(child, path + '/' + str(index))
            check(result.data)
        return LLMResult(
            usage=result.usage, candidate=result.data, validation_feedback=tuple(feedback),
            error=LLMError(
                code="ANALYSIS_INVALID_DRAFT",
                message="The model response failed analysis validation.",
                retryable=False,
            ),
        )
    return result


def validate_draft(draft: dict[str, Any], prepared: AnalysisPrompt) -> None:
    """Local shape/link/source guards, not proof of semantic correctness of a model."""
    validator = Draft202012Validator(prepared.schema, format_checker=FormatChecker())
    require(not any(validator.iter_errors(draft)), "ANALYSIS_INVALID_DRAFT")
    requirements = draft["requirements"]
    _contracts().validate("requirements", requirements)
    frs = {f["id"] for f in requirements["functional_requirements"]}
    assumptions = {a["id"] for a in requirements["assumptions"]}
    questions = {q["question_id"]: q for q in requirements["questions"]}
    confirmed = {q["question_id"]: q["answer"]
                 for q in (prepared.previous_requirements or {}).get("questions", []) if "answer" in q}
    confirmed.update({a["question_id"]: a["value"] for a in prepared.business_input["answers"]})
    for question in questions.values():
        if question["question_id"] in confirmed:
            require(question.get("answer") == confirmed[question["question_id"]], "ANALYSIS_ANSWER_LOST")
        else:
            require("answer" not in question, "ANALYSIS_INVENTED_ANSWER")
    for item in walk(requirements):
        if isinstance(item, dict) and item.get("source") in {"requirements", "answer"} and "quote" in item:
            source = (prepared.business_input["requirements_text"] if item["source"] == "requirements"
                      else confirmed.get(item["question_id"], ""))
            require(item["quote"] in source, "ANALYSIS_UNSUPPORTED_EVIDENCE")
    unresolved = {q["question_id"] for q in questions.values() if q["required"] and "answer" not in q}
    require(len(unresolved) <= prepared.max_critical_questions, "ANALYSIS_TOO_MANY_QUESTIONS")
    previous = prepared.previous_requirements
    if previous:
        for group in ("functional_requirements", "nonfunctional_requirements", "roles", "scenarios",
                      "acceptance_criteria", "assumptions", "risks"):
            require({v["id"] for v in previous[group]} <= {v["id"] for v in requirements[group]},
                    "ANALYSIS_HISTORY_LOST")
        old_questions = {q["question_id"]: q for q in previous["questions"]}
        require(old_questions.keys() <= questions.keys(), "ANALYSIS_QUESTION_HISTORY_LOST")
        for key, old in old_questions.items():
            require(not old["required"] or questions[key]["required"], "ANALYSIS_QUESTION_DOWNGRADED")
            require(old["question"] == questions[key]["question"], "ANALYSIS_QUESTION_REDEFINED")
        expected_revision = previous["questions_revision"] + bool(questions.keys() - old_questions.keys())
    else:
        expected_revision = 1
    require(requirements["questions_revision"] == expected_revision, "ANALYSIS_INVALID_REVISION")
    for note in draft["design_notes"].values():
        require(set(note["related_fr_ids"]) <= frs, "ANALYSIS_UNKNOWN_FR")
        require(set(note["assumption_ids"]) <= assumptions, "ANALYSIS_UNKNOWN_ASSUMPTION")
        require(set(note["question_ids"]) <= questions.keys(), "ANALYSIS_UNKNOWN_QUESTION")
        if note["status"] == "needs_clarification":
            require(bool(set(note["question_ids"]) & unresolved), "ANALYSIS_MISSING_BLOCKER")
