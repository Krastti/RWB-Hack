"""LLM extraction followed by deterministic validation, gate and handoff."""

import hashlib
import json
from time import monotonic

from pydantic import ValidationError

from .llm import Completion, LLMClient, LLMError, load_json
from .models import AgentInput, AgentResult, Analysis, Artifact, Diagnostic, Usage
from .prompt import SYSTEM_PROMPT


def _answers(request: AgentInput) -> dict[str, str]:
    saved = {q.id: q.answer for q in request.previous_analysis.questions if q.answer is not None} if request.previous_analysis else {}
    return {**saved, **{a.question_id: a.value for a in request.answers}}


def validate_analysis(analysis: Analysis, request: AgentInput) -> Analysis:
    """Validate provenance and continuity; model cannot supply its own gate decision."""
    answers = _answers(request)
    for group in analysis.__dict__.values():
        if not isinstance(group, list):
            continue
        for item in group:
            for evidence in getattr(item, "evidence", []):
                source = request.requirements_text if evidence.source == "requirements" else answers.get(evidence.question_id, "")
                if evidence.quote not in source:
                    raise ValueError("evidence does not match the input snapshot")
    questions = []
    for q in analysis.questions:
        expected = answers.get(q.id)
        if q.answer is not None and q.answer != expected:
            raise ValueError("model invented a clarification answer")
        questions.append(q.model_copy(update={"answer": expected}))
    # Re-validate after merging real answers: an empty analysis cannot become successful.
    analysis = Analysis.model_validate({**analysis.model_dump(), "questions": [q.model_dump() for q in questions]})
    if request.previous_analysis:
        for field in ("functional_requirements", "nonfunctional_requirements", "roles", "questions"):
            old = {x.id: x for x in getattr(request.previous_analysis, field)}
            new = {x.id: x for x in getattr(analysis, field)}
            if not old.keys() <= new.keys():
                raise ValueError("previous IDs were removed or renumbered")
            for key, item in old.items():
                current = new[key]
                if field == "questions":
                    if item.text != current.text or item.critical != current.critical:
                        raise ValueError("question identity or criticality changed")
                elif item.evidence != current.evidence:
                    raise ValueError("requirement identity changed")
    pending = [q for q in analysis.questions if q.critical and q.answer is None]
    if len(pending) > request.limits.max_critical_questions:
        raise ValueError("critical question limit exceeded")
    return analysis


def render_markdown(analysis: Analysis) -> str:
    """Readable complete handoff; JSON blocks keep untrusted Markdown inert."""
    sections = {
        "product_goals": "Цели продукта", "in_scope": "Границы: входит в решение",
        "out_of_scope": "Границы: вне решения", "roles": "Роли и права",
        "functional_requirements": "Функциональные требования",
        "nonfunctional_requirements": "Нефункциональные требования",
        "constraints": "Ограничения", "entities": "Основные сущности",
        "scenarios": "Пользовательские сценарии", "integrations": "Интеграции",
        "security_requirements": "Требования к безопасности",
        "acceptance_criteria": "Критерии приёмки", "risks": "Риски",
        "assumptions": "Допущения", "questions": "Открытые вопросы и ответы",
    }
    lines = ["# Разбор бизнес-требований", "", "Локальный контракт: requirements-analyst/0.1.", "",
             "Вход для System Analyst Agent. Детальные API, компоненты и модель данных проектируются далее.", ""]
    for field, title in sections.items():
        lines.extend([f"## {title}", ""])
        entries = getattr(analysis, field)
        if not entries:
            lines.extend(["Во входе не указано; дополнительные требования не добавлены.", ""])
        for entry in entries:
            value = json.dumps(entry.model_dump(), ensure_ascii=False, indent=2).replace("`", "\\u0060")
            lines.extend(["```json", value, "```", ""])
    return "\n".join(lines)


def _artifacts(analysis: Analysis) -> list[Artifact]:
    fr_ids = [r.id for r in analysis.functional_requirements]
    documents = [
        ("requirements.json", "application/json", analysis.model_dump_json(indent=2) + "\n"),
        ("questions.json", "application/json", json.dumps({"schema_version": analysis.schema_version,
          "questions": [q.model_dump() for q in analysis.questions]}, ensure_ascii=False, indent=2) + "\n"),
        ("requirements-analysis.md", "text/markdown", render_markdown(analysis)),
    ]
    return [Artifact(path=path, kind=kind, content=content, related_fr_ids=fr_ids,
                     checksum=hashlib.sha256(content.encode("utf-8")).hexdigest())
            for path, kind, content in documents]


class RequirementsAnalyst:
    def __init__(self, client: LLMClient):
        self._client = client

    def run(self, request: AgentInput) -> AgentResult:
        start = monotonic()
        usage = Usage()

        def failed(code, message, retryable=False):
            return AgentResult(run_id=request.run_id, attempt=request.attempt, status="failed",
                usage=usage.model_copy(update={"duration_seconds": monotonic() - start}),
                diagnostics=[Diagnostic(code=code, message=message, retryable=retryable)])

        if request.limits.max_calls < 1:
            return failed("CALL_BUDGET_EXCEEDED", "Лимит обращений к модели исчерпан.")
        if request.previous_analysis:
            try:
                validate_analysis(request.previous_analysis, request.model_copy(update={"answers": []}))
            except ValueError:
                return failed("INVALID_PREVIOUS_ANALYSIS", "Предыдущий анализ не соответствует входному снимку.")
        data = {
            "business_requirements": request.requirements_text,
            "answers": _answers(request),
            "previous_analysis": request.previous_analysis.model_dump() if request.previous_analysis else None,
            "max_critical_questions": request.limits.max_critical_questions,
        }
        usage = usage.model_copy(update={"calls": 1})
        try:
            completion = self._client.complete(
                messages=[{"role": "system", "content": SYSTEM_PROMPT},
                          {"role": "user", "content": json.dumps(data, ensure_ascii=False)}],
                schema=Analysis.model_json_schema(),
                max_output_tokens=request.limits.max_output_tokens,
                timeout_seconds=request.limits.timeout_seconds,
            )
        except LLMError as exc:
            if exc.completion:
                usage = usage.model_copy(update={"input_tokens": exc.completion.input_tokens,
                                                 "output_tokens": exc.completion.output_tokens})
            # Only public, normalized codes; third-party exception details never escape.
            allowed = {"LLM_TIMEOUT", "LLM_UNAVAILABLE", "LLM_AUTHENTICATION", "LLM_HTTP_ERROR",
                       "LLM_INVALID_ENVELOPE", "LLM_INCOMPLETE_RESPONSE", "LLM_RESPONSE_TOO_LARGE"}
            code = exc.code if exc.code in allowed else "LLM_FAILURE"
            return failed(code, "Обращение к модели не завершилось корректным ответом.", exc.retryable)
        except Exception:
            return failed("LLM_FAILURE", "Ошибка адаптера модели.")
        try:
            if not isinstance(completion, Completion) or not isinstance(completion.content, str):
                raise ValueError("invalid adapter completion")
            usage = Usage(calls=1, input_tokens=completion.input_tokens, output_tokens=completion.output_tokens)
        except (ValueError, TypeError):
            return failed("LLM_INVALID_ENVELOPE", "Адаптер вернул некорректный результат.")
        if completion.output_tokens is not None and completion.output_tokens > request.limits.max_output_tokens:
            return failed("OUTPUT_TOKEN_BUDGET_EXCEEDED", "Модель превысила лимит выходных токенов.")
        try:
            analysis = Analysis.model_validate(load_json(completion.content))
            analysis = validate_analysis(analysis, request)
        except (ValidationError, ValueError, TypeError, RecursionError):
            return failed("INVALID_ANALYSIS", "Ответ модели не прошёл проверку схемы, источников или связей.")
        pending = any(q.critical and q.answer is None for q in analysis.questions)
        return AgentResult(run_id=request.run_id, attempt=request.attempt,
            status="waiting_for_input" if pending else "succeeded",
            output_artifacts=_artifacts(analysis), questions=analysis.questions,
            assumptions=analysis.assumptions,
            usage=usage.model_copy(update={"duration_seconds": monotonic() - start}))
