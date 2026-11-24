"""盯住一批候选地址（来自 hpfind 的候选文件），按 64KB 块批量读取。

用途：候选太多（上千个）时不逐个 ReadProcessMemory，而是把地址分组成块，
每块只读一次，再在块内比对；变化时打印地址、旧值、新值。

    python tools/watch_file.py out/hp_int.bin --seconds 180 --hz 5
"""

from __future__ import annotations

import argparse
import ctypes
import os
import struct
import sys
import time
from array import array

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bridge.reader import (
    PROCESS_QUERY_INFORMATION,
    PROCESS_VM_READ,
    find_process_id,
    k32,
)

BLOCK = 64 * 1024


def read_span(handle: int, addr: int, length: int) -> bytes:
    buf = ctypes.create_string_buffer(length)
    read = ctypes.c_size_t(0)
    ok = k32.ReadProcessMemory(
        handle, ctypes.c_void_p(addr), buf, length, ctypes.byref(read)
    )
    if not ok:
        return b""
    return buf.raw[: read.value]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    parser.add_argument("--process", default="ds.exe")
    parser.add_argument("--seconds", type=float, default=180)
    parser.add_argument("--hz", type=float, default=5)
    parser.add_argument("--block", type=int, default=BLOCK)
    args = parser.parse_args()

    BLK = args.block

    addrs = array("Q")
    addrs.fromfile(open(args.file, "rb"), os.path.getsize(args.file) // 8)
    addrs = sorted(set(addrs))
    print(f"候选 {len(addrs)} 个", flush=True)

    blocks: dict[int, list[int]] = {}
    for a in addrs:
        blocks.setdefault(a // BLK * BLK, []).append(a)
    print(f"分块 {len(blocks)} 个", flush=True)

    pid = find_process_id(args.process)
    if not pid:
        print("找不到进程")
        return 1
    handle = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        print("打开进程失败")
        return 1

    prev: dict[int, int] = {}
    for start, lst in blocks.items():
        data = read_span(handle, start, BLK)
        for a in lst:
            off = a - start
            if off + 4 <= len(data):
                prev[a] = struct.unpack_from("<i", data, off)[0]

    print("开始监视，Ctrl+C 结束", flush=True)
    end = time.time() + args.seconds
    try:
        while time.time() < end:
            for start, lst in blocks.items():
                data = read_span(handle, start, BLK)
                if not data:
                    continue
                for a in lst:
                    off = a - start
                    if off + 4 > len(data):
                        continue
                    v = struct.unpack_from("<i", data, off)[0]
                    if a in prev and prev[a] != v:
                        print(
                            f"[{time.strftime('%H:%M:%S')}] 0x{a:012X}  "
                            f"{prev[a]} → {v}",
                            flush=True,
                        )
                        prev[a] = v
            time.sleep(1.0 / args.hz)
    except KeyboardInterrupt:
        pass
    finally:
        k32.CloseHandle(handle)
    print("结束", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
