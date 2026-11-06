"""统一数据模型（防腐层内核）。

同一商品在四个平台的类目、字段、币种、计量单位都不一样：
- 淘宝价格单位是"分"、京东是"元"、Amazon/Shopify 是美元
- 淘宝库存字段叫 sellable_quantity，Amazon 叫 fulfillable_quantity
- 类目体系完全不同

所有平台 API 响应必须先经 adapters 转换为这里的统一模型，
Agent 之间才谈得上"互相理解数据"。这是整个互操作的地基。
"""
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

Platform = Literal["taobao", "jd", "amazon", "shopify"]
PLATFORMS: list[str] = ["taobao", "jd", "amazon", "shopify"]


class Currency(str, Enum):
    CNY = "CNY"
    USD = "USD"


# 演示用固定汇率；生产环境接入实时汇率服务并保留汇率快照
FX_RATES_TO_CNY: dict[Currency, Decimal] = {
    Currency.CNY: Decimal("1.0"),
    Currency.USD: Decimal("7.15"),
}


class Money(BaseModel):
    """统一货币模型：始终以人民币计价便于比价，同时保留原币种与汇率用于追溯。"""

    amount_cny: Decimal = Field(description="人民币计价金额（统一口径）")
    original_amount: Decimal = Field(description="平台原始金额")
    original_currency: Currency = Field(description="平台原始币种")
    fx_rate: Decimal = Field(default=Decimal("1.0"), description="换算汇率快照")

    @classmethod
    def from_original(
        cls, amount: float | str | Decimal, currency: Currency
    ) -> "Money":
        original = Decimal(str(amount))
        rate = FX_RATES_TO_CNY[currency]
        return cls(
            amount_cny=(original * rate).quantize(Decimal("0.01")),
            original_amount=original,
            original_currency=currency,
            fx_rate=rate,
        )


class Product(BaseModel):
    """统一商品模型。unified_sku 是跨平台主数据键，platform_sku 是平台侧编码。"""

    unified_sku: str = Field(description="内部统一 SKU（跨平台主数据键）")
    platform: Platform
    platform_sku: str = Field(description="平台侧商品编码")
    title: str
    category_path: str = Field(description="统一类目树路径，如 数码/手机配件/手机壳")
    brand: str = ""
    attributes: dict[str, str] = Field(
        default_factory=dict, description="归一化规格属性，如 {机型: iPhone 17, 材质: TPU}"
    )
    price: Money
    monthly_sales: int = Field(default=0, description="月销（统一口径，用于爆款识别）")
    images: list[str] = Field(default_factory=list)


class Inventory(BaseModel):
    """统一库存三态模型：可售/锁定/在途，quota 是防超卖配额分配的结果。"""

    platform: Platform
    platform_sku: str
    available: int = Field(description="可售库存")
    locked: int = Field(default=0, description="锁定库存（下单未付款）")
    in_transit: int = Field(default=0, description="在途库存（已付款未发货）")
    quota: int = Field(default=0, description="本平台分配的配额（防超卖核心）")
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class OrderStatus(str, Enum):
    PENDING_PAYMENT = "pending_payment"
    PAID = "paid"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class OrderItem(BaseModel):
    platform_sku: str
    quantity: int
    unit_price: Money


class Order(BaseModel):
    """统一订单模型。order_id 为内部统一编号，platform_order_id 为平台侧单号。"""

    order_id: str
    platform: Platform
    platform_order_id: str
    merchant_id: str
    items: list[OrderItem]
    status: OrderStatus
    total: Money
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class LogisticsEvent(BaseModel):
    timestamp: datetime
    description: str
    location: str = ""


class LogisticsStatus(str, Enum):
    PENDING = "pending"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    EXCEPTION = "exception"


class Logistics(BaseModel):
    """统一物流模型：承运商编码与物流状态枚举跨平台归一。"""

    platform: Platform
    platform_order_id: str
    tracking_no: str
    carrier: str = Field(description="归一化承运商编码，如 SF/YTO/UPS/USPS")
    status: LogisticsStatus
    events: list[LogisticsEvent] = Field(default_factory=list)
