"""订单 Agent：跨平台订单聚合 + 物流跟踪。

并行委派四平台 order_query，合并为统一订单视图（人民币计价、状态归一），
对已发货订单并行追加 logistics_track 物流跟踪。
"""
import asyncio
from typing import Any, Callable

from omnilink.business_agents.a2a_client import A2ADelegator
from omnilink.common.models import PLATFORMS

StatusCallback = Callable[[str, str, str], None]


async def aggregate_orders(
    merchant_id: str,
    platforms: list[str] | None = None,
    with_logistics: bool = True,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    platforms = platforms or PLATFORMS
    delegator = A2ADelegator()

    def note(platform: str, state: str, text: str) -> None:
        if on_status:
            on_status(platform, state, text)

    # 1. 并行委派各平台订单查询
    results = await asyncio.gather(
        *[delegator.delegate(p, "order_query", {}, merchant_id, on_status=note)
          for p in platforms],
        return_exceptions=True,
    )

    orders: list[dict] = []
    degraded: list[dict] = []
    for platform, res in zip(platforms, results):
        if isinstance(res, Exception):
            degraded.append({"platform": platform, "reason": str(res)})
            continue
        data = res.get("data") or {}
        if res["status"] != "completed" or "error" in data:
            degraded.append({"platform": platform,
                             "reason": data.get("error") or res.get("error")})
            continue
        for o in data.get("orders", []):
            orders.append({
                "platform": platform,
                "platform_order_id": o["platform_order_id"],
                "status": o["status"],
                "total_cny": float(o["total"]["amount_cny"]),
                "items": [{"sku": i["platform_sku"], "quantity": i["quantity"]}
                          for i in o.get("items", [])],
                "created_at": o.get("created_at"),
            })
    orders.sort(key=lambda o: o["created_at"] or "", reverse=True)

    # 2. 已发货订单并行追加物流跟踪
    logistics: dict[str, dict] = {}
    if with_logistics:
        shipped = [o for o in orders if o["status"] == "shipped"]

        async def _track(o: dict) -> None:
            res = await delegator.delegate(
                o["platform"], "logistics_track",
                {"platform_order_id": o["platform_order_id"]},
                merchant_id, on_status=note,
            )
            data = res.get("data") or {}
            if res["status"] == "completed" and "error" not in data:
                logistics[o["platform_order_id"]] = {
                    "tracking_no": data.get("tracking_no"),
                    "carrier": data.get("carrier"),
                    "status": data.get("status"),
                    "latest_event": (data.get("events") or [{}])[-1].get("description")
                    if data.get("events") else None,
                }

        await asyncio.gather(*[_track(o) for o in shipped])
    await delegator.close()

    return {
        "merchant_id": merchant_id,
        "total_orders": len(orders),
        "orders": orders,
        "logistics": logistics,
        "degraded_platforms": degraded,
    }
