# A-29 / #103 — загрузчик профиля

Контракты: C-02 1.0, C-05 `web-fastapi-angular/1.0.0`, база
`7992cfb662ee53f30d57dcd321d196273d761e90`. Изменения ограничены этим модулем.

```python
from backend.app.agents.profiles import ProfileLoader

loader = ProfileLoader()
profile = loader.load("web-fastapi-angular")
private_run_snapshot = profile.snapshot()
# Сохранить snapshot и его metadata атомарно с Run в доверенном хранилище.
restored = loader.restore(
    private_run_snapshot,
    expected_checksum=profile.checksum,
    expected_rules_checksum=profile.metadata["rules_checksum"],
)
assert restored.allows("frontend", "frontend/src/app/orders.ts")
outputs = restored.required_outputs(conditions={"has_integrations": False})
limits = restored.limits("frontend")
```

`ProfileLoader` разрешает ID/версию только через серверный реестр `ProfileSource`.
Клиент не передаёт путь/реестр/schema. Неизвестный ID/версия, unsafe path, symlink,
нарушение JSON/schema/grants/лимитов дают `ProfileError.code`; текст входа и
содержимое файлов в диагностику не попадают. Схемы и references разрешаются
локальным Registry C-02, без сети. Профиль/schema ограничены 1 MiB.

Проверяются конфликты exact files/directories, владельцы обязательных и условных
выходов, соответствие лимитов вложенных областей. `allows(..., directory=True)`
проверяет grant каталога; `allows` для файла не принимает сам каталог как файл.
`validate_plan` совмещает C-02 semantic validation и C-05 grants.
Условия обязательных файлов задаёт вызывающий код из принятой архитектуры:
пропуск `has_integrations` не превращает неизвестность в отсутствие интеграций.

`FrozenProfile` хранит immutable bytes, а `data`, `grant`, `limits` и
`required_outputs` возвращают отдельные копии. SHA-256 профиля считается по
исходным UTF-8 байтам, не по повторной сериализации JSON. Правила разрешаются
через существующий `load_profile_rules` (README marker → `docs/agents.md`),
сохраняются вместе с собственным checksum. При восстановлении не перечитываются
текущие profile/rules/defaults; используется сохранённая версия и разрешённая
сервером схема. Старые версии схем необходимо сохранять в registry до окончания
соответствующих Run. Отсутствующая версия останавливает восстановление.

`snapshot()` — приватный JSON-контекст Run, не новое поле AgentInput/AgentResult.
`expected_*` при restore берутся из доверенной записи Run после проверки владельца,
а не из запроса пользователя. Хеш обеспечивает целостность, не авторизацию.
Graph/worker получают snapshot явно; этот модуль не изменяет чужой checkpoint,
не записывает БД, не резервирует бюджет и не запускает генераторы.

Офлайн-пример из корня репозитория, Python 3.11+:

```sh
python -m pip install -r backend/app/agents/profiles/requirements.txt
python -m backend.app.agents.profiles.example
```

Для worker namespace: `PYTHONPATH=backend:. python -m app.agents.profiles.example`.
При упаковке нужны ресурсы `contracts/agents`, `templates/profiles` и `docs/agents.md`
от одной версии checkout. Откат — исключить вызовы loader и вернуть код; уже
сохранённые snapshots не удалять и не заменять новым текущим профилем.
