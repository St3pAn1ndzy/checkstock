"""Тесты для операции FBS-резерва (reserve_fbs)."""

import pydantic
import pytest
from sqlalchemy import select

from app.enums import MovementStatus, MovementType
from app.exceptions import InsufficientStockError
from app.models import Stock
from app.schemas import ReserveFbsRequest
from app.services import reserve_fbs


class TestReserveFbs:
    """Резерв под FBS."""

    async def test_success(self, session, clean_db):
        """Reserve 3 ед. → reserved_fbs с 0 до 3, available с 10 до 7."""
        req = ReserveFbsRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=3,
            idempotency_key="test-reserve-1",
        )
        mv = await reserve_fbs(session, req)

        assert mv.type == MovementType.FBS_RESERVE
        assert mv.status == MovementStatus.PENDING

        stock = await session.scalar(
            select(Stock).where(
                Stock.warehouse_id == clean_db["sever_id"],
                Stock.product_id == clean_db["sku101_id"],
            )
        )
        assert stock.quantity == 10  # не изменился
        assert stock.reserved_fbs == 3

    async def test_insufficient(self, session, clean_db):
        """Reserve 100 ед. → InsufficientStockError."""
        req = ReserveFbsRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=100,
            idempotency_key="test-reserve-insufficient",
        )
        with pytest.raises(InsufficientStockError) as exc_info:
            await reserve_fbs(session, req)

        assert "Север" in exc_info.value.message
        assert "10" in exc_info.value.message  # available
        assert "100" in exc_info.value.message

        stock = await session.scalar(
            select(Stock).where(
                Stock.warehouse_id == clean_db["sever_id"],
                Stock.product_id == clean_db["sku101_id"],
            )
        )
        assert stock.reserved_fbs == 0  # rollback

    async def test_idempotent(self, session, clean_db):
        """Повторный reserve с тем же idempotency_key — reserved_fbs не увеличивается дважды."""
        req = ReserveFbsRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=3,
            idempotency_key="test-reserve-idempotent",
        )
        mv1 = await reserve_fbs(session, req)
        mv2 = await reserve_fbs(session, req)

        assert mv1.id == mv2.id

        stock = await session.scalar(
            select(Stock).where(
                Stock.warehouse_id == clean_db["sever_id"],
                Stock.product_id == clean_db["sku101_id"],
            )
        )
        assert stock.reserved_fbs == 3  # не 6

    async def test_zero_quantity_raises_validation(self):
        """Reserve c quantity=0 → Pydantic ValidationError."""
        with pytest.raises(pydantic.ValidationError):
            ReserveFbsRequest(
                warehouse_id=1,
                product_id=1,
                quantity=0,
                idempotency_key="test-zero",
            )
