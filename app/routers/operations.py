"""Роутер для операций с движениями."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.enums import MovementStatus, MovementType
from app.schemas import (
    IncomeRequest,
    MovementResponse,
    MovementsListResponse,
    ReserveFbsRequest,
    ShipFboRequest,
    TransferRequest,
)
from app.services import (
    cancel_fbs,
    income,
    list_movements,
    receive,
    reserve_fbs,
    ship_fbo,
    ship_fbs,
    transfer,
)

router = APIRouter(tags=["operations"])


@router.post("/operations/income", response_model=MovementResponse)
async def op_income(payload: IncomeRequest, db: AsyncSession = Depends(get_db)):
    return await income(db, payload)


@router.post("/operations/reserve-fbs", response_model=MovementResponse)
async def op_reserve_fbs(
    payload: ReserveFbsRequest,
    db: AsyncSession = Depends(get_db),
):
    return await reserve_fbs(db, payload)


@router.post("/operations/ship-fbs/{movement_id}", response_model=MovementResponse)
async def op_ship_fbs(movement_id: int, db: AsyncSession = Depends(get_db)):
    return await ship_fbs(db, movement_id)


@router.post("/operations/cancel-fbs/{movement_id}", response_model=MovementResponse)
async def op_cancel_fbs(movement_id: int, db: AsyncSession = Depends(get_db)):
    return await cancel_fbs(db, movement_id)


@router.post("/operations/transfer", response_model=MovementResponse)
async def op_transfer(payload: TransferRequest, db: AsyncSession = Depends(get_db)):
    return await transfer(db, payload)


@router.post("/operations/receive/{movement_id}", response_model=MovementResponse)
async def op_receive(movement_id: int, db: AsyncSession = Depends(get_db)):
    return await receive(db, movement_id)


@router.post("/operations/ship-fbo", response_model=MovementResponse)
async def op_ship_fbo(payload: ShipFboRequest, db: AsyncSession = Depends(get_db)):
    return await ship_fbo(db, payload)


@router.get("/movements", response_model=MovementsListResponse)
async def get_movements(
    status: MovementStatus | None = None,
    type_: MovementType | None = Query(None, alias="type"),
    product_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    return await list_movements(db, status=status, type_=type_, product_id=product_id)
