import time
import uuid
from collections import defaultdict, deque

from fastapi import FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware

# ----- Your assigned values -----
TOTAL_ORDERS = 59
RATE_LIMIT = 16          # requests
RATE_WINDOW = 10         # seconds

app = FastAPI(title="Orders API")

# ----- CORS: allow the grader page to call this API -----
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)

# ----- 1. Idempotent order creation -----
# key -> order response
idempotency_store = {}

# ----- 2. Fixed catalog of orders 1..59 -----
ORDERS = [
    {"id": i, "product": f"Product {i}", "price": 10 + i}
    for i in range(1, TOTAL_ORDERS + 1)
]

# ----- 3. Per-client rate limiting -----
# client_id -> timestamps of recent requests
rate_limit_store = defaultdict(deque)


@app.post("/orders")
def create_order(
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    # If this exact key was used before, return the saved order.
    if idempotency_key in idempotency_store:
        return idempotency_store[idempotency_key]

    # First time: create a new order.
    order = {
        "id": str(uuid.uuid4()),
        "status": "created",
        "created_at": time.time(),
    }

    # Save it so repeated calls return the same order.
    idempotency_store[idempotency_key] = order

    return order


@app.get("/orders")
def list_orders(
    response: Response,
    limit: int = Query(default=10, ge=1),
    cursor: str | None = Query(default=None),
    x_client_id: str = Header(alias="X-Client-Id"),
):
    # ----- Rate limit BEFORE returning data -----
    now = time.time()
    bucket = rate_limit_store[x_client_id]

    # Remove timestamps older than 10 seconds.
    while bucket and now - bucket[0] >= RATE_WINDOW:
        bucket.popleft()

    if len(bucket) >= RATE_LIMIT:
        retry_after = max(1, int(RATE_WINDOW - (now - bucket[0])))
        response.headers["Retry-After"] = str(retry_after)
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )

    # This request is allowed; record it.
    bucket.append(now)

    # ----- Cursor pagination -----
    # Cursor is simply the next starting ID, but hidden inside an opaque string.
    start = 1
    if cursor:
        try:
            start = int(cursor)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid cursor")

    end = min(start + limit - 1, TOTAL_ORDERS)

    items = ORDERS[start - 1:end]

    next_cursor = None
    if end < TOTAL_ORDERS:
        next_cursor = str(end + 1)

    return {
        "items": items,
        "next_cursor": next_cursor,
    }
