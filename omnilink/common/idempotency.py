"""幂等键设计：写操作（下单/改库存/上架）的安全底线。

网络重试、A2A Task 重试都可能导致同一写请求到达平台多次。
幂等键 = merchant_id + sku + operation + 时间窗 的哈希：
- 同一个商家对同一个 SKU 的同一类写操作，在时间窗内只生效一次
- 平台 Agent 执行写工具前先查幂等表，命中则拒绝并返回 DUPLICATE_OPERATION
- 时间窗外视为新的业务意图（如 5 分钟后商家再次主动改库存）
"""
import hashlib
import time


def make_idempotency_key(
    merchant_id: str,
    sku: str,
    operation: str,
    window_seconds: int = 300,
    timestamp: float | None = None,
) -> str:
    """生成幂等键。timestamp 可注入便于测试。"""
    ts = time.time() if timestamp is None else timestamp
    window = int(ts) // window_seconds
    raw = f"{merchant_id}:{sku}:{operation}:{window}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]
