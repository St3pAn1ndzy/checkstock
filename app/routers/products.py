"""Роутер для CRUD товаров."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import ProductCreate, ProductRead
from app.services import create_product, list_products

router = APIRouter(prefix="/products", tags=["products"])


@router.post("", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
async def create(payload: ProductCreate, db: AsyncSession = Depends(get_db)):
    return await create_product(db, payload)


@router.get("", response_model=list[ProductRead])
async def list_all(db: AsyncSession = Depends(get_db)):
    return await list_products(db)
