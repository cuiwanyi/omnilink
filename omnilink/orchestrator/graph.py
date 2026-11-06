"""LangGraph 编排层：Orchestrator 的 StateGraph。

图结构：
    START → decompose（LLM 任务分解）
          → Send 扇出到 4 个业务 Agent 节点（独立子任务并行执行）
          → summarize（汇总结果+降级标注）
          → END

SSE 播报：所有节点把状态事件 put 进 state["bus"]（asyncio.Queue），
API 层消费队列转成 SSE 流推给商家工作台。
"""
import asyncio
import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from rapidfuzz import fuzz, process

from omnilink.business_agents import (
    aggregate_orders,
    compare_price,
    publish_to_platforms,
    sync_inventory,
)
from omnilink.common.models import PLATFORMS
from omnilink.orchestrator.task_decomposer import decompose
from omnilink.services.sku_mapping import SkuMappingStore


def _merge_dicts(a: dict, b: dict) -> dict:
    return {**a, **b}


class OrchestratorState(TypedDict):
    merchant_id: str
    instruction: str
    bus: Any  # asyncio.Queue：SSE 事件总线
    mode: str
    subtasks: list[dict]
    results: Annotated[dict, _merge_dicts]
    events: Annotated[list, operator.add]
    final_answer: str


class SubtaskState(TypedDict):
    merchant_id: str
    subtask: dict
    bus: Any


# ================= 节点实现 =================

async def decompose_node(state: OrchestratorState) -> dict:
    bus: asyncio.Queue = state["bus"]
    bus.put_nowait({"type": "status", "text": "Orchestrator 正在分解任务..."})
    subtasks, mode = await decompose(state["instruction"])
    bus.put_nowait({
        "type": "subtasks", "mode": mode,
        "text": f"任务分解完成（{'云端大模型' if mode == 'llm' else '规则引擎'}），"
                f"共 {len(subtasks)} 个子任务",
        "subtasks": subtasks,
    })
    return {"subtasks": subtasks, "mode": mode,
            "events": [f"分解出 {len(subtasks)} 个子任务"]}


def dispatch(state: OrchestratorState):
    """把子任务扇出到对应业务 Agent 节点（并行）。"""
    node_map = {
        "price_comparison": "price_agent",
        "inventory": "inventory_agent",
        "listing": "listing_agent",
        "order": "order_agent",
    }
    sends = [
        Send(node_map[st["agent"]], {
            "merchant_id": state["merchant_id"], "subtask": st, "bus": state["bus"],
        })
        for st in state["subtasks"]
        if st.get("agent") in node_map
    ]
    return sends or "summarize"


def _status_cb(task_id: str, bus: asyncio.Queue):
    def cb(platform: str, state: str, text: str) -> None:
        bus.put_nowait({
            "type": "task_status", "task": task_id, "platform": platform,
            "state": state, "text": text or f"{platform} Agent: {state}",
        })
    return cb


async def price_node(state: SubtaskState) -> dict:
    t, bus = state["subtask"], state["bus"]
    bus.put_nowait({"type": "status", "text": f"[{t['id']}] 比价 Agent：并行委派四平台 Agent..."})
    result = await compare_price(
        t["params"].get("query", ""), state["merchant_id"],
        on_status=_status_cb(t["id"], bus),
    )
    bus.put_nowait({"type": "status", "text": f"[{t['id']}] 比价完成"})
    return {"results": {t["id"]: {"type": "compare_price", **result}},
            "events": [f"{t['id']} 比价完成"]}


async def inventory_node(state: SubtaskState) -> dict:
    t, bus = state["subtask"], state["bus"]
    params = t["params"]
    keyword = params.get("product_keyword", "")
    source, target = params.get("source_platform", "taobao"), params.get("target_platform", "shopify")
    bus.put_nowait({"type": "status",
                    "text": f"[{t['id']}] 库存 Agent：解析商品关键词 '{keyword}' → 统一 SKU..."})

    unified = await _resolve_unified_sku(keyword)
    if not unified:
        result = {"status": "failed", "stage": "sku_resolution",
                  "reason": f"无法从关键词 '{keyword}' 匹配统一 SKU"}
    else:
        mappings = await SkuMappingStore.mappings_of(unified)
        source_sku = mappings.get(source)
        if not source_sku:
            result = {"status": "failed", "stage": "sku_alignment",
                      "reason": f"统一 SKU {unified} 在源平台 {source} 无映射"}
        else:
            bus.put_nowait({"type": "status",
                            "text": f"[{t['id']}] 库存 Agent：{source}/{source_sku} → "
                                    f"{target}，加分布式锁并幂等委派..."})
            result = await sync_inventory(
                source, source_sku, target, state["merchant_id"],
                on_status=_status_cb(t["id"], bus),
            )
    bus.put_nowait({"type": "status", "text": f"[{t['id']}] 库存同步处理完毕：{result['status']}"})
    return {"results": {t["id"]: {"type": "sync_inventory", **result}},
            "events": [f"{t['id']} 库存同步 {result['status']}"]}


async def listing_node(state: SubtaskState) -> dict:
    t, bus = state["subtask"], state["bus"]
    params = dict(t["params"])
    params.setdefault("platforms", PLATFORMS)
    bus.put_nowait({"type": "status", "text": f"[{t['id']}] 上架 Agent：合规校验+类目映射..."})
    result = await publish_to_platforms(
        params, state["merchant_id"], on_status=_status_cb(t["id"], bus),
    )
    bus.put_nowait({"type": "status", "text": f"[{t['id']}] 上架完成"})
    return {"results": {t["id"]: {"type": "publish", **result}},
            "events": [f"{t['id']} 上架完成"]}


async def order_node(state: SubtaskState) -> dict:
    t, bus = state["subtask"], state["bus"]
    bus.put_nowait({"type": "status", "text": f"[{t['id']}] 订单 Agent：并行聚合四平台订单..."})
    result = await aggregate_orders(
        state["merchant_id"], on_status=_status_cb(t["id"], bus),
    )
    bus.put_nowait({"type": "status", "text": f"[{t['id']}] 订单聚合完成"})
    return {"results": {t["id"]: {"type": "aggregate_orders", **result}},
            "events": [f"{t['id']} 订单聚合完成"]}


async def summarize_node(state: OrchestratorState) -> dict:
    answer = _summarize(state["subtasks"], state["results"])
    state["bus"].put_nowait({"type": "status", "text": "任务全部完成，正在汇总..."})
    return {"final_answer": answer, "events": ["汇总完成"]}


# ================= 汇总文案 =================

def _summarize(subtasks: list[dict], results: dict) -> str:
    lines = ["## 执行结果汇总", ""]
    for st in subtasks:
        res = results.get(st["id"], {})
        rtype = res.get("type")
        lines.append(f"### [{st['id']}] {st.get('description', st['action'])}")
        if rtype == "compare_price":
            if res.get("cheapest"):
                c = res["cheapest"]
                lines.append(f"- 最便宜平台：**{c['platform']}** ¥{c['price_cny']}（{c['original']}）")
                for row in res.get("comparison", []):
                    lines.append(f"  - {row['platform']}: ¥{row['price_cny']}")
            for d in res.get("degraded_platforms", []):
                lines.append(f"- ⚠️ {d['platform']} 降级：{d['reason']}")
        elif rtype == "sync_inventory":
            if res.get("status") == "completed":
                tgt = res["target"]
                lines.append(f"- ✅ 已同步 {tgt['synced_quantity']} 件到 "
                             f"**{tgt['platform']}**（{tgt['sku']}）")
                lines.append(f"- 配额说明：{res.get('quota_note')}")
                lines.append(f"- 幂等键：`{res.get('idempotency_key')}`")
            else:
                lines.append(f"- ❌ 失败（{res.get('stage')}）：{res.get('reason')}")
        elif rtype == "publish":
            for item in res.get("listed", []):
                lines.append(f"- ✅ {item['platform']}：{item['platform_sku']}")
            for item in res.get("failed", []):
                lines.append(f"- ❌ {item['platform']}：{item['reason']}")
            for item in res.get("compliance_blocked", []):
                lines.append(f"- ⛔ {item['platform']} 合规拦截：{item['reason']}")
        elif rtype == "aggregate_orders":
            lines.append(f"- 共聚合 {res.get('total_orders', 0)} 个订单")
            for d in res.get("degraded_platforms", []):
                lines.append(f"- ⚠️ {d['platform']} 降级：{d['reason']}")
        lines.append("")
    return "\n".join(lines)


def _cjk_score(keyword: str, title: str) -> float:
    """中英文混合标题匹配分：子串包含直接满分，否则 partial_ratio（对 CJK 友好）。"""
    k, t = keyword.lower(), title.lower()
    if k and k in t:
        return 100.0
    return float(fuzz.partial_ratio(k, t))


async def _resolve_unified_sku(keyword: str) -> str | None:
    """商品关键词 → 统一 SKU（主数据模糊匹配，生产走 Embedding 向量检索）。"""
    pool = await SkuMappingStore.all_canonical()
    if not pool:
        return None
    scored = [(_cjk_score(keyword, c["title"]), c["unified_sku"]) for c in pool]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1] if scored[0][0] >= 40 else None


# ================= 图构建与执行 =================

def build_graph():
    graph = StateGraph(OrchestratorState)
    graph.add_node("decompose", decompose_node)
    graph.add_node("price_agent", price_node)
    graph.add_node("inventory_agent", inventory_node)
    graph.add_node("listing_agent", listing_node)
    graph.add_node("order_agent", order_node)
    graph.add_node("summarize", summarize_node)

    graph.add_edge(START, "decompose")
    graph.add_conditional_edges("decompose", dispatch)
    for node in ("price_agent", "inventory_agent", "listing_agent", "order_agent"):
        graph.add_edge(node, "summarize")
    graph.add_edge("summarize", END)
    return graph.compile()


async def run_orchestration(
    instruction: str, merchant_id: str, bus: asyncio.Queue
) -> dict:
    """执行完整编排，返回终态（含 final_answer 与结构化 results）。"""
    compiled = build_graph()
    return await compiled.ainvoke({
        "merchant_id": merchant_id,
        "instruction": instruction,
        "bus": bus,
        "mode": "",
        "subtasks": [],
        "results": {},
        "events": [],
        "final_answer": "",
    })
