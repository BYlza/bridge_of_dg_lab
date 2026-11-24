"""安全层：分通道硬上限、看门狗、连续输出冷却。

这一层不参与"好不好玩"的讨论，只负责"绝不出事"。
所有输出路径都必须经过 clamp()。
"""

from __future__ import annotations

import time


class SafetyGuard:
    def __init__(
        self,
        hard_caps: dict[str, int] | None = None,
        watchdog_sec: float = 3.0,
        session_limit_sec: float = 1800.0,
        cooldown_sec: float = 60.0,
    ) -> None:
        caps = {"A": 1, "B": 10}
        if hard_caps:
            caps.update({k.upper(): v for k, v in hard_caps.items()})
        self.hard_caps = {k: max(0, int(v)) for k, v in caps.items()}
        self.watchdog_sec = watchdog_sec
        self.session_limit_sec = session_limit_sec
        self.cooldown_sec = cooldown_sec

        self._last_event = time.monotonic()
        self._active_sec = 0.0
        self._cooling_until = 0.0

    # ------------------------------------------------------------------

    def note_event(self) -> None:
        self._last_event = time.monotonic()

    def watchdog_expired(self) -> bool:
        return (time.monotonic() - self._last_event) > self.watchdog_sec

    def tick(self, dt: float, level: float) -> None:
        if level > 0.5:
            self._active_sec += dt
            self._last_event = time.monotonic()

    def need_cooldown(self) -> bool:
        return self._active_sec >= self.session_limit_sec

    def cooling(self) -> bool:
        return time.monotonic() < self._cooling_until

    def start_cooldown(self) -> None:
        self._active_sec = 0.0
        self._cooling_until = time.monotonic() + self.cooldown_sec

    # ------------------------------------------------------------------

    def clamp(self, channel: str, value: float, app_limit: int) -> int:
        """三重钳制：本通道硬上限、App 上报上限、协议上限 200。"""
        hard = self.hard_caps.get(channel.upper(), 1)
        cap = min(hard, int(app_limit), 200)
        if self.cooling():
            return 0
        return max(0, min(int(round(value)), cap))

    def describe(self) -> str:
        caps = "/".join(f"{k}={v}" for k, v in sorted(self.hard_caps.items()))
        return (
            f"上限 {caps} 看门狗={self.watchdog_sec:.0f}s "
            f"累计输出={self._active_sec:.0f}s/{self.session_limit_sec:.0f}s"
            + (" [冷却中]" if self.cooling() else "")
        )
