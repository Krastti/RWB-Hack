# Агенты, контракты и профиль

Здесь описаны существующие A-01/A-02/A-04/A-07, legacy analyst и правила будущих
потребителей. Наличие интерфейса или схемы не означает готового multi-agent runtime.
Нормативные источники: [C-02 JSON Schema Draft 2020-12](../contracts/agents/v1/),
[C-03 lifecycle](../contracts/lifecycle.md), [C-01 HTTP](../contracts/platform.openapi.yaml),
[C-05 profile.json](../templates/profiles/web-fastapi-angular/profile.json) и
[profile.schema.json](../templates/profiles/web-fastapi-angular/profile.schema.json).

## Целевой конвейер и ответственность

Требования → аналитик → при необходимости clarification gate → архитектор
(API/data/UI/plan) → независимые frontend/backend/migrations/integrations/docs
задачи → сборщик → неизменяемый snapshot. Model output не выбирает инструмент,
owner, путь на сервере, следующий узел или Run.status. Worker предварительно
разрешает входы и проверяет права; orchestrator управляет таблицами C-03.

| ID / узел | Вход и ожидаемый результат | Фактическая граница |
|---|---|---|
| A-01 LLM Adapter | Prompt/schema → structured result/usage/error | Есть transport; не retry/ledger |
| A-02 Prompt | C-02 context + resolved analysis-input → проверенный draft/Markdown | Есть, не AgentResult node |
| A-03 Analyst | A-01/A-02 → requirements/questions/system-analysis artifacts | Legacy ядро 0.1 не заменяет bridge C-02 |
| A-04 LangGraph | AnalysisNode/ArtifactStore/checkpointer → анализ/ожидание/resume | Есть внутренний граф без общего worker/HTTP persistence |
| A-05–A-09 Architecture | Анализ → architecture, OpenAPI, data-model, ui-spec, generation-plan единой версии | Есть генератор data-model; общий архитектурный этап ещё не подключён |
| A-10–A-18 Coding | Frozen API/data/UI/plan → предметные frontend/backend/models/migrations/adapters/docs и fan-out/join | Правила профиля, не готовые агенты |
| A-19 Assembly | Принятые bytes/metadata → manifest/traceability/snapshot | Правила проверки; не замена build/test |
| A-20 Budget | Общие резервы/usage → допуск и причины остановки | Полный ledger/admission требует интеграции |

A-06 владеет предметным OpenAPI, A-07 — data schema, A-08 — UI spec; A-09
фиксирует их совместную contract_version до запуска потребителей. A-10/A-12
предоставляют шаблоны frontend/backend; A-11/A-13 реализуют код, A-14 — модели/
миграции, A-15 — внешние adapters, A-16 — docs, A-17 — fan-out/join, A-18 — wiring.
Конфликт API возвращается архитектору, не исправляется генератором скрыто.
Mobile Kotlin/Compose (X-01/X-02), PostGIS (X-03), geo-adapters/session events
(X-04/X-05), mobile graph/manifest/UI (X-06/X-07) — отдельные расширения.

## Версии и C-02 envelope

| Идентичность | Назначение |
|---|---|
| C-01 2.1.0, HTTP `/api/v1` | Версия API платформы; номер HTTP-префикса не менялся |
| C-02 schema_version=1.0 | Wire-формат agent payloads, не предметный контракт |
| C-03/event data schema_version=1.0 | Состояния, события, метрики |
| profile_id=web-fastapi-angular | Активный стек, не версия профиля |
| profile_version=1.0.0; profile schema_version=1.0 | Набор defaults/правил и формат конфигурации |
| contract_version | Версия предметных API/data/UI одного проекта, например `1` |
| Artifact.version | Ревизия bytes логического пути; не schema_version |
| Run.version / graph state_version / generation / seq | Разные внутренние CAS/формат состояния/lease/порядок событий |

C-02 закрывает публичные объекты для лишних полей. `$id` на `.invalid` —
идентификатор; схемы разрешаются через локальный Registry без сети. Не добавляйте
profile_version/checksum, task_id или внутренние lease поля в закрытый envelope.
Обязательные AgentInput: schema_version, run_id, stage_id, attempt,
input_artifact_ids, contract_version, profile_id, limits. `attempt` ≥1;
max_calls/max_output_tokens обязательны, timeout/max_critical_questions опциональны.
max_calls=0 допускается для детерминированного этапа, max_output_tokens должен
быть положителен; JSON Schema не подставляет defaults автоматически.

Input artifact UUID — не URL, server path или команда. Worker проверяет owner/Run,
версии, checksum и только затем разрешает bytes. `analysis-input` содержит
requirements_text, подтверждённые answers и references на предыдущие requirements/
регламент. Непустые answers требуют previous reference и questions_revision>0;
ссылки должны входить в input_artifact_ids. Правила анализа выбирает сервер,
не инструкция в требованиях пользователя.

AgentResult повторяет Run/stage/attempt/contract и содержит status, output_artifacts,
questions, assumptions, usage, diagnostics. Succeeded требует минимум артефакт,
нет error diagnostics/неотвеченных required questions; waiting_for_input требует
такой вопрос; failed — error diagnostic; cancelled/skipped — причина остановки/
неприменимости. Pending/running не являются завершённым AgentResult. Budget
возвращается как failed diagnostic, Run budget_exceeded устанавливает orchestrator.
Валидность общей схемы не заменяет completeness профиля или смысловую оценку.

Схемы: common, agent-input/result, artifact, analysis-input, requirements,
questions, architecture, generation-plan, manifest, traceability. Common — набор
defs, не самостоятельный payload. Detailed data-model/ui-spec schemas не входят
в общий C-02: их должны зафиксировать архитектурные потребители. Illustrative
payloads не доказывают их валидацию; неподдерживаемый формат блокирует исполнение.

Question: question_id UUID, question/reason/required/related_fr_ids, optional answer.
Неотвеченный answer **опускается**, не null/пустая строка. UUID и FR/NFR/ROLE/SC/AC/
ASM/RISK IDs стабильны между уточнениями. Required блокирует только без answer;
публикация прежних вопросов не очищает подтверждённые ответы. Только сервер
принимает полный пакет обязательных ответов текущей ревизии, модель не подтверждает
их за пользователя. Новые вопросы повышают revision; ответы сами по себе — нет.

## Артефакты, usage и безопасность

Artifact содержит artifact_id/attempt_id/path/kind/media_type/version/schema_version/
author_agent/checksum/size_bytes/related_fr_ids и ровно content либо storage_ref.
Reference — metadata без content/storage_ref. Kind — назначение, MIME — фактический
формат. Text content кодируется UTF-8; checksum — SHA-256 **точных** bytes, 64
lowercase hex без префикса; size_bytes не число символов. JSON-пробелы/новые строки
не нормализуются. Для binary нужен storage_ref с checksum бинарных bytes.

IDs/version/checksums назначает и проверяет worker, не LLM. Другие bytes получают
новый artifact_id и следующую version пути; immutable ID не переиспользуется.
Переход content→storage_ref не меняет идентичность. Storage_ref — opaque relative
ключ доверенного adapter, не произвольная ссылка. Manifest не включает самого
себя: собственный checksum хранится вне снимка, файл экспортируется отдельно.
Traceability перечисляет **каждый** FR, включая unimplemented/partial с причиной.
Prepared означает подготовку профиля, не PASS build/test/security/deploy.

C-02 usage.calls равен длине уникальных model_calls. Каждый вызов имеет call_id,
model/outcome/input_tokens/output_tokens/duration_seconds. Начатый ошибочный/
отменённый вызов учитывается; отказ до сети — 0 новых calls. Provider null tokens
остаются null. Сводная сумма известна только когда все составляющие известны;
при нуле calls сумма 0. Этап duration_seconds исключает ожидание человека и не
обязан равняться сумме parallel calls. В C-01 Metrics — целые суммы известных
токенов; completeness публикуется отдельно в том же event-снимке, не выводится
из нуля. Latency ms A-01 переводится в seconds C-02 явно. Дедупликация call_id и
консервативный резерв — обязанности общего accounting, не helper UI.

Diagnostic: безопасные code/message/severity/retryable/related_fr_ids и optional
artifact_id. Retryable — рекомендация, не команда. Не включайте raw prompts,
provider bodies, chain-of-thought, traceback, URL с секретами и keys. Evidence
должно быть буквальной непустой цитатой требований/подтверждённого ответа, но
наличие цитаты само по себе не доказывает правильную интерпретацию.

Business input и LLM output — данные, не инструменты/политика. Модель не получает
ключ транспорта, файловый доступ или полномочия менять grants. Generated code
не исполняется в API/worker. Пути relative POSIX ASCII `[A-Za-z0-9_.-/]`, ≤512,
без абсолютных путей, `.`/`..` сегментов, `//`, trailing slash, backslash, drive,
URL/percent encoding. Storage дополнительно проверяет containment, symlink escape,
специальные файлы, дубли и case collisions; архив содержит только обычные файлы снимка.

## A-01: LLM Adapter

[adapter.py](../backend/app/agents/llm/adapter.py) предоставляет async
`LLMAdapter(LLMConfig(...)).generate(system=...,prompt=...,schema=...) -> LLMResult`.
Типы доступны из `backend.app.agents.llm` и прежних transport imports:
LLMConfig в config/llm.py, LLMResult/LLMUsage в models/dto/llm.py, LLMError в errors/llm.py.
Generate делает максимум один Chat Completions HTTP-запрос, не retry и не DB write;
CancelledError распространяется наружу. Schema — самодостаточный объект, локальные
refs допустимы, внешняя загрузка запрещена. Перед отправкой копируется/проверяется;
ответ локально проверяется с FormatChecker. Duplicate JSON keys, NaN/Infinity,
Markdown wrappers, неверная схема/finish reason/usage отклоняются.

LLMConfig требует доверенные base_url/model/api_key; defaults: timeout_seconds=60,
max_output_tokens=2048, response_format=json_schema. Strict json_schema и явно
выбранный оператором json_object не переключаются автоматически. В json_object
schema остаётся в системной инструкции и локальная проверка обязательна; адаптер
не ослабляет контракт ради ответа. Лимит вывода передаётся как max_tokens.
Redirects и environment proxy выключены; URL без userinfo/query/fragment, key —
только Authorization. Config не сериализуйте с секретом через asdict; не включайте
HTTP body/header debug logging. Key скрыт из repr, отражённый в ответе key отклоняется.

Result содержит data только при ok, usage даже при ошибке и нормализованный error.
Usage: tokens_in/out/total_tokens либо None, latency_ms по monotonic clock,
calls=0 до запроса и 1 после начала. Network timeout ограничивает суммарное сетевое
ожидание, в том числе body read/close; синхронная локальная валидация не входит.
Невалидная config вызывает ValueError до сети. Error содержит code/message/retryable,
optional http_status, не исходное исключение.

| Код | Retryable |
|---|---|
| LLM_TIMEOUT (включая 408/504), LLM_UNAVAILABLE (transport/5xx), LLM_RATE_LIMITED (429) | Да, при разрешении вызывающего бюджета |
| LLM_INVALID_INPUT, LLM_INVALID_SCHEMA, LLM_AUTH_ERROR (401/403), LLM_REQUEST_REJECTED | Нет |
| LLM_REFUSED, LLM_OUTPUT_TRUNCATED, LLM_INVALID_RESPONSE | Нет |

Пример `python -m backend.app.agents.llm.example` **делает реальный запрос**.
Он читает LLM_BASE_URL (default `http://45.146.131.213:14000/v1`), LLM_MODEL
(`qwen3.8-27b`), обязательный LLM_API_KEY и LLM_RESPONSE_FORMAT (`json_schema`).
Это config предоставленного подключения, не обещание доступности/совместимости
модели. Key задавайте в окружении из secret store; запуск без key завершается.
Документационная проверка не запускает этот пример и не расходует модель.

## A-02: prompt и структура анализа

[Prompt](../backend/app/agents/prompts/analysis/prompt.py) использует неизменённый
[system.md](../backend/app/agents/prompts/analysis/system.md),
[шаблон](../templates/analysis/system-analysis.md.tmpl) и
[draft.schema.json](../templates/analysis/draft.schema.json).
Prompt/template 1.0.0, внутренний analysis-draft/1.0 — **не AgentResult**.

`build_analysis_prompt(context,business_input,previous_requirements=None,analysis_rules=None)`
проверяет C-02, stage_id=analysis, Angular profile и его pinned version, references/
revision/answers, возвращает AnalysisPrompt(system,prompt,schema и limits).
`StructuredLLM.generate` — async Protocol A-01. `generate_analysis(llm,prepared)`
делает один вызов и `validate_draft`; `render_system_analysis(draft,prepared)`
повторяет guard и возвращает строку без записи файла. ContractError при отдельной
валидации/рендере не содержит исходных текстов. Business input сериализуется в
отдельный JSON user-message, static system не форматируется пользовательским текстом.
Регламент передаётся отдельным полем, не повышает приоритет пользовательских команд.

Draft содержит неизменённый C-02 requirements и шесть предварительных design_notes:
components/data_model/api/events_integrations/input_validation/error_handling.
Каждый note: status, обычный text и FR/ASM/question links. Proposed требует ASM,
needs_clarification — реальный неотвеченный required вопрос, not_applicable —
предметную причину. Это не architecture/data/UI контракты. Refs разворачиваются
локально с сохранением sibling constraints, без скрытого упрощения схемы.

Рендер формирует 14 разделов системного анализа: назначение, границы, роли/права,
сценарии, FR, NFR, компоненты, данные, API, события/интеграции, input validation,
ошибки, приёмка и отдельные допущения/вопросы; дополнительно безопасность и риски
(разделы 15–16). Facts берутся из одного requirements payload; design предложения
показываются с явным status. Model/user text экранируется, не создаёт HTML/заголовки.
API/event checklist: обязательные поля, длина/форматы, обоснованные regex, errors,
права, rate limits, idempotency/logging/version; для events — ID/time/schema/dedup.
Неприменимые разделы остаются с объяснением, неизвестные факты не выдумываются.

Проверяются буквальные evidence, связи FR/ASM/roles/questions, сохранение ID,
подтверждённых ответов и revision. Нет FR — нужен required вопрос о цели. При
отказе generate_analysis возвращает ANALYSIS_INVALID_DRAFT, data=None и прежний
usage; transport error передаётся без подмены. Успех не доказывает полноту смысла
и не игнорирует критические вопросы. Владелец клиента обязан применить лимиты
prepared (минимум call/attempt/Run remainder), зарезервировать бюджет до вызова;
переданный клиент сам не перенастраивается. max_calls=0 отклоняется до LLM.

Будущий A-03 упаковывает requirements/questions/Markdown в три обязательных
артефакта с worker metadata/checksum, преобразует usage и выбирает status. Не
сохраняйте оболочку draft как requirements.json и не поручайте LLM генерировать
checksum, usage или авторизацию. Регламент организаторов необязателен; базовая
структура следует PDF, но не подтверждает соответствие непредоставленному регламенту.

Offline examples в [templates/analysis/examples](../templates/analysis/examples/)
про ремонтные заявки: первый — FR-001–003 и вопрос о правах удаления, второй —
подтверждённый запрет удаления всем ролям и FR-004 с прежними UUID/revision=1.
Это hand-authored fixtures, не реальные LLM ответы; old draft.requirements
разрешает previous reference только внутри примера. Пример не пишет в storage.

## Legacy и миграция A-03

`backend/app/agents/requirements_analyst/` — самостоятельное синхронное ядро
`RequirementsAnalyst(client).run(AgentInput) -> AgentResult` с LLMClient.complete и
локальным OpenAICompatibleClient. Порт:
`complete(*,messages,schema,max_output_tokens,timeout_seconds) -> Completion`, где
Completion содержит content и nullable input_tokens/output_tokens. LLMError
содержит безопасный code, retryable и optional completion с уже известным usage.
Использовать внутри worker, не blocking-вызовом
из async HTTP. Один вызов — максимум одно обращение; нет скрытых повторов.
Он извлекает требования, но не формирует полный system-analysis из A-02.

Его `requirements-analyst/0.1` **не C-02 1.0**. Локальные Pydantic models и schemas
живут отдельно. Статусы succeeded/waiting_for_input/failed относятся к этому узлу,
не готовому Run. Успех/ожидание дают requirements.json/questions.json/
requirements-analysis.md с checksum; invalid result не принимается как успех.
Каждый FR имеет AC given/when/then, facts — evidence, assumptions/risks отдельно;
источники/ID/critical questions проверяются, подтверждённые ответы сливаются кодом.
Unknown usage — null, известный usage сохраняется и при ошибке. Лимит critical
вопросов — до 3, max_calls=0 останавливает до сети; совокупный бюджет — orchestrator.

| Legacy 0.1 | Требуемое C-02 1.0 преобразование |
|---|---|
| schema/contract_version=requirements-analyst/0.1, stage=requirements | schema=1.0, предметный contract_version, stage=analysis по профилю |
| requirements_text/answers/previous_analysis в AgentInput | Разрешённые input_artifact_ids, analysis-input и previous reference |
| Q-001, id/text/critical, answer=null | Постоянное отображение question_id UUID/question/required, answer опускается |
| Нет questions_revision | Сохранённая ревизия и проверка текущего пакета |
| Artifact.kind=MIME, неполная metadata | Раздельные kind/media_type и worker metadata |
| requirements-analysis.md | Полный docs/system-analysis.md через A-02, не переименование файла |
| Только aggregate usage | model_calls с call_id, nullable tokens, агрегаты и diagnostics severity/FR |

При миграции сохраняйте FR/NFR/ROLE/SC/AC/ASM/RISK IDs, смыслы, evidence и old
snapshots с маркировкой 0.1. Нельзя объявлять их 1.0 без преобразования в новый
snapshot. Отображение Q→UUID постоянно для Question/Answer/Evidence (например,
сохранённая таблица или UUIDv5 с Run namespace); это проектное решение, не готовый
bridge. Нужно согласовать C-02/профиль/A-01/A-02, реализовать упаковку в A-03,
проверить реальный выход/уточнение и только затем подключать A-04/worker
([#130](https://github.com/Krastti/RWB-Hack/issues/130)).

CLI с `PYTHONPATH=backend/app/agents`: `requirements_analyst validate` проверяет
сохранённые input/analysis без сети; analyze обращается к LLM и печатает JSON.
Коды выхода 0 — valid (включая wait), 1 — agent failure, 2 — input/config error.
Для resume передают previous_analysis, тот же text/run_id, attempt+1 и ответы
Q IDs; owner/current revision проверяет вызывающий API. Ответы без previous
questions/дубликаты/пустой ввод отклоняются. New requirements требуют новый snapshot.

Legacy transport читает LLM_BASE_URL/LLM_MODEL, optional key для локальной модели,
не следует redirects, URL выбирает оператор. По умолчанию json_schema, явный
`--response-format json_object` сохраняет локальную schema validation. Timeout —
network, общий deadline/отмена — worker. Нормализованные причины: CALL_BUDGET_EXCEEDED,
OUTPUT_TOKEN_BUDGET_EXCEEDED, INVALID_PREVIOUS_ANALYSIS, INVALID_ANALYSIS, LLM_TIMEOUT,
LLM_UNAVAILABLE, LLM_AUTHENTICATION, LLM_HTTP_ERROR, LLM_INVALID_ENVELOPE,
LLM_INCOMPLETE_RESPONSE, LLM_RESPONSE_TOO_LARGE, LLM_FAILURE. Raw provider exceptions
не публикуются. Эти имена не нужно выдавать за коды A-01 или Run stop_reason.

## A-04 и восстановление

`AnalysisGraph(node,artifacts,checkpointer,stage_id="analysis")` использует Protocol
[AnalysisNode/ArtifactStore](../backend/app/agents/graph/state.py). `start(request,attempt_id)`
принимает уже зарегистрированную первую попытку=1; одинаковая команда восстанавливает,
иная исходная команда того же Run отклоняется. `snapshot(run_id)` читает состояние,
`recover(run_id)` продолжает unfinished nodes; `resume(run_id,questions_revision,answers,
attempt_id)` принимает новый зарегистрированный UUID и полный пакет обязательных
ответов. Валидация выполняется до изменения checkpoint; replay последнего пакета
не создаёт ещё attempt. Это не API для запуска произвольной failed N+1: bridge
этой команды остаётся #128.

Граф `begin_attempt → analyse → await_answers → prepare_resume → begin_attempt`.
LLM и interrupt в разных узлах: вход в waiting не вызывает модель. Success анализа
оставляет Run running для будущих этапов, не completed. Questions revision начинается
с 1 даже при пустом списке; новые вопросы/изменённое определение повышают её.
Ответы и перестановка related FR сами по себе не повышают revision. Нельзя менять
определение отвеченного вопроса под прежним UUID. Новые FR/NFR/roles допустимы при
сохранении прежних ID и подтверждённых фактов.

`open_checkpoints(path)` требует абсолютный путь в существующем внешнем data
каталоге, например `/var/lib/rwb/analysis.sqlite`; не :memory: и не относительный
репозиторный файл. SQLite saver — локальный durable adapter одного worker,
распределённый saver задаётся отдельно. JSON-only serializer не разрешает pickle.
Durability sync сохраняет границу до следующего узла. Во время waiting worker/
соединение не удерживаются; saver можно переоткрыть.

State хранит Run/stage/attempt, версии/profile/input_hash, вопросы/ответы,
references, attempts/times, usage/diagnostics; не file content или provider body.
Canonical substantive hash (`input_hash_algorithm=substantive-v2`) исключает
request attempt/limits и сортирует references; смена требований/confirmed-answer
artifacts меняет его. Это отдельная версия алгоритма, не state_version. Checkpoint
без маркера проверяется по обоим известным алгоритмам на фактических inputs и
metadata: full-request-v1 (полный request и исходный порядок references) либо
substantive-v2. Успешная проверка фиксирует выбранный алгоритм, **не меняя legacy
hash или историю попыток**. Явная неизвестная/невалидная версия или mismatch не
позволяют fallback. При новой попытке после подтверждённых ответов используется
substantive-v2; история сохраняет прежнюю идентичность. Durable receipt читается
после проверки inputs и восстанавливается без вызова узла. Реальное изменение
inputs по-прежнему даёт INPUT_SNAPSHOT_CHANGED.

ArtifactStore.read проверяет owner/Run/content, put — immutable/idempotent;
result возвращает durable receipt, accept атомарно сохраняет output+receipt либо
идентичный replay. Different bytes под тем же UUID запрещены. После receipt, но до
checkpoint, повтор использует принятый результат без LLM/двойного usage. Сбой
после внешнего запроса **до receipt** может вызвать новый запрос: exactly-once
внешнего LLM не гарантируется. Учёт должен сохранять каждый физический вызов.
Invalid result не применяется, известный usage не теряется. Cancelled/skipped
аналитика без управляющего решения не делают обязательный анализ успешным.

Graph/storage/checkpointer errors передаются bridge для сохранения причины и
восстановления той же команды; отсутствие checkpoint — RESUME_STATE_UNAVAILABLE.
Rebuild из canonical DB пока обязанность server recovery. Для production связки B-06/B-08/B-12/B-13 должны обеспечить owner, lease/fencing,
atomic accepted answers/pending attempts,
idempotency/outbox/public events. Parallel calls одного Run без внешнего lease
запрещены. Граф не реализует автоматический repair или общий budget ledger. Существующие
[StageWorker и queue adapter](backend.md#очередь-и-worker-adapter) принимают
orchestrator/recovery callable, но автоматически не оборачивают ArtifactStore.accept
и checkpoint графа в execution.transaction; этот bridge ещё нужен.

## Генератор модели данных

Модуль [data_model](../backend/app/agents/generators/data_model/) создаёт модель
PostgreSQL по принятому анализу. Формат `data-model/1.0` определён в
[model.schema.json](../backend/app/agents/generators/data_model/model.schema.json).
Он описывает содержимое файла; metadata артефакта сохраняет C-02 `schema_version=1.0`.
Старый иллюстративный data_model в общих fixtures не соответствует этому формату.

| Функция | Назначение |
|---|---|
| `build_data_model_prompt(context, requirements_artifact, profile=...)` | Проверить C-02 вход и inline requirements, подготовить prompt и эффективные лимиты |
| `generate_data_model(llm, prepared)` | Один вызов существующего `generate` port; вернуть проверенный payload или ошибку с исходным usage |
| `validate_data_model(model, requirements=..., requirements_artifact_id=..., contract_version=..., profile_id=...)` | Проверить payload и его ссылки перед использованием потребителем |
| `make_data_model_artifact(model, prepared, artifact_id=..., attempt_id=..., version=1)` | Сериализовать проверенные bytes в C-02 артефакт `data-model.json` |

Caller предварительно авторизует входы и передаёт сохранённый snapshot профиля.
Storage reference сначала разрешается в проверенный inline artifact. Регламент и
profile checksum фиксирует общий loader; генератор не перечитывает defaults Run.
Нерешённый required-вопрос и нулевой лимит вызовов отклоняются до LLM.
Для A-01 нужно установить `prepared.max_output_tokens` и `timeout_seconds` в
LLMConfig, а перед вызовом зарезервировать бюджет. Retry и учёт Run остаются снаружи.

Модель содержит contract_version/profile_id, UUID исходных requirements,
entities, relationships, assumptions, unresolved_items и fr_coverage. Сущность
задаёт имя, исходные source_refs, fields, именованный primary_key, unique/check
constraints и indexes. source_refs ссылаются на точный text элемента entities,
roles или functional_requirements принятого анализа. Имена SQL — lowercase ASCII
snake_case до 63 символов. Каждый FR перечисляется в coverage как modeled с
полным списком связанных сущностей либо not_applicable с причиной.

Типы: uuid, varchar, text, integer, bigint, numeric, boolean, date, timestamptz,
jsonb. Varchar требует max_length; numeric — precision/scale, остальные параметры
равны null. Numeric literals передаются строками для сохранения точности.
Nullable и default заданы явно; application_uuid4 — default приложения,
server_now — серверный default timestamptz. Literal defaults jsonb не поддержаны.

FK содержит source/target fields, cardinality, match и on_delete. Target должен
быть PK/unique key или unique btree index; one_to_one требует unique source.
Many-to-many представляется join-сущностью. Проверяются arity/типы FK, nullable
для set_null, существование полей constraints/indexes, defaults и CHECK predicates.
Predicates — типизированные eq/ne/lt/lte/gt/gte/in/not_in/is_null/is_not_null;
SQL expressions, partial/expression indexes и PostGIS в этом формате отсутствуют.
CHECK сохраняет PostgreSQL null semantics и не заменяет NOT NULL или авторизацию.

Неподдержанное обязательное решение записывается в unresolved_items и блокирует
приёмку модели. Ошибка структуры, ссылок, бюджета или контекста возвращается с
безопасным кодом и исходным usage, без повторного запроса. Полнота смысла анализа
и согласование имён с OpenAPI проверяются архитектурным этапом.

Владелец выходного артефакта — architecture. IDs и version резервирует caller;
генератор вычисляет UTF-8 size/checksum, но не пишет в storage, не выпускает
AgentResult и не меняет состояние Run. Архитектурный агрегатор должен применить
этот validator и согласовать contract_version до передачи backend/миграциям.
Общий C-02 validator сам по себе проверяет data_model только как JSON.

Offline пример использует HTTPX MockTransport и ручной fixture:

```sh
.venv/bin/python -m backend.app.agents.generators.data_model.example
```

Он проходит через существующий adapter и возвращает валидный data-model payload;
реальный provider, PostgreSQL и генерация миграций не запускаются.

## Профиль C-05

[profile.json](../templates/profiles/web-fastapi-angular/profile.json) описывает
**генерируемое приложение**, не готовый шаблон/loader. Сервер разрешает ID через
allowlist, валидирует format, замораживает точные bytes/checksum/profile_version
и effective limits в private Run context. Retry не подхватывает новые defaults.
Пользователь не задаёт каталог, команды или grants. Изменение defaults/обязательных
путей требует profile_version, несовместимое — major; изменение формата — schema_version.
Точные версии библиотек будущего шаблона фиксируются lock, без latest.

Стек: Angular 22 strict/standalone, Router/Forms/HttpClient/RxJS, Bootstrap 5.3/SCSS;
FastAPI/Pydantic/SQLAlchemy/PostgreSQL и **Alembic**. Это не Flyway платформы.
Список всех обязательных файлов — `required_artifacts` в JSON, не копия дерева
в Markdown. Группы: analysis (requirements/questions/system-analysis), architecture
(architecture/OpenAPI/data/UI/plan), frontend source/config/theme, backend API/DTO/
services/models/repositories, migrations, integrations, docs, manifest/traceability/.gitignore.

Condition always требует файл; has_integrations определяется непустым accepted
architecture.integrations, включая mock. Тогда нужны integrations/README.md,
описания и backend/app/integrations adapters; иначе каталоги не создаются, причина
в limitations. Пустой questions.json остаётся обязательным; неприменимые разделы
не удаляются. Entry points/.gitkeep без предметных экранов/сервисов не покрывают FR.

Будущий assembler проверяет обязательные/условные пути, grants и references,
checksum/size/versions, одну contract_version API/data/UI, каждый FR в traceability,
manifest metadata включая traceability, отсутствие blocking unresolved_items.
Сам manifest исключён из собственного списка checksum, но включён в файловый бюджет.
Missing required/error diagnostics → partial, не prepared/completed. Служебный
файл может иметь пустые FR с объяснением. Generated README/guides описывают реальные
зависимости/параметры/migrations/команды, не выдуманные успешные отчёты.

### Ownership и generation plan

`ownership.default=deny`; exact paths и directories — grants по границе сегмента.
`frontend` разрешает frontend/src, не frontend-old. План **сужает** grants, не
расширяет; backend получает перечисленные backend/app поддеревья и config files,
никогда весь backend. Migrations и integrations — отдельные owners. Documentation
владеет корневым README и шестью docs, но не docs/system-analysis.md (analysis).
Architecture владеет общими контрактами; assembly — manifest/traceability/.gitignore
и объединение принятых bytes, не исправление чужого содержимого. Правка docs после
сборки создаёт новую ревизию/snapshot.

Task_id и stage_id уникальны; owner_agent может повторяться. Depends_on содержит
task_id. Orchestrator сохраняет task→stage→owner по принятому плану и выбирает
исполнителя из проверенного registry, а не по строке модели. Одинаковый owner не
означает общую попытку/lease/retry counter. Materialization добавляет references
доступных dependency outputs; dependencies требуют принятых results и checkpoint.
План не допускает overlapping outputs разных задач даже последовательно.
Output_paths — explicit files либо **внутри** granted directory; сам granted
каталог нельзя объявить файлом. File/directory и ancestor-file conflicts внутри
задачи также запрещены. Root backend, чужие migrations/integrations отклоняются.

Правила записи storage в разделе артефактов обязательны сверх JSON Schema.
`validate.py --check` сверяет общий Angular пример с actual C-05 grants; отдельная
общая C-02 validation не авторизует arbitrary profile. Runtime enforcement остаётся
[#131](https://github.com/Krastti/RWB-Hack/issues/131).

### Правила генерируемого приложения

UI spec связывает FR/roles/actions с operationId и Angular/Bootstrap components.
Один UI Kit, тема в frontend/src/styles.scss; SB Admin MIT notice сохраняется,
Angular 11/Bootstrap 4/Pug dependencies не копируются. Widgets документируются с
назначением/доступностью. Loading/empty/error/forbidden/success, labels/errors,
keyboard/focus, подписанные icon buttons и ширины 360/768/1280 обязательны; локальная
табличная прокрутка допустима. Это требования генерации, не результаты тестов.

Frontend обращается к реальному **сгенерированному** backend, configurable base URL,
типизированный client; не использует platform token/URL. Static arrays/localStorage/
фиктивный success не заменяют persistence. Не обходите контракт any/suppressions.
Backend разделяет HTTP/DTO/services/repositories/integrations, проверяет объектные
права/поля/переходы/транзакции сервером; SQLAlchemy/PostgreSQL соответствует
предметной data schema, Alembic воспроизводит её без create_all вместо migrations.
Нельзя выдавать TODO за обязательную реализацию. .env.example — только placeholders,
.gitignore исключает реальные секреты/env/dependencies.

Mock — только явная конфигурация недоступной внешней системы, real/mock реализуют
один контракт; незаметный fallback real→mock-success запрещён. Документируются
FR/operations/fields/errors/timeouts/auth/idempotency/retries; events имеют ID,
version/time/schema/dedup. Mock synthetic/deterministic success/error не заменяет
основной расчёт, owner check, state transition или PostgreSQL запись. Небезопасная
интеграция — вопрос/диагностика и непокрытый FR, а не фиктивная готовность.

### Limits и остановки

Точные defaults — в JSON. На момент профиля 1.0.0: один active Run/user;
Run 60 calls/300000 input/180000 output tokens/3600 active seconds; agent 10 calls/
30000 output/600 active seconds; call 120 seconds; до 2 automatic retries за этап
на весь Run, до 2 clarification rounds с максимум 3 required questions. Artifacts:
500 files, 1 MiB/file, 25 MiB/snapshot; 1 MiB=1048576 bytes, path≤512.

Все области лимитов различаются: в AgentInput идут agent limits; run/model_call/
artifacts остаются серверной политикой. Выделяется минимум defaults, разрешённой
настройки и остатка. Нулевой output остаток останавливает до AgentInput, не задаёт
invalid max_output_tokens=0. File budget включает manifest, history — отдельная
storage policy; размер проверяют до записи.

Резерв calls/input/output до dispatch атомарный для parallel work; неизвестный
usage удерживает резерв, не превращается в 0. Active wall-clock интервалы не
суммируют overlap, исключают queue/waiting; call ограничен остатком agent/Run.
Общий лимит сохраняет partial artifacts/usage, даёт failed+BUDGET_EXCEEDED, затем
Run budget_exceeded. Call timeout/error может быть retryable при доступном бюджете;
невалидные входы/контракты/critical questions не повторяются вслепую. После 2 rounds
unresolved critical не становится assumption: CLARIFICATION_LIMIT_REACHED/failed.

Public Retry меняет только max_calls/max_output_tokens по авторизованной политике;
причины MAX_INPUT_TOKENS/RUN_TIMEOUT/unknown возвращают RETRY_NOT_SUPPORTED,
calls/output требуют достаточного роста нужного предела и сохранения usage/истории/
frozen profile. [Backend policy](backend.md#бюджет-и-время) отличает active time от
elapsed и request timeout. Общий accounting producer — #129.

## Проверки и примеры

Из корня, Python 3.11+, один совместный resolver (API + A-01/A-02/C-02/A-04/legacy):

```sh
python -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-worker.txt
.venv/bin/python -m pip check
.venv/bin/python contracts/agents/validate.py --check
.venv/bin/python -m unittest discover -s contracts/agents -p 'test_*.py' -v
.venv/bin/python -m unittest discover -s backend/app/agents/graph -p 'test_*.py' -v
PYTHONPATH=backend/app/agents .venv/bin/python -m unittest discover -s backend/app/agents/requirements_analyst/tests -v
.venv/bin/python -m backend.app.agents.prompts.analysis.example
.venv/bin/python -m backend.app.agents.prompts.analysis.example --resume
```

Pins JSON Schema/referencing — один источник
[contracts/agents/requirements.txt](../contracts/agents/requirements.txt).
Модульные requirements подключают его, Pydantic объявлен явно; порядок установки,
--no-deps/--force не исправляют несовместимые pins. Необходимые зависимости
worker находятся в entrypoint, успешный import не доказывает работающий pipeline.

A-02 offline пример использует настоящий A-01 с HTTPX MockTransport и ручным draft,
без сети/usage модели. --resume проверяет подтверждённый ответ, не real clarification.
Local graph tests используют временные SQLite и C-02 fixtures. Отдельная C-02 проверка:

```sh
.venv/bin/python contracts/agents/validate.py agent-result contracts/agents/examples/agent-result.waiting.json
.venv/bin/python contracts/agents/validate.py agent-input templates/profiles/web-fastapi-angular/examples/agent-input.frontend.json
.venv/bin/python contracts/agents/validate.py generation-plan templates/profiles/web-fastapi-angular/examples/generation-plan.json
```

CLI: 0 success, 1 invalid, 2 arguments. --check проверяет metaschemas/local refs,
FormatChecker, index, linked input/result identities, FR/role/question IDs/evidence,
usage, inline size/checksum, metadata references, DAG/overlaps и snapshot/traceability.
Storage_ref bytes/owner/actual completeness/смысл анализа проверяет runtime, не CLI.
[examples/index.json](../contracts/agents/examples/index.json) перечисляет связность;
все examples ручные, partial manifest не выдаётся за реальный готовый проект.

Проверка формата профиля тем же локальным Registry:

```sh
.venv/bin/python - <<'PY'
import json
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
root = Path('templates/profiles/web-fastapi-angular')
schema = json.loads((root / 'profile.schema.json').read_text())
schemas = [json.loads(p.read_text()) for p in Path('contracts/agents/v1').glob('*.schema.json')]
schemas.append(schema)
registry = Registry().with_resources((s['$id'], Resource.from_contents(s)) for s in schemas)
Draft202012Validator.check_schema(schema)
Draft202012Validator(schema, registry=registry, format_checker=FormatChecker()).validate(json.loads((root / 'profile.json').read_text()))
print('OK: profile format; local schema registry')
PY
```

Legacy validation без сети (ожидается wait/Q-001):

```sh
PYTHONPATH=backend/app/agents .venv/bin/python -m requirements_analyst validate --input backend/app/agents/requirements_analyst/examples/input.json --analysis backend/app/agents/requirements_analyst/examples/analysis.json
```

Для реального legacy analyze нужны доверенные LLM_BASE_URL/LLM_MODEL/key; эта команда
не используется для проверки документации:

```sh
PYTHONPATH=backend/app/agents .venv/bin/python -m requirements_analyst analyze --input backend/app/agents/requirements_analyst/examples/input.json
```

Экспорт legacy schemas после изменения **legacy** models: `requirements_analyst schema
input|analysis|result` выводит JSON в stdout. Не перезаписывайте этим C-02 schemas.
Миграция/откат действующего контракта требует версии, согласования всех consumers
и сохранения чтения используемых snapshots. Нельзя менять смысл прежнего поля
под той же schema_version или выдавать успешный offline пример за real LLM/E2E.


### Разрешение rules_document

README профиля явно маркируется как указатель. `load_profile_rules` в существующем
C-02 validator распознаёт только точный marker `docs/agents.md#профиль-c-05` для
web-fastapi-angular/README.md и возвращает полный локальный docs/agents.md.
Никакие произвольные Markdown links, сеть, redirects, symlinks или traversal не
разрешаются. Нет pointer/target/обязательных разделов — ContractError и отказ,
а не исполнение с README вместо правил. `validate.py --check` проверяет этот
offline-контракт. Production loader обязан подключить то же разрешение; такая
интеграция не заявлена. Profile ID/version/grants/schema/checksum не меняются.
