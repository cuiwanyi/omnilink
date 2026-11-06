"""Mock 平台开放 API：本地模拟淘宝/京东/Amazon/Shopify 开放平台。

每个平台的响应结构、字段命名、币种、错误格式都不同（刻意还原真实差异），
以此证明 adapters 防腐层与统一数据模型的价值。
支持：模拟延迟、限流（429）、错误注入、幂等键去重、凭证校验。
"""
from omnilink.mock_platforms.server import create_mock_app

__all__ = ["create_mock_app"]
