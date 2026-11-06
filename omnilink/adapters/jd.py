"""京东宙斯开放平台适配器：param JSON 对象、cat_id 类目映射。"""
import uuid
from datetime import datetime, timezone
from typing import Any, ClassVar

from omnilink.adapters.base import BasePlatformAdapter
from omnilink.common.errors import ErrorCode
from omnilink.common.models import (
    Currency,
    Inventory,
    Logistics,
    LogisticsStatus,
    Money,
    Order,
    OrderItem,
    OrderStatus,
    Product,
)


class JdAdapter(BasePlatformAdapter):
    platform: ClassVar[str] = "jd"

    category_map: ClassVar[dict[str, str]] = {
        "652-829-860": "数码/耳机/蓝牙耳机",
        "652-829-861": "数码/手机配件/手机壳",
        "652-829-862": "数码/充电设备/移动电源",
    }
    error_map: ClassVar[dict[str, ErrorCode]] = {
        "jingdong.error.rate.limit": ErrorCode.PLATFORM_RATE_LIMITED,
        "jingdong.error.auth.expired": ErrorCode.PLATFORM_AUTH_EXPIRED,
        "jingdong.error.duplicate": ErrorCode.DUPLICATE_OPERATION,
        "jingdong.error.sku.notfound": ErrorCode.PRODUCT_NOT_FOUND,
        "jingdong.error.param": ErrorCode.INVALID_PARAMS,
        "jingdong.error.stock": ErrorCode.INSUFFICIENT_INVENTORY,
    }
    _ORDER_STATUS = {
        "WAIT_SELLER_DELIVERY": OrderStatus.PAID,
        "SELLER_DELIVERED": OrderStatus.SHIPPED,
        "FINISHED": OrderStatus.DELIVERED,
        "CANCELED": OrderStatus.CANCELLED,
    }

    def _extract_native_code(self, payload: Any) -> str | None:
        if isinstance(payload, dict):
            return payload.get("error", {}).get("code")
        return None

    def _to_product(self, w: dict) -> Product:
        return Product(
            unified_sku="",
            platform="jd",
            platform_sku=w["sku_id"],
            title=w["ware_title"],
            category_path=self.category_map.get(str(w.get("cat_id", "")), w.get("category", "")),
            brand=w.get("brand_name", ""),
            attributes=dict(w.get("param") or {}),
            price=Money.from_original(w["price"], Currency.CNY),
            monthly_sales=w.get("comment_count", 0) * 10,  # 京东以评论数估算销量
            images=[w["image_url"]] if w.get("image_url") else [],
        )

    async def search_products(self, query, merchant_id, limit=10):
        data = await self._call("GET", "/api/products/search", merchant_id,
                                params={"query": query, "limit": limit})
        wares = data.get("jingdong_ware_search_responce", {}).get("wares", [])
        return [self._to_product(w) for w in wares]

    async def get_inventory(self, sku, merchant_id):
        data = await self._call("GET", f"/api/inventory/{sku}", merchant_id)
        inv = data["stock_response"]
        return Inventory(platform="jd", platform_sku=sku,
                         available=inv["stock_num"],
                         locked=inv.get("lock_num", 0),
                         in_transit=inv.get("transit_num", 0))

    async def update_inventory(self, sku, quantity, merchant_id, idempotency_key):
        data = await self._call("POST", "/api/inventory/update", merchant_id,
                                body={"sku": sku, "quantity": quantity,
                                      "idempotency_key": idempotency_key})
        resp = data["stock_update_response"]
        return Inventory(platform="jd", platform_sku=sku, available=resp["stock_num"])

    async def create_order(self, sku, quantity, merchant_id, idempotency_key):
        data = await self._call("POST", "/api/orders", merchant_id,
                                body={"sku": sku, "quantity": quantity,
                                      "idempotency_key": idempotency_key})
        return Order(order_id=f"ORD-{uuid.uuid4().hex[:12]}",
                     platform="jd",
                     platform_order_id=data["platform_order_id"],
                     merchant_id=merchant_id,
                     items=[OrderItem(platform_sku=sku, quantity=quantity,
                                      unit_price=Money.from_original(0, Currency.CNY))],
                     status=OrderStatus.PAID,
                     total=Money.from_original(0, Currency.CNY))

    async def list_orders(self, merchant_id):
        data = await self._call("GET", "/api/orders", merchant_id)
        orders = []
        for o in data.get("orders", []):
            if "order_id" not in o:
                continue
            unit = float(o["payment"]) / max(o["quantity"], 1)
            orders.append(Order(
                order_id=f"ORD-{o['order_id']}",
                platform="jd",
                platform_order_id=o["order_id"],
                merchant_id=merchant_id,
                items=[OrderItem(platform_sku=o["sku_id"], quantity=o["quantity"],
                                 unit_price=Money.from_original(unit, Currency.CNY))],
                status=self._ORDER_STATUS.get(o["order_state"], OrderStatus.PAID),
                total=Money.from_original(o["payment"], Currency.CNY),
                created_at=datetime.fromisoformat(o["created"]).replace(tzinfo=timezone.utc),
            ))
        return orders

    async def track_logistics(self, platform_order_id, merchant_id):
        data = await self._call("GET", f"/api/logistics/{platform_order_id}", merchant_id)
        return Logistics(
            platform="jd", platform_order_id=platform_order_id,
            tracking_no=data["tracking_no"], carrier=data["carrier"],
            status=LogisticsStatus.IN_TRANSIT,
        )

    async def publish_product(self, product, merchant_id, idempotency_key):
        data = await self._call("POST", "/api/products", merchant_id, body={
            "ware_title": product["title"],
            "price": str(product["price_cny"]),
            "stock": product.get("stock", 0),
            "cat_id": product.get("platform_category_id", "652-829-860"),
            "idempotency_key": idempotency_key,
        })
        return data["platform_sku"]
