"""Mock 平台开放 API 服务。

一个工厂函数 create_mock_app(platform) 生成一个平台的模拟开放平台：
- 平台原生的响应结构、错误格式（刻意不一致，还原真实世界）
- 模拟网络延迟、限流（HTTP 429 + Retry-After）、Amazon 可配置错误注入
- 写操作强制幂等键，重复提交返回平台原生"重复请求"错误
- 商家凭证校验（X-Merchant-Token），失效凭证返回平台原生鉴权错误
"""
import asyncio
import random
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from omnilink.common.models import Platform
from omnilink.config import get_settings
from omnilink.mock_platforms.data import (
    PLATFORM_SERIALIZER,
    build_platform_db,
)

# 平台原生错误格式：真实世界中四家的错误结构完全不同
_ERROR_WRAPPERS = {
    "taobao": lambda code, msg: {"error_response": {"code": 40, "sub_code": code, "msg": msg}},
    "jd": lambda code, msg: {"error": {"code": code, "message": msg}},
    "amazon": lambda code, msg: {"errors": [{"code": code, "message": msg}]},
    "shopify": lambda code, msg: {"errors": [{"code": code, "message": msg}]},
}

# 各平台限流错误码（原生叫法）
_RATE_LIMIT_CODES = {
    "taobao": "isv.rate-limit-exceeded",
    "jd": "jingdong.error.rate.limit",
    "amazon": "QuotaExceeded",
    "shopify": "THROTTLED",
}
_AUTH_CODES = {
    "taobao": "isv.invalid-token",
    "jd": "jingdong.error.auth.expired",
    "amazon": "InvalidAccessToken",
    "shopify": "UNAUTHORIZED",
}
_DUP_CODES = {
    "taobao": "isv.duplicate-request",
    "jd": "jingdong.error.duplicate",
    "amazon": "DuplicateRequest",
    "shopify": "DUPLICATE",
}
_NOT_FOUND_CODES = {
    "taobao": "isv.item-not-found",
    "jd": "jingdong.error.sku.notfound",
    "amazon": "NotFound",
    "shopify": "NOT_FOUND",
}


class _TokenBucket:
    """平台 API 配额令牌桶（Mock 侧，模拟平台限流行为）。"""

    def __init__(self, rate_per_sec: float = 20.0, burst: int = 40) -> None:
        self.rate = rate_per_sec
        self.capacity = burst
        self.tokens = float(burst)
        self.updated = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


def create_mock_app(platform: Platform) -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=f"Mock {platform} Open API")
    db = build_platform_db(platform)
    bucket = _TokenBucket()

    def err(wrapper_map: dict, http_status: int, msg: str, retry_after: float | None = None):
        headers = {"Retry-After": str(retry_after)} if retry_after else None
        return JSONResponse(
            status_code=http_status,
            content=_ERROR_WRAPPERS[platform](wrapper_map[platform], msg),
            headers=headers,
        )

    @app.middleware("http")
    async def platform_behavior(request: Request, call_next):
        # 1. 模拟网络延迟
        if settings.mock_latency_ms > 0:
            await asyncio.sleep(settings.mock_latency_ms / 1000)
        # 2. 凭证校验（health 除外）
        if request.url.path != "/health":
            token = request.headers.get("X-Merchant-Token", "")
            if token != f"tok-{platform}-M001":
                return err(_AUTH_CODES, 401, "invalid or expired merchant token")
        # 3. 平台限流：令牌桶 + Amazon 可配置错误注入
        limited = not bucket.allow()
        if platform == "amazon" and random.random() < settings.mock_amazon_rate_limit_prob:
            limited = True
        if limited:
            return err(_RATE_LIMIT_CODES, 429, "rate limit exceeded", retry_after=1.0)
        return await call_next(request)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "platform": platform}

    # ---------- 商品查询（只读） ----------
    @app.get("/api/products/search")
    async def search_products(query: str, limit: int = 10) -> Any:
        q = query.lower()
        hits = [
            {k: v for k, v in p.items() if not k.startswith("__")}
            for p in db["products"].values()
            if q in _search_text(p)
        ]
        hits = hits[:limit]
        if platform == "taobao":
            return {"items_search_response": {"items": hits, "total": len(hits)}}
        if platform == "jd":
            return {"jingdong_ware_search_responce": {"wares": hits, "total": len(hits)}}
        if platform == "amazon":
            return {"payload": {"items": hits, "numberOfResults": len(hits)}}
        return {"products": hits}

    # ---------- 库存查询（只读） ----------
    @app.get("/api/inventory/{sku}")
    async def get_inventory(sku: str) -> Any:
        p = db["products"].get(sku)
        if not p:
            return err(_NOT_FOUND_CODES, 404, f"sku {sku} not found")
        if platform == "taobao":
            return {"inventory_response": {"num_iid": sku, "sellable_quantity": p["sellable_quantity"],
                                           "lock_quantity": 5, "on_way_quantity": 12}}
        if platform == "jd":
            return {"stock_response": {"sku_id": sku, "stock_num": p["stock_num"],
                                       "lock_num": 3, "transit_num": 8}}
        if platform == "amazon":
            return {"payload": {"asin": sku, "fulfillable_quantity": p["fulfillable_quantity"],
                                "reserved_quantity": 10, "inbound_quantity": 20}}
        return {"inventory_item": {"variant_id": sku, "inventory_quantity": p["inventory_quantity"],
                                   "reserved": 2, "incoming": 6}}

    # ---------- 库存更新（写操作，强制幂等键） ----------
    @app.post("/api/inventory/update")
    async def update_inventory(body: dict) -> Any:
        sku = body.get("sku") or body.get("num_iid") or body.get("variant_id")
        quantity = body.get("quantity")
        idem = body.get("idempotency_key")
        if not sku or quantity is None or not idem:
            return err({"taobao": "isv.missing-parameter", "jd": "jingdong.error.param",
                        "amazon": "InvalidInput", "shopify": "UNPROCESSABLE"},
                       400, "missing sku/quantity/idempotency_key")
        dup = _consume_idempotency(db, idem)
        if dup:
            return err(_DUP_CODES, 409, f"duplicate idempotency_key {idem}")
        p = db["products"].get(sku)
        if not p:
            return err(_NOT_FOUND_CODES, 404, f"sku {sku} not found")
        _set_stock(p, platform, int(quantity))
        return _native_write_result(platform, sku, int(quantity))

    # ---------- 创建订单（写操作，强制幂等键） ----------
    @app.post("/api/orders")
    async def create_order(body: dict) -> Any:
        sku = body.get("sku") or body.get("num_iid") or body.get("asin") or body.get("variant_id")
        quantity = int(body.get("quantity", 1))
        idem = body.get("idempotency_key")
        if not sku or not idem:
            return err({"taobao": "isv.missing-parameter", "jd": "jingdong.error.param",
                        "amazon": "InvalidInput", "shopify": "UNPROCESSABLE"},
                       400, "missing sku/idempotency_key")
        dup = _consume_idempotency(db, idem)
        if dup:
            return err(_DUP_CODES, 409, f"duplicate idempotency_key {idem}")
        p = db["products"].get(sku)
        if not p:
            return err(_NOT_FOUND_CODES, 404, f"sku {sku} not found")
        if _get_stock(p, platform) < quantity:
            return err({"taobao": "isv.not-enough-stock", "jd": "jingdong.error.stock",
                        "amazon": "InsufficientInventory", "shopify": "INVENTORY_OUT_OF_STOCK"},
                       400, f"insufficient stock for {sku}")
        _set_stock(p, platform, _get_stock(p, platform) - quantity)
        db["order_seq"] += 1
        order_id = f"{platform.upper()}-NEW-{db['order_seq']}"
        db["orders"].append({"platform_order_id": order_id, "sku": sku, "quantity": quantity})
        return {"platform": platform, "platform_order_id": order_id,
                "sku": sku, "quantity": quantity, "status": "created"}

    # ---------- 订单列表（只读） ----------
    @app.get("/api/orders")
    async def list_orders() -> Any:
        return {"platform": platform, "orders": db["orders"]}

    # ---------- 物流跟踪（只读） ----------
    @app.get("/api/logistics/{order_id}")
    async def track_logistics(order_id: str) -> Any:
        info = db["logistics"].get(order_id)
        if not info:
            return err(_NOT_FOUND_CODES, 404, f"no logistics for order {order_id}")
        return {"platform": platform, "order_id": order_id, **info}

    # ---------- 商品发布/上架（写操作） ----------
    @app.post("/api/products")
    async def publish_product(body: dict) -> Any:
        idem = body.get("idempotency_key")
        if not idem:
            return err({"taobao": "isv.missing-parameter", "jd": "jingdong.error.param",
                        "amazon": "InvalidInput", "shopify": "UNPROCESSABLE"},
                       400, "missing idempotency_key")
        dup = _consume_idempotency(db, idem)
        if dup:
            return err(_DUP_CODES, 409, f"duplicate idempotency_key {idem}")
        new_sku = f"{platform.upper()}-NEW-{uuid.uuid4().hex[:8].upper()}"
        native = dict(body)
        native["_stock_field_set"] = True
        _set_stock(native, platform, int(body.get("stock", 0)))
        db["products"][new_sku] = native
        return {"platform": platform, "platform_sku": new_sku, "status": "listed"}

    return app


# ---------- 内部工具 ----------

def _search_text(p: dict) -> str:
    text = " ".join(str(v) for v in p.values() if not isinstance(v, (dict, list)))
    for key in ("props", "param", "attributes", "options"):
        if key in p:
            text += " " + str(p[key])
    return text.lower()


def _consume_idempotency(db: dict, key: str) -> bool:
    """返回 True 表示重复请求。"""
    if key in db["consumed_idempotency_keys"]:
        return True
    db["consumed_idempotency_keys"].add(key)
    return False


def _get_stock(p: dict, platform: str) -> int:
    field = {"taobao": "sellable_quantity", "jd": "stock_num",
             "amazon": "fulfillable_quantity", "shopify": "inventory_quantity"}[platform]
    return int(p.get(field, 0))


def _set_stock(p: dict, platform: str, value: int) -> None:
    field = {"taobao": "sellable_quantity", "jd": "stock_num",
             "amazon": "fulfillable_quantity", "shopify": "inventory_quantity"}[platform]
    p[field] = value


def _native_write_result(platform: str, sku: str, quantity: int) -> dict:
    if platform == "taobao":
        return {"inventory_update_response": {"num_iid": sku, "sellable_quantity": quantity, "success": True}}
    if platform == "jd":
        return {"stock_update_response": {"sku_id": sku, "stock_num": quantity, "code": "0"}}
    if platform == "amazon":
        return {"payload": {"asin": sku, "fulfillable_quantity": quantity, "status": "ACCEPTED"}}
    return {"inventory_item": {"variant_id": sku, "inventory_quantity": quantity}}
