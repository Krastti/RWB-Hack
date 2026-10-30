# Координация задачи #50

Снимок прочитанного upstream: `9fa0d4c133d9bbfa45e4acc200837d01694cf8a4`.
На момент повторного просмотра #50, #52, #75, #76, #77, #78 открыты, назначения пустые,
комментарии отсутствуют; открытых PR не найдено. Автор #50 уже заявил взятие
Requirements Analyst Agent в тексте issue. Перед новой задачей проверить состояние
повторно. Ни один из текстов ниже автоматически не опубликован.

## Где оставить комментарии сейчас

В [#50](https://github.com/Krastti/RWB-Hack/issues/50) — уточнить область и критерии:

> Продолжаю Requirements Analyst Agent в ветке `feat/requirements-analyst-agent`.
> Область: `backend/app/agents/requirements_analyst/`.
> Результат: цели, роли, FR/NFR, ограничения, сущности, сценарии,
> интеграции, безопасность, критерии приёмки, риски, допущения, вопросы;
> проверка источников и ссылок, стабильные FR ID при уточнении, ожидание при
> critical-вопросах. Выход: `requirements.json`, `questions.json`,
> `requirements-analysis.md`. Появившаяся #77 (A-03) пересекается с этой задачей;
> предлагаю вести дальнейшую реализацию там, а #50 связать как исходную заявку.
> Полный system-analysis.md из #77 пока отсутствует; шаблон зависит от #76.
> LangGraph (#78) и подключение worker (#80) в текущую работу не входят.
> До C-02 предлагаю локальный формат `requirements-analyst/0.1`; он требует
> согласования, совместимость с ещё не принятыми общими схемами не заявляю.

В [#77](https://github.com/Krastti/RWB-Hack/issues/77) — обозначить продолжение A-03:

> Продолжаю реализацию из #50 в ветке feat/requirements-analyst-agent:
> backend/app/agents/requirements_analyst/. Готово ядро A-03: FR/NFR, роли,
> сценарии, риски, допущения, вопросы, проверка источников, стабильные FR ID
> и ожидание критичных ответов. 27 локальных проверок прошли.
> Сейчас выход — requirements.json, questions.json, requirements-analysis.md.
> Для полного закрытия #77 остаются system-analysis.md через шаблон #76,
> согласование #52 и подключение принятого LLM interface. Пришлю Draft PR,
> #77 пока не закрываю. Путь nodes/analysis в issue указан как рекомендуемый;
> прошу подтвердить выбранную область модуля перед интеграцией.

В [#52](https://github.com/Krastti/RWB-Hack/issues/52) — согласовать контракт:

> Потребитель схем — #50/#77. Предлагаю согласовать контекст AgentInput/AgentResult,
> evidence источников, FR/NFR ID, вопросы с critical/reason/related_fr_ids,
> отдельные assumptions, usage с null при отсутствии токенов, артефакты
> с content/schema_version/FR IDs/checksum. Локальные схемы лежат в
> `backend/app/agents/requirements_analyst/schemas/`; это проект версии 0.1.
> Нужно зафиксировать владельца миграции на общую 1.0, stage_id и формат
> передачи предыдущего анализа и ответов. Общий каталог контрактов не меняю.

В [#75](https://github.com/Krastti/RWB-Hack/issues/75) — обозначить порт LLM:

> Для #50 нужен вызов complete(messages, schema, max_output_tokens,
> timeout_seconds) с текстом JSON, input/output tokens и нормализованной ошибкой.
> Отсутствующие токены — null; скрытых retry нет, попытками управляет orchestrator.
> В #50 есть локальный HTTP-транспорт для самостоятельного запуска, общую
> реализацию A-01 не беру. После принятия интерфейса подключу общий адаптер
> через LLMClient. Нужны фактический протокол и конфигурация конкурсной модели.

В [#76](https://github.com/Krastti/RWB-Hack/issues/76) — согласовать подключение шаблона:

> В #50/#77 уже есть ядро аналитического узла; локальный prompt покрывает
> извлечение требований, выход пока requirements-analysis.md. По критериям
> #77 узел должен вернуть полный system-analysis.md. Нужен интерфейс подключения
> полного prompt/шаблона из #76 и общая схема #52. После согласования подключу
> их в A-03; общие шаблоны и чужие каталоги самостоятельно не меняю.

## Перед следующей задачей

Обновить список issues, прочитать выбранную целиком, проверить assignee,
комментарии и PR. Комментарий «беру» оставлять в самой выбранной issue,
а вопросы об интерфейсе — в issue владельца зависимости. Не брать #52, #75,
#76 автоматически только потому, что они связаны с #50. A-04 уже заведена как
#78, подключение worker — #80: это следующие самостоятельные задачи.

## Git и PR выполняет пользователь

Ветка уже существует; не пересоздавать. Её имя не содержит номер issue, хотя
CONTRIBUTING.md рекомендует `<type>/<issue>-<description>`. При желании
переименовать самостоятельно; команды ниже сохраняют текущий выбор пользователя.

Сейчас допустим промежуточный commit и Draft PR. Полный merge требует выполнения
согласованных критериев, проверок и независимого review. AGENTS.md остаётся
личным локальным файлом пользователя; его не добавлять в коммиты.

Для проверенного состояния: в ветке ещё нет собственных коммитов, upstream
опережает базу на два коммита, добавляющих только docs/. Поэтому сначала
получить эти документы через fast-forward, оставаясь в текущей ветке:

```sh
printf '\n/AGENTS.md\n' >> .git/info/exclude
git fetch upstream
git merge --ff-only upstream/main
PYTHONPATH=backend/app/agents backend/app/agents/requirements_analyst/.venv/bin/python -m unittest discover -s backend/app/agents/requirements_analyst/tests -v
git add -- backend/app/agents/requirements_analyst
git diff --cached --name-only
git diff --cached --check
git diff --cached --stat
git commit -m "feat: add requirements analysis core (refs #50, #77)"
PYTHONPATH=backend/app/agents backend/app/agents/requirements_analyst/.venv/bin/python -m unittest discover -s backend/app/agents/requirements_analyst/tests -v
git rev-parse HEAD
git push -u origin feat/requirements-analyst-agent
```

Окружение .venv уже создано и не добавляется в Git. Если merge --ff-only
завершится ошибкой, остановиться и проверить новую историю/состояние, не применять
reset или force push. Перед commit убедиться, что в списке staged файлов нет
AGENTS.md, .venv и посторонних файлов. Не использовать git add . или git add -A.
Документы docs/ придут из upstream уже закоммиченными; заново добавлять их не нужно.
Не считать SHA до коммита доказательством проверки нового SHA.

Открыть Draft PR из форка в `Krastti/RWB-Hack:main`, использовать
[подготовленный текст](pr-description.md). После реального вызова модели,
согласования #52 и проверок актуального SHA заполнить подтверждения и передать
независимому reviewer. Push в upstream/main не требуется.
