# Requirements Analyst Agent

Самостоятельное ядро аналитического узла для [issue #50](https://github.com/Krastti/RWB-Hack/issues/50)
и появившейся [A-03 / #77](https://github.com/Krastti/RWB-Hack/issues/77).
Принимает бизнес-требования, обращается к LLM, проверяет структурированный ответ
и возвращает разбор для System Analyst Agent. Предметная область не зашита в код:
пример с ремонтом оборудования намеренно отличается от парковочного кейса.

Основания: приложенные PDF хакатона (этап 1) и схема оркестрации
`Requirements Agent → Ambiguity Gate → System Analysis Agent`;
[спецификация команды](https://github.com/Krastti/RWB-Hack/blob/9fa0d4c133d9bbfa45e4acc200837d01694cf8a4/docs/project-specification.md),
§2, §10, §12, и
[роадмап](https://github.com/Krastti/RWB-Hack/blob/9fa0d4c133d9bbfa45e4acc200837d01694cf8a4/docs/development-roadmap.md),
A-03. SHA локальной базы: `514edb236cabd76bcdc61e54d670995f892ecf0c`.
Прочитанный upstream: `9fa0d4c133d9bbfa45e4acc200837d01694cf8a4`.

## Статус контракта и границы

`requirements-analyst/0.1` — **локальный проект контракта**, не принятая версия
C-02. В прочитанном upstream общие схемы и runtime отсутствуют. Согласование
с [#52](https://github.com/Krastti/RWB-Hack/issues/52) необходимо до подключения
потребителей. Схемы находятся [в schemas/](schemas/analysis.schema.json),
проверяются на совпадение с моделями. Общий `contracts/agents/` не изменён.

Модуль владеет только этой папкой. Публичный интерфейс —
`RequirementsAnalyst(client).run(AgentInput) -> AgentResult`; клиент реализует
[`LLMClient.complete`](llm.py). Вызов синхронный: использовать внутри worker,
не в цикле событий HTTP-обработчика.

Локальный транспорт `OpenAICompatibleClient` позволяет выполнить реальный запуск
без общего адаптера. Это рабочий HTTP-клиент, не mock и не реализация общей
задачи [#75](https://github.com/Krastti/RWB-Hack/issues/75). После принятия A-01
передать его адаптер через `LLMClient` (с преобразованием в `Completion`/`LLMError`).
Один вызов узла делает максимум одно обращение; автоматических повторов нет.
Повторы и их устойчивый счётчик принадлежат orchestrator.

Prompt здесь покрывает этап извлечения требований. Полный `system-analysis.md`
с компонентами, детальной моделью данных, API и событиями ещё не формируется.
В актуальной #77 этот файл входит в результат узла A-03; полный prompt/шаблон
поставляет [A-02 / #76](https://github.com/Krastti/RWB-Hack/issues/76).
Нужно согласовать их подключение, а не считать отдельного System Analyst Agent
принятым решением команды. Сейчас #77 покрыта частично. Этот модуль не
заявляет закрытие #52, #75, #76, #77 или готовность полного пайплайна. Обсуждение
границ и тексты комментариев: [coordination.md](coordination.md).

## Запуск из чистого окружения

Python 3.11+; зависимости только этой задачи устанавливаются отдельно.
Следующие команды выполняются из корня репозитория:

```sh
python3 -m venv backend/app/agents/requirements_analyst/.venv
backend/app/agents/requirements_analyst/.venv/bin/python -m pip install -r backend/app/agents/requirements_analyst/requirements.txt
export PYTHONPATH="$PWD/backend/app/agents"
export ANALYST_PYTHON="$PWD/backend/app/agents/requirements_analyst/.venv/bin/python"
```

Проверка сохранённого примера без сети и модели:

```sh
"$ANALYST_PYTHON" -m requirements_analyst validate \
  --input backend/app/agents/requirements_analyst/examples/input.json \
  --analysis backend/app/agents/requirements_analyst/examples/analysis.json
```

Ожидается `{"valid":true,"decision":"wait","blocked_question_ids":["Q-001"]}`.
`examples/analysis.json` — написанный вручную образец, а не результат реальной
генерации. Он не подставляется при недоступности модели.

Реальная генерация через API, совместимый с Chat Completions:

```sh
export LLM_BASE_URL='https://your-model-server.example/v1'
export LLM_MODEL='provided-model-name'
# LLM_API_KEY задайте через окружение или менеджер секретов, не в файле проекта.
"$ANALYST_PYTHON" -m requirements_analyst analyze \
  --input backend/app/agents/requirements_analyst/examples/input.json \
  > backend/app/agents/requirements_analyst/result.json
```

Адрес и имя модели выше — заменяемые примеры. Реальный протокол конкурсной модели
не был предоставлен. `LLM_BASE_URL` выбирает оператор сервера, не конечный
пользователь. Клиент добавляет `/chat/completions`; не включать в URL ключи,
параметры запроса или учётные данные. Перенаправления не выполняются.
`LLM_API_KEY` может отсутствовать у локального сервера без аутентификации.

По умолчанию отправляется `response_format=json_schema`. Если фактический сервер
поддерживает только JSON object mode, явно добавить `--response-format json_object`.
Схема всё равно передаётся модели и проверяется локально. При несовместимом API
нужен другой `LLMClient`; скрытых переключений и повторных расходов нет.
`timeout_seconds` передаётся как сетевой timeout транспорта; общий deadline этапа
и отмену контролирует worker.

Коды выхода CLI: `0` — корректный результат, включая ожидание уточнения;
`1` — неудачный запуск агента; `2` — ошибка входа/конфигурации. `validate` проверяет
контракт и источники, не запускает LLM. `analyze` пишет один JSON результата
в stdout; в этой версии артефакты содержатся внутри результата. Для их сохранения
использовать storage adapter следующей задачи. Модель не определяет пути записи.

## Формат и поведение

- [AgentInput](schemas/input.schema.json): контекст запуска, текст до 100 000
  символов, `previous_analysis`, ответы `{question_id,value}`, лимиты.
  Новому запуску достаточно `run_id` и `requirements_text`.
- [Analysis](schemas/analysis.schema.json): цели, границы, роли/права, FR/NFR,
  ограничения, сущности, сценарии, интеграции, безопасность, критерии приёмки,
  риски, допущения и вопросы. Все разделы обязательны; пустые списки допустимы.
- [AgentResult](schemas/result.schema.json): `status`, `output_artifacts`,
  `questions`, `assumptions`, `usage`, `diagnostics`, контекст запуска.

Проверки после LLM:

1. Строгие типы, обязательные поля, отсутствие лишних полей и повторов ID.
   JSON с повтором ключей, NaN, пояснениями или Markdown-обёрткой отклоняется.
2. Каждый факт имеет непустую дословную цитату `evidence` из текущих требований
   или реального ответа. Допущения и производные риски хранятся отдельно.
3. Ссылки на FR и роли существуют, каждый FR имеет given/when/then критерий.
4. При уточнении сохранены ID и evidence FR/NFR/ролей; вопросы не исчезают,
   их текст и critical не меняются. Уточнять текст FR допустимо, подменять источник
   существующего ID нельзя; новые факты добавляются с новыми ID.
5. Модель не выдумала ответы. Подтверждённые ответы сливаются кодом по ID.
6. Не больше трёх нерешённых critical-вопросов; можно задать меньший лимит.

Нерешённый critical-вопрос возвращает `waiting_for_input`, некритичный не блокирует.
`succeeded` означает прошедший проверки разбор требований. Это не подтверждение
качества будущего кода, сборки или deploy. Неверный ответ модели даёт `failed`
с диагностикой и без выходных артефактов. Анализ без FR требует критического
уточнения и не может быть принят как успешный.

Успешный разбор и ожидание возвращают три артефакта: `requirements.json`,
`questions.json`, `requirements-analysis.md`. Каждый имеет schema version,
FR IDs и SHA-256 точных UTF-8 байтов `content` (без префикса). Пути фиксирует код.
Markdown отражает все поля в JSON-блоках; backticks внутри данных экранируются.
Это передача следующему аналитику, не полный документ системного анализа.

Для возобновления передать извлечённый `requirements.json` как `previous_analysis`,
тот же текст требований и `run_id`, увеличить `attempt`, добавить ответы:

```json
{"answers": [{"question_id": "Q-001", "value": "Удаление запрещено всем ролям."}]}
```

Это фрагмент обновления входа, не полный AgentInput. Ответы без предыдущих
вопросов, повторяющиеся ID и пустые строки отклоняются. Новый вход по другой
задаче требует нового снимка, без `previous_analysis`. Владельца, текущую ревизию
и состояние `waiting_for_input` проверяет API/orchestrator до вызова узла.

## Расход и ошибки

Фиксируются `calls`, входные/выходные токены, длительность и `attempt`.
Если provider не сообщил usage, токены равны `null`, а не фиктивному нулю.
После структурно неверного ответа известные токены сохраняются. Нулевой
`max_calls` останавливает узел до обращения; лимит выходных токенов отправляется
provider. При известном превышении результат отклоняется. Сводный бюджет всех
этапов и попыток проверяет orchestrator.

Нормализованные причины: `CALL_BUDGET_EXCEEDED`, `OUTPUT_TOKEN_BUDGET_EXCEEDED`,
`INVALID_PREVIOUS_ANALYSIS`, `INVALID_ANALYSIS`, `LLM_TIMEOUT`, `LLM_UNAVAILABLE`,
`LLM_AUTHENTICATION`, `LLM_HTTP_ERROR`, `LLM_INVALID_ENVELOPE`,
`LLM_INCOMPLETE_RESPONSE`, `LLM_RESPONSE_TOO_LARGE`, `LLM_FAILURE`.
Retryable — рекомендация вызывающему коду, не запуск retry.
Raw body provider, exception message, ключ и конфигурационный URL не публикуются.

Системная политика отделена от JSON бизнес-данных. Модель не имеет инструментов,
доступа к файловой системе или ключа транспорта в prompt. Присутствие реальной
цитаты не доказывает семантическую корректность интерпретации: её оценивает человек
или следующий аналитик. Входные тексты могут содержать конфиденциальные данные;
правила их допуска к модели и storage задаются платформой.

## Проверки

```sh
"$ANALYST_PYTHON" -m unittest discover -s backend/app/agents/requirements_analyst/tests -v
git diff --check
```

Проверки охватывают gate, ответы/продолжение, стабильные ID, источники, связи,
неверный JSON, бюджеты, HTTP-контракт, timeout, usage, очистку ошибок и CLI.
LLM-ответы подменяются **только в тестах**. HTTP-запросы проверяются без внешней
сети. Это проверка узла, не тестирование сгенерированного приложения.

Чтобы обновить локальные JSON Schema после изменения моделей:

```sh
"$ANALYST_PYTHON" -m requirements_analyst schema input > backend/app/agents/requirements_analyst/schemas/input.schema.json
"$ANALYST_PYTHON" -m requirements_analyst schema analysis > backend/app/agents/requirements_analyst/schemas/analysis.schema.json
"$ANALYST_PYTHON" -m requirements_analyst schema result > backend/app/agents/requirements_analyst/schemas/result.schema.json
```

В чистом окружении проверено на Python 3.14.8 и Pydantic 2.14.1: зависимости
установлены из PyPI через `uv venv` / `uv pip install`, все 27 проверок прошли,
CLI вернул ожидаемое ожидание Q-001. Прямые и транзитивные зависимости закреплены
в requirements.txt. Другие версии Python, вызов конкурсной модели, качество
анализа на двух конкурсных кейсах, LangGraph/worker и storage не проверены.
CI для модуля не подключён; независимое review остаётся обязательным.
