# Архитектурный этап A-09

[#87](https://github.com/Krastti/RWB-Hack/issues/87): фиксированная композиция
**A-05 proposal → A-06 OpenAPI → A-07 data model → A-08 UI → freeze**.
UI получает фактический новый OpenAPI, а не отдельный пример или предыдущую версию.
На успехе возвращаются пять artifacts: `architecture.json`, `contracts/openapi.yaml`,
`data-model.json`, `ui-spec.json`, `generation-plan.json`. Это допуск к следующему
этапу кодогенерации, не завершение всего Run или готовность приложения.

## Зависимости и версии

Общий C-02 `schema_version=1.0`, C-05 `web-fastapi-angular` 1.0.0;
предметная `contract_version` назначается worker до запуска и не меняется узлами.
Форматы предложений/потребителей: A-05 `a05/1.0`, A-06 `openapi-draft/1.0` и
`openapi-policy/1.0` (генератор 1.1.0), A-07 `data-model/1.0`, A-08 payload 1.0,
prompt 1.1.0/catalog 1.0.0. OpenAPI — 3.1.0. Версия внутренней карты имён —
`architecture-names/1.0`. Эти версии не заменяют Artifact.version.

Зависимости A-05–A-08 приняты в main; интеграция проверяется offline-тестами
`backend/tests/test_architecture_stage.py` с реальными адаптерами и mock transport.
При отсутствии зависимостей `RuntimeNodes` возвращает
`ARCHITECTURE_DEPENDENCIES_UNAVAILABLE` до первого LLM-вызова.

## Публичный интерфейс

Python 3.11+, запуск из корня репозитория:

```python
from backend.app.agents.graph.architecture import (
    ArchitectureStage, RuntimeNodes, prepare_architecture,
)

prepared = prepare_architecture(
    context, requirements_artifact,
    profile_bytes=frozen_profile_bytes, profile_checksum=profile_checksum,
    rules_text=frozen_rules_text, rules_checksum=rules_checksum,
    api_policy=approved_api_policy, naming_policy=approved_name_bindings,
    resolved_inputs=other_authorized_inline_artifacts,
)
outcome = await ArchitectureStage(RuntimeNodes(llm_config)).run(
    prepared, reservation=worker_reservation,
)
if outcome.ready_for_generation:
    # Worker atomically accepts the full C-02 result with all five artifacts.
    await accept_architecture_attempt(outcome.result)
```

`context` — C-02 AgentInput выбранного архитектурного этапа. Requirements Artifact
должен иметь разрешённый UUID, `author_agent=analysis`, path `requirements.json`,
inline content с точными size/checksum. `resolved_inputs` разрешает остальные
UUID входа; неразрешённые, повторные или чужие references отклоняются. Worker
до вызова проверяет владельца, Run, версии и разрешает storage_ref в точные bytes.

Передаётся уже сохранённый снимок профиля/правил с проверяемыми SHA-256. A-09
не перечитывает текущие defaults при retry. `PreparedArchitecture.inputs_json`
хранит собственную неизменяемую JSON-копию входов; создавайте её через
`prepare_architecture`, не из недоверенного checkpoint/model payload.

`reservation` — назначенные worker идентичности, **не** автоматическая запись в БД:

```json
{
  "attempt_id": "<registered canonical UUID>",
  "artifacts": {
    "architecture": {"artifact_id": "<reserved UUID>", "version": 1},
    "openapi": {"artifact_id": "<reserved UUID>", "version": 1},
    "data_model": {"artifact_id": "<reserved UUID>", "version": 1},
    "ui_spec": {"artifact_id": "<reserved UUID>", "version": 1},
    "generation_plan": {"artifact_id": "<reserved UUID>", "version": 1}
  },
  "call_ids": {
    "proposal": "<reserved UUID>", "openapi": "<reserved UUID>",
    "data_model": "<reserved UUID>", "ui_spec": "<reserved UUID>"
  }
}
```

Все UUID различны, UUID входов не переиспользуются, версии положительные. Все
outputs принадлежат одному registered attempt. Повторные вызовы `run` могут
повторно потратить бюджет: идемпотентность/lease, durable receipt и восстановление
после сбоя обеспечивает вызывающий worker. Внутри нет checkpointer или retry.

`ArchitectureOutcome.result` — обычный C-02 AgentResult. `project_tree` — отдельный
проверенный **план** дерева из A-05, не реальные созданные файлы и не неизвестное
поле закрытого architecture envelope; при отказе он пуст. `ready_for_generation`
вычисляется из принятого успеха всего этапа.

## Проверка имён и фиксация

Политики утверждает архитектор, worker проверяет их происхождение и хранит вместе
с Run. Ответ текущей модели нельзя использовать для самоодобрения. A-06 API policy
ограничивает маршруты/permissions/coverage. Карту имён описывают экспортированная
`naming_policy_schema()` и [пример](examples/naming-policy.json): checksum требований,
bindings `schema → entity`, явные пары API/storage fields, обоснованные derived fields,
nonpersistent schemas и unexposed entities. Полный набор DTO и сущностей должен
совпасть с картой; поля DTO должны быть покрыты явно. Различия `Ticket`/`tickets`
допустимы только как утверждённое сопоставление.

Проверяются существование и типы прямых полей, UUID/date/date-time formats,
nullable, varchar maxLength и совпадение enum с SQL `in` constraint. Переименование
или несовместимость возвращается диагностикой архитектору **до UI/кодогенерации**;
генераторы не исправляются скрытым изменением принятых artifacts. Первая версия
поддерживает плоские object DTO/scalar fields; nested/ref/composition bindings
требуют расширения карты. Это не полное доказательство семантики SQL/JSON Schema.

UI adapter повторно валидирует operationId, request/response field bindings,
security, роли и FR средствами A-08. Его sources должны ссылаться на точные
UUID/checksum текущих requirements и OpenAPI; profile checksum и contract_version
также сверяются. Проверенный API остаётся в тех же bytes.

Freeze добавляет реальные references в architecture core и входы каждого coding
task плана, проверяет C-02 и ownership C-05, затем рассчитывает новые checksums.
Архитектура получает технический ADR с provenance profile/rules/policies и
метаданными четырёх контрактов. Её собственный hash не включается в её содержимое.
Другие bytes получают новые UUID/revisions; изменение уже зафиксированного
предметного API/data/UI требует пересогласованной contract_version.
На отказе любой стадии внешний result не публикует частичный набор artifacts.

## Бюджеты и отказы

Для полного прохода нужны четыре допущенных вызова. Общий max_output_tokens
делится на четыре фиксированные квоты; остаток распределяется по порядку узлов.
Так неизвестный расход одного вызова не считается нулём и не освобождает квоту
следующему. A-01 получает минимум своей конфигурации и квоты, timeout — также
ограничен четвертью stage timeout и profile model_call limit. Общий Run ledger,
резервы input/output tokens, lease, deadline с учётом накладных расходов и отмена
остаются ответственностью worker/A-20.

Usage всех завершившихся вызовов складывается, null сохраняется. Даже превышение
квоты/невалидный artifact/ошибка совместимости не теряет фактический расход.
Required unanswered questions возвращают waiting_for_input без портов/LLM; для
этого допускаются обе policy=None. Неожиданное исключение или недостоверный usage
вызывают `ArchitectureExecutionError`: известный `partial_usage` и `node` доступны
worker, который должен урегулировать незавершённый резерв. Cancellation передаётся
как `asyncio.CancelledError`. Эти случаи не превращаются в фиктивный нулевой расход.

Тестовые/иные trusted adapters реализуют `ArchitectureNodes(node, NodeRequest)` и
возвращают `NodeReply`: для proposal — проверенное A-05 предложение, для остальных —
полный проверенный Artifact, C-02 usage и необязательный безопасный error_code.
Выбор узлов/исполнителей фиксирован кодом, модель не выбирает callable или owner.

## Offline-пример

После принятия A-05–A-08 и установки их зависимостей:

```sh
python -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-worker.txt
.venv/bin/python -m backend.app.agents.graph.architecture.example > /tmp/architecture-result.json
.venv/bin/python contracts/agents/validate.py agent-result /tmp/architecture-result.json
.venv/bin/python -m backend.app.agents.graph.architecture.example --waiting
```

Обычный пример использует настоящие A-01 и четыре RuntimeNodes с явно заданными
HTTPX MockTransport. Proposal/API/data drafts берутся из согласованных локальных
fixtures; [UI draft](examples/ui-draft.json) вручную согласован с BearerJWT A-06.
В runtime переименование `session → BearerJWT` не выполняется. Это четыре offline
HTTP-вызова, не реальная модель: provider tokens остаются null. Waiting-пример
не читает fixtures незавершённых генераторов и работает на базе main.

Откат — revert PR. БД/HTTP/Run lifecycle, кодогенераторы, deploy и контейнеры не изменяются. При дальнейшем
встраивании worker должен принять весь результат атомарно; только после этого
можно выдавать task references потребителям. Ранее сохранённые snapshots сохраняются.
