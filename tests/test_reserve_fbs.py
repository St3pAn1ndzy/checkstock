"""Тесты для операции резерва под FBS (reserve_fbs)."""

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.database import async_session_factory
from app.enums import MovementStatus, MovementType
from app.exceptions import InsufficientStockError
from app.models import Stock
from app.schemas import ReserveFbsRequest
from app.services import reserve_fbs


class TestReserveFbs:
    """Резерв под FBS."""

    async def test_success(self, session, clean_db):
        """Успешный резерв: reserved_fbs увеличивается, available уменьшается."""
        # Сначала делаем income, чтобы было 15
        from app.schemas import IncomeRequest
        from app.services import income

        req_inc = IncomeRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=5,
            idempotency_key="test-reserve-inc",
        )
        await income(session, req_inc)

        req = ReserveFbsRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=3,
            idempotency_key="test-reserve-1",
        )
        mv = await reserve_fbs(session, req)

        assert mv.type == MovementType.FBS_RESERVE
        assert mv.status == MovementStatus.PENDING

        async with async_session_factory() as fresh:
            stock = await fresh.scalar(
                select(Stock).where(
                    Stock.warehouse_id == clean_db["sever_id"],
                    Stock.product_id == clean_db["sku101_id"],
                )
            )
        assert stock.reserved_fbs == 3
        assert stock.quantity - stock.reserved_fbs == 12  # available

    async def test_insufficient(self, session, clean_db):
        """Резерв больше доступного → InsufficientStockError, rollback."""
        req = ReserveFbsRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=100,
            idempotency_key="test-reserve-2",
        )
        with pytest.raises(InsufficientStockError) as exc_info:
            await reserve_fbs(session, req)

        assert "Север" in exc_info.value.message
        assert "10" in exc_info.value.message  # available (нет income)
        assert "100" in exc_info.value.message  # requested

        async with async_session_factory() as fresh:
            stock = await fresh.scalar(
                select(Stock).where(
                    Stock.warehouse_id == clean_db["sever_id"],
                    Stock.product_id == clean_db["sku101_id"],
                )
            )
        assert stock.reserved_fbs == 0  # rollback — не изменилось

    async def test_idempotent(self, session, clean_db):
        """Повторный резерв с тем же ключом не меняет данные."""
        req = ReserveFbsRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=3,
            idempotency_key="test-reserve-3",
        )
        mv1 = await reserve_fbs(session, req)
        mv2 = await reserve_fbs(session, req)

        assert mv1.id == mv2.id

        async with async_session_factory() as fresh:
            stock = await fresh.scalar(
                select(Stock).where(
                    Stock.warehouse_id == clean_db["sever_id"],
                    Stock.product_id == clean_db["sku101_id"],
                )
            )
        assert stock.reserved_fbs == 3  # не 6

    async def test_zero_quantity_raises_validation(self):
        """quantity=0 не проходит Pydantic-валидацию."""
        with pytest.raises(ValidationError):
            ReserveFbsRequest(
                warehouse_id=1,
                product_id=1,
                quantity=0,
                idempotency_key="test-reserve-zero",
            )
