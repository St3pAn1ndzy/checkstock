from datetime import datetime
from enum import Enum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class MovementType(str, Enum):
    INCOME = "INCOME"
    TRANSFER = "TRANSFER"
    FBS_RESERVE = "FBS_RESERVE"
    FBS_CANCEL = "FBS_CANCEL"
    FBS_SHIP = "FBS_SHIP"
    FBO_SHIP = "FBO_SHIP"
    RECEIVE = "RECEIVE"


class MovementStatus(str, Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class Warehouse(Base):
    __tablename__ = "warehouses"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    is_deleted: Mapped[bool] = mapped_column(default=False, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    stocks: Mapped[list["Stock"]] = relationship(back_populates="warehouse")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    stocks: Mapped[list["Stock"]] = relationship(back_populates="product")


class Stock(Base):
    __tablename__ = "stocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    warehouse_id: Mapped[int] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(default=0, nullable=False, server_default=text("0"))
    reserved_fbs: Mapped[int] = mapped_column(default=0, nullable=False, server_default=text("0"))

    warehouse: Mapped["Warehouse"] = relationship(back_populates="stocks")
    product: Mapped["Product"] = relationship(back_populates="stocks")

    __table_args__ = (
        UniqueConstraint("warehouse_id", "product_id", name="uq_stock_warehouse_product"),
        CheckConstraint("quantity >= 0", name="ck_stock_quantity_non_negative"),
        CheckConstraint("reserved_fbs >= 0", name="ck_stock_reserved_non_negative"),
        CheckConstraint("reserved_fbs <= quantity", name="ck_stock_reserved_lte_quantity"),
    )


class Movement(Base):
    __tablename__ = "movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[MovementType] = mapped_column(
        SAEnum(MovementType, name="movement_type"), nullable=False, index=True
    )
    status: Mapped[MovementStatus] = mapped_column(
        SAEnum(MovementStatus, name="movement_status"),
        nullable=False,
        index=True,
        default=MovementStatus.PENDING,
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(nullable=False)
    from_warehouse_id: Mapped[int | None] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=True
    )
    to_warehouse_id: Mapped[int | None] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(
        String(100), unique=True, nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_movement_quantity_positive"),
    )
