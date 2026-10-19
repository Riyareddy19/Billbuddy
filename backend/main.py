
import hmac
import hashlib
import json
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any
from urllib.request import Request as UrlRequest, urlopen

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError, field_validator
from psycopg import connect
from psycopg.rows import dict_row
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse


SESSION_COOKIE = "billbuddy_session"
SESSION_MAX_AGE = 12 * 60 * 60
VOICE_CAPABILITY_TTL_SECONDS = 120
LOGIN_RATE_LIMIT = 5
LOGIN_RATE_WINDOW_SECONDS = 15 * 60
VOICE_RATE_LIMIT = 6
VOICE_RATE_WINDOW_SECONDS = 60
MAX_LOGIN_BODY_BYTES = 1024
FRONTEND_DIST_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"
API_ROUTE_ROOTS = {
    "auth",
    "bills",
    "docs",
    "health",
    "openapi.json",
    "redoc",
    "summary",
    "voice",
}
_VOICE_CAPABILITIES: dict[str, tuple[str, str, float]] = {}
_RATE_LIMITS: dict[tuple[str, str], tuple[int, list[float]]] = {}
_SECURITY_STATE_LOCK = threading.Lock()


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


def require_frontend_origin(request: Request):
    origin = request.headers.get("origin")
    if not origin or origin not in frontend_origins():
        raise HTTPException(
            status_code=403,
            detail="Request origin is not allowed",
        )


def request_client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def enforce_rate_limit(
    request: Request,
    *,
    action: str,
    limit: int,
    window_seconds: int,
):
    now = time.monotonic()
    key = (action, request_client_key(request))
    with _SECURITY_STATE_LOCK:
        expired_keys = [
            rate_key
            for rate_key, (rate_window, timestamps) in _RATE_LIMITS.items()
            if not timestamps or now - timestamps[-1] >= rate_window
        ]
        for rate_key in expired_keys:
            del _RATE_LIMITS[rate_key]
        if key not in _RATE_LIMITS and len(_RATE_LIMITS) >= 4096:
            raise HTTPException(
                status_code=503,
                detail="Request processing is temporarily unavailable",
            )
        _, prior_requests = _RATE_LIMITS.get(key, (window_seconds, []))
        recent_requests = [
            timestamp
            for timestamp in prior_requests
            if now - timestamp < window_seconds
        ]
        if len(recent_requests) >= limit:
            _RATE_LIMITS[key] = (window_seconds, recent_requests)
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please wait and try again.",
                headers={"Retry-After": str(window_seconds)},
            )
        recent_requests.append(now)
        _RATE_LIMITS[key] = (window_seconds, recent_requests)


def issue_voice_capability(origin: str) -> str:
    if origin not in frontend_origins():
        raise HTTPException(status_code=403, detail="Request origin is not allowed")

    token = secrets.token_urlsafe(32)
    token_digest = hashlib.sha256(token.encode()).hexdigest()
    expires_at = time.time() + VOICE_CAPABILITY_TTL_SECONDS
    with _SECURITY_STATE_LOCK:
        expired = [
            digest
            for digest, (_, _, expiry) in _VOICE_CAPABILITIES.items()
            if expiry <= time.time()
        ]
        for digest in expired:
            del _VOICE_CAPABILITIES[digest]
        if len(_VOICE_CAPABILITIES) >= 2048:
            raise HTTPException(
                status_code=503,
                detail="Voice calling is temporarily unavailable",
            )
        _VOICE_CAPABILITIES[token_digest] = (
            origin,
            "voice:session",
            expires_at,
        )
    return token


def consume_voice_capability(token: str, origin: str) -> bool:
    token_digest = hashlib.sha256(token.encode()).hexdigest()
    with _SECURITY_STATE_LOCK:
        capability = _VOICE_CAPABILITIES.get(token_digest)
        if capability is None:
            return False
        allowed_origin, scope, expires_at = capability
        if expires_at <= time.time():
            del _VOICE_CAPABILITIES[token_digest]
            return False
        if allowed_origin != origin or scope != "voice:session":
            return False
        del _VOICE_CAPABILITIES[token_digest]
        return True


async def read_json_body(request: Request, max_bytes: int) -> bytes:
    content_type = request.headers.get("content-type", "")
    if content_type.partition(";")[0].strip().lower() != "application/json":
        raise HTTPException(
            status_code=415,
            detail="Content-Type must be application/json",
        )

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise HTTPException(status_code=413, detail="Request body is too large")
        except ValueError as error:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from error

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise HTTPException(status_code=413, detail="Request body is too large")
    return bytes(body)


def require_dashboard_csrf(request: Request, session: str):
    require_frontend_origin(request)
    secret = os.getenv("BILLBUDDY_SESSION_SECRET")
    supplied_token = request.headers.get("x-billbuddy-csrf")
    if (
        not secret
        or not supplied_token
        or not secrets.compare_digest(
            supplied_token,
            create_dashboard_csrf_token(session, secret),
        )
    ):
        raise HTTPException(
            status_code=403,
            detail="Missing or invalid CSRF token",
        )


def connect_db():
    return connect(database_url(), row_factory=dict_row)


def create_dashboard_session(expires_at: int, secret: str) -> str:
    signature = hmac.new(
        secret.encode(),
        str(expires_at).encode(),
        "sha256",
    ).hexdigest()
    return f"{expires_at}.{signature}"


def create_dashboard_csrf_token(session: str, secret: str) -> str:
    return hmac.new(
        secret.encode(),
        f"csrf:{session}".encode(),
        "sha256",
    ).hexdigest()


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
    request: Request,
    x_billbuddy_token: str | None = Header(default=None),
    billbuddy_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
):
    expected_token = os.getenv("BILLBUDDY_API_KEY")

    if expected_token and x_billbuddy_token and secrets.compare_digest(
        x_billbuddy_token.encode(), expected_token.encode()
    ):
        return

    if verify_dashboard_session(billbuddy_session):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            require_dashboard_csrf(request, billbuddy_session)
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


class BolnaWebCallSession(BaseModel):
    run_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    sip_username: str = Field(min_length=1)
    sip_password: str = Field(min_length=1)
    sip_domain: str = Field(min_length=1)
    wss_url: str = Field(min_length=1)
    sip_register: bool
    expires_in: int = Field(gt=0)
    ice_servers: list[dict[str, Any]]


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


def mint_bolna_session() -> BolnaWebCallSession:
    api_key = os.getenv("BOLNA_API_KEY")
    agent_id = os.getenv("BOLNA_AGENT_ID")
    if not api_key or not agent_id:
        raise HTTPException(
            status_code=503,
            detail="Voice calling is not configured",
        )

    request = UrlRequest(
        "https://api.bolna.ai/web-call/freeswitch-session",
        data=json.dumps({"agent_id": agent_id}).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=15) as response:
            session_data = json.loads(response.read())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(
            status_code=502,
            detail="Could not create a voice session",
        ) from None

    try:
        session = BolnaWebCallSession.model_validate(session_data)
    except ValidationError:
        raise HTTPException(
            status_code=502,
            detail="Bolna returned an invalid voice session",
        ) from None

    if session.agent_id != agent_id:
        raise HTTPException(
            status_code=502,
            detail="Bolna returned a session for an unexpected agent",
        )

    return session


@app.get("/")
def home(request: Request):
    accept = request.headers.get("accept", "")
    if "text/html" not in accept.lower():
        return {"message": "BillBuddy API is running"}

    index_file = FRONTEND_DIST_DIR / "index.html"
    if not index_file.is_file():
        raise HTTPException(
            status_code=503,
            detail="Frontend assets are not available",
        )
    return FileResponse(index_file)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/auth/session")
def dashboard_session_status(
    response: Response,
    billbuddy_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
):
    response.headers["Cache-Control"] = "no-store"
    authenticated = verify_dashboard_session(billbuddy_session)
    csrf_token = None
    if authenticated and billbuddy_session:
        session_secret = os.getenv("BILLBUDDY_SESSION_SECRET")
        if session_secret:
            csrf_token = create_dashboard_csrf_token(
                billbuddy_session,
                session_secret,
            )
    return {"authenticated": authenticated, "csrf_token": csrf_token}


@app.post("/voice/session", response_model=BolnaWebCallSession)
async def create_voice_session(
    request: Request,
    authorization: str | None = Header(default=None),
):
    require_frontend_origin(request)
    enforce_rate_limit(
        request,
        action="voice-session",
        limit=VOICE_RATE_LIMIT,
        window_seconds=VOICE_RATE_WINDOW_SECONDS,
    )
    await read_json_body(request, max_bytes=0)

    scheme, separator, token = (authorization or "").partition(" ")
    if (
        not separator
        or scheme.lower() != "bearer"
        or not token
        or len(token) > 256
        or not token.isascii()
    ):
        raise HTTPException(
            status_code=401,
            detail="A valid voice capability is required",
        )
    origin = request.headers["origin"]
    if not consume_voice_capability(token, origin):
        raise HTTPException(
            status_code=401,
            detail="Voice capability is expired, invalid, or already used",
        )

    return await run_in_threadpool(mint_bolna_session)


@app.post("/auth/login")
async def dashboard_login(
    request: Request,
    response: Response,
):
    require_frontend_origin(request)
    enforce_rate_limit(
        request,
        action="dashboard-login",
        limit=LOGIN_RATE_LIMIT,
        window_seconds=LOGIN_RATE_WINDOW_SECONDS,
    )
    response.headers["Cache-Control"] = "no-store"

    body = await read_json_body(request, max_bytes=MAX_LOGIN_BODY_BYTES)
    try:
        credentials = DashboardLogin.model_validate_json(body)
    except ValidationError:
        raise HTTPException(status_code=422, detail="Invalid login request") from None

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

    origin = request.headers["origin"]
    voice_capability = issue_voice_capability(origin)
    expires_at = int(time.time()) + SESSION_MAX_AGE
    session = create_dashboard_session(expires_at, session_secret)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=dashboard_cookie_is_secure(),
        samesite=dashboard_cookie_samesite(),
        path="/",
    )
    return {
        "authenticated": True,
        "csrf_token": create_dashboard_csrf_token(session, session_secret),
        "voice_capability": voice_capability,
        "voice_capability_expires_in": VOICE_CAPABILITY_TTL_SECONDS,
    }


@app.post("/auth/logout")
def dashboard_logout(
    request: Request,
    response: Response,
    billbuddy_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
):
    require_frontend_origin(request)
    if verify_dashboard_session(billbuddy_session) and billbuddy_session:
        require_dashboard_csrf(request, billbuddy_session)
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


@app.get("/{frontend_path:path}", include_in_schema=False)
def serve_frontend_route(frontend_path: str):
    first_path_component = frontend_path.partition("/")[0]
    if first_path_component in API_ROUTE_ROOTS:
        raise HTTPException(status_code=404, detail="Not found")

    frontend_root = FRONTEND_DIST_DIR.resolve()
    requested_file = (frontend_root / frontend_path).resolve()
    try:
        requested_file.relative_to(frontend_root)
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found") from None

    if requested_file.is_file():
        return FileResponse(requested_file)

    if Path(frontend_path).suffix:
        raise HTTPException(status_code=404, detail="Not found")

    index_file = frontend_root / "index.html"
    if not index_file.is_file():
        raise HTTPException(
            status_code=503,
            detail="Frontend assets are not available",
        )
    return FileResponse(index_file)
