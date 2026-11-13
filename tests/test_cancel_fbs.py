"""Тесты для операции отмены FBS-резерва (cancel_fbs)."""

import pytest
from sqlalchemy import select

from app.enums import MovementStatus
from app.exceptions import InvalidStatusError
from app.models import Stock
from app.schemas import ReserveFbsRequest
from app.services import cancel_fbs, reserve_fbs, ship_fbs


class TestCancelFbs:
    """Отмена FBS-резерва."""

    async def test_success(self, session, clean_db):
        """Cancel_fbs переводит PENDING → CANCELLED, возвращает reserved_fbs."""
        req = ReserveFbsRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=3,
            idempotency_key="test-cancel-success",
        )
        mv = await reserve_fbs(session, req)
        mv = await cancel_fbs(session, mv.id)

        assert mv.status == MovementStatus.CANCELLED
        assert mv.completed_at is not None

        stock = await session.scalar(
            select(Stock).where(
                Stock.warehouse_id == clean_db["sever_id"],
                Stock.product_id == clean_db["sku101_id"],
            )
        )
        assert stock.quantity == 10  # не изменился
        assert stock.reserved_fbs == 0  # 3 → 0

    async def test_idempotent(self, session, clean_db):
        """Повторный cancel_fbs с тем же movement_id → CANCELLED (не ошибка)."""
        req = ReserveFbsRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=2,
            idempotency_key="test-cancel-idempotent",
        )
        mv = await reserve_fbs(session, req)
        mv1 = await cancel_fbs(session, mv.id)
        mv2 = await cancel_fbs(session, mv.id)

        assert mv1.id == mv2.id
        assert mv2.status == MovementStatus.CANCELLED

    async def test_completed_raises(self, session, clean_db):
        """Cancel_fbs на FBS_SHIP (COMPLETED) → InvalidStatusError."""
        req = ReserveFbsRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=3,
            idempotency_key="test-cancel-completed",
        )
        mv = await reserve_fbs(session, req)
        mv_ship = await ship_fbs(session, mv.id)  # FBS_SHIP, COMPLETED

        with pytest.raises(InvalidStatusError) as exc_info:
            await cancel_fbs(session, mv_ship.id)
        assert "FBS_SHIP" in str(exc_info.value)
