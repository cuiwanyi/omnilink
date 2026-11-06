"""平台适配器基类（防腐层）。

职责：
1. 持有平台 API 端点与商家凭证（经 CredentialVault 动态取用）
2. 把平台原生错误结构映射为统一错误码 PlatformAPIError
3. 定义五类标准能力的抽象接口（商品查询/订单创建/物流跟踪/库存更新/商品上架）

平台差异只允许存在于适配器内部，对外只产出 common.models 统一模型。
"""
from abc import ABC, abstractmethod
from typing import Any, ClassVar

import httpx

from omnilink.common.errors import ErrorCode, PlatformAPIError
from omnilink.common.models import Inventory, Logistics, Order, Platform, Product
from omnilink.services.credentials import CredentialVault


class BasePlatformAdapter(ABC):
    platform: ClassVar[Platform]
    #: 平台叶子类目 ID → 内部统一类目树路径
    category_map: ClassVar[dict[str, str]] = {}
    #: 平台原生错误码 → 统一错误码
    error_map: ClassVar[dict[str, ErrorCode]] = {}

    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url, timeout=self._timeout
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _call(
        self,
        method: str,
        path: str,
        merchant_id: str,
        *,
        params: dict | None = None,
        body: dict | None = None,
    ) -> Any:
        """统一调用入口：注入凭证、转换网络错误、映射平台原生错误。"""
        token = CredentialVault.get(merchant_id, self.platform)
        if not token:
            raise PlatformAPIError(
                ErrorCode.PLATFORM_AUTH_EXPIRED,
                f"merchant {merchant_id} has no credential for {self.platform}",
                platform=self.platform,
            )
        client = await self._get_client()
        try:
            resp = await client.request(
                method, path, params=params, json=body,
                headers={"X-Merchant-Token": token},
            )
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            raise PlatformAPIError(
                ErrorCode.PLATFORM_UNAVAILABLE, f"network error: {exc}",
                platform=self.platform, retry_after=2.0,
            ) from exc
        payload = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            raise self._map_error(resp.status_code, payload, resp.headers.get("Retry-After"))
        return payload

    def _map_error(
        self, status: int, payload: Any, retry_after: str | None
    ) -> PlatformAPIError:
        """先按 HTTP 状态码粗映射，再用平台原生错误码精化。"""
        native_code = self._extract_native_code(payload)
        code = self.error_map.get(native_code) if native_code else None
        if code is None:
            code = {
                401: ErrorCode.PLATFORM_AUTH_EXPIRED,
                404: ErrorCode.PRODUCT_NOT_FOUND,
                409: ErrorCode.DUPLICATE_OPERATION,
                429: ErrorCode.PLATFORM_RATE_LIMITED,
            }.get(status, ErrorCode.INTERNAL_ERROR)
        return PlatformAPIError(
            code,
            f"{self.platform} api error (http={status}, native={native_code})",
            platform=self.platform,
            retry_after=float(retry_after) if retry_after else None,
        )

    @abstractmethod
    def _extract_native_code(self, payload: Any) -> str | None:
        """从平台原生错误结构中提取错误码。"""

    @abstractmethod
    async def search_products(
        self, query: str, merchant_id: str, limit: int = 10
    ) -> list[Product]:
        """商品查询（只读）。"""

    @abstractmethod
    async def get_inventory(self, sku: str, merchant_id: str) -> Inventory:
        """库存查询（只读）。"""

    @abstractmethod
    async def update_inventory(
        self, sku: str, quantity: int, merchant_id: str, idempotency_key: str
    ) -> Inventory:
        """库存更新（写操作，强制幂等键）。"""

    @abstractmethod
    async def create_order(
        self, sku: str, quantity: int, merchant_id: str, idempotency_key: str
    ) -> Order:
        """订单创建（写操作，强制幂等键）。"""

    @abstractmethod
    async def list_orders(self, merchant_id: str) -> list[Order]:
        """订单聚合（只读）。"""

    @abstractmethod
    async def track_logistics(self, platform_order_id: str, merchant_id: str) -> Logistics:
        """物流跟踪（只读）。"""

    @abstractmethod
    async def publish_product(
        self, product: dict, merchant_id: str, idempotency_key: str
    ) -> str:
        """商品上架（写操作，强制幂等键），返回平台侧商品编码。"""
