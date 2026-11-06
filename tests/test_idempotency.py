"""幂等体系：幂等键时间窗 + run_idempotent 防重。"""
import pytest

from omnilink.common.idempotency import make_idempotency_key
from omnilink.services.idempotency import run_idempotent


def test_same_window_same_key():
    k1 = make_idempotency_key("M001", "SKU1", "create_order", window_seconds=300,
                              timestamp=1000.0)
    k2 = make_idempotency_key("M001", "SKU1", "create_order", window_seconds=300,
                              timestamp=1100.0)
    assert k1 == k2


def test_different_window_different_key():
    k1 = make_idempotency_key("M001", "SKU1", "create_order", window_seconds=300,
                              timestamp=1000.0)
    k2 = make_idempotency_key("M001", "SKU1", "create_order", window_seconds=300,
                              timestamp=1000.0 + 600)
    assert k1 != k2


def test_different_operation_different_key():
    k1 = make_idempotency_key("M001", "SKU1", "create_order", timestamp=1000.0)
    k2 = make_idempotency_key("M001", "SKU1", "update_inventory", timestamp=1000.0)
    assert k1 != k2


@pytest.mark.asyncio
async def test_run_idempotent_dedup():
    """同一幂等键只执行一次真实写操作，重复请求返回首次结果。"""
    calls = {"count": 0}

    async def fake_write():
        calls["count"] += 1
        return {"order_id": "X1"}

    key = make_idempotency_key("M001", "SKU1", "create_order")
    r1 = await run_idempotent(key, "M001", "create_order", fake_write)
    r2 = await run_idempotent(key, "M001", "create_order", fake_write)

    assert calls["count"] == 1
    assert r1["order_id"] == "X1"
    assert r2["order_id"] == "X1"
    assert r2.get("deduplicated") is True
