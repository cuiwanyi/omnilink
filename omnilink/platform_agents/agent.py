"""平台 Agent（A2A Server）实现，基于官方 a2a-sdk。

职责边界（A2A 与 MCP 的分工）：
- 本层管 Agent↔Agent：Agent Card 发布/发现、Task 委派、状态播报、限流重试自治
- MCP 层管 Agent↔工具：平台 API 的标准化调用

委派协议（Task 输入为 JSON 文本，生产环境用 A2A DataPart，演示用 TextPart）：
    {"skill": "product_search", "params": {...}, "merchant_id": "M001"}
"""
import asyncio
import json
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Part,
    Task,
    TaskState,
    TaskStatus,
)
from fastapi import FastAPI
from fastmcp import Client as McpClient

from omnilink.common.models import Platform
from omnilink.config import get_settings
from omnilink.services.audit import audit

# A2A 技能 → MCP 工具映射：平台 Agent 的每个技能背后是本平台的同名 MCP 工具
SKILL_TO_TOOL = {
    "product_search": "product_search",
    "inventory_query": "get_inventory",
    "inventory_update": "update_inventory",
    "order_create": "create_order",
    "logistics_track": "track_logistics",
    "product_publish": "publish_product",
    "order_query": "list_orders",
}

PLATFORM_SKILLS = [
    AgentSkill(
        id="product_search",
        name="商品查询",
        description="按关键词查询本平台在售商品，返回统一数据模型（人民币计价、统一类目树）",
        tags=["read", "product"],
        examples=["{\"skill\": \"product_search\", \"params\": {\"query\": \"蓝牙耳机\"}, \"merchant_id\": \"M001\"}"],
    ),
    AgentSkill(
        id="inventory_query",
        name="库存查询",
        description="查询本平台某 SKU 的库存三态（可售/锁定/在途）",
        tags=["read", "inventory"],
        examples=["{\"skill\": \"inventory_query\", \"params\": {\"platform_sku\": \"TB1001\"}, \"merchant_id\": \"M001\"}"],
    ),
    AgentSkill(
        id="inventory_update",
        name="库存更新",
        description="更新本平台某 SKU 的可售库存（写操作，强制幂等键）",
        tags=["write", "inventory"],
        examples=["{\"skill\": \"inventory_update\", \"params\": {\"platform_sku\": \"SHOP-EAR-001\", \"quantity\": 800, \"idempotency_key\": \"...\"}, \"merchant_id\": \"M001\"}"],
    ),
    AgentSkill(
        id="order_create",
        name="订单创建",
        description="在本平台创建订单（写操作，强制幂等键）",
        tags=["write", "order"],
        examples=["{\"skill\": \"order_create\", \"params\": {\"platform_sku\": \"TB1001\", \"quantity\": 1, \"idempotency_key\": \"...\"}, \"merchant_id\": \"M001\"}"],
    ),
    AgentSkill(
        id="logistics_track",
        name="物流跟踪",
        description="按平台订单号跟踪物流，承运商与状态跨平台归一化",
        tags=["read", "logistics"],
        examples=["{\"skill\": \"logistics_track\", \"params\": {\"platform_order_id\": \"TBORD90002\"}, \"merchant_id\": \"M001\"}"],
    ),
    AgentSkill(
        id="product_publish",
        name="商品上架",
        description="在本平台发布新商品（写操作，强制幂等键）",
        tags=["write", "listing"],
        examples=["{\"skill\": \"product_publish\", \"params\": {\"title\": \"...\", \"price_cny\": 99.0, \"stock\": 100, \"idempotency_key\": \"...\"}, \"merchant_id\": \"M001\"}"],
    ),
    AgentSkill(
        id="order_query",
        name="订单查询",
        description="查询商家在本平台的订单列表（统一订单模型，人民币计价）",
        tags=["read", "order"],
        examples=["{\"skill\": \"order_query\", \"params\": {}, \"merchant_id\": \"M001\"}"],
    ),
]

_PLATFORM_NAMES = {
    "taobao": "淘宝",
    "jd": "京东",
    "amazon": "Amazon",
    "shopify": "Shopify",
}


def build_agent_card(platform: Platform, base_url: str) -> AgentCard:
    """Agent Card：对外声明"我是谁、会什么、怎么连"，是互发现的基础。"""
    return AgentCard(
        name=f"{platform}-agent",
        description=f"{_PLATFORM_NAMES[platform]}平台 Agent：自治封装本平台开放 API，"
        f"对外提供商品/库存/订单/物流/上架六类技能",
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(
                url=f"{base_url}/a2a",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text"],
        default_output_modes=["text"],
        skills=PLATFORM_SKILLS,
    )


class PlatformAgentExecutor(AgentExecutor):
    """平台 Agent 执行器：接 Task → 调本平台 MCP 工具 → 播报状态 → 回传结果。

    自治性体现：本平台的限流/超时由本 Agent 内部指数退避重试，
    委派方无需关心平台特性。
    """

    MAX_RETRIES = 3

    def __init__(self, platform: Platform) -> None:
        self.platform = platform
        self.mcp_url = get_settings().mcp_url(platform)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        # 协议要求：首个事件必须是 Task 对象，之后才是状态更新
        if context.current_task is None:
            await event_queue.enqueue_event(Task(
                id=context.task_id, context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
            ))
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        if context.current_task is None:
            await updater.submit()
        await updater.start_work()
        try:
            command = json.loads(context.get_user_input())
            skill = command["skill"]
            params = command.get("params", {})
            merchant_id = command.get("merchant_id", "M001")
        except (json.JSONDecodeError, KeyError) as exc:
            await updater.failed(message=updater.new_agent_message(
                [Part(text=f"invalid delegation command: {exc}")]))
            return
        if skill not in SKILL_TO_TOOL:
            await updater.failed(message=updater.new_agent_message(
                [Part(text=f"unsupported skill: {skill}")]))
            return

        await audit(f"agent:{self.platform}", f"a2a_delegate:{skill}",
                    self.platform, merchant_id, params)
        try:
            # MCP 工具统一要求 merchant_id（凭证取用/缓存隔离/审计），由 Agent 注入
            tool_params = {**params, "merchant_id": merchant_id}
            payload = await self._call_tool_with_retry(
                SKILL_TO_TOOL[skill], tool_params, updater)
        except Exception as exc:  # noqa: BLE001 - Agent 边界统一兜底
            await audit(f"agent:{self.platform}", f"a2a_delegate:{skill}",
                        self.platform, merchant_id, params, result=f"error:{exc}")
            await updater.add_artifact(
                [Part(text=json.dumps(
                    {"error": {"error_code": "INTERNAL_ERROR", "message": str(exc)}},
                    ensure_ascii=False))],
                name="error",
            )
            await updater.failed()
            return

        is_error = isinstance(payload, dict) and "error" in payload
        await updater.add_artifact(
            [Part(text=json.dumps(payload, ensure_ascii=False, default=str))],
            name="error" if is_error else "result",
        )
        if is_error:
            await updater.failed(message=updater.new_agent_message(
                [Part(text=f"{self.platform} tool call failed: "
                      f"{payload['error'].get('error_code')}")]))
        else:
            await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.cancel()

    async def _call_tool_with_retry(
        self, tool: str, params: dict, updater: TaskUpdater
    ) -> Any:
        """调本平台 MCP 工具；可重试错误（限流/平台故障）指数退避。"""
        async with McpClient(self.mcp_url) as client:
            for attempt in range(self.MAX_RETRIES):
                result = await client.call_tool(tool, params)
                payload = json.loads(result.content[0].text)
                error = payload.get("error") if isinstance(payload, dict) else None
                if error and error.get("retryable") and attempt < self.MAX_RETRIES - 1:
                    wait = (error.get("retry_after") or 1.0) * (2 ** attempt)
                    await updater.update_status(
                        TaskState.TASK_STATE_WORKING,
                        message=updater.new_agent_message(
                            [Part(text=f"{self.platform} rate limited, "
                                  f"retrying in {wait:.1f}s (attempt {attempt + 1})")]),
                    )
                    await asyncio.sleep(wait)
                    continue
                return payload
        return {"error": {"error_code": "INTERNAL_ERROR", "message": "unreachable"}}


def create_platform_agent_app(platform: Platform) -> FastAPI:
    """构建平台 Agent 的 FastAPI 应用（Agent Card 路由 + JSON-RPC 路由）。"""
    settings = get_settings()
    base_url = settings.a2a_base_url(platform)
    card = build_agent_card(platform, base_url)
    handler = DefaultRequestHandler(
        PlatformAgentExecutor(platform), InMemoryTaskStore(), card
    )
    app = FastAPI(title=f"{platform} A2A Agent")
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/a2a"),
    )

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "platform": platform, "agent": card.name}

    return app
