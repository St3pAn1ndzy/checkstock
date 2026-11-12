"""Тесты идемпотентности с проверкой типа и параметров (A3 + A4)."""

import pytest

from app.exceptions import IdempotencyConflictError
from app.schemas import IncomeRequest, ReserveFbsRequest, TransferRequest
from app.services import income, reserve_fbs, transfer


class TestIdempotencyConflict:
    """Идемпотентность с валидацией параметров."""

    async def test_income_then_reserve_same_key_raises(self, session, clean_db):
        """income с key="k1", потом reserve_fbs с key="k1" → IdempotencyConflictError."""
        inc_req = IncomeRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=5,
            idempotency_key="k1",
        )
        await income(session, inc_req)

        res_req = ReserveFbsRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=2,
            idempotency_key="k1",
        )
        with pytest.raises(IdempotencyConflictError):
            await reserve_fbs(session, res_req)

    async def test_income_different_quantity_same_key_raises(self, session, clean_db):
        """income qty=5 key="k2", потом income qty=10 key="k2" → IdempotencyConflictError."""
        req1 = IncomeRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=5,
            idempotency_key="k2",
        )
        await income(session, req1)

        req2 = IncomeRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=10,
            idempotency_key="k2",
        )
        with pytest.raises(IdempotencyConflictError):
            await income(session, req2)

    async def test_transfer_different_to_warehouse_raises(self, session, clean_db):
        """transfer key="k3" с разными from/to → IdempotencyConflictError."""
        req1 = TransferRequest(
            from_warehouse_id=clean_db["sever_id"],
            to_warehouse_id=clean_db["yug_id"],
            product_id=clean_db["sku101_id"],
            quantity=2,
            idempotency_key="k3",
        )
        await transfer(session, req1)

        req2 = TransferRequest(
            from_warehouse_id=clean_db["yug_id"],
            to_warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=2,
            idempotency_key="k3",
        )
        with pytest.raises(IdempotencyConflictError):
            await transfer(session, req2)

    async def test_same_key_same_params_still_returns_existing(self, session, clean_db):
        """income qty=5 key="k4" дважды → оба 200, один Movement."""
        req = IncomeRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=5,
            idempotency_key="k4",
        )
        mv1 = await income(session, req)
        mv2 = await income(session, req)

        assert mv1.id == mv2.id
        # quantity не изменился после повторного вызова
        assert mv1.quantity == 5

        # В БД один Movement
        from sqlalchemy import select

        from app.models import Movement

        all_mv = (
            (
                await session.execute(
                    select(Movement).where(Movement.idempotency_key == "k4")
                )
            )
            .scalars()
            .all()
        )
        assert len(all_mv) == 1
