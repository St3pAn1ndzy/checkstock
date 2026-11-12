"""Тесты для soft delete складов."""

import pytest
from sqlalchemy import select

from app.exceptions import NotFoundError
from app.models import Stock, Warehouse
from app.schemas import IncomeRequest
from app.services import delete_warehouse, income


class TestDeleteWarehouse:
    """Soft delete склада."""

    async def test_delete_warehouse_soft(self, session, clean_db):
        """Delete Север: is_deleted=True, stocks не удалены."""
        result = await delete_warehouse(session, clean_db["sever_id"])

        assert result.id == clean_db["sever_id"]
        assert result.is_deleted is True

        # В БД запись существует с is_deleted=True
        wh = await session.get(Warehouse, clean_db["sever_id"])
        assert wh is not None
        assert wh.is_deleted is True

        # Stocks Севера не удалены
        sever_stocks = (
            (
                await session.execute(
                    select(Stock).where(Stock.warehouse_id == clean_db["sever_id"])
                )
            )
            .scalars()
            .all()
        )
        assert len(sever_stocks) > 0

        # Всего stocks осталось 5
        all_stocks = (await session.execute(select(Stock))).scalars().all()
        assert len(all_stocks) == 5

    async def test_delete_warehouse_idempotent(self, session, clean_db):
        """Повторный delete → 200, is_deleted=True."""
        await delete_warehouse(session, clean_db["sever_id"])
        result = await delete_warehouse(session, clean_db["sever_id"])

        assert result.id == clean_db["sever_id"]
        assert result.is_deleted is True

    async def test_delete_warehouse_not_found(self, session):
        """delete(9999) → NotFoundError."""
        with pytest.raises(NotFoundError):
            await delete_warehouse(session, 9999)

    async def test_delete_warehouse_keeps_history(self, session, clean_db):
        """После delete: income и stocks существуют, склад помечен."""
        req = IncomeRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=5,
            idempotency_key="test-del-hist",
        )
        await income(session, req)

        result = await delete_warehouse(session, clean_db["sever_id"])
        assert result.is_deleted is True

        # Stock существует с quantity=15
        stock = await session.scalar(
            select(Stock).where(
                Stock.warehouse_id == clean_db["sever_id"],
                Stock.product_id == clean_db["sku101_id"],
            )
        )
        assert stock is not None
        assert stock.quantity == 15

        # Склад помечен
        wh = await session.get(Warehouse, clean_db["sever_id"])
        assert wh.is_deleted is True
