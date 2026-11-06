"""演示脚本：复现文档 5.1 全链路场景 + 订单聚合 + 一键上架 + SKU 对齐。

前置：服务已启动（python -m omnilink.launcher all 或 scripts/run_all.py）。
运行：python scripts/demo.py
"""
import asyncio
import json

import httpx

API = "http://127.0.0.1:8000"

SCENARIOS = [
    ("场景1：比价 + 库存同步（文档5.1场景）",
     "帮我看下蓝牙耳机哪个平台卖得最便宜，Shopify那边卖断货了，把淘宝的库存同步过去"),
    ("场景2：跨平台订单聚合 + 物流跟踪",
     "帮我汇总所有平台的订单，已发货的顺便看下物流到哪了"),
    ("场景3：新品一键上架（演示合规拦截）",
     "把新品 SoundCore 旅行充电器上架到所有平台"),
]

PORT_MAP = {
    "Mock API": [9001, 9002, 9003, 9004],
    "MCP Server": [9101, 9102, 9103, 9104],
    "A2A Agent": [9201, 9202, 9203, 9204],
    "工作台API": [8000],
}


async def wait_all_healthy(timeout: float = 60.0) -> None:
    ports = [p for group in PORT_MAP.values() for p in group]
    deadline = asyncio.get_running_loop().time() + timeout
    async with httpx.AsyncClient(timeout=2.0) as client:
        while True:
            ok = 0
            for port in ports:
                try:
                    r = await client.get(f"http://127.0.0.1:{port}/health")
                    # MCP Server 由 FastMCP http_app 托管，无 /health 路由，
                    # 404 也代表服务进程已就绪（能正常响应 HTTP）
                    ok += 1 if r.status_code in (200, 404) else 0
                except Exception:  # noqa: BLE001
                    pass
            if ok == len(ports):
                print(f"全部 {len(ports)} 个服务健康检查通过\n")
                return
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError(f"健康检查超时：{ok}/{len(ports)} 就绪")
            await asyncio.sleep(1.0)


async def run_instruction(title: str, instruction: str) -> None:
    print("=" * 72)
    print(title)
    print(f"商家指令：{instruction}")
    print("-" * 72)
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream(
            "POST", f"{API}/api/v1/agent/run",
            json={"instruction": instruction, "merchant_id": "M001"},
        ) as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                etype = event.get("type")
                if etype == "accepted":
                    print(f"[接入] LLM 模式：{event.get('llm_mode')}")
                elif etype == "subtasks":
                    print(f"[分解] {event.get('text')}")
                    for st in event.get("subtasks", []):
                        print(f"        - {st['id']} [{st['agent']}] {st['description']}")
                elif etype == "task_status":
                    note = f" | {event['text']}" if event.get("text") else ""
                    print(f"[委派] {event.get('platform')} Agent → {event.get('state')}{note}")
                elif etype == "status":
                    print(f"[状态] {event.get('text')}")
                elif etype == "done":
                    print("\n" + event.get("final_answer", ""))
                elif etype == "error":
                    print(f"[错误] {event.get('text')}")
    print()


async def demo_sku_alignment() -> None:
    print("=" * 72)
    print("场景4：SKU 对齐（PRO9 在 Shopify 无映射 → 召回 → LLM 精判 → 分级）")
    print("-" * 72)
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(f"{API}/api/v1/sku/align", json={
            "platform": "shopify",
            "platform_sku": "SHOP-EAR-002",
            "title": "SoundCore Pro 9 - Adaptive ANC Earbuds",
            "attributes": {"蓝牙版本": "5.4", "降噪": "自适应ANC", "续航": "40小时"},
        })
        print(json.dumps(r.json(), ensure_ascii=False, indent=2))
        if r.json().get("status") == "review_pending":
            q = await client.get(f"{API}/api/v1/sku/review-queue")
            print("\n人工审核队列：")
            print(json.dumps(q.json(), ensure_ascii=False, indent=2))
    print()


async def main() -> None:
    await wait_all_healthy()
    for title, instruction in SCENARIOS:
        await run_instruction(title, instruction)
    await demo_sku_alignment()


if __name__ == "__main__":
    asyncio.run(main())
