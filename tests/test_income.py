"""Тесты для операции прихода товара (income)."""

from sqlalchemy import select

from app.database import async_session_factory
from app.enums import MovementStatus, MovementType
from app.models import Movement, Stock
from app.schemas import IncomeRequest
from app.services import income


class TestIncome:
    """Приход товара на склад."""

    async def test_increases_quantity(self, session, clean_db):
        """Income 5 ед. → quantity увеличивается с 10 до 15."""
        req = IncomeRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=5,
            idempotency_key="test-income-1",
        )
        mv = await income(session, req)

        assert mv.type == MovementType.INCOME
        assert mv.status == MovementStatus.COMPLETED
        assert mv.completed_at is not None

        async with async_session_factory() as fresh:
            stock = await fresh.scalar(
                select(Stock).where(
                    Stock.warehouse_id == clean_db["sever_id"],
                    Stock.product_id == clean_db["sku101_id"],
                )
            )
        assert stock.quantity == 15

    async def test_idempotent(self, session, clean_db):
        """Повторный вызов с тем же ключом не меняет quantity и не создаёт дубль."""
        req = IncomeRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=5,
            idempotency_key="test-income-2",
        )
        mv1 = await income(session, req)
        mv2 = await income(session, req)

        assert mv1.id == mv2.id
        async with async_session_factory() as fresh:
            stock = await fresh.scalar(
                select(Stock).where(
                    Stock.warehouse_id == clean_db["sever_id"],
                    Stock.product_id == clean_db["sku101_id"],
                )
            )
            assert stock.quantity == 15  # не 20

            count = (
                await fresh.execute(
                    select(Movement).where(Movement.idempotency_key == "test-income-2")
                )
            ).scalar_one_or_none()
        assert count is not None

    async def test_creates_stock_if_missing(self, session, clean_db):
        """Income на (Юг, SKU-102), где stock не было → создаётся с quantity=5."""
        req = IncomeRequest(
            warehouse_id=clean_db["yug_id"],
            product_id=clean_db["sku102_id"],
            quantity=5,
            idempotency_key="test-income-create-stock",
        )
        await income(session, req)

        async with async_session_factory() as fresh:
            stock = await fresh.scalar(
                select(Stock).where(
                    Stock.warehouse_id == clean_db["yug_id"],
                    Stock.product_id == clean_db["sku102_id"],
                )
            )
        assert stock is not None
        assert stock.quantity == 5
        assert stock.reserved_fbs == 0
