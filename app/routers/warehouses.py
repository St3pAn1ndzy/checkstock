"""Роутер для CRUD складов."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import WarehouseCreate, WarehouseRead, WarehouseUpdate
from app.services import (
    create_warehouse,
    delete_warehouse,
    list_warehouses,
    update_warehouse,
)

router = APIRouter(prefix="/warehouses", tags=["warehouses"])


@router.post("", response_model=WarehouseRead, status_code=201)
async def create(payload: WarehouseCreate, db: AsyncSession = Depends(get_db)):
    return await create_warehouse(db, payload)


@router.get("", response_model=list[WarehouseRead])
async def list_all(db: AsyncSession = Depends(get_db)):
    return await list_warehouses(db)


@router.patch("/{warehouse_id}", response_model=WarehouseRead)
async def rename(
    warehouse_id: int,
    payload: WarehouseUpdate,
    db: AsyncSession = Depends(get_db),
):
    return await update_warehouse(db, warehouse_id, payload)


@router.delete("/{warehouse_id}", response_model=WarehouseRead)
async def remove(warehouse_id: int, db: AsyncSession = Depends(get_db)):
    return await delete_warehouse(db, warehouse_id)
