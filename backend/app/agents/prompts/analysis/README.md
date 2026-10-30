# A-02: prompt системного анализа

Задача [#76](https://github.com/Krastti/RWB-Hack/issues/76). Prompt/template **1.0.0**,
C-02 **1.0**, профиль `web-fastapi-react` **1.0.0** из зависимого
[PR #97](https://github.com/Krastti/RWB-Hack/pull/97), SHA
`4ff219b22e54de4194f81df4e476d5307e2e28e6`.

Модуль готовит запрос и рендерит полный Markdown по
[шаблону анализа](../../../../../templates/analysis/README.md). Он использует
существующий интерфейс [A-01](../../llm/README.md) и
[схемы C-02](../../../../../contracts/agents/README.md).
Старый `requirements_analyst` не изменён: его перенос с 0.1 и подключение этого
модуля остаются A-03 (#77). Здесь нет графа, хранения артефактов, retry, назначения
статусов запуска или проверки готовности всего pipeline.

## Публичный интерфейс

| Функция / тип | Назначение |
|---|---|
| `build_analysis_prompt(context, business_input, *, previous_requirements=None, analysis_rules=None)` | Проверяет вход и возвращает `AnalysisPrompt` с `system`, `prompt`, самодостаточной `schema` и лимитами вызова |
| `StructuredLLM.generate(*, system, prompt, schema)` | Async Protocol с сигнатурой существующего `LLMAdapter.generate`, возвращает A-01 `LLMResult` |
| `generate_analysis(llm, prepared)` | Ровно один вызов порта; без HTTP-реализации, fallback, retry и преобразования ошибки/usage |
| `validate_draft(draft, prepared)` | Проверка схемы, связей, буквальных evidence, подтверждённых ответов и сохранения IDs |
| `render_system_analysis(draft, prepared)` | Повторяет guard и возвращает строку для `docs/system-analysis.md`, без записи файла |

`context` — C-02 AgentInput, `stage_id=analysis`. `business_input` — разрешённый
worker-ом `analysis-input`. Worker предварительно проверяет владельца/run,
checksum и версии всех input_artifact_ids; передаёт содержимое, а не пути/URL.
Для ненулевых previous/rules references соответствующее содержимое обязательно,
reference должен входить в input_artifact_ids. При возобновлении проверяются
ревизия, известность question_id и отсутствие противоречия уже сохранённому ответу.

Вход сериализуется отдельным JSON user-message. Статический system prompt из
[system.md](system.md) не форматируется пользовательским текстом. Регламент
анализа передаётся отдельным полем; он не получает приоритет системной инструкции.
Это снижение риска prompt injection, не доказательство невозможности атаки на LLM.

Схема ответа модели — внутренний [analysis-draft/1.0](../../../../../templates/analysis/draft.schema.json):
`requirements` (без изменений по C-02) + шесть предварительных `design_notes`.
Это не AgentResult и не новый формат общих артефактов. Межфайловые `$ref`
разрешаются через локальный Registry и разворачиваются с сохранением sibling
constraints; схема A-01 не требует сетевой загрузки. Неподдержанную провайдером
схему нельзя молча упрощать. Режим `json_object` выбирается явно оператором,
локальная проверка сохраняется; `json_schema` доступен через A-01, но поддержка
всех конструкций конкретной моделью требует отдельного реального вызова.

`requirements.schema_version=1.0` и предметный `context.contract_version`
различаются. `profile_version` не добавляется в закрытые C-02 структуры.
Формат внутреннего draft и prompt версионируются здесь; изменение обязательных
полей требует новой draft-версии и согласования с A-03. Не копировать в wire
0.1 поля `Q-001`, `critical`, `answer=null` или `business_requirements`.

## Вызов через A-01

Python 3.11+, запуск из корня репозитория:

```python
from backend.app.agents.llm import LLMAdapter, LLMConfig
from backend.app.agents.prompts.analysis import (
    build_analysis_prompt, generate_analysis, render_system_analysis,
)

prepared = build_analysis_prompt(context, business_input)
# server_url/model_name/api_key приходят из доверенной серверной конфигурации,
# не из бизнес-требований. Ключ не включается в prepared.
llm = LLMAdapter(LLMConfig(
    base_url=server_url, model=model_name, api_key=api_key,
    timeout_seconds=prepared.timeout_seconds,
    max_output_tokens=prepared.max_output_tokens,
    response_format="json_object",  # явный выбор оператора
))
result = await generate_analysis(llm, prepared)
# Сохранить result.usage и при ошибке; неизвестные токены остаются None.
if result.ok:
    markdown = render_system_analysis(result.data, prepared)
    requirements = result.data["requirements"]
    questions = {
        "schema_version": "1.0",
        "questions_revision": requirements["questions_revision"],
        "questions": requirements["questions"],
    }
else:
    error = result.error  # code/message/retryable; без скрытого повтора
```

Worker A-03 упаковывает `requirements`, `questions`, `markdown` соответственно
в `requirements.json`, `questions.json`, `docs/system-analysis.md`, назначает
metadata/checksum и преобразует usage A-01 в C-02. Статус `waiting_for_input`
при обязательном неотвеченном вопросе тоже назначает A-03. Нельзя сохранять
внешнюю оболочку draft как requirements.json или отправлять LLM весь AgentResult,
ожидая достоверных checksum, токенов и авторизации.

`generate_analysis` не перенастраивает переданный клиент. Его владелец обязан
применить возвращённые timeout/max_output_tokens, заранее зарезервировать общий
бюджет и ограничить параллельные вызовы. Значения — минимум лимита вызова C-05,
лимита попытки и переданного worker остатка. `max_calls=0` отклоняется до LLM.
Число раундов, retries и накопленный usage не сбрасываются этим модулем.

Ошибки формы/связей — `ContractError` с безопасным кодом `ANALYSIS_*`, без текста
требований/ответа модели. Они не являются кодами Run/HTTP. После успешного
транспортного вызова guard может отклонить draft: вызывающий код всё равно
сохраняет usage из уже полученного result, затем формирует diagnostics.
Проверка цитаты подтверждает наличие строки, не её смысл. Изменение смысла FR,
полноту анализа, согласованность регламента и безопасность проектных решений
должен дополнительно проверять аналитический узел; это не гарантия от LLM-ошибок.

## Воспроизводимый пример без модели

```sh
python -m venv /tmp/rwb-a02-env
/tmp/rwb-a02-env/bin/python -m pip install -r backend/app/agents/prompts/analysis/requirements.txt
/tmp/rwb-a02-env/bin/python contracts/agents/validate.py --check
/tmp/rwb-a02-env/bin/python -m backend.app.agents.prompts.analysis.example > /tmp/system-analysis.md
/tmp/rwb-a02-env/bin/python -m backend.app.agents.prompts.analysis.example --resume > /tmp/system-analysis.answered.md
```

Пример использует **настоящий A-01 LLMAdapter** с `httpx.MockTransport` и явно
обозначенным ручным fixture [draft.json](../../../../../templates/analysis/examples/draft.json).
Сетевого обращения и расхода токенов модели нет. Проверяются сериализация запроса,
приём ответа A-01, schema validation и рендер; качество работы реальной модели
не подтверждается. Provider usage намеренно отсутствует, а не заполнен
выдуманными значениями. Для `--resume` используется подтверждённый запрет удаления:
сохраняется UUID вопроса, ревизия 1 и прежние FR; добавляется FR-004.

requirements.txt использует точный набор C-02, HTTPX из A-01 и Pydantic для
существующего пакета DTO. У A-01 отдельный старый lock (`jsonschema==4.23.0`,
`referencing==0.36.2`), у C-02 — `4.25.1`/`0.37.0`: не устанавливать оба файла
одновременно. Для этого примера выбран C-02; общие зависимости не переписаны.

Обычные проверки примеров и прав не заменяют тесты приложения. Тестовые наборы,
deploy/CI/CD/контейнеризация, миграция старого аналитика и вызов реального LLM
с секретом не входят в данный модуль.
