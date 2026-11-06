"""A2A 委派客户端：业务 Agent 与平台 Agent 之间的协议通道。

流程：A2ACardResolver 读 Agent Card（互发现）→ 校验技能 →
ClientFactory 建客户端 → send_message 流式收 Task 事件 → 提取 Artifact 结果。
"""
import asyncio
import json
import uuid
from typing import Any

import httpx
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import (
    AgentCard,
    Message,
    Part,
    Role,
    SendMessageRequest,
    TaskState,
)

from omnilink.config import get_settings
from omnilink.services.audit import audit

_TERMINAL_STATES = {
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_REJECTED,
}


class A2ADelegator:
    """业务 Agent 侧的 A2A 委派器（进程内共享，卡片缓存）。"""

    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None
        self._cards: dict[str, AgentCard] = {}

    async def _http_client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        return self._http

    async def close(self) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    async def discover(self, platform: str) -> AgentCard:
        """互发现：读取平台 Agent 的 Agent Card 并缓存。"""
        if platform not in self._cards:
            http = await self._http_client()
            base = get_settings().a2a_base_url(platform)
            self._cards[platform] = await A2ACardResolver(http, base).get_agent_card()
        return self._cards[platform]

    async def delegate(
        self,
        platform: str,
        skill: str,
        params: dict,
        merchant_id: str,
        timeout: float = 40.0,
        on_status=None,
    ) -> dict[str, Any]:
        """委派一个 Task 并等待终态。

        返回 {"platform", "skill", "status": completed/failed/timeout, "data": ...}
        on_status: 可选回调，接收 (state_text) 用于 SSE 播报。
        """
        try:
            card = await self.discover(platform)
        except Exception as exc:  # noqa: BLE001
            return {"platform": platform, "skill": skill, "status": "failed",
                    "error": f"agent card resolve failed: {exc}"}
        if skill not in [s.id for s in card.skills]:
            return {"platform": platform, "skill": skill, "status": "failed",
                    "error": f"skill '{skill}' not declared in agent card"}

        http = await self._http_client()
        client = ClientFactory(
            ClientConfig(httpx_client=http, streaming=True)
        ).create(card)
        command = {"skill": skill, "params": params, "merchant_id": merchant_id}
        request = SendMessageRequest(
            message=Message(
                message_id=uuid.uuid4().hex,
                role=Role.ROLE_USER,
                parts=[Part(text=json.dumps(command, ensure_ascii=False))],
            )
        )

        final_state: TaskState | None = None
        payload: Any = None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        try:
            stream = client.send_message(request)
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError()
                try:
                    # 逐事件剩余时限控制（兼容 Python 3.10，无 asyncio.timeout）
                    event = await asyncio.wait_for(stream.__anext__(), remaining)
                except StopAsyncIteration:
                    break
                kind = event.WhichOneof("payload")
                if kind == "artifact_update":
                    for part in event.artifact_update.artifact.parts:
                        if part.text:
                            payload = json.loads(part.text)
                elif kind == "status_update":
                    state = event.status_update.status.state
                    if on_status is not None:
                        msg = event.status_update.status.message
                        note = ""
                        if msg and msg.parts:
                            note = msg.parts[0].text or ""
                        on_status(platform, TaskState.Name(state), note)
                    if state in _TERMINAL_STATES:
                        final_state = state
                        break
                elif kind == "task":
                    continue  # 初始 Task 事件
        except (asyncio.TimeoutError, TimeoutError):
            await audit(f"biz", f"a2a_delegate:{skill}", platform, merchant_id,
                    params, result="timeout")
            return {"platform": platform, "skill": skill, "status": "timeout",
                    "error": f"delegation timeout after {timeout}s"}

        succeeded = final_state == TaskState.TASK_STATE_COMPLETED
        await audit("biz", f"a2a_delegate:{skill}", platform, merchant_id, params,
                    result="ok" if succeeded else "failed")
        return {
            "platform": platform,
            "skill": skill,
            "status": "completed" if succeeded else "failed",
            "data": payload,
        }
