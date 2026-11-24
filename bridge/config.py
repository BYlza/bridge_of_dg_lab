"""配置加载。配置文件是 JSON，缺字段就用默认值，方便逐步补齐。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_APP_CONFIG = os.path.join(ROOT, "config", "app.json")
DEFAULT_PROFILE = os.path.join(ROOT, "config", "profiles", "ds_dc_1.004.json")


@dataclass
class AppConfig:
    ws_port: int = 5678
    public_host: str | None = None
    web_host: str = "127.0.0.1"
    web_port: int = 8765
    poll_hz: float = 100.0

    channel_mode: str = "A"
    max_intensity_a: float = 1.0     # A 通道硬上限（本机实测：只允许 1）
    max_intensity_b: float = 30.0    # B 通道硬上限
    min_intensity: float = 0.0
    floor_ratio: float = 0.35        # 受伤时至少给到上限的这个比例
    gamma: float = 0.5
    rise_rate: float = 20.0          # 归一化：每秒最多涨满量程的倍数
    decay_tau: float = 1.2
    deadzone: float = 0.005

    watchdog_sec: float = 3.0
    session_limit_sec: float = 1800.0
    cooldown_sec: float = 60.0

    # 单帧血量变化超过 max_hp 的这个比例，视为"读档/传送/重生"而不是受伤
    jump_ratio: float = 0.35

    profile: str = DEFAULT_PROFILE
    waveform: str = "hit"

    extra: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | None = None) -> "AppConfig":
        path = path or DEFAULT_APP_CONFIG
        raw: dict = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        # 环境变量覆盖：换网络 / 换设备时不用改文件，启动脚本直接设一下就行。
        # 优先级：命令行 --host-ip > BRIDGE_PUBLIC_HOST > app.json > 自动探测
        env_host = os.environ.get("BRIDGE_PUBLIC_HOST", "").strip()
        if env_host:
            raw["public_host"] = env_host
        known = {f for f in cls.__dataclass_fields__ if f != "extra"}
        kwargs = {k: v for k, v in raw.items() if k in known}
        cfg = cls(**kwargs)
        cfg.extra = {k: v for k, v in raw.items() if k not in known}
        if not os.path.isabs(cfg.profile):
            cfg.profile = os.path.join(ROOT, cfg.profile)
        return cfg
