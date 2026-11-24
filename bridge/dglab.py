"""郊狼 3.0 连接层：对 pydglab-ws 的薄封装。

协议细节（WS 绑定、心跳、strength/pulse 指令序列化）全部交给 pydglab-ws，
本文件只做三件事：
  1) 把它的 asyncio API 包成线程安全的同步 API（主循环是同步的，调用更省心）；
  2) 维护绑定/断开状态，断线后自动重新等待扫码；
  3) 提供 0-200 的强度钳制与「双通道归零」急停。

pydglab-ws 的数据格式：add_pulses(channel, *pulses)，每个 pulse 是
((freq1..freq4), (int1..int4))，对应 8 字节 / 100ms 的一帧。
"""

from __future__ import annotations

import asyncio
import logging
import socket
import threading
from contextlib import suppress
from typing import Callable, Optional

from pydglab_ws import (
    Channel,
    DGLabWSServer,
    FeedbackButton,
    RetCode,
    StrengthData,
    StrengthOperationType,
)

log = logging.getLogger("dglab")

MAX_STRENGTH = 200
_CHANNEL = {"A": Channel.A, "B": Channel.B}


def list_local_ips() -> list[str]:
    """列出本机可能的局域网 IPv4，按"手机最可能可达"排序。

    排除回环与 169.254 自动地址；192.168.x 优先，其次 10.x、172.16-31.x。
    有虚拟网卡（Radmin / Tailscale / VMware）时会出现多个候选，需要用户自己挑。
    """
    ips: set[str] = set()
    try:
        ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
    except OSError:
        pass
    finally:
        s.close()

    ips = {ip for ip in ips if not ip.startswith("127.") and not ip.startswith("169.254.")}

    def score(ip: str) -> tuple[int, str]:
        if ip.startswith("192.168."):
            return (0, ip)
        if ip.startswith("10."):
            return (1, ip)
        if ip.startswith("172."):
            return (2, ip)
        return (3, ip)

    return sorted(ips, key=score)


def local_ip() -> str:
    """取最可能可达的那个地址（仅作为默认值，可用 --host-ip 覆盖）。"""
    ips = list_local_ips()
    return ips[0] if ips else "127.0.0.1"


class DGLabLink:
    def __init__(
        self,
        port: int = 5678,
        host: str = "0.0.0.0",
        public_host: str | None = None,
        base_uri: str | None = None,
        on_strength: Optional[Callable[[int, int, int, int], None]] = None,
        on_feedback: Optional[Callable[[int], None]] = None,
        on_bind: Optional[Callable[[], None]] = None,
        on_disconnect: Optional[Callable[[], None]] = None,
    ) -> None:
        self.port = port
        self.host = host
        self.public_host = public_host or local_ip()
        # 二维码里写什么地址：默认 ws://<ip>:<port>；走隧道时传 wss://<域名>:443
        self.base_uri = base_uri or f"ws://{self.public_host}:{self.port}"

        self.client_id: str = ""
        self.qr_url: str = ""

        self.strength_a = 0
        self.strength_b = 0
        self.limit_a = MAX_STRENGTH
        self.limit_b = MAX_STRENGTH

        self._on_strength = on_strength
        self._on_feedback = on_feedback
        self._on_bind = on_bind
        self._on_disconnect = on_disconnect

        self._client = None
        self._bound = threading.Event()
        self._ready = threading.Event()
        self._loop = asyncio.new_event_loop()
        self._stop_evt: asyncio.Event | None = None
        self._thread: threading.Thread | None = None
        self._start_error: Exception | None = None

    # ------------------------------------------------------------------ 生命周期

    def start(self, wait_ready: float = 8.0) -> bool:
        self._thread = threading.Thread(target=self._run, name="dglab-ws", daemon=True)
        self._thread.start()
        if not self._ready.wait(wait_ready):
            log.error("Socket 服务端启动超时（端口 %s 可能被占用）", self.port)
            return False
        if self._start_error:
            log.error("Socket 服务端启动失败：%s", self._start_error)
            return False
        return True

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception as exc:  # noqa: BLE001
            self._start_error = exc
            log.exception("连接层异常退出")
        finally:
            self._ready.set()
            with suppress(Exception):
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())

    async def _main(self) -> None:
        self._stop_evt = asyncio.Event()
        async with DGLabWSServer(self.host, self.port, 60) as server:
            client = server.new_local_client()
            self._client = client
            if client.not_registered:
                await client.register()

            self.client_id = str(client.client_id)
            self.qr_url = client.get_qrcode(self.base_uri) or ""
            log.info("Socket 服务端已监听 %s:%s", self.host, self.port)
            self._ready.set()

            task = asyncio.create_task(self._session(), name="dglab-session")
            await self._stop_evt.wait()
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def _session(self) -> None:
        client = self._client
        assert client is not None

        await client.bind()
        while True:
            self._bound.set()
            log.info("App 已绑定：%s", client.target_id)
            self._call(self._on_bind)

            try:
                while True:
                    data = await client.recv_data()
                    if isinstance(data, StrengthData):
                        self._apply_strength(data)
                    elif isinstance(data, FeedbackButton):
                        log.info("App 按钮反馈：%s", int(data))
                        self._call(self._on_feedback, int(data))
                    elif data == RetCode.CLIENT_DISCONNECTED:
                        break
                    # RetCode.SUCCESS 心跳，忽略
            finally:
                self._bound.clear()

            log.warning("App 已断开，等待重新扫码")
            self._call(self._on_disconnect)
            await client.rebind()

    def _apply_strength(self, data: StrengthData) -> None:
        self.strength_a, self.strength_b = data.a, data.b
        self.limit_a, self.limit_b = data.a_limit, data.b_limit
        self._call(self._on_strength, data.a, data.b, data.a_limit, data.b_limit)

    def _call(self, fn, *args) -> None:
        if fn is None:
            return
        try:
            fn(*args)
        except Exception:  # noqa: BLE001
            log.exception("回调异常")

    def stop(self) -> None:
        if self._loop and self._stop_evt:
            self._loop.call_soon_threadsafe(self._stop_evt.set)
        if self._thread:
            self._thread.join(timeout=3)

    # ------------------------------------------------------------------ 状态

    @property
    def bound(self) -> bool:
        return self._bound.is_set()

    def wait_bind(self, timeout: float | None = None) -> bool:
        return self._bound.wait(timeout)

    # ------------------------------------------------------------------ 指令

    def set_strength(self, channel: str, value: int) -> None:
        ch = _CHANNEL[channel.upper()]
        value = max(0, min(int(value), MAX_STRENGTH))
        self._submit(lambda: self._client.set_strength(ch, StrengthOperationType.SET_TO, value))

    def add_strength(self, channel: str, delta: int) -> None:
        ch = _CHANNEL[channel.upper()]
        op = StrengthOperationType.INCREASE if delta >= 0 else StrengthOperationType.DECREASE
        value = abs(int(delta))
        self._submit(lambda: self._client.set_strength(ch, op, value))

    def send_pulse(self, channel: str, frames: list[str]) -> None:
        """frames 是 16 位十六进制字符串（8 字节 = 4 频率 + 4 强度）。"""
        if not frames:
            return
        ch = _CHANNEL[channel.upper()]
        pulses: list[tuple[tuple[int, int, int, int], tuple[int, int, int, int]]] = []
        for text in frames:
            try:
                raw = bytes.fromhex(text)
            except ValueError:
                continue
            if len(raw) != 8:
                continue
            pulses.append(((raw[0], raw[1], raw[2], raw[3]), (raw[4], raw[5], raw[6], raw[7])))
        if not pulses:
            return

        # 单次最多 86 帧，留点余量
        for i in range(0, len(pulses), 80):
            part = tuple(pulses[i : i + 80])
            self._submit(lambda part=part: self._client.add_pulses(ch, *part))

    def clear(self, channel: str) -> None:
        ch = _CHANNEL[channel.upper()]
        self._submit(lambda: self._client.clear_pulses(ch))

    def zero(self) -> None:
        """双通道归零 + 清空波形队列——急停用。"""
        self.set_strength("A", 0)
        self.set_strength("B", 0)
        self.clear("A")
        self.clear("B")

    def _submit(self, factory) -> None:
        loop = self._loop
        if loop is None or not loop.is_running() or self._client is None:
            return
        if not self._bound.is_set():
            return
        try:
            asyncio.run_coroutine_threadsafe(factory(), loop)
        except RuntimeError:
            pass


# ---------------------------------------------------------------------- 波形

def frame(freq: int, intensity: int) -> str:
    """生成一帧 8 字节波形（4 频率 + 4 强度），代表 100ms。

    V3 的频率字节是压缩编码，这里只使用社区文档中验证过的两个取值
    （0x0A≈10Hz、0x64≈100Hz），避免自己换算导致 App 收到却打不出波形。
    """
    f = 0x0A if freq <= 30 else 0x64
    i = max(0, min(int(intensity), 100))
    return bytes([f, f, f, f, i, i, i, i]).hex().upper()


def constant(count: int, freq: int = 100, intensity: int = 100) -> list[str]:
    return [frame(freq, intensity) for _ in range(count)]


WAVEFORMS: dict[str, list[str]] = {
    "hit": constant(10, freq=10, intensity=100),      # 短促锐利
    "rumble": constant(10, freq=100, intensity=70),   # 低频闷振
    "heart": (
        [frame(100, 100)] * 2
        + [frame(100, 0)] * 6
        + [frame(100, 100)] * 2
        + [frame(100, 0)] * 10
    ),
}
