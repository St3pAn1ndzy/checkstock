"""Сквозной E2E-тест — полный цикл операций."""

from sqlalchemy import select

from app.database import async_session_factory
from app.enums import MovementStatus, MovementType
from app.exceptions import InsufficientStockError
from app.models import Movement, Stock
from app.schemas import IncomeRequest, ReserveFbsRequest
from app.services import cancel_fbs, income, reserve_fbs, ship_fbs


class TestWorkflowE2E:
    """Сквозной сценарий из README."""

    async def test_full_workflow(self, session, clean_db):
        """Полный цикл: приход → резерв → нехватка → отправка → отмена."""
        wh = clean_db["sever_id"]
        prod = clean_db["sku101_id"]

        # 1. Income 5 ед. (было 10 → 15)
        req_inc = IncomeRequest(
            warehouse_id=wh,
            product_id=prod,
            quantity=5,
            idempotency_key="e2e-income",
        )
        await income(session, req_inc)

        # 2. Reserve 3 ед. (reserved 0 → 3, available 15 → 12)
        req_res = ReserveFbsRequest(
            warehouse_id=wh,
            product_id=prod,
            quantity=3,
            idempotency_key="e2e-reserve",
        )
        mv_res = await reserve_fbs(session, req_res)
        assert mv_res.status == MovementStatus.PENDING

        # 3. Попытка reserve 100 → InsufficientStockError (доступно 12)
        req_fail = ReserveFbsRequest(
            warehouse_id=wh,
            product_id=prod,
            quantity=100,
            idempotency_key="e2e-reserve-fail",
        )
        try:
            await reserve_fbs(session, req_fail)
            assert False, "Ожидалась ошибка InsufficientStockError"
        except InsufficientStockError as e:
            assert "12" in e.message

        # 4. Ship того же movement (quantity 15 → 12, reserved 3 → 0)
        mv_ship = await ship_fbs(session, mv_res.id)
        assert mv_ship.type == MovementType.FBS_SHIP
        assert mv_ship.status == MovementStatus.COMPLETED

        # 5. Income повторно с тем же ключом — не увеличивает
        await income(session, req_inc)

        # 6. Reserve 2 ед. (reserved 0 → 2)
        req_res2 = ReserveFbsRequest(
            warehouse_id=wh,
            product_id=prod,
            quantity=2,
            idempotency_key="e2e-reserve-2",
        )
        mv_res2 = await reserve_fbs(session, req_res2)

        # 7. Cancel (reserved 2 → 0)
        mv_cancel = await cancel_fbs(session, mv_res2.id)
        assert mv_cancel.status == MovementStatus.CANCELLED

        # 8. Финальная проверка
        async with async_session_factory() as fresh:
            stock = await fresh.scalar(
                select(Stock).where(Stock.warehouse_id == wh, Stock.product_id == prod)
            )
            assert stock.quantity == 12
            assert stock.reserved_fbs == 0

            movements = (
                (await fresh.execute(select(Movement).order_by(Movement.id)))
                .scalars()
                .all()
            )

        # Проверяем движения по idempotency_key
        types_by_key = {m.idempotency_key: m.type for m in movements}
        status_by_key = {m.idempotency_key: m.status for m in movements}

        assert types_by_key["e2e-income"] == MovementType.INCOME
        assert status_by_key["e2e-income"] == MovementStatus.COMPLETED

        assert types_by_key["e2e-reserve"] == MovementType.FBS_SHIP
        assert status_by_key["e2e-reserve"] == MovementStatus.COMPLETED

        assert status_by_key["e2e-reserve-2"] == MovementStatus.CANCELLED

        assert len(movements) == 3
