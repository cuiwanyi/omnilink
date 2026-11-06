"""平台 API 限流自适应：令牌桶（Agent 侧主动限速）+ 熔断器（平台故障时快速失败）。

- 令牌桶：按平台 API 配额自适应放行，超配额的调用在 Agent 内排队而非直接失败
- 熔断器：连续失败达到阈值后断开，冷却期内的调用直接走缓存降级
"""
import asyncio
import time
from enum import Enum


class TokenBucket:
    """异步令牌桶限流器。"""

    def __init__(self, rate_per_sec: float, burst: int | None = None) -> None:
        self.rate = rate_per_sec
        self.capacity = burst if burst is not None else max(1, int(rate_per_sec * 2))
        self._tokens = float(self.capacity)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> float:
        """获取一个令牌，返回等待秒数。"""
        async with self._lock:
            now = time.monotonic()
            self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
            self._updated = now
            if self._tokens >= 1:
                self._tokens -= 1
                return 0.0
            wait = (1 - self._tokens) / self.rate
            self._tokens = 0
        await asyncio.sleep(wait)  # 排队等待而非失败
        return wait


class CircuitState(str, Enum):
    CLOSED = "closed"        # 正常
    OPEN = "open"            # 熔断中，快速失败
    HALF_OPEN = "half_open"  # 冷却结束，试探恢复


class CircuitBreaker:
    """熔断器：fail_threshold 次连续失败后断开 cool_down 秒。"""

    def __init__(self, fail_threshold: int = 3, cool_down: float = 30.0) -> None:
        self.fail_threshold = fail_threshold
        self.cool_down = cool_down
        self.state = CircuitState.CLOSED
        self.fail_count = 0
        self.opened_at = 0.0

    def allow(self) -> bool:
        if self.state == CircuitState.OPEN:
            if time.monotonic() - self.opened_at >= self.cool_down:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        return True

    def on_success(self) -> None:
        self.fail_count = 0
        self.state = CircuitState.CLOSED

    def on_failure(self) -> None:
        self.fail_count += 1
        if self.fail_count >= self.fail_threshold or self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            self.opened_at = time.monotonic()


class PlatformThrottle:
    """单个平台的 限流器 + 熔断器 组合。"""

    def __init__(self, rate_per_sec: float = 10.0) -> None:
        self.bucket = TokenBucket(rate_per_sec)
        self.breaker = CircuitBreaker()


_THROTTLES: dict[str, PlatformThrottle] = {}


def get_throttle(platform: str) -> PlatformThrottle:
    if platform not in _THROTTLES:
        _THROTTLES[platform] = PlatformThrottle()
    return _THROTTLES[platform]
