"""幂等执行器：写操作的系统级防重（与平台侧幂等键双重保险）。

执行写工具前先查幂等表：
- 命中且已成功 → 直接返回首次结果，不重复调用平台（防重复下单/重复扣库存）
- 未命中 → 调用平台并记录结果
"""
import json
from datetime import datetime, timezone
from typing import Awaitable, Callable

from omnilink.common.errors import ErrorCode, PlatformAPIError
from omnilink.services.database import execute, fetchone


async def run_idempotent(
    idempotency_key: str,
    merchant_id: str,
    operation: str,
    executor: Callable[[], Awaitable[dict]],
) -> dict:
    """幂等执行写操作。重复请求返回首次结果并标注 deduplicated=True。"""
    row = await fetchone(
        "SELECT result FROM idempotency_records WHERE key=?", (idempotency_key,)
    )
    if row is not None:
        result = json.loads(row[0])
        result["deduplicated"] = True
        return result
    try:
        result = await executor()
    except PlatformAPIError as exc:
        # 平台侧已识别为重复请求：视为成功路径的幂等命中
        if exc.code == ErrorCode.DUPLICATE_OPERATION:
            return {"deduplicated": True, "platform_confirmed": True,
                    "idempotency_key": idempotency_key}
        raise
    await execute(
        "INSERT INTO idempotency_records (key, merchant_id, operation, result, created_at) "
        "VALUES (?,?,?,?,?)",
        (idempotency_key, merchant_id, operation,
         json.dumps(result, ensure_ascii=False, default=str),
         datetime.now(timezone.utc).isoformat()),
    )
    return result
