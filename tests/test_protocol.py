"""不需要游戏、不需要手机的协议自检。

    python tests/test_protocol.py

用一个假 App 连上本地 WS 服务端，验证：
  1) 终端二维码/绑定流程能走通；
  2) App 回传的强度与上限被正确解析；
  3) 我们发出的 strength / pulse / clear 指令到达 App 且格式正确；
  4) 断开后状态清理。

真正的设备行为（有没有输出）只能靠手机实测，见 docs/TESTING.md 测试 1。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import websockets  # noqa: E402

from bridge.dglab import WAVEFORMS, DGLabLink  # noqa: E402

PORT = 5699


async def recv_until(ws, predicate, timeout: float = 5.0) -> dict:
    """跳过 heartbeat 之类无关消息，直到拿到符合条件的一条。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        left = deadline - asyncio.get_event_loop().time()
        if left <= 0:
            raise TimeoutError("等待消息超时")
        msg = json.loads(await asyncio.wait_for(ws.recv(), left))
        if predicate(msg):
            return msg


async def main() -> int:
    link = DGLabLink(port=PORT, public_host="127.0.0.1")
    assert link.start(), "服务端启动失败"
    assert link.client_id, "clientId 为空"

    url = f"ws://127.0.0.1:{PORT}/{link.client_id}"
    async with websockets.connect(url) as ws:
        hello = await recv_until(ws, lambda m: m.get("type") == "bind")
        assert hello["message"] == "targetId", hello
        app_id = hello["clientId"]
        print("✓ 服务端分配了 App clientId")

        # 模拟 App 扫码后的绑定请求：clientId 填二维码里的终端 ID，targetId 填自己
        await ws.send(json.dumps({
            "type": "bind",
            "clientId": link.client_id,
            "targetId": app_id,
            "message": "DGLAB",
        }))
        confirm = await recv_until(
            ws, lambda m: m.get("type") == "bind" and str(m.get("message")) == "200"
        )
        assert confirm["targetId"] == app_id, confirm
        assert link.wait_bind(3.0), "终端侧没有进入已绑定状态"
        print("✓ 绑定流程正确")

        # 注意：pydglab-ws 的路由是「以终端 ID 为 clientId、App ID 为 targetId」，
        # 谁来发都一样，别按直觉写成 clientId=App。
        await ws.send(json.dumps({
            "type": "msg",
            "clientId": link.client_id,
            "targetId": app_id,
            "message": "strength-12+34+150+120",
        }))
        for _ in range(40):
            if link.limit_a == 150:
                break
            await asyncio.sleep(0.05)
        assert (link.strength_a, link.strength_b) == (12, 34), (link.strength_a, link.strength_b)
        assert (link.limit_a, link.limit_b) == (150, 120), (link.limit_a, link.limit_b)
        print("✓ App 回传解析正确")

        link.set_strength("A", 5)
        msg = await recv_until(ws, lambda m: str(m.get("message", "")).startswith("strength-"))
        assert msg["message"].endswith("+5"), msg
        assert msg["targetId"] == app_id, msg
        print("✓ strength 指令正确")

        link.send_pulse("B", WAVEFORMS["hit"][:2])
        msg = await recv_until(ws, lambda m: str(m.get("message", "")).startswith("pulse-B:"))
        frames = json.loads(msg["message"].split(":", 1)[1])
        assert len(frames) == 2 and all(len(f) == 16 for f in frames), frames
        print("✓ pulse 指令正确")

        link.clear("A")
        msg = await recv_until(ws, lambda m: str(m.get("message", "")).startswith("clear-"))
        assert msg["message"] in ("clear-1", "clear-A"), msg
        print("✓ clear 指令正确")

    for _ in range(40):
        if not link.bound:
            break
        await asyncio.sleep(0.05)
    assert not link.bound, "断开后应清除绑定状态"
    print("✓ 断开清理正确")

    link.stop()
    print("\n全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
