"""SKU 主数据映射表：跨平台实体对齐的持久层。

- 已确认映射直接查表（90% 请求命中，跳过 LLM）
- 新 SKU 走 向量召回(rapidfuzz 降级版) → LLM 精判 → 置信度分级：
  >0.9 自动入库；0.7~0.9 进人工审核队列；<0.7 不建映射
"""
import json
from datetime import datetime, timezone

from omnilink.services.database import execute, fetchall, fetchone


class SkuMappingStore:
    """跨平台 SKU 映射主数据。"""

    # ---------- 查询 ----------
    @staticmethod
    async def to_unified(platform: str, platform_sku: str) -> str | None:
        """平台 SKU → 统一 SKU（命中即返回，未命中返回 None 走对齐流程）。"""
        row = await fetchone(
            "SELECT unified_sku FROM sku_mappings WHERE platform=? AND platform_sku=? "
            "AND source != 'review_pending'",
            (platform, platform_sku),
        )
        return row[0] if row else None

    @staticmethod
    async def to_platform_sku(unified_sku: str, platform: str) -> str | None:
        """统一 SKU → 某平台 SKU。"""
        row = await fetchone(
            "SELECT platform_sku FROM sku_mappings WHERE unified_sku=? AND platform=? "
            "AND source != 'review_pending'",
            (unified_sku, platform),
        )
        return row[0] if row else None

    @staticmethod
    async def mappings_of(unified_sku: str) -> dict[str, str]:
        """统一 SKU 的所有平台映射：{platform: platform_sku}。"""
        rows = await fetchall(
            "SELECT platform, platform_sku FROM sku_mappings WHERE unified_sku=? "
            "AND source != 'review_pending'",
            (unified_sku,),
        )
        return {r[0]: r[1] for r in rows}

    @staticmethod
    async def all_canonical() -> list[dict]:
        """统一商品主数据全集（候选召回池）。"""
        rows = await fetchall("SELECT unified_sku, canonical_title, attributes FROM unified_products")
        return [{"unified_sku": r[0], "title": r[1], "attributes": json.loads(r[2])} for r in rows]

    @staticmethod
    async def review_queue() -> list[dict]:
        """人工审核队列：LLM 置信度 0.7~0.9 的映射。"""
        rows = await fetchall(
            "SELECT unified_sku, platform, platform_sku, confidence, created_at "
            "FROM sku_mappings WHERE source='review_pending' ORDER BY created_at DESC"
        )
        return [{"unified_sku": r[0], "platform": r[1], "platform_sku": r[2],
                 "confidence": r[3], "created_at": r[4]} for r in rows]

    @staticmethod
    async def review_confirm(platform: str, platform_sku: str, approve: bool) -> bool:
        """人工审核：通过则转正式映射，拒绝则删除。"""
        row = await fetchone(
            "SELECT unified_sku FROM sku_mappings WHERE platform=? AND platform_sku=? "
            "AND source='review_pending'",
            (platform, platform_sku),
        )
        if not row:
            return False
        if approve:
            await execute(
                "UPDATE sku_mappings SET source='review_confirmed' "
                "WHERE platform=? AND platform_sku=?",
                (platform, platform_sku),
            )
        else:
            await execute(
                "DELETE FROM sku_mappings WHERE platform=? AND platform_sku=?",
                (platform, platform_sku),
            )
        return True

    # ---------- 写入 ----------
    @staticmethod
    async def save(
        unified_sku: str,
        platform: str,
        platform_sku: str,
        confidence: float,
        source: str,
    ) -> None:
        await execute(
            "INSERT OR REPLACE INTO sku_mappings "
            "(unified_sku, platform, platform_sku, confidence, source, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (unified_sku, platform, platform_sku, confidence, source,
             datetime.now(timezone.utc).isoformat()),
        )

    @staticmethod
    async def save_canonical(unified_sku: str, title: str, attributes: dict) -> None:
        await execute(
            "INSERT OR REPLACE INTO unified_products (unified_sku, canonical_title, attributes) "
            "VALUES (?,?,?)",
            (unified_sku, title, json.dumps(attributes, ensure_ascii=False)),
        )


async def seed_mappings() -> None:
    """演示种子：4 个统一商品；其中 PRO9 只映射了淘宝（其余平台走对齐流程演示）。"""
    from omnilink.mock_platforms.data import CANONICAL_PRODUCTS

    for p in CANONICAL_PRODUCTS:
        await SkuMappingStore.save_canonical(p["unified_sku"], p["platforms"]["taobao"]["title"]
                                             if "taobao" in p["platforms"] else next(iter(p["platforms"].values()))["title"],
                                             p["attributes"])
        for platform, info in p["platforms"].items():
            if p["unified_sku"] == "USKU-EARPHONE-PRO9" and platform != "taobao":
                continue  # 刻意留白：演示 LLM 对齐
            await SkuMappingStore.save(p["unified_sku"], platform, info["sku"], 1.0, "manual")
