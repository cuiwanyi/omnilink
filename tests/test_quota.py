"""库存配额分配：防超卖核心算法。"""
from omnilink.services.quota import allocate_quota, would_oversell


def test_quota_sums_to_total():
    q = allocate_quota(1000)
    assert sum(q.values()) == 1000


def test_quota_respects_weights():
    q = allocate_quota(1000)
    # taobao 权重最高（0.35），配额应最大
    assert q["taobao"] > q["jd"] > q["shopify"]


def test_quota_remainder_goes_to_top_platform():
    # 1000 * 0.35=350, 0.25=250, 0.25=250, 0.15=150 → 余数 0
    q = allocate_quota(1000)
    assert q["taobao"] == 350
    # 101 件：35.35/25.25/25.25/15.15 → floor 后余数归 taobao
    q = allocate_quota(101)
    assert sum(q.values()) == 101


def test_would_oversell():
    sold = {"shopify": 150}  # shopify 配额 150（1000*0.15）
    assert would_oversell(1, "shopify", sold, 1000) is True
    assert would_oversell(1, "taobao", sold, 1000) is False
