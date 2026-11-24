"""M1 辅助：验证血量读取是否正确。

用法（在项目根目录）：
    python tools/hp_probe.py --list-modules
    python tools/hp_probe.py --read 0x1234ABCD          # 直接读绝对地址的 float
    python tools/hp_probe.py --set-chain 0x1A2B3C4 0x10 0x20 0x8
    python tools/hp_probe.py --watch

--set-chain 会把指针链写进 profile（module_offset + offsets），格式与 Cheat Engine 的
[[ds.exe+1A2B3C4]+10+20+8] 一致。

--watch 只打印「变化」，方便你一边游戏里挨打/回血，一边看数值是否对得上。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bridge.config import DEFAULT_PROFILE  # noqa: E402
from bridge.reader import GameReader, Profile, ReaderError  # noqa: E402


def save_chain(profile_path: str, module_offset: int, offsets: list[int], max_hp: float | None):
    with open(profile_path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    hp = raw.setdefault("health", {})
    hp["pointer"] = {
        "module_offset": hex(module_offset),
        "offsets": [hex(x) for x in offsets],
    }
    hp.pop("address", None)  # 有了稳定链就不用临时地址了
    if max_hp is not None:
        hp["max"] = max_hp
    with open(profile_path, "w", encoding="utf-8") as fh:
        json.dump(raw, fh, ensure_ascii=False, indent=2)
    print(f"已写入 {profile_path}")
    print(f"  链：[[{raw.get('module', 'ds.exe')}+{hex(module_offset)}]"
          + "".join(f"+{hex(x)}" for x in offsets) + "]")


def save_address(profile_path: str, address: int, max_hp: float | None):
    with open(profile_path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    hp = raw.setdefault("health", {})
    hp["address"] = hex(address)
    if max_hp is not None:
        hp["max"] = max_hp
    with open(profile_path, "w", encoding="utf-8") as fh:
        json.dump(raw, fh, ensure_ascii=False, indent=2)
    print(f"已写入临时地址 {hex(address)}（仅本次游戏会话有效）")


def main() -> int:
    parser = argparse.ArgumentParser(description="血量读取验证工具")
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--set-chain", nargs="+", metavar="X",
                        help="module_offset offsets...（十六进制，可省 0x）")
    parser.add_argument("--set-address", default=None,
                        help="临时绝对地址，如 0x1F2A3B4C（本次会话有效，重启游戏失效）")
    parser.add_argument("--max", type=float, default=None, help="最大血量（写入 profile）")
    parser.add_argument("--read", default=None, help="直接读绝对地址的 float，如 0x1234")
    parser.add_argument("--list-modules", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--hz", type=float, default=10.0)
    args = parser.parse_args()

    if args.set_chain:
        nums = [int(x, 16) for x in args.set_chain]
        save_chain(args.profile, nums[0], nums[1:], args.max)
        if not args.watch and not args.read:
            return 0

    if args.set_address:
        save_address(args.profile, int(args.set_address, 0), args.max)
        if not args.watch and not args.read:
            return 0

    profile = Profile.load(args.profile)
    reader = GameReader(profile)
    try:
        reader.attach()
    except ReaderError as exc:
        print(f"附加失败：{exc}")
        return 1

    print(f"进程 {profile.process}，模块基址 0x{reader.base:X}")

    if args.list_modules:
        for name, base, size in sorted(reader.modules(), key=lambda x: -x[2])[:15]:
            print(f"  0x{base:012X}  {size/1024/1024:8.1f} MB  {name}")
        reader.close()
        return 0

    if args.read:
        addr = int(args.read, 16)
        try:
            print(f"0x{addr:X} = {reader.read_float(addr)!r}")
        except ReaderError as exc:
            print(f"读取失败：{exc}")
        reader.close()
        return 0

    if not profile.usable:
        print("profile 里还没有血量地址。先用 --set-address（临时）或 --set-chain（稳定）写入。")
        reader.close()
        return 1

    try:
        addr = reader.refresh_health_addr()
        print(f"血量地址 0x{addr:X}")
    except ReaderError as exc:
        print(f"指针链解析失败：{exc}")
        reader.close()
        return 1

    print("开始监视，Ctrl+C 结束。\n")

    prev: float | None = None
    last = 0.0
    hz = max(args.hz, 1.0)
    try:
        while True:
            try:
                hp = reader.health()
            except ReaderError as exc:
                print(f"读取异常：{exc}")
                time.sleep(0.5)
                continue

            now = time.monotonic()
            if prev is None:
                print(f"  初始 = {hp:.6f}")
            elif hp != prev:
                delta = prev - hp
                tag = ""
                if abs(delta) > profile.health_max * 0.35:
                    tag = "  ← 大幅跳变（读档/传送/重生？）"
                elif delta > 0:
                    tag = "  ← 掉血"
                elif delta < 0:
                    tag = "  ← 回血"
                print(f"  {hp:.6f}  (Δ {delta:+.6f}){tag}")
                prev = hp
            elif now - last > 3:
                print(f"  {hp:.6f}  (静止)")
                last = now

            if prev is None:
                prev = hp
                last = now
            time.sleep(1.0 / hz)
    except KeyboardInterrupt:
        pass
    finally:
        reader.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
