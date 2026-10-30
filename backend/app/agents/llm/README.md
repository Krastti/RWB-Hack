# LLM adapter — A-01 (#75)

Асинхронный адаптер к `POST {base_url}/chat/completions`. Не содержит
аналитического prompt, графа, доступа к БД, повторов или mock-ответов модели.
Один `generate()` выполняет максимум один HTTP-вызов. Повторы и бюджеты принадлежат
вызывающему графу; отмена задачи (`CancelledError`) распространяется наружу.

## Зависимости и запуск

Python 3.11+. Пока backend не имеет общего файла зависимостей, зависимости
этого модуля фиксируются отдельно:

```sh
python -m pip install -r backend/app/agents/llm/requirements.txt
python -m backend.app.agents.llm.example
```

Перед запуском задайте `LLM_API_KEY` в окружении процесса из хранилища секретов.
Не записывайте ключ в исходники или команду, сохраняемую в shell history.
Пример делает настоящий запрос и требует доступного API и действующего ключа.
Схема примера демонстрационная; она не заменяет результат анализа требований.

Конфигурация примера (остальной модуль не читает окружение):

| Переменная | Значение по умолчанию |
|---|---|
| `LLM_BASE_URL` | `http://45.146.131.213:14000/v1` |
| `LLM_MODEL` | `qwen3.8-27b` |
| `LLM_API_KEY` | обязательна, без значения по умолчанию |
| `LLM_RESPONSE_FORMAT` | `json_schema` |

Это предоставленное командой подключение. Сервер требует Bearer-аутентификацию.
Реальный вызов примера подтвержден с ключом: модель вернула
`{"name":"Parking Helper"}`, ответ прошел локальную проверку схемы, API вернул
входные и выходные токены. Это проверка демонстрационной схемы, а не всех будущих
контрактов агентов или всего подмножества JSON Schema на сервере.

## Публичный интерфейс

Типы разделены на пакеты: `config` — `LLMConfig`, `dto` — `LLMResult`
и `LLMUsage`, `error` — `LLMError`. В `adapter.py` остается логика вызова
и обработки ответа. Все публичные классы доступны через `backend.app.agents.llm`;
существующие импорты из `adapter` также сохранены.

```python
from backend.app.agents.llm import LLMAdapter, LLMConfig

config = LLMConfig(
    base_url=server_url,
    model=model_name,
    api_key=secret_from_environment,
    timeout_seconds=60.0,
    max_output_tokens=2048,
    response_format="json_schema",
)
adapter = LLMAdapter(config)
result = await adapter.generate(system=system_prompt, prompt=input_text, schema=output_schema)
if result.ok:
    structured_data = result.data
else:
    error_code = result.error.code
usage = result.usage
```

`schema` — самодостаточная JSON Schema (объект). Поддерживаются локальные `$ref`;
загрузка внешних схем запрещена. Схема копируется, проверяется до отправки и
передается модели. Ответ обязательно разбирается как JSON и проверяется локально,
включая известные `format` через `jsonschema.FormatChecker`.
Дубли ключей, NaN/Infinity, Markdown-обертки и несоответствие схеме отклоняются.

`json_schema` запрашивает strict structured outputs. Если фактическая модель
поддерживает только JSON mode, оператор явно задает `response_format="json_object"`:
схема остается в системной инструкции и локальная проверка обязательна. Автоматического
перехода между режимами нет. Неподдерживаемую сервером схему/параметр сервер может
отклонить: адаптер не переписывает контракт ради успешного ответа.
Лимит вывода передается как `max_tokens` для Chat Completions совместимых серверов.

`LLMResult`: `data` (только при успехе), `usage`, `error`, свойство `ok`.
`LLMUsage`: `tokens_in`, `tokens_out`, `total_tokens` из ответа провайдера,
`latency_ms` (монотонные часы), `calls` (0 до HTTP-запроса, иначе 1).
Отсутствующие счетчики равны `None`, а не выдуманному нулю.
При неправильном ответе сохраняются уже прочитанные счетчики. При сетевой ошибке
или таймауте расход может быть неизвестен; отправленный запрос всё равно учитывается.
Таймаут ограничивает суммарное сетевое ожидание, включая чтение тела и закрытие
клиента; синхронная локальная валидация схемы в него не входит.

`LLMError` содержит только фиксированные `code`, `message`, `retryable` и
необязательный `http_status`. `retryable` — рекомендация графу, не запуск повтора.

| Код | Причина | Возможен повтор |
|---|---|---|
| `LLM_INVALID_INPUT` | Неверный prompt/system | нет |
| `LLM_INVALID_SCHEMA` | Некорректная схема | нет |
| `LLM_TIMEOUT` | Сетевой дедлайн, HTTP 408/504 | да |
| `LLM_UNAVAILABLE` | Ошибка транспорта или HTTP 5xx | да |
| `LLM_AUTH_ERROR` | HTTP 401/403 | нет |
| `LLM_RATE_LIMITED` | HTTP 429 | да |
| `LLM_REQUEST_REJECTED` | Прочий неуспешный HTTP, включая redirect | нет |
| `LLM_REFUSED` | Отказ модели | нет |
| `LLM_OUTPUT_TRUNCATED` | `finish_reason=length` | нет |
| `LLM_INVALID_RESPONSE` | Некорректный JSON, usage, завершение или нарушение схемы | нет |

Неверная конфигурация вызывает `ValueError` с фиксированным текстом до вызова.
Адаптер не логирует запросы, ответы, ключи или исключения провайдера. Ключ скрыт
из `repr(config)`, `data` из `repr(result)`; ответ с отраженным ключом отклоняется.
Ключ передается только в Authorization, redirects и proxy-настройки окружения
отключены. Не включайте отладочное логирование HTTP-тел/заголовков в приложении;
не сериализуйте конфигурацию с секретом через `dataclasses.asdict`.

## Общие контракты и границы готовности

Базовый SHA: `d54a1d4` (актуальный `origin/main` при пересоздании коммита).
Основание: `docs/project-specification.md`, §5, §9–10, §12; issue #75.
Общие контракты C-02 доступны в `contracts/agents/v1/`, schema_version=1.0.
Типы этого модуля — внутренний интерфейс транспорта, не AgentInput/AgentResult.
В контракте этапа используются `input_tokens`, `output_tokens`, `duration_seconds`
и список `model_calls`; здесь возвращаются `tokens_in`, `tokens_out`, `latency_ms`.
Вызывающему коду необходимо преобразовать эти поля, назначить call_id и собрать
usage/diagnostics этапа. Схемы C-02 содержат межфайловые `$ref`; перед отправкой
нужно подготовить самодостаточную provider-совместимую схему.
Это подключение пока не реализовано: полная готовность issue A-01 не заявляется.
Общие контракты и модуль аналитика данным коммитом не изменяются.

Документация протокола и библиотек:

- [Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
- [HTTPX timeouts](https://www.python-httpx.org/advanced/timeouts/)
- [jsonschema validation](https://python-jsonschema.readthedocs.io/en/stable/validate/)
