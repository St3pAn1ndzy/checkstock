"""Тесты для soft delete складов."""

import pytest
from sqlalchemy import select

from app.exceptions import ConflictError, NotFoundError
from app.models import Stock, Warehouse
from app.schemas import IncomeRequest, ReserveFbsRequest
from app.services import cancel_fbs, delete_warehouse, income, reserve_fbs, ship_fbs


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

    async def test_delete_warehouse_with_active_reserve_raises(self, session, clean_db):
        """reserve_fbs 3 → delete Север → ConflictError."""
        # Arrange
        req = ReserveFbsRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=3,
            idempotency_key="b4-reserve",
        )
        await reserve_fbs(session, req)

        # Act / Assert
        with pytest.raises(ConflictError) as exc:
            await delete_warehouse(session, clean_db["sever_id"])
        assert "резерве" in str(exc.value.message)

        # Склад не удалён
        wh = await session.get(Warehouse, clean_db["sever_id"])
        assert wh.is_deleted is False

    async def test_delete_warehouse_after_cancel_reserve_ok(self, session, clean_db):
        """reserve_fbs 3 → cancel_fbs → delete Север → 200."""
        req = ReserveFbsRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=3,
            idempotency_key="b4-cancel",
        )
        mv = await reserve_fbs(session, req)
        await cancel_fbs(session, mv.id)

        result = await delete_warehouse(session, clean_db["sever_id"])
        assert result.is_deleted is True

    async def test_delete_warehouse_after_ship_reserve_ok(self, session, clean_db):
        """reserve_fbs 3 → ship_fbs → delete Север → 200."""
        req = ReserveFbsRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=3,
            idempotency_key="b4-ship",
        )
        mv = await reserve_fbs(session, req)
        await ship_fbs(session, mv.id)

        result = await delete_warehouse(session, clean_db["sever_id"])
        assert result.is_deleted is True
