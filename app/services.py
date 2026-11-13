"""Бизнес-логика учёта остатков.

Каждая операция — асинхронная функция, принимающая AsyncSession и Request-схему.
Транзакция завершается commit внутри функции. При ошибке — rollback + исключение.
"""

from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.enums import MovementStatus, MovementType
from app.exceptions import (
    ConflictError,
    IdempotencyConflictError,
    InsufficientStockError,
    InvalidStatusError,
    NotFoundError,
)
from app.models import Movement, Product, Stock, Warehouse
from app.schemas import (
    IncomeRequest,
    MovementResponse,
    MovementsListResponse,
    ProductCreate,
    ProductRead,
    ReserveFbsRequest,
    ShipFboRequest,
    StockItem,
    StockReportResponse,
    TransferRequest,
    WarehouseCreate,
    WarehouseRead,
    WarehouseUpdate,
)

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


async def _find_or_conflict(
    session: AsyncSession,
    idempotency_key: str,
    expected_type: MovementType,
    expected_product_id: int,
    expected_quantity: int,
    expected_from_warehouse_id: int | None = None,
    expected_to_warehouse_id: int | None = None,
) -> Movement | None:
    """
    Найти Movement по idempotency_key.
    Если найден — проверить, что type и параметры совпадают с ожидаемыми.
    Если не совпадают — IdempotencyConflictError (409).
    Если не найден — None.
    """
    existing = await _find_by_idempotency_key(session, idempotency_key)
    if existing is None:
        return None

    mismatches = []
    if existing.type != expected_type:
        mismatches.append(
            f"type (ожидалось {expected_type.value}, получено {existing.type.value})"
        )
    if existing.product_id != expected_product_id:
        mismatches.append(
            f"product_id (ожидалось {expected_product_id}, получено {existing.product_id})"
        )
    if existing.quantity != expected_quantity:
        mismatches.append(
            f"quantity (ожидалось {expected_quantity}, получено {existing.quantity})"
        )
    if (
        expected_from_warehouse_id is not None
        and existing.from_warehouse_id != expected_from_warehouse_id
    ):
        mismatches.append("from_warehouse_id")
    if (
        expected_to_warehouse_id is not None
        and existing.to_warehouse_id != expected_to_warehouse_id
    ):
        mismatches.append("to_warehouse_id")

    if mismatches:
        raise IdempotencyConflictError(
            f"idempotency_key '{idempotency_key}' уже использован с другими параметрами: "
            f"{', '.join(mismatches)}"
        )
    return existing


def _to_movement_response(mv: Movement) -> MovementResponse:
    """Сконвертировать ORM-модель в Response-схему."""
    return MovementResponse.model_validate(mv)


# ── Операции ──────────────────────────────────────────────────────


async def income(session: AsyncSession, req: IncomeRequest) -> MovementResponse:
    """
    Приход товара на склад. Идемпотентен по idempotency_key.
    Увеличивает quantity, пишет Movement(INCOME, COMPLETED).
    """
    existing = await _find_or_conflict(
        session,
        idempotency_key=req.idempotency_key,
        expected_type=MovementType.INCOME,
        expected_product_id=req.product_id,
        expected_quantity=req.quantity,
        expected_to_warehouse_id=req.warehouse_id,
    )
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
    existing = await _find_or_conflict(
        session,
        idempotency_key=req.idempotency_key,
        expected_type=MovementType.FBS_RESERVE,
        expected_product_id=req.product_id,
        expected_quantity=req.quantity,
        expected_from_warehouse_id=req.warehouse_id,
    )
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


async def ship_fbo(session: AsyncSession, req: ShipFboRequest) -> MovementResponse:
    """
    Отправка на маркетплейс по FBO. Списывает товар с ФФ,
    создаёт Movement(FBO_SHIP, PENDING). Приёмка маркетплейсом — через receive().
    Идемпотентен по idempotency_key.
    """
    existing = await _find_or_conflict(
        session,
        idempotency_key=req.idempotency_key,
        expected_type=MovementType.FBO_SHIP,
        expected_product_id=req.product_id,
        expected_quantity=req.quantity,
        expected_from_warehouse_id=req.warehouse_id,
    )
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

    stock.quantity -= req.quantity
    # reserved_fbs не трогаем — FBO не про резерв

    mv = Movement(
        type=MovementType.FBO_SHIP,
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


async def delete_warehouse(session: AsyncSession, warehouse_id: int) -> WarehouseRead:
    """
    Soft delete склада. Остатки и история сохраняются, склад скрывается из списков.
    Идемпотентно: повторное удаление не падает.
    """
    wh = await session.get(Warehouse, warehouse_id)
    if wh is None:
        raise NotFoundError(f"Склад с id={warehouse_id} не найден")

    if wh.is_deleted:
        return WarehouseRead.model_validate(wh)

    # Проверить, что на складе нет активных FBS-резервов
    reserved_total = await session.scalar(
        select(func.coalesce(func.sum(Stock.reserved_fbs), 0)).where(
            Stock.warehouse_id == warehouse_id
        )
    )
    if reserved_total > 0:
        raise ConflictError(
            f"Нельзя удалить склад: на нём {reserved_total} ед. в резерве FBS. "
            f"Отмените резервы (cancel_fbs) или отправьте их (ship_fbs)."
        )

    wh.is_deleted = True
    await session.commit()
    await session.refresh(wh)
    return WarehouseRead.model_validate(wh)


async def get_stock_report(session: AsyncSession) -> StockReportResponse:
    """
    Отчёт остатков: список stocks с available + товар в пути (PENDING movements).
    Склады с is_deleted=True не включаются.
    """
    # Items: stocks JOIN warehouses JOIN products WHERE warehouses.is_deleted=False
    stmt_items = (
        select(
            Stock,
            Warehouse.name.label("warehouse_name"),
            Product.sku,
            Product.name.label("product_name"),
        )
        .join(Warehouse, Stock.warehouse_id == Warehouse.id)
        .join(Product, Stock.product_id == Product.id)
        .where(Warehouse.is_deleted == False)
        .order_by(Warehouse.name, Product.sku)
    )
    rows = (await session.execute(stmt_items)).all()

    items = [
        StockItem(
            warehouse_id=stock.warehouse_id,
            warehouse_name=warehouse_name,
            product_id=stock.product_id,
            sku=sku,
            product_name=product_name,
            quantity=stock.quantity,
            reserved_fbs=stock.reserved_fbs,
            available=stock.quantity - stock.reserved_fbs,
        )
        for stock, warehouse_name, sku, product_name in rows
    ]

    # In-transit:
    #   TRANSFER PENDING — между ФФ
    #   FBO_SHIP PENDING — на склад маркетплейса
    #   FBS_SHIP COMPLETED — к клиенту (нет отметки «доставлено», висит навсегда)
    WarehouseFrom = aliased(Warehouse, name="from_wh")
    WarehouseTo = aliased(Warehouse, name="to_wh")
    stmt_transit = (
        select(
            Movement,
            Product.sku,
            Product.name.label("product_name"),
            WarehouseFrom.name.label("from_warehouse_name"),
            WarehouseTo.name.label("to_warehouse_name"),
        )
        .join(Product, Movement.product_id == Product.id)
        .outerjoin(WarehouseFrom, Movement.from_warehouse_id == WarehouseFrom.id)
        .outerjoin(WarehouseTo, Movement.to_warehouse_id == WarehouseTo.id)
        .where(
            or_(
                and_(
                    Movement.status == MovementStatus.PENDING,
                    Movement.type.in_([MovementType.TRANSFER, MovementType.FBO_SHIP]),
                ),
                and_(
                    Movement.status == MovementStatus.COMPLETED,
                    Movement.type == MovementType.FBS_SHIP,
                ),
            )
        )
        .order_by(Movement.created_at)
    )
    transit_rows = (await session.execute(stmt_transit)).all()

    def _direction(mv_type: MovementType) -> str:
        if mv_type == MovementType.TRANSFER:
            return "between_warehouses"
        if mv_type == MovementType.FBO_SHIP:
            return "to_marketplace"
        if mv_type == MovementType.FBS_SHIP:
            return "to_customer"
        raise ValueError(f"unexpected type in transit: {mv_type}")

    from app.schemas import InTransitItem

    in_transit = [
        InTransitItem(
            movement_id=mv.id,
            type=mv.type,
            direction=_direction(mv.type),
            product_id=mv.product_id,
            sku=sku,
            product_name=product_name,
            quantity=mv.quantity,
            from_warehouse_name=from_warehouse_name,
            to_warehouse_name=to_warehouse_name,
            created_at=mv.created_at,
        )
        for mv, sku, product_name, from_warehouse_name, to_warehouse_name in transit_rows
    ]

    return StockReportResponse(items=items, in_transit=in_transit)


async def transfer(session: AsyncSession, req: TransferRequest) -> MovementResponse:
    """
    Перемещение между ФФ. Списывает с from_wh, создаёт Movement(TRANSFER, PENDING).
    На to_wh НЕ зачисляет — только при receive().
    Блокирует обе строки stocks в порядке возрастания warehouse_id (защита от deadlock).
    """
    existing = await _find_or_conflict(
        session,
        idempotency_key=req.idempotency_key,
        expected_type=MovementType.TRANSFER,
        expected_product_id=req.product_id,
        expected_quantity=req.quantity,
        expected_from_warehouse_id=req.from_warehouse_id,
        expected_to_warehouse_id=req.to_warehouse_id,
    )
    if existing is not None:
        return _to_movement_response(existing)

    from_wh = await _get_warehouse_or_404(session, req.from_warehouse_id)
    await _get_warehouse_or_404(session, req.to_warehouse_id)
    await _get_product_or_404(session, req.product_id)

    # Блокировка в порядке возрастания warehouse_id предотвращает deadlock
    # между transfer(A→B) и transfer(B→A).
    # Блокируем обе строки в порядке возрастания warehouse_id (защита от deadlock).
    # _to_stock нужен только для FOR UPDATE — модификация будет в receive().
    lo_id = min(req.from_warehouse_id, req.to_warehouse_id)
    hi_id = max(req.from_warehouse_id, req.to_warehouse_id)
    lo_stock = await _get_or_create_stock(session, lo_id, req.product_id)
    hi_stock = await _get_or_create_stock(session, hi_id, req.product_id)

    if req.from_warehouse_id == lo_id:
        from_stock, _to_stock = lo_stock, hi_stock
    else:
        from_stock, _to_stock = hi_stock, lo_stock

    available = from_stock.quantity - from_stock.reserved_fbs
    if available < req.quantity:
        wh_name = from_wh.name
        await session.rollback()
        raise InsufficientStockError(
            warehouse_name=wh_name,
            available=available,
            requested=req.quantity,
        )

    from_stock.quantity -= req.quantity
    # to_stock НЕ трогаем — товар в пути

    mv = Movement(
        type=MovementType.TRANSFER,
        status=MovementStatus.PENDING,
        product_id=req.product_id,
        quantity=req.quantity,
        from_warehouse_id=req.from_warehouse_id,
        to_warehouse_id=req.to_warehouse_id,
        idempotency_key=req.idempotency_key,
    )
    session.add(mv)
    await session.commit()
    await session.refresh(mv)
    return _to_movement_response(mv)


async def receive(session: AsyncSession, movement_id: int) -> MovementResponse:
    """
    Приёмка товара. Для TRANSFER — зачисляет на to_warehouse.
    Для FBO_SHIP — просто помечает COMPLETED (товар у маркетплейса, stocks не трогаем).
    Идемпотентность по статусу: COMPLETED → вернуть, CANCELLED → 409.
    """
    mv = await session.get(Movement, movement_id)
    if mv is None:
        raise NotFoundError(f"Movement с id={movement_id} не найден")

    if mv.status == MovementStatus.COMPLETED:
        return _to_movement_response(mv)

    if mv.type not in (MovementType.TRANSFER, MovementType.FBO_SHIP):
        raise InvalidStatusError(
            f"receive поддерживает TRANSFER и FBO_SHIP, получен {mv.type}"
        )

    if mv.status == MovementStatus.CANCELLED:
        raise InvalidStatusError("Нельзя принять отменённое движение")

    # mv.status == PENDING
    if mv.type == MovementType.TRANSFER:
        to_stock = await _get_or_create_stock(
            session, mv.to_warehouse_id, mv.product_id
        )
        to_stock.quantity += mv.quantity
    # FBO_SHIP: ничего не делаем со stocks — товар у маркетплейса

    mv.status = MovementStatus.COMPLETED
    mv.completed_at = datetime.now(UTC)

    await session.commit()
    await session.refresh(mv)
    return _to_movement_response(mv)


# ── CRUD-функции для роутеров ────────────────────────────────────────


async def create_warehouse(
    session: AsyncSession, req: WarehouseCreate
) -> WarehouseRead:
    """Создать новый склад. Проверяет уникальность имени."""
    stmt = select(Warehouse).where(Warehouse.name == req.name)
    existing = await session.scalar(stmt)
    if existing is not None:
        raise ConflictError(f"Склад «{req.name}» уже существует")

    wh = Warehouse(name=req.name)
    session.add(wh)
    await session.commit()
    await session.refresh(wh)
    return WarehouseRead.model_validate(wh)


async def list_warehouses(session: AsyncSession) -> list[WarehouseRead]:
    """Вернуть все активные склады (is_deleted=False)."""
    stmt = select(Warehouse).where(Warehouse.is_deleted == False).order_by(Warehouse.id)
    result = await session.execute(stmt)
    return [WarehouseRead.model_validate(w) for w in result.scalars()]


async def update_warehouse(
    session: AsyncSession, warehouse_id: int, req: WarehouseUpdate
) -> WarehouseRead:
    """Переименовать склад. Проверяет уникальность нового имени."""
    wh = await session.get(Warehouse, warehouse_id)
    if wh is None or wh.is_deleted:
        raise NotFoundError(f"Склад с id={warehouse_id} не найден")

    stmt = select(Warehouse).where(
        Warehouse.name == req.name, Warehouse.id != warehouse_id
    )
    existing = await session.scalar(stmt)
    if existing is not None:
        raise ConflictError(f"Склад «{req.name}» уже существует")

    wh.name = req.name
    await session.commit()
    await session.refresh(wh)
    return WarehouseRead.model_validate(wh)


async def create_product(session: AsyncSession, req: ProductCreate) -> ProductRead:
    """Создать новый товар. Проверяет уникальность SKU."""
    stmt = select(Product).where(Product.sku == req.sku)
    existing = await session.scalar(stmt)
    if existing is not None:
        raise ConflictError(f"Товар с SKU «{req.sku}» уже существует")

    prod = Product(sku=req.sku, name=req.name)
    session.add(prod)
    await session.commit()
    await session.refresh(prod)
    return ProductRead.model_validate(prod)


async def list_products(session: AsyncSession) -> list[ProductRead]:
    """Вернуть все товары."""
    stmt = select(Product).order_by(Product.id)
    result = await session.execute(stmt)
    return [ProductRead.model_validate(p) for p in result.scalars()]


async def list_movements(
    session: AsyncSession,
    status: MovementStatus | None = None,
    type_: MovementType | None = None,
    product_id: int | None = None,
) -> MovementsListResponse:
    """Вернуть список движений с опциональными фильтрами."""
    stmt = select(Movement)
    if status is not None:
        stmt = stmt.where(Movement.status == status)
    if type_ is not None:
        stmt = stmt.where(Movement.type == type_)
    if product_id is not None:
        stmt = stmt.where(Movement.product_id == product_id)
    stmt = stmt.order_by(Movement.created_at.desc(), Movement.id.desc())

    result = await session.execute(stmt)
    movements = list(result.scalars())
    return MovementsListResponse(
        items=[MovementResponse.model_validate(m) for m in movements],
        total=len(movements),
    )
