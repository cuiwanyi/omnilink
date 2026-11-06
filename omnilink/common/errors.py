"""统一错误码规范。

各平台 API 的错误形态五花八门（淘宝返回 sub_code、Amazon 返回 HTTP 状态码、
Shopify 返回 errors 数组），MCP 工具层与 A2A 委派必须把它们映射到统一错误码，
Agent 收到错误码后才能自动决策：
- 限流/不可用 → 指数退避重试
- 凭证失效 → 熔断该平台并告警商家
- 商品不存在 → 终止当前子任务并降级标注
"""
from enum import Enum


class ErrorCode(str, Enum):
    PLATFORM_RATE_LIMITED = "PLATFORM_RATE_LIMITED"  # 平台限流（可重试）
    PLATFORM_UNAVAILABLE = "PLATFORM_UNAVAILABLE"    # 平台故障/超时（可重试）
    PLATFORM_AUTH_EXPIRED = "PLATFORM_AUTH_EXPIRED"  # 凭证失效（告警商家，不可重试）
    PRODUCT_NOT_FOUND = "PRODUCT_NOT_FOUND"          # 商品不存在
    INVALID_PARAMS = "INVALID_PARAMS"                # 参数非法
    DUPLICATE_OPERATION = "DUPLICATE_OPERATION"      # 幂等键命中（重复写操作被拒绝）
    INSUFFICIENT_INVENTORY = "INSUFFICIENT_INVENTORY"  # 库存不足/超卖防护拦截
    INTERNAL_ERROR = "INTERNAL_ERROR"


# 可自动重试的错误码：Agent 收到后指数退避
RETRYABLE_CODES = {ErrorCode.PLATFORM_RATE_LIMITED, ErrorCode.PLATFORM_UNAVAILABLE}


class PlatformAPIError(Exception):
    """平台 API 统一异常：贯穿 适配器 → MCP 工具 → A2A Task 的完整链路。"""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        platform: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        self.code = code
        self.platform = platform
        self.retry_after = retry_after
        super().__init__(f"[{code.value}] {message}")

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE_CODES

    def to_dict(self) -> dict:
        """MCP 工具/A2A Task 失败时的结构化错误输出。"""
        return {
            "error_code": self.code.value,
            "message": str(self),
            "platform": self.platform,
            "retryable": self.retryable,
            "retry_after": self.retry_after,
        }
