"""Роутер для отчёта остатков."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import StockReportResponse
from app.services import get_stock_report

router = APIRouter(prefix="/stock", tags=["stock"])


@router.get("", response_model=StockReportResponse)
async def report(db: AsyncSession = Depends(get_db)):
    return await get_stock_report(db)
