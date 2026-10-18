
from datetime import date
from decimal import Decimal

from sqlalchemy import String, Date, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Bill(Base):
    __tablename__ = "bills"

    id: Mapped[int] = mapped_column(
        primary_key=True, index=True
    )

    bill_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False
    )

    due_date: Mapped[date] = mapped_column(
        Date, nullable=False, index=True
    )

    status: Mapped[str] = mapped_column(
        String(20), default="pending", nullable=False
    )

    notes: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
