"""比价 Agent：跨平台实时比价。

亮点（对应文档场景）：
- 并行拉取四平台 Agent 的 Agent Card（互发现）
- 并行委派比价 Task
- Amazon 限流时 Amazon Agent 内部重试，若最终失败则降级标注，不拖垮整体
- 所有价格已统一换算人民币，直接可比
"""
import asyncio
from typing import Any, Callable

from omnilink.business_agents.a2a_client import A2ADelegator
from omnilink.common.models import PLATFORMS

StatusCallback = Callable[[str, str, str], None]


async def compare_price(
    query: str,
    merchant_id: str,
    platforms: list[str] | None = None,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    platforms = platforms or PLATFORMS
    delegator = A2ADelegator()

    def note(platform: str, state: str, text: str) -> None:
        if on_status:
            on_status(platform, state, text)

    # 1. 互发现：并行拉取 Agent Card
    await asyncio.gather(
        *[delegator.discover(p) for p in platforms], return_exceptions=True
    )
    # 2. 并行委派比价 Task
    results = await asyncio.gather(
        *[delegator.delegate(p, "product_search", {"query": query}, merchant_id,
                             on_status=note) for p in platforms],
        return_exceptions=True,
    )
    await delegator.close()

    rows: list[dict] = []
    degraded: list[dict] = []
    for platform, res in zip(platforms, results):
        if isinstance(res, Exception):
            degraded.append({"platform": platform, "reason": f"delegation error: {res}"})
            continue
        if res["status"] != "completed" or not res.get("data"):
            degraded.append({"platform": platform,
                             "reason": res.get("error") or res["status"]})
            continue
        products = res["data"].get("products", [])
        if not products:
            degraded.append({"platform": platform, "reason": "该平台未在售此商品"})
            continue
        # 取与本店商品匹配度最高的（映射表已回填 unified_sku 的优先）
        best = next((p for p in products if p.get("unified_sku")), products[0])
        rows.append({
            "platform": platform,
            "unified_sku": best.get("unified_sku") or None,
            "platform_sku": best["platform_sku"],
            "title": best["title"],
            "price_cny": float(best["price"]["amount_cny"]),
            "original": f"{best['price']['original_amount']} {best['price']['original_currency']}",
            "monthly_sales": best.get("monthly_sales", 0),
            "cache_hit": res["data"].get("cache_hit", False),
        })

    rows.sort(key=lambda r: r["price_cny"])
    return {
        "query": query,
        "comparison": rows,
        "cheapest": rows[0] if rows else None,
        "degraded_platforms": degraded,  # 部分失败降级标注
    }
