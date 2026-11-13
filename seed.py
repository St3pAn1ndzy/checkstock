"""
Идемпотентный скрипт наполнения тестовыми данными:
- 2 склада: Север, Юг
- 3 товара: SKU-101, SKU-102, SKU-103
- 5 строк stocks

Запуск: docker compose exec app python seed.py
"""

import asyncio

from sqlalchemy import select

from app.database import Base, async_session_factory, engine
from app.models import Product, Stock, Warehouse

SEED_WAREHOUSES = [("Север",), ("Юг",)]
SEED_PRODUCTS = [
    ("SKU-101", "Органайзер"),
    ("SKU-102", "Контейнер"),
    ("SKU-103", "Корзина"),
]
SEED_STOCKS = [
    ("Север", "SKU-101", 10),
    ("Юг", "SKU-101", 6),
    ("Север", "SKU-102", 5),
    ("Север", "SKU-103", 0),
    ("Юг", "SKU-103", 4),
]


async def seed():
    # Убеждаемся, что таблицы созданы
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_factory() as session:
        # Склады — upsert по name
        wh_map = {}
        for (name,) in SEED_WAREHOUSES:
            existing = await session.scalar(
                select(Warehouse).where(Warehouse.name == name)
            )
            if not existing:
                existing = Warehouse(name=name)
                session.add(existing)
                await session.flush()
            wh_map[name] = existing.id

        # Товары — upsert по sku
        prod_map = {}
        for sku, name in SEED_PRODUCTS:
            existing = await session.scalar(select(Product).where(Product.sku == sku))
            if not existing:
                existing = Product(sku=sku, name=name)
                session.add(existing)
                await session.flush()
            prod_map[sku] = existing.id

        # Stocks — upsert по (warehouse_id, product_id)
        for wh_name, sku, qty in SEED_STOCKS:
            wh_id = wh_map[wh_name]
            prod_id = prod_map[sku]
            existing = await session.scalar(
                select(Stock).where(
                    Stock.warehouse_id == wh_id,
                    Stock.product_id == prod_id,
                )
            )
            if not existing:
                session.add(
                    Stock(
                        warehouse_id=wh_id,
                        product_id=prod_id,
                        quantity=qty,
                        reserved_fbs=0,
                    )
                )

        await session.commit()
        print("Seed complete: 2 warehouses, 3 products, 5 stocks")


if __name__ == "__main__":
    asyncio.run(seed())
