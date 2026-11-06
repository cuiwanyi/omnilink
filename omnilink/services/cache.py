"""缓存与分布式锁：Redis 实现，本地开发自动降级 fakeredis。

多商家隔离：缓存 key 带商家前缀 `m{merchant_id}:{platform}:{kind}:{key}`，
比价等公开数据（平台在售价格）走公共缓存池，商家私有数据走隔离缓存。
"""
import json
from contextlib import asynccontextmanager

import fakeredis.aioredis
import redis.asyncio as aioredis

from omnilink.config import get_settings

_redis = None


async def get_redis():
    """单例 Redis 客户端：REDIS_URL=fakeredis:// 时用进程内 fakeredis。"""
    global _redis
    if _redis is None:
        url = get_settings().redis_url
        if url.startswith("fakeredis://"):
            _redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        else:
            _redis = aioredis.from_url(url, decode_responses=True)
    return _redis


def cache_key(kind: str, platform: str, identifier: str, merchant_id: str | None = None) -> str:
    """商家前缀隔离：私有数据带 merchant 前缀，公开数据进公共池。"""
    prefix = f"m{merchant_id}" if merchant_id else "public"
    return f"{prefix}:{platform}:{kind}:{identifier}"


class CacheService:
    """带 TTL 的 JSON 缓存。价格 TTL=60s，库存 TTL=5s，商品静态信息 TTL=24h。"""

    def __init__(self, ttl_seconds: int) -> None:
        self.ttl = ttl_seconds

    async def get(self, key: str):
        r = await get_redis()
        raw = await r.get(key)
        return json.loads(raw) if raw is not None else None

    async def set(self, key: str, value) -> None:
        r = await get_redis()
        await r.set(key, json.dumps(value, ensure_ascii=False, default=str), ex=self.ttl)

    async def get_or_load(self, key: str, loader):
        cached = await self.get(key)
        if cached is not None:
            cached["__cache_hit"] = True
            return cached
        data = await loader()
        if data is not None:
            await self.set(key, data)
        return data


@asynccontextmanager
async def distributed_lock(name: str, timeout_ms: int = 10000):
    """Redis 分布式锁（SET NX PX + 唯一值校验释放）。

    同一 SKU 的库存同步操作串行化，防止并发写导致超卖。
    """
    import uuid

    r = await get_redis()
    token = uuid.uuid4().hex
    acquired = await r.set(f"lock:{name}", token, nx=True, px=timeout_ms)
    if not acquired:
        yield False
        return
    try:
        yield True
    finally:
        # Lua 脚本保证"是我的锁才删"的原子性；fakeredis 无 Lua 时降级为先校验再删
        try:
            await r.eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then "
                "return redis.call('del', KEYS[1]) else return 0 end",
                1, f"lock:{name}", token,
            )
        except Exception:  # noqa: BLE001 - fakeredis 不支持 eval
            if await r.get(f"lock:{name}") == token:
                await r.delete(f"lock:{name}")
