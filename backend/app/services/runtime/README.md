# B-11: HTTP → PostgreSQL → очередь → lifecycle

Публичные контракты: [C-01 2.1.0](../../../../contracts/platform.openapi.yaml),
[C-03 1.0](../../../../contracts/lifecycle.md), C-02 1.0 и профиль
`web-fastapi-angular` 1.0.0. Публичные DTO не расширяются.

`create_app()` подключает создание, чтение Run/событий и cancel/retry к одной
PostgreSQL. `app.state.run_runtime` содержит результат
`compose_runtime(sessions, retry_limits=Limits(...), queue_config=QueueConfig())`:
repository, states, queue, control, intake. SQLite/in-memory stores здесь не используются.
Необходимы миграции V1–V6; V6 хранит только private state, без второй копии Run/событий.

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
V4/V5/V6 и принятые inputs/replay/commands для восстановления; не удалять таблицы
с данными и не переисполнять потерянную попытку вместо recovery.
