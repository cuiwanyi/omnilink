"""OmniLink 商链智联 · 跨平台电商 Agent 互操作平台

架构三层：
- MCP 工具层：FastMCP 将四平台开放 API 封装为标准工具（5类×4平台=20个）
- A2A 互操作层：每个平台一个自治 Agent，Agent Card 声明能力，Task 委派协作
- LangGraph 编排层：Orchestrator 理解商家意图、分解复合任务、并行调度业务 Agent
"""

__version__ = "1.0.0"
