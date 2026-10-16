"""Win32/GDI 采集后端（仅 Windows）。

四条本机实测结论决定了这里的实现，见 docs/validation/T04_capture.md：

1. **BitBlt 读的是屏幕表面**。目标窗口被别的窗口挡住时它会静默返回遮挡窗口的
   像素——实测抓到的「VRChat 帧」其实是 VS Code 画面。所以遮挡采样是必需项。
2. **窗口 DC 对 DWM 重定向的窗口会返回一整幅全零画面**（BitBlt 与 GetDIBits 都
   报成功）。实测 VS Code（Chrome_WidgetWin_1）走 `GetDC(hwnd)` 得到 2160x1368
   的纯零缓冲，被黑帧检测判成黑屏；同一个矩形走屏幕 DC 就拿到真实内容。因此抓取
   统一走屏幕 DC + 客户区原点，并且只在「已确认未被遮挡」时使用它。
3. **PrintWindow 对 VRChat 的 UnityWndClass 窗口返回 ERROR_ACCESS_DENIED**，
   没有「抓被遮挡窗口自身内容」的可用路径。因此被遮挡时只能如实报告失败。
4. **进程必须是 per-monitor DPI 感知**，否则 2160x1440 的全屏窗口会被虚拟化成
   1440x960，抓到的帧是被系统缩放过的错误分辨率画面。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from vrc_agent.capture.backend import (
    AWARE_LEVELS,
    DpiAwareness,
    OcclusionReport,
    WindowInfo,
    WindowMatch,
    matches,
)
from vrc_agent.capture.errors import CaptureError

# ---- 基本类型（显式声明，避免 64 位下句柄被截断成 int）------------------------

HWND = ctypes.c_void_p
HDC = ctypes.c_void_p
HBITMAP = ctypes.c_void_p
HGDIOBJ = ctypes.c_void_p

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0
GA_ROOT = 2
#: GetSystemMetrics 索引：虚拟屏幕（多显示器时的并集）边界。
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
MAX_TITLE_CHARS = 512
MAX_CLASS_CHARS = 256

DPI_AWARENESS_CONTEXT_UNAWARE = -1
DPI_AWARENESS_CONTEXT_SYSTEM_AWARE = -2
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE = -3
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4

_CONTEXT_NAMES: tuple[tuple[int, DpiAwareness], ...] = (
    (DPI_AWARENESS_CONTEXT_UNAWARE, "unaware"),
    (DPI_AWARENESS_CONTEXT_SYSTEM_AWARE, "system"),
    (DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE, "per_monitor"),
    (DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2, "per_monitor_v2"),
)

if sys.platform == "win32":
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, HWND, ctypes.c_void_p)
else:  # 非 Windows 上可以导入本模块，但任何真调用都会先被 _require_windows() 挡住。
    WNDENUMPROC = None


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


# ---- DLL 绑定 ----------------------------------------------------------------

# 缓存变量名必须与访问器函数名（_user32/_gdi32/_shcore_dll）不同：同名会让 def 覆盖
# 掉缓存哨兵，访问器于是返回函数对象本身，真正的 DLL 永远不会被初始化。
_USER32_DLL: ctypes.WinDLL | None = None
_GDI32_DLL: ctypes.WinDLL | None = None
_SHCORE_DLL: ctypes.WinDLL | None = None
_SHCORE_READY = False


def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("Win32 capture is only available on Windows")


def _bind(dll: ctypes.WinDLL, name: str, restype: object, argtypes: list[object]) -> None:
    function = getattr(dll, name, None)
    if function is None:
        return
    function.restype = restype
    function.argtypes = argtypes


def _user32() -> ctypes.WinDLL:
    global _USER32_DLL
    _require_windows()
    if _USER32_DLL is None:
        dll = ctypes.WinDLL("user32", use_last_error=True)
        _bind(dll, "GetDC", HDC, [HWND])
        _bind(dll, "ReleaseDC", ctypes.c_int, [HWND, HDC])
        _bind(dll, "GetWindowRect", wintypes.BOOL, [HWND, ctypes.POINTER(RECT)])
        _bind(dll, "GetClientRect", wintypes.BOOL, [HWND, ctypes.POINTER(RECT)])
        _bind(dll, "ClientToScreen", wintypes.BOOL, [HWND, ctypes.POINTER(POINT)])
        _bind(dll, "WindowFromPoint", HWND, [POINT])
        _bind(dll, "GetAncestor", HWND, [HWND, wintypes.UINT])
        _bind(dll, "IsWindow", wintypes.BOOL, [HWND])
        _bind(dll, "IsWindowVisible", wintypes.BOOL, [HWND])
        _bind(dll, "IsIconic", wintypes.BOOL, [HWND])
        _bind(dll, "GetForegroundWindow", HWND, [])
        _bind(
            dll, "GetWindowThreadProcessId", wintypes.DWORD, [HWND, ctypes.POINTER(wintypes.DWORD)]
        )
        _bind(dll, "GetWindowTextW", ctypes.c_int, [HWND, wintypes.LPWSTR, ctypes.c_int])
        _bind(dll, "GetClassNameW", ctypes.c_int, [HWND, wintypes.LPWSTR, ctypes.c_int])
        _bind(dll, "GetDpiForWindow", wintypes.UINT, [HWND])
        _bind(dll, "EnumWindows", wintypes.BOOL, [WNDENUMPROC, wintypes.LPARAM])
        _bind(dll, "GetSystemMetrics", ctypes.c_int, [ctypes.c_int])
        _bind(dll, "GetThreadDpiAwarenessContext", ctypes.c_void_p, [])
        _bind(
            dll,
            "AreDpiAwarenessContextsEqual",
            wintypes.BOOL,
            [ctypes.c_void_p, ctypes.c_void_p],
        )
        _bind(dll, "SetProcessDpiAwarenessContext", wintypes.BOOL, [ctypes.c_void_p])
        _USER32_DLL = dll
    return _USER32_DLL


def _gdi32() -> ctypes.WinDLL:
    global _GDI32_DLL
    _require_windows()
    if _GDI32_DLL is None:
        dll = ctypes.WinDLL("gdi32", use_last_error=True)
        _bind(dll, "CreateCompatibleDC", HDC, [HDC])
        _bind(dll, "DeleteDC", wintypes.BOOL, [HDC])
        _bind(dll, "CreateCompatibleBitmap", HBITMAP, [HDC, ctypes.c_int, ctypes.c_int])
        _bind(dll, "SelectObject", HGDIOBJ, [HDC, HGDIOBJ])
        _bind(dll, "DeleteObject", wintypes.BOOL, [HGDIOBJ])
        _bind(
            dll,
            "BitBlt",
            wintypes.BOOL,
            [
                HDC,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                HDC,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.DWORD,
            ],
        )
        _bind(
            dll,
            "GetDIBits",
            ctypes.c_int,
            [
                HDC,
                HBITMAP,
                wintypes.UINT,
                wintypes.UINT,
                ctypes.c_void_p,
                ctypes.POINTER(BITMAPINFO),
                wintypes.UINT,
            ],
        )
        _GDI32_DLL = dll
    return _GDI32_DLL


def _shcore_dll() -> ctypes.WinDLL | None:
    global _SHCORE_DLL, _SHCORE_READY
    _require_windows()
    if not _SHCORE_READY:
        _SHCORE_READY = True
        try:
            dll = ctypes.WinDLL("shcore", use_last_error=True)
            _bind(dll, "SetProcessDpiAwareness", ctypes.c_long, [ctypes.c_int])
            _SHCORE_DLL = dll
        except OSError:
            _SHCORE_DLL = None
    return _SHCORE_DLL


# ---- DPI 感知 ----------------------------------------------------------------


def awareness() -> DpiAwareness:
    """只读查询：当前进程的 DPI 感知级别。"""
    if sys.platform != "win32":
        return "unavailable"
    user32 = _user32()
    if not hasattr(user32, "GetThreadDpiAwarenessContext"):
        return "unknown"
    context = user32.GetThreadDpiAwarenessContext()
    if not context:
        return "unknown"
    for value, name in _CONTEXT_NAMES:
        if user32.AreDpiAwarenessContextsEqual(context, ctypes.c_void_p(value)):
            return name
    return "unknown"


def ensure_dpi_awareness() -> DpiAwareness:
    """把进程提升到 per-monitor-v2 DPI 感知，并返回最终级别。

    必须在创建任何窗口/DC 之前调用；一旦进程的 DPI 感知被固定（例如由宿主进程
    或清单决定），这里无法再改，只能如实返回当前级别。
    """
    if sys.platform != "win32":
        return "unavailable"
    current = awareness()
    if current in AWARE_LEVELS:
        return current

    user32 = _user32()
    if hasattr(user32, "SetProcessDpiAwarenessContext"):
        if user32.SetProcessDpiAwarenessContext(
            ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        ):
            return awareness()
        if user32.SetProcessDpiAwarenessContext(
            ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE)
        ):
            return awareness()

    shcore = _shcore_dll()
    if shcore is not None and shcore.SetProcessDpiAwareness(2) == 0:  # PER_MONITOR
        return awareness()

    return awareness()


# ---- 窗口查询工具 ------------------------------------------------------------


def window_title(hwnd: int) -> str:
    user32 = _user32()
    buffer = ctypes.create_unicode_buffer(MAX_TITLE_CHARS)
    user32.GetWindowTextW(HWND(hwnd), buffer, MAX_TITLE_CHARS)
    return buffer.value


def window_class(hwnd: int) -> str:
    user32 = _user32()
    buffer = ctypes.create_unicode_buffer(MAX_CLASS_CHARS)
    user32.GetClassNameW(HWND(hwnd), buffer, MAX_CLASS_CHARS)
    return buffer.value


def window_pid(hwnd: int) -> int:
    user32 = _user32()
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(HWND(hwnd), ctypes.byref(pid))
    return int(pid.value)


def window_dpi(hwnd: int) -> int:
    user32 = _user32()
    if not hasattr(user32, "GetDpiForWindow"):
        return 96
    dpi = int(user32.GetDpiForWindow(HWND(hwnd)) or 0)
    return dpi if dpi > 0 else 96


def _virtual_screen() -> tuple[int, int, int, int]:
    """虚拟屏幕边界 (x_min, y_min, x_max, y_max)，多显示器时是并集。"""
    user32 = _user32()
    if not hasattr(user32, "GetSystemMetrics"):
        return (0, 0, 0, 0)
    x_min = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
    y_min = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
    width = int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
    height = int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
    if width <= 0 or height <= 0:
        return (0, 0, 0, 0)
    return (x_min, y_min, x_min + width, y_min + height)


def enumerate_windows(max_items: int = 25) -> tuple[dict[str, object], ...]:
    """列出可见的顶层窗口，用于「没找到目标窗口」时的诊断输出。"""
    user32 = _user32()
    found: list[dict[str, object]] = []

    def callback(hwnd: int, _param: int) -> bool:
        if len(found) >= max_items:
            return False
        if not user32.IsWindowVisible(HWND(hwnd)) or user32.IsIconic(HWND(hwnd)):
            return True
        title = window_title(hwnd)
        if not title:
            return True
        rect = RECT()
        user32.GetWindowRect(HWND(hwnd), ctypes.byref(rect))
        found.append(
            {
                "hwnd": int(hwnd),
                "title": title,
                "class_name": window_class(hwnd),
                "pid": window_pid(hwnd),
                "size": [rect.right - rect.left, rect.bottom - rect.top],
            }
        )
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return tuple(found)


# ---- 后端实现 ----------------------------------------------------------------


class Win32CaptureBackend:
    """真实窗口采集。只读：不注入、不改变目标窗口状态。"""

    name = "win32"

    def awareness(self) -> DpiAwareness:
        return awareness()

    def find_window(self, title: str, class_name: str | None, match: WindowMatch) -> int | None:
        user32 = _user32()
        candidates: list[int] = []

        def callback(hwnd: int, _param: int) -> bool:
            if class_name is not None and window_class(hwnd) != class_name:
                return True
            if matches(window_title(hwnd), title, match):
                candidates.append(int(hwnd))
            return True

        user32.EnumWindows(WNDENUMPROC(callback), 0)
        if not candidates:
            return None

        def rank(hwnd: int) -> int:
            if not user32.IsWindowVisible(HWND(hwnd)):
                return 2
            return 1 if user32.IsIconic(HWND(hwnd)) else 0

        # 优先「可见且未最小化」的那个；全都不可用时也要返回一个句柄，
        # 这样状态检测能如实报告 minimized/hidden，而不是含糊的 not found。
        return min(candidates, key=rank)

    def window_info(self, hwnd: int) -> WindowInfo:
        user32 = _user32()
        if not user32.IsWindow(HWND(hwnd)):
            raise CaptureError("WINDOW_NOT_FOUND", f"hwnd {hwnd} is gone")

        rect = RECT()
        client = RECT()
        origin = POINT(0, 0)
        user32.GetWindowRect(HWND(hwnd), ctypes.byref(rect))
        user32.GetClientRect(HWND(hwnd), ctypes.byref(client))
        user32.ClientToScreen(HWND(hwnd), ctypes.byref(origin))

        return WindowInfo(
            hwnd=hwnd,
            title=window_title(hwnd),
            class_name=window_class(hwnd),
            pid=window_pid(hwnd),
            rect=(rect.left, rect.top, rect.right, rect.bottom),
            client_origin=(origin.x, origin.y),
            client_size=(client.right, client.bottom),
            dpi=window_dpi(hwnd),
            visible=bool(user32.IsWindowVisible(HWND(hwnd))),
            iconic=bool(user32.IsIconic(HWND(hwnd))),
            foreground=int(user32.GetForegroundWindow() or 0) == hwnd,
        )

    def occlusion(self, info: WindowInfo, samples: int) -> OcclusionReport:
        user32 = _user32()
        left, top, right, bottom = info.client_rect
        width = right - left
        height = bottom - top
        count = max(1, samples)
        target = info.hwnd
        matching = 0
        total = 0

        for iy in range(count):
            y = top + (height * (2 * iy + 1)) // (2 * count)
            for ix in range(count):
                x = left + (width * (2 * ix + 1)) // (2 * count)
                total += 1
                at_point = user32.WindowFromPoint(POINT(x, y))
                if not at_point:
                    continue
                root = user32.GetAncestor(HWND(at_point), GA_ROOT)
                if int(root or 0) == target:
                    matching += 1

        return OcclusionReport(samples=total, matching=matching)

    def grab_bgra(self, info: WindowInfo) -> bytes:
        """抓取客户区画面（BGRA，自上而下，stride = width*4）。

        **必须从屏幕 DC 抓，不能从窗口 DC 抓**：对 DWM 重定向的窗口（Electron/
        Chromium，实测 VS Code），`GetDC(hwnd)` + BitBlt 会「成功」返回一整幅全零
        画面，被黑帧检测当成黑屏。屏幕 DC 在客户区原点取同样的矩形，拿到的是屏幕
        上真实呈现的内容——只有在「已确认目标窗口未被遮挡」时它才等于窗口内容，
        所以这里先做虚拟屏幕范围检查，遮挡检查由状态机在调用前完成。
        """
        width, height = info.width, info.height
        left, top = info.client_origin
        user32 = _user32()
        gdi32 = _gdi32()

        x_min, y_min, x_max, y_max = _virtual_screen()
        if left < x_min or top < y_min or left + width > x_max or top + height > y_max:
            raise CaptureError(
                "CAPTURE_FAILED",
                f"client rect ({left},{top},{left + width},{top + height}) is not fully "
                f"inside the virtual screen ({x_min},{y_min},{x_max},{y_max})",
            )

        source = user32.GetDC(None)
        if not source:
            raise CaptureError("CAPTURE_FAILED", "GetDC(NULL) failed")
        try:
            memory = gdi32.CreateCompatibleDC(source)
            if not memory:
                raise CaptureError("CAPTURE_FAILED", "CreateCompatibleDC failed")
            try:
                bitmap = gdi32.CreateCompatibleBitmap(source, width, height)
                if not bitmap:
                    raise CaptureError("CAPTURE_FAILED", "CreateCompatibleBitmap failed")
                try:
                    previous = gdi32.SelectObject(memory, bitmap)
                    try:
                        ok = gdi32.BitBlt(memory, 0, 0, width, height, source, left, top, SRCCOPY)
                        if not ok:
                            raise CaptureError(
                                "CAPTURE_FAILED", f"BitBlt failed (error {ctypes.get_last_error()})"
                            )
                        buffer = ctypes.create_string_buffer(width * height * 4)
                        header = BITMAPINFO()
                        header.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                        header.bmiHeader.biWidth = width
                        header.bmiHeader.biHeight = -height  # 负值 = 行序自上而下
                        header.bmiHeader.biPlanes = 1
                        header.bmiHeader.biBitCount = 32
                        header.bmiHeader.biCompression = BI_RGB
                        scan_lines = gdi32.GetDIBits(
                            memory,
                            bitmap,
                            0,
                            height,
                            ctypes.cast(buffer, ctypes.c_void_p),
                            ctypes.byref(header),
                            DIB_RGB_COLORS,
                        )
                        if scan_lines != height:
                            raise CaptureError(
                                "CAPTURE_FAILED",
                                f"GetDIBits copied {scan_lines}/{height} scan lines",
                            )
                        return bytes(buffer.raw)
                    finally:
                        gdi32.SelectObject(memory, previous)
                finally:
                    gdi32.DeleteObject(bitmap)
            finally:
                gdi32.DeleteDC(memory)
        finally:
            user32.ReleaseDC(None, source)
