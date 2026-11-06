"""库存配额分配：防超卖核心。

跨平台做不到绝对实时一致（各平台 API 有延迟），因此可用库存不直接共享总量，
而是按平台权重做配额分配——单平台超卖不影响其他平台，剩余风险用
"优先高利润平台"的策略兜底。

配额公式：platform_quota = floor(total_available * weight)，余数归权重最高的平台。
"""
from omnilink.common.models import PLATFORMS

# 平台权重：按利润率/流量分配（高利润平台多分）
DEFAULT_WEIGHTS: dict[str, float] = {
    "taobao": 0.35,
    "jd": 0.25,
    "amazon": 0.25,
    "shopify": 0.15,
}


def allocate_quota(
    total_available: int,
    weights: dict[str, float] | None = None,
    platforms: list[str] | None = None,
) -> dict[str, int]:
    """把总可售库存按权重切成各平台配额。"""
    weights = weights or DEFAULT_WEIGHTS
    platforms = platforms or PLATFORMS
    active = [p for p in platforms if p in weights]
    weight_sum = sum(weights[p] for p in active)
    quota: dict[str, int] = {}
    allocated = 0
    for p in active:
        q = int(total_available * weights[p] / weight_sum)
        quota[p] = q
        allocated += q
    # 余数补给权重最高的平台
    if active:
        top = max(active, key=lambda p: weights[p])
        quota[top] += total_available - allocated
    return quota


def would_oversell(requested: int, platform: str, current_sold: dict[str, int],
                   total_available: int, weights: dict[str, float] | None = None) -> bool:
    """判定某平台再卖 requested 件是否超出其配额（超卖防护检查）。"""
    quota = allocate_quota(total_available, weights)
    return current_sold.get(platform, 0) + requested > quota.get(platform, 0)
