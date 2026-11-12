"""Тесты для операции FBO-отгрузки (ship_fbo)."""

import pytest
from sqlalchemy import select, func

from app.enums import MovementStatus, MovementType
from app.exceptions import InsufficientStockError
from app.models import Movement, Stock
from app.schemas import ReserveFbsRequest, ShipFboRequest
from app.services import reserve_fbs, ship_fbo


class TestShipFbo:
    """Отправка на маркетплейс по FBO."""

    async def test_ship_fbo_success(self, session, clean_db):
        """Ship_fbo 4 ед. с Севера (SKU-101): quantity 10→6, reserved_fbs не меняется, Movement(FBO_SHIP, PENDING)."""
        req = ShipFboRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=4,
            idempotency_key="test-fbo-success",
        )
        mv = await ship_fbo(session, req)

        assert mv.type == MovementType.FBO_SHIP
        assert mv.status == MovementStatus.PENDING
        assert mv.from_warehouse_id == clean_db["sever_id"]
        assert mv.to_warehouse_id is None
        assert mv.quantity == 4

        stock = await session.scalar(
            select(Stock).where(
                Stock.warehouse_id == clean_db["sever_id"],
                Stock.product_id == clean_db["sku101_id"],
            )
        )
        assert stock.quantity == 6     # 10 - 4
        assert stock.reserved_fbs == 0  # не изменился

    async def test_ship_fbo_insufficient(self, session, clean_db):
        """Ship_fbo 100 ед. → InsufficientStockError, ничего не изменилось."""
        req = ShipFboRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=100,
            idempotency_key="test-fbo-insufficient",
        )
        with pytest.raises(InsufficientStockError) as exc_info:
            await ship_fbo(session, req)

        assert "Север" in exc_info.value.message
        assert "10" in exc_info.value.message
        assert "100" in exc_info.value.message

        stock = await session.scalar(
            select(Stock).where(
                Stock.warehouse_id == clean_db["sever_id"],
                Stock.product_id == clean_db["sku101_id"],
            )
        )
        assert stock.quantity == 10
        assert stock.reserved_fbs == 0

    async def test_ship_fbo_respects_reserved(self, session, clean_db):
        """После reserve_fbs qty=8 (available=2) ship_fbo 3 → InsufficientStockError."""
        req_res = ReserveFbsRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=8,
            idempotency_key="test-fbo-reserve",
        )
        await reserve_fbs(session, req_res)

        req_fbo = ShipFboRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=3,
            idempotency_key="test-fbo-reserved-insufficient",
        )
        with pytest.raises(InsufficientStockError) as exc_info:
            await ship_fbo(session, req_fbo)

        assert "2" in exc_info.value.message  # available = 10 - 8 = 2

        stock = await session.scalar(
            select(Stock).where(
                Stock.warehouse_id == clean_db["sever_id"],
                Stock.product_id == clean_db["sku101_id"],
            )
        )
        assert stock.quantity == 10
        assert stock.reserved_fbs == 8

    async def test_ship_fbo_idempotent(self, session, clean_db):
        """Повторный ship_fbo с тем же ключом — quantity=6 (не 2), movements=1."""
        req = ShipFboRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=4,
            idempotency_key="test-fbo-idempotent",
        )
        mv1 = await ship_fbo(session, req)
        mv2 = await ship_fbo(session, req)

        assert mv1.id == mv2.id

        stock = await session.scalar(
            select(Stock).where(
                Stock.warehouse_id == clean_db["sever_id"],
                Stock.product_id == clean_db["sku101_id"],
            )
        )
        assert stock.quantity == 6  # 10 - 4, не 10 - 4 - 4

        cnt = await session.scalar(
            select(func.count(Movement.id)).where(
                Movement.idempotency_key == "test-fbo-idempotent"
            )
        )
        assert cnt == 1
