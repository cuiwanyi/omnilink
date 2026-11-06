"""adapters：四平台防腐层适配器，平台 API 响应 ↔ 统一数据模型。"""
from omnilink.adapters.base import BasePlatformAdapter
from omnilink.adapters.amazon import AmazonAdapter
from omnilink.adapters.jd import JdAdapter
from omnilink.adapters.shopify import ShopifyAdapter
from omnilink.adapters.taobao import TaobaoAdapter
from omnilink.common.models import Platform

ADAPTER_CLASSES: dict[str, type[BasePlatformAdapter]] = {
    "taobao": TaobaoAdapter,
    "jd": JdAdapter,
    "amazon": AmazonAdapter,
    "shopify": ShopifyAdapter,
}


def get_adapter(platform: Platform, base_url: str) -> BasePlatformAdapter:
    return ADAPTER_CLASSES[platform](base_url)


__all__ = [
    "ADAPTER_CLASSES",
    "AmazonAdapter",
    "BasePlatformAdapter",
    "JdAdapter",
    "ShopifyAdapter",
    "TaobaoAdapter",
    "get_adapter",
]
