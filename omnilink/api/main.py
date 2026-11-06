"""商家工作台 API：商家用自然语言指挥，SSE 实时推送各 Agent 执行状态。

POST /api/v1/agent/run      接收商家指令，SSE 流式返回任务分解/委派/状态/结果
GET  /api/v1/audit/logs     审计日志（安全合规回溯）
GET  /api/v1/sku/review-queue   SKU 对齐人工审核队列
POST /api/v1/sku/review     审核确认/拒绝
GET  /api/v1/agents/cards   查看四个平台 Agent 的 Agent Card（互发现验证）
"""
import asyncio
import json

import httpx
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from omnilink.common.models import PLATFORMS
from omnilink.config import get_settings
from omnilink.orchestrator import run_orchestration
from omnilink.orchestrator.llm import llm_mode
from omnilink.services.audit import recent_logs
from omnilink.services.sku_mapping import SkuMappingStore

app = FastAPI(title="OmniLink 商家工作台 API", version="1.0.0")


class RunRequest(BaseModel):
    instruction: str
    merchant_id: str = "M001"


@app.post("/api/v1/agent/run")
async def run_agent(req: RunRequest) -> StreamingResponse:
    """商家指令入口：SSE 流式播报 LangGraph 编排全过程。"""
    bus: asyncio.Queue = asyncio.Queue()

    async def event_stream():
        task = asyncio.create_task(
            run_orchestration(req.instruction, req.merchant_id, bus)
        )
        yield _sse({"type": "accepted", "instruction": req.instruction,
                    "merchant_id": req.merchant_id, "llm_mode": llm_mode()})
        while True:
            if task.done() and bus.empty():
                break
            try:
                event = await asyncio.wait_for(bus.get(), timeout=0.3)
                yield _sse(event)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
        if task.exception():
            yield _sse({"type": "error", "text": str(task.exception())})
            return
        final = task.result()
        yield _sse({"type": "done", "final_answer": final["final_answer"],
                    "results": _jsonable(final["results"])})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/v1/audit/logs")
async def audit_logs(limit: int = 50) -> dict:
    return {"logs": await recent_logs(limit)}


@app.get("/api/v1/sku/review-queue")
async def review_queue() -> dict:
    return {"queue": await SkuMappingStore.review_queue()}


class ReviewRequest(BaseModel):
    platform: str
    platform_sku: str
    approve: bool


@app.post("/api/v1/sku/review")
async def review(req: ReviewRequest) -> dict:
    ok = await SkuMappingStore.review_confirm(req.platform, req.platform_sku, req.approve)
    return {"handled": ok}


class AlignRequest(BaseModel):
    platform: str
    platform_sku: str
    title: str
    attributes: dict = {}


@app.post("/api/v1/sku/align")
async def align_sku(req: AlignRequest) -> dict:
    """SKU 对齐演示端点：映射表未命中的新 SKU 走 召回→LLM 精判→置信度分级。"""
    from omnilink.orchestrator import align_new_sku

    existing = await SkuMappingStore.to_unified(req.platform, req.platform_sku)
    if existing:
        return {"status": "already_mapped", "unified_sku": existing}
    return await align_new_sku(req.platform, req.platform_sku, req.title, req.attributes)


@app.get("/api/v1/agents/cards")
async def agent_cards() -> dict:
    """拉取四个平台 Agent 的 Agent Card（验证互发现）。"""
    settings = get_settings()
    cards = {}
    async with httpx.AsyncClient(timeout=5.0) as client:
        for p in PLATFORMS:
            try:
                resp = await client.get(
                    f"{settings.a2a_base_url(p)}/.well-known/agent-card.json"
                )
                data = resp.json()
                cards[p] = {"name": data.get("name"),
                            "skills": [s.get("id") for s in data.get("skills", [])],
                            "streaming": data.get("capabilities", {}).get("streaming")}
            except Exception as exc:  # noqa: BLE001
                cards[p] = {"error": str(exc)}
    return {"cards": cards}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "omnilink-api"}


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _jsonable(obj):
    return json.loads(json.dumps(obj, ensure_ascii=False, default=str))
