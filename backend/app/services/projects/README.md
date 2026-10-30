# Сервис проектов B-03 (#58)

Публичный интерфейс: `app.services.projects.ProjectService` и
`app.repositories.ProjectRepository`. Ошибки `InvalidProjectInput` и
`ProjectNotFound` находятся в `app.errors`. Прежние импорты из
`app.services.projects` сохранены для совместимости. Контракт: `contracts/platform.openapi.yaml`, API 1.0.0
(C-01). Код сервиса не использует HTTP, SQLAlchemy или драйвер БД.
Импорт `app.models.dto` загружает родительский пакет `app.models`,
который экспортирует ORM-модели и требует установленный SQLAlchemy.

Операции синхронные, с именованными аргументами:

- `create_project(owner_id, name, requirements_text) -> Project`: проект и
  ревизия №1 сохраняются одной атомарной операцией repository.
- `get_project(owner_id, project_id) -> Project`: карточка с последней ревизией.
- `list_projects(owner_id, limit=100, cursor=None) -> ProjectPage`: список
  владельца с непрозрачным курсором; порядок `(created_at DESC, id DESC)`.

Пример подключения в B-04 (repository — настоящий адаптер, реализующий Protocol):

```python
from app.services.projects import ProjectService

service = ProjectService(repository)
project = service.create_project(
    owner_id=current_user.id,
    name="Парковочный помощник",
    requirements_text="Показывать доступные парковочные места",
)
card = service.get_project(owner_id=current_user.id, project_id=project.id)
page = service.list_projects(owner_id=current_user.id, limit=20)
if page.next_cursor is not None:
    next_page = service.list_projects(
        owner_id=current_user.id, limit=20, cursor=page.next_cursor,
    )
```

`owner_id` берётся из доверенного контекста авторизации, а не тела запроса.
Repository фильтрует по владельцу до пагинации. Кодирование, привязка к владельцу
и проверка курсора — ответственность адаптера: неверный/чужой курсор вызывает
`InvalidProjectInput`. Сервис дополнительно не выдаёт чужие объекты даже при
ошибке адаптера. Отсутствующий и чужой проект дают одинаковый `ProjectNotFound`.
HTTP-слой B-04 отображает эти ошибки в общий envelope C-01 с кодами 422 и 404;
сбой хранилища пробрасывается, успешный ответ при нём не возвращается.

DTO `Project`, `RequirementRevision`, `ProjectPage` находятся в пакете
`app.models.dto` (повторно экспортируются из `app.services.projects` для
совместимости). DTO неизменяемые, имена полей соответствуют схемам `Project`,
`RequirementRevision`, `ProjectPage`. HTTP-адаптер сериализует UUID в строки,
UTC datetime в ISO 8601 с `Z`, tuple items в JSON-массив. Текст сохраняется
без trim/нормализации; hash — SHA-256 от точных UTF-8 байтов. Проверяются длины
1–120 и 1–100000 согласно C-01 (пробелы контрактом не запрещены).

В БД `current_revision` раскладывается в `RequirementRevision` с
`project_id=project.id`; её hash и номер сохраняются вместе с текстом.
Чтение выбирает максимальный номер ревизии. Существующие ревизии не меняются.
Создание новых ревизий при запуске относится к B-07: оно должно быть атомарным
с созданием запуска и обработкой идемпотентности. B-03 не предоставляет
отдельной операции обновления текста и не запускает агентов.

Реализация PostgreSQL repository, HTTP, JWT и миграции сюда не входят (B-04).
Постоянной заглушки или временного хранилища в модуле нет.
