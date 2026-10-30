# Переход аналитика 0.1 на C-02 / 1.0

Это план подключения для #77 после принятия #52. C-02 не меняет существующий
backend/app/agents/requirements_analyst и не объявляет его совместимым с 1.0.
В одном PR #52 фиксируется контракт; реализация преобразования идёт отдельно.

| Локальная версия 0.1 | Общая версия 1.0 |
|---|---|
| schema_version=requirements-analyst/0.1 | schema_version=1.0 |
| contract_version=requirements-analyst/0.1 | согласованная версия предметного контракта, например 1 |
| stage_id=requirements | analysis в принятом профиле; допустимые узлы задаёт граф |
| requirements_text / answers / previous_analysis внутри AgentInput | разрешённые input_artifact_ids; текст и ответы в analysis-input, предыдущий анализ по reference |
| Question.id=Q-001 | question_id=UUID, стабильный в рамках run и вопроса |
| Question.text | question |
| Question.critical | required |
| answer=null | поле answer опускается |
| Answer.question_id=Q-001 | UUID из сохранённого Question |
| нет ревизии вопросов | questions_revision в requirements/questions и пакете ответов |
| Artifact.kind=application/json | kind=requirements, media_type=application/json |
| нет artifact_id/attempt_id/version/author_agent/size_bytes | все metadata поля заполняет/проверяет worker |
| requirements-analysis.md | полный docs/system-analysis.md по шаблону #76; переименование старого файла не заменяет заполнение разделов |
| usage только агрегатный | model_calls с отдельными вызовами и агрегаты |
| diagnostics code/message/retryable | дополнительно severity и related_fr_ids |

FR/NFR/ROLE/SC/AC/ASM/RISK ID и тексты/источники сохраняются при переносе.
Нельзя создавать новые FR только из-за изменения wire-версии.

Вопросы уже выставленного запуска получают постоянное отображение Q ID → UUID.
Возможный способ: хранить таблицу соответствия либо UUIDv5 с namespace=run_id
и фиксированным именем `analysis/question/<старый Q ID>`. Это предложение для
реализации, а не уже существующий runtime. Соответствие сохраняется и для
Question, Answer и Evidence.question_id; нельзя генерировать UUID заново при retry.

Порядок:

1. Принять и зафиксировать C-02 с SHA и review потребителей.
2. Согласовать профиль #55, полный prompt/шаблон #76 и интерфейс адаптера #75.
3. В отдельной ветке #77 добавить преобразование входа worker в данные анализа,
   вызов нового prompt и формирование полного system-analysis.md.
4. Проверить shared schema на реальном выходе и сценарии уточнения; обеспечить
   стабильные ID, ревизию, usage и артефакты. Секреты не передавать в payload.
5. Подключить узел к графу #78 и worker #80 через отдельные задачи подключения.
6. Старые snapshots остаются помечены 0.1. Не переписывать их metadata/version
   и не трактовать как 1.0 без явного преобразования в новый snapshot.

## Согласование с соседними контрактами

- C-01: question_id/required/question и metadata артефакта совпадают по именам
  с contracts/platform.openapi.yaml. HTTP DTO и agent envelope — разные границы;
  content/storage_ref не добавляются в публичный Artifact автоматически.
- C-03: AgentResult — результат одного узла; Run.status, RunEvent и переходы
  не определяются этой задачей. Usage переводится в API Metrics явно.
- C-05: разрешённые stage/profile, лимиты, права каталогов и обязательные файлы
  проверяются по принятому профилю, а не только по JSON Schema.
- A-05/A-06/A-07/A-08: architecture ссылается на неизменяемые артефакты API/data/UI/plan.
  Подробные структуры API/data/UI должны получить собственные согласованные версии.

Откат C-02 до подключения — revert PR через отдельный PR. После подключения
сначала согласовать откат потребителей либо сохранить чтение обеих версий;
удаление схемы, которую использует действующий runtime, несовместимо.
