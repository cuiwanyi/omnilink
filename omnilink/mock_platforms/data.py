"""Mock 平台种子数据：同一批商品在四个平台的不同"写法"。

刻意还原真实差异：
- 同一商品四个平台四个标题（SKU 对齐的演示素材）
- 淘宝价格单位是分、京东是元、Amazon/Shopify 是美元
- 类目体系、库存字段名、错误格式全部不同
- 蓝牙耳机：淘宝 800 件 / Shopify 37 件（对应库存同步演示场景）
"""
from copy import deepcopy
from decimal import Decimal
from typing import Any

# ================= 主数据（内部视角，Mock 服务据此生成平台原生数据） =================
CANONICAL_PRODUCTS: list[dict[str, Any]] = [
    {
        "unified_sku": "USKU-EARPHONE-BT5",
        "brand": "SoundCore",
        "category_path": "数码/耳机/蓝牙耳机",
        "attributes": {"蓝牙版本": "5.3", "降噪": "ANC主动降噪", "续航": "30小时"},
        "monthly_sales": 15000,
        "platforms": {
            "taobao": {
                "sku": "TB1001",
                "title": "SoundCore蓝牙耳机5.3 ANC降噪 长续航30小时",
                "price_cny": "89.00",
                "stock": 800,
            },
            "jd": {
                "sku": "JD2001",
                "title": "声阔（SoundCore）蓝牙耳机 5.3降噪版 30小时续航",
                "price_cny": "92.00",
                "stock": 650,
            },
            "amazon": {
                "sku": "B0C000001",
                "title": "SoundCore Bluetooth Earbuds 5.3, ANC, 30H Playtime",
                "price_usd": "12.99",
                "stock": 500,
            },
            "shopify": {
                "sku": "SHOP-EAR-001",
                "title": "SoundCore Wireless Earbuds - ANC - 30H Battery",
                "price_usd": "13.29",
                "stock": 37,
            },
        },
    },
    {
        "unified_sku": "USKU-PHONECASE-17CLEAR",
        "brand": "CASEKo",
        "category_path": "数码/手机配件/手机壳",
        "attributes": {"机型": "iPhone 17", "材质": "TPU透明", "特性": "MagSafe磁吸防摔"},
        "monthly_sales": 42000,
        "platforms": {
            "taobao": {
                "sku": "TB1002",
                "title": "iPhone17透明壳防摔 MagSafe compatible 全包",
                "price_cny": "19.90",
                "stock": 5000,
            },
            "jd": {
                "sku": "JD2002",
                "title": "苹果17透明手机壳 磁吸防摔全包边",
                "price_cny": "22.90",
                "stock": 3200,
            },
            "amazon": {
                "sku": "B0C000002",
                "title": "Clear Case for iPhone 17 with MagSafe, Anti-Scratch, Full Body Protection",
                "price_usd": "4.99",
                "stock": 8000,
            },
            "shopify": {
                "sku": "SHOP-CASE-001",
                "title": "iPhone 17 Clear MagSafe Case - Anti-Scratch",
                "price_usd": "5.49",
                "stock": 2100,
            },
        },
    },
    {
        "unified_sku": "USKU-POWERBANK-20K",
        "brand": "Anker",
        "category_path": "数码/充电设备/移动电源",
        "attributes": {"容量": "20000mAh", "快充": "22.5W", "接口": "USB-C双向"},
        "monthly_sales": 8000,
        "platforms": {
            "taobao": {
                "sku": "TB1003",
                "title": "Anker充电宝20000毫安 22.5W快充",
                "price_cny": "129.00",
                "stock": 1200,
            },
            "jd": {
                "sku": "JD2003",
                "title": "Anker安克移动电源20000mAh双向快充",
                "price_cny": "135.00",
                "stock": 900,
            },
            "amazon": {
                "sku": "B0C000003",
                "title": "Anker Power Bank 20000mAh 22.5W Fast Charging USB-C",
                "price_usd": "24.99",
                "stock": 1500,
            },
            "shopify": {
                "sku": "SHOP-PWR-001",
                "title": "Anker 20000mAh Power Bank - Fast Charge",
                "price_usd": "25.99",
                "stock": 600,
            },
        },
    },
    {
        # 该商品京东未在售：演示"部分平台无货/未上架"的降级标注场景
        "unified_sku": "USKU-EARPHONE-PRO9",
        "brand": "SoundCore",
        "category_path": "数码/耳机/蓝牙耳机",
        "attributes": {"蓝牙版本": "5.4", "降噪": "自适应ANC", "续航": "40小时"},
        "monthly_sales": 6000,
        "platforms": {
            "taobao": {
                "sku": "TB1004",
                "title": "SoundCore旗舰蓝牙耳机5.4 自适应降噪",
                "price_cny": "299.00",
                "stock": 300,
            },
            "amazon": {
                "sku": "B0C000004",
                "title": "SoundCore Pro 9 Earbuds, Adaptive ANC, 40H",
                "price_usd": "45.99",
                "stock": 220,
            },
            "shopify": {
                "sku": "SHOP-EAR-002",
                "title": "SoundCore Pro 9 - Adaptive ANC Earbuds",
                "price_usd": "47.99",
                "stock": 180,
            },
        },
    },
]

# 各平台类目命名体系（刻意不同，演示类目映射）
PLATFORM_CATEGORY = {
    "taobao": "3C数码配件",
    "jd": "数码>电脑办公>外设产品",
    "amazon": "Cell Phones & Accessories",
    "shopify": "Electronics > Accessories",
}

# 叶子类目 ID：各平台自己的类目编码体系（适配器据此映射到内部统一类目树）
LEAF_CAT_IDS: dict[str, dict[str, str]] = {
    "USKU-EARPHONE-BT5": {"taobao": "1512", "jd": "652-829-860",
                          "amazon": "172541", "shopify": "gid://tax/electronics-headphones"},
    "USKU-PHONECASE-17CLEAR": {"taobao": "1513", "jd": "652-829-861",
                               "amazon": "2407761011", "shopify": "gid://tax/electronics-cases"},
    "USKU-POWERBANK-20K": {"taobao": "1514", "jd": "652-829-862",
                           "amazon": "7073966011", "shopify": "gid://tax/electronics-chargers"},
    "USKU-EARPHONE-PRO9": {"taobao": "1512", "jd": "652-829-860",
                           "amazon": "172541", "shopify": "gid://tax/electronics-headphones"},
}

# ================= 平台原生序列化器（Mock 返回给调用方的数据结构） =================

def _specs_native(platform: str, attributes: dict) -> Any:
    """规格属性的平台原生形态：淘宝是分号字符串，京东是JSON对象，Amazon/Shopify是数组。"""
    if platform == "taobao":
        return ";".join(f"{k}:{v}" for k, v in attributes.items())
    if platform == "jd":
        return dict(attributes)
    return [{"name": k, "value": v} for k, v in attributes.items()]


def to_taobao_item(p: dict, unified: dict) -> dict:
    return {
        "num_iid": p["sku"],
        "title": p["title"],
        "price_cents": int(Decimal(p["price_cny"]) * 100),  # 淘宝单位：分
        "sellable_quantity": p["stock"],
        "cat_name": PLATFORM_CATEGORY["taobao"],
        "cid": LEAF_CAT_IDS[unified["unified_sku"]]["taobao"],
        "brand": unified["brand"],
        "monthly_sales": unified["monthly_sales"],
        "props": _specs_native("taobao", unified["attributes"]),
        "pict_url": f"https://img.taobao.com/{p['sku']}.jpg",
    }


def to_jd_ware(p: dict, unified: dict) -> dict:
    return {
        "sku_id": p["sku"],
        "ware_title": p["title"],
        "price": p["price_cny"],  # 京东单位：元（字符串）
        "stock_num": p["stock"],
        "category": PLATFORM_CATEGORY["jd"],
        "cat_id": LEAF_CAT_IDS[unified["unified_sku"]]["jd"],
        "brand_name": unified["brand"],
        "comment_count": unified["monthly_sales"] // 10,
        "param": _specs_native("jd", unified["attributes"]),
        "image_url": f"https://img.jd.com/{p['sku']}.jpg",
    }


def to_amazon_item(p: dict, unified: dict) -> dict:
    return {
        "asin": p["sku"],
        "item_name": p["title"],
        "price": {"amount": float(p["price_usd"]), "currency": "USD"},
        "fulfillable_quantity": p["stock"],
        "category": PLATFORM_CATEGORY["amazon"],
        "browse_node": LEAF_CAT_IDS[unified["unified_sku"]]["amazon"],
        "brand": unified["brand"],
        "sales_rank": max(1, 100000 - unified["monthly_sales"]),
        "attributes": _specs_native("amazon", unified["attributes"]),
        "image_url": f"https://m.media-amazon.com/{p['sku']}.jpg",
    }


def to_shopify_product(p: dict, unified: dict) -> dict:
    return {
        "variant_id": p["sku"],
        "product_title": p["title"],
        "price": p["price_usd"],  # Shopify：字符串 USD
        "currency": "USD",
        "inventory_quantity": p["stock"],
        "product_type": PLATFORM_CATEGORY["shopify"],
        "taxonomy_id": LEAF_CAT_IDS[unified["unified_sku"]]["shopify"],
        "vendor": unified["brand"],
        "options": _specs_native("shopify", unified["attributes"]),
        "image": {"src": f"https://cdn.shopify.com/{p['sku']}.jpg"},
    }


PLATFORM_SERIALIZER = {
    "taobao": to_taobao_item,
    "jd": to_jd_ware,
    "amazon": to_amazon_item,
    "shopify": to_shopify_product,
}

# ================= 订单种子数据（商家 M001） =================
SEED_ORDERS: dict[str, list[dict]] = {
    "taobao": [
        {"tid": "TBORD90001", "num_iid": "TB1001", "num": 2, "payment_cents": 17800,
         "status": "WAIT_SELLER_SEND_GOODS", "buyer": "buyer_a", "created": "2026-11-05 10:23:11"},
        {"tid": "TBORD90002", "num_iid": "TB1002", "num": 1, "payment_cents": 1990,
         "status": "WAIT_BUYER_CONFIRM_GOODS", "buyer": "buyer_b", "created": "2026-11-04 16:02:40"},
    ],
    "jd": [
        {"order_id": "JDORD80001", "sku_id": "JD2001", "quantity": 1, "payment": "92.00",
         "order_state": "WAIT_SELLER_DELIVERY", "customer": "user_c", "created": "2026-11-05 09:11:05"},
    ],
    "amazon": [
        {"amazon_order_id": "AMZ-111-0000001", "asin": "B0C000002", "quantity": 3,
         "amount": 14.97, "currency": "USD", "order_status": "Unshipped",
         "buyer_email": "d@example.com", "created": "2026-11-05T08:30:00Z"},
    ],
    "shopify": [
        {"id": 70001, "variant_id": "SHOP-EAR-001", "quantity": 1, "total_price": "13.29",
         "currency": "USD", "financial_status": "paid", "fulfillment_status": None,
         "email": "e@example.com", "created_at": "2026-11-05T07:45:12Z"},
    ],
}

# 物流种子数据：platform_order_id -> 物流信息
SEED_LOGISTICS: dict[str, dict] = {
    "TBORD90002": {"carrier": "SF", "tracking_no": "SF1234567890", "status": "运输中",
                   "traces": [{"time": "2026-11-04 18:00", "context": "顺丰已揽收"},
                               {"time": "2026-11-05 06:30", "context": "到达杭州转运中心"}]},
    "AMZ-111-0000001": {"carrier": "UPS", "tracking_no": "1Z999AA10123456784", "status": "In Transit",
                        "traces": [{"time": "2026-11-05T09:00:00Z", "context": "Departed facility"}]},
}


def build_platform_db(platform: str) -> dict:
    """构建某平台的内存数据库（商品/库存/订单/物流/已消费幂等键）。"""
    products: dict[str, dict] = {}
    for unified in CANONICAL_PRODUCTS:
        p = unified["platforms"].get(platform)
        if not p:
            continue
        native = PLATFORM_SERIALIZER[platform](deepcopy(p), unified)
        native["__unified_sku"] = unified["unified_sku"]
        native["__attributes"] = unified["attributes"]
        native["__category_path"] = unified["category_path"]
        products[p["sku"]] = native
    return {
        "products": products,
        "orders": deepcopy(SEED_ORDERS.get(platform, [])),
        "logistics": deepcopy(SEED_LOGISTICS),
        "consumed_idempotency_keys": set(),
        "order_seq": 100000,
    }
