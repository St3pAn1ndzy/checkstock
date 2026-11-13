# AGENTS.md — CheckStock

Правила для ИИ-агента, работающего с этим репозиторием. Читать перед любым действием.

## Что это за проект

Тестовое задание на 1 рабочий день. Веб-прототип учёта остатков для менеджера маркетплейса. Один магазин, два
фулфилмент-склада (ФФ): «Север» и «Юг». Товар может лежать на нескольких ФФ, быть зарезервированным под FBS или ехать на
склад маркетплейса по FBO.

**Цель:** показать рабочее ядро учёта и обработку edge-кейсов (нехватка, удаление ФФ с остатком, повторное
подтверждение). НЕ продакшн-система.

## Стек (не менять)

- Python 3.12, FastAPI, SQLAlchemy 2.0 **async** + asyncpg, Pydantic v2 (`pydantic-settings`), PostgreSQL 16
- Docker + docker-compose, uvicorn на порту 8000
- Без Alembic. Без auth. Без React/Vue. Без Celery/Redis. Без CI/CD.
- Управление зависимостями: **Poetry** (pyproject.toml + poetry.lock). Виртуальное окружение в Docker не создаётся (POETRY_VIRTUALENVS_CREATE=false).

## Архитектура (строго)

Горизонтальный срез, слоистая архитектура. Файлы по слоям, не по фичам.

```
app/
├── main.py         # FastAPI app, lifespan, exception handlers, include routers
├── database.py     # async engine, SessionLocal, Base, get_db
├── models.py       # SQLAlchemy-модели, НИКАКОЙ логики
├── schemas.py      # Pydantic Request/Response
├── services.py     # ВСЯ бизнес-логика, транзакции, FOR UPDATE, идемпотентность
├── exceptions.py   # кастомные исключения
└── routers/
    ├── warehouses.py
    ├── stock.py
    └── operations.py
```

**Правила слоёв:**

- Роутер НЕ содержит SQL. Принимает Pydantic-схему → вызывает сервис → возвращает ответ.
- Сервис НЕ знает про HTTP-статусы. Бросает кастомное исключение → хендлер в `main.py` мапит в HTTP.
- Модели — только структура таблиц. Никаких `@property` с логикой, никаких запросов внутри.
- Вся логика — в `services.py`. Файл может быть 300–400 строк, это норм.

## Модели БД (кратко)

- **Warehouse:** id, name (unique), is_deleted (default False), created_at
- **Product:** id, sku (unique), name
- **Stock:** id, warehouse_id FK, product_id FK, quantity (default 0), reserved_fbs (default 0). UNIQUE (warehouse_id,
  product_id)
- **Movement:** id, type (enum), product_id FK, quantity, from_warehouse_id FK nullable, to_warehouse_id FK nullable,
  status (enum), idempotency_key (unique nullable), created_at, completed_at nullable

**Enums:**

- `MovementType`: INCOME, TRANSFER, FBS_RESERVE, FBS_CANCEL, FBS_SHIP, FBO_SHIP, RECEIVE
- `MovementStatus`: PENDING, COMPLETED, CANCELLED

## Ключевые архитектурные решения (НЕ нарушать)

1. **`available` НЕ хранится в БД.** Вычисляется как `quantity - reserved_fbs` в момент запроса.
2. **`SELECT ... FOR UPDATE`** в любой операции, которая меняет `quantity` или `reserved_fbs`. Для transfer — блокировка
   обеих строк stocks в порядке возрастания `warehouse_id` (защита от deadlock).
3. **Идемпотентность:** POST income / transfer / reserve-fbs / ship-fbo принимают `idempotency_key` (UUID от клиента).
   Если ключ уже есть в movements — вернуть существующий результат, НЕ выполнять операцию повторно. Для ship-fbs /
   cancel-fbs / receive идемпотентность по статусу movement (COMPLETED/CANCELLED → вернуть 200 без изменений).
4. **Soft delete складов:** `is_deleted = True`. Stocks и movements НЕ трогаем. GET /warehouses фильтрует
   `is_deleted=False`.
5. **Транзакция = одна операция.** Ошибка → rollback, ничего не применилось.
6. **`Base.metadata.create_all()` в lifespan**, не Alembic. Это осознанный trade-off для прототипа.

## Бизнес-логика (сводка)

- `income` — увеличить quantity, записать Movement (INCOME, COMPLETED)
- `reserve_fbs` — проверить `available >= qty`, увеличить reserved_fbs, Movement (FBS_RESERVE, PENDING)
- `ship_fbs` — уменьшить quantity и reserved_fbs, Movement → FBS_SHIP, COMPLETED
- `cancel_fbs` — уменьшить reserved_fbs, Movement → CANCELLED
- `transfer` — FOR UPDATE обе строки, уменьшить quantity на from, Movement (TRANSFER, PENDING)
- `receive` — увеличить quantity на to, Movement → COMPLETED
- `ship_fbo` — проверить `available >= qty`, уменьшить quantity, Movement (FBO_SHIP, PENDING). Приёмка через receive ()
- `delete_warehouse` — `is_deleted = True`
- `get_stock_report` — список stocks с available + список PENDING movements («в пути»)

**Нехватка на выбранном ФФ** (при достаточном общем остатке) → `InsufficientStockError` → 409 с сообщением «На складе
{name} доступно {available}, запрошено {requested}».

## Эндпоинты

```
POST   /warehouses
GET    /warehouses
DELETE /warehouses/{id}
POST   /products
GET    /products
GET    /stock
POST   /operations/income
POST   /operations/reserve-fbs
POST   /operations/ship-fbs/{movement_id}
POST   /operations/cancel-fbs/{movement_id}
POST   /operations/transfer
POST   /operations/receive/{movement_id}
POST   /operations/ship-fbo
GET    /movements?status=&type=&product_id=
```

Коды: 404 not found, 409 бизнес-конфликт (нехватка / невалидный статус), 422 Pydantic (авто).

## Чего НЕ делать

- ❌ Alembic, миграции
- ❌ Аутентификация, JWT, роли
- ❌ Красивый UI — Swagger /docs достаточно
- ❌ pytest (только если пользователь явно попросит после основной сдачи)
- ❌ CI/CD, nginx, gunicorn, Sentry, метрики
- ❌ Redis, Celery, фоновые задачи
- ❌ Паттерны ради паттернов: Repository, Unit of Work, абстрактные базовые классы
- ❌ Разбивать `models.py` / `schemas.py` / `services.py` по папкам
- ❌ Добавлять фичи, которых нет в этом файле
- ❌ Комментарии-очевидности (`# increment quantity`). Только там, где неочевидно «почему», не «что»
- ❌ Не создавать `requirements.txt` вручную. Все зависимости — только через `pyproject.toml`.

## Стиль кода

- Полная типизация, `async def` везде, где есть I/O
- Docstring у каждого сервиса — 1–2 строки, зачем операция (не «что делает», а «зачем нужна»)
- Config через `pydantic-settings`, `DATABASE_URL` из env, `.env.example` в репо
- `@asynccontextmanager lifespan` в `main.py` для create_all
- Ответы и имена — на английском. Общение с пользователем — на русском

## Процесс работы

1. Работать **поэтапно**. После каждого этапа — стоп, показать summary, ждать «продолжай».
2. Не генерировать сразу много файлов. Один этап = один смысловой кусок.
3. Перед изменением существующего файла — показать diff или объяснить, что меняется и зачем.
4. Если видишь потенциальный баг в логике учёта остатков — предупредить сразу, не дожидаясь конца этапа.
5. Если пользователь просит что-то, противоречащее этому файлу — спросить «точно? это нарушает правило X», но не
   спорить.

## Этапы

1. **Каркас:** pyproject.toml (+ package-mode = false), poetry.lock, Dockerfile (с Poetry), docker-compose, .env.example, database.py, main.py с `/health` + lifespan
2. **models.py + seed.py**
3. **schemas.py + exceptions.py + хендлеры**
4. **services.py** — по одной функции с проверкой после каждой
5. **routers/*.py**
6. **README.md**
7. (опционально) pytest — по запросу

## README (обязательные разделы)

1. Что это, стек
2. Запуск: `cp .env.example .env && docker-compose up -d && docker-compose exec app python seed.py`
3. Эндпоинты + curl-примеры
4. Сквозной сценарий проверки: приход → резерв → нехватка (409) → отправка FBS → перемещение → приёмка → FBO → удаление
   ФФ → повторный приход с тем же ключом (не списался)
5. Ключевые решения (available вычисляется, FOR UPDATE, idempotency_key, soft delete, create_all vs Alembic)
6. Что бы сделал дальше (Alembic, pytest, auth, UI, интеграция с WB API)
7. Использование ИИ: где ускорил, что проверил руками

## Seed-данные

Склады: «Север», «Юг».
Товары: SKU-101 «Органайзер», SKU-102 «Контейнер», SKU-103 «Корзина».
Stocks: (Север, SKU-101, 10), (Юг, SKU-101, 6), (Север, SKU-102, 5), (Север, SKU-103, 0), (Юг, SKU-103, 4).
`seed.py` идемпотентный: если склады уже есть — ничего не делает.