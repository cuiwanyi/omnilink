"""库存 Agent：跨平台库存同步（防超卖链路完整演示）。

链路：源平台查询 → SKU 对齐（映射表）→ Redis 分布式锁 →
配额防超卖检查 → 幂等委派目标平台写入 → 审计。
对应文档 5.1：Shopify 卖断货（37件），把淘宝库存（800件）同步过去。
"""
from typing import Any, Callable

from omnilink.business_agents.a2a_client import A2ADelegator
from omnilink.common.idempotency import make_idempotency_key
from omnilink.config import get_settings
from omnilink.services.cache import distributed_lock
from omnilink.services.quota import allocate_quota
from omnilink.services.sku_mapping import SkuMappingStore

StatusCallback = Callable[[str, str, str], None]


async def sync_inventory(
    source_platform: str,
    source_sku: str,
    target_platform: str,
    merchant_id: str,
    quantity: int | None = None,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    delegator = A2ADelegator()

    def note(platform: str, state: str, text: str) -> None:
        if on_status:
            on_status(platform, state, text)

    # 1. 委派源平台 Agent 查询库存
    src = await delegator.delegate(
        source_platform, "inventory_query",
        {"platform_sku": source_sku}, merchant_id, on_status=note,
    )
    if src["status"] != "completed" or "error" in (src.get("data") or {}):
        await delegator.close()
        return {"status": "failed", "stage": "source_query",
                "reason": (src.get("data") or {}).get("error") or src.get("error")}
    source_inv = src["data"]
    available = int(source_inv["available"])

    # 2. SKU 对齐：源 SKU → 统一 SKU → 目标平台 SKU（映射表命中才执行写操作）
    unified_sku = await SkuMappingStore.to_unified(source_platform, source_sku)
    if not unified_sku:
        await delegator.close()
        return {"status": "failed", "stage": "sku_alignment",
                "reason": f"{source_platform}/{source_sku} 未映射统一 SKU，需人工确认"}
    target_sku = await SkuMappingStore.to_platform_sku(unified_sku, target_platform)
    if not target_sku:
        await delegator.close()
        return {"status": "failed", "stage": "sku_alignment",
                "reason": f"统一 SKU {unified_sku} 在 {target_platform} 无映射，"
                          f"需走 SKU 对齐流程或人工确认"}

    # 3. 配额防超卖：同步量不超过目标平台配额
    quota = allocate_quota(available)
    target_quota = quota.get(target_platform, available)
    desired = quantity if quantity is not None else available
    final_qty = min(desired, target_quota)
    quota_note = (f"总可售 {available} 件按权重分配，{target_platform} 配额 "
                  f"{target_quota} 件，实际同步 {final_qty} 件")

    # 4. 分布式锁串行化同一 SKU 的库存写操作（防并发超卖）
    async with distributed_lock(f"inventory:{unified_sku}") as locked:
        if not locked:
            await delegator.close()
            return {"status": "failed", "stage": "lock",
                    "reason": f"SKU {unified_sku} 库存同步进行中，拒绝并发写"}
        # 5. 幂等委派目标平台写入
        idem_key = make_idempotency_key(
            merchant_id, f"{unified_sku}:{target_platform}",
            "inventory_update", settings.idempotency_window_seconds,
        )
        dst = await delegator.delegate(
            target_platform, "inventory_update",
            {"platform_sku": target_sku, "quantity": final_qty,
             "idempotency_key": idem_key},
            merchant_id, on_status=note,
        )
    await delegator.close()

    ok = dst["status"] == "completed" and "error" not in (dst.get("data") or {})
    return {
        "status": "completed" if ok else "failed",
        "unified_sku": unified_sku,
        "source": {"platform": source_platform, "sku": source_sku,
                   "before_available": available},
        "target": {"platform": target_platform, "sku": target_sku,
                   "synced_quantity": final_qty,
                   "response": dst.get("data")},
        "quota_note": quota_note,
        "idempotency_key": idem_key,
    }
