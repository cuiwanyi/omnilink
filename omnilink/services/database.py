"""存储层：SKU 主数据映射表 + 幂等记录 + 审计日志。

本地开发用 SQLite（aiosqlite），生产切 MySQL 只需改 DATABASE_URL。
"""
import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS unified_products (
  unified_sku TEXT PRIMARY KEY,
  canonical_title TEXT NOT NULL,
  attributes TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS sku_mappings (
  unified_sku TEXT NOT NULL,
  platform TEXT NOT NULL,
  platform_sku TEXT NOT NULL,
  confidence REAL NOT NULL,
  source TEXT NOT NULL,              -- manual / llm_auto / review_confirmed / review_pending
  created_at TEXT NOT NULL,
  PRIMARY KEY (platform, platform_sku)
);
CREATE TABLE IF NOT EXISTS idempotency_records (
  key TEXT PRIMARY KEY,
  merchant_id TEXT NOT NULL,
  operation TEXT NOT NULL,
  result TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  actor TEXT NOT NULL,               -- 发起方（orchestrator/某平台Agent/某业务Agent）
  action TEXT NOT NULL,              -- a2a_delegate / mcp_call / human_confirm ...
  platform TEXT,
  merchant_id TEXT,
  params TEXT,
  result TEXT
);
"""

_db_path = "omnilink.db"
_initialized = False


def configure(db_path: str) -> None:
    global _db_path
    _db_path = db_path


async def init_db() -> None:
    global _initialized
    if _initialized:
        return
    async with aiosqlite.connect(_db_path) as db:
        await db.executescript(_SCHEMA)
        await db.commit()
    _initialized = True


async def execute(sql: str, params: tuple = ()) -> None:
    await init_db()
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(sql, params)
        await db.commit()


async def fetchone(sql: str, params: tuple = ()) -> tuple | None:
    await init_db()
    async with aiosqlite.connect(_db_path) as db:
        async with db.execute(sql, params) as cur:
            return await cur.fetchone()


async def fetchall(sql: str, params: tuple = ()) -> list[tuple]:
    await init_db()
    async with aiosqlite.connect(_db_path) as db:
        async with db.execute(sql, params) as cur:
            return await cur.fetchall()
