# Авторизация B-02 (#57)

Контракт C-01: `contracts/platform.openapi.yaml`, API 2.0.0.
`POST /api/v1/auth/token` принимает JSON `{login, password}` и возвращает
`{access_token, token_type: "bearer", expires_in}`. OAuth2 form не используется.

## Подключение

Установить `backend/requirements.txt`. `httpx` нужен также из-за существующего
экспорта `LLMConfig` в родительском пакете `app.config`.

```python
import os
from fastapi import Depends, FastAPI
from app.api.auth import configure_auth, current_user
from app.config.auth import AuthConfig
from app.models.dto.auth import CurrentUser
from app.services.auth import AuthService, hash_password

# repository реализует app.repositories.UserRepository.
# Пользователи создаются заранее: сохранить hash_password(password)
# в User.password_hash; plaintext не сохранять и не логировать.
service = AuthService(repository, AuthConfig(secret_key=os.environ["JWT_SECRET_KEY"]))
app = FastAPI()
configure_auth(app, service)  # один раз при создании приложения

@app.get("/private")
def private(user: CurrentUser = Depends(current_user)):
    return {"id": str(user.id), "login": user.login}
```

`JWT_SECRET_KEY` — случайный секрет минимум 32 байта, например результат
`secrets.token_urlsafe(32)`. Встроенного секрета и fallback нет. Все workers
используют один ключ, issuer и audience. Ключ не включается в repr конфигурации.
`AuthConfig` задаёт issuer (`rwb-platform`), audience (`rwb-platform-api`) и срок
действия 1800 секунд (допустимо 1–86400). Замена ключа инвалидирует выданные JWT.

## Разделение модулей

- `app.services.auth`: `AuthService`, `hash_password`, `verify_password`.
- `app.models.dto.auth`: `UserCredentials`, `CurrentUser`, `TokenRequest`, `Token`.
- `app.repositories.users`: синхронный `UserRepository` с `get_by_login(login)`
  и `get_by_id(user_id)`; отсутствующий пользователь возвращается как `None`.
- `app.errors.auth`: `Unauthorized`.
- `app.api.auth`: router, `configure_auth`, `current_user`, `get_auth_service`.

Repository возвращает `UserCredentials` из существующих полей User B-01.
`CurrentUser` не содержит hash. DTO с hash, паролем или токеном скрывают эти
поля в repr. Сервис не хранит plaintext; Argon2id создаёт случайную соль для
каждого hash. Неизвестный login также вызывает проверку dummy hash. Для
существующих пользователей с повреждённым hash вход отклоняется.

JWT подписывается HS256; проверяется фиксированный алгоритм, подпись, issuer,
audience, срок действия и обязательные sub/iat/exp/iss/aud. `sub` — UUID User.
При каждом обращении `current_user` заново читает пользователя: удаление или
отключение учётной записи немедленно запрещает использование токена.

Неверный вход, отсутствующий Bearer, неверный/истёкший JWT и неактивный
пользователь возвращают одинаковый 401 `UNAUTHORIZED` и `WWW-Authenticate:
Bearer`. Ошибка содержит envelope C-01: code, message, details, request_id.
Если middleware уже назначил `request.state.request_id`, он сохраняется;
иначе генерируется UUID. Ошибки тела login возвращают 422 `VALIDATION_ERROR`
без исходного ввода, чтобы пароль не попадал в ответ.
В OpenAPI декоратор login endpoint явно описывает ответы 401 и 422 через
DTO `app.models.dto.errors.Error` по схеме C-01; стандартная схема
FastAPI `HTTPValidationError` для этого endpoint не используется.
`configure_auth` устанавливает обработчик только `Unauthorized`; валидация
перехватывается только для auth router и не меняет остальные маршруты.

PostgreSQL-адаптер User и сборка приложения относятся к B-04. Постоянных mock,
публичной регистрации, refresh token и миграций нет. Роутер работоспособен при
подключении реального repository через `configure_auth`.

Реализация сверена с официальными руководствами:
[FastAPI JWT](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/),
[PyJWT API](https://pyjwt.readthedocs.io/en/stable/api.html).
