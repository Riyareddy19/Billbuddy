
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, ConfigDict


class BillCreate(BaseModel):
    bill_type: str = Field(min_length=1, max_length=50)
    amount: Decimal = Field(
        gt=0, max_digits=10, decimal_places=2
    )
    due_date: date
    notes: str | None = Field(default=None, max_length=500)


class BillUpdate(BaseModel):
    bill_type: str | None = Field(
        default=None, min_length=1, max_length=50
    )
    amount: Decimal | None = Field(
        default=None, gt=0, max_digits=10, decimal_places=2
    )
    due_date: date | None = None
    status: Literal["pending", "paid"] | None = None
    notes: str | None = Field(default=None, max_length=500)


class BillResponse(BaseModel):
    id: int
    bill_type: str
    amount: Decimal
    due_date: date
    status: str
    notes: str | None

    model_config = ConfigDict(from_attributes=True)
