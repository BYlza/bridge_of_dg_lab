"""伤害 → 强度映射。

内部统一用 0..1 的归一化量程，输出时再按各通道自己的上限换算：
    A 通道 cap = 1，B 通道 cap = 10
这样两条通道共用同一套伤害曲线，又各自守住自己的硬上限。
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class MapperConfig:
    channel_mode: str = "A"          # "A" | "B" | "dual"
    cap_a: float = 1.0               # A 通道允许的最大强度
    cap_b: float = 30.0              # B 通道允许的最大强度
    min_intensity: float = 0.0
    floor_ratio: float = 0.35        # 只要有伤害，至少给到上限的这个比例
    gamma: float = 0.5               # <1：小伤害也有感知，大伤害不炸顶
    rise_rate: float = 20.0          # 归一化上升速率：每秒最多涨满量程的几倍
    decay_tau: float = 1.2           # 衰减时间常数（秒）
    deadzone: float = 0.005          # 伤害占最大生命低于此值忽略


class IntensityMapper:
    """单条"伤害脉冲"状态机。

    peak  —— 目标峰值（0..1），受伤时抬升、随后指数衰减
    level —— 实际输出（0..1），向上受 rise_rate 限速，向下随 peak 一起衰减
    """

    def __init__(self, cfg: MapperConfig):
        self.cfg = cfg
        self.peak = 0.0
        self.level = 0.0
        self.last_damage = 0.0

    # ------------------------------------------------------------------

    def on_damage(self, delta: float, max_hp: float) -> float:
        """吃进一次伤害，返回归一化目标强度（0 表示低于死区）。"""
        if max_hp <= 0 or delta <= 0:
            return 0.0
        ratio = min(delta / max_hp, 1.0)
        if ratio < self.cfg.deadzone:
            return 0.0

        target = self.cfg.floor_ratio + (1.0 - self.cfg.floor_ratio) * (
            ratio ** self.cfg.gamma
        )
        target = min(target, 1.0)
        if target > self.peak:
            self.peak = target
        self.last_damage = delta
        return target

    def tick(self, dt: float) -> float:
        cfg = self.cfg
        if self.peak > 0:
            self.peak *= math.exp(-dt / max(cfg.decay_tau, 1e-3))
            if self.peak < 0.01:
                self.peak = 0.0

        if self.peak > self.level:
            self.level = min(self.peak, self.level + cfg.rise_rate * dt)
        else:
            self.level = self.peak

        if self.level < 0.01:
            self.level = 0.0
        return self.level

    # ------------------------------------------------------------------

    @property
    def channels(self) -> list[str]:
        mode = self.cfg.channel_mode.upper()
        if mode == "DUAL":
            return ["A", "B"]
        return [mode] if mode in ("A", "B") else ["A"]

    def cap_for(self, channel: str) -> float:
        return self.cfg.cap_b if channel.upper() == "B" else self.cfg.cap_a

    def intensity(self, channel: str) -> float:
        """归一化 level → 该通道的绝对强度（还没经过 SafetyGuard 钳制）。"""
        cap = self.cap_for(channel)
        return self.cfg.min_intensity + (cap - self.cfg.min_intensity) * self.level

    def reset(self) -> None:
        self.peak = 0.0
        self.level = 0.0
