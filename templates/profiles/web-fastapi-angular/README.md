# Профиль web-fastapi-angular

Значения профиля — [profile.json](profile.json), формат —
[profile.schema.json](profile.schema.json).

Правила интерпретации, обязательные результаты, ownership, limits и проверка
централизованы в [документации агентов](../../../docs/agents.md#профиль-c-05).
Этот указатель сохранён, поскольку поле `rules_document` профиля и его схема
фиксируют имя `README.md`. Сам профиль и его права не изменены.

<!-- rwb-profile-rules: docs/agents.md#профиль-c-05 -->

Этот файл — только указатель, не полный набор правил исполнения. Потребитель
обязан разрешить указанную allowlisted локальную цель по контракту
`load_profile_rules` из `contracts/agents/validate.py`; иначе загрузка профиля
завершается отказом. Произвольные Markdown redirects, сеть и symlinks запрещены.
Production loader ещё должен подключить эту проверку; offline `--check` проверяет
доступность полного документа, а не готовность интеграции.
