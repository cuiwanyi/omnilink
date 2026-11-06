# OmniLink 商链智联 — 跨平台电商 Agent 互操作平台

> 基于 **A2A 协议 + MCP 协议 + LangGraph** 的多智能体协作系统：商家说一句自然语言
> （"帮我看下蓝牙耳机哪个平台卖得最便宜，Shopify 那边卖断货了，把淘宝的库存同步过去"），
> 系统自动完成任务分解、跨平台 Agent 互发现与委派、统一数据模型换算、防超卖库存同步，
> 并通过 SSE 实时播报每个 Agent 的执行状态。

## 为什么做这个项目

电商商家同时经营淘宝、京东、Amazon、Shopify 四个平台时，面临三个结构性难题：

1. **数据孤岛**——同一商品在四个平台有四个 SKU、四种标题、两种币种，无法直接比价/调拨；
2. **API 异构**——各平台接口格式、错误码、限流策略完全不同，直连代码不可维护；
3. **并发超卖**——多平台共享一盘货，并发写入极易超卖。

本项目用"**协议分层 + 防腐层 + 防超卖三板斧**"解决：MCP 统一 Agent↔工具接口，
A2A 统一 Agent↔Agent 协作，LangGraph 统一任务编排，Redis 分布式锁 + 配额分配 + 幂等键
保证库存写入安全。

## 系统架构

```
商家自然语言指令
      │
      ▼
┌─────────────────────────────────────────────────────────┐
│  Orchestrator（LangGraph StateGraph）        :8000      │
│  LLM 任务分解 → Send 扇出并行 → 结果归约 → SSE 播报      │
│  └─ 业务 Agent：比价 / 库存同步 / 上架 / 订单聚合         │
└─────────────────────────────────────────────────────────┘
      │  A2A 协议（Agent Card 互发现 / Task 委派 / 流式状态）
      ▼
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│淘宝 Agent│ │京东 Agent│ │Amazon Agt│ │Shopify Agt│ :9201-9204
└──────────┘ └──────────┘ └──────────┘ └──────────┘
      │  MCP 协议（7 个标准化工具/平台，统一 schema）
      ▼
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│淘宝 MCP  │ │京东 MCP  │ │Amazon MCP│ │Shopify MCP│ :9101-9104
└──────────┘ └──────────┘ └──────────┘ └──────────┘
      │  防腐层适配器（统一数据模型 / 币种换算 / 错误码映射）
      ▼
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│淘宝 Mock │ │京东 Mock │ │Amazon Mck│ │Shopify Mck│ :9001-9004
│原生 API  │ │原生 API  │ │原生 API  │ │原生 API  │
└──────────┘ └──────────┘ └──────────┘ └──────────┘

横切支撑：Redis 缓存/分布式锁 │ SQLite 映射表/幂等记录/审计日志 │ 令牌桶限流+熔断
```

### 四层职责

| 层 | 协议/技术 | 职责 |
|---|---|---|
| Mock 平台层 | FastAPI | 模拟四平台**原生形态** API：不同字段结构、币种、错误格式、限流、延迟、故障注入 |
| MCP 工具层 | FastMCP | 每平台暴露完全一致的 7 个工具（商品查询/库存读写/订单创建与查询/物流跟踪/上架），Agent 用一套 schema 调所有平台 |
| A2A 平台 Agent 层 | a2a-sdk | 发布 Agent Card 供互发现；接收 Task 委派；限流时自治指数退避重试并实时播报 |
| 编排层 | LangGraph | LLM 分解商家指令 → `Send` 扇出并行子任务 → 归约汇总；SSE 实时推送 |

## 核心设计亮点

### 1. 防腐层：统一数据模型

- **币种归一**：`Money` 模型持有原始币种金额 + 人民币金额 + 汇率快照，USD→CNY 在适配器边界完成，比价直接可比；
- **库存三态**：`available / locked / in_transit`，跨平台统一；
- **类目映射表**：各平台类目编码（淘宝 `1512`、京东 `652-829-860`、Amazon `172541`…）↔ 内部统一类目树双向映射；
- **统一错误码**：平台原生错误（淘宝 `isp.service-unavailable`、Amazon `RequestThrottled`…）双映射到统一 `ErrorCode`，上层只处理一套错误语义。

### 2. 防超卖三板斧

| 机制 | 实现 | 防什么 |
|---|---|---|
| 分布式锁 | Redis `SET NX PX` + 唯一值校验释放（Lua） | 同一 SKU 的同步操作并发写 |
| 配额分配 | 按平台权重分配总库存（0.35/0.25/0.25/0.15），余数归最高权重 | 四平台库存加总超实际库存 |
| 幂等键 | `sha256(merchant:sku:op:时间窗)`，客户端生成 + 平台侧去重 + 系统级防重表 | 网络重试/用户重复提交导致重复扣减 |

### 3. SKU 对齐三级漏斗

同一商品四平台四个标题，跨平台联动前必须实体对齐：

```
映射表直查（90% 命中，零 LLM 开销）
   ↓ 未命中
向量召回（本地 rapidfuzz，生产 Milvus 余弦相似度）取 Top-K 候选
   ↓
LLM 精判置信度 ──> ≥0.9 自动入库（llm_auto）
                 ──> 0.7~0.9 进人工审核队列（审核前不参与联动）
                 ──> <0.7 不建映射
```

无 LLM Key 时降级为"标题相似度 + 属性匹配"规则打分，全链路仍可运行。

### 4. 容错与可观测

- **优雅降级**：Amazon 限流时其 Agent 内部指数退避重试，最终失败则降级标注，不拖垮其他三个平台的比价结果；
- **熔断器**：连续失败自动熔断，半开探测恢复；
- **审计日志**：每次 A2A 委派 / MCP 调用 / 库存写操作落 `audit_log` 表（actor/action/platform/params/result）；
- **多商家隔离**：缓存 key 带 `m{merchant_id}` 前缀，凭证走 `CredentialVault`。

## 快速开始

```bash
# 1. 环境（Python 3.10+）
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt

# 2. 配置（可选：不填 LLM Key 自动降级规则引擎，全部功能可演示）
cp .env.example .env

# 3. 一条命令启动全部 13 个服务（4 Mock + 4 MCP + 4 A2A + 1 API）
python -m omnilink.launcher all

# 4. 另开终端，跑演示（4 个场景）
python scripts/demo.py

# 5. 测试
pytest tests -q        # 17 个用例
```

也可以 `python scripts/run_all.py` 一键启动 + 演示。

## 演示场景实录

**场景 1（文档 5.1 原场景）**："帮我看下蓝牙耳机哪个平台卖得最便宜，Shopify 那边卖断货了，把淘宝的库存同步过去"

```
[分解] 共 2 个子任务：t1 跨平台比价；t2 库存同步 taobao → shopify
[委派] taobao/jd/amazon/shopify Agent 并行 → SUBMITTED → WORKING → COMPLETED

### [t1] 跨平台比价：蓝牙耳机
- 最便宜平台：taobao ¥89.0（jd ¥92 / amazon ¥92.88 / shopify ¥95.02，USD 已换算）

### [t2] 库存同步：taobao → shopify
- ✅ 已同步 120 件到 shopify（SHOP-EAR-001）
- 配额说明：总可售 800 件按权重分配，shopify 配额 120 件
- 幂等键：4bff69e346f3c6a8…
```

**场景 3（合规拦截）**："把新品 SoundCore 旅行充电器上架到所有平台"

```
- ✅ taobao：TAOBAO-NEW-36B6D490
- ✅ shopify：SHOPIFY-NEW-5BA3FCD5
- ⛔ jd 合规拦截：京东上架需品牌授权书
- ⛔ amazon 合规拦截：Amazon 上架需 UPC 码
```

**场景 4（SKU 对齐）**：Shopify 新品 `SHOP-EAR-002`（无映射）→ 召回命中
`USKU-EARPHONE-PRO9` → 置信度 0.715 → 进入人工审核队列（配 LLM Key 时 >0.9 自动入库）。

## 项目结构

```
omnilink/
├── omnilink/
│   ├── config.py            # pydantic-settings 集中配置
│   ├── common/              # 统一数据模型 / 错误码 / 幂等键
│   ├── mock_platforms/      # 四平台原生 Mock API（含限流/延迟/故障注入）
│   ├── adapters/            # 防腐层：原生格式 ↔ 统一模型（每平台一个适配器）
│   ├── services/            # 缓存/分布式锁/配额/限流熔断/审计/凭证/SKU映射
│   ├── mcp_servers/         # FastMCP 工厂：每平台 7 个标准化工具
│   ├── platform_agents/     # A2A Server：Agent Card + Task 委派 + 自治重试
│   ├── business_agents/     # 比价/库存同步/上架/订单聚合 + A2A 委派客户端
│   ├── orchestrator/        # LangGraph 编排 + LLM 任务分解 + SKU 对齐
│   ├── api/                 # 商家工作台 API（SSE 实时播报）
│   └── launcher.py          # 单进程启动全部 13 个服务
├── scripts/demo.py          # 4 场景演示脚本
├── tests/                   # 17 个测试（模型/幂等/配额/适配器集成）
└── requirements.txt
```

## 技术栈与版本

Python 3.10 · fastmcp 4.1.6 · a2a-sdk 1.3.0 · LangGraph 1.2.20 · FastAPI ·
Redis(fakeredis) · SQLite(aiosqlite) · OpenAI 兼容 LLM（可降级规则引擎）· rapidfuzz
