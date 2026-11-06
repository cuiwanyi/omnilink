"""MCP 工具层：FastMCP 将四平台开放 API 封装为标准工具。

5 类核心工具（商品查询/订单创建/物流跟踪/库存更新/商品上架）× 4 平台 = 20 个，
另含每平台库存查询辅助工具。任何 Agent 调用同名工具传同样的参数格式，
不用关心背后连的是淘宝还是 Amazon。
"""
from omnilink.mcp_servers.platform_server import create_platform_mcp

__all__ = ["create_platform_mcp"]
