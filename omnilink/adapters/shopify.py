"""Shopify Admin API 适配器：options 数组解析、taxonomy_id 类目映射。"""
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


class ShopifyAdapter(BasePlatformAdapter):
    platform: ClassVar[str] = "shopify"

    category_map: ClassVar[dict[str, str]] = {
        "gid://tax/electronics-headphones": "数码/耳机/蓝牙耳机",
        "gid://tax/electronics-cases": "数码/手机配件/手机壳",
        "gid://tax/electronics-chargers": "数码/充电设备/移动电源",
    }
    error_map: ClassVar[dict[str, ErrorCode]] = {
        "THROTTLED": ErrorCode.PLATFORM_RATE_LIMITED,
        "UNAUTHORIZED": ErrorCode.PLATFORM_AUTH_EXPIRED,
        "DUPLICATE": ErrorCode.DUPLICATE_OPERATION,
        "NOT_FOUND": ErrorCode.PRODUCT_NOT_FOUND,
        "UNPROCESSABLE": ErrorCode.INVALID_PARAMS,
        "INVENTORY_OUT_OF_STOCK": ErrorCode.INSUFFICIENT_INVENTORY,
    }

    def _extract_native_code(self, payload: Any) -> str | None:
        if isinstance(payload, dict) and payload.get("errors"):
            err = payload["errors"]
            if isinstance(err, list) and err:
                return err[0].get("code")
        return None

    @staticmethod
    def _parse_options(options: list) -> dict[str, str]:
        return {o["name"]: o["value"] for o in (options or []) if "name" in o}

    def _to_product(self, p: dict) -> Product:
        return Product(
            unified_sku="",
            platform="shopify",
            platform_sku=p["variant_id"],
            title=p["product_title"],
            category_path=self.category_map.get(str(p.get("taxonomy_id", "")), p.get("product_type", "")),
            brand=p.get("vendor", ""),
            attributes=self._parse_options(p.get("options")),
            price=Money.from_original(p["price"], Currency(p.get("currency", "USD"))),
            images=[p["image"]["src"]] if p.get("image") else [],
        )

    async def search_products(self, query, merchant_id, limit=10):
        data = await self._call("GET", "/api/products/search", merchant_id,
                                params={"query": query, "limit": limit})
        return [self._to_product(p) for p in data.get("products", [])]

    async def get_inventory(self, sku, merchant_id):
        data = await self._call("GET", f"/api/inventory/{sku}", merchant_id)
        inv = data["inventory_item"]
        return Inventory(platform="shopify", platform_sku=sku,
                         available=inv["inventory_quantity"],
                         locked=inv.get("reserved", 0),
                         in_transit=inv.get("incoming", 0))

    async def update_inventory(self, sku, quantity, merchant_id, idempotency_key):
        data = await self._call("POST", "/api/inventory/update", merchant_id,
                                body={"variant_id": sku, "quantity": quantity,
                                      "idempotency_key": idempotency_key})
        resp = data["inventory_item"]
        return Inventory(platform="shopify", platform_sku=sku,
                         available=resp["inventory_quantity"])

    async def create_order(self, sku, quantity, merchant_id, idempotency_key):
        data = await self._call("POST", "/api/orders", merchant_id,
                                body={"variant_id": sku, "quantity": quantity,
                                      "idempotency_key": idempotency_key})
        return Order(order_id=f"ORD-{uuid.uuid4().hex[:12]}",
                     platform="shopify",
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
            if "id" not in o or "variant_id" not in o:
                continue
            unit = float(o["total_price"]) / max(o["quantity"], 1)
            orders.append(Order(
                order_id=f"ORD-SHOP-{o['id']}",
                platform="shopify",
                platform_order_id=str(o["id"]),
                merchant_id=merchant_id,
                items=[OrderItem(platform_sku=o["variant_id"], quantity=o["quantity"],
                                 unit_price=Money.from_original(unit, Currency.USD))],
                status=OrderStatus.PAID if o["financial_status"] == "paid" else OrderStatus.PENDING_PAYMENT,
                total=Money.from_original(o["total_price"], Currency(o.get("currency", "USD"))),
                created_at=datetime.fromisoformat(o["created_at"].replace("Z", "+00:00")),
            ))
        return orders

    async def track_logistics(self, platform_order_id, merchant_id):
        data = await self._call("GET", f"/api/logistics/{platform_order_id}", merchant_id)
        return Logistics(
            platform="shopify", platform_order_id=platform_order_id,
            tracking_no=data["tracking_no"], carrier=data["carrier"],
            status=LogisticsStatus.IN_TRANSIT,
        )

    async def publish_product(self, product, merchant_id, idempotency_key):
        data = await self._call("POST", "/api/products", merchant_id, body={
            "product_title": product["title"],
            "price": str(product["price_usd"]),
            "currency": "USD",
            "stock": product.get("stock", 0),
            "taxonomy_id": product.get("platform_category_id", "gid://tax/electronics-headphones"),
            "idempotency_key": idempotency_key,
        })
        return data["platform_sku"]
