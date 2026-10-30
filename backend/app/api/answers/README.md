# B-12: ответы на уточнения

C-01 2.1.0, C-03 1.0. Штатный `create_app()` подключает
`POST /api/v1/runs/{run_id}/answers`, Bearer и обязательный Idempotency-Key.
Ответ — 202 с Run. Требуются PostgreSQL V1–V9 и runtime из #140.

```json
{
  "questions_revision": 2,
  "answers": [
    {"question_id": "20b5ce33-5873-4796-a47d-4ce79578e101", "value": "Только владелец проекта."}
  ]
}
```

UUID берётся из GET Run. Пакет включает все ещё не отвеченные обязательные
вопросы; необязательные ответы допускаются. Строки сохраняются без trim, но
пустые/пробельные, длиннее 10000, повторные/неизвестные UUID и неполный пакет
дают 422 без записей. Подтверждённое значение нельзя заменить (422).
Новая операция требует waiting_for_input; неверный статус — 409 RUN_STATE_CONFLICT,
другая ревизия — 409 STALE_QUESTIONS_REVISION, чужой Run — 404.

Под owner-scoped User→Run lock одна транзакция сохраняет Clarification с ревизией,
UUID, вопросом, причиной, FR, required и ответом; immutable clarification input;
N+1 pending attempt с новым input_hash; metrics/manual_clarifications; events;
running Run; WorkerCommand и исходный HTTP response. Предыдущая waiting attempt
сохраняется. Число уточнений растёт только для впервые отвеченных UUID.
Нет сетевых/LLM вызовов под блокировкой. Ошибка откатывает весь пакет.

Scope replay — owner/Run/answerQuestions/key. Fingerprint использует валидированное
тело, сортирует ответы по UUID и сохраняет точные значения. Тот же ключ/пакет
возвращает исходный 202 даже после смены статуса/рестарта, другой пакет даёт
409 IDEMPOTENCY_KEY_CONFLICT. Одновременная операция того же scope даёт
409 IDEMPOTENCY_IN_PROGRESS. Ошибка валидации не резервирует ключ.

Producer вопросов вызывает `persist_questions(session, run)` из service.py после
записи **каждой** новой ревизии run.questions и до commit, держа Run lock.
Так сохраняется история прошлых наборов, даже если пользователь не отвечал.
Прежнюю ревизию нельзя переписать. Для уже существующей текущей ревизии endpoint
сохраняет её перед приёмом пакета; утраченные исторические наборы не выдумываются.
Вызов hook — часть будущей AG/worker интеграции, не её автоматическое подключение.

ClarificationInput — реально сохранённый immutable JSON с внутренней версией
clarification-input/1 и UUID, на который ссылается answers.accepted.input_artifact_id.
Это private input receipt, не готовый C-02 Artifact/AgentInput. Он содержит прежний
frozen input и полный набор подтверждённых ответов. B-13/#128/#130 должны построить
настоящий C-02 вход с проверенными artifact IDs/bytes и доставить его в граф,
используя уже зарегистрированный attempt UUID. Endpoint не запускает граф, не
подделывает checkpoint и не требует готового checkpoint для приёма ответов.
Бюджет следующего модельного вызова проверяет worker; принятые ответы не теряются.

Откат: остановить приём answers и исполнителей, откатить код; сохранить V9
Clarification/Input/Replay и V5 commands для восстановления. Не удалять принятые
ответы и не применять миграцию вниз поверх пользовательских данных.

Clarifications now use V9 after the parent runtime's published V7 (recovery) and
V8 (intake). Upgrade from runtime V8 is supported without changing its checksums.
An environment that previously applied the standalone, unmerged clarifications
V7 must not run this history automatically: its V7 identity conflicts with the
published runtime V7. Preserve its database and Flyway history for a separately
reviewed data-preserving migration; do not repair checksums or delete data.
