# UI specification — A-08 (#86)

Генератор `ui-spec.json` для Angular/Bootstrap. Получает подтверждённые требования,
OpenAPI и сохранённый профиль, вызывает A-01 один раз, проверяет ответ и формирует
артефакт C-02. Подключение к архитектурному этапу принадлежит A-09 (#87).

## Запуск примера

Python 3.11+, из корня репозитория:

```sh
python -m pip install -r backend/app/agents/generators/ui_spec/requirements.txt
python -m backend.app.agents.generators.ui_spec.example
python -m backend.app.agents.generators.ui_spec.example --result
```

Первый вызов выводит UI payload, второй — `AgentResult` с артефактом и usage.
Пример использует настоящий A-01 с `httpx.MockTransport`, локальные
[OpenAPI](examples/openapi.json) и [ответ модели](examples/draft.json).
Токены в примере заданы вручную; сеть и реальные учётные данные не используются.
Это демонстрация интерфейса, не источник бизнес-данных рабочего генератора.

Регрессионные проверки самого A-08 сохранены рядом с модулем:

```sh
python -B -m unittest discover -s backend/app/agents/generators/ui_spec -p 'test_*.py' -v
```

## Интерфейс

```python
from backend.app.agents.generators.ui_spec import (
    prepare_ui_spec, generate_ui_spec, build_agent_result,
)
from backend.app.agents.llm import LLMAdapter, LLMConfig

prepared = prepare_ui_spec(
    context,
    requirements_artifact=requirements_artifact,
    openapi_artifact=openapi_artifact,
    profile_content=saved_profile_content,
)
adapter = LLMAdapter(LLMConfig(
    base_url=provider_url, model=model, api_key=api_key,
    max_output_tokens=prepared.max_output_tokens,
    timeout_seconds=prepared.timeout_seconds,
))
result = await generate_ui_spec(adapter, prepared)
agent_result = build_agent_result(
    prepared, result,
    attempt_id=attempt_id, artifact_id=artifact_id, artifact_version=artifact_version,
    call_id=call_id, model=model, duration_seconds=elapsed_seconds,
)
```

`context` — C-02 `AgentInput` 1.0. `stage_id` передаётся из принятого плана:
он не обязан совпадать с `owner_agent=architecture`. Worker до вызова проверяет
владельца Run, lease, разрешённые inputs/outputs и резервирует бюджет. `attempt_id`,
`artifact_id`, version и `call_id` назначаются worker, а не моделью. При повторной
доставке используются прежние идентичности и принятый receipt.

Входные артефакты передаются с inline `content`, UUID из `input_artifact_ids`,
правильными SHA-256 и `size_bytes`. Storage refs сначала разрешает worker.
Требования: `kind=requirements`, `path=requirements.json`, JSON по C-02.
OpenAPI: `kind=openapi`, `path=contracts/openapi.yaml`, JSON или YAML; версия
`3.1.0`, `info.version` совпадает с `context.contract_version`. JSON также является
допустимым содержимым YAML-файла. Повторные ключи, YAML aliases и внешние `$ref`
отклоняются. Локальные JSON Pointer refs проверяются без сетевых запросов;
Path Item `$ref` поддерживается без siblings. Полная проверка OpenAPI остаётся A-06.

`profile_content` — точные сохранённые bytes, декодированные в UTF-8. Поддерживается
C-05 `web-fastapi-angular/1.0.0`; профиль не перечитывается с диска между попытками.
Из профиля проверяются права architecture на `ui-spec.json`, состояния, ширины,
лимиты вызова и размер артефакта. Лимиты Run и параллельные резервы контролирует worker.
Подготовленный запрос хранит копии входов; изменение исходных dict его не меняет.

`generate_ui_spec` возвращает `LLMResult`: при успехе `data` содержит полный UI
payload, при отказе — безопасный код ошибки. Ошибка модели или валидации сохраняет
usage, включая неизвестные токены. Retry и fallback отсутствуют. Отмена asyncio
распространяется вызывающему коду; учёт незавершённого вызова выполняет worker.
`build_agent_result` переводит ms в seconds и сохраняет nullable usage; результат
предназначен для атомарного принятия storage, сам модуль ничего не записывает.

## Формат и проверки

[ui-spec.schema.json](ui-spec.schema.json) — принадлежащая A-08 схема payload 1.0.
Она не изменяет закрытые C-02 envelopes или профиль 1.0.0. `contract_version`
приходит от A-09; `profile_version`, checksum профиля, prompt/catalog versions и
UUID/checksum двух исходных артефактов добавляет код после ответа модели.

Модель возвращает только:

- `screens`: уникальные ID и маршруты, FR, права, компоненты и действия;
- `flows`: роли, цель, входной экран и связанная последовательность действий;
- `non_ui_requirements`: FR без UI с обоснованием, без пересечения с экранными FR.

Каталог [catalog.json](catalog.json) 1.0.0 содержит Angular/Bootstrap-примитивы.
Каждый компонент имеет назначение, доступное имя, поведение клавиатуры/фокуса и
объявлений. Для каждого экрана обязательны loading/empty/error/forbidden/success,
а также отдельные описания ширин 360/768/1280. `table_scroll` допустим только при
наличии таблицы; горизонтальная прокрутка всей страницы не предусматривается.

API-действия ссылаются на существующий `operationId`; local-действия описывают
только UI, navigation — существующий экран. Проверяются ссылки на компоненты,
действия, экраны, известные FR/роли, непрерывность flow и достижимость экранов.
Каждый FR должен попасть в экран или иметь явное обоснование отсутствия UI.
Все FR экрана должны быть представлены хотя бы одним его компонентом или действием.
Маршруты рассматриваются как полные пути: пересечение `/tickets/new` и `/tickets/:id`
отклоняется независимо от порядка экранов. Порядок маршрутов влияет на выбор
[Angular Router](https://angular.dev/guide/routing/define-routes#how-angular-matches-urls),
поэтому A-08 требует непересекающиеся пути, а не поручает A-11 выбирать приоритет.

Права flow не шире прав его экранов/действий; действие не расширяет доступ экрана
или целевого маршрута. Учитывается стандартный
[OpenAPI security](https://spec.openapis.org/oas/v3.1.0.html#security-requirement-object):
операция наследует корневой `security`, собственное значение полностью его заменяет,
`[]` снимает требование аутентификации, `{}` разрешает анонимную альтернативу.
`action.security` сохраняет OR между альтернативами и AND между `schemes` внутри
альтернативы. Каждый элемент содержит `scheme_id` и `scopes`; для non-OAuth схем
это могут быть имена ролей по OpenAPI 3.1. Защищённая операция запрещена для
`access.mode=public`. Неизвестные схемы и OAuth2 scopes отклоняются; OpenID Connect
scopes сохраняются без обращения к discovery URL. Scopes не превращаются в ROLE-ID.

Необязательные `x-role-ids`/`x-fr-ids` дополнительно ограничивают API-действие.
Без extensions бизнес-роль сопоставляется моделью по требованиям; семантическую
правильность сопоставления проверяет review. Backend проверяет и аутентификацию,
и права на объекты; видимость кнопки их не заменяет.

## Привязки данных

Компонент объявляет именованные `data_fields`. Действие связывает их с контрактом:

```json
{
  "component_id": "COMP-003",
  "component_field": "value",
  "location": "body",
  "parameter_name": null,
  "media_type": "application/json",
  "field_path": ["description"]
}
```

Это элемент `request_bindings` действия `createTicket` из примера. Для path/query/
header/cookie задаются точный `parameter_name` и `media_type=null`; учитываются
параметры Path Item и их переопределения операцией. Для body имя параметра равно
null. `field_path` содержит имена свойств, `[]` означает целое значение, `"*"` —
элемент массива. Это путь данных, не JSON Pointer к исходному тексту OpenAPI.

`response_bindings` содержит `status_code`, `media_type`, `field_path`, `component_id`
и `component_field`. Например, ответ `listTickets` 200 с `field_path=[]` передаёт
массив в `COMP-011.rows`; `changeTicketStatus` берёт path-параметр из
`COMP-011.selected_ticket_id`, тело — из `COMP-012.value`, а ответ сохраняет в
`COMP-011.updated_ticket`. A-11 реализует эти поля как состояние компонента.

Проверяются существование полей с разрешением локальных `$ref`, направления
readOnly/writeOnly, обязательные параметры и вложенные поля тела, отсутствие
дублирующих назначений. Для каждого 2xx-ответа с content нужна привязка хотя бы
одного поля; для ответа без тела она не нужна. Типы, enum, format и required
читаются из OpenAPI, не дублируются свободным текстом в UI schema.

Поддерживаются явные properties/items и локальные refs без siblings, кроме summary/description.
Boolean/composed/conditional schemas и параметры с `content` вместо `schema`
не интерпретируются: при попытке привязки возвращается ошибка. Полная валидация
OpenAPI остаётся в A-06; здесь проверяется поддерживаемая форма привязок.
Для local/navigation действий security и обе коллекции bindings пусты.

Формат payload остаётся 1.0: первая версия A-08 ещё не принята в main.
Версия изменённого промпта — 1.1.0; каталог и общие C-02/C-05 не меняются.

`UiSpecError.code` сообщает ошибку входа до вызова модели. Невалидный ответ даёт
`LLMResult.error`: например `UI_UNKNOWN_OPERATION`, `UI_UNKNOWN_FR`,
`UI_OPERATION_ACCESS_WIDENED`, `UI_BROKEN_FLOW`, `UI_INCOMPLETE_FR_COVERAGE`.
Диагностика не содержит сырого prompt, ответа провайдера или содержимого артефактов.

## Подключение потребителей

A-09 принимает payload через `validate_ui_spec(spec, prepared)`, затем C-02 envelope. Общий
`Contracts.validate("agent-result", ...)` проверяет bytes/metadata, но не заменяет
предметную проверку UI. A-11 читает эту схему и фиксированный каталог; старый
illustrative `ui_spec` из общих fixtures не считается форматом A-08 и отклоняется.
Общие fixtures намеренно не переписываются: их совместная версия обновляется в A-09.

Модуль создаёт спецификацию интерфейса, не Angular-код. Он не подключает HTTP,
очередь, admission/ledger или хранилище; не запускает сгенерированный продукт.
