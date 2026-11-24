"""只读游戏内存：用 Win32 API 直接读，不申请写权限。

为什么不用 pymem：它默认以 PROCESS_ALL_ACCESS 打开进程，对 ds.exe 会 Access Denied；
我们只需要 PROCESS_VM_READ，用 ctypes 自己开句柄更稳，也符合"绝不写游戏内存"的约束。

指针链语义与 Cheat Engine 一致：
    [[base+module_offset] + off1 + off2 + ...]
即每处理一个 offset，先解引用再相加；最后一个 offset 加完就是数值地址。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import struct
from dataclasses import dataclass, field
from typing import Optional

k32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
MAX_PATH = 260


class ReaderError(RuntimeError):
    pass


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD),
        ("szExeFile", ctypes.c_wchar * MAX_PATH),
    ]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("th32ModuleID", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("GlblcntUsage", wt.DWORD),
        ("ProccntUsage", wt.DWORD),
        ("modBaseAddr", ctypes.c_void_p),
        ("modBaseSize", wt.DWORD),
        ("hModule", wt.HMODULE),
        ("szModule", ctypes.c_wchar * 256),
        ("szExePath", ctypes.c_wchar * MAX_PATH),
    ]


@dataclass
class PointerSpec:
    module_offset: int = 0
    offsets: list[int] = field(default_factory=list)

    @property
    def configured(self) -> bool:
        return bool(self.offsets)

    @classmethod
    def from_dict(cls, raw: dict | None) -> "PointerSpec":
        raw = raw or {}
        return cls(
            module_offset=int(str(raw.get("module_offset", "0")), 0),
            offsets=[int(str(x), 0) for x in raw.get("offsets", [])],
        )


@dataclass
class Profile:
    name: str
    process: str
    module: str
    health: PointerSpec
    health_max: float
    health_type: str = "float"
    signature: Optional[str] = None
    game_size: Optional[int] = None
    # 临时模式：直接给绝对地址（只在本次游戏会话有效，重启游戏后失效）
    health_address: Optional[int] = None

    @property
    def usable(self) -> bool:
        return self.health.configured or bool(self.health_address)

    @classmethod
    def load(cls, path: str) -> "Profile":
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        hp = raw.get("health", {})
        addr = hp.get("address")
        return cls(
            name=raw.get("name", os.path.basename(path)),
            process=raw.get("process", "ds.exe"),
            module=raw.get("module", "ds.exe"),
            health=PointerSpec.from_dict(hp.get("pointer")),
            health_max=float(hp.get("max", 1.0)),
            health_type=hp.get("type", "float"),
            signature=hp.get("signature") or raw.get("signature"),
            game_size=raw.get("size"),
            health_address=int(str(addr), 0) if addr else None,
        )


def find_process_id(name: str) -> Optional[int]:
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return None
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == name.lower():
                return int(entry.th32ProcessID)
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
        return None
    finally:
        k32.CloseHandle(snap)


class GameReader:
    def __init__(self, profile: Profile):
        self.profile = profile
        self.handle: Optional[int] = None
        self.pid: int = 0
        self.base: int = 0
        self.module_size: int = 0
        self._addr: int = 0

    # ------------------------------------------------------------------ 生命周期

    def attach(self) -> None:
        pid = find_process_id(self.profile.process)
        if not pid:
            raise ReaderError(f"找不到进程 {self.profile.process}（游戏没启动？）")

        handle = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
        if not handle:
            err = ctypes.get_last_error()
            raise ReaderError(
                f"打开 {self.profile.process}(pid={pid}) 失败，错误码 {err}"
                "（若游戏以管理员运行，本程序也需要管理员权限）"
            )

        self.handle = handle
        self.pid = pid

        module = self._find_module(self.profile.module)
        if module is None:
            self.close()
            raise ReaderError(f"进程里找不到模块 {self.profile.module}")
        self.base, self.module_size = module

    def _find_module(self, name: str) -> Optional[tuple[int, int]]:
        snap = k32.CreateToolhelp32Snapshot(
            TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, self.pid
        )
        if not snap or snap == INVALID_HANDLE_VALUE:
            return None
        try:
            entry = MODULEENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            ok = k32.Module32FirstW(snap, ctypes.byref(entry))
            while ok:
                if entry.szModule.lower() == name.lower():
                    return int(entry.modBaseAddr or 0), int(entry.modBaseSize)
                ok = k32.Module32NextW(snap, ctypes.byref(entry))
            return None
        finally:
            k32.CloseHandle(snap)

    @property
    def alive(self) -> bool:
        if not self.handle:
            return False
        code = wt.DWORD()
        if not k32.GetExitCodeProcess(self.handle, ctypes.byref(code)):
            return False
        return code.value == 259  # STILL_ACTIVE

    def close(self) -> None:
        if self.handle:
            k32.CloseHandle(self.handle)
            self.handle = None

    # ------------------------------------------------------------------ 读取

    def read_bytes(self, addr: int, size: int) -> bytes:
        if not self.handle:
            raise ReaderError("未附加进程")
        buf = ctypes.create_string_buffer(size)
        read = ctypes.c_size_t(0)
        ok = k32.ReadProcessMemory(
            self.handle, ctypes.c_void_p(addr), buf, size, ctypes.byref(read)
        )
        if not ok or read.value != size:
            err = ctypes.get_last_error()
            raise ReaderError(f"读取 0x{addr:X} 失败（{size} 字节，错误码 {err}）")
        return buf.raw[: read.value]

    def read_u64(self, addr: int) -> int:
        return struct.unpack("<Q", self.read_bytes(addr, 8))[0]

    def read_float(self, addr: int) -> float:
        return struct.unpack("<f", self.read_bytes(addr, 4))[0]

    def read_int32(self, addr: int) -> int:
        return struct.unpack("<i", self.read_bytes(addr, 4))[0]

    def resolve(self, spec: PointerSpec) -> int:
        addr = self.base + spec.module_offset
        for off in spec.offsets:
            addr = self.read_u64(addr) + off
        return addr

    def _value_at(self, addr: int) -> float:
        kind = (self.profile.health_type or "float").lower()
        if kind in ("int", "int32", "dword"):
            return float(self.read_int32(addr))
        return self.read_float(addr)

    def health(self) -> float:
        if self.profile.health_address:
            return self._value_at(self.profile.health_address)
        if not self.profile.health.configured:
            raise ReaderError("profile 里还没有配置血量地址")
        if not self._addr:
            self._addr = self.resolve(self.profile.health)
        return self._value_at(self._addr)

    def refresh_health_addr(self) -> int:
        if self.profile.health_address:
            self._addr = self.profile.health_address
            return self._addr
        self._addr = 0
        self._addr = self.resolve(self.profile.health)
        return self._addr

    # ------------------------------------------------------------------ 调试辅助

    def modules(self) -> list[tuple[str, int, int]]:
        snap = k32.CreateToolhelp32Snapshot(
            TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, self.pid
        )
        if not snap or snap == INVALID_HANDLE_VALUE:
            return []
        out: list[tuple[str, int, int]] = []
        try:
            entry = MODULEENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            ok = k32.Module32FirstW(snap, ctypes.byref(entry))
            while ok:
                out.append(
                    (entry.szModule, int(entry.modBaseAddr or 0), int(entry.modBaseSize))
                )
                ok = k32.Module32NextW(snap, ctypes.byref(entry))
        finally:
            k32.CloseHandle(snap)
        return out

    def scan_module(self, pattern: str) -> list[int]:
        """在模块内做 AOB 扫描，pattern 形如 "F3 0F 11 ?? ?? 48"。"""
        if not pattern:
            return []
        tokens = pattern.split()
        needle = bytearray()
        mask = bytearray()
        for tok in tokens:
            if tok in ("??", "?"):
                needle.append(0)
                mask.append(0)
            else:
                needle.append(int(tok, 16))
                mask.append(1)
        if not any(mask):
            return []

        size = self.profile.game_size or self.module_size
        if not size:
            raise ReaderError("无法确定模块大小，profile 里补一个 size 字段")

        data = bytearray()
        chunk = 8 * 1024 * 1024
        offset = 0
        while offset < size:
            n = min(chunk, size - offset)
            data += self.read_bytes(self.base + offset, n)
            offset += n

        hits: list[int] = []
        n = len(needle)
        anchor_idx = mask.index(1)
        anchor = needle[anchor_idx]
        start = 0
        while True:
            pos = data.find(bytes([anchor]), start)
            if pos < 0:
                break
            begin = pos - anchor_idx
            if begin >= 0 and begin + n <= len(data):
                ok = True
                for i in range(n):
                    if mask[i] and data[begin + i] != needle[i]:
                        ok = False
                        break
                if ok:
                    hits.append(self.base + begin)
            start = pos + 1
        return hits
