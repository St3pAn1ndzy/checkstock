"""Тесты для операции отправки FBS-заказа (ship_fbs)."""

import pytest
from sqlalchemy import select

from app.enums import MovementStatus, MovementType
from app.exceptions import InvalidStatusError
from app.models import Stock
from app.schemas import ReserveFbsRequest
from app.services import cancel_fbs, reserve_fbs, ship_fbs


class TestShipFbs:
    """Отправка FBS-заказа."""

    async def test_success(self, session, clean_db):
        """Ship_fbs переводит FBS_RESERVE PENDING → FBS_SHIP COMPLETED, списывает quantity."""
        req = ReserveFbsRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=3,
            idempotency_key="test-ship-success",
        )
        mv = await reserve_fbs(session, req)
        mv = await ship_fbs(session, mv.id)

        assert mv.type == MovementType.FBS_SHIP
        assert mv.status == MovementStatus.COMPLETED
        assert mv.completed_at is not None

        stock = await session.scalar(
            select(Stock).where(
                Stock.warehouse_id == clean_db["sever_id"],
                Stock.product_id == clean_db["sku101_id"],
            )
        )
        assert stock.quantity == 7  # 10 - 3
        assert stock.reserved_fbs == 0  # 3 - 3

    async def test_idempotent(self, session, clean_db):
        """Повторный ship_fbs с тем же movement_id → повторный спрос не происходит."""
        req = ReserveFbsRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=3,
            idempotency_key="test-ship-idempotent",
        )
        mv = await reserve_fbs(session, req)
        mv1 = await ship_fbs(session, mv.id)
        mv2 = await ship_fbs(session, mv.id)

        assert mv1.id == mv2.id
        assert mv2.status == MovementStatus.COMPLETED

        stock = await session.scalar(
            select(Stock).where(
                Stock.warehouse_id == clean_db["sever_id"],
                Stock.product_id == clean_db["sku101_id"],
            )
        )
        assert stock.quantity == 7  # не 4

    async def test_cancelled_raises(self, session, clean_db):
        """Ship_fbs на отменённый резерв → InvalidStatusError."""
        req = ReserveFbsRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=3,
            idempotency_key="test-ship-cancelled",
        )
        mv = await reserve_fbs(session, req)
        mv = await cancel_fbs(session, mv.id)

        with pytest.raises(InvalidStatusError) as exc_info:
            await ship_fbs(session, mv.id)
        assert "Нельзя отправить отменённый резерв" in str(exc_info.value)
