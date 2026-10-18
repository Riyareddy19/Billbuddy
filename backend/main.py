
import hmac
import os
import secrets
import time
from contextlib import asynccontextmanager
from datetime import date

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from psycopg import connect
from psycopg.rows import dict_row


SESSION_COOKIE = "billbuddy_session"
SESSION_MAX_AGE = 12 * 60 * 60


def database_url() -> str:
    configured_url = os.getenv("DATABASE_URL")
    if not configured_url:
        raise RuntimeError("DATABASE_URL is not configured")
    return configured_url


def frontend_origins() -> list[str]:
    configured_origins = os.getenv("BILLBUDDY_FRONTEND_ORIGINS")
    if configured_origins is None:
        return [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]

    origins = [
        origin.strip().rstrip("/")
        for origin in configured_origins.split(",")
        if origin.strip()
    ]
    if not origins:
        raise RuntimeError("BILLBUDDY_FRONTEND_ORIGINS must not be empty")
    return origins


def connect_db():
    return connect(database_url(), row_factory=dict_row)


def create_dashboard_session(expires_at: int, secret: str) -> str:
    signature = hmac.new(
        secret.encode(),
        str(expires_at).encode(),
        "sha256",
    ).hexdigest()
    return f"{expires_at}.{signature}"


def verify_dashboard_session(session: str | None) -> bool:
    secret = os.getenv("BILLBUDDY_SESSION_SECRET")
    if not secret or not session:
        return False

    try:
        expires_at_text, signature = session.split(".", maxsplit=1)
        expires_at = int(expires_at_text)
    except ValueError:
        return False

    if expires_at <= int(time.time()):
        return False

    expected_signature = create_dashboard_session(expires_at, secret).split(
        ".", maxsplit=1
    )[1]
    return secrets.compare_digest(signature, expected_signature)


def dashboard_cookie_is_secure() -> bool:
    return os.getenv("BILLBUDDY_COOKIE_SECURE", "false").lower() == "true"


def dashboard_cookie_samesite() -> str:
    samesite = os.getenv("BILLBUDDY_COOKIE_SAMESITE", "lax").lower()
    if samesite not in {"lax", "strict", "none"}:
        raise RuntimeError("BILLBUDDY_COOKIE_SAMESITE must be lax, strict, or none")
    if samesite == "none" and not dashboard_cookie_is_secure():
        raise RuntimeError("SameSite=None requires BILLBUDDY_COOKIE_SECURE=true")
    return samesite


def verify_api_key(
    x_billbuddy_token: str | None = Header(default=None),
    billbuddy_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
):
    expected_token = os.getenv("BILLBUDDY_API_KEY")

    if expected_token and x_billbuddy_token and secrets.compare_digest(
        x_billbuddy_token.encode(), expected_token.encode()
    ):
        return

    if verify_dashboard_session(billbuddy_session):
        return

    if not expected_token:
        raise HTTPException(
            status_code=500,
            detail="API authentication is not configured",
        )

    raise HTTPException(
        status_code=401,
        detail="Invalid or missing authentication",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    with connect_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS bills (
                id BIGSERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                amount DOUBLE PRECISION NOT NULL CHECK(amount > 0),
                due_date DATE NOT NULL,
                paid BOOLEAN NOT NULL DEFAULT FALSE
            )
        """)
    yield


app = FastAPI(
    title="BillBuddy API",
    version="1.0.0",
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class BillCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(default="Other", max_length=50)
    amount: float = Field(gt=0, le=100000000)
    due_date: date
    paid: bool = False


class BillUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = Field(default=None, max_length=50)
    amount: float | None = Field(default=None, gt=0, le=100000000)
    due_date: date | None = None
    paid: bool | None = None


class DashboardLogin(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class BillDelete(BaseModel):
    bill_id: int = Field(
        gt=0,
        strict=True,
        description="Positive integer ID or decimal-digit string identifying the bill to delete.",
    )

    @field_validator("bill_id", mode="before")
    @classmethod
    def normalize_bill_id(cls, value):
        if isinstance(value, bool):
            raise ValueError("bill_id must be a positive integer or decimal-digit string")

        if isinstance(value, int):
            bill_id = value
        elif isinstance(value, str) and value.isascii() and value.isdigit():
            bill_id = int(value)
        else:
            raise ValueError("bill_id must be a positive integer or decimal-digit string")

        if bill_id <= 0:
            raise ValueError("bill_id must be greater than zero")

        return bill_id

    model_config = {
        "json_schema_extra": {
            "example": {"bill_id": 123},
        }
    }


def serialize_bill(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "category": row["category"],
        "amount": row["amount"],
        "due_date": row["due_date"].isoformat(),
        "paid": bool(row["paid"]),
    }


def get_bill_or_404(db, bill_id):
    row = db.execute(
        "SELECT * FROM bills WHERE id = %s",
        (bill_id,),
    ).fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Bill not found",
        )

    return row


@app.get("/")
def home():
    return {"message": "BillBuddy API is running"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/auth/session")
def dashboard_session_status(
    billbuddy_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
):
    return {"authenticated": verify_dashboard_session(billbuddy_session)}


@app.post("/auth/login")
def dashboard_login(credentials: DashboardLogin, response: Response):
    expected_password = os.getenv("BILLBUDDY_DASHBOARD_PASSWORD")
    session_secret = os.getenv("BILLBUDDY_SESSION_SECRET")

    if not expected_password or not session_secret:
        raise HTTPException(
            status_code=500,
            detail="Dashboard authentication is not configured",
        )

    if not secrets.compare_digest(
        credentials.password.encode(),
        expected_password.encode(),
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid dashboard password",
        )

    expires_at = int(time.time()) + SESSION_MAX_AGE
    response.set_cookie(
        key=SESSION_COOKIE,
        value=create_dashboard_session(expires_at, session_secret),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=dashboard_cookie_is_secure(),
        samesite=dashboard_cookie_samesite(),
        path="/",
    )
    return {"authenticated": True}


@app.post("/auth/logout")
def dashboard_logout(response: Response):
    response.delete_cookie(
        key=SESSION_COOKIE,
        httponly=True,
        secure=dashboard_cookie_is_secure(),
        samesite=dashboard_cookie_samesite(),
        path="/",
    )
    return {"authenticated": False}


@app.get("/bills")
def list_bills(_: None = Depends(verify_api_key)):
    with connect_db() as db:
        rows = db.execute(
            "SELECT * FROM bills ORDER BY due_date ASC, id ASC"
        ).fetchall()

    return [serialize_bill(row) for row in rows]


@app.post("/bills", status_code=201)
def create_bill(
    bill: BillCreate,
    _: None = Depends(verify_api_key),
):
    name = bill.name.strip()

    if not name:
        raise HTTPException(
            status_code=422,
            detail="Bill name is required",
        )

    with connect_db() as db:
        cursor = db.execute(
            """
            INSERT INTO bills (name, category, amount, due_date, paid)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                name,
                bill.category,
                bill.amount,
                bill.due_date,
                bill.paid,
            ),
        )

        row = cursor.fetchone()

    return serialize_bill(row)


@app.patch("/bills/{bill_id}")
def update_bill(
    bill_id: int,
    changes: BillUpdate,
    _: None = Depends(verify_api_key),
):
    updates = changes.model_dump(exclude_unset=True)

    if not updates:
        raise HTTPException(
            status_code=400,
            detail="No changes supplied",
        )

    if "name" in updates:
        updates["name"] = updates["name"].strip()

        if not updates["name"]:
            raise HTTPException(
                status_code=422,
                detail="Bill name is required",
            )

    with connect_db() as db:
        get_bill_or_404(db, bill_id)

        assignments = ", ".join(
            f"{field} = %s" for field in updates
        )
        values = list(updates.values()) + [bill_id]

        db.execute(
            f"UPDATE bills SET {assignments} WHERE id = %s",
            values,
        )

        row = get_bill_or_404(db, bill_id)

    return serialize_bill(row)


def delete_bill_by_id(bill_id: int):
    with connect_db() as db:
        result = db.execute(
            "DELETE FROM bills WHERE id = %s RETURNING id",
            (bill_id,),
        )
        if result.fetchone() is None:
            raise HTTPException(
                status_code=404,
                detail="Bill not found",
            )

    return {"message": "Bill deleted successfully"}


@app.post(
    "/bills/delete",
    summary="Delete a bill (Bolna-compatible)",
    description="Deletes the bill identified by the positive integer `bill_id` in the JSON body.",
)
def delete_bill_from_body(
    request: BillDelete,
    _: None = Depends(verify_api_key),
):
    return delete_bill_by_id(request.bill_id)


@app.delete("/bills/{bill_id}")
def delete_bill(
    bill_id: int,
    _: None = Depends(verify_api_key),
):
    return delete_bill_by_id(bill_id)


@app.get("/summary")
def get_summary(_: None = Depends(verify_api_key)):
    today = date.today().isoformat()
    month = today[:7]

    with connect_db() as db:
        rows = db.execute(
            "SELECT * FROM bills"
        ).fetchall()

    bills = [serialize_bill(row) for row in rows]
    unpaid = [bill for bill in bills if not bill["paid"]]
    overdue = [
        bill for bill in unpaid
        if bill["due_date"] < today
    ]
    upcoming = [
        bill for bill in unpaid
        if bill["due_date"] >= today
    ]

    return {
        "total_bills": len(bills),
        "unpaid_count": len(unpaid),
        "outstanding": sum(bill["amount"] for bill in unpaid),
        "overdue_count": len(overdue),
        "overdue_amount": sum(bill["amount"] for bill in overdue),
        "upcoming_amount": sum(bill["amount"] for bill in upcoming),
        "month_amount": sum(
            bill["amount"]
            for bill in bills
            if bill["due_date"].startswith(month)
        ),
    }
