"""Фикстуры для интеграционных тестов с БД.

Единая фикстура `db` на тест: TRUNCATE + seed → yield (session, data).

Используем NullPool + engine.dispose() между тестами, чтобы избежать
race condition asyncpg при смене event loop.
"""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.database import Base, async_session_factory, engine
from app.main import app
from app.models import Product, Stock, Warehouse


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _init_schema():
    """Создать схему БД один раз на сессию тестов."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest_asyncio.fixture(scope="function")
async def db():
    """Единая фикстура: TRUNCATE + seed → yield (session, data)."""
    async with async_session_factory() as s:
        await s.execute(
            text(
                "TRUNCATE movements, stocks, products, warehouses RESTART IDENTITY CASCADE"
            )
        )

        sever = Warehouse(name="Север")
        yug = Warehouse(name="Юг")
        sku101 = Product(sku="SKU-101", name="Органайзер")
        sku102 = Product(sku="SKU-102", name="Контейнер")
        sku103 = Product(sku="SKU-103", name="Корзина")
        s.add_all([sever, yug, sku101, sku102, sku103])
        await s.flush()

        s.add_all(
            [
                Stock(warehouse_id=sever.id, product_id=sku101.id, quantity=10),
                Stock(warehouse_id=yug.id, product_id=sku101.id, quantity=6),
                Stock(warehouse_id=sever.id, product_id=sku102.id, quantity=5),
                Stock(warehouse_id=sever.id, product_id=sku103.id, quantity=0),
                Stock(warehouse_id=yug.id, product_id=sku103.id, quantity=4),
            ]
        )
        await s.commit()

        data = {
            "sever_id": sever.id,
            "yug_id": yug.id,
            "sku101_id": sku101.id,
            "sku102_id": sku102.id,
            "sku103_id": sku103.id,
        }

        yield s, data

    # После выхода из db фикстуры — принудительно закрываем все соединения,
    # чтобы новый event loop не подхватил "висящие" asyncpg-соединения.
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def clean_db(db):
    """Прокси: только данные из db."""
    _, data = db
    return data


@pytest_asyncio.fixture(scope="function")
async def session(db):
    """Прокси: только сессия из db."""
    sess, _ = db
    return sess


@pytest_asyncio.fixture(scope="function")
async def client(clean_db):
    """HTTP-клиент поверх ASGI-приложения. Использует чистую БД."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
