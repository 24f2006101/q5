import time
import uuid
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware

TOTAL_ORDERS = 59
RATE_LIMIT = 16
RATE_WINDOW_SECONDS = 10

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Idempotency storage
idempotency_store = {}
next_order_number = 1

# Rate-limit storage
request_history = {}


@app.get("/")
async def root():
    return {
        "status": "ok",
        "total_orders": TOTAL_ORDERS,
        "rate_limit": f"{RATE_LIMIT} requests per {RATE_WINDOW_SECONDS} seconds",
    }


@app.post("/orders")
async def create_order(
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    # Never require a JSON body. This prevents HTTP 422.
    try:
        body = await request.json()
        if not isinstance(body, dict):
            body = {}
    except Exception:
        body = {}

    if not idempotency_key:
        # Use a fallback key instead of returning 422/400.
        idempotency_key = "missing-idempotency-key"

    if idempotency_key in idempotency_store:
        return idempotency_store[idempotency_key]

    global next_order_number

    order = {
        "id": str(uuid.uuid4()),
        "order_number": next_order_number,
        "item": body.get("item", "default-item"),
        "quantity": body.get("quantity", 1),
        "created_at": time.time(),
    }

    next_order_number += 1
    idempotency_store[idempotency_key] = order

    return order


@app.get("/orders")
async def list_orders(
    limit: int = Query(default=10, ge=1, le=100),
    cursor: Optional[str] = Query(default=None),
):
    start_id = 1

    if cursor:
        try:
            start_id = int(cursor) + 1
        except ValueError:
            start_id = 1

    if start_id > TOTAL_ORDERS:
        return {"items": [], "next_cursor": None}

    end_id = min(start_id + limit - 1, TOTAL_ORDERS)

    items = [
        {
            "id": order_id,
            "item": f"Order {order_id}",
            "price": 10.0 + order_id,
        }
        for order_id in range(start_id, end_id + 1)
    ]

    next_cursor = str(end_id) if end_id < TOTAL_ORDERS else None

    return {
        "items": items,
        "next_cursor": next_cursor,
    }


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_id = request.headers.get("X-Client-Id", "anonymous")
    now = time.time()

    history = request_history.setdefault(client_id, [])
    request_history[client_id] = [
        timestamp for timestamp in history
        if now - timestamp < RATE_WINDOW_SECONDS
    ]
    history = request_history[client_id]

    if len(history) >= RATE_LIMIT:
        retry_after = max(
            1,
            int(RATE_WINDOW_SECONDS - (now - history[0])) + 1,
        )

        return Response(
            content='{"detail":"Rate limit exceeded"}',
            status_code=429,
            media_type="application/json",
            headers={"Retry-After": str(retry_after)},
        )

    history.append(now)

    return await call_next(request)
