"""SKU 对齐：跨平台实体对齐（同一商品四个平台四个标题/编码）。

三级漏斗：
1. 映射表直查（90% 请求命中，零 LLM 开销）——在调用方完成
2. 向量召回（本地用 rapidfuzz 模糊匹配降级，生产用 Milvus 余弦相似度）取候选
3. LLM 精判置信度：>0.9 自动入库；0.7~0.9 进人工审核队列；<0.7 不建映射
"""
import json
from typing import Any

from rapidfuzz import fuzz, process

from omnilink.orchestrator.llm import chat_json
from omnilink.services.sku_mapping import SkuMappingStore

AUTO_CONFIRM_THRESHOLD = 0.9
REVIEW_THRESHOLD = 0.7

_JUDGE_SYSTEM = """你是电商商品主数据专家。判断"待对齐商品"与"候选统一商品"是否为同一商品。
输出 JSON：{"best_match": "候选的unified_sku或null", "confidence": 0.0~1.0, "reason": "一句话理由"}
判断要点：品牌、品类、型号/规格、关键属性是否一致；仅标题措辞不同不扣分。"""


async def _recall_candidates(title: str, top_k: int = 5) -> list[dict]:
    """向量召回降级实现：rapidfuzz 模糊匹配（生产：Embedding + Milvus）。

    中英文混合标题：子串包含记满分，否则 partial_ratio（对 CJK 友好）。
    """
    pool = await SkuMappingStore.all_canonical()
    if not pool:
        return []
    t = title.lower()
    for c in pool:
        ct = c["title"].lower()
        c["recall_score"] = round(
            1.0 if t and t in ct else fuzz.partial_ratio(t, ct) / 100, 3
        )
    pool.sort(key=lambda c: c["recall_score"], reverse=True)
    return pool[:top_k]


def _attr_score(attributes: dict, candidate_attributes: dict) -> float:
    """属性匹配度：待对齐商品的属性值在候选商品属性值中的命中率。"""
    if not attributes or not candidate_attributes:
        return 0.0
    cand_values = [str(v).lower() for v in candidate_attributes.values()]
    hits = sum(
        1 for v in attributes.values()
        if any(str(v).lower() in cv or cv in str(v).lower() for cv in cand_values)
    )
    return hits / len(attributes)


async def _llm_judge(title: str, attributes: dict,
                     candidates: list[dict]) -> dict[str, Any] | None:
    user = json.dumps({
        "待对齐商品": {"title": title, "attributes": attributes},
        "候选统一商品": [
            {"unified_sku": c["unified_sku"], "title": c["title"],
             "attributes": c["attributes"]} for c in candidates
        ],
    }, ensure_ascii=False)
    return await chat_json(_JUDGE_SYSTEM, user)


async def align_new_sku(
    platform: str, platform_sku: str, title: str, attributes: dict
) -> dict[str, Any]:
    """对一个未映射的平台 SKU 执行完整对齐流程。"""
    candidates = await _recall_candidates(title)
    if not candidates:
        return {"status": "no_mapping", "reason": "候选池为空"}

    judged = await _llm_judge(title, attributes, candidates)
    if judged and judged.get("best_match"):
        best = judged["best_match"]
        confidence = float(judged.get("confidence", 0))
        reason = judged.get("reason", "")
    else:
        # LLM 不可用降级：标题相似度与属性匹配各占一半（模糊匹配弱于语义判断，降权 0.9）
        best = candidates[0]["unified_sku"]
        attr = _attr_score(attributes, candidates[0].get("attributes", {}))
        confidence = round(
            (0.5 * candidates[0]["recall_score"] + 0.5 * attr) * 0.9, 3)
        reason = "规则降级：rapidfuzz 标题相似度 + 属性匹配"

    if confidence >= AUTO_CONFIRM_THRESHOLD:
        await SkuMappingStore.save(best, platform, platform_sku, confidence, "llm_auto")
        return {"status": "auto_confirmed", "unified_sku": best,
                "confidence": confidence, "reason": reason}
    if confidence >= REVIEW_THRESHOLD:
        await SkuMappingStore.save(best, platform, platform_sku, confidence,
                                   "review_pending")
        return {"status": "review_pending", "unified_sku": best,
                "confidence": confidence, "reason": reason,
                "note": "已进人工审核队列，审核通过前不参与跨平台联动"}
    return {"status": "no_mapping", "best_candidate": best,
            "confidence": confidence, "reason": reason}
