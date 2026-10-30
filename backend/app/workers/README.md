# Worker B-08

Реализация `StageWorker` вызывает внедряемые callable оркестратора и восстановления
под PostgreSQL lease. `LeasedExecution` поддерживает heartbeat и fenced-транзакции.

Публичные интерфейсы, пример подключения Celery, постановка задания, требования
к сохранению результатов, миграции и ограничения описаны в
[README адаптера очереди](../adapters/queue/README.md).

Готовность worker-адаптера не означает подключения полного HTTP → graph pipeline.
