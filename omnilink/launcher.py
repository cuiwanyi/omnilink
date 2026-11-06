"""服务启动器。

用法：
    python -m omnilink.launcher all                  # 单进程起全部 13 个服务（演示推荐，fakeredis 全局共享）
    python -m omnilink.launcher platform taobao      # 单平台的 mock+MCP+A2A（拆分部署模式，需真实 Redis）
    python -m omnilink.launcher api                  # 编排层 API（拆分模式）

端口布局（.env 可改）：
    Mock API   9001-9004    MCP Server   9101-9104
    A2A Agent  9201-9204    工作台 API   8000
"""
import argparse
import asyncio
import sys

import uvicorn

from omnilink.common.models import PLATFORMS, Platform
from omnilink.config import Settings, get_settings
from omnilink.services.credentials import seed_demo_credentials
from omnilink.services.database import configure as configure_db
from omnilink.services.database import init_db
from omnilink.services.sku_mapping import seed_mappings


async def _serve(app, host: str, port: int, name: str) -> uvicorn.Server:
    config = uvicorn.Config(app, host=host, port=port, log_level="warning",
                            access_log=False)
    server = uvicorn.Server(config)
    print(f"  [UP] {name:<28} http://{host}:{port}", flush=True)
    await server.serve()
    return server


def _platform_apps(platform: Platform, settings: Settings):
    """单平台三件套：Mock 开放 API + MCP 工具服务 + A2A Agent。"""
    from omnilink.mcp_servers import create_platform_mcp
    from omnilink.mock_platforms import create_mock_app
    from omnilink.platform_agents import create_platform_agent_app

    mock_app = create_mock_app(platform)
    mcp_app = create_platform_mcp(platform).http_app(path="/mcp")
    agent_app = create_platform_agent_app(platform)
    return [
        (mock_app, getattr(settings, f"mock_{platform}_port"), f"{platform} Mock API"),
        (mcp_app, getattr(settings, f"mcp_{platform}_port"), f"{platform} MCP Server"),
        (agent_app, getattr(settings, f"a2a_{platform}_port"), f"{platform} A2A Agent"),
    ]


async def _boot_common() -> None:
    """初始化存储与演示种子数据。"""
    settings = get_settings()
    if settings.database_url.startswith("sqlite"):
        path = settings.database_url.split("///")[-1]
        configure_db(path)
    await init_db()
    seed_demo_credentials()
    await seed_mappings()


async def run_platform(platform: Platform) -> None:
    await _boot_common()
    settings = get_settings()
    apps = _platform_apps(platform, settings)
    print(f"[OmniLink] 启动 {platform} 平台服务组（Mock API + MCP + A2A）", flush=True)
    await asyncio.gather(*[_serve(app, "127.0.0.1", port, name)
                           for app, port, name in apps])


async def run_api() -> None:
    await _boot_common()
    from omnilink.api.main import app

    settings = get_settings()
    print("[OmniLink] 启动商家工作台 API", flush=True)
    await _serve(app, "127.0.0.1", settings.api_port, "Orchestrator API")


async def run_all() -> None:
    await _boot_common()
    settings = get_settings()
    servers = []
    for platform in PLATFORMS:
        servers.extend(_platform_apps(platform, settings))
    from omnilink.api.main import app as api_app

    servers.append((api_app, settings.api_port, "Orchestrator 商家工作台 API"))
    print("[OmniLink] 单进程启动全部服务：4 Mock + 4 MCP + 4 A2A + 1 API", flush=True)
    print("-" * 64, flush=True)
    await asyncio.gather(*[_serve(app, "127.0.0.1", port, name)
                           for app, port, name in servers])


def main() -> None:
    parser = argparse.ArgumentParser(description="OmniLink 服务启动器")
    parser.add_argument("target", choices=["all", "api"] + PLATFORMS)
    args = parser.parse_args()
    try:
        if args.target == "all":
            asyncio.run(run_all())
        elif args.target == "api":
            asyncio.run(run_api())
        else:
            asyncio.run(run_platform(args.target))
    except KeyboardInterrupt:
        print("\n[OmniLink] 已停止", flush=True)
        sys.exit(0)


if __name__ == "__main__":
    main()
