"""平台 MCP Server 工厂：一个函数生成一个平台的标准工具服务。

每个平台暴露完全一致的 6 个工具 schema：
- product_search   商品查询（只读，价格走公共缓存池 TTL 60s）
- get_inventory    库存查询（只读，TTL 5s，商家前缀隔离）
- update_inventory 库存更新（写，强制幂等键）
- create_order     订单创建（写，强制幂等键）
- track_logistics  物流跟踪（只读）
- publish_product  商品上架（写，强制幂等键）

写操作三层防护：系统级幂等表（run_idempotent）→ 平台侧幂等键 → 审计日志。
"""
import json
from typing import Any

from fastmcp import FastMCP
from pydantic import Field

from omnilink.adapters import get_adapter
from omnilink.common.errors import ErrorCode, PlatformAPIError
from omnilink.common.idempotency import make_idempotency_key
from omnilink.common.models import Platform
from omnilink.config import get_settings
from omnilink.services.audit import audit
from omnilink.services.cache import CacheService, cache_key, get_redis
from omnilink.services.idempotency import run_idempotent
from omnilink.services.rate_limiter import get_throttle
from omnilink.services.sku_mapping import SkuMappingStore


def create_platform_mcp(platform: Platform) -> FastMCP:
    settings = get_settings()
    adapter = get_adapter(platform, settings.mock_base_url(platform))
    throttle = get_throttle(platform)
    mcp = FastMCP(f"{platform}-tools")

    price_cache = CacheService(settings.price_cache_ttl_seconds)
    inventory_cache = CacheService(settings.inventory_cache_ttl_seconds)

    def _err_json(exc: PlatformAPIError) -> str:
        """统一错误码以结构化 JSON 返回，A2A 层据此决策（限流→重试，凭证失效→告警）。"""
        return json.dumps({"error": exc.to_dict()}, ensure_ascii=False)

    async def _guarded_call(action: str, merchant_id: str, fn, params: dict) -> Any:
        """统一护栏：令牌桶排队 → 熔断检查 → 调用 → 审计。"""
        await throttle.bucket.acquire()
        if not throttle.breaker.allow():
            raise PlatformAPIError(
                ErrorCode.PLATFORM_UNAVAILABLE,
                f"{platform} circuit breaker open, serving degraded",
                platform=platform, retry_after=5.0,
            )
        try:
            result = await fn()
        except PlatformAPIError as exc:
            if exc.retryable:
                throttle.breaker.on_failure()
            await audit(f"mcp:{platform}", action, platform, merchant_id, params,
                        result=f"error:{exc.code.value}")
            raise
        throttle.breaker.on_success()
        await audit(f"mcp:{platform}", action, platform, merchant_id, params)
        return result

    # ================= 商品查询（只读） =================
    @mcp.tool(
        description="按关键词查询本平台在售商品，返回统一数据模型列表"
        "（价格已换算人民币、类目已映射到统一类目树）。"
    )
    async def product_search(
        query: str = Field(description="搜索关键词，如 '蓝牙耳机' 或 'iPhone'"),
        merchant_id: str = Field(description="商家 ID，用于凭证取用与审计"),
        limit: int = Field(default=10, ge=1, le=50, description="返回条数上限"),
    ) -> str:
        key = cache_key("search", platform, f"{query}:{limit}")  # 公开数据公共缓存池
        cached = await price_cache.get(key)
        if cached:
            return json.dumps({"products": cached, "cache_hit": True}, ensure_ascii=False)

        async def load():
            products = await adapter.search_products(query, merchant_id, limit)
            # 命中 SKU 映射表的回填 unified_sku
            for p in products:
                p.unified_sku = await SkuMappingStore.to_unified(platform, p.platform_sku) or ""
            return [p.model_dump(mode="json") for p in products]

        try:
            products = await _guarded_call("product_search", merchant_id, load,
                                           {"query": query, "limit": limit})
        except PlatformAPIError as exc:
            return _err_json(exc)
        await price_cache.set(key, products)
        return json.dumps({"products": products, "cache_hit": False}, ensure_ascii=False)

    # ================= 库存查询（只读） =================
    @mcp.tool(description="查询本平台某 SKU 的库存三态（可售/锁定/在途）及配额。")
    async def get_inventory(
        platform_sku: str = Field(description="平台侧商品编码"),
        merchant_id: str = Field(description="商家 ID"),
    ) -> str:
        key = cache_key("inventory", platform, platform_sku, merchant_id)

        async def load():
            inv = await adapter.get_inventory(platform_sku, merchant_id)
            return inv.model_dump(mode="json")

        try:
            data = await inventory_cache.get_or_load(
                key,
                lambda: _guarded_call("get_inventory", merchant_id, load,
                                      {"sku": platform_sku}),
            )
        except PlatformAPIError as exc:
            return _err_json(exc)
        return json.dumps(data, ensure_ascii=False, default=str)

    # ================= 库存更新（写操作，强制幂等键） =================
    @mcp.tool(
        description="更新本平台某 SKU 的可售库存。写操作，idempotency_key 必填，"
        "时间窗内重复提交自动去重（防重复扣库存）。"
    )
    async def update_inventory(
        platform_sku: str = Field(description="平台侧商品编码"),
        quantity: int = Field(ge=0, description="目标可售库存"),
        merchant_id: str = Field(description="商家 ID"),
        idempotency_key: str = Field(
            description="幂等键：sha256(merchant:sku:op:时间窗)，由调用方生成"
        ),
    ) -> str:
        async def do_write():
            inv = await adapter.update_inventory(platform_sku, quantity,
                                                 merchant_id, idempotency_key)
            # 写后失效缓存，保证下次读到新值
            r = await get_redis()
            await r.delete(cache_key("inventory", platform, platform_sku, merchant_id))
            return inv.model_dump(mode="json")

        try:
            result = await run_idempotent(
                idempotency_key, merchant_id, "update_inventory",
                lambda: _guarded_call("update_inventory", merchant_id, do_write,
                                      {"sku": platform_sku, "quantity": quantity}),
            )
        except PlatformAPIError as exc:
            return _err_json(exc)
        return json.dumps(result, ensure_ascii=False, default=str)

    # ================= 订单创建（写操作，强制幂等键） =================
    @mcp.tool(
        description="在本平台创建订单。写操作，idempotency_key 必填，"
        "网络重试/A2A 重试不会导致重复下单。"
    )
    async def create_order(
        platform_sku: str = Field(description="平台侧商品编码"),
        quantity: int = Field(ge=1, description="购买数量"),
        merchant_id: str = Field(description="商家 ID"),
        idempotency_key: str = Field(description="幂等键"),
    ) -> str:
        async def do_write():
            order = await adapter.create_order(platform_sku, quantity,
                                               merchant_id, idempotency_key)
            return order.model_dump(mode="json")

        try:
            result = await run_idempotent(
                idempotency_key, merchant_id, "create_order",
                lambda: _guarded_call("create_order", merchant_id, do_write,
                                      {"sku": platform_sku, "quantity": quantity}),
            )
        except PlatformAPIError as exc:
            return _err_json(exc)
        return json.dumps(result, ensure_ascii=False, default=str)

    # ================= 订单查询（只读，订单聚合场景） =================
    @mcp.tool(description="查询商家在本平台的订单列表，返回统一订单模型（金额已换算人民币）。")
    async def list_orders(
        merchant_id: str = Field(description="商家 ID"),
    ) -> str:
        async def load():
            orders = await adapter.list_orders(merchant_id)
            return [o.model_dump(mode="json") for o in orders]

        try:
            data = await _guarded_call("list_orders", merchant_id, load, {})
        except PlatformAPIError as exc:
            return _err_json(exc)
        return json.dumps({"orders": data}, ensure_ascii=False, default=str)

    # ================= 物流跟踪（只读） =================
    @mcp.tool(description="按平台订单号跟踪物流，返回统一物流模型（承运商/状态已归一化）。")
    async def track_logistics(
        platform_order_id: str = Field(description="平台侧订单号"),
        merchant_id: str = Field(description="商家 ID"),
    ) -> str:
        async def load():
            lg = await adapter.track_logistics(platform_order_id, merchant_id)
            return lg.model_dump(mode="json")

        try:
            data = await _guarded_call("track_logistics", merchant_id, load,
                                       {"order": platform_order_id})
        except PlatformAPIError as exc:
            return _err_json(exc)
        return json.dumps(data, ensure_ascii=False, default=str)

    # ================= 商品上架（写操作，强制幂等键） =================
    @mcp.tool(
        description="在本平台发布新商品（标题/价格/库存/类目）。写操作，"
        "idempotency_key 必填；平台类目 ID 由编排层的类目映射给出。"
    )
    async def publish_product(
        title: str = Field(description="商品标题（已按本平台规范适配）"),
        price_cny: float = Field(gt=0, description="人民币价格（跨境平台自动换算）"),
        stock: int = Field(ge=0, description="初始可售库存"),
        merchant_id: str = Field(description="商家 ID"),
        idempotency_key: str = Field(description="幂等键"),
        platform_category_id: str = Field(default="", description="平台叶子类目 ID"),
    ) -> str:
        product = {
            "title": title,
            "price_cny": price_cny,
            "price_usd": round(price_cny / 7.15, 2),
            "stock": stock,
            "platform_category_id": platform_category_id,
        }

        async def do_write():
            sku = await adapter.publish_product(product, merchant_id, idempotency_key)
            return {"platform": platform, "platform_sku": sku, "status": "listed"}

        try:
            result = await run_idempotent(
                idempotency_key, merchant_id, "publish_product",
                lambda: _guarded_call("publish_product", merchant_id, do_write,
                                      {"title": title}),
            )
        except PlatformAPIError as exc:
            return _err_json(exc)
        return json.dumps(result, ensure_ascii=False)

    return mcp


def build_idempotency_key(merchant_id: str, sku: str, operation: str) -> str:
    """便捷函数：供业务 Agent 生成写操作幂等键。"""
    s = get_settings()
    return make_idempotency_key(merchant_id, sku, operation, s.idempotency_window_seconds)
