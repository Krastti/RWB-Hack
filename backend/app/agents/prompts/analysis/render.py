"""Deterministic Markdown rendering; model text cannot create extra headings."""

import html
import re
from string import Template

from .prompt import TEMPLATES, AnalysisPrompt, validate_draft


def _text(value):
    value = html.escape(str(value), quote=False)
    value = re.sub(r'([\\`*_{}\[\]()#+!|~])', r'\\\1', value)
    return value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


def _refs(values):
    return ", ".join(_text(v) for v in values) or "нет"


def _findings(items):
    rows = []
    for item in items:
        label = _text(item["id"]) + ": " if "id" in item else ""
        rows.append(f"- {label}{_text(item['text'])}")
        for e in item["evidence"]:
            source = "требования" if e["source"] == "requirements" else "ответ " + e["question_id"]
            rows.append(f"  - Источник ({_text(source)}): «{_text(e['quote'])}».")
        if "permissions" in item:
            rows.append("  - Явно заданные права: " + _refs(item["permissions"]) + ".")
        if "verification" in item:
            rows.append("  - Способ проверки (не результат): " + _text(item["verification"]) + ".")
        if "steps" in item:
            rows.append(f"  - Роли: {_refs(item['role_ids'])}; FR: {_refs(item['related_fr_ids'])}.")
            rows.extend(f"  - Шаг {i}: {_text(step)}" for i, step in enumerate(item["steps"], 1))
    return "\n".join(rows) or "Во входе нет явных сведений; это не утверждение о неприменимости раздела."


def render_system_analysis(draft: dict, prepared: AnalysisPrompt) -> str:
    """Return docs/system-analysis.md text. Worker owns persistence and metadata."""
    validate_draft(draft, prepared)
    r = draft["requirements"]
    values = {k: _findings(r[k]) for k in (
        "in_scope", "out_of_scope", "constraints", "roles", "scenarios",
        "functional_requirements", "nonfunctional_requirements", "entities",
        "integrations", "security_requirements",
    )}
    values["purpose"] = _findings(r["product_goals"])
    status_labels = {"proposed": "Предложение, не требование заказчика",
                     "not_applicable": "Неприменимо с обоснованием",
                     "needs_clarification": "Нужен ответ на критичный вопрос"}
    for key, note in draft["design_notes"].items():
        values[key] = (f"**{status_labels[note['status']]}.** {_text(note['text'])}\n\n"
                       f"FR: {_refs(note['related_fr_ids'])}. Допущения: {_refs(note['assumption_ids'])}. "
                       f"Вопросы: {_refs(note['question_ids'])}.")
    values["acceptance_criteria"] = "\n\n".join(
        f"- {_text(a['id'])}; FR: {_refs(a['related_fr_ids'])}.\n"
        f"  - Дано: {_text(a['given'])}\n  - Когда: {_text(a['when'])}\n  - Тогда: {_text(a['then'])}"
        for a in r["acceptance_criteria"]
    ) or "Критерии ещё не определены; требуется уточнить целевой сценарий."
    values["assumptions"] = "\n\n".join(
        f"- {_text(a['id'])}: {_text(a['text'])}\n  - Основание: {_text(a['reason'])}\n"
        f"  - FR: {_refs(a['related_fr_ids'])}." for a in r["assumptions"]
    ) or "Допущений нет."
    values["questions"] = "\n\n".join(
        f"- {_text(q['question_id'])}: {_text(q['question'])}\n"
        f"  - Обязательный: {'да' if q['required'] else 'нет'}. Причина: {_text(q['reason'])}\n"
        f"  - FR: {_refs(q['related_fr_ids'])}.\n"
        f"  - Ответ: {_text(q['answer']) if 'answer' in q else 'не получен; не подменять допущением'}."
        for q in r["questions"]
    ) or "Открытых вопросов нет."
    values["risks"] = "\n\n".join(
        f"- {_text(x['id'])}: {_text(x['text'])}\n  - Влияние: {_text(x['impact'])}\n"
        f"  - FR: {_refs(x['related_fr_ids'])}." for x in r["risks"]
    ) or "Явные риски не выделены; это не результат проверки безопасности."
    values.update(
        profile=_text(prepared.context["profile_id"] + " / " + prepared.profile_version),
        contract_version=_text(prepared.context["contract_version"]),
        questions_revision=str(r["questions_revision"]),
        status=("есть обязательные вопросы" if any(q["required"] and "answer" not in q for q in r["questions"])
                else "черновик анализа подготовлен"),
        rules_notice=("Регламент передан отдельным артефактом; соответствие его смыслу требует проверки."
                      if prepared.has_analysis_rules else
                      "Отдельный регламент организаторов не передан; использована структура PDF без заявления о соответствии неизвестному регламенту."),
    )
    template = Template((TEMPLATES / "system-analysis.md.tmpl").read_text(encoding="utf-8"))
    return template.substitute(values)
