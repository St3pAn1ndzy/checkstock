"""API-тесты: проверяют, что HTTP-слой корректно проксирует в сервисы.

Не дублируют глубокую логику сервисных тестов.
Проверяют статус-коды, структуру ответов, базовую маршрутизацию.
"""

from httpx import AsyncClient

from app.models import Stock


class TestApi:
    """Набор API-тестов для всех эндпоинтов."""

    async def test_create_warehouse_201(self, client: AsyncClient, clean_db):
        """POST /warehouses → 201, склад создаётся и виден в списке."""
        resp = await client.post("/warehouses", json={"name": "Запад"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Запад"
        assert data["is_deleted"] is False

        resp = await client.get("/warehouses")
        assert resp.status_code == 200
        names = [w["name"] for w in resp.json()]
        assert "Запад" in names
        assert len(names) == 3  # Север, Юг, Запад

    async def test_create_warehouse_duplicate_409(self, client: AsyncClient, clean_db):
        """POST /warehouses с существующим именем → 409."""
        resp = await client.post("/warehouses", json={"name": "Север"})
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "conflict"

    async def test_rename_warehouse_200(self, client: AsyncClient, clean_db):
        """PATCH /warehouses/{id} → имя обновляется."""
        sever_id = clean_db["sever_id"]
        resp = await client.patch(f"/warehouses/{sever_id}", json={"name": "Северный"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Северный"

        resp = await client.get("/warehouses")
        names = [w["name"] for w in resp.json()]
        assert "Северный" in names
        assert "Север" not in names

    async def test_delete_warehouse_200(self, client: AsyncClient, clean_db, session):
        """DELETE /warehouses/{id} → soft delete, stocks сохраняются."""
        sever_id = clean_db["sever_id"]
        resp = await client.delete(f"/warehouses/{sever_id}")
        assert resp.status_code == 200
        assert resp.json()["is_deleted"] is True

        # Склад скрыт из списка
        resp = await client.get("/warehouses")
        ids = [w["id"] for w in resp.json()]
        assert sever_id not in ids

        # Остатки в БД нетронуты
        from sqlalchemy import select

        stmt = select(Stock).where(Stock.warehouse_id == sever_id)
        stocks = (await session.execute(stmt)).scalars().all()
        assert len(stocks) > 0

    async def test_get_stock_report_200(self, client: AsyncClient):
        """GET /stock → 5 items, пустой in_transit."""
        resp = await client.get("/stock")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 5
        assert body["in_transit"] == []

    async def test_income_via_api(self, client: AsyncClient, clean_db):
        """POST /operations/income → quantity увеличивается."""
        resp = await client.post(
            "/operations/income",
            json={
                "warehouse_id": clean_db["sever_id"],
                "product_id": clean_db["sku101_id"],
                "quantity": 5,
                "idempotency_key": "api-test-income-1",
            },
        )
        assert resp.status_code == 200
        mv = resp.json()
        assert mv["type"] == "INCOME"
        assert mv["status"] == "COMPLETED"

        # Проверяем через отчёт
        resp = await client.get("/stock")
        items = resp.json()["items"]
        sever_sku101 = next(
            i
            for i in items
            if i["warehouse_id"] == clean_db["sever_id"]
            and i["product_id"] == clean_db["sku101_id"]
        )
        assert sever_sku101["quantity"] == 15  # 10 + 5

    async def test_reserve_insufficient_409(self, client: AsyncClient, clean_db):
        """POST /operations/reserve-fbs с qty > available → 409."""
        resp = await client.post(
            "/operations/reserve-fbs",
            json={
                "warehouse_id": clean_db["sever_id"],
                "product_id": clean_db["sku101_id"],
                "quantity": 100,
                "idempotency_key": "api-test-reserve-1",
            },
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "insufficient_stock"
        assert "Север" in body["message"]
        assert "10" in body["message"]

    async def test_idempotency_via_api(self, client: AsyncClient, clean_db):
        """Повторный POST с тем же idempotency_key не меняет состояние."""
        payload = {
            "warehouse_id": clean_db["sever_id"],
            "product_id": clean_db["sku101_id"],
            "quantity": 5,
            "idempotency_key": "api-test-dup-income",
        }
        resp1 = await client.post("/operations/income", json=payload)
        resp2 = await client.post("/operations/income", json=payload)
        assert resp1.status_code == 200
        assert resp2.status_code == 200

        # quantity = 15, не 20
        resp = await client.get("/stock")
        items = resp.json()["items"]
        sever_sku101 = next(
            i
            for i in items
            if i["warehouse_id"] == clean_db["sever_id"]
            and i["product_id"] == clean_db["sku101_id"]
        )
        assert sever_sku101["quantity"] == 15

        # Только 1 COMPLETED INCOME
        resp = await client.get(
            "/movements", params={"status": "COMPLETED", "type": "INCOME"}
        )
        assert resp.json()["total"] == 1

    async def test_movements_filter(self, client: AsyncClient, clean_db):
        """Фильтры /movements работают корректно."""
        sever_id = clean_db["sever_id"]
        sku101_id = clean_db["sku101_id"]

        # Приход
        await client.post(
            "/operations/income",
            json={
                "warehouse_id": sever_id,
                "product_id": sku101_id,
                "quantity": 1,
                "idempotency_key": "api-test-mv-income",
            },
        )
        # Резерв
        await client.post(
            "/operations/reserve-fbs",
            json={
                "warehouse_id": sever_id,
                "product_id": sku101_id,
                "quantity": 2,
                "idempotency_key": "api-test-mv-reserve",
            },
        )
        # Получаем id резерва, чтобы отправить
        resp = await client.get(
            "/movements", params={"status": "PENDING", "type": "FBS_RESERVE"}
        )
        reserve_mv = resp.json()["items"][0]

        # Отправка
        await client.post(f"/operations/ship-fbs/{reserve_mv['id']}")

        # Фильтры
        resp = await client.get("/movements", params={"status": "PENDING"})
        assert resp.json()["total"] == 0

        resp = await client.get("/movements", params={"type": "INCOME"})
        assert resp.json()["total"] == 1

        resp = await client.get("/movements", params={"type": "FBS_SHIP"})
        assert resp.json()["total"] == 1

        resp = await client.get("/movements", params={"product_id": sku101_id})
        assert resp.json()["total"] == 2  # INCOME + FBS_SHIP

    async def test_swagger_docs_available(self, client: AsyncClient):
        """GET /openapi.json → 200, содержит все ожидаемые пути."""
        resp = await client.get("/openapi.json")
        assert resp.status_code == 200
        paths = resp.json()["paths"]
        expected = [
            "/warehouses",
            "/warehouses/{warehouse_id}",
            "/products",
            "/stock",
            "/operations/income",
            "/operations/reserve-fbs",
            "/operations/ship-fbs/{movement_id}",
            "/operations/cancel-fbs/{movement_id}",
            "/operations/transfer",
            "/operations/receive/{movement_id}",
            "/operations/ship-fbo",
            "/movements",
            "/health",
        ]
        for path in expected:
            assert path in paths, f"Ожидаемый путь {path} отсутствует"
