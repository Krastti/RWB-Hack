# Backend платформы

FastAPI/Pydantic/SQLAlchemy/PostgreSQL. Это API и БД **платформы**, а не backend
генерируемого приложения. У последнего собственный OpenAPI, база и Alembic.
HTTP-источник истины — [C-01 OpenAPI 2.1.0](../contracts/platform.openapi.yaml),
правила состояний — [C-03 1.0](../contracts/lifecycle.md).

## Структура и подключение

| Код | Ответственность |
|---|---|
| `backend/app/main.py` | `create_app(database_url=None, auth_config=None, run_config=None)`: engine → sessions → PostgreSQL repositories → auth/projects services → HTTP |
| `api/auth`, `api/projects`, `api/common.py` | Bearer, endpoints, валидация и безопасный Error envelope |
| `models/`, `models/dto/` | Раздельные ORM и HTTP/transport projections; ORM-экспорты разрешаются лениво |
| `repositories/` | Protocol `UserRepository`/`ProjectRepository`, PostgreSQL-адаптеры |
| `services/auth`, `services/projects` | Доменные операции без HTTP/ORM-зависимости в проектном сервисе |
| `services/run_queries`, `api/runs` | Чтение Run/событий через `RunReadRepository`; opt-in HTTP |
| `services/lifecycle` | Guarded transitions, CAS/generation, события и локальный transactional adapter |
| `services/run_control` | Cancel/retry, worker admission hooks, opt-in router и локальный adapter |
| `adapters/queue/`, `workers/` | Celery transport, PostgreSQL WorkerCommand lease/fencing, StageWorker |
| `agents/` | [Агенты и граф](agents.md); не HTTP orchestration |

Штатная factory монтирует auth/projects, POST создания Run, три Run GET-маршрута
и cancel/retry к одной PostgreSQL через общий sessionmaker и
[runtime composition B-11](../backend/app/services/runtime/README.md). In-memory fallback нет. Answers,
artifact/archive/export endpoints и предметный AG/worker bridge остаются отдельными
потребителями [#127](https://github.com/Krastti/RWB-Hack/issues/127)/
[#128](https://github.com/Krastti/RWB-Hack/issues/128).

## Установка и запуск

Из корня репозитория, Python 3.11+. Для API достаточно `backend/requirements.txt`;
для API и агентных модулей вместе используйте `backend/requirements-worker.txt`:

```sh
python -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-worker.txt
.venv/bin/python -m pip check
```

Нужна отдельная PostgreSQL. Установите параметры через окружение/хранилище секретов:
`DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DATABASE` и случайный
`JWT_SECRET_KEY` не короче 32 UTF-8 байтов. Не помещайте действующий пароль/ключ
в Git, shell history или лог. Другие СУБД factory отклоняет; PostgreSQL-драйвер
нормализуется в psycopg. Engine использует `pool_pre_ping`, скрывает параметры
и освобождается при завершении lifespan. Таблицы автоматически не создаются.

Flyway запускается из корня с установленными `FLYWAY_URL` (JDBC PostgreSQL URL),
`FLYWAY_USER`, `FLYWAY_PASSWORD`:

```sh
flyway -configFiles=backend/flyway.conf migrate
PYTHONPATH=backend .venv/bin/uvicorn app.main:create_app --factory
```

Для штатного API/runtime нужны V1–V6; миграции поставляются в этой ветке
и применяются по порядку без пропуска V4. V6 сохраняет private execution/control state.
API слушает `127.0.0.1:8000`. Flyway/настоящий PostgreSQL-запуск требуют доступной
инфраструктуры; список локально выполненных проверок приводится в PR, а не
предполагается из этой инструкции. Возможна явная сборка:

```python
from backend.app.config.auth import AuthConfig
from backend.app.main import create_app
app = create_app(database_url=database_url, auth_config=AuthConfig(secret_key=secret))
```

Используйте либо `backend.app.*` из корня, либо `app.*` с `PYTHONPATH=backend`;
не смешивайте оба имени пакета в одном процессе.

Публичной регистрации нет. Пользователя создаёт оператор с Argon2id hash:

```python
import os
from getpass import getpass
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from backend.app.models import User
from backend.app.services.auth import hash_password
engine = create_engine(os.environ['DATABASE_URL'], hide_parameters=True)
with Session(engine) as session, session.begin():
    session.add(User(login=input('Login: '), password_hash=hash_password(getpass())))
engine.dispose()
```

## PostgreSQL и модели

[Миграции](../backend/migrations/) выполняются по порядку: V1 — User/Project/
RequirementRevision, V2 — Run/StageAttempt/RunEvent/IdempotencyRecord и составная
ссылка ревизии на проект, V3 — обновление `Run.updated_at`. Применённые SQL-файлы
не редактируются; изменения оформляются новой V-миграцией. Flyway проверяет имена
и checksums, ведёт `flyway_schema_history`; clean отключён конфигурацией.
`create_all` не заменяет Flyway и не устанавливает V3 trigger.

| Модель | Хранение и mapping |
|---|---|
| User / Project | Владелец — `Project.owner_id`; активность пользователя читается при каждом защищённом запросе |
| RequirementRevision | Неизменяемые текст/номер/hash, принадлежность проекту; current_revision выбирается по максимальному номеру |
| Run | `id` → HTTP `run_id`; `project_id`, `revision_id`, исходный `profile_id`, limits и read projections |
| StageAttempt | `id` — внутренний UUID/Artifact.attempt_id; `attempt_no` начинается с 1 в `(run_id, stage_id)` |
| RunEvent | Составной PK `(run_id, seq)`; BIGINT seq от 1; публичный C-03 data со schema_version |
| IdempotencyRecord | `(owner_id, operation, resource_id, key)`, request_hash и непрозрачный durable response_ref |

FK используют RESTRICT: удаление проекта/пользователя не должно молча удалять
историю или replay. `Run.revision` — read-only relationship: запись задаёт
`revision_id`/`project_id`. В БД profile_id — непустой идентификатор; допустимость
проверяет сервис. `stopped_at` внутренний: ставится при остановке, очищается при
возобновлении и фиксирует elapsed; updated_at не заменяет stop timestamp.
V3 BEFORE UPDATE trigger ставит `clock_timestamp()` при любом SQL UPDATE Run;
ORM помечает поле server-generated, при необходимости после flush нужен refresh.

`questions_revision/questions/metrics` — сохранённые проекции C-01, не замена
Clarification/ModelCall учёту. Начальные значения — ревизия 0, пустые вопросы,
шесть нулевых счётчиков. При записи JSON заменяйте значение целиком: вложенные
изменения обычных dict/list SQLAlchemy автоматически не отслеживает. Обновляйте
проекции атомарно с фактами и событиями. Optional поля в БД SQL NULL; в HTTP
отсутствующие optional опускаются, обязательный nullable cursor остаётся null.
StageAttempt.error хранит **весь** `{"error": {...}}`, а не только inner object.

Rollback кода не откатывает БД. До приёма данных V3 можно убрать удалением trigger
`runs_refresh_updated_at` и функции `refresh_run_updated_at()`; V2 — в порядке
idempotency/events/attempts/runs, затем composite unique ревизий. После приёма
данных это уничтожает историю: остановите writers, используйте backup или
согласованную forward migration, не выполняйте разрушительный rollback по инструкции.

## Repositories и проекты

`PostgreSQLUserRepository(sessions)` реализует `get_by_login`/`get_by_id` и выдаёт
`UserCredentials`. `PostgreSQLProjectRepository(sessions, cursor_secret=...)`
реализует `ProjectRepository.create/get/list`, возвращая detached DTO.
Каждая операция открывает свою Session; session не разделяется между потоками.
Проект и первая ревизия сохраняются в одной транзакции, ошибка INSERT/commit
откатывает обе записи. Репозиторий не редактирует существующие ревизии.

`ProjectService(repository)` предоставляет keyword-only:
`create_project(owner_id, name, requirements_text)`, `get_project(owner_id, project_id)`,
`list_projects(owner_id, limit=100, cursor=None)`. Ошибки `InvalidProjectInput` и
`ProjectNotFound` доступны из `app.errors`; совместимые экспорты сервиса сохранены.
Владелец приходит из current_user, никогда из тела. Сервис дополнительно проверяет
owner у результата адаптера и не маскирует storage failure успешным ответом.

Название: 1–120 символов; requirements: 1–100000 и минимум один непробельный символ.
Whitespace для требований/ответов: U+0009–000D, U+001C–0020, U+0085, U+00A0,
U+1680, U+2000–200A, U+2028–2029, U+202F, U+205F, U+3000 (Python `isspace`).
DTO и ProjectService отклоняют невалидный текст до записи. Валидный текст не
trim/нормализуется: SHA-256 считается от исходных UTF-8 байтов. Исторические тексты
не переписываются; правило не применяется к паролям. API возвращает 422 с C-01
ошибкой. Проверка отсутствующих Run/answers write consumers остаётся
[#124](https://github.com/Krastti/RWB-Hack/issues/124)/#127.

Списки owner-filtered **до** LIMIT и отсортированы `created_at DESC, id DESC`.
Keyset cursor содержит версию, owner и пару ключей, подписан HMAC-SHA256 с
контекстом `projects:v1:` и URL-safe base64. Он не зашифрован и не является
авторизацией; требования в нём не хранятся. Секрет ≥32 байта, неверный/чужой/
изменённый cursor даёт 422, смена секрета инвалидирует cursors. Дополнительная
строка определяет следующую страницу; новые записи видны после обновления первой.
`latest_run_status` выбирается в том же SELECT коррелированным подзапросом по
`created_at DESC/id DESC`, без N+1; null означает отсутствие Run.

## Авторизация и ошибки HTTP

`POST /api/v1/auth/token` принимает **JSON** `{login,password}`, не OAuth2 form,
и возвращает `{access_token,token_type:"bearer",expires_in}`. `AuthService`:
`issue_token(login=..., password=...)` и `current_user(token)`; пароли — Argon2id
со случайной солью, неизвестный login проверяется через dummy hash. Повреждённый
hash отклоняется. `CurrentUser` не содержит password hash; secret-поля скрыты из repr.

`AuthConfig(secret_key, issuer="rwb-platform", audience="rwb-platform-api",
ttl_seconds=1800)` допускает TTL 1–86400. JWT — HS256 с фиксированным алгоритмом,
проверкой подписи, issuer/audience, обязательных sub/iat/exp/iss/aud; sub — UUID.
Ключ общий у instances, без fallback. Смена ключа инвалидирует токены. current_user
каждый раз читает пользователя: удалённый/неактивный немедленно теряет доступ.

Неверные credentials, JWT, Bearer или неактивный пользователь — одинаковый 401
UNAUTHORIZED с `WWW-Authenticate: Bearer`. Чужой/отсутствующий ресурс — одинаковый
404. `ContractRoute` возвращает безопасную валидацию 422 без исходного пароля.
Error имеет `{error:{code,message,details,request_id}}`; существующий request_id
сохраняется, иначе создаётся UUID. Не выдаются SQL, connection credentials, raw
provider response или traceback. SQLAlchemyError — 500 INTERNAL_ERROR.
Неизвестный маршрут — 404 NOT_FOUND; неверный метод — 405 METHOD_NOT_ALLOWED с
Allow. Последний — локальное расширение списка HTTP-статусов C-01, форма та же.
Проектные 409/429 описаны, но предметный rate limiting проектов этим не реализован.
Refresh tokens/регистрации/командных ролей нет.

## HTTP endpoints и DTO

Все пути ниже относительно `/api/v1`; актуальная полная спецификация — C-01.

| Endpoints | Подключение |
|---|---|
| POST `/auth/token`; POST/GET `/projects`; GET `/projects/{id}` | Штатная factory, реальные PostgreSQL repositories |
| GET `/projects/{id}/runs`, `/runs/{id}`, `/runs/{id}/events` | Штатная factory с PostgreSQLRunReadRepository; отдельный configure_runs также сохранён |
| POST `/runs/{id}/cancel`, `/runs/{id}/retry` | Штатная factory: RunControlService над общим PostgreSQLRuntimeRepository |
| POST `/projects/{id}/runs` | Штатная factory с PostgreSQLRunCreationRepository и PostgreSQLRunQueue |
| POST answers; artifacts/archive/exports | Контракты есть; общего подключения/полного runtime пока нет |

Не отдавайте внутренний RunRecord как HTTP Run: aggregate version, generation,
attempt_id, state_version, input_hash и private data не добавляются в закрытые DTO.
Даты UTC ISO 8601 с Z, UUID — строки. HTTP Run.stages содержит **все** попытки,
сортировка `(stage_id, attempt_no)`; максимум номера нужен лишь для текущего состояния.

## Чтение Run

`RunQueryService(repository, cursor_secret=...)` синхронно предоставляет keyword-only
`list_runs(owner_id, project_id, limit=100, cursor=None)`, `get_run(owner_id, run_id)`,
`list_events(owner_id, run_id, after_seq=0, limit=100)`. DTO — в `run_queries/dto.py`.
`RunReadRepository` проверяет Project.owner_id на каждом запросе, включая events;
`project_exists` отличает чужой проект от пустой истории. `OwnedRun` даёт сервису
дополнительную проверку владельца. Duplicate attempts, неверный порядок, несогласованный
owner/run/project и лишние строки отклоняются, а не «чинятся» на чтении.

История — keyset по `(created_at, run_id)` вниз; cursor подписан и привязан к owner,
project и версии. Events — `seq > after_seq` вверх, limit 1–100, repository получает
limit+1 (до 101). `next_after_seq` — последний seq либо исходный cursor пустой
страницы; `has_more` относится к текущему чтению, не запрещает будущие commits.
Пропуски seq допустимы, переупорядочивание/поздняя видимость меньшего seq — нет.

`InMemoryRunReadRepository(projects={project_id:owner_id}, runs=[OwnedRun(...)],
events=[Event(...)])` — только явный локальный снимок: копии detached, конфликты
владельца/Run/seq отклоняются. Не persistence и не concurrency adapter. Пример:

```python
from backend.app.services.run_queries import RunQueryService
from backend.app.services.run_queries.memory import InMemoryRunReadRepository
service = RunQueryService(InMemoryRunReadRepository(projects={project_id: owner_id}),
                          cursor_secret=cursor_secret)
page = service.list_runs(owner_id=owner_id, project_id=project_id)
```

## Совместимость профилей

Новые Run — только `web-fastapi-angular`; read/control DTO сохраняют исторический
`web-fastapi-react` без переименования snapshots/ревизий. Исторический профиль —
только чтение: server control/worker admission возвращает 409 UNSUPPORTED_RUN_PROFILE,
UI также скрывает команды. A-02 принимает только активный профиль. Не заменяйте
profile_id профилем по умолчанию при восстановлении; profile_version — другое поле.

## Lifecycle и события

`LifecycleService(repository, guard=..., checkpoint_confirmed=None)` — внутренний
API. `create(owner_id,run_id,context,data)` регистрирует queued с начальными events;
это не HTTP createRun. `get` даёт копию RunRecord, `events` — owner-scoped EventPage.
`transaction(owner_id,run_id,expected_version)` создаёт атомарную группу:

- `tx.update_data(callback)` изменяет копию только domain data: accepted refs,
  usage, checkpoint intent, outbox, replay; не status/generation/context/history.
- `transition_run(expected,target,reason_code)` проверяет C-03, stopped_at и барьеры.
- `register_attempt(stage_id,contract_version,input_hash,reason_code,attempt_id=None)`
  создаёт pending N+1 с новым UUID, не переписывает succeeded.
- `transition_attempt(attempt_id,expected,target,generation,reason_code)` и
  `fence_attempt(attempt_id,generation)` отсеивают устаревшее исполнение.
- `publish(event_type,data,stage_id=None)` добавляет разрешённый domain event;
  `snapshot` возвращает отделённую копию.

`guard(record,change)` обязателен, работает на копии authoritative state под
блокировкой, одобряет только возвратом None, отказывает LifecycleError. Change
содержит kind/source/target/attempt_id/stage_id/event_type/event_data. Для
register_attempt guard видит предложенную попытку. Production guard проверяет
профиль, реальные артефакты/права/versions, ответы, бюджет, lease, outbox/replay,
готовность manifest и зависимостей. Безусловно разрешающий guard допустим только
в явно ограниченных тестах. Сеть/LLM/saver write под транзакционной блокировкой запрещены.

CAS `version` начинается с 1 и растёт один раз за изменяющую транзакцию, независимо
от числа events. No-op не меняет version/seq/updated_at. Результаты из with ещё
не committed; публикуйте их только после выхода. Ошибка команды, guard, callback,
версии/поколения или commit откатывает всю группу. Пойманная внутри with ошибка
всё равно помечает транзакцию aborted; закрытый tx использовать нельзя.

completed/cancelled неизменяемы. Stopped Run не коммитит active pending/running.
Resume после ответов требует pending N+1; обычный retry сохраняет input_hash и
contract_version, ответы могут создать новый input_hash при прежнем контракте.
Уже принятые succeeded/skipped допускают внутреннее восстановление checkpoint/
сборки без новой агентской попытки. До completed каждый latest succeeded требует
`checkpoint_confirmed(record,attempt) is True`; отсутствие callback — CHECKPOINT_REQUIRED.
Callback должен проверить реальную durable saver confirmation по Run/revision/
attempt/input/contract, а не receipt/intent. Его интеграция и guard зависимостей — #128.

Общий `next_event` проверяет уникальный возрастающий seq и watermark. Нейтральная
EventSequenceError преобразуется в доменный RUN_STATE_CONFLICT вызывающего сервиса.
Состояние и events меняются вместе; не публикуйте события после commit отдельной
операцией. Run.created/status и stage.status создаёт движок, publish их не подделывает.
Сохраняются Event.data.schema_version=1.0 и безопасные reason_code. Не публикуйте
lease, idempotency keys, prompt, chain-of-thought, provider bodies и stack traces.

Для SQL все writers блокируют **одну** Run row до MAX(seq)+1/MAX(attempt_no)+1 и
в одной транзакции обновляют projections, accepted refs, outbox и replay. Unique
constraints — последняя защита, не allocator. `SQLiteLifecycleRepository(path)` и
`SQLiteControlStore(path)` используют файлы, BEGIN IMMEDIATE и synchronous=FULL;
они локальные адаптеры, не production. Нельзя одновременно управлять одним Run
из двух независимых агрегатов. Штатный runtime использует общий PostgreSQL write
adapter B-11; локальные примеры по-прежнему остаются изолированными.

## Управление Run

`RunControlService(store, authorize_retry=policy)` проверяет owner в каждой
транзакции. Публичный `cancel(owner_id,run_id)` без Idempotency-Key: queued/running/
waiting_for_input → cancelling, outbox, запрет новой работы; pending отменяются,
running доходят до безопасной границы. Если active нет — cancelled. Повтор cancel
и отмена прочих stopped/final статусов — no-op без events/attempts/usage (кроме
исторического профиля, где сама команда запрещена).

`retry(owner_id,run_id,key,body)` принимает Retry `{}` либо
`{"limits":{"max_calls":12,"max_output_tokens":60000}}`. Ключ `[A-Za-z0-9._:-]`,
1–128 символов. Replay после owner/profile check сравнивает hash тела до проверки
текущего статуса и возвращает исходный сохранённый Run, не новый GET. Другое тело
с тем же ключом — 409 IDEMPOTENCY_KEY_CONFLICT. Scope — owner+Run+retryRun.

Manual retry — только failed/budget_exceeded без active attempts, выбирает latest
failed current_stage либо первый failed stage_id лексикографически. Создаёт N+1
pending/new UUID с прежними input_state/hash/contract_version. История, успешные
независимые этапы, frozen profile, usage и автоматические retry counters не сбрасываются.
Queued, events, outbox, бюджет и replay фиксируются вместе. `limits:null`/лишние
поля/неверная форма — 422; недостаточные сохранённые входы — 500 RESUME_STATE_UNAVAILABLE.

Если failed Run имеет только принятые latest succeeded/skipped, manual retry
возвращает **409 RECOVERY_REQUIRED** без эффектов. UI показывает необходимость
внутреннего восстановления checkpoint/сборки, не обещая автоматический запуск.
Существующий Lifecycle recovery разрешает failed → queued по сохранённым
результатам без register_attempt; реальный dispatcher ещё нужен. C-03 §5.1
описывает восстановление failed через POST /retry шире текущего manual consumer;
эта граница интеграции остаётся #128, а не скрывается документацией.

Worker hooks: `start_attempt(...stage_id,attempt_id)` допускает pending ровно один
раз и возвращает совместимый checkpoint либо saved_state. UUID не заменяет lease
generation. `admit_call(...reserve_budget)` сериализует каждый вызов с cancel и
резервом; после успешного callback всегда публикует metrics.updated. Исключение
callback откатывает допуск/event. `finish_attempt(...accepted_result,save_boundary)`
сверяет Run/stage/attempt/contract, сохраняет validated result, refs/usage, полный
snapshot/checkpoint и времена одной транзакцией. Callback merge учитывает другие
параллельные результаты; его ошибка откатывает барьер. В cancelling поздний валидный
результат сохраняется без completed/failed; после последнего active — cancelled.
`reach_boundary` обслуживает отмену без active call. Stale/повторный результат — 409.

Checkpoint сверяется по Run/revision/contract/input_hash/state_version. Authoritative
saved_state — fallback при несовместимом checkpoint, не выдуманный пустой граф.
POST не требует готового checkpoint; продолжение и completed требуют барьера C-03.
Отказ до принятия результата и ошибка после durable receipt — разные случаи:
accepted receipt нельзя терять или превращать в новый платный вызов.

## Бюджет и время

| stop_reason | Public retry |
|---|---|
| MAX_CALLS | Нужен достаточный рост именно max_calls |
| MAX_OUTPUT_TOKENS | Нужен достаточный рост именно max_output_tokens |
| MAX_INPUT_TOKENS / RUN_TIMEOUT / неизвестный | 409 RETRY_NOT_SUPPORTED до поиска failed stage, даже до старта следующего узла |

Public Limits содержит только calls/output. Нельзя уменьшать другой предел;
нужны positive remaining capacity относительно cumulative metrics и
`required_limits` с резервами следующей работы. `authorize_retry(record,limits)`
обязателен: политика проверяет разрешения, лимиты и слот активного Run в той же
транзакции. Для 429 задаёт `ControlError(...,retry_after=positive_integer_seconds)`;
HTTP передаёт Retry-After без угадывания. Отказ не создаёт частичных эффектов.
Клиент/текст требований не расширяет полномочия. Полный ledger/reservation producer —
[#129](https://github.com/Krastti/RWB-Hack/issues/129).

Различайте request timeout, active attempt/Run time и публичный elapsed duration_ms.
Active интервалы исключают очередь/ожидание человека и считают пересечения один
раз; elapsed идёт от исходного created_at, включая паузу retry, и фиксируется
stopped_at. Сумма параллельных latency не равна elapsed. Provider null tokens
хранятся как неизвестные; Metrics — целые известные суммы, полнота в event отдельно.

## Проверки и ограничения

Из корня с установленным runtime:

```sh
PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests -v
.venv/bin/python -m backend.app.services.lifecycle.example
.venv/bin/python -m backend.app.services.run_control.example
```

Примеры — local control fixtures, не модель и не business generation. Для реальных
PostgreSQL-тестов задайте `RWB_TEST_DATABASE_URL` на **отдельную UTF-8 тестовую БД**;
они применяют миграции в собственных схемах и удаляют только созданные схемы.
Без неё тесты SKIPPED. Не используйте production-БД. Фактическое выполнение
Flyway/server/user creation/настоящего PostgreSQL нельзя выводить из offline unit-тестов.

План backend: B-01–B-04 — данные/auth/projects; B-05–B-11 — persisted Run, commands,
queue/lease, read и factory composition; B-12–B-13 — ответы/worker bridge;
B-14–B-18 — storage, artifact API, ZIP, GitLab и wiring; B-19–B-21 — ModelCall,
пользовательский слот и общий учёт. Схемы и локальные порты не заменяют эти связи.
При отключении локальной интеграции уберите configure hook; сохранённые данные,
accepted receipts и outbox не удаляйте. Порядок выпуска/revert — CONTRIBUTING.

### Явная HTTP-композиция

Для собственного entrypoint настройте auth один раз через
`app.api.auth.configure_auth(app, AuthService(user_repository, auth_config))`.
`current_user`, `get_auth_service`, router находятся в том же модуле;
`app.api.projects.configure_projects(app, ProjectService(project_repository))`
подключает проекты. Read/control требуют отдельных **реальных** портов:

```python
from backend.app.api.runs import configure_runs
from backend.app.services.run_queries import RunQueryService
from backend.app.services.run_control.api import configure_run_control
from backend.app.services.run_control import RunControlService
configure_runs(app, RunQueryService(read_repository, cursor_secret=auth_config.secret_key))
configure_run_control(app, RunControlService(shared_store, authorize_retry=retry_policy))
```

`create_runs_router()` доступен для DI с переопределением `get_run_query_service`.
Configure вызывается один раз после auth. Этот пример показывает точки сборки,
а штатную PostgreSQL сборку предоставляет `create_app()` / `compose_runtime()`. Response_ref
идемпотентности разрешает исходные status/body из durable response store, не
ссылается на изменяемый текущий GET. Operation scopes: createRun использует
project UUID, answerQuestions/retryRun/createExport — Run UUID; keys/hashes не
публикуются в events/logs, validation errors ничего не резервируют, records без TTL.
При конфликте SQL locking/IntegrityError обработчик должен вернуть согласованный
409, не коммитить placeholder. Успешный 202 подтверждает принятие, не исполнение.


## Очередь и worker adapter

Существуют `app.adapters.queue.celery.create_worker_app` и `app.workers.StageWorker`.
Это готовые transport/lease seams, не полный orchestrator и не подключённый
HTTP→graph pipeline. Нужны PostgreSQL, Redis/RabbitMQ, callable Orchestrator и
RecoveryHandler; фабрика не выбирает бизнес-функцию из model/message payload.
Установка — общий requirements-worker с Celery Redis extra. Celery result backend
не используется: результаты хранятся в БД/artifact store.

`create_worker_app(*,broker_url,sessions,orchestrator,recovery,config=QueueConfig())`
возвращает Celery с `app.queue_adapter`. Публичные методы adapter:
`enqueue(transaction:Session,attempt_id:UUID)->UUID` сохраняет одну команду для
latest pending StageAttempt, без commit/публикации/нового номера; replay возвращает
тот же UUID. Регистратор берёт Run FOR UPDATE, создаёт attempt/events/metrics и
вызывает enqueue в **той же** транзакции. Команда createRun до StageAttempt и
команда исполнения этапа — разные сущности; RunIntakeDispatcher регистрирует первую
попытку атомарно с WorkerCommand, не выдавая create job за готовый этап.

`publish_due(limit=100)` резервирует окно доставки и отправляет только UUID,
возвращает published/failed counts. Dispatch task `rwb.queue.dispatch.v1`, execution
`rwb.stage.execute.v1`; PostgreSQL остаётся источником недоставленных команд.
После ошибки/недоставки повторяется тот же command_id без новой StageAttempt.
Celery использует JSON-only, persistent delivery, late ACK и prefetch=1;
при prefork фабрика сбрасывает унаследованный DB pool.

`QueueConfig`: queue_name=rwb.stages, control_queue=rwb.control (должны различаться),
lease_seconds=90 (≥15), heartbeat_seconds=20 (строго чаще трети lease),
redelivery_seconds=30, dispatch_seconds=5. Broker/DB/network deadlines задаются
интегратором; рекомендуемые engine connect_timeout=5, statement_timeout=10000 ms.
Stage worker и control worker разделяются, чтобы длинный этап не блокировал dispatch.

Миграция V5 создаёт WorkerCommand с уникальной attempt_id и состояниями pending,
running,lost,recovering,completed,obsolete, generation/recovery_generation,
owner_token/expires_at и next_delivery_at. Требуется **V1–V3 → V4 → V5**: V5
структурно ссылается на V2, но Flyway после V5 не должен получать более раннюю V4
без согласованного out-of-order решения. V4 и V5 уже присутствуют в дереве.
Production запуск worker здесь не считается проверенным и не предлагается с
готовым module path. Интегратор должен создать engine/sessionmaker и вызвать
create_worker_app с реальными orchestrator/recovery. Синтаксис будущего запуска
из backend (это схема команды, а не исполняемая команда поставляемого модуля):

```text
celery -A <реальный_модуль>:app worker -Q rwb.stages --concurrency=2
celery -A <реальный_модуль>:app worker -Q rwb.control --concurrency=1
celery -A <реальный_модуль>:app beat
```

Готового integration.worker в репозитории нет; выполнение worker/beat с реальной
PostgreSQL/брокером для документационной проверки — SKIPPED.

Worker блокирует Run→WorkerCommand→StageAttempt, проверяет latest attempt и
выдаёт lease(owner_token,generation,expires_at) по времени PostgreSQL. Первый claim
переводит queued→running/pending→running с C-03 events; новую попытку/метрику attempts
не создаёт. Duplicate с активным lease не вызывает callable. Obsolete/completed/
несуществующая команда не исполняется; waiting_for_input/отмена/stopped Run
не запускают pending. Следующая попытка ждёт завершения/recovery предыдущей команды.

`StageCommand` содержит command_id/run_id/attempt_id/stage_id/attempt_no/input_hash/
contract_version; callable `(command,execution)->None` получает доверенный context.
`LeasedExecution` ведёт heartbeat отдельным потоком/Session и предоставляет lease,
heartbeat и `transaction(recovery_complete=False)`. Последний проверяет fencing
до и после предметной записи, commit принадлежит context. Прямые Session.commit,
SessionTransaction.commit и Connection.commit блокируются SQLAlchemy hooks;
rollback/close не подтверждают результат. Raw DBAPI/SQL transaction control и
изменение worker_commands из callable не входят в интерфейс.

Orchestrator сохраняет реальные accepted refs/статусы/usage/events/checkpoint intent
через execution.transaction, не под Run-lock во время LLM. Immutable external bytes
можно записать до транзакции, но их принятие требует fenced commit. К возврату
attempt должен выйти из pending/running; иначе нужна recovery. Успешная команда
фиксируется completed и не вызывает callable при redelivery.

Expired lease не разрешает повторно вызывать старую попытку. Worker записывает
WORKER_LOST, закрывает ещё running attempt как failed, вызывает recovery под
новым lease; ошибка callable — ORCHESTRATOR_FAILED. Принятый success не заменяется
ошибкой транспорта. Причина в execution.lease.reason_code. Recovery идемпотентен
по command_id и сохраняет реальное решение через **transaction(recovery_complete=True)**,
атомарно ставя recovery_generation текущего lease. Пустой return/обычная transaction
не подтверждают recovery: команда снова lost и будет доставлена. Другой generation
не принимается. После сбоя между recovery commit и complete новый recovery проверяет
уже сохранённое решение. Новый LLM при разрешении — N+1 и новая команда; принятый
success восстанавливает checkpoint без LLM. Потерянный физический вызов может
завершиться: fencing не делает external call exactly-once, расход не обнуляется.

Callable/heartbeat/delivery/recovery errors логируются Python logger с traceback,
command_id и для worker Run/attempt/generation/recovery. Это служебные логи,
не HTTP/Event payload; callback должен избегать секретов в исключениях. Lease keys
не публикуются. При ошибке сохранения recovery отдельно сохраняется диагностика
исходной ошибки. После приёма команд V5 не удаляется откатом Python; остановите
новые исполнения, сохраните очередь для восстановления. До приёма данных её
удаление — отдельное согласованное изменение схемы.

Standalone queue store пишет ORM state/events напрямую, а local lifecycle/control
имеют собственные store seams. Штатный B-11 runtime использует RuntimeQueueStore и
PostgreSQLRuntimeRepository: claim/lost transitions проходят через lifecycle service
под общим Run lock, control использует те же SQL rows/events. AnalysisGraph ArtifactStore
и checkpoint также должны принимать результаты через этот fenced context (#130),
иначе старый worker обходит защиту. Эту отсутствующую связку документация не
объявляет реализованной.

`StageWorker.execute(command_id)` возвращает already_handled/completed/recovered/
fenced; Celery task при LeaseBusy возвращает lease_busy, при временной ошибке БД —
delivery_pending. Здесь не используется task.retry: единый планировщик redelivery —
PostgreSQL next_delivery_at + dispatch, heartbeat переносит окно running-команды.
Потеря сообщения/брокера не удаляет command; дубль после публикации безопасен.
Для отката transport останавливают beat и workers, а таблицу принятых команд
сохраняют до обработки/восстановления. Готовность этой схемы на реальной БД/брокере
не подтверждается offline-тестами остальных модулей.


### Budget stop между этапами

`budget_exceeded` может быть сохранён до регистрации следующего StageAttempt.
Для MAX_CALLS/MAX_OUTPUT_TOKENS public retry допускает первую попытку ещё не
исполнявшегося ready-узла, не переписывая успешные этапы в failed. В private
`Record.saved_state` producer сохраняет frozen `generation_plan` по C-02 и
`pending_inputs[stage_id]={input_hash,input_state}`: полную проверенную AgentInput
будущего этапа с attempt=1 и неизменяемыми входными artifact IDs. Эти данные не
приходят из публичного Retry и не добавляются в HTTP DTO. Runtime выбирает первый
ready task в порядке плана; уже зарегистрированные stage_id не создаются заново.

Проверяются plan/profile/contract, C-02 input context и hash format, точное
соответствие разрешённым input IDs задачи. Dependencies требуют accepted latest
succeeded/skipped receipts и согласованный checkpoint Run/revision/state_version/
contract с authoritative saved_state. Producer/worker по-прежнему проверяет bytes,
ownership, input hashes и реальные saver receipts; сервис не выдумывает эти данные.
Нет полного frozen state/ready task/подтверждения — RESUME_STATE_UNAVAILABLE без
эффектов (UI показывает server error envelope). Затем обычные budget reservations,
sufficient cause-specific increase и authorize_retry. Новая **первая** pending
attempt, metrics/events/outbox/replay фиксируются атомарно, receipt прежнего этапа
и usage не меняются. retry.scheduled с выдуманным previous_attempt_no не создаётся:
используется stage.status_changed from=null плюс status/budget/metrics events.
Повтор ключа возвращает исходный ответ без новой attempt/outbox/event. #129 producer
должен сохранять этот private resume context до остановки, а не угадывать этап.

Event allocator проверяет порядок и уникальность полной истории один раз при
открытии сервисной транзакции под блокировкой Run (O(n) времени и памяти).
Дальнейшие append используют локальный watermark за O(1), проверяя длину и хвост
истории; cursor не сохраняется и не переносится между транзакциями. Исключение
откатывает события и состояние вместе. Общая сериализация локального агрегата
по-прежнему зависит от размера истории; production PostgreSQL adapter остаётся
отдельной интеграцией (#128, #137).
При completed checkpoint checker получает один отделённый снимок для всех
успешных этапов. Checker должен только читать снимок: изменение отклоняется до
перехода статуса и фиксации событий.



## Создание Run и PostgreSQL read composition

`RunCreationConfig(limits=Limits(...))` задаётся через `create_app(run_config=...)`
с приоритетом над окружением. RUN_MAX_CALLS/RUN_MAX_OUTPUT_TOKENS необязательны:
по умолчанию C-05 limits.run (60/180000). Заданные значения — положительные целые;
ошибка конфигурации останавливает factory до открытия соединений. Только DATABASE_URL
и JWT_SECRET_KEY обязательны. Тело createRun не меняет серверный бюджет.

`RunCreationService(repository,limits=...,profile=...).create_run(owner_id=...,
project_id=...,idempotency_key=...,body=RunCreate(...))` возвращает CreationResponse
(status_code и исходное JSON body). `RunCreationRepository` задаёт атомарную запись;
`PostgreSQLRunCreationRepository(sessions,queue)` использует PostgreSQLRunQueue.
`configure_run_creation(app,service)` сохранён для отдельной композиции после auth.

POST `/api/v1/projects/{project_id}/runs` принимает requirements_text и optional
Angular profile, обязательный Idempotency-Key; возвращает 202 queued Run с новой
revision_id, limits, пустыми stages/questions и нулевыми metrics. Project.updated_at
обновляется; исходные ревизии не меняются. RunCreate отвергает whitespace-only 422,
валидные bytes и SHA-256 сохраняются без trim. V4 хранит run_input_snapshots (текст,
hash, revision, полный profile/contracts/effective limits), run_creation_responses
(исходный HTTP status/body) и уникальные run_queue_jobs createRun.

Scope createRun: owner/project/key. Hash — нормализованное валидированное тело с
profile default и стабильным JSON порядком плюс scope. Пропущенный profile равен
явному default; изменение пробелов требований меняет fingerprint. Доступ проверяется
до replay; совпавшие key/body возвращают исходный 202 независимо от нынешнего Run,
настроек и рестарта. Иное тело — IDEMPOTENCY_KEY_CONFLICT; одновременная операция
той же области — IDEMPOTENCY_IN_PROGRESS через pg_try_advisory_xact_lock.

После replay берётся User FOR UPDATE, затем Project FOR UPDATE. Один active Run
во всех проектах пользователя даёт 429 RATE_LIMIT_EXCEEDED **без Retry-After**:
момент освобождения слота неизвестен (в том числе при waiting_for_input). Повтор
имеет смысл после остановки активного Run. Это отличается от control policy,
которая передаёт вычисленный retry_after. Future writers/resume соблюдают тот же
порядок User→Project/Run; номер ревизии выделяют под Project lock. Isolation —
PostgreSQL READ COMMITTED. Любой сбой откатывает revision/run/key/response/job/events;
validation/rate denial не резервируют ключ. Response_ref — UUID immutable ответа
без TTL. Keys/hash не публикуются в events.

`RunQueue.enqueue(transaction:Session,run_id:UUID)->None` добавляет job в эту же
транзакцию, не делает commit и не обращается к брокеру. Начальные run.created seq1
и metrics.updated seq2 получают timestamp=Run.created_at после ожидания locks,
не transaction-start now(). 202 — durable принятие job, не исполнение графа.
Create-job и worker StageCommand остаются разными сущностями; B-11 intake dispatcher
связывает их через атомарную регистрацию первой analysis-попытки. Предметное
преобразование private intake input в C-02/LangGraph остаётся #128/#130.

`PostgreSQLRunReadRepository(sessions)` из services/runs/postgresql.py реализует
RunReadRepository той же БД и монтируется factory. Каждый SQL owner-scoped;
Run и все StageAttempts загружаются одним SELECT/joinedload. History —
created_at/id descending с keyset, events — seq ascending; cursors/extra owner
checks остаются в RunQueryService. Stored questions/metrics/events не выдумываются;
invalid saved snapshot преобразуется в безопасный 500 INTERNAL_ERROR. Query service
сам не зависит от ORM и не устанавливает локальный fallback. Production SQL,
миграции и конкурентный HTTP требуют проверки реальной инфраструктуры.

Rollback createRun: остановить POST/исполнителей и откатить код, не удалять V4
таблицы с принятыми входами/ответами/jobs. Только до приёма данных возможно
согласованное удаление run_queue_jobs → run_creation_responses → run_input_snapshots.
