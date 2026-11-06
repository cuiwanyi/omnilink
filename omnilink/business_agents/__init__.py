"""业务 Agent：面向业务场景的组合者，通过 A2A 委派平台 Agent 完成任务。

- 比价 Agent：并行委派四平台 product_search，汇总比价表，部分失败降级标注
- 库存 Agent：源平台查询 → SKU 对齐 → 分布式锁 → 幂等委派目标平台写入
- 上架 Agent：平台合规校验 → 类目映射 → 并行委派 product_publish
- 订单 Agent：并行委派 order_query 聚合，发货单追加 logistics_track
"""
from omnilink.business_agents.a2a_client import A2ADelegator
from omnilink.business_agents.inventory_sync import sync_inventory
from omnilink.business_agents.listing import publish_to_platforms
from omnilink.business_agents.order_aggregation import aggregate_orders
from omnilink.business_agents.price_comparison import compare_price

__all__ = [
    "A2ADelegator",
    "aggregate_orders",
    "compare_price",
    "publish_to_platforms",
    "sync_inventory",
]
