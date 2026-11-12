"""Тесты для операции прихода товара (income)."""

from sqlalchemy import func, select

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

        stock = await session.scalar(
            select(Stock).where(
                Stock.warehouse_id == clean_db["sever_id"],
                Stock.product_id == clean_db["sku101_id"],
            )
        )
        assert stock is not None
        assert stock.quantity == 15

    async def test_idempotent(self, session, clean_db):
        """Повторный income с тем же idempotency_key — quantity увеличивается 1 раз."""
        req = IncomeRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=5,
            idempotency_key="test-income-idempotent",
        )
        mv1 = await income(session, req)
        mv2 = await income(session, req)

        assert mv1.id == mv2.id

        stock = await session.scalar(
            select(Stock).where(
                Stock.warehouse_id == clean_db["sever_id"],
                Stock.product_id == clean_db["sku101_id"],
            )
        )
        assert stock.quantity == 15  # 10+5, не 10+5+5

        cnt = await session.scalar(
            select(func.count(Movement.id)).where(
                Movement.idempotency_key == "test-income-idempotent"
            )
        )
        assert cnt == 1

    async def test_creates_stock_if_missing(self, session, clean_db):
        """Income на товар, которого нет на складе → создаёт Stock с нуля."""
        req = IncomeRequest(
            warehouse_id=clean_db["yug_id"],
            product_id=clean_db["sku102_id"],
            quantity=3,
            idempotency_key="test-income-create-stock",
        )
        await income(session, req)

        stock = await session.scalar(
            select(Stock).where(
                Stock.warehouse_id == clean_db["yug_id"],
                Stock.product_id == clean_db["sku102_id"],
            )
        )
        assert stock is not None
        assert stock.quantity == 3
        assert stock.reserved_fbs == 0
