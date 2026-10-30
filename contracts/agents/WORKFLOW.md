# Действия пользователя: C-02 в форке

Реализация подготовлена локально только в contracts/agents/. Текущая ветка
feat/requirements-analyst-agent сохранена; commit/push/PR не выполнялись.
Перед коммитом перенести изменения в отдельную ветку C-02 от актуального upstream.

## 1. Сообщить о задаче

В https://github.com/Krastti/RWB-Hack/issues/52:

> Беру C-02 в ветке docs/52-agent-contracts. Подготовлены схемы версии 1.0
> в contracts/agents/: AgentInput/AgentResult, артефакты, требования, вопросы,
> архитектура, generation-plan, manifest и traceability. Есть примеры и локальная
> проверка. Поля вопросов и metadata учтены по принятому API платформы.
> Прошу потребителей #75/#76/#77/#78 согласовать формат до merge.
> Runtime аналитика и чужие модули в этом PR не меняются.

Этот комментарий не опубликован автоматически.

## 2. Получить последние изменения и создать ветку

Из корня репозитория:

```sh
git status --short
git fetch upstream
git switch -c docs/52-agent-contracts upstream/main
```

Создаётся новая ветка от основного репозитория, без переключения/перезаписи main
в вашем форке. Новые незакоммиченные файлы contracts/agents/ останутся на месте.
origin — VenEraBle842/RWB-Hack, upstream — Krastti/RWB-Hack.
Если команда сообщает о конфликтующих файлах, существующей ветке или других
ошибках, остановиться и проверить состояние; не применять reset/force push.

## 3. Подготовить окружение и проверить

```sh
python3 -m venv contracts/agents/.venv
source contracts/agents/.venv/bin/activate
python -m pip install -r contracts/agents/requirements.txt
python contracts/agents/validate.py --check
```

Окружение уже подготовлено при реализации. Блок можно повторить; на другом
компьютере он создаст окружение заново. PYTHONPATH не нужен.

Ожидаемый результат:

```text
OK: 11 schemas, 22 examples, 4 linked bundles, 1 snapshot; offline
```

## 4. Проверить состав и закоммитить

```sh
git add -- contracts/agents
git diff --cached --stat
git diff --cached --check
git commit -m "docs: define agent contracts v1 (#52)"
```

В staged diff должны быть только contracts/agents/. AGENTS.md — личный файл,
не добавлять его; .venv и __pycache__ игнорируются локальным .gitignore модуля.
Не использовать git add . или git add -A.

## 5. Проверить коммит и отправить в форк

```sh
python contracts/agents/validate.py --check
git rev-parse HEAD
git rev-parse upstream/main
git push -u origin docs/52-agent-contracts
```

Сохранить первые два SHA для описания PR: проверенный коммит и база main.
Если проверки упали, push отложить до исправления.

## 6. Открыть PR и согласовать

На GitHub создать Draft PR:

- Base repository: Krastti/RWB-Hack, base: main.
- Head repository: VenEraBle842/RWB-Hack, compare: docs/52-agent-contracts.
- Заголовок: `docs: define agent contracts v1 (#52)`.
- Описание: [PR_DESCRIPTION.md](PR_DESCRIPTION.md); заменить SHA-заглушки,
  сверить реальные результаты и не отмечать несогласованные пункты.

В #52 добавить ссылку на PR. В #75 написать:

> Для A-01 подготовлен C-02 / #52: AgentInput/AgentResult, usage с model_calls,
> nullable токенами и нормализованные diagnostics. Прошу проверить пригодность
> для вашего адаптера. Схемы используют локальные $ref Draft 2020-12; форму,
> поддерживаемую фактическим provider, готовит адаптер. PR: <ссылка>.

В #76 написать:

> Предложен C-02 / #52: requirements/questions 1.0 и analysis-input, UUID вопросов
> и поля question_id/required/question по API платформы. Полный system-analysis.md
> остаётся шаблоном A-02; runtime аналитика не мигрирован. Прошу согласовать
> вход/выход prompt с новыми схемами. PR: <ссылка>.

В #78 написать:

> Подготовлен C-02 / #52: контекст вызова, immutable artifact references,
> результаты waiting_for_input/failed/cancelled/skipped/succeeded и usage.
> Run/Stage lifecycle остаётся в #53; прошу проверить пригодность для state
> и checkpoint графа. PR: <ссылка>.

Ничего из этих сообщений не отправлено автоматически. Версию 1.0 не считать
принятой до согласования и merge. Для приёмки C-02 не требуется реализовывать
все зависимые runtime-задачи: нужны схемы, примеры и согласованный интерфейс.

## 7. После review

Если в main появились изменения, обновить текущую ветку и повторить проверку:

```sh
git fetch upstream
git merge --no-edit upstream/main
python contracts/agents/validate.py --check
git push
```

Обновить SHA и результаты в PR. После согласования контракта, обязательных checks
и минимум одного одобрения другого reviewer — пользователь выполняет Squash
and merge. Затем обновить рабочую копию из upstream; миграцию аналитика в #77
вести отдельной веткой и PR. Самостоятельно удалять или переключать старую
ветку помощник не будет.
