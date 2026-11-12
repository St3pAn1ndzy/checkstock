"""Тесты для операции отправки FBS-заказа (ship_fbs)."""

import pytest
from sqlalchemy import select

from app.database import async_session_factory
from app.enums import MovementStatus, MovementType
from app.exceptions import InvalidStatusError
from app.models import Stock
from app.services import ship_fbs


class TestShipFbs:
    """Отправка FBS-заказа клиенту."""

    async def test_success(self, session, clean_db, reserved_movement):
        """Ship_fbs переводит PENDING → COMPLETED, списывает quantity и reserved."""
        # reserved_movement = reserve 3 на quantity=10 → reserved=3, available=7
        mv = await ship_fbs(session, reserved_movement)

        assert mv.type == MovementType.FBS_SHIP
        assert mv.status == MovementStatus.COMPLETED
        assert mv.completed_at is not None

        async with async_session_factory() as fresh:
            stock = await fresh.scalar(
                select(Stock).where(
                    Stock.warehouse_id == clean_db["sever_id"],
                    Stock.product_id == clean_db["sku101_id"],
                )
            )
        assert stock.quantity == 7  # 10 - 3
        assert stock.reserved_fbs == 0  # 3 - 3

    async def test_idempotent(self, session, clean_db, shipped_movement):
        """Повторный ship_fbs не меняет данные."""
        # shipped_movement = reserve 3 + ship → quantity=7, reserved=0
        mv = await ship_fbs(session, shipped_movement)

        assert mv.type == MovementType.FBS_SHIP
        assert mv.status == MovementStatus.COMPLETED

        async with async_session_factory() as fresh:
            stock = await fresh.scalar(
                select(Stock).where(
                    Stock.warehouse_id == clean_db["sever_id"],
                    Stock.product_id == clean_db["sku101_id"],
                )
            )
        assert stock.quantity == 7
        assert stock.reserved_fbs == 0

    async def test_cancelled_raises(self, session, clean_db):
        """Ship_fbs на отменённый резерв → InvalidStatusError."""
        from app.schemas import ReserveFbsRequest
        from app.services import cancel_fbs, reserve_fbs

        req = ReserveFbsRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=2,
            idempotency_key="test-ship-cancelled",
        )
        mv = await reserve_fbs(session, req)
        await cancel_fbs(session, mv.id)

        with pytest.raises(InvalidStatusError) as exc_info:
            await ship_fbs(session, mv.id)
        assert "отменённый" in str(exc_info.value)

        async with async_session_factory() as fresh:
            stock = await fresh.scalar(
                select(Stock).where(
                    Stock.warehouse_id == clean_db["sever_id"],
                    Stock.product_id == clean_db["sku101_id"],
                )
            )
        assert stock.quantity == 10  # не изменилось
        assert stock.reserved_fbs == 0
