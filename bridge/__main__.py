"""Bridge of DG-LAB — 主程序。

    python -m bridge              # 预览模式：连设备、读血量、只打印，不驱动输出
    python -m bridge --arm        # 武装模式：受伤时真的输出（请先确认上限！）
    python -m bridge --arm --channel B

设计约束：
  - 只读游戏内存；
  - 输出前必过 SafetyGuard.clamp()；
  - 任何异常 / Ctrl+C / App 断连 → 立即归零。
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time

from .config import AppConfig, ROOT
from .dglab import WAVEFORMS, DGLabLink
from .mapper import IntensityMapper, MapperConfig
from .qr import show_qr
from .reader import GameReader, Profile, ReaderError
from .safety import SafetyGuard
from .webui import ControlPanel

log = logging.getLogger("bridge")

BANNER = r"""
   ___      _              __     ___    ____   ____   _        _    ___
  | _ )_ _ (_)_ __  ___ ___\ \   / / |  |  _ \ / ___| | |      / \  | _ )
  | _ \ '_|| | '_ \/ -_)___| \ \ / /| |__| | | | |  _  | |__   / _ \ | _ \
  |___/|_|  |_| .__/\___|    \_/  \_\____|_| |_|_| \__| |____| /_/ \_\|___/
              |_|
        死亡搁浅 · 导演剪辑版  ×  郊狼 3.0        只读内存 / 可随时急停（Ctrl+C）
"""


def health_alive(reader: GameReader) -> bool:
    """地址是否还有效：读得到、且落在 0..1000 的合理区间。"""
    try:
        hp = reader.health()
    except ReaderError:
        return False
    return 0 <= hp <= 1000


LAST_HP = os.path.join(ROOT, "out", "last_hp.txt")


def read_last_hp() -> float | None:
    try:
        with open(LAST_HP, "r", encoding="utf-8") as fh:
            return float(fh.read().strip())
    except (OSError, ValueError):
        return None


def write_last_hp(hp: float) -> None:
    try:
        os.makedirs(os.path.dirname(LAST_HP), exist_ok=True)
        with open(LAST_HP, "w", encoding="utf-8") as fh:
            fh.write(f"{hp:.0f}")
    except OSError:
        pass


def _relocate(profile_path: str, hp: int) -> bool:
    script = os.path.join(ROOT, "tools", "relocate.py")
    subprocess.run([sys.executable, script, "--hp", str(hp), "--write"], check=False)
    try:
        prof = Profile.load(profile_path)
        probe = GameReader(prof)
        probe.attach()
        ok = health_alive(probe)
        probe.close()
        return ok
    except (ReaderError, OSError, ValueError):
        return False


def relocate_auto(profile_path: str) -> bool:
    """地址失效时自动重定位。

    候选顺序：上次记住的血量 → 满血 1000。存档里血量通常就是这两种之一，
    所以绝大多数情况不需要人工输入。
    """
    print("⚠️ 血量地址已失效（退出游戏 / 读档后会这样，属正常）。")

    hints: list[int] = []
    last = read_last_hp()
    if last and 1 <= last <= 1000:
        hints.append(int(last))
    hints.append(1000)
    hints = list(dict.fromkeys(hints))

    for hp in hints:
        print(f"   尝试按血量 {hp} 定位，约 30–40 秒…", flush=True)
        if _relocate(profile_path, hp):
            print(f"   ✅ 已自动恢复（血量 {hp}）。")
            return True

    print("   自动尝试没对上（这局的血量既不是上次值也不是满血）。")
    if not sys.stdin or not sys.stdin.isatty():
        print("   请在设置页面填写当前血量，或执行：python tools\\relocate.py --hp <当前血量> --write")
        return False
    try:
        text = input("   请输入游戏里当前的血量数字（直接回车跳过）: ").strip()
    except EOFError:
        text = ""
    if not text.isdigit():
        print("   已跳过，进入预览模式。")
        return False
    return _relocate(profile_path, int(text))


def build_mapper(cfg: AppConfig) -> IntensityMapper:
    return IntensityMapper(
        MapperConfig(
            channel_mode=cfg.channel_mode,
            cap_a=cfg.max_intensity_a,
            cap_b=cfg.max_intensity_b,
            min_intensity=cfg.min_intensity,
            floor_ratio=cfg.floor_ratio,
            gamma=cfg.gamma,
            rise_rate=cfg.rise_rate,
            decay_tau=cfg.decay_tau,
            deadzone=cfg.deadzone,
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bridge of DG-LAB")
    parser.add_argument("--config", default=None)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--channel", choices=["A", "B", "dual"], default=None)
    parser.add_argument("--host-ip", default=None,
                        help="二维码里用的 IP（多网卡时手动指定，比如热点 192.168.137.1）")
    parser.add_argument("--qr-uri", default=None,
                        help="二维码里直接写这个地址，例如 wss://xxx.ngrok-free.dev:443（走隧道时用）")
    parser.add_argument("--arm", action="store_true", help="真正驱动输出（默认只预览）")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = AppConfig.load(args.config)
    if args.profile:
        cfg.profile = args.profile
    if args.port:
        cfg.ws_port = args.port
    if args.channel:
        cfg.channel_mode = args.channel

    print(BANNER)
    print(f"通道模式：{cfg.channel_mode}    上限 A={cfg.max_intensity_a:g} B={cfg.max_intensity_b:g}    "
          f"{'已武装' if args.arm else '预览模式（不会输出）'}")
    print("⚠️ 先从最低强度试，出现任何不适应立刻 Ctrl+C 或关掉主机电源。\n")

    # ---------------------------------------------------------------- 设备链路

    pending = {"A": (None, 0.0), "B": (None, 0.0)}
    state: dict = {"hp": None, "hp_max": 1000.0, "armed": False, "latency_ms": None}

    def on_strength(a: int, b: int, la: int, lb: int) -> None:
        now = time.monotonic()
        for ch, value in (("A", a), ("B", b)):
            sent, t0 = pending[ch]
            if sent is not None and value == sent and t0:
                ms = (now - t0) * 1000
                state["latency_ms"] = ms
                log.info("回环延迟 %s：%.0f ms", ch, ms)
                pending[ch] = (None, 0.0)
        log.debug("App 回传：A=%s B=%s 上限 A=%s B=%s", a, b, la, lb)

    link = DGLabLink(
        port=cfg.ws_port,
        public_host=args.host_ip or cfg.public_host,
        base_uri=args.qr_uri,
        on_strength=on_strength,
        on_feedback=lambda code: log.info("App 按钮反馈：%s", code),
        on_disconnect=lambda: log.warning("App 已断开，输出已归零"),
    )
    if not link.start():
        print("Socket 服务端启动失败，检查端口是否被占用。")
        return 1

    print(f"二维码地址：{link.base_uri}")
    show_qr(link.qr_url, out_dir=os.path.join(ROOT, "out"))
    print("等待手机扫码…\n")

    # ---------------------------------------------------------------- 读游戏

    reader: GameReader | None = None
    preview_reason = ""
    try:
        profile = Profile.load(cfg.profile)
        if not profile.usable:
            preview_reason = "profile 里还没有血量地址（先做 M1 找地址）"
        else:
            reader = GameReader(profile)
            reader.attach()
            log.info("已附加 %s，模块基址 0x%X", profile.process, reader.base)

            # 地址是会话级的：退出游戏/读档后会失效，这里就地重定位
            if not health_alive(reader):
                reader.close()
                if relocate_auto(cfg.profile):
                    profile = Profile.load(cfg.profile)
                    reader = GameReader(profile)
                    reader.attach()
                    log.info("重定位完成，血量地址 0x%X", profile.health_address or 0)
                else:
                    reader = None
                    preview_reason = "血量地址已失效，重定位未完成"
    except (ReaderError, FileNotFoundError, ValueError) as exc:
        preview_reason = str(exc)
        reader = None

    if preview_reason:
        print(f"⚠️ 未接入游戏数据：{preview_reason}")
        print("   程序会保持连接，但不会因为游戏状态输出。\n")

    mapper = build_mapper(cfg)
    guard = SafetyGuard(
        hard_caps={"A": cfg.max_intensity_a, "B": cfg.max_intensity_b},
        watchdog_sec=cfg.watchdog_sec,
        session_limit_sec=cfg.session_limit_sec,
        cooldown_sec=cfg.cooldown_sec,
    )
    frames = WAVEFORMS.get(cfg.waveform, WAVEFORMS["hit"])

    dt = 1.0 / max(cfg.poll_hz, 1.0)
    baseline: float | None = None
    last_sent: dict[str, int] = {ch: -1 for ch in ("A", "B")}
    last_pulse = 0.0
    last_status = 0.0
    last_hp = 0.0
    last_hp_save = 0.0
    bad_since: float | None = None
    armed = args.arm and reader is not None
    state["armed"] = armed
    if reader is not None:
        state["hp_max"] = reader.profile.health_max
    panel = ControlPanel(
        cfg, mapper, guard, link, state,
        host=cfg.web_host, port=cfg.web_port,
    )
    if not panel.start():
        log.warning("设置页面未启动（端口 %s 被占用？）", cfg.web_port)
    else:
        print(f"设置页面：http://127.0.0.1:{cfg.web_port}\n")

    def silence(reason: str = "") -> None:
        link.zero()
        for ch in last_sent:
            last_sent[ch] = -1
        mapper.reset()
        if reason:
            log.warning("归零：%s", reason)

    try:
        while True:
            now = time.monotonic()

            # 设置页面触发的重定位完成后，重新附加
            if state.pop("need_reattach", False):
                if reader is not None:
                    reader.close()
                try:
                    profile = Profile.load(cfg.profile)
                    reader = GameReader(profile)
                    reader.attach()
                    if not health_alive(reader):
                        reader.close()
                        reader = None
                except (ReaderError, OSError, ValueError) as exc:
                    log.warning("重新附加失败：%s", exc)
                    reader = None
                armed = args.arm and reader is not None
                state["armed"] = armed
                baseline = None
                last_sent = {"A": -1, "B": -1}
                state["hp"] = reader.health() if reader is not None else None
                log.info("重新附加完成：%s", "已接入" if reader is not None else "仍未接入")

            if not link.bound:
                if now - last_status > 5:
                    print("…等待扫码（手机需与电脑同一局域网）")
                    last_status = now
                time.sleep(0.5)
                continue

            # ---------------- 读血量
            if reader is not None:
                try:
                    hp = reader.health()
                except ReaderError as exc:
                    log.debug("读取失败：%s", exc)
                    hp = None

                if hp is None or not (0 <= hp <= 1000):
                    # 加载 / 重生瞬间会读到脏数据，容忍几秒再判定地址失效
                    if bad_since is None:
                        bad_since = now
                        log.warning("血量读数异常（%.0f），先观察…", hp if hp is not None else -1)
                    if now - bad_since < 8.0:
                        silence()
                        time.sleep(0.05)
                        continue
                    log.error("血量地址持续异常超过 8 秒，判定失效")
                    silence("地址失效")
                    state["hp"] = None
                    if not state.get("relocating") and not state.get("relocate_failed"):
                        hint = int(last_hp) if last_hp and 1 <= last_hp <= 1000 else 1000
                        print(
                            f"⚠️ 血量地址失效，正在后台按 {hint} 重定位…"
                            "（也可在设置页面填入当前血量）"
                        )
                        panel.relocate(hint)
                    time.sleep(1.0)
                    continue

                bad_since = None

                if baseline is None:
                    baseline = hp
                    log.info("血量基线：%.0f", hp)
                else:
                    delta = baseline - hp
                    max_hp = reader.profile.health_max
                    if abs(delta) > max_hp * cfg.jump_ratio:
                        # 读档 / 传送 / 重生 / 大幅回血 → 不当成受伤
                        log.info("血量跳变 %.0f → %.0f，重置基线", baseline, hp)
                        baseline = hp
                        mapper.reset()
                    elif delta > 0:
                        target = mapper.on_damage(delta, max_hp)
                        if target > 0:
                            guard.note_event()
                            last_pulse = 0.0  # 立刻推波形，别等保活周期
                            state["last_damage"] = {
                                "delta": delta,
                                "pct": delta / max_hp * 100,
                                "peak": target * 100,
                                "t": time.time(),
                            }
                            log.info(
                                "受伤 -%.0f（%.1f%%）→ 峰值 %.0f%%，预计 A=%.1f B=%.1f",
                                delta,
                                delta / max_hp * 100,
                                target * 100,
                                mapper.cap_for("A") * target,
                                mapper.cap_for("B") * target,
                            )
                        baseline = hp
                    else:
                        baseline = hp
                last_hp = hp
                state["hp"] = hp
                if now - last_hp_save > 10:
                    write_last_hp(hp)
                    last_hp_save = now

            # ---------------- 设置页面正在测试：让出输出权
            if panel.testing.is_set():
                last_sent["A"] = last_sent["B"] = -1
                time.sleep(dt)
                continue

            # ---------------- 映射与输出
            level = mapper.tick(dt)
            guard.tick(dt, level)

            if guard.watchdog_expired() and level > 0:
                silence("看门狗超时")
            elif guard.need_cooldown():
                guard.start_cooldown()
                silence("连续输出达到上限，进入冷却")
            else:
                for ch in mapper.channels:
                    app_limit = link.limit_a if ch == "A" else link.limit_b
                    value = guard.clamp(ch, mapper.intensity(ch), app_limit) if armed else 0
                    if value != last_sent[ch]:
                        link.set_strength(ch, value)
                        last_sent[ch] = value
                        if value > 0:
                            pending[ch] = (value, time.monotonic())
                        if armed:
                            log.debug("输出 %s=%s（App 上限 %s）", ch, value, app_limit)

                # 波形保活：始终把帧队列填满（强度为 0 也发），
                # 这样受伤时设备不会等下一帧才出声，起效更快
                if armed and (now - last_pulse) > 0.6:
                    for ch in mapper.channels:
                        link.send_pulse(ch, frames)
                    last_pulse = now

            # ---------------- 状态行
            if now - last_status > 2:
                hp_text = f"{last_hp:.4f}" if reader is not None else "n/a"
                levels = " ".join(
                    f"{ch}={mapper.intensity(ch):.1f}" for ch in mapper.channels
                )
                print(
                    f"  血量={hp_text}  {levels}  峰值={level * 100:3.0f}%  "
                    f"{'ARMED' if armed else 'dry'}  {guard.describe()}"
                )
                last_status = now

            time.sleep(dt)

    except KeyboardInterrupt:
        print("\n收到中断，正在归零…")
    finally:
        silence("退出")
        time.sleep(0.3)
        link.stop()
        if reader is not None:
            reader.close()

    print("已退出。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
