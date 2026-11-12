"""Тесты для операции перемещения между ФФ (transfer)."""

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.enums import MovementStatus, MovementType
from app.exceptions import InsufficientStockError, NotFoundError
from app.models import Movement, Stock
from app.schemas import ReserveFbsRequest, TransferRequest
from app.services import reserve_fbs, transfer


class TestTransfer:
    """Перемещение между складами."""

    async def test_transfer_success(self, session, clean_db):
        """Transfer 3 ед. Север→Юг: Север.quantity 10→7, Юг.quantity 6 (не меняется), Movement(TRANSFER, PENDING)."""
        req = TransferRequest(
            from_warehouse_id=clean_db["sever_id"],
            to_warehouse_id=clean_db["yug_id"],
            product_id=clean_db["sku101_id"],
            quantity=3,
            idempotency_key="test-transfer-1",
        )
        mv = await transfer(session, req)

        assert mv.type == MovementType.TRANSFER
        assert mv.status == MovementStatus.PENDING
        assert mv.from_warehouse_id == clean_db["sever_id"]
        assert mv.to_warehouse_id == clean_db["yug_id"]
        assert mv.quantity == 3

        sever_stock = await session.scalar(
            select(Stock).where(
                Stock.warehouse_id == clean_db["sever_id"],
                Stock.product_id == clean_db["sku101_id"],
            )
        )
        yug_stock = await session.scalar(
            select(Stock).where(
                Stock.warehouse_id == clean_db["yug_id"],
                Stock.product_id == clean_db["sku101_id"],
            )
        )

        assert sever_stock.quantity == 7  # 10 - 3
        assert yug_stock.quantity == 6  # не изменился — товар в пути

    async def test_transfer_insufficient(self, session, clean_db):
        """Transfer 100 ед. → InsufficientStockError, ничего не изменилось."""
        req = TransferRequest(
            from_warehouse_id=clean_db["sever_id"],
            to_warehouse_id=clean_db["yug_id"],
            product_id=clean_db["sku101_id"],
            quantity=100,
            idempotency_key="test-transfer-insufficient",
        )
        with pytest.raises(InsufficientStockError) as exc_info:
            await transfer(session, req)

        assert "Север" in exc_info.value.message
        assert "10" in exc_info.value.message  # available
        assert "100" in exc_info.value.message

        sever_stock = await session.scalar(
            select(Stock).where(
                Stock.warehouse_id == clean_db["sever_id"],
                Stock.product_id == clean_db["sku101_id"],
            )
        )
        yug_stock = await session.scalar(
            select(Stock).where(
                Stock.warehouse_id == clean_db["yug_id"],
                Stock.product_id == clean_db["sku101_id"],
            )
        )

        assert sever_stock.quantity == 10  # rollback — не изменилось
        assert yug_stock.quantity == 6

    async def test_transfer_respects_reserved(self, session, clean_db):
        """После reserve_fbs qty=8 (available=2) transfer 3 → InsufficientStockError."""
        req_reserve = ReserveFbsRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=8,
            idempotency_key="test-transfer-reserved-reserve",
        )
        await reserve_fbs(session, req_reserve)

        req_transfer = TransferRequest(
            from_warehouse_id=clean_db["sever_id"],
            to_warehouse_id=clean_db["yug_id"],
            product_id=clean_db["sku101_id"],
            quantity=3,
            idempotency_key="test-transfer-reserved-transfer",
        )
        with pytest.raises(InsufficientStockError) as exc_info:
            await transfer(session, req_transfer)

        assert "2" in exc_info.value.message  # available = 10 - 8 = 2

        sever_stock = await session.scalar(
            select(Stock).where(
                Stock.warehouse_id == clean_db["sever_id"],
                Stock.product_id == clean_db["sku101_id"],
            )
        )
        assert sever_stock.quantity == 10  # не изменилось
        assert sever_stock.reserved_fbs == 8

    async def test_transfer_idempotent(self, session, clean_db):
        """Повторный с тем же ключом — Север.quantity=7 (не 4), movements=1 (не 2)."""
        req = TransferRequest(
            from_warehouse_id=clean_db["sever_id"],
            to_warehouse_id=clean_db["yug_id"],
            product_id=clean_db["sku101_id"],
            quantity=3,
            idempotency_key="test-transfer-idempotent",
        )
        mv1 = await transfer(session, req)
        mv2 = await transfer(session, req)

        assert mv1.id == mv2.id

        sever_stock = await session.scalar(
            select(Stock).where(
                Stock.warehouse_id == clean_db["sever_id"],
                Stock.product_id == clean_db["sku101_id"],
            )
        )
        assert sever_stock.quantity == 7  # не 4

        cnt = await session.scalar(
            select(func.count(Movement.id)).where(
                Movement.idempotency_key == "test-transfer-idempotent"
            )
        )
        assert cnt == 1

    async def test_transfer_to_nonexistent_warehouse(self, session, clean_db):
        """to_warehouse_id=999 → NotFoundError."""
        req = TransferRequest(
            from_warehouse_id=clean_db["sever_id"],
            to_warehouse_id=999,
            product_id=clean_db["sku101_id"],
            quantity=1,
            idempotency_key="test-transfer-notfound",
        )
        with pytest.raises(NotFoundError):
            await transfer(session, req)

    async def test_transfer_same_warehouse_raises(self):
        """from=to → Pydantic ValidationError."""
        with pytest.raises(ValidationError):
            TransferRequest(
                from_warehouse_id=1,
                to_warehouse_id=1,
                product_id=1,
                quantity=1,
                idempotency_key="test-transfer-same",
            )
