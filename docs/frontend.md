# Frontend платформы

Angular 22.2.4, TypeScript 6.0.2, Bootstrap 5.3.8/SCSS; standalone components,
Router, Reactive Forms, HttpClient и RxJS. Версии и lock — в
[package.json](../frontend/package.json), [package-lock.json](../frontend/package-lock.json).
Это UI **платформы**. UI создаваемого приложения описывается отдельно
[профилем генерации](agents.md#профиль-c-05) и использует собственный backend.

## Разработка и сборка

Node.js 24.15+ ветки 24.x, см. [`.nvmrc`](../frontend/.nvmrc). Из корня:

```sh
cd frontend
npm ci
npm start
```

Открыть `http://localhost:4200`. Proxy перенаправляет `/api/**` на
`http://127.0.0.1:8000`. Нужны [настроенный backend и пользователь](backend.md#установка-и-запуск);
публичной регистрации нет. Сценарии отсутствующих endpoints не заменяются
статическими ответами: интерфейс показывает ошибку сервера.

```sh
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

Unit runner — штатный Angular builder/Vitest с компиляцией signal inputs, не JIT
обход. Playwright fixtures — только тестовая API-граница; тесты браузера не
доказывают работу настоящей PostgreSQL/LLM. Установка Chromium и E2E требуют
дополнительной среды; выполненные и пропущенные команды указываются в PR.
Для production раздаётся `dist/platform/browser`, неизвестный frontend route
возвращает index.html, `/api/v1` проксируется к backend. Это требование размещения,
а не готовый deploy/CI.

## Структура и маршруты

| Путь внутри `frontend/src/app` | Назначение |
|---|---|
| `app.routes.ts`, `layout/` | Router и навигация |
| `api/` | Generated schema, типизированный client, transport DI, interceptor/errors |
| `auth/` | Login, session/expiry, guard |
| `projects/` | Список, создание и карточка |
| `runs/` | Detail, monitor, timeline, questions, analysis и политика retry |
| `shared/` | Error banner, meaningful-text validator, not-found |

Маршруты: `/login`, `/projects`, `/projects/new`, `/projects/:id`, `/runs/:id`.
UI предусматривает loading/empty/error/forbidden/success; состояние отправки
блокирует повторный клик. Проекты получают latest_run_status/null вместе с
основным GET, без запроса Run на каждую строку. Карточка показывает исходную ревизию
и историю; действие создания Run зависит от существующего HTTP-контракта, но
POST создания Run и Run GET уже подключены штатной factory к PostgreSQL;
control/answers/artifacts остаются отдельной интеграцией.

## API client и DTO

Источник — [OpenAPI 2.1.0](../contracts/platform.openapi.yaml), префикс `/api/v1`.
После согласованного изменения контракта типы можно пересоздать из `frontend/`:

```sh
npm ci --prefix tools/api-types
npm run generate:api
```

Генератор изолирован в `tools/api-types`: его peer TypeScript 5 не смешивается с
TypeScript 6 Angular-приложения. Оба dependency tree зафиксированы lock-файлами;
`--force` и отключение peers не нужны. Генерация — не замена review итогового diff.

`ApiClient` предоставляет token/projects/runs/events/answers/artifacts и команды.
`API_BASE` по умолчанию `/api/v1`; `API_TRANSPORT` заменяется через DI для
контрактных примеров, основной путь — HttpClient. Пример:

```ts
const api = inject(ApiClient);
api.projects().subscribe(page => console.log(page.items));
```

Interceptor посылает Bearer только на origin/path настроенного API, оставляет
login публичным. `ApiError` содержит status/code/message/requestId/details и
Retry-After; ошибки JSON envelope текстовых артефактов также сохраняют эти поля.
Защищённый 401 очищает сессию и открывает вход. UI показывает безопасные message
и request_id, не raw stack/provider response. Серверные 422 остаются обязательными
даже при наличии клиентской проверки.

## Сессия и формы

Сессия — sessionStorage текущей вкладки (`rwb.session.v1`), при недоступном storage
остаётся в памяти. expires_in задаёт таймер; expiry/logout очищают state и
прекращают polling/запросы. Backend повторно проверяет активность пользователя:
клиентское хранение token не даёт дополнительных прав.

Project create проверяет name 1–120 и requirements 1–100000. Требования и отправляемые
answers должны содержать непробельный символ; Unicode whitespace совпадает с
[backend](backend.md#repositories-и-проекты). Валидный текст не trim-ится. Questions
проверяет maxlength 10000, required и whitespace, показывает связанное с textarea
сообщение и aria-invalid/aria-describedby после взаимодействия. Пустой optional
ответ можно не отправлять; непустой пробельный ответ запрещён.

Create/answers/retry используют устойчивый Idempotency-Key для повторения того же
тела после сетевой ошибки; автоматического replay записей клиент не запускает.
Изменившееся тело/область получает новый ключ. При смене Run/questions_revision
Questions отменяет старую подписку, очищает pending/key; старый HTTP-ответ не
применяется к новому Run. 409 stale revision вызывает обновление состояния.

## Polling, events и metrics

`RunMonitor.watch(id)` выдаёт `{run,events,error}`. Опрос каждые 2 секунды;
`exhaustMap` исключает перекрывающиеся циклы. Страницы events дочитываются по
`after_seq`; seq сортируются, дубли удаляются, чужой run_id игнорируется.
Не продвинувшийся cursor отклоняется. После сетевой ошибки следующий цикл
продолжает с принятого cursor. После terminal completed/cancelled/failed/
budget_exceeded опрос заканчивается только после успешного дочитывания событий;
waiting_for_input продолжает опрашиваться. Logout/уход со страницы отписывает
поток. Смена Run сбрасывает run/events/cursor и старые action-запросы.

429 Run/events приостанавливает polling **и** ручной refresh до Retry-After
(секунды или HTTP-date), иначе минимум на 2 секунды. После commands состояние
перечитывается. Повторная доставка событий не означает новый usage или attempt.

Числа карточки берутся из GET Run; его DTO не содержит общей идентичности
снимка с metrics.updated. Поэтому подпись всегда **«полнота неизвестна»**, даже
если старое событие имело tokens_complete=true. Это остаётся безопасным при
ошибке events, новом GET и восстановлении polling. Ноль — известная числовая
сумма, а не доказательство полного нулевого расхода.

Helper `tokenUsageNote(events)` выбирает максимальный seq metrics.updated
**до** проверки schema_version. Неизвестная последняя версия отменяет прежнюю
полноту. При будущем отображении event-снимка numbers/flags/identity нужно
применять вместе; нельзя присоединять флаги старого события к новым числам Run.

## Компоненты Run и команды

`Timeline` принимает Run/Event[] и сохраняет историю stage_id/attempt_no.
`Questions` принимает Run, отдаёт updated Run либо stale; пакет содержит текущую
questions_revision и ответы на обязательные вопросы. `Analysis(runId,refreshKey)`
читает pages артефактов, `requirements.json`, `questions.json` и
`docs/system-analysis.md` из **результата Run**, а не документации репозитория.
Refresh key зависит от статуса, ревизии и artifact.accepted/snapshot.prepared.
Новая загрузка отменяет предыдущую. Markdown через marked и Angular sanitization;
`bypassSecurityTrustHtml` не используется. FR/NFR/roles/constraints/assumptions/
questions отображаются отдельно.

Для встраивания импортируйте standalone-классы из `runs/`:

```html
<rwb-timeline [run]="run" [events]="events" />
<rwb-questions [run]="run" (updated)="reloadRun($event)" (stale)="reloadRun()" />
<rwb-analysis [runId]="run.run_id" />
```

Angular — активный профиль. Исторический React Run доступен только для просмотра:
UI скрывает cancel/retry/answers, direct handlers также отказывают; сервер
проверяет профиль независимо. Cancel доступен для queued/running/waiting_for_input;
cancelling показывает ожидание безопасного барьера.

Retry failed повторяет неуспешную попытку. Если latest по каждому stage_id уже
succeeded/skipped, UI показывает **RECOVERY_REQUIRED**: требуется внутреннее
восстановление checkpoint/сборки; генерация не нужна. Это не обещание работающего
background recovery. Для budget_exceeded retry предлагается только при причине
MAX_CALLS/MAX_OUTPUT_TOKENS; требуется рост нужного лимита, другой не уменьшается.
Input/time/unknown дают RETRY_NOT_SUPPORTED, кнопки бесполезного повтора нет.
Сервер дополнительно проверяет резервы/полномочия. Форма бюджета сохраняет ручные
правки при polling и reset-ится при новой бюджетной остановке с другими limits/attempts.

## Контрактные примеры C-04

[contracts/examples/index.json](../contracts/examples/index.json) задаёт HTTP status,
запрос и имя DTO каждого JSON. Это тела ответов без UI-полей или transport-обёрток,
не подключённые к production. `project-empty`/`runs-empty` показывают проект с
валидной ревизией и без запусков; run/events running, waiting-for-input, failed,
completed — **разные независимые Run**, не последовательность одного запуска.
Error JSON — ошибка команды 409, тогда как failed Run читается с HTTP 200.
StageAttempt.error включает внешний error envelope. UUID/времена/метрики вымышлены.

Event pages — хвосты после after_seq=40, начинают seq=41; отсутствие предыдущих
событий намеренное. has_more=false не запрещает будущие commits. Required-вопрос
без answer блокирует; уже подтверждённый answer не удаляется при публикации нового
снимка. manual_clarifications растёт при принятии ответа, не при показе вопроса.
Пара event metrics/tokens_complete согласована внутри примера, но принадлежность
GET/event в реальном API нельзя выводить из этой фикстуры. Для просмотра:

```sh
python -m json.tool contracts/examples/run-waiting-for-input.json
```

## Лицензия и границы готовности

Layout адаптирован из SB Admin Angular. Полный MIT-текст сохранён в
[THIRD_PARTY_NOTICES.md](../frontend/THIRD_PARTY_NOTICES.md). Исходный Angular 11/
Bootstrap 4/Pug runtime и демонстрационная авторизация шаблона не переносились.

Текущий UI не содержит готового дерева исходников, ZIP/GitLab-выдачи или mobile-этапа
(F-11/F-12/X-07). Они требуют backend storage/export и генераторов. F-01–F-05
соответствуют skeleton/client/login/projects, F-06–F-10 — timeline/polling/actions/
questions/analysis; F-13/F-14 — metrics и итоговая интеграция. Плановые функции,
размеры 360/768/1280 в тестах и наличие TypeScript DTO не доказывают полного pipeline.
