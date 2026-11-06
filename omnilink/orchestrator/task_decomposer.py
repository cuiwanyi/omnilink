"""任务分解器：把商家的自然语言指令分解为可并行的子任务图。

主路径：云端 LLM 输出结构化子任务 JSON（含依赖关系）；
降级路径：规则引擎按关键词匹配（离线/无 Key 可演示）。
"""
import re
from typing import Any

from omnilink.orchestrator.llm import chat_json

DECOMPOSE_SYSTEM = """你是跨平台电商编排器的任务分解器。商家说一句话，你分解为子任务图。

可用的业务 Agent 与动作：
- price_comparison / compare_price：跨平台比价。params: {"query": "商品关键词"}
- inventory / sync_inventory：跨平台库存同步。params: {"product_keyword": "商品关键词", "source_platform": "taobao|jd|amazon|shopify", "target_platform": "..."}
- listing / publish：一键多平台上架。params: {"title": "商品标题", "price_cny": 数字, "stock": 数字, "category_path": "如 数码/耳机/蓝牙耳机", "platforms": ["taobao", ...]}
- order / aggregate_orders：跨平台订单聚合+物流跟踪。params: {}

平台名映射：淘宝=taobao，京东=jd，亚马逊/Amazon=amazon，Shopify=shopify。

输出 JSON（不要输出任何其他内容）：
{"subtasks": [{"id": "t1", "agent": "...", "action": "...", "params": {...}, "description": "一句话说明"}]}

规则：
- 复合指令拆多个子任务（如"比价+库存同步"拆两个）
- 只输出 JSON，subtasks 可为空数组"""

_PLATFORM_WORDS = {
    "淘宝": "taobao", "京东": "jd", "亚马逊": "amazon",
    "amazon": "amazon", "shopify": "shopify",
}
# 已知商品线索词（规则引擎离线兜底；LLM 模式下由大模型自由提取）
_PRODUCT_HINTS = ["蓝牙耳机", "耳机", "手机壳", "充电宝", "移动电源", "充电器"]
_VERB_WORDS = ["帮我", "看下", "看看", "查一下", "哪个平台", "卖得最便宜", "最便宜",
               "卖断货了", "卖断货", "断货了", "断货", "库存", "同步过去", "同步到",
               "那边", "把", "的", "了", "一下", "上架", "发布到", "所有平台",
               "订单", "物流", "聚合", "汇总", "新品"]


def _extract_platforms(text: str) -> list[str]:
    text_lower = text.lower()
    return [p for w, p in _PLATFORM_WORDS.items() if w in text_lower]


def _extract_keyword(text: str) -> str:
    """从指令中提取商品关键词：优先命中已知商品线索词，否则剔除动词/平台词后取主干。"""
    text_lower = text.lower()
    for hint in _PRODUCT_HINTS:
        if hint in text_lower:
            return hint
    cleaned = text_lower
    for w in list(_PLATFORM_WORDS.keys()) + _VERB_WORDS:
        cleaned = cleaned.replace(w, " ")
    cleaned = re.sub(r"[^\w一-鿿]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or text[:10]


def _source_target_for_stockout(text: str) -> tuple[str, str]:
    """解析 'X那边卖断货了，把Y的库存同步过去'：断货方是目标，'把'后面是源。"""
    text_lower = text.lower()
    mentions = sorted(
        ((text_lower.find(w), p) for w, p in _PLATFORM_WORDS.items() if w in text_lower),
        key=lambda x: x[0],
    )
    if not mentions:
        return "taobao", "shopify"
    if len(mentions) == 1:
        only = mentions[0][1]
        return ("taobao" if only != "taobao" else "jd"), only
    idx_break = text_lower.find("断货")
    idx_ba = text_lower.find("把")
    target = mentions[0][1]
    source = mentions[-1][1]
    if idx_break >= 0:  # 断货词前最近的平台是缺货方（目标）
        before = [m for m in mentions if m[0] < idx_break]
        if before:
            target = before[-1][1]
    if idx_ba >= 0:  # "把"字后第一个平台是货源方（源）
        after = [m for m in mentions if m[0] > idx_ba]
        if after:
            source = after[0][1]
    if source == target:  # 防呆：源和目标不能相同
        source, target = ("taobao", target) if target != "taobao" else ("jd", target)
    return source, target


def _rule_decompose(instruction: str) -> list[dict[str, Any]]:
    """规则引擎降级路径。"""
    subtasks: list[dict[str, Any]] = []
    platforms = _extract_platforms(instruction)
    keyword = _extract_keyword(instruction)
    n = 0

    def nid() -> str:
        nonlocal n
        n += 1
        return f"t{n}"

    if any(w in instruction for w in ("便宜", "比价", "价格")):
        subtasks.append({
            "id": nid(), "agent": "price_comparison", "action": "compare_price",
            "params": {"query": keyword}, "description": f"跨平台比价：{keyword}",
        })
    if any(w in instruction for w in ("断货", "库存同步", "同步")) and "库存" in instruction:
        source, target = _source_target_for_stockout(instruction)
        subtasks.append({
            "id": nid(), "agent": "inventory", "action": "sync_inventory",
            "params": {"product_keyword": keyword,
                       "source_platform": source, "target_platform": target},
            "description": f"库存同步：{source} → {target}",
        })
    if any(w in instruction for w in ("上架", "发布")) and "库存" not in instruction:
        subtasks.append({
            "id": nid(), "agent": "listing", "action": "publish",
            "params": {"title": keyword, "price_cny": 99.0, "stock": 100,
                       "category_path": "数码/耳机/蓝牙耳机",
                       "platforms": platforms or None},
            "description": f"多平台上架：{keyword}",
        })
    if "订单" in instruction:
        subtasks.append({
            "id": nid(), "agent": "order", "action": "aggregate_orders",
            "params": {}, "description": "跨平台订单聚合+物流跟踪",
        })
    if not subtasks:
        subtasks.append({
            "id": nid(), "agent": "price_comparison", "action": "compare_price",
            "params": {"query": keyword}, "description": f"跨平台比价：{keyword}（默认动作）",
        })
    return subtasks


async def decompose(instruction: str) -> tuple[list[dict[str, Any]], str]:
    """返回 (subtasks, mode)。mode ∈ {llm, rules}。"""
    result = await chat_json(DECOMPOSE_SYSTEM, instruction)
    if result and isinstance(result.get("subtasks"), list) and result["subtasks"]:
        valid = []
        for i, st in enumerate(result["subtasks"]):
            if st.get("agent") and st.get("action"):
                st.setdefault("id", f"t{i + 1}")
                st.setdefault("params", {})
                st.setdefault("description", st["action"])
                valid.append(st)
        if valid:
            return valid, "llm"
    return _rule_decompose(instruction), "rules"
