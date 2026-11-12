"""Бизнес-логика учёта остатков.

Каждая операция — асинхронная функция, принимающая AsyncSession и Request-схему.
Транзакция завершается commit внутри функции. При ошибке — rollback + исключение.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import MovementStatus, MovementType
from app.exceptions import (
    ConflictError,
    InsufficientStockError,
    InvalidStatusError,
    NotFoundError,
)
from app.models import Movement, Product, Stock, Warehouse
from app.schemas import IncomeRequest, MovementResponse, ReserveFbsRequest

# ── Helpers (приватные) ───────────────────────────────────────────


async def _get_warehouse_or_404(session: AsyncSession, warehouse_id: int) -> Warehouse:
    """Вернуть склад по id или NotFoundError."""
    wh = await session.get(Warehouse, warehouse_id)
    if wh is None or wh.is_deleted:
        raise NotFoundError(f"Склад с id={warehouse_id} не найден")
    return wh


async def _get_product_or_404(session: AsyncSession, product_id: int) -> Product:
    """Вернуть товар по id или NotFoundError."""
    prod = await session.get(Product, product_id)
    if prod is None:
        raise NotFoundError(f"Товар с id={product_id} не найден")
    return prod


async def _get_stock_for_update(
    session: AsyncSession, warehouse_id: int, product_id: int
) -> Stock | None:
    """SELECT ... FOR UPDATE. Блокирует строку до конца транзакции."""
    stmt = (
        select(Stock)
        .where(Stock.warehouse_id == warehouse_id, Stock.product_id == product_id)
        .with_for_update()
    )
    return await session.scalar(stmt)


async def _get_or_create_stock(
    session: AsyncSession, warehouse_id: int, product_id: int
) -> Stock:
    """Вернуть Stock с FOR UPDATE. Если нет — создать с нулями."""
    stock = await _get_stock_for_update(session, warehouse_id, product_id)
    if stock is None:
        stock = Stock(
            warehouse_id=warehouse_id, product_id=product_id, quantity=0, reserved_fbs=0
        )
        session.add(stock)
        await session.flush()
    return stock


async def _find_by_idempotency_key(
    session: AsyncSession,
    idempotency_key: str,
) -> Movement | None:
    """Поиск существующего движения по ключу идемпотентности."""
    stmt = select(Movement).where(Movement.idempotency_key == idempotency_key)
    return await session.scalar(stmt)


def _to_movement_response(mv: Movement) -> MovementResponse:
    """Сконвертировать ORM-модель в Response-схему."""
    return MovementResponse.model_validate(mv)


# ── Операции ──────────────────────────────────────────────────────


async def income(session: AsyncSession, req: IncomeRequest) -> MovementResponse:
    """
    Приход товара на склад. Идемпотентен по idempotency_key.
    Увеличивает quantity, пишет Movement(INCOME, COMPLETED).
    """
    existing = await _find_by_idempotency_key(session, req.idempotency_key)
    if existing is not None:
        return _to_movement_response(existing)

    await _get_warehouse_or_404(session, req.warehouse_id)
    await _get_product_or_404(session, req.product_id)

    stock = await _get_or_create_stock(session, req.warehouse_id, req.product_id)
    stock.quantity += req.quantity

    mv = Movement(
        type=MovementType.INCOME,
        status=MovementStatus.COMPLETED,
        product_id=req.product_id,
        quantity=req.quantity,
        from_warehouse_id=None,
        to_warehouse_id=req.warehouse_id,
        idempotency_key=req.idempotency_key,
        completed_at=datetime.now(UTC),
    )
    session.add(mv)
    await session.commit()
    await session.refresh(mv)
    return _to_movement_response(mv)


async def reserve_fbs(
    session: AsyncSession, req: ReserveFbsRequest
) -> MovementResponse:
    """
    Резерв под FBS. Проверяет available >= quantity, иначе InsufficientStockError (409).
    Увеличивает reserved_fbs, пишет Movement(FBS_RESERVE, PENDING).
    """
    existing = await _find_by_idempotency_key(session, req.idempotency_key)
    if existing is not None:
        return _to_movement_response(existing)

    wh = await _get_warehouse_or_404(session, req.warehouse_id)
    await _get_product_or_404(session, req.product_id)

    stock = await _get_or_create_stock(session, req.warehouse_id, req.product_id)
    available = stock.quantity - stock.reserved_fbs
    if available < req.quantity:
        wh_name = wh.name
        await session.rollback()
        raise InsufficientStockError(
            warehouse_name=wh_name,
            available=available,
            requested=req.quantity,
        )

    stock.reserved_fbs += req.quantity

    mv = Movement(
        type=MovementType.FBS_RESERVE,
        status=MovementStatus.PENDING,
        product_id=req.product_id,
        quantity=req.quantity,
        from_warehouse_id=req.warehouse_id,
        to_warehouse_id=None,
        idempotency_key=req.idempotency_key,
    )
    session.add(mv)
    await session.commit()
    await session.refresh(mv)
    return _to_movement_response(mv)


async def ship_fbs(session: AsyncSession, movement_id: int) -> MovementResponse:
    """
    Отправка FBS-заказа клиенту. Переводит FBS_RESERVE (PENDING) → FBS_SHIP (COMPLETED).
    Списывает с quantity и снимает reserved_fbs.
    Идемпотентность по статусу: COMPLETED → вернуть, CANCELLED → 409.
    """
    mv = await session.get(Movement, movement_id)
    if mv is None:
        raise NotFoundError(f"Movement с id={movement_id} не найден")

    if mv.status == MovementStatus.COMPLETED:
        return _to_movement_response(mv)

    if mv.type != MovementType.FBS_RESERVE:
        raise InvalidStatusError(f"Ожидался FBS_RESERVE, получен {mv.type}")

    if mv.status == MovementStatus.CANCELLED:
        raise InvalidStatusError("Нельзя отправить отменённый резерв")

    # mv.status == PENDING
    stock = await _get_stock_for_update(session, mv.from_warehouse_id, mv.product_id)
    if stock is None:
        await session.rollback()
        raise NotFoundError("Сток не найден, данные несогласованы")

    if stock.quantity < mv.quantity or stock.reserved_fbs < mv.quantity:
        await session.rollback()
        raise ConflictError(
            f"Несогласованные данные: quantity={stock.quantity}, "
            f"reserved_fbs={stock.reserved_fbs}, ship_qty={mv.quantity}"
        )

    stock.quantity -= mv.quantity
    stock.reserved_fbs -= mv.quantity
    mv.type = MovementType.FBS_SHIP
    mv.status = MovementStatus.COMPLETED
    mv.completed_at = datetime.now(UTC)

    await session.commit()
    await session.refresh(mv)
    return _to_movement_response(mv)


async def cancel_fbs(session: AsyncSession, movement_id: int) -> MovementResponse:
    """
    Отмена FBS-резерва. Возвращает reserved_fbs в available.
    Идемпотентность по статусу: CANCELLED → вернуть, COMPLETED → 409.
    """
    mv = await session.get(Movement, movement_id)
    if mv is None:
        raise NotFoundError(f"Movement с id={movement_id} не найден")

    if mv.status == MovementStatus.CANCELLED:
        return _to_movement_response(mv)

    if mv.type != MovementType.FBS_RESERVE:
        raise InvalidStatusError(f"Ожидался FBS_RESERVE, получен {mv.type}")

    if mv.status == MovementStatus.COMPLETED:
        raise InvalidStatusError("Нельзя отменить уже отправленный заказ")

    # mv.status == PENDING
    stock = await _get_stock_for_update(session, mv.from_warehouse_id, mv.product_id)
    if stock is None:
        await session.rollback()
        raise NotFoundError("Сток не найден, данные несогласованы")

    stock.reserved_fbs -= mv.quantity
    mv.status = MovementStatus.CANCELLED
    mv.completed_at = datetime.now(UTC)

    await session.commit()
    await session.refresh(mv)
    return _to_movement_response(mv)
