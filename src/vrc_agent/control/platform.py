"""Windows 人工控制边界。命名 Event 只能停止，不能授予控制权限。"""

import ctypes
import getpass
import hashlib
import os
from pathlib import Path


def object_name(state_dir: Path, suffix: str) -> str:
    scope = f"{getpass.getuser()}:{str(state_dir.resolve()).casefold()}"
    digest = hashlib.sha256(scope.encode()).hexdigest()[:24]
    return f"Local\\vrc-agent-{digest}-{suffix}"


def kernel32():
    from ctypes import wintypes as w

    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    dll.CreateEventW.argtypes = [ctypes.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR]
    dll.CreateEventW.restype = w.HANDLE
    dll.OpenEventW.argtypes = [w.DWORD, w.BOOL, w.LPCWSTR]
    dll.OpenEventW.restype = w.HANDLE
    dll.SetEvent.argtypes = [w.HANDLE]
    dll.SetEvent.restype = w.BOOL
    dll.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    dll.WaitForSingleObject.restype = w.DWORD
    dll.CloseHandle.argtypes = [w.HANDLE]
    dll.CloseHandle.restype = w.BOOL
    dll.CreateMutexW.argtypes = [ctypes.c_void_p, w.BOOL, w.LPCWSTR]
    dll.CreateMutexW.restype = w.HANDLE
    return dll


class InstanceLock:
    """文件锁随进程终止释放；live 额外按目标端口阻止跨状态目录重复控制。"""

    def __init__(self, state_dir: Path, live_port: int | None = None):
        state_dir.mkdir(parents=True, exist_ok=True)
        self.stream = (state_dir / "controller.lock").open("a+b")
        self.mutex = None
        self.dll = None
        try:
            self.stream.seek(0, 2)
            if self.stream.tell() == 0:
                self.stream.write(b"0")
                self.stream.flush()
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if live_port is not None and os.name == "nt":
                self.dll = kernel32()
                name = f"Local\\vrc-agent-osc-{live_port}"
                self.mutex = self.dll.CreateMutexW(None, False, name)
                if not self.mutex or ctypes.get_last_error() == 183:
                    raise OSError("another live controller owns this OSC destination")
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self.mutex:
            self.dll.CloseHandle(self.mutex)
            self.mutex = None
        self.stream.close()


class StopSignal:
    def __init__(self, state_dir: Path):
        self.path = state_dir / "stop.request"
        self.dll = None
        self.handle = None
        if os.name == "nt":
            self.dll = kernel32()
            self.handle = self.dll.CreateEventW(None, False, False, object_name(state_dir, "stop"))
            if not self.handle:
                raise ctypes.WinError(ctypes.get_last_error())
        else:
            self.path.unlink(missing_ok=True)

    def triggered(self) -> bool:
        if self.dll:
            return self.dll.WaitForSingleObject(self.handle, 0) == 0
        if self.path.exists():
            self.path.unlink(missing_ok=True)
            return True
        return False

    def close(self) -> None:
        if self.handle:
            self.dll.CloseHandle(self.handle)


def signal_stop(state_dir: Path) -> None:
    if os.name != "nt":
        if not state_dir.is_dir():
            raise OSError("controller state directory does not exist")
        (state_dir / "stop.request").touch()
        return
    dll = kernel32()
    handle = dll.OpenEventW(0x0002, False, object_name(state_dir, "stop"))
    if not handle:
        raise OSError("no accessible running controller for this state directory")
    try:
        if not dll.SetEvent(handle):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        dll.CloseHandle(handle)


class WindowsControls:
    def __init__(self, title: str, enable_probe: bool):
        if os.name != "nt":
            raise OSError("live mode requires Windows")
        from ctypes import wintypes as w

        self.w = w
        self.dll = ctypes.WinDLL("user32", use_last_error=True)
        self.dll.GetForegroundWindow.restype = w.HWND
        self.dll.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        self.dll.IsWindowVisible.argtypes = [w.HWND]
        self.dll.IsIconic.argtypes = [w.HWND]
        self.dll.PeekMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT]
        self.dll.RegisterHotKey.argtypes = [w.HWND, ctypes.c_int, w.UINT, w.UINT]
        self.dll.UnregisterHotKey.argtypes = [w.HWND, ctypes.c_int]
        self.title = title
        self.bound_hwnd = None
        self.keys = {1: "estop", 2: "arm", 3: "reset", 14: "extend"}
        self.registered: list[int] = []
        if enable_probe:
            # T00 人工探针：F7 前进 / Ctrl+F11 后退 / F6 右转 / Ctrl+F12 左转 / F5 左移 /
            # F4 右移（短动作）；F3 交互 / F1 抓取 / F11 丢下 / F2 聊天框。
            # 说明：单键 F12 在本机被其他软件占用（实测），转向与后退键改用 Ctrl 组合。
            self.keys[4] = "probe_forward"
            self.keys[5] = "probe_yaw"
            self.keys[6] = "probe_strafe_left"
            self.keys[7] = "probe_strafe_right"
            self.keys[8] = "probe_use"
            self.keys[9] = "probe_chat"
            self.keys[10] = "probe_grab"
            self.keys[11] = "probe_drop"
            self.keys[12] = "probe_yaw_left"
            self.keys[13] = "probe_backward"
        try:
            for ident, key, modifiers in (
                (1, 0x77, 0x4000),
                (2, 0x78, 0x4000),
                (3, 0x79, 0x4000),
                (14, 0x78, 0x4002),  # Ctrl+F9 / MOD_NOREPEAT：仅本机人工续租。
                (4, 0x76, 0x4000),
                (5, 0x75, 0x4000),
                (6, 0x74, 0x4000),
                (7, 0x73, 0x4000),
                (8, 0x72, 0x4000),
                (9, 0x71, 0x4000),
                (10, 0x70, 0x4000),
                (11, 0x7A, 0x4000),
                (12, 0x7B, 0x4002),  # Ctrl+F12
                (13, 0x7A, 0x4002),  # Ctrl+F11
            ):
                if ident in self.keys:
                    if not self.dll.RegisterHotKey(None, ident, modifiers, key):
                        raise OSError("required global hotkey is already in use")
                    self.registered.append(ident)
        except BaseException:
            self.close()
            raise

    def focus_ok(self, bind: bool = False) -> bool:
        hwnd = self.dll.GetForegroundWindow()
        text = ctypes.create_unicode_buffer(512)
        self.dll.GetWindowTextW(hwnd, text, len(text))
        valid = bool(
            hwnd
            and self.dll.IsWindowVisible(hwnd)
            and not self.dll.IsIconic(hwnd)
            and text.value == self.title
        )
        if valid and bind:
            self.bound_hwnd = hwnd
        return valid and (bind or hwnd == self.bound_hwnd)

    def events(self) -> list[str]:
        events = []
        message = self.w.MSG()
        for _ in range(16):
            if not self.dll.PeekMessageW(ctypes.byref(message), None, 0x0312, 0x0312, 1):
                break
            if message.wParam in self.keys:
                events.append(self.keys[message.wParam])
        return events

    def close(self) -> None:
        for ident in self.registered:
            self.dll.UnregisterHotKey(None, ident)
        self.registered.clear()
