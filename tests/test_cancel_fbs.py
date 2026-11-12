"""Тесты для операции отмены FBS-резерва (cancel_fbs)."""

import pytest
from sqlalchemy import select

from app.database import async_session_factory
from app.enums import MovementStatus
from app.exceptions import InvalidStatusError
from app.models import Stock
from app.services import cancel_fbs


class TestCancelFbs:
    """Отмена FBS-резерва."""

    async def test_success(self, session, clean_db, reserved_movement):
        """Cancel_fbs переводит PENDING → CANCELLED, возвращает reserved_fbs."""
        mv = await cancel_fbs(session, reserved_movement)

        assert mv.status == MovementStatus.CANCELLED
        assert mv.completed_at is not None

        async with async_session_factory() as fresh:
            stock = await fresh.scalar(
                select(Stock).where(
                    Stock.warehouse_id == clean_db["sever_id"],
                    Stock.product_id == clean_db["sku101_id"],
                )
            )
        assert stock.quantity == 10  # не изменился
        assert stock.reserved_fbs == 0  # 3 → 0

    async def test_idempotent(self, session, clean_db, reserved_movement):
        """Повторный cancel_fbs не меняет данные."""
        mv1 = await cancel_fbs(session, reserved_movement)
        mv2 = await cancel_fbs(session, reserved_movement)

        assert mv1.id == mv2.id

        async with async_session_factory() as fresh:
            stock = await fresh.scalar(
                select(Stock).where(
                    Stock.warehouse_id == clean_db["sever_id"],
                    Stock.product_id == clean_db["sku101_id"],
                )
            )
        assert stock.reserved_fbs == 0

    async def test_completed_raises(self, session, clean_db, shipped_movement):
        """Cancel_fbs на COMPLETED (FBS_SHIP) → InvalidStatusError."""
        # shipped_movement: reserve 3 → ship → quantity=7, reserved=0
        # но shipped_movement уже не FBS_RESERVE, а FBS_SHIP
        with pytest.raises(InvalidStatusError):
            await cancel_fbs(session, shipped_movement)

        async with async_session_factory() as fresh:
            stock = await fresh.scalar(
                select(Stock).where(
                    Stock.warehouse_id == clean_db["sever_id"],
                    Stock.product_id == clean_db["sku101_id"],
                )
            )
        assert stock.quantity == 7
        assert stock.reserved_fbs == 0
