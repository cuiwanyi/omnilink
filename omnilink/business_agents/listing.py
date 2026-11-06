"""上架 Agent：一键多平台上架。

链路：平台合规校验（Amazon 需 UPC / 京东需品牌授权）→
类目映射（统一类目树 → 平台叶子类目 ID）→ 并行委派各平台 product_publish。
不合规的平台单独标注原因，不影响其他平台上架（子任务级降级）。
"""
import asyncio
from typing import Any, Callable

from omnilink.adapters import ADAPTER_CLASSES
from omnilink.business_agents.a2a_client import A2ADelegator
from omnilink.common.idempotency import make_idempotency_key
from omnilink.common.models import PLATFORMS
from omnilink.config import get_settings

StatusCallback = Callable[[str, str, str], None]

# 平台合规要求（模拟各平台真实上架资质要求）
COMPLIANCE_RULES: dict[str, tuple[str, str]] = {
    "jd": ("brand_auth", "京东上架需品牌授权书"),
    "amazon": ("upc", "Amazon 上架需 UPC 码"),
}

# 统一类目树 → 平台叶子类目 ID 的反查表（由适配器的 category_map 生成）
_REVERSE_CATEGORY: dict[str, dict[str, str]] = {
    platform: {v: k for k, v in cls.category_map.items()}
    for platform, cls in ADAPTER_CLASSES.items()
}


async def publish_to_platforms(
    product: dict,
    merchant_id: str,
    platforms: list[str] | None = None,
    on_status: StatusCallback | None = None,
) -> dict[str, Any]:
    """product: {title, price_cny, stock, category_path, brand_auth?, upc?}"""
    settings = get_settings()
    platforms = platforms or PLATFORMS
    delegator = A2ADelegator()

    def note(platform: str, state: str, text: str) -> None:
        if on_status:
            on_status(platform, state, text)

    # 1. 平台合规校验 + 类目映射
    ready: list[str] = []
    blocked: list[dict] = []
    cat_ids: dict[str, str] = {}
    for p in platforms:
        rule = COMPLIANCE_RULES.get(p)
        if rule and not product.get(rule[0]):
            blocked.append({"platform": p, "reason": rule[1]})
            continue
        cat_id = _REVERSE_CATEGORY.get(p, {}).get(product.get("category_path", ""), "")
        cat_ids[p] = cat_id
        ready.append(p)

    # 2. 并行委派各平台 Agent 执行上架（每个平台自治处理本平台规范）
    async def _publish(platform: str) -> dict:
        idem_key = make_idempotency_key(
            merchant_id, f"publish:{product['title']}:{platform}",
            "product_publish", settings.idempotency_window_seconds,
        )
        return await delegator.delegate(
            platform, "product_publish",
            {"title": product["title"], "price_cny": product["price_cny"],
             "stock": product["stock"], "idempotency_key": idem_key,
             "platform_category_id": cat_ids[platform]},
            merchant_id, on_status=note,
        )

    results = await asyncio.gather(*[_publish(p) for p in ready])
    await delegator.close()

    listed, failed = [], []
    for platform, res in zip(ready, results):
        data = res.get("data") or {}
        if res["status"] == "completed" and "error" not in data:
            listed.append({"platform": platform,
                           "platform_sku": data.get("platform_sku")})
        else:
            failed.append({"platform": platform,
                           "reason": data.get("error") or res.get("error")})

    return {
        "title": product["title"],
        "listed": listed,
        "failed": failed,
        "compliance_blocked": blocked,
    }
