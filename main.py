import time
import uuid
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ----- Assigned values -----
TOTAL_ORDERS = 59
RATE_LIMIT = 16
RATE_WINDOW_SECONDS = 10

app = FastAPI(title="Orders API")

# ----- CORS -----
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----- 1. Idempotent POST /orders -----

class OrderCreate(BaseModel):
    item: str
    quantity: int = 1

idempotency_store: dict[str, dict] = {}
orders_by_id: dict[int, dict] = {}
next_order_number = 1

@app.post("/orders", status_code=201)
def create_order(
    body: OrderCreate,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")

    # If this exact key was used before, return the original order.
    if idempotency_key in idempotency_store:
        return idempotency_store[idempotency_key]

    global next_order_number

    order = {
        "id": str(uuid.uuid4()),
        "order_number": next_order_number,
        "item": body.item,
        "quantity": body.quantity,
        "created_at": time.time(),
    }

    next_order_number += 1
    idempotency_store[idempotency_key] = order

    return order

# ----- 2. Cursor pagination -----

@app.get("/orders")
def list_orders(
    limit: int = Query(default=10, ge=1, le=100),
    cursor: Optional[str] = Query(default=None),
):
    # Cursor is simply the ID of the last order returned.
    # It is opaque to the client: they only need to send it back unchanged.
    start_id = 1

    if cursor is not None:
        try:
            start_id = int(cursor) + 1
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid cursor")

    end_id = min(start_id + limit - 1, TOTAL_ORDERS)

    items = [
        {
            "id": order_id,
            "item": f"Order {order_id}",
            "price": 10.0 + order_id,
        }
        for order_id in range(start_id, end_id + 1)
    ]

    next_cursor = None
    if end_id < TOTAL_ORDERS:
        next_cursor = str(end_id)

    return {
        "items": items,
        "next_cursor": next_cursor,
    }

# ----- 3. Per-client rate limiting -----

# client_id -> list of timestamps of accepted requests
request_history: dict[str, list[float]] = {}

@app.middleware("http")
async def rate_limit_middleware(request, call_next):
    client_id = request.headers.get("X-Client-Id")

    # Apply the limit only to API endpoints, not docs or favicon.
    if client_id and request.url.path.startswith(("/orders", "/")):
        now = time.time()

        history = request_history.setdefault(client_id, [])

        # Remove timestamps older than the 10-second window.
        request_history[client_id] = [
            timestamp for timestamp in history
            if now - timestamp < RATE_WINDOW_SECONDS
        ]
        history = request_history[client_id]

        if len(history) >= RATE_LIMIT:
            oldest_relevant = history[0]
            retry_after = max(1, int(RATE_WINDOW_SECONDS - (now - oldest_relevant)) + 1)

            return Response(
                content='{"detail":"Rate limit exceeded"}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": str(retry_after)},
            )

        # Count this accepted request.
        history.append(now)

    response = await call_next(request)
    return response

@app.get("/")
def root():
    return {
        "total_orders": TOTAL_ORDERS,
        "rate_limit": f"{RATE_LIMIT} requests per {RATE_WINDOW_SECONDS} seconds per client",
    }
