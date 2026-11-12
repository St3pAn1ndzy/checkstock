"""Pydantic-схемы запросов и ответов для всех эндпоинтов."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.enums import MovementStatus, MovementType

__all__ = [
    "ErrorResponse",
    "InTransitItem",
    "IncomeRequest",
    "MovementResponse",
    "MovementsListResponse",
    "ProductCreate",
    "ProductRead",
    "ReserveFbsRequest",
    "ShipFboRequest",
    "StockItem",
    "StockReportResponse",
    "TransferRequest",
    "WarehouseCreate",
    "WarehouseRead",
    "WarehouseUpdate",
]

# ── Склады ────────────────────────────────────────────────────────


class WarehouseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class WarehouseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    is_deleted: bool
    created_at: datetime


class WarehouseUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


# ── Товары ────────────────────────────────────────────────────────


class ProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=200)


class ProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku: str
    name: str


# ── Отчёт остатков ────────────────────────────────────────────────


class StockItem(BaseModel):
    warehouse_id: int
    warehouse_name: str
    product_id: int
    sku: str
    product_name: str
    quantity: int
    reserved_fbs: int
    available: int


class InTransitItem(BaseModel):
    movement_id: int
    type: MovementType
    product_id: int
    sku: str
    product_name: str
    quantity: int
    from_warehouse_name: str | None
    to_warehouse_name: str | None
    created_at: datetime


class StockReportResponse(BaseModel):
    items: list[StockItem]
    in_transit: list[InTransitItem]


# ── Операции ──────────────────────────────────────────────────────


class IncomeRequest(BaseModel):
    warehouse_id: int
    product_id: int
    quantity: int = Field(gt=0)
    idempotency_key: str = Field(min_length=1, max_length=100)


class ReserveFbsRequest(BaseModel):
    warehouse_id: int
    product_id: int
    quantity: int = Field(gt=0)
    idempotency_key: str = Field(min_length=1, max_length=100)


class TransferRequest(BaseModel):
    from_warehouse_id: int
    to_warehouse_id: int
    product_id: int
    quantity: int = Field(gt=0)
    idempotency_key: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _from_ne_to(self):
        if self.from_warehouse_id == self.to_warehouse_id:
            raise ValueError("from_warehouse_id и to_warehouse_id должны различаться")
        return self


class ShipFboRequest(BaseModel):
    warehouse_id: int
    product_id: int
    quantity: int = Field(gt=0)
    idempotency_key: str = Field(min_length=1, max_length=100)


# ── Ответы ────────────────────────────────────────────────────────


class MovementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: MovementType
    status: MovementStatus
    product_id: int
    quantity: int
    from_warehouse_id: int | None
    to_warehouse_id: int | None
    created_at: datetime
    completed_at: datetime | None


class MovementsListResponse(BaseModel):
    items: list[MovementResponse]
    total: int


# ── Ошибки ────────────────────────────────────────────────────────


class ErrorResponse(BaseModel):
    code: str
    message: str
    context: dict = {}
