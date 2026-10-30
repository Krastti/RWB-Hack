# B-10: отмена и ручной повтор

Публичный интерфейс соответствует [C-01 OpenAPI v2.1.0](../../../../contracts/platform.openapi.yaml),
[C-03 v1.0](../../../../contracts/lifecycle.md), AgentResult —
[C-02 schema_version=1.0](../../../../contracts/agents/README.md).
База контрактов: `ef1956c958ef64c8784ce0558c53269bea25142f` (принятая #53).
Общие контракты не изменены. `contract_version` копируется из исходной попытки;
это версия предметного контракта (например, `"1"`), а не версия C-03.

## Подключение HTTP

```python
from backend.app.services.run_control.api import configure_run_control
from backend.app.services.run_control import RunControlService

# app уже настроен через configure_auth с настоящей Bearer-аутентификацией.
# shared_store реализует ControlStore на общем хранилище запусков.
# retry_policy проверяет разрешённые лимиты и отсутствие другого активного Run.
configure_run_control(app, RunControlService(shared_store, authorize_retry=retry_policy))
```

`POST /api/v1/runs/{run_id}/cancel` без тела/ключа возвращает Run, HTTP 200.
`POST /api/v1/runs/{run_id}/retry` принимает `{}` либо
`{"limits":{"max_calls":12,"max_output_tokens":60000}}`, обязательный
`Idempotency-Key`, возвращает сохранённый Run, HTTP 202. `limits:null`, неизвестные
поля, неверные UUID/лимиты/ключ — 422. Ошибки имеют общий C-01 Error envelope.
Чужой и отсутствующий Run одинаково дают 404; используется существующий current_user.

Роутер подключается явно: штатный `create_app` не получает отдельное хранилище
запусков автоматически. Модели PostgreSQL B-05, lifecycle B-06 и граф A-04 существуют, но общий
транзакционный write adapter для их совместной работы ещё не подключён. Это готовый модуль управления с проверенным локальным
адаптером, а не заявление готовности полного пайплайна.

## Публичные операции

- `cancel(owner_id, run_id)` сохраняет `cancelling` и outbox-намерение атомарно.
  Новые исполнения и вызовы запрещены. Pending-попытки закрываются без вызова
  модели; если активных running-попыток нет, сразу достигнут безопасный барьер
  и сохраняется `cancelled`. Завершённые результаты waiting_for_input не меняются.
  Для cancelling/completed/cancelled/failed/budget_exceeded это no-op: без событий,
  попыток, новых seq и расхода. Повтор возвращает текущее состояние.
- `retry(owner_id, run_id, key, body)` допускается только из failed/budget_exceeded,
  без активных попыток. Выбирается последний failed-вызов current_stage; если эта
  подсказка отсутствует/не указывает на failed, выбирается первый failed stage_id
  в лексикографическом порядке. При нескольких ошибках дальнейшее планирование
  оставшихся узлов выполняет orchestrator. Создаётся pending N+1 с новым внутренним
  UUID и прежними input_hash, входами, contract_version. Старые результаты, успешные
  независимые узлы, общий расход и сохранённые счётчики автоматических повторов
  не сбрасываются. Переход в queued, события, replay и outbox — одна транзакция.
- Replay проверяет владельца до чтения сохранённого ответа, затем ключ/нормализованный
  hash тела до проверки статуса. Тот же ключ/тело возвращает исходный ответ даже
  после отмены или завершения; другое тело — 409 IDEMPOTENCY_KEY_CONFLICT.
  Область ключа — владелец + Run + операция retry. Другие конфликты —
  409 RUN_STATE_CONFLICT, без частичных эффектов.
- Из budget_exceeded нужен полный Limits: ни один предел не уменьшается,
  исчерпанный увеличивается достаточно для следующего вызова. Бюджетный модуль
  перед остановкой записывает `Record.required_limits` — необходимые **общие**
  лимиты с учётом расхода и консервативных резервов. При отсутствии этого знания
  повтор отклоняется. Внедряемый обязательный `authorize_retry(record, limits)`
  проверяет политику владельца, разрешённые пределы и один активный Run внутри
  той же транзакции. При отказе он передаёт рассчитанное политикой число секунд:
  `ControlError(429, "RATE_LIMIT_EXCEEDED", message, retry_after=seconds)`.
  HTTP-обработчик возвращает это значение в `Retry-After` без подмены константой.
  `seconds` — положительное целое число; при расчёте из времени следующего допуска
  интервал округляется вверх и ограничивается снизу одной секундой. Срок для
  ограничения активных запусков выбирает политика по условиям повторной проверки,
  а не HTTP-слой. Ошибка 429 без retry_after или с некорректным значением отклоняется
  при создании ControlError; для остальных статусов этот аргумент необязателен.

## Worker и checkpoint

`start_attempt(owner_id, run_id, stage_id, attempt_id)` атомарно проверяет статус
и актуальный UUID попытки, фиксирует pending → running и возвращает состояние
восстановления. Повторная доставка не создаёт попытку и не получает второе право
исполнения. Worker держит свой lease/fencing; его восстановление и таймауты относятся
к A-04/B-05. UUID отсекает ответы старых попыток, но не заменяет lease поколений.

Перед **каждым** внешним вызовом worker вызывает
`admit_call(..., reserve_budget=...)`: проверка отмены и запись реального вызова/
резерва происходят в общей транзакции. После admission вызов считается активным;
отмена ждёт его результата/дедлайна. Мгновенная остановка provider не обещается.
После каждого успешного reserve_budget публикуется metrics.updated с полным
снимком metrics/tokens_complete, даже если callback не изменил числовые значения.
Callback отвечает за достоверный учёт вызова/резерва и полноту usage; сервис
не выдумывает токены и не дублирует его счётчики. Если callback выбросил исключение,
допуск и событие не фиксируются.

После schema/reference/checksum/path-проверок настоящего AgentResult:

```python
def save_boundary(record):
    # Исполняется под блокировкой общего Run, а не на устаревшем snapshot worker.
    # В реальной интеграции сохранить ссылки на принятые байты артефактов,
    # дедуплицированный usage по call_id, резервы, tokens_complete и состояние графа.
    return graph_persistence.merge_accepted_result(record, validated_result)

service.finish_attempt(
    owner_id=owner_id, run_id=run_id, stage_id=stage_id, attempt_id=attempt_id,
    accepted_result=validated_result, save_boundary=save_boundary,
)
```

`finish_attempt` дополнительно сверяет run/stage/attempt/contract_version, хранит
исходный результат, finished_at, полный snapshot и checkpoint в одной транзакции.
Callback обязан учитывать фактический/неизвестный расход и сохранять результаты
других параллельных узлов. Его исключение откатывает весь барьер. При cancelling
успех/ошибка не переводят Run в completed/failed: после последнего активного
этапа Run становится cancelled. До этого поздние валидные результаты сохраняются.
Повторный/устаревший результат — 409. `reach_boundary` доступен dispatcher без
активного вызова. Обычные ошибки, вопросы и общий барьер completed ведёт orchestrator.

Checkpoint связан с Run, ревизией, contract_version, input_hash и state_version.
Worker использует его только при полном совпадении; отсутствующий/устаревший/
несовместимый checkpoint заменяется восстановлением из authoritative saved_state,
истории и неизменяемых входов. Принятые результаты/ответы не откатываются.
POST /retry не требует готового checkpoint. Недостаточные исходные данные —
500 RESUME_STATE_UNAVAILABLE без принятия команды. Формат graph state непрозрачен
для B-10, его полноту обеспечивает A-04. Нельзя подставлять пустой граф/выдуманные
артефакты вместо потерянного состояния.

## Хранение и локальный пример

`ControlStore.transaction` — публичный порт: owner scope, сериализация всех
изменений одного Run, commit/rollback состояния, событий, replay, outbox и checkpoint.
Производственный адаптер должен разделять транзакции/блокировки с созданием Run,
answers, budget и worker; глобальное ограничение активного Run — в общей БД.
Dispatcher доставляет outbox по ID с повторной доставкой, проверяет актуальные
статусы/attempt_id и подтверждает доставку транзакционно. Отменённый retry-job
не выполняется. Ни HTTP-запрос, ни повтор доставки не должны создавать новые попытки.

`SQLiteControlStore(path)` — самостоятельный **локальный интеграционный адаптер**
с файловым хранением, BEGIN IMMEDIATE, rollback и synchronous=FULL. Перезапуск
сервиса сохраняет принятую команду/replay/outbox/checkpoint. Это не второй источник
истины для production: до подключения общего PostgreSQL repository он не включён
в приложение по умолчанию. `create(Record)` служит загрузке сохранённого состояния
для локальной интеграции, не заменяет B-05 POST /runs.

```sh
python -m backend.app.services.run_control.example
```

Пример использует явно помеченные control-fixtures, не вызывает LLM и не подменяет
бизнес-логику продукта: retry → queued/2, replay после переоткрытия файла → queued/2,
cancel до worker → cancelled/cancelled. Дальнейшая интеграция адаптера — B-05/B-06/B-11/A-04 (#66, #78),
ответственный — исполнитель соответствующего модуля. Удалить локальный адаптер
из используемой конфигурации после подключения общего транзакционного хранилища.
Feature flag не вводится; включение — явный configure_run_control.
Откат B-10: отключить этот configure и revert PR; принятые данные/outbox не удалять.


## Stabilization policy

Run defaults to Angular; historical React records deserialize unchanged but every
control/worker admission rejects them with 409 UNSUPPORTED_RUN_PROFILE. UI checks
are not the authorization boundary.

Budget causes are normalized in Run.stop_reason: MAX_CALLS, MAX_OUTPUT_TOKENS,
MAX_INPUT_TOKENS, RUN_TIMEOUT. Unknown reasons fail closed. Only calls/output
causes permit public retry, with increased cause-specific limit, positive remaining
capacity versus cumulative usage, required_limits sufficient for reservations,
and mandatory authorize_retry policy. Input/time/unknown causes return 409
RETRY_NOT_SUPPORTED before any attempts/events/outbox/budget changes. Cumulative
usage, automatic retry counts and frozen inputs/profile are never reset.

Event allocation and transition tables are shared with lifecycle. This does NOT
make two independent repositories a single Run owner. Production integration
must use one authoritative transaction/lock/sequence and one state writer (#128).
Do not connect the standalone SQLite control and lifecycle stores to the same Run.


Manual retry and internal recovery are distinct. Public POST /retry retries a failed
agent attempt. When the latest attempts are all succeeded/skipped and the Run is
failed, it returns 409 RECOVERY_REQUIRED without effects; UI hides retry and
explains that checkpoint/assembly recovery is needed. The existing guarded
Lifecycle recovery transition reuses accepted results without a new attempt or
LLM usage. Its real dispatcher/persistence integration remains #128; the UI does
not promise automatic recovery. Unsupported budget causes are checked before
attempt lookup, including stops before the next stage starts.

The event allocator raises neutral EventSequenceError. Lifecycle/control map it
to their own RUN_STATE_CONFLICT error envelopes, rolling back the transaction.
