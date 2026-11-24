"""M0 测试：只验证「电脑 ↔ 手机 App ↔ 郊狼」这条链路，完全不碰游戏。

用法（在项目根目录）：
    python tools/bench_ws.py
    python tools/bench_ws.py --port 5678

流程：
    1. 运行后终端打印二维码（同时保存 out/qr.svg）；
    2. 手机连同一个 Wi-Fi，打开 DG-Lab App → SOCKET 控制 → 扫码；
    3. 连接成功后用下面的命令手动打强度 / 波形。

命令：
    a 30         A 通道设为 30（0-200，实际受 App 上限限制）
    b 30         B 通道设为 30
    +5 / -5      A 通道相对加减
    p [名字] [秒] 播放波形（hit / rumble / heart），默认 hit 3 秒
    c            清空波形队列
    0            双通道归零 + 清队列（急停）
    r            按当前网络重新生成二维码（换 Wi-Fi / 热点后用）
    s            查看状态
    q            退出（退出前会自动归零）
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bridge.dglab import WAVEFORMS, DGLabLink, list_local_ips, local_ip  # noqa: E402
from bridge.qr import show_qr  # noqa: E402


class PulseStream:
    """持续喂波形：每 0.85s 发 1s 的帧，保证 App 端不断音。"""

    def __init__(self, link: DGLabLink, channel: str = "A", name: str = "hit"):
        self.link = link
        self.channel = channel
        self.name = name
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        frames = WAVEFORMS.get(self.name, WAVEFORMS["hit"])
        while not self._stop.is_set():
            self.link.send_pulse(self.channel, frames)
            self._stop.wait(0.85)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None


def main() -> int:
    parser = argparse.ArgumentParser(description="郊狼链路测试（不含游戏）")
    parser.add_argument("--port", type=int, default=5678)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--host-ip", default=None,
                        help="二维码里用的 IP（多网卡时手动指定，比如热点 192.168.137.1）")
    parser.add_argument("--qr-uri", default=None,
                        help="二维码里直接写这个地址，例如 wss://xxx.ngrok-free.dev:443（走隧道时用）")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    link = DGLabLink(
        port=args.port,
        host=args.host,
        public_host=args.host_ip,
        base_uri=args.qr_uri,
        on_strength=lambda a, b, la, lb: print(f"  [App] 强度 A={a} B={b} 上限 A={la} B={lb}"),
        on_feedback=lambda code: print(f"  [App] 按钮反馈 {code}"),
        on_disconnect=lambda: print("  [App] 已断开，可重新扫码"),
    )

    if not link.start():
        print("启动失败：端口被占用？换 --port 试试。")
        return 1

    print(f"二维码将使用地址：{link.base_uri}")
    candidates = [ip for ip in list_local_ips() if ip != link.public_host]
    if candidates:
        print(f"其他可用地址（连不上时用 --host-ip 换一个）：{', '.join(candidates)}")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    show_qr(link.qr_url, out_dir=os.path.join(root, "out"))
    print("⚠️ 扫码提示「服务器连接失败」时，按顺序排查：")
    print("   1) 二维码里的 IP 是不是本机当前 IP（换过网络后必须重启本程序重新生成）")
    print("   2) 用管理员 PowerShell 执行 tools\\allow_firewall.ps1 放行 TCP 端口")
    print("   3) 手机浏览器打开 http://<二维码里的IP>:<端口>，能立刻报错才算网络通\n")

    print("等待扫码…（扫码后这里会提示已连接）")
    last_state = False

    stream = PulseStream(link)
    current = {"A": 0, "B": 0}

    try:
        while True:
            if link.bound and not last_state:
                print(">>> 已绑定，可以开始测试了。先 a 5 试试，别一上来就拉高。\n")
                last_state = True
            elif not link.bound and last_state:
                print(">>> 连接断开，等待重新扫码。\n")
                last_state = False
                if stream:
                    stream.stop()

            try:
                line = input("dg> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not line:
                continue
            parts = line.split()
            cmd = parts[0].lower()

            if cmd in ("q", "quit", "exit"):
                break
            if cmd == "s":
                print(
                    f"  连接={'是' if link.bound else '否'}  A={current['A']} B={current['B']} "
                    f"App上限 A={link.limit_a} B={link.limit_b}"
                )
                continue
            if cmd == "0":
                stream.stop()
                link.zero()
                current = {"A": 0, "B": 0}
                print("  已归零并清空队列")
                continue
            if cmd == "c":
                stream.stop()
                link.clear("A")
                link.clear("B")
                print("  已清空波形队列")
                continue
            if cmd in ("r", "qr"):
                if args.qr_uri:
                    print(f"  使用固定地址：{link.base_uri}")
                else:
                    link.public_host = local_ip()
                    link.base_uri = f"ws://{link.public_host}:{args.port}"
                    print(f"  已按当前网络重新生成，地址：{link.base_uri}")
                root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                show_qr(link.qr_url, out_dir=os.path.join(root, "out"))
                continue
            if cmd in ("a", "b"):
                if len(parts) < 2:
                    print(f"  用法：{cmd} <0-200>")
                    continue
                try:
                    value = int(parts[1])
                except ValueError:
                    print("  强度必须是整数")
                    continue
                ch = cmd.upper()
                current[ch] = value
                link.set_strength(ch, value)
                print(f"  {ch} 通道 → {value}")
                continue
            if cmd in ("+", "-", "+5", "-5"):
                delta = int(cmd)
                if len(parts) > 1:
                    try:
                        delta = int(parts[1])
                    except ValueError:
                        pass
                current["A"] = max(0, min(200, current["A"] + delta))
                link.add_strength("A", delta)
                print(f"  A 通道 → {current['A']}")
                continue
            if cmd in ("p", "play", "pulse"):
                name = parts[1] if len(parts) > 1 else "hit"
                if name not in WAVEFORMS:
                    print(f"  可用波形：{', '.join(WAVEFORMS)}")
                    continue
                seconds = float(parts[2]) if len(parts) > 2 else 3.0
                if current["A"] <= 0:
                    print("  提示：先把 A 通道强度调起来（比如 a 8），否则波形没输出")
                stream = PulseStream(link, "A", name)
                stream.start()
                print(f"  播放 {name} 波形 {seconds:.0f}s（强度沿用 A={current['A']}）")

                def _auto_zero(sec=seconds):
                    time.sleep(sec)
                    link.zero()
                    current["A"] = 0
                    print("\n  [自动] 播放结束，已归零")

                threading.Thread(target=_auto_zero, daemon=True).start()
                continue

            print("  未知命令。输入 a 5 / +5 / p hit 3 / 0 / s / q")

    finally:
        stream.stop()
        link.zero()
        time.sleep(0.4)
        link.stop()
        print("已退出。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
