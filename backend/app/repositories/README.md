# PostgreSQL-подключение B-04 (#59)

Контракт: `contracts/platform.openapi.yaml`, C-01 / API 1.0.0.
Единственный пакет репозиториев — `app.repositories` (имя согласовано
с пользователем). Старый `app.repository` переименован, импорты потребителей
обновлены; совместимого дубликата нет. Пустой `app.controller` удалён.

## Публичный интерфейс

- `ProjectRepository`, `UserRepository` — прежние Protocol из B-03/B-02.
- `PostgreSQLProjectRepository(sessions, cursor_secret=...)` реализует
  создание, карточку и список, возвращает DTO `app.models.dto`.
- `PostgreSQLUserRepository(sessions)` возвращает `UserCredentials` по login
  или UUID, включая актуальный статус учётной записи.
- `app.api.projects.configure_projects(app, service)` подключает маршруты
  и обработчики ошибок проекта.
- `app.main.create_app(database_url=..., auth_config=...)` собирает настоящее
  приложение: engine → sessionmaker → repositories → services → HTTP.
  Без аргументов читает DATABASE_URL и JWT_SECRET_KEY из окружения.

Каждая операция открывает собственную Session и закрывает её до возврата DTO.
Session не разделяется между потоками запросов. Создание проекта и первой
ревизии выполняется в одной транзакции; ошибка любого INSERT/commit откатывает
обе записи. Репозиторий не изменяет существующие ревизии. Карточка и список
выбирают ревизию с максимальным номером одним SQL-запросом.

Список фильтруется по owner_id в SQL до LIMIT и сортируется по
`created_at DESC, id DESC`. Следующая страница использует сравнение пары ключей,
а не OFFSET. Дополнительная строка определяет наличие следующей страницы.
Курсор содержит версию, владельца и позицию, кодируется URL-safe base64 и
подписывается HMAC-SHA256 с отдельным контекстом `projects:v1:`. Секрет берётся
из AuthConfig, минимум 32 байта; неизвестная версия, подмена, неверный формат
или чужой владелец дают 422. Курсор не содержит текст требований; содержимое
не зашифровано. Изменение секрета инвалидирует старые токены и курсоры.
Пагинация не фиксирует снимок списка: новые проекты появляются при обновлении
первой страницы.

## Запуск

Из корня репозитория, в Python-окружении проекта:

```sh
pip install -r backend/requirements.txt
export DATABASE_URL='postgresql+psycopg://USER:PASSWORD@localhost:5432/DATABASE'
export JWT_SECRET_KEY='REPLACE_WITH_RANDOM_SECRET_AT_LEAST_32_BYTES'
```

Перед первым запуском применить существующую миграцию B-01 через Flyway
(см. `backend/flyway.conf` и переменные FLYWAY_*). Приложение не создаёт таблицы
автоматически и не меняет существующую схему.

Предварительно создать пользователя с Argon2id hash; публичной регистрации нет.
Пример с интерактивным вводом пароля (запускается из корня репозитория):

```python
import os
from getpass import getpass
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from backend.app.models import User
from backend.app.services.auth import hash_password

engine = create_engine(os.environ["DATABASE_URL"], hide_parameters=True)
with Session(engine) as session, session.begin():
    session.add(User(login=input("Login: "), password_hash=hash_password(getpass())))
engine.dispose()
```

```sh
PYTHONPATH=backend uvicorn app.main:create_app --factory
```

Или собрать приложение явно в собственном entry point:

```python
from backend.app.config.auth import AuthConfig
from backend.app.main import create_app

app = create_app(database_url=database_url, auth_config=AuthConfig(secret_key=secret))
```

Не смешивать импорты `app.*` и `backend.app.*` в одном процессе. Engine
освобождается при завершении lifespan. URL допускает только PostgreSQL,
драйвер нормализуется в `postgresql+psycopg`.

## HTTP

Сначала `POST /api/v1/auth/token` с JSON `{login, password}`, затем заголовок
`Authorization: Bearer <access_token>` для всех маршрутов проектов:

| Метод | Путь | Результат |
|---|---|---|
| POST | `/api/v1/projects` | `{name, requirements_text}` → 201 Project с ревизией №1 |
| GET | `/api/v1/projects?limit=100&cursor=...` | 200 ProjectPage: items и next_cursor/null |
| GET | `/api/v1/projects/{project_id}` | 200 Project с актуальной ревизией |

Владелец берётся только из current_user. Передавать owner_id в теле нельзя.
Даты сериализуются в UTC ISO 8601 с `Z`, UUID — в строки. Отсутствующий и чужой
проект дают одинаковый 404 NOT_FOUND. Ошибки тела, UUID, limit и cursor — 422
VALIDATION_ERROR. Без авторизации/с неактивным пользователем — 401 UNAUTHORIZED.
SQLAlchemyError — безопасный 500 INTERNAL_ERROR без SQL, параметров и реквизитов
соединения. Все ошибки используют C-01 envelope с request_id. Общий
ContractRoute обеспечивает такой же формат ошибок валидации на auth и projects;
декораторы OpenAPI описывают фактические DTO ошибок.

Новых миграций, временных хранилищ и mock в приложении нет. Запуски генерации
и изменение требований через новый запуск остаются отдельными задачами.

## Уточнения по HTTP и OpenAPI после review #96

Ошибки маршрутизации также возвращают C-01 envelope: неизвестный URL —
404 NOT_FOUND, неподдерживаемый метод — 405 METHOD_NOT_ALLOWED. Код
METHOD_NOT_ALLOWED — локальное расширение для 405, не перечисленного в C-01;
форма ответа совпадает со схемой Error. Сохраняются служебные заголовки
Starlette, включая Allow. Текст HTTPException не передаётся клиенту.

Generated OpenAPI проектов перечисляет также 409 и 429 из C-01. В текущем
B-04 они зарезервированы: обработка предметных конфликтов проекта и rate
limiting отложены. Добавление описаний не включает эти механизмы; ошибки БД
по-прежнему дают безопасный 500. Для будущего 429 описан Retry-After в секундах.
