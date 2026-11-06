"""审计日志：所有 A2A Task 委派与 MCP 工具调用落审计日志。

记录 谁、何时、调了什么、参数是什么、结果如何——满足电商 SaaS 的安全合规要求，
安全团队可回溯每一次跨平台操作的完整链路。
"""
import json
from datetime import datetime, timezone

from omnilink.services.database import execute, fetchall


async def audit(
    actor: str,
    action: str,
    platform: str | None = None,
    merchant_id: str | None = None,
    params: dict | None = None,
    result: str = "ok",
) -> None:
    await execute(
        "INSERT INTO audit_log (ts, actor, action, platform, merchant_id, params, result) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            datetime.now(timezone.utc).isoformat(),
            actor, action, platform, merchant_id,
            json.dumps(params, ensure_ascii=False, default=str) if params else None,
            result,
        ),
    )


async def recent_logs(limit: int = 50) -> list[dict]:
    rows = await fetchall(
        "SELECT ts, actor, action, platform, merchant_id, params, result "
        "FROM audit_log ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    return [
        {"ts": r[0], "actor": r[1], "action": r[2], "platform": r[3],
         "merchant_id": r[4], "params": r[5], "result": r[6]}
        for r in rows
    ]
