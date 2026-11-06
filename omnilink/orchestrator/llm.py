"""LLM 客户端：云端 OpenAI 兼容 API（通义千问/DeepSeek 等）。

- 配置了 LLM_API_KEY 走云端大模型（任务分解 / SKU 对齐判断）
- 未配置或调用失败自动降级为规则引擎，保证离线可演示
"""
import json
from typing import Any

from openai import AsyncOpenAI

from omnilink.config import get_settings

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI | None:
    global _client
    settings = get_settings()
    if not settings.llm_available:
        return None
    if _client is None:
        _client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            timeout=30.0,
        )
    return _client


async def chat_json(system: str, user: str) -> dict[str, Any] | None:
    """请求 LLM 输出结构化 JSON；失败/未配置返回 None（调用方走降级路径）。"""
    settings = get_settings()
    client = _get_client()
    if client is None:
        return None
    try:
        resp = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=settings.llm_temperature,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content or ""
        return json.loads(content)
    except Exception:  # noqa: BLE001 - LLM 不可用不阻断主流程
        return None


def llm_mode() -> str:
    """当前运行模式：llm（云端大模型）/ rules（规则引擎降级）。"""
    return "llm" if get_settings().llm_available else "rules"
