"""一键演示：启动全部服务 → 健康检查 → 跑演示场景 → 保持服务运行。

用法：
    python scripts/run_all.py            # 启动 + 演示 + 保持运行（Ctrl+C 停止）
    python scripts/run_all.py --no-demo  # 只启动服务
"""
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    server = subprocess.Popen(
        [sys.executable, "-m", "omnilink.launcher", "all"],
        cwd=PROJECT_ROOT,
    )
    try:
        # 等服务起来
        time.sleep(6)
        if "--no-demo" not in sys.argv:
            import asyncio

            sys.path.insert(0, str(PROJECT_ROOT))
            from scripts.demo import main as demo_main  # noqa

            asyncio.run(demo_main())
        print("=" * 72)
        print("服务仍在运行：")
        print("  商家工作台 API   http://127.0.0.1:8000/docs")
        print("  Agent Card 示例  http://127.0.0.1:9201/.well-known/agent-card.json")
        print("按 Ctrl+C 停止全部服务")
        server.wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.terminate()
        server.wait(timeout=10)
        print("[OmniLink] 全部服务已停止")


if __name__ == "__main__":
    main()
