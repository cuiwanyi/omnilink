"""适配器 × Mock 平台集成测试（httpx ASGI 直测，不起真实服务）。

覆盖防腐层关键价值：
- 平台原生响应 → 统一数据模型（分→元、USD→CNY、类目映射、规格解析）
- 平台原生错误 → 统一错误码（限流/凭证失效/幂等重复/商品不存在）
- 写操作幂等键防重
"""
import httpx
import pytest

from omnilink.adapters import get_adapter
from omnilink.common.errors import ErrorCode, PlatformAPIError
from omnilink.mock_platforms import create_mock_app
from omnilink.services.credentials import CredentialVault, seed_demo_credentials


def _wired_adapter(platform: str):
    """把适配器的 HTTP 层接到 Mock App 的 ASGI 传输上。"""
    seed_demo_credentials()
    adapter = get_adapter(platform, "http://mock")
    app = create_mock_app(platform)
    adapter._client = httpx.AsyncClient(  # noqa: SLF001 - 测试注入
        transport=httpx.ASGITransport(app=app), base_url="http://mock"
    )
    return adapter


@pytest.mark.asyncio
async def test_taobao_search_unified_model():
    adapter = _wired_adapter("taobao")
    products = await adapter.search_products("蓝牙耳机", "M001")
    assert products, "应能搜到蓝牙耳机"
    p = products[0]
    assert p.platform == "taobao"
    assert p.platform_sku == "TB1001"
    assert p.price.original_currency.value == "CNY"
    assert float(p.price.amount_cny) == 89.00  # 分→元换算
    assert p.category_path == "数码/耳机/蓝牙耳机"  # cid 映射到统一类目树
    assert p.attributes.get("蓝牙版本") == "5.3"  # props 分号串解析


@pytest.mark.asyncio
async def test_amazon_search_usd_converted():
    adapter = _wired_adapter("amazon")
    products = await adapter.search_products("SoundCore", "M001")
    assert products
    p = products[0]
    assert p.price.original_currency.value == "USD"
    assert float(p.price.amount_cny) > float(p.price.original_amount)  # 已换算人民币


@pytest.mark.asyncio
async def test_auth_expired_maps_to_unified_error():
    adapter = _wired_adapter("taobao")
    CredentialVault.register("BAD", "taobao", "wrong-token")
    with pytest.raises(PlatformAPIError) as exc_info:
        await adapter.search_products("耳机", "BAD")
    assert exc_info.value.code == ErrorCode.PLATFORM_AUTH_EXPIRED


@pytest.mark.asyncio
async def test_idempotent_write_duplicate_rejected():
    adapter = _wired_adapter("shopify")
    key = "test-idem-key-001"
    inv1 = await adapter.update_inventory("SHOP-EAR-001", 100, "M001", key)
    assert inv1.available == 100
    # 同一幂等键重复提交 → 平台返回 409 → 统一错误码 DUPLICATE_OPERATION
    with pytest.raises(PlatformAPIError) as exc_info:
        await adapter.update_inventory("SHOP-EAR-001", 200, "M001", key)
    assert exc_info.value.code == ErrorCode.DUPLICATE_OPERATION
    # 库存仍是第一次写入的值
    inv = await adapter.get_inventory("SHOP-EAR-001", "M001")
    assert inv.available == 100


@pytest.mark.asyncio
async def test_not_found_maps_to_unified_error():
    adapter = _wired_adapter("jd")
    with pytest.raises(PlatformAPIError) as exc_info:
        await adapter.get_inventory("NOT-EXIST", "M001")
    assert exc_info.value.code == ErrorCode.PRODUCT_NOT_FOUND


@pytest.mark.asyncio
async def test_rate_limit_maps_to_retryable(monkeypatch):
    from omnilink.mock_platforms import server as mock_server

    monkeypatch.setattr(mock_server._TokenBucket, "allow", lambda self: False)
    adapter = _wired_adapter("taobao")
    with pytest.raises(PlatformAPIError) as exc_info:
        await adapter.search_products("耳机", "M001")
    assert exc_info.value.code == ErrorCode.PLATFORM_RATE_LIMITED
    assert exc_info.value.retryable is True
    assert exc_info.value.retry_after == 1.0
