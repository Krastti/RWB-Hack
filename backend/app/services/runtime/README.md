# B-11: HTTP → PostgreSQL → очередь → lifecycle

Публичные контракты: [C-01 2.1.0](../../../../contracts/platform.openapi.yaml),
[C-03 1.0](../../../../contracts/lifecycle.md), C-02 1.0 и профиль
`web-fastapi-angular` 1.0.0. Публичные DTO не расширяются.

`create_app()` подключает создание, чтение Run/событий и cancel/retry к одной
PostgreSQL. `app.state.run_runtime` содержит результат
`compose_runtime(sessions, retry_limits=Limits(...), queue_config=QueueConfig())`:
repository, states, queue, control, intake. SQLite/in-memory stores здесь не используются.
Необходимы миграции V1–V8, применённые по порядку. V6 хранит private state без второй копии Run/событий; V7 добавляет учёт ошибок recovery в WorkerCommand, V8 — состояние и повторы доставки RunQueueJob.

## Путь команды

1. POST `/api/v1/projects/{project_id}/runs` атомарно сохраняет frozen input,
   queued Run, initial events, исходный ответ/replay и createRun job (V4).
2. `runtime.intake.dispatch(limit=100)` под Run lock регистрирует первую
   `analysis` StageAttempt через lifecycle, сохраняет input/metrics/events и
   WorkerCommand (V5), удаляет job **в той же транзакции**. При сбое всё откатывается.
   Это не второй вызов createRun; повтор dispatch не создаёт ещё одну попытку.
3. Celery dispatch сначала вызывает intake, затем публикует сохранённые команды.
   Сбой брокера оставляет command для redelivery; сеть не вызывается под Run lock.
4. Worker claim использует тот же runtime queue store: queued → running и
   pending → running выполняются существующим lifecycle service и видны через GET
   Run/events. Повтор доставки не создаёт attempts/events/usage.
5. Orchestrator/recovery обязаны сохранять реальные результаты через
   `execution.transaction()`; никакого встроенного mock-аналитика нет.

`AttemptState.input_state.schema_version = runtime-intake/1` обозначает private
снимок требований/профиля/contracts/limits. Это **не AgentInput C-02** и не
выдуманный input artifact. Его input_hash — SHA-256 стабильного JSON frozen context
без изменяемых limits; attempt_no/lease generation также не входят в hash.
Преобразование в настоящий C-02 с проверенными artifact IDs/bytes и согласование
с substantive-v2 графа остаются задачами #128/#130. Не передавайте intake envelope
непосредственно LLM. Смена текущего profile.json не изменяет сохранённый input.

## Пример композиции worker

Из корня, единое имя пакета `backend.app` во всём процессе:

```python
import os
from backend.app.main import create_app
from backend.app.services.runtime import compose_worker

def build_worker(orchestrator, recovery):
    api = create_app()  # DATABASE_URL, JWT_SECRET_KEY; production PostgreSQL
    runtime = api.state.run_runtime
    return compose_worker(runtime, broker_url=os.environ["BROKER_URL"],
                          orchestrator=orchestrator, recovery=recovery)
```

Функции `(StageCommand, ExecutionContext) -> None` предоставляет интеграция AG.
Без них factory worker не вызывается. Запускайте worker для `rwb.stages`, отдельный
control worker для `rwb.control` и beat по инструкции [backend](../../../../docs/backend.md).
HTTP-процесс не запускает beat/worker автоматически. Только запущенные dispatch и
worker переводят durable 202 в исполнение. Настройки queue/lease едины в runtime;
Celery factory отклоняет несовпадающие sessions/config.

Внутри `with execution.transaction() as session` загрузите ORM Run по command.run_id
(lease уже держит Run lock). `runtime.states.transaction(session, run)` даёт
LifecycleTransaction с CAS текущей aggregate version. Для control hooks доступен
`runtime.repository.control_in(session, run)`: finish_attempt/admit_call/reach_boundary
работают в **этой** fenced транзакции. Запрещено вызывать из worker HTTP control
service, открывающий независимую Session, или повторно start_attempt после claim.
Model/HTTP calls выполняются за пределами этих транзакций. Final accepted refs,
usage, checkpoint receipts и domain guards — ответственность интеграции #128–#131.

Базовый execution_guard допускает регистрацию с сохранённым input, начало,
ошибку и отмену. Он отклоняет принятие результата/вопросов/budget/completed без
предметного guard. Для них создайте runtime с `guard` и `checkpoint_confirmed`,
проверяющими реальные данные; успешный analysis сам по себе не завершает Run.

## Control, доступ и восстановление

Cancel/retry используют RunControlService над PostgreSQLRuntimeRepository.
HTTP transaction берёт User → owner-scoped Run lock в том же порядке, что createRun.
Возобновление проверяет один active Run пользователя во всех проектах. При занятом
слоте — 429 без вымышленного Retry-After (момент освобождения неизвестен).
Retry ceiling задаётся доверенным `create_app(retry_limits=Limits(...))`, по
умолчанию равен серверным лимитам создания. Превышение ceiling — 409 без эффектов;
повышение публичного бюджета разрешается только до настроенного сервером потолка.

Run/stages/events читаются из ORM, private inputs/checkpoint/replays/outbox — из V6
под тем же Run lock. Control и lifecycle используют один EventAllocator, общую
append-only SQL историю и общую aggregate version; lifecycle attempt generation
и lease generation транспорта — разные счётчики. Retry replay сохранён в private
state со scope owner/run/retry и возвращает исходный 202 Run после смены статуса
и перезапуска. Retry attempt/events/replay/WorkerCommand фиксируются вместе.
Cancel pending/waiting/queued достигает безопасного барьера сразу; running ждёт
finish_attempt/recovery и не допускает новых calls. Intent отмены сохраняется.

Старые Run без private inputs/receipts читаются; недостающие inputs не выдумываются.
Пустой/повреждённый private state не разрешает retry или принятие результата.
Одна лишь V6 не мигрирует исторические SQLite stores и LangGraph checkpoints.

Откат: остановить dispatch/workers и новые commands, откатить код. Сохранить
схему V4–V8 и принятые inputs/replay/commands для восстановления; не удалять таблицы
с данными и не переисполнять потерянную попытку вместо recovery.

## Technical recovery delivery policy (V7)

`WorkerCommand.recovery_failures` is independent of domain automatic retry counts.
An unacknowledged recovery lease expiration or a fenced `abandon()` increments it
atomically. Retry delays are 5 and 10 seconds; the third failure makes the command
`obsolete`, retains `RECOVERY_HANDLER_FAILED` in `recovery_error`, and invokes the
runtime terminal barrier with `RECOVERY_INFRA_EXHAUSTED`. Pending work is closed
through lifecycle; running independent work may finish before the final transition.
Cancellation retains priority. Exception messages are not persisted because they
may contain credentials or prompts. A database failure rolls back the counter,
lease change and terminal intent together; delivery can resume when SQL returns.

Direct delivery of a lost command must respect `next_delivery_at` as dispatch does.
Dispatch does not move this recovery backoff timestamp when selecting a due lost
command; claim acquires the lease under the Run/command locks. A durable successful
`recovery_generation` is a commit receipt: losing the process before `complete()`
does not increment technical failures or schedule another domain attempt.

## Intake delivery (V8)

V8 must be applied before deploying the new intake. Existing jobs default to pending
and are immediately eligible. Registration, stage command and job deletion still
commit together. Failure rolls back a savepoint under Run/job locks; delivery
metadata then commits under those same locks. Known immutable-input validation
errors are rejected immediately; other errors get at most three failures with
5/10-second backoff. Rejected jobs retain a safe error code for investigation and
never occupy an active dispatch slot. Valid frozen state allows queued → running
→ failed through lifecycle. When corrupt snapshots prevent lifecycle loading,
`TERMINALIZATION_UNAVAILABLE` is persisted with the rejected job for operator
repair; no terminal Run state is fabricated. Database outages can prevent recording
failure metadata; the whole transaction then rolls back and delivery remains durable.
