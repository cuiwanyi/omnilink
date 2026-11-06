"""淘宝开放平台适配器：分→元、props 分号字符串解析、cid 类目映射。"""
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


class TaobaoAdapter(BasePlatformAdapter):
    platform: ClassVar[str] = "taobao"

    category_map: ClassVar[dict[str, str]] = {
        "1512": "数码/耳机/蓝牙耳机",
        "1513": "数码/手机配件/手机壳",
        "1514": "数码/充电设备/移动电源",
    }
    error_map: ClassVar[dict[str, ErrorCode]] = {
        "isv.rate-limit-exceeded": ErrorCode.PLATFORM_RATE_LIMITED,
        "isv.invalid-token": ErrorCode.PLATFORM_AUTH_EXPIRED,
        "isv.duplicate-request": ErrorCode.DUPLICATE_OPERATION,
        "isv.item-not-found": ErrorCode.PRODUCT_NOT_FOUND,
        "isv.missing-parameter": ErrorCode.INVALID_PARAMS,
        "isv.not-enough-stock": ErrorCode.INSUFFICIENT_INVENTORY,
    }
    _ORDER_STATUS = {
        "WAIT_SELLER_SEND_GOODS": OrderStatus.PAID,
        "WAIT_BUYER_CONFIRM_GOODS": OrderStatus.SHIPPED,
        "TRADE_FINISHED": OrderStatus.DELIVERED,
        "TRADE_CLOSED": OrderStatus.CANCELLED,
    }

    def _extract_native_code(self, payload: Any) -> str | None:
        if isinstance(payload, dict):
            return payload.get("error_response", {}).get("sub_code")
        return None

    @staticmethod
    def _parse_props(props: str) -> dict[str, str]:
        """淘宝规格是分号分隔字符串：'蓝牙版本:5.3;降噪:ANC主动降噪'。"""
        result = {}
        for pair in (props or "").split(";"):
            if ":" in pair:
                k, v = pair.split(":", 1)
                result[k.strip()] = v.strip()
        return result

    def _to_product(self, item: dict) -> Product:
        return Product(
            unified_sku="",  # 由 SKU 映射表回填，适配器不做跨平台对齐
            platform="taobao",
            platform_sku=item["num_iid"],
            title=item["title"],
            category_path=self.category_map.get(str(item.get("cid", "")), item.get("cat_name", "")),
            brand=item.get("brand", ""),
            attributes=self._parse_props(item.get("props", "")),
            price=Money.from_original(item["price_cents"] / 100, Currency.CNY),
            monthly_sales=item.get("monthly_sales", 0),
            images=[item["pict_url"]] if item.get("pict_url") else [],
        )

    async def search_products(self, query, merchant_id, limit=10):
        data = await self._call("GET", "/api/products/search", merchant_id,
                                params={"query": query, "limit": limit})
        items = data.get("items_search_response", {}).get("items", [])
        return [self._to_product(i) for i in items]

    async def get_inventory(self, sku, merchant_id):
        data = await self._call("GET", f"/api/inventory/{sku}", merchant_id)
        inv = data["inventory_response"]
        return Inventory(platform="taobao", platform_sku=sku,
                         available=inv["sellable_quantity"],
                         locked=inv.get("lock_quantity", 0),
                         in_transit=inv.get("on_way_quantity", 0))

    async def update_inventory(self, sku, quantity, merchant_id, idempotency_key):
        data = await self._call("POST", "/api/inventory/update", merchant_id,
                                body={"num_iid": sku, "quantity": quantity,
                                      "idempotency_key": idempotency_key})
        resp = data["inventory_update_response"]
        return Inventory(platform="taobao", platform_sku=sku,
                         available=resp["sellable_quantity"])

    async def create_order(self, sku, quantity, merchant_id, idempotency_key):
        data = await self._call("POST", "/api/orders", merchant_id,
                                body={"num_iid": sku, "quantity": quantity,
                                      "idempotency_key": idempotency_key})
        return Order(order_id=f"ORD-{uuid.uuid4().hex[:12]}",
                     platform="taobao",
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
            if "tid" not in o:
                continue  # 跳过 Mock 侧动态创建的简略订单
            orders.append(Order(
                order_id=f"ORD-{o['tid']}",
                platform="taobao",
                platform_order_id=o["tid"],
                merchant_id=merchant_id,
                items=[OrderItem(platform_sku=o["num_iid"], quantity=o["num"],
                                 unit_price=Money.from_original(o["payment_cents"] / 100 / o["num"], Currency.CNY))],
                status=self._ORDER_STATUS.get(o["status"], OrderStatus.PAID),
                total=Money.from_original(o["payment_cents"] / 100, Currency.CNY),
                created_at=datetime.fromisoformat(o["created"]).replace(tzinfo=timezone.utc),
            ))
        return orders

    async def track_logistics(self, platform_order_id, merchant_id):
        data = await self._call("GET", f"/api/logistics/{platform_order_id}", merchant_id)
        return Logistics(
            platform="taobao",
            platform_order_id=platform_order_id,
            tracking_no=data["tracking_no"],
            carrier=data["carrier"],
            status=LogisticsStatus.IN_TRANSIT if data["status"] == "运输中" else LogisticsStatus.DELIVERED,
            events=[LogisticsEvent(timestamp=datetime.fromisoformat(t["time"]).replace(tzinfo=timezone.utc),
                                   description=t["context"]) for t in data.get("traces", [])],
        )

    async def publish_product(self, product, merchant_id, idempotency_key):
        data = await self._call("POST", "/api/products", merchant_id, body={
            "title": product["title"],
            "price_cents": int(float(product["price_cny"]) * 100),
            "stock": product.get("stock", 0),
            "cid": product.get("platform_category_id", "1512"),
            "idempotency_key": idempotency_key,
        })
        return data["platform_sku"]
