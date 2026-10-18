
import os
import secrets
from datetime import date, timedelta
from typing import Annotated

from dotenv import load_dotenv
from fastapi import (
    FastAPI,
    Depends,
    HTTPException,
    Query,
    Response,
    Header,
    status,
)
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from app.database import Base, engine, get_db
from app.models import Bill
from app.schemas import BillCreate, BillUpdate, BillResponse


# Load environment variables
load_dotenv()

API_KEY = os.getenv("BILLBUDDY_API_KEY")

if not API_KEY:
    raise RuntimeError(
        "BILLBUDDY_API_KEY is missing from your .env file"
    )


# Create database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="BillBuddy API")

DB = Annotated[Session, Depends(get_db)]


# Verify API key
def verify_api_key(
    x_api_key: Annotated[str, Header(alias="X-API-Key")]
):
    if not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )


# Public health check
@app.get("/")
def home():
    return {"message": "Welcome to BillBuddy API"}


# Add a new bill
@app.post(
    "/bills",
    response_model=BillResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_api_key)],
)
def create_bill(data: BillCreate, db: DB):
    bill = Bill(**data.model_dump())
    db.add(bill)
    db.commit()
    db.refresh(bill)
    return bill


# View all bills, optionally filtered
@app.get(
    "/bills",
    response_model=list[BillResponse],
    dependencies=[Depends(verify_api_key)],
)
def list_bills(
    db: DB,
    status_filter: str | None = Query(
        default=None, alias="status"
    ),
    bill_type: str | None = Query(default=None),
):
    query = select(Bill)

    if status_filter is not None:
        if status_filter not in {"pending", "paid"}:
            raise HTTPException(400, "Invalid status")
        query = query.where(Bill.status == status_filter)

    if bill_type:
        query = query.where(
            Bill.bill_type.ilike(f"%{bill_type}%")
        )

    return db.scalars(
        query.order_by(Bill.due_date)
    ).all()


# View upcoming unpaid bills
@app.get(
    "/bills/upcoming",
    response_model=list[BillResponse],
    dependencies=[Depends(verify_api_key)],
)
def upcoming_bills(
    db: DB,
    days: int = Query(default=7, ge=1, le=365),
):
    today = date.today()
    end_date = today + timedelta(days=days)

    query = (
        select(Bill)
        .where(
            Bill.status == "pending",
            Bill.due_date >= today,
            Bill.due_date <= end_date,
        )
        .order_by(Bill.due_date)
    )

    return db.scalars(query).all()


# Get bill totals
@app.get(
    "/bills/summary",
    dependencies=[Depends(verify_api_key)],
)
def bill_summary(db: DB):
    result = db.execute(
        select(
            func.count(Bill.id),
            func.coalesce(func.sum(Bill.amount), 0),
        ).where(Bill.status == "pending")
    ).one()

    return {
        "pending_count": result[0],
        "pending_total": result[1],
    }


# View a single bill
@app.get(
    "/bills/{bill_id}",
    response_model=BillResponse,
    dependencies=[Depends(verify_api_key)],
)
def get_bill(bill_id: int, db: DB):
    bill = db.get(Bill, bill_id)

    if bill is None:
        raise HTTPException(404, "Bill not found")

    return bill


# Update a bill
@app.patch(
    "/bills/{bill_id}",
    response_model=BillResponse,
    dependencies=[Depends(verify_api_key)],
)
def update_bill(
    bill_id: int,
    data: BillUpdate,
    db: DB,
):
    bill = db.get(Bill, bill_id)

    if bill is None:
        raise HTTPException(404, "Bill not found")

    changes = data.model_dump(exclude_unset=True)

    for field, value in changes.items():
        setattr(bill, field, value)

    db.commit()
    db.refresh(bill)
    return bill


# Delete a bill
@app.delete(
    "/bills/{bill_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_api_key)],
)
def delete_bill(bill_id: int, db: DB):
    bill = db.get(Bill, bill_id)

    if bill is None:
        raise HTTPException(404, "Bill not found")

    db.delete(bill)
    db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Mark a bill as paid
@app.patch(
    "/bills/{bill_id}/pay",
    response_model=BillResponse,
    dependencies=[Depends(verify_api_key)],
)
def mark_paid(bill_id: int, db: DB):
    bill = db.get(Bill, bill_id)

    if bill is None:
        raise HTTPException(404, "Bill not found")

    bill.status = "paid"
    db.commit()
    db.refresh(bill)

    return bill
