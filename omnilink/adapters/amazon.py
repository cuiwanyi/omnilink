"""Amazon SP-API 适配器：USD→CNY 汇率换算、attributes 数组解析、browse_node 类目映射。"""
import uuid
from datetime import datetime, timezone
from typing import Any, ClassVar

from omnilink.adapters.base import BasePlatformAdapter
from omnilink.common.errors import ErrorCode
from omnilink.common.models import (
    Currency,
    Inventory,
    Logistics,
    LogisticsEvent,
    LogisticsStatus,
    Money,
    Order,
    OrderItem,
    OrderStatus,
    Product,
)


class AmazonAdapter(BasePlatformAdapter):
    platform: ClassVar[str] = "amazon"

    category_map: ClassVar[dict[str, str]] = {
        "172541": "数码/耳机/蓝牙耳机",
        "2407761011": "数码/手机配件/手机壳",
        "7073966011": "数码/充电设备/移动电源",
    }
    error_map: ClassVar[dict[str, ErrorCode]] = {
        "QuotaExceeded": ErrorCode.PLATFORM_RATE_LIMITED,
        "InvalidAccessToken": ErrorCode.PLATFORM_AUTH_EXPIRED,
        "DuplicateRequest": ErrorCode.DUPLICATE_OPERATION,
        "NotFound": ErrorCode.PRODUCT_NOT_FOUND,
        "InvalidInput": ErrorCode.INVALID_PARAMS,
        "InsufficientInventory": ErrorCode.INSUFFICIENT_INVENTORY,
    }
    _ORDER_STATUS = {
        "Unshipped": OrderStatus.PAID,
        "Shipped": OrderStatus.SHIPPED,
        "Delivered": OrderStatus.DELIVERED,
        "Canceled": OrderStatus.CANCELLED,
    }

    def _extract_native_code(self, payload: Any) -> str | None:
        if isinstance(payload, dict) and payload.get("errors"):
            return payload["errors"][0].get("code")
        return None

    @staticmethod
    def _parse_attributes(attrs: list) -> dict[str, str]:
        """Amazon 规格是数组：[{"name": k, "value": v}]。"""
        return {a["name"]: a["value"] for a in (attrs or []) if "name" in a}

    def _to_product(self, item: dict) -> Product:
        return Product(
            unified_sku="",
            platform="amazon",
            platform_sku=item["asin"],
            title=item["item_name"],
            category_path=self.category_map.get(str(item.get("browse_node", "")), item.get("category", "")),
            brand=item.get("brand", ""),
            attributes=self._parse_attributes(item.get("attributes")),
            price=Money.from_original(item["price"]["amount"], Currency.USD),
            monthly_sales=max(0, 100000 - item.get("sales_rank", 100000)),  # 以排名估算
            images=[item["image_url"]] if item.get("image_url") else [],
        )

    async def search_products(self, query, merchant_id, limit=10):
        data = await self._call("GET", "/api/products/search", merchant_id,
                                params={"query": query, "limit": limit})
        items = data.get("payload", {}).get("items", [])
        return [self._to_product(i) for i in items]

    async def get_inventory(self, sku, merchant_id):
        data = await self._call("GET", f"/api/inventory/{sku}", merchant_id)
        inv = data["payload"]
        return Inventory(platform="amazon", platform_sku=sku,
                         available=inv["fulfillable_quantity"],
                         locked=inv.get("reserved_quantity", 0),
                         in_transit=inv.get("inbound_quantity", 0))

    async def update_inventory(self, sku, quantity, merchant_id, idempotency_key):
        data = await self._call("POST", "/api/inventory/update", merchant_id,
                                body={"sku": sku, "quantity": quantity,
                                      "idempotency_key": idempotency_key})
        resp = data["payload"]
        return Inventory(platform="amazon", platform_sku=sku,
                         available=resp["fulfillable_quantity"])

    async def create_order(self, sku, quantity, merchant_id, idempotency_key):
        data = await self._call("POST", "/api/orders", merchant_id,
                                body={"asin": sku, "quantity": quantity,
                                      "idempotency_key": idempotency_key})
        return Order(order_id=f"ORD-{uuid.uuid4().hex[:12]}",
                     platform="amazon",
                     platform_order_id=data["platform_order_id"],
                     merchant_id=merchant_id,
                     items=[OrderItem(platform_sku=sku, quantity=quantity,
                                      unit_price=Money.from_original(0, Currency.USD))],
                     status=OrderStatus.PAID,
                     total=Money.from_original(0, Currency.USD))

    async def list_orders(self, merchant_id):
        data = await self._call("GET", "/api/orders", merchant_id)
        orders = []
        for o in data.get("orders", []):
            if "amazon_order_id" not in o:
                continue
            unit = float(o["amount"]) / max(o["quantity"], 1)
            created = o["created"].replace("Z", "+00:00")
            orders.append(Order(
                order_id=f"ORD-{o['amazon_order_id']}",
                platform="amazon",
                platform_order_id=o["amazon_order_id"],
                merchant_id=merchant_id,
                items=[OrderItem(platform_sku=o["asin"], quantity=o["quantity"],
                                 unit_price=Money.from_original(unit, Currency.USD))],
                status=self._ORDER_STATUS.get(o["order_status"], OrderStatus.PAID),
                total=Money.from_original(o["amount"], Currency.USD),
                created_at=datetime.fromisoformat(created),
            ))
        return orders

    async def track_logistics(self, platform_order_id, merchant_id):
        data = await self._call("GET", f"/api/logistics/{platform_order_id}", merchant_id)
        return Logistics(
            platform="amazon", platform_order_id=platform_order_id,
            tracking_no=data["tracking_no"], carrier=data["carrier"],
            status=LogisticsStatus.IN_TRANSIT if data["status"] == "In Transit" else LogisticsStatus.DELIVERED,
            events=[LogisticsEvent(timestamp=datetime.fromisoformat(t["time"].replace("Z", "+00:00")),
                                   description=t["context"]) for t in data.get("traces", [])],
        )

    async def publish_product(self, product, merchant_id, idempotency_key):
        # Amazon 上架需要 UPC 码等平台特有资质，由编排层做合规校验
        data = await self._call("POST", "/api/products", merchant_id, body={
            "item_name": product["title"],
            "price": {"amount": product["price_usd"], "currency": "USD"},
            "stock": product.get("stock", 0),
            "browse_node": product.get("platform_category_id", "172541"),
            "idempotency_key": idempotency_key,
        })
        return data["platform_sku"]
