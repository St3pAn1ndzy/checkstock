"""Тесты для отчёта остатков (get_stock_report)."""

import pytest
from sqlalchemy import select

from app.enums import MovementStatus, MovementType
from app.models import Stock
from app.schemas import IncomeRequest, ReserveFbsRequest, ShipFboRequest, TransferRequest
from app.services import (
    delete_warehouse,
    get_stock_report,
    income,
    reserve_fbs,
    ship_fbo,
    transfer,
)


class TestStockReport:
    """Отчёт остатков."""

    async def test_report_empty_in_transit(self, session, clean_db):
        """На seed: 5 items, in_transit пустой."""
        report = await get_stock_report(session)

        assert len(report.items) == 5
        # available = quantity (резервов нет)
        for item in report.items:
            assert item.available == item.quantity

        assert len(report.in_transit) == 0

    async def test_report_after_income(self, session, clean_db):
        """Income 5 на Север (SKU-101): quantity=15, available=15."""
        req = IncomeRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=5,
            idempotency_key="test-report-income",
        )
        await income(session, req)

        report = await get_stock_report(session)
        sever_101 = next(
            i for i in report.items
            if i.warehouse_id == clean_db["sever_id"] and i.product_id == clean_db["sku101_id"]
        )
        assert sever_101.quantity == 15
        assert sever_101.available == 15

    async def test_report_with_reserved(self, session, clean_db):
        """Reserve_fbs 3: quantity=10, reserved_fbs=3, available=7. FBS_RESERVE не в in_transit."""
        req = ReserveFbsRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=3,
            idempotency_key="test-report-reserve",
        )
        await reserve_fbs(session, req)

        report = await get_stock_report(session)
        sever_101 = next(
            i for i in report.items
            if i.warehouse_id == clean_db["sever_id"] and i.product_id == clean_db["sku101_id"]
        )
        assert sever_101.quantity == 10
        assert sever_101.reserved_fbs == 3
        assert sever_101.available == 7

        # FBS_RESERVE — не в пути, только TRANSFER и FBO_SHIP
        assert len(report.in_transit) == 0

    async def test_report_with_transfer_in_transit(self, session, clean_db):
        """Transfer 3 Север→Юг: in_transit содержит 1 элемент."""
        req = TransferRequest(
            from_warehouse_id=clean_db["sever_id"],
            to_warehouse_id=clean_db["yug_id"],
            product_id=clean_db["sku101_id"],
            quantity=3,
            idempotency_key="test-report-transfer",
        )
        mv = await transfer(session, req)

        report = await get_stock_report(session)
        assert len(report.in_transit) == 1

        transit = report.in_transit[0]
        assert transit.movement_id == mv.id
        assert transit.type == MovementType.TRANSFER
        assert transit.quantity == 3
        assert transit.from_warehouse_name == "Север"
        assert transit.to_warehouse_name == "Юг"

        # Север.quantity уменьшился
        sever_101 = next(
            i for i in report.items
            if i.warehouse_id == clean_db["sever_id"] and i.product_id == clean_db["sku101_id"]
        )
        assert sever_101.quantity == 7  # 10 - 3

    async def test_report_with_fbo_in_transit(self, session, clean_db):
        """Ship_fbo 4 с Севера: in_transit содержит FBO_SHIP, to=None."""
        req = ShipFboRequest(
            warehouse_id=clean_db["sever_id"],
            product_id=clean_db["sku101_id"],
            quantity=4,
            idempotency_key="test-report-fbo",
        )
        mv = await ship_fbo(session, req)

        report = await get_stock_report(session)
        assert len(report.in_transit) == 1

        transit = report.in_transit[0]
        assert transit.movement_id == mv.id
        assert transit.type == MovementType.FBO_SHIP
        assert transit.from_warehouse_name == "Север"
        assert transit.to_warehouse_name is None

        sever_101 = next(
            i for i in report.items
            if i.warehouse_id == clean_db["sever_id"] and i.product_id == clean_db["sku101_id"]
        )
        assert sever_101.quantity == 6  # 10 - 4

    async def test_report_excludes_deleted_warehouses(self, session, clean_db):
        """После delete Севера: items не содержит Север, stocks в БД остались."""
        await delete_warehouse(session, clean_db["sever_id"])

        report = await get_stock_report(session)
        # Только Юг
        for item in report.items:
            assert item.warehouse_name == "Юг"
        assert len(report.items) == 2  # Юг: SKU-101 (qty=6), SKU-103 (qty=4)

        # Но в БД stocks Севера остались
        sever_stocks = (
            await session.execute(
                select(Stock).where(Stock.warehouse_id == clean_db["sever_id"])
            )
        ).scalars().all()
        assert len(sever_stocks) > 0
