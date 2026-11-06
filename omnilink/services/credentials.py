"""商家凭证托管（KMS 加密托管的演示实现）。

生产语义：
- 商家的平台 API 凭证（AppKey/Token）加密存储于 KMS
- 平台 Agent 执行任务时按 Task 上下文里的商家身份动态解密取用
- 即用即毁：任何日志、缓存、A2A 消息里都不出现明文凭证

本演示实现：凭证以 base64"加密"存放于内存保险库，仅在使用时解码。
"""
import base64
import threading


class CredentialVault:
    _lock = threading.Lock()
    _store: dict[tuple[str, str], str] = {}

    @classmethod
    def _encrypt(cls, plaintext: str) -> str:
        return base64.b64encode(plaintext.encode()).decode()

    @classmethod
    def _decrypt(cls, ciphertext: str) -> str:
        return base64.b64decode(ciphertext.encode()).decode()

    @classmethod
    def register(cls, merchant_id: str, platform: str, token: str) -> None:
        with cls._lock:
            cls._store[(merchant_id, platform)] = cls._encrypt(token)

    @classmethod
    def get(cls, merchant_id: str, platform: str) -> str | None:
        """按商家身份动态取用凭证（解密即用）。"""
        with cls._lock:
            encrypted = cls._store.get((merchant_id, platform))
        return cls._decrypt(encrypted) if encrypted else None


def seed_demo_credentials() -> None:
    """演示商家 M001 在四个平台的授权凭证。"""
    for platform in ("taobao", "jd", "amazon", "shopify"):
        CredentialVault.register("M001", platform, f"tok-{platform}-M001")
