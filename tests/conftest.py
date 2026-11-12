"""Фикстуры для интеграционных тестов с БД."""

import pytest_asyncio
from sqlalchemy import text

from app.database import Base, async_session_factory, engine
from app.models import Product, Stock, Warehouse
from app.schemas import ReserveFbsRequest
from app.services import reserve_fbs, ship_fbs


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _init_schema():
    """Создать схему БД один раз на сессию тестов."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest_asyncio.fixture
async def clean_db():
    """Полный сброс + seed перед каждым тестом.

    Возвращает словарь с id созданных записей.
    """
    async with async_session_factory() as session:
        await session.execute(
            text(
                "TRUNCATE movements, stocks, products, warehouses RESTART IDENTITY CASCADE"
            )
        )

        sever = Warehouse(name="Север")
        yug = Warehouse(name="Юг")
        sku101 = Product(sku="SKU-101", name="Органайзер")
        sku102 = Product(sku="SKU-102", name="Контейнер")
        sku103 = Product(sku="SKU-103", name="Корзина")
        session.add_all([sever, yug, sku101, sku102, sku103])
        await session.flush()

        session.add_all(
            [
                Stock(warehouse_id=sever.id, product_id=sku101.id, quantity=10),
                Stock(warehouse_id=yug.id, product_id=sku101.id, quantity=6),
                Stock(warehouse_id=sever.id, product_id=sku102.id, quantity=5),
                Stock(warehouse_id=sever.id, product_id=sku103.id, quantity=0),
                Stock(warehouse_id=yug.id, product_id=sku103.id, quantity=4),
            ]
        )
        await session.commit()

        return {
            "sever_id": sever.id,
            "yug_id": yug.id,
            "sku101_id": sku101.id,
            "sku102_id": sku102.id,
            "sku103_id": sku103.id,
        }


@pytest_asyncio.fixture
async def session():
    async with async_session_factory() as s:
        yield s


@pytest_asyncio.fixture
async def reserved_movement(session, clean_db):
    """FBS_RESERVE на Север/SKU-101, qty=3. Возвращает movement_id."""
    req = ReserveFbsRequest(
        warehouse_id=clean_db["sever_id"],
        product_id=clean_db["sku101_id"],
        quantity=3,
        idempotency_key="fixture-reserve-1",
    )
    mv = await reserve_fbs(session, req)
    return mv.id


@pytest_asyncio.fixture
async def shipped_movement(session, reserved_movement):
    """FBS_SHIP (COMPLETED). Возвращает movement_id."""
    mv = await ship_fbs(session, reserved_movement)
    return mv.id
