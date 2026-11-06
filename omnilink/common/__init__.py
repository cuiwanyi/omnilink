"""common：统一数据模型 + 统一错误码 + 幂等键，是整个互操作系统的地基。

平台差异（字段名/币种/计量单位/库存口径）只允许存在于 adapters 层，
Agent 之间、Orchestrator 内部流转的永远是这里定义的统一模型。
"""
from omnilink.common.errors import ErrorCode, PlatformAPIError
from omnilink.common.idempotency import make_idempotency_key
from omnilink.common.models import (
    PLATFORMS,
    Currency,
    Inventory,
    Logistics,
    LogisticsEvent,
    Money,
    Order,
    OrderItem,
    OrderStatus,
    Platform,
    Product,
)

__all__ = [
    "Currency",
    "ErrorCode",
    "Inventory",
    "Logistics",
    "LogisticsEvent",
    "Money",
    "Order",
    "OrderItem",
    "OrderStatus",
    "PLATFORMS",
    "Platform",
    "PlatformAPIError",
    "Product",
    "make_idempotency_key",
]
