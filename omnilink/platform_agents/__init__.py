"""平台 Agent：每个平台一个自治 A2A Server。

- Agent Card 声明能力（6 个技能）、端点、流式支持
- 接受别的 Agent 按 A2A 协议派来的 Task
- 自治处理本平台的限流重试与异常，执行时调自己的 MCP 工具
- 通过 SSE/Task 状态把 working/completed/failed 实时播报给委派方
"""
from omnilink.platform_agents.agent import (
    PLATFORM_SKILLS,
    PlatformAgentExecutor,
    build_agent_card,
    create_platform_agent_app,
)

__all__ = [
    "PLATFORM_SKILLS",
    "PlatformAgentExecutor",
    "build_agent_card",
    "create_platform_agent_app",
]
