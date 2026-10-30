# B-06: жизненный цикл и события

Задача [#66](https://github.com/Krastti/RWB-Hack/issues/66).
Нормативный контракт — [C-03 v1.0](../../../../contracts/lifecycle.md),
wire-схемы статусов, Event и EventPage —
[C-01 / API 1.0.0](../../../../contracts/platform.openapi.yaml).
Попытка и идентичность входов следуют [C-02 schema_version=1.0](../../../../contracts/agents/README.md).
База реализации: `ccb5620ae4122c6c4fbad6ea2c4cf6d4d53831c7`;
версия C-03 принята в `ef1956c958ef64c8784ce0558c53269bea25142f` (#53/#99).
Общие контракты и чужие модули не изменены.

## Назначение и границы

Модуль проверяет таблицы переходов, ожидаемые состояния, версию Run и поколение
попытки. Под общей блокировкой репозитория он согласованно сохраняет состояние,
историю попыток, данные предметных модулей и события с монотонным seq.
Нет отдельной записи события после commit состояния или счётчика seq в worker.

В main приняты [ORM-модели B-05](../../models/README.md) и read-only интерфейс B-07
из services/run_queries. Транзакционного write repository с version/generation,
checkpoint/outbox/replay пока нет. Поэтому определён публичный LifecycleRepository
и явно выделен локальный SQLite-адаптер. Модуль не подключён
автоматически к create_app: он не вводит production SQLite вместо PostgreSQL,
новые HTTP endpoints, очередь или работающий граф. Это результат B-06;
готовность полного пайплайна не заявляется.

## Публичный интерфейс

Импорты — из `backend.app.services.lifecycle` (либо `app.services.lifecycle` при
PYTHONPATH=backend; не смешивать оба варианта в одном процессе).

| Интерфейс | Назначение |
|---|---|
| LifecycleService(repository, guard=...) | Обязательные транзакционный репозиторий и серверная проверка предметных условий |
| create(owner_id, run_id, context, data) | Атомарная внутренняя регистрация queued, run.created и начального metrics.updated; не HTTP POST /runs |
| transaction(owner_id, run_id, expected_version) | Одна атомарная группа эффектов, переходов и событий |
| get(owner_id, run_id) | Отделённая копия внутреннего RunRecord, включая version, попытки и private data |
| events(owner_id, run_id, after_seq=0, limit=100) | Владелец + курсор, EventPage по C-01 |
| tx.update_data(callback) | Изменения domain data внутри той же транзакции: реальные artifact refs, счётчики, восстановление, outbox, replay |
| tx.transition_run(expected, target, reason_code) | Проверка и фиксация перехода Run + run.status_changed |
| tx.register_attempt(stage_id, contract_version, input_hash, reason_code, attempt_id=None) | Новый UUID и pending N+1; stage.status_changed с from=null |
| tx.transition_attempt(attempt_id, expected, target, generation, reason_code) | Проверка поколения и переход попытки + stage.status_changed |
| tx.fence_attempt(attempt_id, generation) | Отсечь активное поколение до фиксации WORKER_LOST/дедлайна; вернуть новое поколение |
| tx.publish(event_type, data, stage_id=None) | Согласованно добавить проверенное предметное событие |
| tx.snapshot | Отделённая копия состояния для чтения; изменение копии не меняет хранилище |

RunRecord и Attempt — **внутренние** модели, не расширение закрытого C-01 Run/
StageAttempt. UUID попытки, input_hash, generation, version, context и private data
нельзя напрямую отдавать как HTTP Run. Event/EventPage сериализуются через
`model_dump(mode="json", exclude_none=True)` / `model_dump_json(exclude_none=True)`:
UTC с Z, UUID строками, необязательный stage_id без значения опущен.

`version` — версия агрегата для CAS, не contract_version и не seq. Создание даёт
version=1. Успешная изменяющая транзакция увеличивает её ровно на один независимо
от числа событий. Пустая транзакция/неизменившийся update_data не меняют version,
updated_at и seq. Возвращаемые внутри with объекты ещё не закоммичены: публиковать
их клиенту/в очередь можно только после успешного выхода; новую version читать get.

## Переходы и обязательные проверки

Наборы RunStatus/StageStatus точно соответствуют OpenAPI; RUN_TRANSITIONS и
STAGE_TRANSITIONS в service.py воспроизводят таблицы C-03 §2–3. Самопереход и
неожиданное исходное состояние дают 409 RUN_STATE_CONFLICT без нового события.
No-op повтор cancel и replay по Idempotency-Key принадлежат B-10/B-05/B-12:
в этих случаях вызывающий модуль возвращает сохранённый ответ, не вызывая переход.

Дополнительные неизменяемые правила движка:

- completed/cancelled запрещают новые переходы, попытки, private data и события.
- waiting_for_input/failed/budget_exceeded/cancelled/completed фиксируются только
  после сведения всех running/pending-попыток на безопасный барьер. Это также
  проверяется при commit: нельзя отдельно сохранить pending-повтор в stopped Run.
- completed требует хотя бы одного завершённого узла и отсутствия unresolved
  последних результатов. skipped ещё должен быть допустим по конкретному профилю.
- failed/budget_exceeded → queued и waiting_for_input → running требуют pending
  N+1 после соответствующей failed/waiting_for_input попытки. Старые строки не
  возвращаются в running, successful/skipped результаты не повторяются автоматически.
- Retry сохраняет input_hash и contract_version. После ответа допустим новый
  input_hash для waiting_for_input, но с прежней contract_version; предметный
  модуль обязан подтвердить неизменность исходных требований и валидность ответов.
- cancelling запрещает новые попытки и pending → running. Уже активный результат
  можно сохранить; он не меняет cancelling на completed/failed/budget_exceeded.
- started_at появляется только при pending → running; все закрывающие попытку
  переходы, включая waiting_for_input, задают finished_at. До исполнения started_at
  не подменяется временем регистрации. История сортируется по stage_id/attempt_no.

`guard(record, change) -> None` — обязательный серверный адаптер предметных условий.
Он получает копию текущего состояния **внутри транзакции**, а не пользовательские
флаги из HTTP. Для register_attempt копия содержит предложенную новую попытку.
Change описывает kind, source/target, attempt_id/stage_id или event_type/event_data.
Успех — None; отказ — LifecycleError с безопасными status/code/message. Возврат
False/True вместо ошибки не трактуется как разрешение. Без guard сервис не создаётся.

Ответственность интеграционного guard по C-03:

| Операция | Реальные условия от owning-модуля |
|---|---|
| create / queued → running | Авторизация, immutable snapshot/профиль/версии, право worker; начальные idempotency/outbox — B-05/B-08 |
| Результат попытки | schema/identity/references/checksum/path/profile-валидация; сохранённые артефакты и usage — worker/A-04 |
| running → waiting_for_input | Текущая ревизия вопросов, required без answer, сохранённые частичные данные — B-12 |
| waiting_for_input → running | Принят полный пакет обязательных ответов, новый неизменяемый вход, надёжное resume-намерение — B-12 |
| failed/budget_exceeded → queued | Явный разрешённый retry, новая попытка, outbox/replay; для бюджета — достаточное увеличение исчерпанных лимитов — B-10/B-09 |
| running → failed/budget_exceeded | Сохранённые диагностика/расход/частичный snapshot; автоматические меры исчерпаны или неприменимы — worker/B-09 |
| cancelling → cancelled | Все разрешённые исполнения завершены/отсечены, безопасные результаты/неизвестный расход сохранены — B-10 |
| running → completed | Все условия C-03 §8: required stage coverage, профиль, manifest/snapshot, принятые результаты/ответы, нет checkpoint pending — A-04/assembly |
| register_attempt / publish | Правильные input refs, план/маршрут, лимит автоповторов и бюджета; схема/безопасность data и реальные учётные факты — соответствующий модуль |

Движок не может подтвердить сохранность внешних артефактов или готовность профиля
одним названием статуса. В production нельзя подставлять безусловно разрешающий
guard. `local_guard` из example.py допускает только узкие control-fixtures примера.
Ни guard, ни callbacks не должны выполнять LLM/сетевые операции под блокировкой.

## Одна транзакция для эффектов и событий

```python
record = service.get(owner_id=owner_id, run_id=run_id)
with service.transaction(
    owner_id=owner_id, run_id=run_id, expected_version=record.version,
) as tx:
    # Worker ранее проверил настоящий результат и сохранил неизменяемые байты.
    # Здесь под той же блокировкой merge refs/usage/checkpoint intent/outbox/replay.
    tx.update_data(persist_accepted_result)
    tx.transition_attempt(
        attempt_id=attempt_id, expected="running", target="succeeded",
        generation=lease_generation, reason_code="RESULT_ACCEPTED",
    )
    # completed — отдельный общий барьер после подтверждения checkpoint/assembly.
```

Callback получает копию только data, не может через неё перезаписать status,
generation, context или историю событий. Сохранять нужно полное согласованное
состояние, включая результаты других параллельных этапов; не заменять его
устаревшим worker snapshot. Предметные условия должны оставаться истинны до
commit; не удалять принятые refs/outbox после разрешённого guard перехода.
Изменяемые предметные эффекты рекомендуется подготовить до статусных переходов.

Любое исключение, отказ guard, неверный expected status/version/generation либо
ошибка записи/commit откатывают всю группу: state/data/events/seq/version.
Если вызывающий код поймал ошибку команды внутри with, транзакция всё равно
помечается aborted и не коммитит предыдущие эффекты. Закрытый tx нельзя использовать.

Lease поколения хранится отдельно для каждой попытки. pending → running увеличивает
generation; результат должен предъявить актуальные attempt_id и generation.
fence_attempt увеличивает generation, после чего старый worker не может применить
ответ даже с заново прочитанной version Run. Lease выдача/срок/heartbeat, отмена
внешнего вызова и выбор новой попытки остаются у worker/A-04/B-08/B-10. Не пытаться
возобновить потерянный вызов переводом running → running.

seq выделяется из сохранённого last_seq под блокировкой агрегата; может отличаться
от len(events). Начало — 1; перезапуск/ручной retry не сбрасывают счётчик. Локальный
адаптер не создаёт пропуски после rollback; контракт допускает пропуски у других
адаптеров, но не позднюю видимость меньшего уже закоммиченного seq. Глобального
порядка между Run нет. Повторная доставка **сохранённого Event** сохраняет его seq;
повторный publish — новый факт, не dedup запроса (ключи идемпотентности ведёт владелец команды).

run.created/run.status_changed/stage.status_changed создаёт только движок; через
publish их подделать нельзя. Каждое событие получает data.schema_version=1.0.
reason_code — безопасный код, не raw exception. generation/lease/Idempotency-Key
движок в события не пишет. Для внешних событий owning-модуль проверяет схему и
исключает prompt, provider response, chain-of-thought, секреты, stack traces и ключи.
B-09 публикует реальные metrics/usage и полноту токенов после изменения счётчиков;
регистрация попытки здесь сама по себе не заявляет готовность учёта всех метрик.

events возвращает только seq>after_seq, limit 1–100; next_after_seq для пустой
страницы равен входному курсору, has_more относится к текущему снимку. История
owner-scoped: отсутствующий и чужой Run одинаково дают 404 NOT_FOUND. Отрицательный
курсор/неверный limit — 422 VALIDATION_ERROR; ошибки БД — безопасный 500 INTERNAL_ERROR.
LifecycleError не содержит wire-envelope: B-11 оборачивает его через существующий
api.common.error_response с request_id, без выдачи внутреннего RunRecord клиенту.

## Репозиторий и локальный запуск

LifecycleRepository.insert обязан атомарно сохранить весь новый Run с событиями;
дубликат run_id — 409. transaction обязан owner-scope, блокировку/CAS всех писателей
этого Run и единый commit/rollback состояния, попыток, data, events, last_seq,
version. Проверка expected_version выполняется после получения блокировки.
Ключи/индексы production БД должны защищать (run_id, seq) и
(run_id, stage_id, attempt_no). Все модули, включая B-10 из PR #109, должны
использовать общий repository/seq allocator; независимые счётчики двух модулей
нельзя одновременно подключить к одному Run.

Совместимость с принятыми моделями B-05: Attempt.attempt_id → StageAttempt.id,
reason_code → reason, input_hash — SHA-256, 64 lowercase hex; Event → RunEvent.
stopped_at устанавливается при failed/budget_exceeded/completed/cancelled и
снимается при retry/resume. В production updated_at выставляет триггер V3,
а не локальные часы адаптера. Приватные version/generation/context/data требуют
согласованного долговечного хранения у общего write repository и worker: этих
колонок в принятой V2/V3 нет, нельзя спрятать их в закрытых Metrics/Event DTO.
Для PostgreSQL seq можно выделять как MAX(seq)+1 **после** SELECT Run FOR UPDATE;
все писатели должны брать ту же блокировку, как описано в B-05. Новый write
адаптер обязан сохранять проекции вопросов/метрик и связанные строки в этой же
транзакции. Модели и миграции B-05 этот PR не переписывает.

SQLiteLifecycleRepository(path) — **локальный интеграционный адаптер**, а не
production repository: файловый JSON-агрегат, BEGIN IMMEDIATE, synchronous=FULL,
атомарный UPDATE, без миграций общей БД. Блокировка грубая — на локальную БД,
но порядок seq независим для каждого Run. Пример:

```sh
python -m backend.app.services.lifecycle.example
```

Ожидается: forbidden transition 409 RUN_STATE_CONFLICT; сохранённый cancelled,
version=3; seq=[1,2,3,4,5,6,7,8], в том числе после переоткрытия файла. Пример не
вызывает модель, не генерирует предметные ответы и не подменяет бизнес-логику.

Локальный адаптер включается только явным созданием сервиса. Feature flag и
изменение штатного запуска приложения не вводятся. После появления общего
PostgreSQL Run repository B-05/B-11 реализуют этот порт и убирают локальный адаптер
из используемой конфигурации; ответственные — исполнители этих модулей. B-06 не
заявляет готовность этой интеграции. Откат: убрать вызовы сервиса и revert PR,
не удаляя принятые события/outbox/snapshot из хранилища. Новые тесты, CI/CD,
контейнеризация, сканеры и deploy в scope #66 не входят.
