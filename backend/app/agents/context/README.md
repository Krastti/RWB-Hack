# A-30 / #104 — подготовка входного контекста

Зависимость: A-29 (#103, PR #143), C-02/C-05 1.0. Модуль не вызывает LLM,
не выбирает следующий этап, не пишет БД/артефакты и не резервирует бюджет.

`ContextBuilder(resolver, max_context_bytes=...).prepare(request, owner_id=...,
owner_agent=..., profile=..., references=..., execution_inputs=...,
related_fr_ids=..., accepted_result_references=..., conditions=...) -> PreparedContext`.

`request` — закрытый C-02 AgentInput. `references` — точные C-02 metadata всех
разрешённых входов из frozen Run/task. Их ID должны совпадать с AgentInput.
`profile` — FrozenProfile A-29, восстановленный по доверенным checksum Run.
`conditions` — доверенные булевы условия из frozen Run/task, те же, что
используются при проверке плана A-29. Например, `has_integrations=True`
разрешает условные директории интеграций; отсутствие условия запрещает их.

Resolver получает owner_id/run_id/artifact_id и возвращает `ResolvedArtifact`
с фактическими bytes, metadata, contract_version и признаком accepted. Проверка
владельца и receipt выполняется реальным storage adapter до возврата данных.
accepted означает принятие предметной схемы производителем/A-09, а не только
прохождение generic C-02. Контекст не заменяет OpenAPI/data/UI validators.
В примере accepted задаётся явно для ручных fixtures; production fallback нет.

Проверяются scope, exact metadata, SHA-256/UTF-8 size, профильные grants, C-02,
единая contract_version и структурные refs архитектуры/плана. Неизвестные ссылки
не догружаются автоматически. `ContextError.code` не включает бизнес-текст.

Обязательные kinds:

| Роль | Входы |
|---|---|
| analysis | business_requirements |
| architecture | requirements |
| frontend | requirements, openapi, ui_spec |
| backend | requirements, openapi, data_model |
| migrations | requirements, data_model |
| integrations | requirements, architecture, openapi |
| documentation, assembly | requirements, architecture, generation_plan |

Документации/сборке дополнительно передаются все входы по структурным refs
architecture/plan и metadata уже принятых результатов. Отсутствие любого
обязательного input останавливает подготовку до вызова модели.
Для documentation worker обязан передать `accepted_result_references`: полный
словарь ID и metadata принятых результатов разработки из доверенного frozen
Run/task (пустой словарь допустим). Он отделён от `references`: содержимое этих
результатов не загружается и в LLM передаётся только их metadata. Worker отвечает
за полноту, scope и факт принятия инвентаря; builder проверяет схему и grants.

`related_fr_ids` выделяет FR и замыкает связанные сценарии/критерии/ограничения.
Полные требования, NFR, роли/permissions и ограничения сохраняются во входе:
фильтр не удаляет общий контекст. При превышении серверного лимита UTF-8 bytes
возвращается CONTEXT_SIZE_LIMIT, без молчаливой обрезки. Это лимит транспорта,
не оценка токенов; admission/token budget остаётся у A-20.
Для входов исполнения действует отдельный `max_execution_input_bytes` (по
умолчанию 1 МиБ), в том числе для конфигураций, которые не передаются модели.

`execution_inputs` содержит точные bytes четырёх серверных входов: prompt,
template, model_configuration, compiler_configuration (последние два — JSON
objects). Их берёт worker из реально применяемой конфигурации,
не из ответа модели/запроса пользователя. Политика профиля и prompt образуют
`system`; бизнес-артефакты находятся только в JSON `data`. PreparedContext
не исполняет содержимое. Не передавайте data в system при подключении LLM.
Конфигурации модели и компилятора не включаются в system/data; в manifest
попадают только их хеши и размеры. Для воспроизведения worker отдельно хранит
исходные bytes в приватном хранилище.
`context_checksum` идентифицирует сообщения system/data; отдельный
`execution_fingerprint` включает его и хеши всех входов исполнения и подходит
для различения запусков с разными настройками.

`manifest` фиксирует metadata/checksum входов, профиль/правила, конфигурацию,
contract_version, Run/stage/attempt и hash/size канонической пары system/data.
`system`/`data` сохраняют фактические строки для воспроизведения; manifest —
индекс, а не замена immutable bytes. Worker сохраняет их в private input store;
они не предназначены для публичных operational logs. Порядок входных IDs не
влияет на порядок артефактов; identity/attempt входят в хеш контекста явно.

Из корня после подключения A-29:

```sh
python -m pip install -r backend/app/agents/context/requirements.txt
python -m backend.app.agents.context.example
```

Откат: исключить вызов builder, сохранить фактические inputs уже принятых
попыток. Готовность модуля не подтверждает готовность A-09/генераторов/полного Run.
