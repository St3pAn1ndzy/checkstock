"""Тесты для операции приёмки товара (receive)."""

import pytest
from sqlalchemy import select

from app.enums import MovementStatus, MovementType
from app.exceptions import InvalidStatusError, NotFoundError
from app.models import Stock
from app.schemas import IncomeRequest, ReserveFbsRequest, TransferRequest
from app.services import cancel_fbs, income, receive, reserve_fbs, transfer


class TestReceive:
    """Приёмка товара (TRANSFER → зачисление, FBO_SHIP → COMPLETED)."""

    async def test_receive_transfer_success(self, session, clean_db):
        """Receive transfer: Юг.quantity 6→9, Север.quantity 7 (не меняется), Movement COMPLETED."""
        req_trf = TransferRequest(
            from_warehouse_id=clean_db["sever_id"],
            to_warehouse_id=clean_db["yug_id"],
            product_id=clean_db["sku101_id"],
            quantity=3,
            idempotency_key="test-rec-trf",
        )
        mv_trf = await transfer(session, req_trf)

        mv = await receive(session, mv_trf.id)

        assert mv.type == MovementType.TRANSFER
        assert mv.status == MovementStatus.COMPLETED
        assert mv.completed_at is not None

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

        assert sever_stock.quantity == 7  # 10 - 3, не изменился при receive
        assert yug_stock.quantity == 9  # 6 + 3

    async def test_receive_transfer_idempotent(self, session, clean_db):
        """Повторный receive не зачисляет дважды."""
        req_trf = TransferRequest(
            from_warehouse_id=clean_db["sever_id"],
            to_warehouse_id=clean_db["yug_id"],
            product_id=clean_db["sku101_id"],
            quantity=3,
            idempotency_key="test-rec-idem",
        )
        mv_trf = await transfer(session, req_trf)

        await receive(session, mv_trf.id)
        await receive(session, mv_trf.id)

        yug_stock = await session.scalar(
            select(Stock).where(
                Stock.warehouse_id == clean_db["yug_id"],
                Stock.product_id == clean_db["sku101_id"],
            )
        )
        assert yug_stock.quantity == 9  # не 12

    async def test_receive_fbo_ship_completes_without_stock_change(self, session, clean_db):
        """Receive на FBO_SHIP: stocks не меняются, Movement COMPLETED, completed_at заполнен."""
        from app.schemas import ShipFboRequest
        from app.services import ship_fbo

        # Сначала income 5 → Север=15
        req_inc = IncomeRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=5,
            idempotency_key="test-rec-fbo-inc",
        )
        await income(session, req_inc)

        # ship_fbo 4 → Север=11, Movement(FBO_SHIP, PENDING)
        req_fbo = ShipFboRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=4,
            idempotency_key="test-rec-fbo-ship",
        )
        mv_fbo = await ship_fbo(session, req_fbo)

        # receive
        mv = await receive(session, mv_fbo.id)

        assert mv.type == MovementType.FBO_SHIP
        assert mv.status == MovementStatus.COMPLETED
        assert mv.completed_at is not None

        # stocks не изменились
        sever_stock = await session.scalar(
            select(Stock).where(
                Stock.warehouse_id == clean_db["sever_id"],
                Stock.product_id == clean_db["sku101_id"],
            )
        )
        assert sever_stock.quantity == 11  # 10+5-4, не изменился после receive

    async def test_receive_wrong_type(self, session, clean_db):
        """Receive на INCOME → InvalidStatusError (ещё не COMPLETED)."""
        # Создаём INCOME-движение вручную: не COMPLETED, получаем PENDING
        from app.models import Movement

        # income делает COMPLETED сразу, поэтому создадим PENDING-движение вручную
        mv = Movement(
            type=MovementType.INCOME,
            status=MovementStatus.PENDING,
            product_id=clean_db["sku101_id"],
            quantity=1,
            to_warehouse_id=clean_db["sever_id"],
        )
        session.add(mv)
        await session.commit()

        with pytest.raises(InvalidStatusError) as exc_info:
            await receive(session, mv.id)
        assert "TRANSFER" in str(exc_info.value)

    async def test_receive_cancelled_raises(self, session, clean_db):
        """Receive на отменённый FBS_RESERVE → InvalidStatusError."""
        req = ReserveFbsRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=2,
            idempotency_key="test-receive-cancelled",
        )
        mv = await reserve_fbs(session, req)
        await cancel_fbs(session, mv.id)

        with pytest.raises(InvalidStatusError):
            await receive(session, mv.id)

    async def test_receive_not_found(self, session):
        """receive(9999) → NotFoundError."""
        with pytest.raises(NotFoundError):
            await receive(session, 9999)
