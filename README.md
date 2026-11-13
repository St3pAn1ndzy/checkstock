# CheckStock — учёт остатков на фулфилмент-складах

Веб-прототип учёта остатков для менеджера маркетплейса. Один магазин, два фулфилмент-склада (ФФ): «Север» и «Юг».
Товар может лежать на нескольких ФФ, быть зарезервированным под FBS, списанным при отправке клиенту (FBS/Ship) или
ехать на склад маркетплейса по FBO. Система показывает остатки по каждому SKU и ФФ (физически, доступно, в резерве FBS),
товар в пути между ФФ или на FBO, а также поддерживает полный жизненный цикл: приход → резерв → отправка → перемещение
→ приёмка → удаление ФФ с сохранением истории.

Тестовое задание на 1 рабочий день. Код и архитектура — результат совместной работы с ИИ (см. «Использование ИИ»).

## Стек

- Python 3.12, FastAPI, SQLAlchemy 2.0 (async) + asyncpg, Pydantic v2 (`pydantic-settings`)
- PostgreSQL 16 (Alpine)
- Docker + docker-compose (uvicorn с `--reload`)
- Poetry (зависимости — только `pyproject.toml`, виртуальное окружение в Docker не создаётся)
- Ruff, Pytest
- Тесты: `pytest`, `pytest-asyncio`, `httpx` (ASGITransport)

## Запуск

```bash
cp .env.example .env
docker compose up -d --build
docker compose exec app python seed.py
```

После запуска:

| URL                          | Описание                         |
|------------------------------|----------------------------------|
| http://localhost:8000/docs   | Swagger UI (ReDoc — `/redoc`)    |
| http://localhost:8000/health | Health-check: `{"status": "ok"}` |

**Важно:** `.env` добавлен в `.gitignore` — в репо только `.env.example` с шаблоном `DATABASE_URL`. **`seed.py`
идемпотентен** — повторный запуск не дублирует данные (upsert по name/sku).

## Сквозной сценарий проверки

Сценарий проходит полный жизненный цикл товара. Можно выполнить через Swagger `/docs` или curl.
Для curl требуется `JQ` (`apt install jq` или `brew install jq`).


### Перед началом

Сценарий предполагает **чистое состояние БД** после seed. Если вы уже запускали шаги раньше или что-то пошло не так — сбросьте:

```bash
docker compose down -v
docker compose up -d --build
docker compose exec app python seed.py
```

`down -v` удаляет volume PostgreSQL. После этого id снова: склады **1** (Север), **2** (Юг); товары **1** (SKU-101), **2** (SKU-102), **3** (SKU-103).

**Выполняйте шаги строго по порядку.** Каждый следующий шаг опирается на состояние предыдущего. Если в ответе на любой POST-запрос есть поле `code` — это ошибка, разберитесь до перехода к следующему шагу.


### Шаг 1. Seed-данные

**Что делаем:** Проверяем, что seed заполнил склады, товары и остатки. **Запрос:** `GET /stock`
**Ожидаемо:** 200, 5 строк stocks, резервов нет → `available = quantity`.

```json
{
  "items": [
    {
      "warehouse_name": "Север",
      "sku": "SKU-101",
      "quantity": 10,
      "reserved_fbs": 0,
      "available": 10
    },
    {
      "warehouse_name": "Север",
      "sku": "SKU-102",
      "quantity": 5,
      "reserved_fbs": 0,
      "available": 5
    },
    {
      "warehouse_name": "Север",
      "sku": "SKU-103",
      "quantity": 0,
      "reserved_fbs": 0,
      "available": 0
    },
    {
      "warehouse_name": "Юг",
      "sku": "SKU-101",
      "quantity": 6,
      "reserved_fbs": 0,
      "available": 6
    },
    {
      "warehouse_name": "Юг",
      "sku": "SKU-103",
      "quantity": 4,
      "reserved_fbs": 0,
      "available": 4
    }
  ],
  "in_transit": []
}
```

### Шаг 2. Приход

**Что делаем:** Добавляем 5 единиц SKU-101 на Север. **Запрос:**

```bash
curl -s -X POST http://localhost:8000/operations/income \
  -H 'Content-Type: application/json' \
  -d '{"warehouse_id": 1, "product_id": 1, "quantity": 5, "idempotency_key": "demo-income-1"}'
```

**Ожидаемо:** 200, `{"type": "INCOME", "status": "COMPLETED", "quantity": 5}`.

`GET /stock` → Север/SKU-101: `quantity=15, available=15`.

### Шаг 3. Повтор прихода с тем же ключом (идемпотентность)

**Что делаем:** Отправляем тот же запрос повторно. **Запрос:** тот же curl (key `demo-income-1`). **Ожидаемо:** 200,
`{"id": ...}` — тот же `id`, что и в шаге 2. `quantity` всё ещё 15 (не 20).

**Если бы ключ не совпадал** — товар бы пришёл дважды и quantity стал бы 20.

### Шаг 4. Резерв FBS

**Что делаем:** Резервируем 3 единицы SKU-101 с Севера под FBS-заказ. **Запрос:**

```bash
RESPONSE=$(curl -s -X POST http://localhost:8000/operations/reserve-fbs \
  -H 'Content-Type: application/json' \
  -d '{"warehouse_id": 1, "product_id": 1, "quantity": 3, "idempotency_key": "demo-reserve-1"}')
echo "$RESPONSE" | jq

MOVEMENT_ID_RESERVE=$(echo "$RESPONSE" | jq -r '.id // empty')
if [ -z "$MOVEMENT_ID_RESERVE" ]; then
  echo "❌ Шаг 4 не выполнен. Смотрите ответ сервера выше. Скорее всего БД не в чистом состоянии — сбросьте через 'docker compose down -v && docker compose up -d --build && docker compose exec app python seed.py'"
  exit 1
fi
echo "✓ movement_id (резерв): $MOVEMENT_ID_RESERVE"
```

**Ожидаемо:** 200, `{"type": "FBS_RESERVE", "status": "PENDING", "quantity": 3}`.

`GET /stock` → Север/SKU-101: `quantity=15, reserved_fbs=3, available=12`.

### Шаг 5. Нехватка на выбранном ФФ (409)

**Что делаем:** Пытаемся зарезервировать 100 единиц SKU-101 с Севера. **Запрос:**

```bash
curl -s -X POST http://localhost:8000/operations/reserve-fbs \
  -H 'Content-Type: application/json' \
  -d '{"warehouse_id": 1, "product_id": 1, "quantity": 100, "idempotency_key": "demo-insufficient-1"}'
```

**Ожидаемо:** 409,
`{"code": "insufficient_stock", "message": "На складе «Север» доступно 12, запрошено 100", "context": {"warehouse_name": "Север", "available": 12, "requested": 100}}`.

`GET /stock` не изменился — rollback сработал.

```bash
# Проверяем через curl, что остатки не испорчены
curl -s http://localhost:8000/stock | jq '.items[] | select(.warehouse_name=="Север" and .sku=="SKU-101")'
```

Ожидаемо: `quantity=15, reserved_fbs=3, available=12` — как было до запроса. Это доказывает, что `rollback()` в `reserve_fbs` сработал.

### Шаг 6. Отправка FBS (Ship)

**Что делаем:** Отправляем зарезервированный товар клиенту.
**Запрос:**

```bash
curl -s -X POST "http://localhost:8000/operations/ship-fbs/$MOVEMENT_ID_RESERVE"
```

**Ожидаемо:** 200, `{"type": "FBS_SHIP", "status": "COMPLETED", "quantity": 3}`.

`GET /stock` → Север/SKU-101: `quantity=12, reserved_fbs=0, available=12`.

`GET /stock` → `in_transit` содержит:

```json
{
  "movement_id": 3,
  "type": "FBS_SHIP",
  "direction": "to_customer",
  "product_id": 1,
  "sku": "SKU-101",
  "product_name": "Органайзер",
  "quantity": 3,
  "from_warehouse_name": "Север",
  "to_warehouse_name": null,
  "created_at": "2026-11-13T12:00:00Z"
}
```

**Ключевое:** FBS_SHIP виден в `in_transit` даже после отправки — потому что доставка клиенту не подтверждается в системе. Это сознательное упрощение: `receive` для FBS_SHIP не предусмотрен. В проде — отдельная отметка «доставлено».

`GET /movements?status=COMPLETED&type=FBS_SHIP` → один movement.

### Шаг 7. Перемещение между ФФ (Transfer)

**Что делаем:** Перемещаем 2 единицы SKU-101 с Севера на Юг. **Запрос:**

```bash
RESPONSE=$(curl -s -X POST http://localhost:8000/operations/transfer \
  -H 'Content-Type: application/json' \
  -d '{"from_warehouse_id": 1, "to_warehouse_id": 2, "product_id": 1, "quantity": 2, "idempotency_key": "demo-transfer-1"}')
echo "$RESPONSE" | jq

MOVEMENT_ID_TRANSFER=$(echo "$RESPONSE" | jq -r '.id // empty')
if [ -z "$MOVEMENT_ID_TRANSFER" ]; then
  echo "❌ Шаг 7 не выполнен. Смотрите ответ сервера выше. Скорее всего БД не в чистом состоянии — сбросьте через 'docker compose down -v && docker compose up -d --build && docker compose exec app python seed.py'"
  exit 1
fi
echo "✓ movement_id (transfer): $MOVEMENT_ID_TRANSFER"
```

**Ожидаемо:** 200, `{"type": "TRANSFER", "status": "PENDING"}`.

`GET /stock`:

- Север/SKU-101: `quantity=10, available=10` (списалось 2)
- Юг/SKU-101: `quantity=6, available=6` (не зачислено — товар в пути)
- `in_transit` содержит **два** элемента: TRANSFER qty=2 (`direction="between_warehouses"`) и FBS_SHIP qty=3 из шага 6 (`direction="to_customer"`).

### Шаг 8. Приёмка (Receive)

**Что делаем:** Завершаем перемещение — товар прибыл на Юг. **Запрос:**

```bash
curl -s -X POST "http://localhost:8000/operations/receive/$MOVEMENT_ID_TRANSFER"
```

**Ожидаемо:** 200, `{"status": "COMPLETED"}`.

`GET /stock`: Север/SKU-101=10, Юг/SKU-101=8. `in_transit` содержит только FBS_SHIP COMPLETED qty=3 (`direction="to_customer"`) — TRANSFER больше нет, он закрыт receive.

### Шаг 9. FBO — отправка на маркетплейс

**Что делаем:** Отправляем 4 единицы SKU-101 с Севера на склад маркетплейса по FBO. **Запрос:**

```bash
RESPONSE=$(curl -s -X POST http://localhost:8000/operations/ship-fbo \
  -H 'Content-Type: application/json' \
  -d '{"warehouse_id": 1, "product_id": 1, "quantity": 4, "idempotency_key": "demo-fbo-1"}')
echo "$RESPONSE" | jq

MOVEMENT_ID_FBO=$(echo "$RESPONSE" | jq -r '.id // empty')
if [ -z "$MOVEMENT_ID_FBO" ]; then
  echo "❌ Шаг 9 не выполнен. Смотрите ответ сервера выше. Скорее всего БД не в чистом состоянии — сбросьте через 'docker compose down -v && docker compose up -d --build && docker compose exec app python seed.py'"
  exit 1
fi
echo "✓ movement_id (FBO): $MOVEMENT_ID_FBO"
```

**Ожидаемо:** 200, `{"type": "FBO_SHIP", "status": "PENDING", "quantity": 4}`.

`GET /stock`: Север/SKU-101=6. `in_transit` содержит **два** элемента: FBO_SHIP qty=4 (`direction="to_marketplace"`) и FBS_SHIP qty=3 из шага 6 (`direction="to_customer"`).

**Приёмка FBO (receive):**

```bash
curl -s -X POST "http://localhost:8000/operations/receive/$MOVEMENT_ID_FBO"
```

**Ожидаемо:** 200. Movement → COMPLETED. Stocks не меняются: товар физически у маркетплейса, в нашей системе он вне учёта. `receive` для `FBO_SHIP` — подтверждение приёмки, а не зачисление на склад.

### Шаг 10. Удаление ФФ (soft delete)

**Что делаем:** Удаляем склад «Север». **Запрос:**

```bash
curl -s -X DELETE http://localhost:8000/warehouses/1
```

**Ожидаемо:** 200, `{"is_deleted": true}`.

`GET /warehouses` — Север не показывается.
`GET /stock` — только Юг.

**Проверка сохранности данных:** stocks и movements Севера не удалены:

```bash
docker compose exec db psql -U postgres -d checkstock -c "SELECT * FROM stocks WHERE warehouse_id=1;"
```

Вернёт 3 строки (SKU-101=6, SKU-102=5, SKU-103=0) — остатки сохранились.
Движения (movements) с `from_warehouse_id=1` или `to_warehouse_id=1` тоже в истории:

```bash
docker compose exec db psql -U postgres -d checkstock -c "SELECT id, type, status, quantity FROM movements WHERE from_warehouse_id=1 OR to_warehouse_id=1;"
```

### Шаг 11. Проверка сходимости остатков

**Инвариант двойного учёта:** сумма всех stocks (включая stocks удалённых складов — они сохраняются) + товар в пути = сумма приходов − сумма списаний.

```bash
docker compose exec db psql -U postgres -d checkstock -c "
  SELECT
    (SELECT COALESCE(SUM(quantity), 0) FROM stocks) AS stocks_total,
    (SELECT COALESCE(SUM(quantity), 0) FROM movements
      WHERE (status='PENDING' AND type IN ('TRANSFER','FBO_SHIP'))
         OR (status='COMPLETED' AND type='FBS_SHIP')
    ) AS in_transit,
    (SELECT COALESCE(SUM(quantity), 0) FROM movements WHERE type='FBO_SHIP' AND status='COMPLETED') AS shipped,
    25 + (SELECT COALESCE(SUM(quantity), 0) FROM movements WHERE type='INCOME' AND status='COMPLETED') AS expected_total;
"
```

**Ожидаемо:** `stocks_total + in_transit + shipped = expected_total`. Если равенство выполняется — ни одна единица не потеряна и не задвоена. Это ключевой тест корректности учёта.

**Ожидаемые числа после прохождения сценария:**

| Показатель | Значение | Почему |
|---|---|---|
| `stocks_total` | 23 | 25 исходно + 5 income − 3 FBS_SHIP − 4 FBO_SHIP = 23 |
| `in_transit` | 3 | FBS_SHIP COMPLETED qty=3 (теперь в in_transit как "to_customer") |
| `shipped` | 4 | FBO_SHIP COMPLETED qty=4 |
| `expected_total` | 30 | 25 исходных + 5 income |

**Проверка:** 23 + 3 + 4 = 30. ✅

Если у вас другие числа — где-то потеряна или задвоена единица товара.

> **Почему `in_transit` не 0?** FBS_SHIP (отправка клиенту) теперь показывается в `in_transit` с `direction="to_customer"`, потому что система не знает, доставлен ли товар. `shipped` считает только FBO_SHIP — товар, ушедший на маркетплейс. Сходимость: `stocks(23) + in_transit(3) + shipped(4) = 30`.

### Промежуточные warehouse_id / product_id

Если сбрасываете БД и перезапускаете seed — id могут измениться. Уточните их:

```bash
curl -s http://localhost:8000/warehouses | jq '.[] | {id, name}'
curl -s http://localhost:8000/products   | jq '.[] | {id, sku}'
```

## API

| Метод  | Путь                                   | Body (обязательные поля)                                                        | Ответ                                  | Ошибки                                 |
|--------|----------------------------------------|---------------------------------------------------------------------------------|----------------------------------------|----------------------------------------|
| POST   | `/warehouses`                          | `{"name": "..."}`                                                               | 201, WarehouseRead                     | 409 дубликат                           |
| GET    | `/warehouses`                          | —                                                                               | list\[WarehouseRead\]                  | —                                      |
| PATCH  | `/warehouses/{id}`                     | `{"name": "..."}`                                                               | 200, WarehouseRead                     | 404, 409                               |
| DELETE | `/warehouses/{id}`                     | —                                                                               | 200, WarehouseRead (`is_deleted=true`) | 404, 409 (есть резервы)                |
| POST   | `/products`                            | `{"sku": "...", "name": "..."}`                                                 | 201, ProductRead                       | 409 дубликат                           |
| GET    | `/products`                            | —                                                                               | list\[ProductRead\]                    | —                                      |
| GET    | `/stock`                               | —                                                                               | StockReportResponse                    | —                                      |
| POST   | `/operations/income`                   | `{warehouse_id, product_id, quantity>0, idempotency_key}`                       | 200, MovementResponse                  | 404, 409 idempotency conflict          |
| POST   | `/operations/reserve-fbs`              | `{warehouse_id, product_id, quantity>0, idempotency_key}`                       | 200, MovementResponse                  | 404, 409 нехватка / conflict           |
| POST   | `/operations/ship-fbs/{movement_id}`   | —                                                                               | 200, MovementResponse                  | 404, 409 invalid status                |
| POST   | `/operations/cancel-fbs/{movement_id}` | —                                                                               | 200, MovementResponse                  | 404, 409 invalid status                |
| POST   | `/operations/transfer`                 | `{from_warehouse_id, to_warehouse_id, product_id, quantity>0, idempotency_key}` | 200, MovementResponse                  | 404, 409 нехватка / conflict / from=to |
| POST   | `/operations/receive/{movement_id}`    | —                                                                               | 200, MovementResponse                  | 404, 409 invalid status                |
| POST   | `/operations/ship-fbo`                 | `{warehouse_id, product_id, quantity>0, idempotency_key}`                       | 200, MovementResponse                  | 404, 409 нехватка / conflict           |
| GET    | `/movements`                           | `?status=&type=&product_id=`                                                    | MovementsListResponse                  | —                                      |

Все ошибки возвращают JSON `{"code": "...", "message": "...", "context": {...}}`.

## Тесты

```bash
ruff check && ruff format --check
docker compose exec app pytest -v
```

**Линтер:** `ruff check` и `ruff format --check` — зелёные.
**Тесты:** 61 тест, время прогона ~10–15 с. Покрытие:

- **Сервисы:** 9 бизнес-операций + 3 CRUD-функции + все edge-кейсы (нехватка, удаление с резервом, конфликт
  идемпотентности, невалидный статус, дубликаты)
- **API:** статус-коды и тела ответов через `httpx.AsyncClient` (ASGITransport), фильтры movements, Swagger docs
- **E2E:** полный сквозной сценарий от seed до удаления склада

## Структура проекта

```
checkstock/
├── app/
│   ├── main.py          # FastAPI приложение, lifespan (create_all), exception handlers
│   ├── database.py      # async engine, SessionLocal, Base, get_db (NullPool)
│   ├── enums.py         # MovementType, MovementStatus (общие для всех слоёв)
│   ├── models.py        # SQLAlchemy-модели: Warehouse, Product, Stock, Movement
│   ├── schemas.py       # Pydantic Request/Response (16 схем)
│   ├── services.py      # Вся бизнес-логика (658 строк) — 9 операций + CRUD
│   ├── exceptions.py    # AppError → NotFoundError / ConflictError / InsufficientStockError / ...
│   └── routers/         # Тонкие HTTP-слои: warehouses, products, stock, operations
├── tests/               # 61 pytest-тест, 12 файлов
├── seed.py              # Идемпотентный seed-скрипт
├── Dockerfile           # python:3.12-slim + Poetry
├── docker-compose.yml   # db (postgres:16-alpine) + app (uvicorn --reload)
├── pyproject.toml       # Зависимости (Poetry), настройки ruff/pytest
├── .env.example         # Шаблон DATABASE_URL
└── README.md
```

## Ключевые решения

### 1. `available` вычисляется, а не хранится

В БД есть только `quantity` (физически на складе) и `reserved_fbs` (зарезервировано под FBS).
`available = quantity - reserved_fbs` считается в момент запроса. Причина: если хранить `available` отдельным полем, при
любой операции нужно синхронно обновлять три поля, и рассинхрон почти неизбежен. Вычисление на лету даёт единственный
источник правды.

### 2. `SELECT ... FOR UPDATE` для защиты от гонок

Все операции, которые меняют `quantity` или `reserved_fbs`, оборачиваются в транзакцию с `SELECT ... FOR UPDATE` на
строку `stocks`. Без этого два параллельных запроса могут прочитать одинаковый остаток и оба его изменить — товар
спишется дважды или уйдёт в минус.

### 3. Порядок блокировок в `transfer` — по возрастанию `warehouse_id`

При перемещении между ФФ нужно заблокировать две строки `stocks`. Если блокировать в порядке `from → to`, то transfer
(A→B) и transfer (B→A), запущенные одновременно, схватят по одной строке и будут ждать вторую друг у друга — deadlock.
Решение: всегда блокировать в порядке `min(warehouse_id)` → `max(warehouse_id)`, независимо от направления.

### 4. Идемпотентность через `idempotency_key` + UNIQUE в БД

Клиент генерирует UUID и передаёт его в теле запроса. Перед выполнением операция ищет движение с таким ключом; если
найдено — возвращает существующий результат, не выполняя операцию повторно. `UNIQUE` constraint на `idempotency_key` в
БД — страховка от гонки: даже если два параллельных запроса прошли проверку, второй INSERT упадёт. Дополнительно
проверяется, что **тип операции и параметры** совпадают: если тот же ключ пришёл для `reserve_fbs` вместо `income` или с
другой `quantity` — это `IdempotencyConflictError` (409), а не тихий возврат старого результата.

### 5. Soft delete складов

`DELETE /warehouses/{id}` ставит `is_deleted=True`, но не удаляет запись физически. Stocks и movements не трогаются. Это
требование ТЗ: «при удалении сохраните остатки и историю». Плюс: если склад удалён, но на нём были FBS-резервы,
`delete_warehouse` вернёт 409 — нужно сначала отменить или отправить резервы, иначе они зависнут в PENDING навсегда.

### 6. `create_all()` вместо Alembic

Для прототипа на 1 день — осознанный trade-off. Alembic нужен, когда схема стабилизировалась и есть живые данные,
которые нельзя терять при изменении. Сейчас — `Base.metadata.create_all()` в lifespan. В проде — Alembic обязателен:
изменения схемы без миграции = потеря данных.

### 7. Мутация `Movement.type` при `ship_fbs` — trade-off

При отправке FBS-заказа движение `FBS_RESERVE` превращается в `FBS_SHIP` (одна запись вместо двух). Плюс: проще, меньше
строк в БД. Минус: Movement не immutable — теряется информация «это был резерв». В проде — две связанные записи через
`parent_id`: резерв остаётся с `status=COMPLETED`, а `FBS_SHIP` — новая запись. Для прототипа осознанно упрощено.

## Известные ограничения

Честный список того, что **знаю и осознанно не сделал** — либо из-за тайминга, либо как trade-off прототипа:

1. **Гонка на `idempotency_key` при истинно параллельных запросах.** Если два запроса с одним ключом приходят
   **одновременно** (не последовательно), оба могут пройти `SELECT` и один упадёт с `IntegrityError` → 500 вместо 200. В
   UI-сценарии (два клика, retry после таймаута) проблемы нет: запросы последовательные. Фикс —
   `try/except IntegrityError` с повторным `_find_by_idempotency_key`. Не реализовано из-за тайминга.
2. **Гонка в `_get_or_create_stock`** — аналогично, при одновременном создании stock для новой пары (warehouse,
   product). Фикс тот же.
3. **`services.py` ~658 строк** — для прототипа оставлено в одном файле с логической группировкой (helpers → CRUD →
   operations). При росте — разбить на пакет `services/` по доменам.
4. **`get_stock_report` делает два отдельных SELECT'а** (stocks + in_transit) — это две разные структуры данных, объединять в UNION было бы менее читаемо. N+1 нет: relationships не используются, всё в одном запросе на структуру. Для прототипа приемлемо.
5. **Разный формат ошибок:** `AppError` → `{code, message, context}`, Pydantic 422 → стандартный FastAPI
   `{detail: [...]}`. Не критично для прототипа, но для клиентской интеграции — стоит унифицировать.
6. **Нет auth, ролей, audit log** — вне скоупа тестового.
7. **FBS_SHIP навсегда в `in_transit`.** Система не знает, доставлен ли товар клиенту — нет отметки «доставлено». Поэтому все FBS_SHIP COMPLETED висят в `in_transit` как «к клиенту» (`direction="to_customer"`). В проде — отдельный статус DELIVERED и фильтр «только за последние N дней».

## Использование ИИ

Использовал (разрешено ТЗ). Где ускорил и что проверил:

**Ускорил:**

- Boilerplate: Dockerfile, docker-compose, `pyproject.toml`, начальные Pydantic-схемы.
- Шаблонные SQL-запросы (`select(...).where(...)`).
- Генерация 61 тестов по моему описанию сценариев.

**Что проверил вручную и правил:**

- Архитектура: слои, порядок блокировок в `transfer`, семантика `FBO_SHIP` vs `FBS_RESERVE`.
- Каждый тест прогнан локально (`docker-compose exec app pytest -v`), все 61 зелёные.
- Сквозной сценарий (11 шагов в README) прогнан руками через Swagger — остатки сходятся на каждом шаге.
- Аудит проводился в отдельной сессии ИИ-ревьюера — чистый контекст, только отчёт без правок кода. Найденные дыры
  исправил, добавил 7 тестов на идемпотентность и удаление склада с резервом. Аудит не писал код —
  это тоже часть работы с ИИ: поставить задачу на ревью, отфильтровать полезное, применить.