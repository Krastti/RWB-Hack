"""Strict, locally versioned DTOs. These are not the shared C-02 contract."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
FRId = Annotated[str, StringConstraints(pattern=r"^FR-[0-9]{3,}$")]
NFRId = Annotated[str, StringConstraints(pattern=r"^NFR-[0-9]{3,}$")]
QuestionId = Annotated[str, StringConstraints(pattern=r"^Q-[0-9]{3,}$")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Evidence(Model):
    source: Literal["requirements", "answer"]
    quote: Text
    question_id: QuestionId | None

    @model_validator(mode="after")
    def source_matches(self):
        if (self.source == "answer") != (self.question_id is not None):
            raise ValueError("answer evidence requires question_id; requirements forbids it")
        return self


class Finding(Model):
    text: Text
    evidence: Annotated[list[Evidence], Field(min_length=1)]


class FunctionalRequirement(Finding):
    id: FRId


class NonfunctionalRequirement(Finding):
    id: NFRId
    verification: Text


class Role(Finding):
    id: Annotated[str, StringConstraints(pattern=r"^ROLE-[0-9]{3,}$")]
    # Only explicitly stated permissions. Unknown rights become questions.
    permissions: list[Text]


class Scenario(Finding):
    id: Annotated[str, StringConstraints(pattern=r"^SC-[0-9]{3,}$")]
    role_ids: list[Text]
    related_fr_ids: Annotated[list[FRId], Field(min_length=1)]
    steps: Annotated[list[Text], Field(min_length=1)]


class AcceptanceCriterion(Model):
    id: Annotated[str, StringConstraints(pattern=r"^AC-[0-9]{3,}$")]
    related_fr_ids: Annotated[list[FRId], Field(min_length=1)]
    given: Text
    when: Text
    then: Text


class Assumption(Model):
    id: Annotated[str, StringConstraints(pattern=r"^ASM-[0-9]{3,}$")]
    text: Text
    reason: Text
    related_fr_ids: list[FRId]


class Question(Model):
    id: QuestionId
    text: Text
    reason: Text
    critical: bool
    related_fr_ids: list[FRId]
    answer: Text | None


class Risk(Model):
    id: Annotated[str, StringConstraints(pattern=r"^RISK-[0-9]{3,}$")]
    text: Text
    impact: Text
    related_fr_ids: list[FRId]


class Analysis(Model):
    schema_version: Literal["requirements-analyst/0.1"]
    product_goals: list[Finding]
    in_scope: list[Finding]
    out_of_scope: list[Finding]
    roles: list[Role]
    functional_requirements: list[FunctionalRequirement]
    nonfunctional_requirements: list[NonfunctionalRequirement]
    constraints: list[Finding]
    entities: list[Finding]
    scenarios: list[Scenario]
    integrations: list[Finding]
    security_requirements: list[Finding]
    acceptance_criteria: list[AcceptanceCriterion]
    risks: list[Risk]
    assumptions: list[Assumption]
    questions: list[Question]

    @model_validator(mode="after")
    def check_links(self):
        collections = [v for v in self.__dict__.values() if isinstance(v, list)]
        ids = [item.id for group in collections for item in group if hasattr(item, "id")]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate IDs")
        fr_ids = {r.id for r in self.functional_requirements}
        role_ids = {r.id for r in self.roles}
        for group in collections:
            for item in group:
                if hasattr(item, "related_fr_ids"):
                    if not set(item.related_fr_ids) <= fr_ids:
                        raise ValueError("unknown FR reference")
                if isinstance(item, Scenario) and not set(item.role_ids) <= role_ids:
                    raise ValueError("unknown role reference")
        covered = {fr for ac in self.acceptance_criteria for fr in ac.related_fr_ids}
        if fr_ids - covered:
            raise ValueError("each FR requires an acceptance criterion")
        if not fr_ids and not any(q.critical and q.answer is None for q in self.questions):
            raise ValueError("empty analysis requires a critical clarification")
        return self


class Answer(Model):
    question_id: QuestionId
    value: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)]


class Limits(Model):
    max_calls: Annotated[int, Field(ge=0, le=100)] = 1
    max_output_tokens: Annotated[int, Field(ge=1, le=100000)] = 8000
    timeout_seconds: Annotated[float, Field(gt=0, le=600, allow_inf_nan=False)] = 90.0
    max_critical_questions: Annotated[int, Field(ge=1, le=3)] = 3


class AgentInput(Model):
    schema_version: Literal["requirements-analyst/0.1"] = "requirements-analyst/0.1"
    run_id: UUID
    stage_id: Literal["requirements"] = "requirements"
    attempt: Annotated[int, Field(ge=1)] = 1
    input_artifact_ids: list[UUID] = Field(default_factory=list)
    contract_version: Literal["requirements-analyst/0.1"] = "requirements-analyst/0.1"
    profile_id: Text = "web-fastapi-react"
    requirements_text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100000)]
    answers: list[Answer] = Field(default_factory=list)
    previous_analysis: Analysis | None = None
    limits: Limits = Field(default_factory=Limits)

    @model_validator(mode="after")
    def check_answers(self):
        keys = [a.question_id for a in self.answers]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate answers")
        known = {q.id for q in self.previous_analysis.questions} if self.previous_analysis else set()
        if not set(keys) <= known:
            raise ValueError("answers require questions from previous_analysis")
        return self


class Usage(Model):
    calls: Annotated[int, Field(ge=0)] = 0
    input_tokens: Annotated[int, Field(ge=0)] | None = None
    output_tokens: Annotated[int, Field(ge=0)] | None = None
    duration_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0.0


class Diagnostic(Model):
    code: str
    message: str
    retryable: bool = False


class Artifact(Model):
    path: Literal["requirements.json", "questions.json", "requirements-analysis.md"]
    kind: Literal["application/json", "text/markdown"]
    content: str
    schema_version: Literal["requirements-analyst/0.1"] = "requirements-analyst/0.1"
    related_fr_ids: list[FRId]
    checksum: str


class AgentResult(Model):
    schema_version: Literal["requirements-analyst/0.1"] = "requirements-analyst/0.1"
    run_id: UUID
    stage_id: Literal["requirements"] = "requirements"
    attempt: int
    status: Literal["succeeded", "waiting_for_input", "failed"]
    output_artifacts: list[Artifact] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    usage: Usage
    diagnostics: list[Diagnostic] = Field(default_factory=list)
