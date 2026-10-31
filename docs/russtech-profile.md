# Конкурсный профиль russtech-fullstack 1.0.0

## Профиль C-05

Профиль продукта: Angular 20.1.6, Bootstrap 5.3, ASP.NET Core 10,
C# 14, EF Core 10, PostgreSQL 17.9. Платформа сохраняет свой стек.
Kotlin 2.2.21 включается только при has_android=true в принятой архитектуре.
PostGIS включается при географических требованиях; Kafka требует обоснования.

### Ownership и generation plan

Default deny. Android владеет android/, verification — tests/, verification/,
compose.yaml, .env.example и .gitlab-ci.yml. Backend не может писать миграции.
Каждая задача до dispatch перечисляет точные output_paths; каталоги не являются
перечнем файлов. Условия has_android и has_integrations задаёт доверенный caller
из принятой архитектуры. Отсутствующее условие не означает false.

### Правила генерируемого приложения

Только бизнес-требования определяют предметную модель. OpenAPI, DTO, сущности,
экраны и проверки должны использовать одинаковые идентификаторы и права.
Миграции продукта — EF Core, расположены в backend/migrations и включаются
в App.csproj. Применяются отдельным migration job перед запуском приложения
на чистой PostgreSQL. Flyway платформы к продукту не применяется.
Frontend должен включать lockfile для npm ci. Android должен включать Gradle
wrapper с проверенными distribution checksum и полным app module.
Нужны реальные acceptance tests, seed, health checks, Compose и GitLab CI.
Все секреты поступают из окружения; credentials не входят в artifacts.
Недоверенный код выполняется только отдельным изолированным runner без доступа
к socket Docker, сети и секретам платформы. Evidence связывается с snapshot.
Недоступные build/test/security/deploy имеют статус not_run или blocked.
Внешняя публикация разрешена только по server-managed GitLab configuration.

### Limits и остановки

Лимиты хранятся в profile.json и замораживаются вместе с правилами.
Автоматических повторов этапа не более двух. Неизвестный usage не равен нулю.
Исправления создают новые версии и snapshots. Отсутствие runner запрещает PASS.

Этот профиль задаёт политику и обязательные выходы, но сам по себе не доказывает
работоспособность генераторов, сборки, Android, миграций или production E2E.
HTTP-платформа пока не предлагает его для запуска.
