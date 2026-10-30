# Frontend платформы

Angular 22.2.4, TypeScript 6.0.2, Bootstrap 5.3.8/SCSS.
Standalone-компоненты, Router, Reactive Forms, HttpClient и RxJS.
Layout и оформление адаптированы из SB Admin Angular; лицензия находится
в [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Запуск

Нужен Node.js 24.15+ версии 24.x (см. `.nvmrc`). Из этого каталога:

```sh
npm ci
npm start
```

Открыть http://localhost:4200. Запросы `/api/**` направляются proxy на
`http://127.0.0.1:8000`; запуск FastAPI и PostgreSQL описан в
[backend README](../backend/app/repositories/README.md).
Создания пользователей через UI нет: вход использует существующую запись backend.

Сборка и проверки:

```sh
npm run build
npm test
npx playwright install chromium
npm run test:e2e
```

Для production требуется раздавать `dist/platform/browser`, возвращать
`index.html` для frontend-маршрутов и проксировать `/api/v1` на backend.
Деплой в эту задачу не входит.

## Контракты и интерфейсы

Источник DTO — [OpenAPI платформы 2.1.0](../contracts/platform.openapi.yaml).
HTTP-префикс `/api/v1` сохранён; новый профиль — `web-fastapi-angular`.
После изменения OpenAPI пересоздать `src/app/api/schema.ts`:

```sh
npm ci --prefix tools/api-types
npm run generate:api
```

Генератор изолирован в `tools/api-types`: его peer dependency требует TypeScript 5,
а приложение Angular использует TypeScript 6. Оба дерева зафиксированы lock-файлами,
без `--force` или отключения проверки peer dependencies.

`ApiClient` предоставляет типизированные операции входа, проектов, запусков,
событий, вопросов и артефактов. Пример вызова:

```ts
const api = inject(ApiClient);
api.projects().subscribe(page => console.log(page.items));
```

`API_BASE` задаёт адрес API (по умолчанию `/api/v1`). `API_TRANSPORT` позволяет
заменить HttpClient через DI для контрактных примеров; основной сценарий всегда
использует HTTP. Interceptor передаёт token только origin/path выбранного API,
оставляет вход публичным и преобразует ошибки в `ApiError` со status/code/message,
requestId и details. При защищённом 401 сессия очищается и открывается вход.

Сессия хранится в sessionStorage текущей вкладки, при недоступности storage —
в памяти. Истечение `expires_in` и выход очищают сессию и прекращают polling.
Ответы и операции создания/повтора используют ключ идемпотентности, сохраняемый
при повторе того же запроса после ошибки. Автоматического повтора записей нет.

`RunMonitor.watch(id)` возвращает RunSnapshot с запуском, событиями и ошибкой.
Статус опрашивается раз в 2 секунды; страницы событий дочитываются через after_seq,
дубли seq исключаются. Сетевая ошибка сохраняет историю, следующий цикл продолжает
с последнего принятого курсора. Терминальный статус останавливает опрос после
успешного дочитывания событий; уход со страницы и выход отменяют подписку.
Timeline получает DTO через inputs; Questions отправляет пакет по текущей ревизии.
Признаки `tokens_complete` из поддерживаемых событий C-03 `metrics.updated`
определяют подпись расхода: полный, неполный или с неизвестной полнотой.
Числовой Run.metrics сам по себе не доказывает полноту токенов.
Analysis читает Markdown/JSON из артефактов; HTML проходит Angular sanitization,
обхода защиты через bypassSecurityTrustHtml нет.

Маршруты экранов: `/login`, `/projects`, `/projects/new`, `/projects/:id`,
`/runs/:id`. Компоненты результата можно использовать отдельно с DTO:

```html
<rwb-timeline [run]="run" [events]="events" />
<rwb-questions [run]="run" (updated)="reloadRun($event)" (stale)="reloadRun()" />
<rwb-analysis [runId]="run.run_id" />
```

`Timeline` принимает Run и Event[]; `Questions` принимает Run и возвращает
обновлённый Run либо сигнал устаревшей ревизии; `Analysis` принимает runId и
читает артефакты через ApiClient. Для встраивания импортировать standalone-классы
из `src/app/runs/` в `imports` родительского компонента.

## Границы готовности

Реализованы интерфейсы входа, создания/списка/карточки проекта, истории/действий
запуска, этапов/событий, вопросов и просмотра анализа. В текущем backend
реализованы вход, операции проектов и модуль чтения запусков/событий.
Подключение чтения к PostgreSQL/HTTP factory, создание и управление запусками,
ответы и артефакты требуют соответствующих BE-задач. До их подключения
сервер вернёт ошибку, которая показывается в UI. Статические данные не подставляются.

Unit-тесты проверяют HTTP-границы, ошибки, сессию и восстановление polling.
Браузерные тесты используют явно заданный API fixture исключительно внутри
Playwright: проверяют пользовательский сценарий, 401, очистку сессии,
безопасное отображение анализа и ширины 360/768/1280 px. Они не подтверждают
готовность отсутствующего backend runtime или полного pipeline.

Дерево исходников, ZIP, GitLab-экспорт и мобильный этап остаются задачами
F-11/F-12/X-07 из roadmap. Генерируемый frontend и его агент — отдельные A-10/A-11;
данный каталог содержит интерфейс самой платформы.

## Исправления review PR #100

- Project.latest_run_status приходит вместе с GET /projects. Backend выбирает
  последний Run по created_at DESC/id DESC коррелированным подзапросом в том же
  SELECT. Список не запрашивает историю для каждого проекта; null означает отсутствие
  запусков. Это дополнение OpenAPI 2.1.0, новых таблиц/миграций нет.
- Analysis принимает refreshKey: родитель меняет его при смене состояния, ревизии
  вопросов или событиях artifact.accepted/snapshot.prepared. Завершение того же
  Run обновляет анализ автоматически. Новый ключ отменяет предыдущее чтение.
- Questions отменяет отправку при смене run_id/questions_revision, сбрасывает
  pending и ключ идемпотентности нового пакета; чужой ответ не попадает в новый Run.
- Форма бюджета сохраняет ручные правки при обычном polling и заново заполняется
  при следующем budget_exceeded с новыми лимитами/попыткой. Увеличить посторонний
  лимит вместо исчерпанного нельзя. Если public Metrics не определяет нехватку
  резерва следующей операции, UI допускает увеличение любого одного лимита; сервер проверяет
  реальный необходимый запас. Уменьшение другого лимита запрещено.
- 429 при чтении Run/событий приостанавливает polling и ручной refresh до
  Retry-After (секунды или HTTP-date); при отсутствии корректного значения — 2 секунды.
- JSON envelope ошибок текстовых артефактов сохраняет code/message/details/request_id.

npm test использует штатный Angular unit-test builder и Vitest с компиляцией
signal inputs, а не отдельный JIT runner. Регрессии PostgreSQL:

```sh
PYTHONPATH=backend RWB_TEST_DATABASE_URL=postgresql+psycopg://user:password@localhost/test_db \
  python -m unittest discover -s backend/tests -p 'test_project_summary.py' -v
```

Нужна отдельная UTF-8 тестовая БД. Тесты применяют реальные миграции в собственных
схемах и удаляют только созданные ими схемы.
