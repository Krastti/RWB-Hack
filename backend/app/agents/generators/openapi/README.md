# Генератор OpenAPI проекта A-06

Задача [#84](https://github.com/Krastti/RWB-Hack/issues/84). Модуль создаёт
`contracts/openapi.yaml` **генерируемого приложения** из принятого анализа.
Вход/выход — C-02 `schema_version=1.0`; OpenAPI — **3.1.0**;
`info.version` берётся из предметного `AgentInput.contract_version`.
Внутренний ответ модели — **openapi-draft/1.0**, генератор — **1.1.0**.
Доверенная политика архитектора — **openapi-policy/1.0**.
Формат API платформы `contracts/platform.openapi.yaml` не меняется.

## Интерфейс

```python
from backend.app.agents.generators.openapi import prepare_openapi, generate_openapi
from backend.app.agents.llm import LLMAdapter, LLMConfig

prepared = prepare_openapi(
    context, requirements_artifact, profile=frozen_profile,
    api_policy=approved_api_policy,
)
config = LLMConfig(
    base_url=server_url, model=model_name, api_key=secret_from_environment,
    max_output_tokens=prepared.max_output_tokens,
    timeout_seconds=prepared.timeout_seconds,
    response_format="json_object",
)
# Worker предварительно резервирует общий бюджет и проверяет отмену/lease.
result = await generate_openapi(
    LLMAdapter(config), prepared, model=config.model,
    attempt_id=registered_attempt_id,
    artifact_id=reserved_artifact_id,
    artifact_version=next_path_version,
)
# Сохранить result.usage и diagnostics также при status=failed.
```

`context` — существующий C-02 AgentInput. `stage_id` назначает доверенный
архитектурный план: модуль повторяет его, не выбирает узел или исполнителя по
имени. `requirements_artifact` — полный inline Artifact с `kind=requirements`,
`path=requirements.json`, разрешённым UUID и проверяемыми UTF-8 checksum/size.
Worker предварительно проверяет владельца, принадлежность Run и версии; для
`storage_ref` resolver возвращает тот же артефакт с точным `content`.

`api_policy` — обязательный keyword argument с утверждённой архитектором политикой
для точного `requirements_checksum`. Это доверенный вход worker, а не ответ этой
же модели, пользовательское пожелание или автоматически скопированный draft.
C-02 хранит permissions и FR текстом: безопасно вывести из них машинные разрешения
или право исключить FR из HTTP API одной проверкой JSON Schema невозможно.
Поэтому вызывающий архитектурный этап передаёт явную политику до вызова A-01;
без неё готовый анализ отклоняется с `OPENAPI_POLICY_REQUIRED` до расхода бюджета.
Для неотвеченных required questions допускается `api_policy=None`: сначала
возвращается waiting_for_input; после ответа нужна политика нового snapshot.

Формат политики описан экспортированной `policy_schema(Contracts())` и
[ручным примером](examples/api-policy.json). Он содержит:

- `schema_version`, `requirements_checksum` — версия и привязка к исходным bytes;
- `operations` — утверждённые operation_id, method/path, related_fr_ids,
  authentication, authorization_rules; `grants` связывает role_id с непустыми
  `permission_indices` в исходном `roles[].permissions`;
- `security_requirement_indices` у операции — ссылки на исходные security rules;
  их точный текст должен входить в утверждённые authorization_rules;
- `coverage` — для каждого FR утверждённые kind, operation_ids и reason.

Политика проверяется по исходным требованиям: неизвестные permissions/роли/FR,
пропуски и несогласованные ссылки отклоняются. Модель не может расширить/убрать
роль, сменить public/bearer, убрать owner restriction, переименовать маршрут,
добавить или пропустить операцию, объявить FR constraint/outside_api самостоятельно.
Изменение API surface требует пересогласования политики архитектором. Worker
проверяет происхождение политики и сохраняет её для данного Run; контроль этого
источника нельзя возлагать на модель, производящую draft.

`profile` — уже зафиксированная конфигурация данного Run, не пользовательский
путь и не новое чтение defaults при retry. Проверяются её локальная JSON Schema,
profile_id, OpenAPI version и architecture grant на выход. Первый поддержанный
профиль — `web-fastapi-angular`. Profile checksum/version сохраняет orchestrator
в своём контексте; дополнительные поля в закрытый C-02 не добавляются.

`prepared.max_calls`, `max_output_tokens`, `timeout_seconds` — минимум бюджета
вызова и соответствующих пределов профиля. Переданный клиент не перенастраивается:
его владелец обязан применить эти значения и общий остаток Run. Нет скрытых retry.
`max_calls=0` возвращает failed/BUDGET_EXCEEDED без вызова. Неотвеченный required
вопрос возвращает waiting_for_input с исходными UUID/ответами и нулём calls.
Новых вопросов или подтверждений пользователя генератор не придумывает.

`generate_openapi` возвращает обычный dict AgentResult. Успех содержит один
Artifact: `kind=openapi`, `media_type=application/yaml`, `author_agent=architecture`,
путь `contracts/openapi.yaml`. UUID попытки/артефакта и версию предоставляет worker;
checksum/size вычисляются кодом по фактическим байтам. Существующие UUID входов
нельзя переиспользовать для выхода. Записи файлов, БД и переходов Run здесь нет.

## Формат и проверки

Модель возвращает структурированный draft: title/description, именованные JSON
Schema DTO, HTTP operations, design assumptions и API coverage всех FR. Python
детерминированно собирает документ и проверяет его `OpenAPIV31SpecValidator`.
`render_openapi(draft, prepared)` выполняет те же проверки без модели и возвращает
YAML; нарушение входа/контракта вызывает `OpenAPIError` с безопасным кодом.

Поддерживаются GET/POST/PUT/PATCH/DELETE, JSON request/response, path/query/header
parameters, public/bearer operations и именованные component schemas. Все `$ref`
имеют вид `#/components/schemas/Name`; внешние ссылки и rebasing/dynamic keywords
запрещены до запуска OpenAPI validator. YAML модели не разбирается и код не
исполняется. Файл ограничен меньшим из profile max_file_bytes и 1 MiB; draft —
32 уровнями и 20000 JSON-узлами, до 100 операций/DTO. Остальные форматы тел,
callbacks/webhooks, OAuth discovery и multi-file refs требуют новой версии draft.

Проверяются уникальность operationId и method/path, эквивалентные templated paths,
обязательность/совпадение path parameters, дубли параметров/ответов, тело 204,
JSON Schema/диапазоны полей, локальные references и известность FR/ROLE/ASM IDs.
Идентификаторы и имена параметров не допускают перевод строки в конце. Числовые
границы проверяются во всех сочетаниях minimum/exclusiveMinimum и
maximum/exclusiveMaximum; равенство разрешено только при обеих закрытых границах.
Для `type=integer` также проверяется наличие целого числа в интервале.
`Idempotency-Key` можно явно описать как header parameter; численные квоты и
предметные решения нельзя выдавать за факты без основания/допущения.

Каждая операция содержит `x-fr-ids`, `x-role-ids`, `x-authorization-rules`,
`x-assumption-ids` и `x-integration-indices`. Роли разрешены только у bearer
операций; объектные ограничения описываются отдельно. Это контракт для будущего
backend-генератора, а не уже исполняемая авторизация.
`x-role-permissions` содержит только утверждённые ссылки и точные permissions из
требований; `x-security-requirement-indices` сохраняет их связь с security rules.
`x-api-policy` фиксирует версию и SHA-256 канонического JSON политики
(`sort_keys=True`, `ensure_ascii=False`, `separators=(",", ":")`).

Coverage (`x-requirement-coverage`) перечисляет каждый FR ровно один раз и
согласован с операциями в обе стороны. `operation` требует operationId;
`constraint` описывает правило/запрет; `outside_api` объясняет часть вне HTTP API,
не объявляя её реализованной. DTO могут иметь `x-fr-ids`/`x-assumption-ids`.
Kind и ссылки сверяются с доверенной политикой, reason берётся из неё.
`x-integrations` берётся только из указанных существующих entries входного
requirements; для пустого списка ссылаться на интеграцию нельзя.

Compiler добавляет общий Error envelope `error.{code,message,details,request_id}`,
422/500 и 401/403 для bearer; доменные ошибки и 429 задаются явно. Для 429 есть
Retry-After. Относительный `/api/v1`, JSON, Error envelope и JWT-соглашение записаны
отдельным техническим ASM с новым ID. Предметные defaults остаются в явных
design assumptions. Выдача JWT/IdP не изобретается и требует согласования A-09.
NFR, security constraints, роли и допущения остаются в расширениях документа.

Ошибки A-01 возвращаются с безопасной диагностикой и фактическим usage; ошибка
draft/specification не публикует артефакт, но не теряет расход. Неизвестные токены
остаются null. Модуль преобразует A-01 latency_ms в C-02 seconds, назначает
call_id и не сбрасывает накопленный Run ledger. Некорректный порт usage,
неожиданное исключение транспорта и cancellation передаются worker: они не
маскируются фиктивным успехом или нулевым расходом.

## Воспроизводимый пример

Python 3.11+, из корня репозитория:

```sh
python -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-worker.txt
.venv/bin/python -m pip check
.venv/bin/python -m backend.app.agents.generators.openapi.example > /tmp/openapi.yaml
.venv/bin/python -m backend.app.agents.generators.openapi.example --result > /tmp/openapi-result.json
.venv/bin/python contracts/agents/validate.py agent-result /tmp/openapi-result.json
.venv/bin/python -m backend.app.agents.generators.openapi.example --waiting
```

Пример использует C-02 fixtures заявок и [ручной draft](examples/draft.json)
с отдельной ручной политикой через настоящий A-01 с `httpx.MockTransport`. Это явно выделенный offline transport,
не основной runtime и не результат реальной модели. Provider usage отсутствует;
один транспортный вызов отмечен `model=offline-fixture`, tokens=null. `--waiting`
не вызывает даже транспорт. Пример показывает подачу/просмотр/смену статуса,
owner restriction и FR-004 как запрет без DELETE. UUID/status enum — явный ASM.

Зависимости модуля подключены к worker. `openapi-spec-validator==0.8.5` совместим
с общими pins C-02; собственные несовместимые версии jsonschema/referencing не
вводятся. Общие контракты и сохранённые артефакты не переписываются.

## Подключение и границы

A-09 (#87) предоставляет утверждённую API policy, получает этот результат вместе с A-05/A-07/A-08, согласует имена,
фиксирует единую contract_version и references в architecture.json. Самостоятельный
выход A-06 не означает готовности всего архитектурного этапа или Run. Изменение
согласованного контракта создаёт новый artifact/version; его нельзя скрыто менять
после запуска потребителей.

Структурные проверки не доказывают правильную интерпретацию бизнес-текста:
архитектор должен проверить полноту полей, смысл permissions, запреты и assumptions.
Здесь нет генерации предметного backend, тестов приложения, CI/CD, сканеров,
контейнеризации или deploy. Откат — revert этого additive PR и исключение вызова
из будущей композиции; ранее принятые snapshots не удаляются.
