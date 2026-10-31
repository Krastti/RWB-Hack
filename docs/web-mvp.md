# Web MVP: запуск и проверка

Один бизнес-вход проходит analysis → source-bound policies → architecture →
frontend-template → backend-template → models → migrations → backend → frontend →
documentation → assembly. При принятых внешних интеграциях перед backend добавляется
integrations. Scheduler регистрирует только готовую задачу и сохраняет её точный
inventory, зависимости, модель без ключа, checksum реализации/шаблона и accepted refs.
Смена реализации при replay явно отклоняется вместо смешивания версий.

## Конфигурация

Используйте Python 3.12, PostgreSQL и Redis, примените Flyway V1–V14.
API: `uvicorn app.main:create_app --factory`. Worker:
`celery -A app.workers.pipeline:app worker -B`.
`DATABASE_URL`, `CELERY_BROKER_URL`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`
задаются оператором. `PYTHONPATH=backend:.` для запуска из корня.
API также требует auth-конфигурацию и пользователя по [backend](backend.md).

`PIPELINE_POLICY_FILE` необязателен для web MVP: модель предлагает пути и имена,
а сервер привязывает grants к доказательствам принятых FR/permissions. Поддерживается
ограниченное распознавание явных RU/EN CRUD-разрешений. Неясность вызывает вопрос,
а после исчерпания уточнений — диагностический отказ. Это не универсальный ACL-решатель.
Проверенный оператором policy-файл остаётся явным override. Для других профилей
автоматическая подготовка прав и web generators не применяются.

## Готовность и восстановление

Шаблоны `templates/web-mvp/1` и ORM/Alembic строятся детерминированно, без LLM.
Предметные Python/Angular файлы проходят A-22 с полным inventory и отдельным учётом
каждого физического вызова. Python проверяется через AST, маршруты/operationId и
наличие server auth dependency сверяются с OpenAPI. Это структурные проверки,
не доказательство реализации всех бизнес-правил. SQLAlchemy-модели и immutable
initial revision разделяют один принятый data-model; Alembic принадлежит продукту,
Flyway — платформе. Путь миграций продукта — `backend/migrations/`; `backend/alembic.ini`
принадлежит backend owner.

Каждый завершённый этап имеет immutable result и подтверждённый checkpoint.
Повторная доставка завершённой команды не вызывает модель. Сохранённые A-22 batches
восстанавливаются в пределах той же попытки. Capture/recovery результата использует
существующий fenced protocol. Новая предметная попытка не переписывает старую историю.

V14 хранит интервалы активного исполнения под generation lease. Очередь и ожидание
ответов не входят в активный бюджет; пересекающиеся интервалы считаются один раз.
Незакрытый интервал после сбоя ограничен последним подтверждённым lease deadline.
Это консервативная оценка: между фактическим crash и lease expiry время учитывается.
Таймер переживает перезапуск/retry; timeout физического вызова ограничен остатком.
Публичный `metrics.duration_ms` остаётся elapsed time по C-03 и включает ожидания.

Assembly принимает только точные upstream bytes с подтверждёнными checkpoints,
обязательными файлами C-05 и traceability всех FR. ZIP привязан к snapshot_id.
`completed` разрешён после prepared snapshot и подтверждения всех задач плана.
Traceability консервативно использует `partial`: наличие исходника не доказывает
поведенческое покрытие. Worker не исполняет сгенерированный код, не деплоит продукт
и не утверждает успешность его build/test/security.

## Проверки

Установите `backend/requirements-worker.txt` и `backend/requirements-test.txt`.
`RWB_TEST_DATABASE_URL` должен указывать на отдельную тестовую PostgreSQL; тесты
создают и удаляют собственные случайные схемы.

```sh
PYTHONPATH=backend:. python -m pytest backend/tests/test_pipeline_mvp.py backend/tests/test_pipeline_active_time.py backend/tests/test_pipeline_policies.py backend/tests/test_pipeline_policy_postgresql.py backend/tests/test_mvp_generated_product.py backend/tests/test_pipeline_execution.py backend/tests/test_pipeline_review_regressions.py backend/tests/test_artifact_persistence.py -q
PYTHONPATH=backend:. python -m unittest backend.app.agents.generation.tests.test_executor -q
python contracts/agents/validate.py --check
cd frontend
npm ci
npm test
npm run build
```

`test_pipeline_mvp` использует настоящие HTTP handlers, worker/queue, PostgreSQL,
LLM adapter, generators и ZIP; подменяется только HTTP transport модели.
В production нет fixture fallback. Отдельный controlled product test применяет
Alembic up/down/up и проверяет persistence, 401/403, владельца, диспетчера и запрет DELETE.
Он исполняет только явно заданные тестовые исходники, не произвольный ответ модели.
Для экспорта ZIP теста задайте `RWB_MVP_EXPORT_DIR`, затем в его `frontend/` выполните
`npm ci && npm run build`. Эти проверки включены в CI; живой provider smoke независим.

## Отложено

GitLab, дополнительные профили, runtime-интеграция behavior/codegen, simulation,
impact/diff/coverage UI и performance. Behavior-модули из PR #147/#148/#150/#151
сохранены; MVP не выдаёт их наличие за production-интеграцию. Внешние adapters
исполняются только в целевом продукте после отдельной настройки и проверки.
Живой LLM smoke остаётся отдельной проверкой: предоставленный endpoint вернул 401;
секреты не сохранялись в репозитории. Это не ошибка controlled transport тестов.
