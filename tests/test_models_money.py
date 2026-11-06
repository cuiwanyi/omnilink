"""统一货币模型：跨币种换算与汇率快照。"""
from decimal import Decimal

from omnilink.common.models import Currency, Money


def test_usd_to_cny_conversion():
    m = Money.from_original("12.99", Currency.USD)
    assert m.original_currency == Currency.USD
    assert m.amount_cny == Decimal("92.88")  # 12.99 * 7.15 = 92.8785 → 92.88
    assert m.fx_rate == Decimal("7.15")


def test_cny_passthrough():
    m = Money.from_original(89.00, Currency.CNY)
    assert m.amount_cny == Decimal("89.00")
    assert m.fx_rate == Decimal("1.0")


def test_fx_rate_snapshot_preserved():
    """保留汇率快照：比价用人民币口径，追溯用原币种。"""
    m = Money.from_original("4.99", Currency.USD)
    assert m.original_amount == Decimal("4.99")
    assert m.amount_cny == Decimal("35.68")
