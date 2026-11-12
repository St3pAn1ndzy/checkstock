"""Сквозной E2E-тест — полный цикл операций."""

import pytest
from sqlalchemy import func, select

from app.enums import MovementStatus, MovementType
from app.exceptions import InsufficientStockError, InvalidStatusError
from app.models import Movement, Stock
from app.schemas import (
    IncomeRequest,
    ReserveFbsRequest,
    ShipFboRequest,
    TransferRequest,
)
from app.services import (
    cancel_fbs,
    delete_warehouse,
    get_stock_report,
    income,
    receive,
    reserve_fbs,
    ship_fbo,
    ship_fbs,
    transfer,
)


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
            idempotency_key="e2e-reserve-1",
        )
        mv_res = await reserve_fbs(session, req_res)
        assert mv_res.status == MovementStatus.PENDING

        # 3. Нехватка: reserve 50 → InsufficientStockError
        req_bad = ReserveFbsRequest(
            warehouse_id=wh,
            product_id=prod,
            quantity=50,
            idempotency_key="e2e-reserve-2",
        )
        with pytest.raises(InsufficientStockError):
            await reserve_fbs(session, req_bad)

        # 4. Ship FBS (send)
        mv_ship = await ship_fbs(session, mv_res.id)
        assert mv_ship.status == MovementStatus.COMPLETED
        assert mv_ship.type == MovementType.FBS_SHIP

        # 5. Cancel того же резерва — уже отправлен, ошибка
        with pytest.raises(InvalidStatusError):
            await cancel_fbs(session, mv_res.id)

        # 6. Проверка финального состояния
        stock = await session.scalar(
            select(Stock).where(Stock.warehouse_id == wh, Stock.product_id == prod)
        )
        assert stock is not None
        assert stock.quantity == 12  # 10+5-3
        assert stock.reserved_fbs == 0  # был 3, ушёл в ship

        movements = (
            (await session.execute(select(Movement).order_by(Movement.id)))
            .scalars()
            .all()
        )
        assert len(movements) == 2  # income, reserve (обновлён до FBS_SHIP COMPLETED)

        status_by_key = {
            m.idempotency_key: m.status for m in movements if m.idempotency_key
        }
        assert status_by_key["e2e-income"] == MovementStatus.COMPLETED
        assert (
            status_by_key["e2e-reserve-1"] == MovementStatus.COMPLETED
        )  # стал COMPLETED после ship_fbs

    async def test_workflow_transfer_receive(self, session, clean_db):
        """Сквозной сценарий transfer + receive с проверкой инварианта суммы."""
        sever = clean_db["sever_id"]
        yug = clean_db["yug_id"]
        prod = clean_db["sku101_id"]

        # 1. Income 5 ед. (Север.quantity: 10 → 15)
        req_inc = IncomeRequest(
            warehouse_id=sever,
            product_id=prod,
            quantity=5,
            idempotency_key="e2e-trf-inc",
        )
        await income(session, req_inc)

        # 2. Transfer 3 Север→Юг (Север: 15→12, Юг: 6→6, в пути 3)
        req_trf = TransferRequest(
            from_warehouse_id=sever,
            to_warehouse_id=yug,
            product_id=prod,
            quantity=3,
            idempotency_key="e2e-trf-trf",
        )
        mv_trf = await transfer(session, req_trf)
        assert mv_trf.status == MovementStatus.PENDING

        # 3. Проверка после transfer
        s_stock = await session.scalar(
            select(Stock).where(Stock.warehouse_id == sever, Stock.product_id == prod)
        )
        y_stock = await session.scalar(
            select(Stock).where(Stock.warehouse_id == yug, Stock.product_id == prod)
        )
        pending_qty = await session.scalar(
            select(Movement.quantity).where(
                Movement.id == mv_trf.id,
                Movement.status == MovementStatus.PENDING,
            )
        )
        assert s_stock.quantity == 12
        assert y_stock.quantity == 6  # не изменился — товар в пути
        assert pending_qty == 3

        # 4. Receive
        mv_recv = await receive(session, mv_trf.id)
        assert mv_recv.status == MovementStatus.COMPLETED

        # 5. Финальная проверка
        s_stock = await session.scalar(
            select(Stock).where(Stock.warehouse_id == sever, Stock.product_id == prod)
        )
        y_stock = await session.scalar(
            select(Stock).where(Stock.warehouse_id == yug, Stock.product_id == prod)
        )
        s103 = await session.scalar(
            select(Stock).where(
                Stock.warehouse_id == sever, Stock.product_id == clean_db["sku103_id"]
            )
        )
        y103 = await session.scalar(
            select(Stock).where(
                Stock.warehouse_id == yug, Stock.product_id == clean_db["sku103_id"]
            )
        )
        s102 = await session.scalar(
            select(Stock).where(
                Stock.warehouse_id == sever, Stock.product_id == clean_db["sku102_id"]
            )
        )

        total_stocks = (
            (s_stock.quantity if s_stock else 0)
            + (y_stock.quantity if y_stock else 0)
            + (s102.quantity if s102 else 0)
            + (s103.quantity if s103 else 0)
            + (y103.quantity if y103 else 0)
        )

        assert s_stock.quantity == 12
        assert y_stock.quantity == 9  # 6 + 3
        assert total_stocks == 30  # 10+6+5+0+4 + 5(income) = 30
        assert mv_recv.quantity == mv_trf.quantity

    async def test_workflow_full(self, session, clean_db):
        """Полный сквозной сценарий: все операции + проверка сходимости."""
        sever = clean_db["sever_id"]
        yug = clean_db["yug_id"]
        prod = clean_db["sku101_id"]

        # 1. Income 5 на Север (SKU-101) → Север=15
        req_inc = IncomeRequest(
            warehouse_id=sever,
            product_id=prod,
            quantity=5,
            idempotency_key="e2e-full-inc",
        )
        await income(session, req_inc)

        # 2. Reserve_fbs 3 → reserved=3
        req_res = ReserveFbsRequest(
            warehouse_id=sever,
            product_id=prod,
            quantity=3,
            idempotency_key="e2e-full-res",
        )
        await reserve_fbs(session, req_res)

        # 3. Ship_fbs → Север=12, reserved=0
        mv_res = await session.scalar(
            select(Movement).where(Movement.idempotency_key == "e2e-full-res")
        )
        await ship_fbs(session, mv_res.id)

        # 4. Transfer 2 Север→Юг → Север=10, Юг=6, PENDING TRANSFER qty=2
        req_trf = TransferRequest(
            from_warehouse_id=sever,
            to_warehouse_id=yug,
            product_id=prod,
            quantity=2,
            idempotency_key="e2e-full-trf",
        )
        mv_trf = await transfer(session, req_trf)

        # 5. Receive → Юг=8
        await receive(session, mv_trf.id)

        # 6. Ship_fbo 4 с Севера → Север=6, PENDING FBO_SHIP qty=4
        req_fbo = ShipFboRequest(
            warehouse_id=sever,
            product_id=prod,
            quantity=4,
            idempotency_key="e2e-full-fbo",
        )
        mv_fbo = await ship_fbo(session, req_fbo)

        # 7. Receive → PENDING FBO_SHIP закрыт, stocks не изменились (Север=6, Юг=8)
        await receive(session, mv_fbo.id)

        # 8. Delete Север
        await delete_warehouse(session, sever)

        # 9. get_stock_report()
        report = await get_stock_report(session)

        # items только Юг (SKU-101=8, SKU-103=4)
        assert len(report.items) == 2
        for item in report.items:
            assert item.warehouse_name == "Юг"
        yug_101 = next(i for i in report.items if i.sku == "SKU-101")
        assert yug_101.quantity == 8
        yug_103 = next(i for i in report.items if i.sku == "SKU-103")
        assert yug_103.quantity == 4

        # in_transit пустой
        assert len(report.in_transit) == 0

        # 10. Проверка сходимости:
        # Исходные stocks: 10+6+5+0+4 = 25
        # Income +5 → 30 — это «весь товар в системе»
        # После всех операций:
        #   stocks (все, включая удалённые): Север=6+5+0, Юг=8+0+4 = 23
        #   PENDING movements: 0 (все закрыты)
        #   FBS_SHIP/FBO_SHIP (списано): 3 + 4 = 7
        # Итого: 23 + 7 = 30 ✅
        all_stocks_qty = sum(
            row[0] for row in (await session.execute(select(Stock.quantity))).all()
        )
        pending_qty = (
            await session.scalar(
                select(func.coalesce(func.sum(Movement.quantity), 0)).where(
                    Movement.status == MovementStatus.PENDING
                )
            )
            or 0
        )
        shipped_qty = (
            await session.scalar(
                select(func.coalesce(func.sum(Movement.quantity), 0)).where(
                    Movement.type.in_([MovementType.FBS_SHIP, MovementType.FBO_SHIP]),
                    Movement.status == MovementStatus.COMPLETED,
                )
            )
            or 0
        )
        assert all_stocks_qty + pending_qty + shipped_qty == 30
