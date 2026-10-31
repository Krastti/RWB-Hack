# A-22 / #105 — пакетный исполнитель исходников

Зависимости: A-29 #143, A-30 #145, A-01, C-02/C-05 1.0. Этот модуль выполняет
одну принятую задачу generation-plan. Он не заменяет A-11/A-13, A-20, B-14
и не регистрирует новые StageAttempt на каждый пакет.

`BatchExecutor(config, store, ledger, max_files_per_batch, max_request_bytes,
validate_file).run(request, owner_id, attempt_id, task_id, plan, profile, context,
files, model_configuration) -> GenerationOutcome`. Все аргументы run — keyword-only.

`FileSpec` задаёт path/kind/media_type/version и output_token_budget одного
полного файла. Предметный генератор заранее материализует конечный список файлов
в разрешённых каталогах принятого задания. Explicit output_paths обязательны;
директория сама по себе не принимается как готовый файл. Task/stage/owner,
contract_version, profile и context проверяются перед вызовом модели.
Обязательные неотвеченные вопросы блокируют dispatch.

Файлы делятся в заданном стабильном порядке по числу и сумме token allocations,
с запасом 256 токенов на JSON envelope. Выделенный максимум каждого вызова
ограничивается AgentInput, C-05 и LLMConfig; фактический ответ может не поместиться.
A-01 возвращает OUTPUT_TRUNCATED вместо приёмки обрезанных bytes. Размер запроса
с profile/prompt/schema и ранее принятыми файлами проверяется без обрезки;
слишком большой запрос останавливает пакет с диагностикой. Byte limit не заменяет
точный подсчёт/резерв input tokens в общем ledger.

Для каждого пакета используется настоящий A-01 с ограниченными timeout/output.
`model_configuration` — обязательные исходные bytes конфигурации модели без api_key,
переданные в `ContextBuilder.prepare(execution_inputs=...)`. Их checksum сверяется
с manifest A-30, а содержимое — с конфигурацией исполнителя. Например:

```python
outcome = await executor.run(
    request=request, owner_id=owner_id, attempt_id=attempt_id, task_id=task_id,
    plan=plan, profile=profile, context=context, files=files,
    model_configuration=execution_inputs["model_configuration"],
)
```

Worker передаёт prompt/template/compiler configuration, которые фактически
использует генератор. complete=true — только транспортный признак, не доказательство
предметной корректности: обязательный validate_file должен проверить синтаксис,
контракты и соответствие задаче, либо выбросить исключение. Он не вызывает LLM.

## Порты хранения и бюджета

`BatchStore.open_attempt` авторизует owner/Run/attempt и фиксирует fingerprint
входов, плана, списка файлов и модели. Другой fingerprint в той же попытке —
конфликт, а не разрешение перезаписи. `receipt` возвращает проверенные bytes.
`accept` атомарно сохраняет полные файлы и receipt, проверяя grants, версии,
UUID/path/content и текущий lease/fence. Идентичный replay не создаёт копии.
Batch/artifact UUID детерминированы внутри attempt; разные файлы не делят ID.
Этот модуль не реализует production store и не использует скрытый memory fallback.

`BudgetLedger.reserve` атомарно проверяет все области Run/agent, input/output,
таймауты и сохраняет резерв до dispatch. A-01 сам добавляет JSON-schema instruction;
ledger должен учитывать фактический provider envelope. `finish` сохраняет usage
сразу после ответа, **до** проверки/сохранения файлов, включая ошибку/обрезку.
`abandon` сохраняет in-doubt dispatch при отмене, не списывает неизвестный расход
как нулевой. `usage` возвращает C-02 snapshot всех физических вызовов попытки,
включая предыдущие доставки/ошибки. Nullable tokens сохраняются. Admission и
reconciliation принадлежат A-20; пример ledger не является production accounting.

Ранние полные пакеты остаются в store при ошибке позднего. Outcome содержит
AgentResult failed с принятыми artifacts/usage и отдельный remaining_paths;
success допустим только при наличии всех файлов задания. При успешном replay
receipt повторно проверяется, модель/usage не вызываются и не суммируются снова.
Файлы ранних пакетов передаются следующему целиком: интерфейсы не пересказываются
и не подменяются устаревшими заглушками. При нехватке контекста нужен меньший
accepted task, а не скрытое удаление файлов/требований.

Автоповторов нет. Внешний orchestrator владеет retry и lease. Если процесс погиб
после внешнего вызова до receipt, нужна reconciliation ledger/storage; exactly-once
LLM не обещается. Ошибка ledger.usage/open_attempt передаётся вызывающему коду;
ошибка приёмки не становится успешной генерацией. Cancellation пробрасывается,
ранние receipts остаются доступны после восстановления worker.

## Пример

После подключения #143 и #145, из корня Python 3.11+:

```sh
python -m pip install -r backend/app/agents/generation/requirements.txt
python -m backend.app.agents.generation.example
python -m backend.app.agents.generation.example --fail-second
```

Используется реальный A-01 с явным HTTPX MockTransport, ручными Python-файлами
и локальными ExampleStore/ExampleLedger. Успешный пример проверяет replay без
новых вызовов; второй — сохранение первого файла и расхода при обрезке второго.
Числа usage в HTTP fixtures синтетические, не метрики настоящей модели.

A-11/A-13 подключают свои FileSpec/validate_file и шаблоны через этот интерфейс;
самих генераторов в принятой базе пока нет, окончательная интеграция не заявляется.
Готовые bytes не означают успешную сборку/запуск приложения. Откат — исключить
исполнитель из dispatcher, сохранив receipts/артефакты и ledger принятых попыток.
