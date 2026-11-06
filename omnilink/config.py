"""全局配置：从 .env 读取，本地开发零依赖可跑（fakeredis + SQLite）。"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ---- LLM（云端 OpenAI 兼容 API）----
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_api_key: str = ""
    llm_model: str = "qwen-plus"
    llm_temperature: float = 0.1
    llm_fallback_to_rules: bool = True

    # ---- 基础设施 ----
    redis_url: str = "fakeredis://local/0"
    database_url: str = "sqlite+aiosqlite:///./omnilink.db"

    # ---- 端口 ----
    mock_taobao_port: int = 9001
    mock_jd_port: int = 9002
    mock_amazon_port: int = 9003
    mock_shopify_port: int = 9004
    mcp_taobao_port: int = 9101
    mcp_jd_port: int = 9102
    mcp_amazon_port: int = 9103
    mcp_shopify_port: int = 9104
    a2a_taobao_port: int = 9201
    a2a_jd_port: int = 9202
    a2a_amazon_port: int = 9203
    a2a_shopify_port: int = 9204
    api_port: int = 8000

    # ---- 故障注入（演示容错） ----
    mock_amazon_rate_limit_prob: float = 0.0
    mock_latency_ms: int = 200

    # ---- 缓存与幂等策略 ----
    price_cache_ttl_seconds: int = 60
    inventory_cache_ttl_seconds: int = 5
    product_cache_ttl_seconds: int = 86400
    idempotency_window_seconds: int = 300

    @property
    def llm_available(self) -> bool:
        """配置了有效 API Key 才走云端 LLM，否则降级规则引擎。"""
        return bool(self.llm_api_key) and "your-api-key" not in self.llm_api_key

    def mock_base_url(self, platform: str) -> str:
        return f"http://127.0.0.1:{getattr(self, f'mock_{platform}_port')}"

    def mcp_url(self, platform: str) -> str:
        return f"http://127.0.0.1:{getattr(self, f'mcp_{platform}_port')}/mcp"

    def a2a_base_url(self, platform: str) -> str:
        return f"http://127.0.0.1:{getattr(self, f'a2a_{platform}_port')}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
