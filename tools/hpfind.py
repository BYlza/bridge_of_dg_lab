"""按"精确浮点值"扫描进程内存，用来快速定位血量。

原理：
  - 满血时血量大概率是精确的 1.0f（字节 00 00 80 3F），
    用 bytes.find 在 C 层扫描，比逐字节循环快两三个数量级；
  - 第一遍记下所有候选地址；
  - 受伤后再按"变小"过滤，通常一轮就能收敛到个位数。

用法：
    python tools/hpfind.py scan --eq 1.0
    # 回游戏让 Sam 受一次伤，然后：
    python tools/hpfind.py filter --mode decreased
    python tools/hpfind.py show --limit 20

    # 反复 filter 直到候选很少；再用 hp_probe --watch 逐个确认
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import struct
import sys
import time
from array import array

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bridge.reader import (
    PROCESS_QUERY_INFORMATION,
    PROCESS_VM_READ,
    GameReader,
    ReaderError,
    find_process_id,
    k32,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAND = os.path.join(ROOT, "out", "hp_cand.bin")

MEM_COMMIT = 0x1000
MEM_MAPPED = 0x40000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01
WRITABLE = (0x04, 0x08, 0x40, 0x80)  # RW / WRITECOPY / EXECUTE_RW / EXECUTE_WC
CHUNK = 8 * 1024 * 1024


class MEMORY_BASIC_INFORMATION64(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_ulonglong),
        ("AllocationBase", ctypes.c_ulonglong),
        ("AllocationProtect", wt.DWORD),
        ("__alignment1", wt.DWORD),
        ("RegionSize", ctypes.c_ulonglong),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
        ("__alignment2", wt.DWORD),
    ]


def iter_regions(handle: int):
    addr = 0
    mbi = MEMORY_BASIC_INFORMATION64()
    size = ctypes.sizeof(mbi)
    max_addr = 0x7FFFFFFF0000
    while addr < max_addr:
        got = k32.VirtualQueryEx(
            handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.c_size_t(size)
        )
        if not got:
            break
        base = int(mbi.BaseAddress)
        length = int(mbi.RegionSize)
        if length <= 0:
            break
        if (
            mbi.State == MEM_COMMIT
            and mbi.Type != MEM_MAPPED
            and (mbi.Protect & 0xFF) in WRITABLE
            and not (mbi.Protect & PAGE_GUARD)
        ):
            yield base, length
        addr = base + length


def read_span(handle: int, addr: int, length: int) -> bytes:
    buf = ctypes.create_string_buffer(length)
    read = ctypes.c_size_t(0)
    ok = k32.ReadProcessMemory(
        handle, ctypes.c_void_p(addr), buf, length, ctypes.byref(read)
    )
    if not ok:
        return b""
    return buf.raw[: read.value]


def do_scan(handle: int, pattern: bytes, alignment: int, limit_hits: int) -> array:
    hits = array("Q")
    total = 0
    t0 = time.time()
    for base, length in iter_regions(handle):
        offset = 0
        while offset < length:
            n = min(CHUNK, length - offset)
            data = read_span(handle, base + offset, n)
            if not data:
                break
            pos = 0
            while True:
                pos = data.find(pattern, pos)
                if pos < 0:
                    break
                if (offset + pos) % alignment == 0:
                    hits.append(base + offset + pos)
                    if limit_hits and len(hits) >= limit_hits:
                        print(f"\n达到上限 {limit_hits} 个候选，提前结束")
                        return hits
                pos += 1
            offset += n
        total += length
        if len(hits) % 200000 < 2000:
            print(
                f"\r  已扫描 {total/1024/1024:7.0f} MB  候选 {len(hits):>9}  "
                f"{time.time()-t0:5.1f}s",
                end="",
                flush=True,
            )
    print()
    return hits


def load_candidates(path: str) -> array:
    arr = array("Q")
    with open(path, "rb") as fh:
        arr.fromfile(fh, os.path.getsize(path) // 8)
    return arr


def save_candidates(path: str, arr: array) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        arr.tofile(fh)


def filter_candidates(handle: int, addresses: array, mode: str, eps: float, base: float) -> array:
    """按页读取，避免逐地址 ReadProcessMemory。"""
    keep = array("Q")
    ordered = sorted(addresses)
    if not ordered:
        return keep

    i = 0
    total = len(ordered)
    while i < total:
        span_start = ordered[i]
        span_end = span_start + 4
        j = i + 1
        while j < total and ordered[j] - span_end < CHUNK:
            span_end = ordered[j] + 4
            j += 1

        length = span_end - span_start
        data = read_span(handle, span_start, length)
        if data:
            for k in range(i, j):
                addr = ordered[k]
                off = addr - span_start
                if off + 4 > len(data):
                    continue
                value = struct.unpack_from("<f", data, off)[0]
                if mode == "decreased" and value < base - eps and value > 0.0:
                    keep.append(addr)
                elif mode == "increased" and value > base + eps:
                    keep.append(addr)
                elif mode == "changed" and abs(value - base) > eps:
                    keep.append(addr)
                elif mode == "unchanged" and abs(value - base) <= eps:
                    keep.append(addr)
        i = j
    return keep


def show_values(handle: int, addresses: array, limit: int) -> None:
    for addr in addresses[:limit]:
        data = read_span(handle, addr, 4)
        if len(data) == 4:
            print(f"  0x{addr:012X}  {struct.unpack('<f', data)[0]:.6f}")


def main() -> int:
    parser = argparse.ArgumentParser(description="精确浮点值内存扫描")
    parser.add_argument("--process", default="ds.exe")
    parser.add_argument("--file", default=CAND)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", help="扫描等于某个值的地址")
    p_scan.add_argument("--eq", type=float, default=1.0, help="按 float 匹配")
    p_scan.add_argument("--eq-int", type=int, default=None, help="按 int32 匹配（给整数刻度用）")
    p_scan.add_argument("--align", type=int, default=4)
    p_scan.add_argument("--limit", type=int, default=8_000_000)

    p_filter = sub.add_parser("filter", help="按变化方向过滤候选")
    p_filter.add_argument("--mode", choices=["decreased", "increased", "changed", "unchanged"],
                          default="decreased")
    p_filter.add_argument("--base", type=float, default=None,
                          help="扫描时的基准值，默认取 --eq 或 1.0")
    p_filter.add_argument("--eps", type=float, default=1e-4)

    p_show = sub.add_parser("show", help="打印候选及当前值")
    p_show.add_argument("--limit", type=int, default=20)

    args = parser.parse_args()

    pid = find_process_id(args.process)
    if not pid:
        print(f"找不到进程 {args.process}")
        return 1
    handle = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        print("打开进程失败")
        return 1

    try:
        if args.cmd == "scan":
            if args.eq_int is not None:
                pattern = struct.pack("<i", args.eq_int)
                label = f"int32 == {args.eq_int}"
            else:
                pattern = struct.pack("<f", args.eq)
                label = f"float == {args.eq}"
            print(f"扫描 {label}（模式 {pattern.hex()}），进程 pid={pid}")
            hits = do_scan(handle, pattern, args.align, args.limit)
            save_candidates(args.file, hits)
            print(f"候选 {len(hits)} 个 → {args.file}")
            return 0

        if args.cmd == "filter":
            if not os.path.exists(args.file):
                print("没有候选文件，先跑 scan")
                return 1
            arr = load_candidates(args.file)
            base = args.base if args.base is not None else 1.0
            print(f"载入候选 {len(arr)} 个，按 {args.mode}（基准 {base}）过滤…")
            keep = filter_candidates(handle, arr, args.mode, args.eps, base)
            save_candidates(args.file, keep)
            print(f"剩余 {len(keep)} 个")
            show_values(handle, keep, 10)
            return 0

        if args.cmd == "show":
            if not os.path.exists(args.file):
                print("没有候选文件")
                return 1
            show_values(handle, load_candidates(args.file), args.limit)
            return 0

    finally:
        k32.CloseHandle(handle)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
