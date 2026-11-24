"""盯住若干个地址，只打印发生变化的时刻。用于确认哪个地址才是真正的血量。

    python tools/watch_addr.py 0x0485A7261CA0 0x02B9717E6698 --seconds 120
"""

from __future__ import annotations

import argparse
import ctypes
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bridge.reader import (
    PROCESS_QUERY_INFORMATION,
    PROCESS_VM_READ,
    find_process_id,
    k32,
)


def read_int(handle: int, addr: int):
    buf = ctypes.create_string_buffer(4)
    read = ctypes.c_size_t(0)
    ok = k32.ReadProcessMemory(
        handle, ctypes.c_void_p(addr), buf, 4, ctypes.byref(read)
    )
    if not ok or read.value != 4:
        return None
    return struct.unpack("<i", buf.raw)[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("addresses", nargs="+")
    parser.add_argument("--process", default="ds.exe")
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--hz", type=float, default=10)
    args = parser.parse_args()

    addrs = [int(a, 0) for a in args.addresses]
    pid = find_process_id(args.process)
    if not pid:
        print("找不到进程")
        return 1
    handle = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        print("打开进程失败")
        return 1

    print(f"监视 {len(addrs)} 个地址，{args.seconds:.0f} 秒，Ctrl+C 结束", flush=True)
    prev = {a: read_int(handle, a) for a in addrs}
    for a, v in prev.items():
        print(f"  起始 0x{a:012X} = {v}", flush=True)

    end = time.time() + args.seconds
    try:
        while time.time() < end:
            for a in addrs:
                v = read_int(handle, a)
                if v is not None and v != prev[a]:
                    print(
                        f"[{time.strftime('%H:%M:%S')}] 0x{a:012X}  {prev[a]} → {v}",
                        flush=True,
                    )
                    prev[a] = v
            time.sleep(1.0 / args.hz)
    except KeyboardInterrupt:
        pass
    finally:
        k32.CloseHandle(handle)
    print("监视结束", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
