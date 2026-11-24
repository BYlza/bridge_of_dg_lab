"""按结构特征重新定位血量：只需要你报一个当前血量数字。

依据实测结构（导演剪辑版 1.004）：
    偏移 +0  int32    当前血量（0-1000）
    偏移 +4  float    HP/1000
    偏移 +8  float    HP/1000（再来一份）

先扫描 int == 你报的数字，再校验后 8 字节是不是两个 N/1000 的浮点。
命中通常只有一处。扫描多进程并行，内存大时也能在几十秒内完成。

    python tools/relocate.py --hp 767
    python tools/relocate.py --hp 767 --write      # 命中唯一时写进 profile
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from typing import List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bridge.config import DEFAULT_PROFILE
from bridge.reader import (
    PROCESS_QUERY_INFORMATION,
    PROCESS_VM_READ,
    find_process_id,
    k32,
)
from tools.hpfind import CHUNK, iter_regions, read_span


def _scan_regions(job):
    """子进程：扫一段区域，返回命中地址。"""
    pid, regions, pattern = job
    handle = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        return []
    hits = []
    try:
        for base, length in regions:
            off = 0
            while off < length:
                n = min(CHUNK, length - off)
                data = read_span(handle, base + off, n)
                if not data:
                    break
                pos = 0
                while True:
                    pos = data.find(pattern, pos)
                    if pos < 0:
                        break
                    if (off + pos) % 4 == 0:
                        hits.append(base + off + pos)
                    pos += 1
                off += n
    finally:
        k32.CloseHandle(handle)
    return hits


def split_regions(regions: List[Tuple[int, int]], workers: int):
    """按字节数均分成若干块，让每个进程负载接近。"""
    total = sum(size for _, size in regions)
    target = total / max(1, workers)
    buckets = [[] for _ in range(workers)]
    acc, idx = 0, 0
    for base, size in regions:
        buckets[idx].append((base, size))
        acc += size
        if acc >= target and idx < workers - 1:
            idx += 1
            acc = 0
    return [b for b in buckets if b]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hp", type=int, required=True, help="游戏里显示的血量")
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 4))
    args = parser.parse_args()

    pid = find_process_id("ds.exe")
    if not pid:
        print("找不到 ds.exe")
        return 1

    handle = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        print("打开进程失败")
        return 1
    regions = list(iter_regions(handle))
    k32.CloseHandle(handle)
    total = sum(s for _, s in regions)
    print(f"待扫描 {total/1024/1024:.0f} MB / {len(regions)} 个区域，{args.workers} 进程并行")

    pattern = struct.pack("<i", args.hp)
    jobs = [(pid, chunk, pattern) for chunk in split_regions(regions, args.workers)]

    t0 = time.time()
    hits = []
    if len(jobs) == 1:
        hits = _scan_regions(jobs[0])
    else:
        with ProcessPoolExecutor(max_workers=len(jobs)) as pool:
            for part in pool.map(_scan_regions, jobs):
                hits.extend(part)
    print(f"  int == {args.hp} 命中 {len(hits)} 个，用时 {time.time()-t0:.1f}s")

    ratio = args.hp / 1000.0
    handle = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    matches = []
    try:
        for addr in hits:
            data = read_span(handle, addr, 12)
            if len(data) < 12:
                continue
            f1, f2 = struct.unpack_from("<ff", data, 4)
            if abs(f1 - ratio) <= 0.0015 and abs(f2 - ratio) <= 0.0015:
                matches.append(addr)
    finally:
        k32.CloseHandle(handle)

    print(f"结构匹配: {len(matches)} 个")
    for m in matches:
        print(f"  0x{m:012X}")

    if len(matches) == 1:
        addr = matches[0]
        print(f"\n唯一命中：0x{addr:X}（int {args.hp} / float {ratio:.3f} ×2）")
        if args.write:
            with open(args.profile, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            hp = raw.setdefault("health", {})
            hp["type"] = "int"
            hp["max"] = 1000
            hp["address"] = hex(addr)
            with open(args.profile, "w", encoding="utf-8") as fh:
                json.dump(raw, fh, ensure_ascii=False, indent=2)
            print(f"已写入 {args.profile}")
    elif not matches:
        print("没有匹配。确认输入的血量数字是否与游戏内一致（可能刚回血）。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
