"""Local-only authenticated Windows companion for window/UI operations."""

from __future__ import annotations

import argparse
import base64
import ctypes
import hmac
import json
import logging
import os
import re
import struct
import subprocess
import sys
import threading
import time
import unicodedata
import zlib
from ctypes import wintypes
from difflib import SequenceMatcher
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

try:
    from .observations import ObservationRejected, ObservationStore
except ImportError:  # Installed bridge is executed as a standalone script.
    from observations import ObservationRejected, ObservationStore


if sys.platform != "win32":
    raise RuntimeError("O servidor do Nexo Windows Bridge deve rodar no Windows")


LOG = logging.getLogger("nexo.windows_bridge")
USER32 = ctypes.windll.user32
KERNEL32 = ctypes.windll.kernel32
SHELL32 = ctypes.windll.shell32
POWRPROF = ctypes.windll.PowrProf
GDI32 = ctypes.windll.gdi32

# Explicit 64-bit-safe signatures.  ctypes otherwise assumes ``c_int`` and
# can truncate HWND/HANDLE/pointer values on modern Windows.
USER32.GetForegroundWindow.restype = wintypes.HWND
USER32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
USER32.GetWindowTextLengthW.restype = ctypes.c_int
USER32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
USER32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
USER32.GetWindowThreadProcessId.restype = wintypes.DWORD
USER32.GetClipboardData.argtypes = [wintypes.UINT]
USER32.GetClipboardData.restype = wintypes.HANDLE
USER32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
USER32.SetClipboardData.restype = wintypes.HANDLE
KERNEL32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
KERNEL32.OpenProcess.restype = wintypes.HANDLE
KERNEL32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
KERNEL32.GlobalAlloc.restype = wintypes.HGLOBAL
KERNEL32.GlobalLock.argtypes = [wintypes.HGLOBAL]
KERNEL32.GlobalLock.restype = ctypes.c_void_p
KERNEL32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
KERNEL32.GlobalFree.argtypes = [wintypes.HGLOBAL]

SW_MINIMIZE = 6
SW_MAXIMIZE = 3
SW_RESTORE = 9
WM_CLOSE = 0x0010
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
VK_CONTROL = 0x11
VK_LWIN = 0x5B
VK_MENU = 0x12
VK_TAB = 0x09
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_PLAY_PAUSE = 0xB3
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
HALFTONE = 4
DIB_RGB_COLORS = 0
BI_RGB = 0
FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long), ("top", ctypes.c_long),
        ("right", ctypes.c_long), ("bottom", ctypes.c_long),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


ULONG_PTR = wintypes.WPARAM


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", wintypes.DWORD), ("value", INPUT_UNION)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class RGBQUAD(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", ctypes.c_ubyte),
        ("rgbGreen", ctypes.c_ubyte),
        ("rgbRed", ctypes.c_ubyte),
        ("rgbReserved", ctypes.c_ubyte),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", RGBQUAD * 1)]


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", wintypes.WORD),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


SHELL32.SHFileOperationW.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
SHELL32.SHFileOperationW.restype = ctypes.c_int
USER32.LockWorkStation.argtypes = []
USER32.LockWorkStation.restype = wintypes.BOOL
POWRPROF.SetSuspendState.argtypes = [wintypes.BOOLEAN, wintypes.BOOLEAN, wintypes.BOOLEAN]
POWRPROF.SetSuspendState.restype = wintypes.BOOLEAN
USER32.GetDC.argtypes = [wintypes.HWND]
USER32.GetDC.restype = wintypes.HDC
USER32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
USER32.ReleaseDC.restype = ctypes.c_int
USER32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
USER32.GetCursorPos.restype = wintypes.BOOL
USER32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
USER32.SetCursorPos.restype = wintypes.BOOL
USER32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
USER32.SendInput.restype = wintypes.UINT
USER32.VkKeyScanW.argtypes = [wintypes.WCHAR]
USER32.VkKeyScanW.restype = ctypes.c_short
USER32.GetSystemMetrics.argtypes = [ctypes.c_int]
USER32.GetSystemMetrics.restype = ctypes.c_int
GDI32.CreateCompatibleDC.argtypes = [wintypes.HDC]
GDI32.CreateCompatibleDC.restype = wintypes.HDC
GDI32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
GDI32.CreateCompatibleBitmap.restype = wintypes.HANDLE
GDI32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
GDI32.SelectObject.restype = wintypes.HANDLE
GDI32.DeleteObject.argtypes = [wintypes.HANDLE]
GDI32.DeleteObject.restype = wintypes.BOOL
GDI32.DeleteDC.argtypes = [wintypes.HDC]
GDI32.DeleteDC.restype = wintypes.BOOL
GDI32.SetStretchBltMode.argtypes = [wintypes.HDC, ctypes.c_int]
GDI32.SetStretchBltMode.restype = ctypes.c_int
GDI32.StretchBlt.argtypes = [
    wintypes.HDC,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HDC,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.DWORD,
]
GDI32.StretchBlt.restype = wintypes.BOOL
GDI32.GetDIBits.argtypes = [
    wintypes.HDC,
    wintypes.HANDLE,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.c_void_p,
    ctypes.POINTER(BITMAPINFO),
    wintypes.UINT,
]
GDI32.GetDIBits.restype = ctypes.c_int

try:
    # Per-monitor-v2 awareness keeps screenshots and input coordinates aligned.
    USER32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except (AttributeError, OSError):
    try:
        USER32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


OBSERVATIONS = ObservationStore(ttl_seconds=30, max_entries=32)


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value))
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"[^a-zA-Z0-9]+", " ", value.casefold())
    return " ".join(value.split())


def _process_path(pid: int) -> str:
    handle = KERNEL32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if KERNEL32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return ""
    finally:
        KERNEL32.CloseHandle(handle)


def _window_info(hwnd: int) -> dict[str, Any] | None:
    if not USER32.IsWindowVisible(hwnd):
        return None
    length = USER32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return None
    title = ctypes.create_unicode_buffer(length + 1)
    USER32.GetWindowTextW(hwnd, title, length + 1)
    if not title.value.strip():
        return None
    pid = wintypes.DWORD()
    USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process_path = _process_path(pid.value)
    rect = RECT()
    USER32.GetWindowRect(hwnd, ctypes.byref(rect))
    return {
        "handle": int(hwnd),
        "title": title.value,
        "pid": int(pid.value),
        "process": Path(process_path).stem if process_path else "",
        "process_path": process_path,
        "minimized": bool(USER32.IsIconic(hwnd)),
        "maximized": bool(USER32.IsZoomed(hwnd)),
        "bounds": {
            "left": rect.left, "top": rect.top,
            "width": max(0, rect.right - rect.left),
            "height": max(0, rect.bottom - rect.top),
        },
    }


def list_windows() -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        info = _window_info(hwnd)
        if info:
            windows.append(info)
        return True

    USER32.EnumWindows(callback_type(callback), 0)
    foreground = int(USER32.GetForegroundWindow())
    for item in windows:
        item["active"] = item["handle"] == foreground
    return windows


def active_window() -> dict[str, Any]:
    hwnd = int(USER32.GetForegroundWindow())
    info = _window_info(hwnd)
    if not info:
        return {"available": False}
    info["active"] = True
    return info


def _virtual_screen_bounds() -> dict[str, int]:
    bounds = {
        "left": USER32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        "top": USER32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        "width": USER32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        "height": USER32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    }
    if bounds["width"] <= 0 or bounds["height"] <= 0:
        raise RuntimeError("O Windows nao retornou uma area de tela valida")
    return bounds


def _intersect_bounds(
    first: dict[str, int],
    second: dict[str, int],
) -> dict[str, int]:
    left = max(first["left"], second["left"])
    top = max(first["top"], second["top"])
    right = min(
        first["left"] + first["width"],
        second["left"] + second["width"],
    )
    bottom = min(
        first["top"] + first["height"],
        second["top"] + second["height"],
    )
    if right <= left or bottom <= top:
        raise RuntimeError("A janela ativa nao possui uma regiao visivel para captura")
    return {
        "left": left,
        "top": top,
        "width": right - left,
        "height": bottom - top,
    }


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _encode_png_bgra(pixels: bytes, width: int, height: int) -> bytes:
    """Encode top-down 32-bit BGRA pixels as a compact RGB PNG."""
    stride = width * 4
    if len(pixels) != stride * height:
        raise ValueError("Buffer de imagem possui tamanho inesperado")
    scanlines = bytearray()
    for row_index in range(height):
        scanlines.append(0)  # PNG filter: None
        row = pixels[row_index * stride:(row_index + 1) * stride]
        for index in range(0, len(row), 4):
            blue, green, red = row[index:index + 3]
            scanlines.extend((red, green, blue))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(scanlines), level=6))
        + _png_chunk(b"IEND", b"")
    )


def _capture_region(
    bounds: dict[str, int],
    *,
    max_width: int,
    max_height: int,
) -> tuple[bytes, int, int]:
    source_width = int(bounds["width"])
    source_height = int(bounds["height"])
    maximum_width = max(320, min(int(max_width), 1920))
    maximum_height = max(240, min(int(max_height), 1080))
    scale = min(
        1.0,
        maximum_width / source_width,
        maximum_height / source_height,
    )
    width = max(1, round(source_width * scale))
    height = max(1, round(source_height * scale))

    screen_dc = USER32.GetDC(None)
    if not screen_dc:
        raise RuntimeError("Nao foi possivel acessar a tela do Windows")
    memory_dc = GDI32.CreateCompatibleDC(screen_dc)
    bitmap = None
    previous = None
    try:
        if not memory_dc:
            raise RuntimeError("Nao foi possivel criar o contexto da captura")
        bitmap = GDI32.CreateCompatibleBitmap(screen_dc, width, height)
        if not bitmap:
            raise RuntimeError("Nao foi possivel criar a imagem da captura")
        previous = GDI32.SelectObject(memory_dc, bitmap)
        GDI32.SetStretchBltMode(memory_dc, HALFTONE)
        copied = GDI32.StretchBlt(
            memory_dc,
            0,
            0,
            width,
            height,
            screen_dc,
            bounds["left"],
            bounds["top"],
            source_width,
            source_height,
            SRCCOPY | CAPTUREBLT,
        )
        if not copied:
            raise RuntimeError("O Windows recusou a captura da tela")

        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height  # Top-down, matching screenshot coordinates.
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB
        buffer = ctypes.create_string_buffer(width * height * 4)
        rows = GDI32.GetDIBits(
            memory_dc,
            bitmap,
            0,
            height,
            buffer,
            ctypes.byref(info),
            DIB_RGB_COLORS,
        )
        if rows != height:
            raise RuntimeError("A captura retornou dados incompletos")
        return _encode_png_bgra(buffer.raw, width, height), width, height
    finally:
        if previous and memory_dc:
            GDI32.SelectObject(memory_dc, previous)
        if bitmap:
            GDI32.DeleteObject(bitmap)
        if memory_dc:
            GDI32.DeleteDC(memory_dc)
        USER32.ReleaseDC(None, screen_dc)


def observe_desktop(
    scope: str = "active_window",
    max_width: int = 1280,
    max_height: int = 720,
) -> dict[str, Any]:
    if scope not in {"active_window", "desktop"}:
        raise ValueError("Escopo de observacao invalido")
    window = active_window()
    if window.get("available") is False:
        raise RuntimeError("Nenhuma janela ativa disponivel para observacao")
    if window.get("minimized"):
        raise RuntimeError("A janela ativa esta minimizada")
    window_bounds = dict(window["bounds"])
    virtual_bounds = _virtual_screen_bounds()
    capture_bounds = (
        _intersect_bounds(window_bounds, virtual_bounds)
        if scope == "active_window"
        else virtual_bounds
    )
    png, image_width, image_height = _capture_region(
        capture_bounds,
        max_width=max_width,
        max_height=max_height,
    )
    observation = OBSERVATIONS.create(
        hwnd=window["handle"],
        window_bounds=window_bounds,
        capture_bounds=capture_bounds,
        image_width=image_width,
        image_height=image_height,
        scope=scope,
    )
    return {
        "ok": True,
        **observation,
        "mime_type": "image/png",
        "image_base64": base64.b64encode(png).decode("ascii"),
        "image_bytes": len(png),
        "coordinate_space": {"width": image_width, "height": image_height},
        "window": window,
        "privacy": {
            "ephemeral": True,
            "logged": False,
            "may_contain_sensitive_content": True,
        },
    }


def _cursor_position() -> dict[str, int]:
    point = POINT()
    if not USER32.GetCursorPos(ctypes.byref(point)):
        raise RuntimeError("Nao foi possivel consultar a posicao do cursor")
    return {"x": int(point.x), "y": int(point.y)}


def _action_state() -> dict[str, Any]:
    window = active_window()
    return {
        "window": {
            key: value
            for key, value in window.items()
            if key in {
                "available", "handle", "title", "pid", "process", "minimized",
                "maximized", "bounds", "active",
            }
        },
        "cursor": _cursor_position(),
    }


def _validate_visual_target(observation_id: str) -> dict[str, Any]:
    hwnd = int(USER32.GetForegroundWindow())
    window = _window_info(hwnd)
    if not window:
        raise ObservationRejected(
            "missing_foreground",
            "Nenhuma janela ativa esta disponivel para receber a acao",
        )
    return OBSERVATIONS.validate(
        observation_id,
        current_hwnd=hwnd,
        current_bounds=window["bounds"],
        consume=True,
    )


def _observation_point(
    observation: dict[str, Any],
    x: Any,
    y: Any,
) -> tuple[int, int]:
    if isinstance(x, bool) or isinstance(y, bool):
        raise ValueError("Coordenadas precisam ser numeros inteiros")
    try:
        image_x = int(x)
        image_y = int(y)
    except (TypeError, ValueError) as exc:
        raise ValueError("A acao exige coordenadas x e y") from exc
    image_width = int(observation["image_width"])
    image_height = int(observation["image_height"])
    if not (0 <= image_x < image_width and 0 <= image_y < image_height):
        raise ValueError("Coordenadas fora da imagem observada")
    bounds = observation["capture_bounds"]
    if image_width <= 1:
        screen_x = int(bounds["left"])
    else:
        screen_x = int(bounds["left"]) + round(
            image_x * (int(bounds["width"]) - 1) / (image_width - 1)
        )
    if image_height <= 1:
        screen_y = int(bounds["top"])
    else:
        screen_y = int(bounds["top"]) + round(
            image_y * (int(bounds["height"]) - 1) / (image_height - 1)
        )
    return screen_x, screen_y


def _mouse_input(flags: int, data: int = 0) -> INPUT:
    item = INPUT()
    item.type = INPUT_MOUSE
    item.mi = MOUSEINPUT(0, 0, data & 0xFFFFFFFF, flags, 0, 0)
    return item


def _keyboard_input(
    virtual_key: int = 0,
    *,
    scan_code: int = 0,
    key_up: bool = False,
    unicode_input: bool = False,
) -> INPUT:
    flags = KEYEVENTF_KEYUP if key_up else 0
    if unicode_input:
        flags |= KEYEVENTF_UNICODE
    item = INPUT()
    item.type = INPUT_KEYBOARD
    item.ki = KEYBDINPUT(
        int(virtual_key),
        int(scan_code),
        flags,
        0,
        0,
    )
    return item


def _send_inputs(items: list[INPUT]) -> int:
    if not items:
        return 0
    sent = 0
    # Avoid oversized SendInput calls for long dictated text.
    for start in range(0, len(items), 512):
        batch = items[start:start + 512]
        array = (INPUT * len(batch))(*batch)
        delivered = int(USER32.SendInput(len(batch), array, ctypes.sizeof(INPUT)))
        sent += delivered
        if delivered != len(batch):
            break
    return sent


KEY_NAMES = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "pause": 0x13,
    "capslock": 0x14,
    "escape": 0x1B,
    "esc": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "insert": 0x2D,
    "delete": 0x2E,
    "win": 0x5B,
    "windows": 0x5B,
    "menu": 0x5D,
}
KEY_NAMES.update({f"f{number}": 0x6F + number for number in range(1, 25)})


def _virtual_key(name: str) -> int:
    raw = str(name or "").strip()
    normalized = _normalize(raw).replace(" ", "")
    if normalized in KEY_NAMES:
        return KEY_NAMES[normalized]
    if len(raw) == 1:
        mapped = int(USER32.VkKeyScanW(raw))
        if mapped != -1:
            return mapped & 0xFF
    raise ValueError(f"Tecla nao suportada: {raw}")


def _unicode_inputs(text: str) -> list[INPUT]:
    encoded = str(text).encode("utf-16-le")
    units = [
        int.from_bytes(encoded[index:index + 2], "little")
        for index in range(0, len(encoded), 2)
    ]
    result: list[INPUT] = []
    for unit in units:
        result.append(_keyboard_input(scan_code=unit, unicode_input=True))
        result.append(
            _keyboard_input(scan_code=unit, unicode_input=True, key_up=True)
        )
    return result


def computer_action(
    action: str,
    observation_id: str = "",
    x: int | None = None,
    y: int | None = None,
    button: str = "left",
    delta: int = 0,
    key: str = "",
    keys: list[str] | None = None,
    text: str = "",
) -> dict[str, Any]:
    allowed = {
        "move", "click", "double_click", "scroll", "key", "hotkey",
        "type_text", "play_pause", "next", "previous",
    }
    if action not in allowed:
        raise ValueError("Acao de computador invalida")
    media_action = action in {"play_pause", "next", "previous"}
    observation = None if media_action else _validate_visual_target(observation_id)
    before = _action_state()
    inputs: list[INPUT] = []
    target_point: tuple[int, int] | None = None
    characters = None

    if action in {"move", "click", "double_click"}:
        target_point = _observation_point(observation, x, y)
        if not USER32.SetCursorPos(*target_point):
            raise RuntimeError("O Windows recusou o movimento do cursor")
        if action != "move":
            buttons = {
                "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
                "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
                "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
            }
            if button not in buttons:
                raise ValueError("Botao do mouse invalido")
            down, up = buttons[button]
            clicks = 2 if action == "double_click" else 1
            for _click_index in range(clicks):
                inputs.extend((_mouse_input(down), _mouse_input(up)))
    elif action == "scroll":
        if (x is None) != (y is None):
            raise ValueError("Informe x e y juntos para posicionar a rolagem")
        if x is not None:
            target_point = _observation_point(observation, x, y)
            if not USER32.SetCursorPos(*target_point):
                raise RuntimeError("O Windows recusou o movimento do cursor")
        wheel_delta = int(delta)
        if wheel_delta == 0 or not -2400 <= wheel_delta <= 2400:
            raise ValueError("delta da rolagem deve estar entre -2400 e 2400 e nao pode ser zero")
        inputs.append(_mouse_input(MOUSEEVENTF_WHEEL, wheel_delta))
    elif action == "key":
        virtual_key = _virtual_key(key)
        inputs.extend((
            _keyboard_input(virtual_key),
            _keyboard_input(virtual_key, key_up=True),
        ))
    elif action == "hotkey":
        requested = list(keys or [])
        if not 2 <= len(requested) <= 5:
            raise ValueError("hotkey exige de duas a cinco teclas")
        virtual_keys = [_virtual_key(item) for item in requested]
        inputs.extend(_keyboard_input(item) for item in virtual_keys)
        inputs.extend(
            _keyboard_input(item, key_up=True)
            for item in reversed(virtual_keys)
        )
    elif action == "type_text":
        payload = str(text)
        if not payload or len(payload) > 10_000:
            raise ValueError("Texto vazio ou acima do limite de 10 mil caracteres")
        if any(ord(char) < 32 for char in payload):
            raise ValueError(
                "type_text nao aceita teclas de controle; use key em uma nova observacao"
            )
        characters = len(payload)
        inputs = _unicode_inputs(payload)
    else:
        media_keys = {
            "play_pause": VK_MEDIA_PLAY_PAUSE,
            "next": VK_MEDIA_NEXT_TRACK,
            "previous": VK_MEDIA_PREV_TRACK,
        }
        virtual_key = media_keys[action]
        inputs.extend((
            _keyboard_input(virtual_key),
            _keyboard_input(virtual_key, key_up=True),
        ))

    expected = len(inputs)
    sent = _send_inputs(inputs)
    time.sleep(0.08)
    after = _action_state()
    delivery_verified = sent == expected
    if action == "move" and target_point is not None:
        cursor = after["cursor"]
        delivery_verified = delivery_verified and (
            abs(cursor["x"] - target_point[0]) <= 1
            and abs(cursor["y"] - target_point[1]) <= 1
        )
    if action == "type_text":
        delivery_verified = delivery_verified and (
            after["window"].get("handle") == before["window"].get("handle")
        )

    result = {
        "ok": bool(delivery_verified),
        "action": action,
        "verified": bool(delivery_verified),
        "verification_scope": (
            "cursor_position" if action == "move" else "input_delivery"
        ),
        "effect_verified": False,
        "evidence": {
            "before": before,
            "after": after,
            "input": {"expected": expected, "sent": sent},
        },
        "integrity_boundary": "same_or_lower_integrity_only",
    }
    if observation:
        result["observation_id"] = observation["observation_id"]
    if target_point is not None:
        result["evidence"]["target"] = {
            "screen_x": target_point[0],
            "screen_y": target_point[1],
        }
    if characters is not None:
        result["characters"] = characters
    if not delivery_verified:
        result["reason"] = "input_not_fully_delivered"
    return result


def _select_window(query: str | None) -> dict[str, Any]:
    if not query:
        current = active_window()
        if current.get("available") is False:
            raise LookupError("Nenhuma janela ativa encontrada")
        return current
    normalized = _normalize(query)
    ranked = []
    for item in list_windows():
        title = _normalize(item["title"])
        process = _normalize(item["process"])
        if normalized in {title, process}:
            score = 100.0
        elif normalized in title or normalized in process:
            score = 92.0
        else:
            score = max(
                SequenceMatcher(None, normalized, title).ratio() * 100,
                SequenceMatcher(None, normalized, process).ratio() * 100,
            )
        ranked.append((score, item))
    ranked.sort(key=lambda candidate: candidate[0], reverse=True)
    if not ranked or ranked[0][0] < 55:
        raise LookupError(f"Janela não encontrada para '{query}'")
    result = dict(ranked[0][1])
    result["match_score"] = round(ranked[0][0], 1)
    return result


def _focus_handle(hwnd: int) -> bool:
    USER32.ShowWindow(hwnd, SW_RESTORE)
    foreground = USER32.GetForegroundWindow()
    current_thread = KERNEL32.GetCurrentThreadId()
    target_thread = USER32.GetWindowThreadProcessId(hwnd, None)
    foreground_thread = USER32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    attached_target = bool(target_thread and target_thread != current_thread and USER32.AttachThreadInput(current_thread, target_thread, True))
    attached_foreground = bool(foreground_thread and foreground_thread != current_thread and foreground_thread != target_thread and USER32.AttachThreadInput(current_thread, foreground_thread, True))
    try:
        USER32.BringWindowToTop(hwnd)
        return bool(USER32.SetForegroundWindow(hwnd))
    finally:
        if attached_foreground:
            USER32.AttachThreadInput(current_thread, foreground_thread, False)
        if attached_target:
            USER32.AttachThreadInput(current_thread, target_thread, False)


def focus_window(query: str) -> dict[str, Any]:
    window = _select_window(query)
    focused = _focus_handle(window["handle"])
    window["focused"] = focused or int(USER32.GetForegroundWindow()) == window["handle"]
    return window


def window_action(query: str | None, action: str) -> dict[str, Any]:
    if action not in {"focus", "minimize", "maximize", "restore", "close"}:
        raise ValueError("Ação de janela inválida")
    window = _select_window(query)
    hwnd = window["handle"]
    if action == "focus":
        dispatched = _focus_handle(hwnd)
    elif action == "minimize":
        USER32.ShowWindow(hwnd, SW_MINIMIZE)
        dispatched = True
    elif action == "maximize":
        USER32.ShowWindow(hwnd, SW_MAXIMIZE)
        _focus_handle(hwnd)
        dispatched = True
    elif action == "restore":
        USER32.ShowWindow(hwnd, SW_RESTORE)
        _focus_handle(hwnd)
        dispatched = True
    else:
        dispatched = bool(USER32.PostMessageW(hwnd, WM_CLOSE, 0, 0))

    deadline = time.monotonic() + (1.5 if action == "close" else 0.6)
    verified = False
    while time.monotonic() < deadline:
        exists = bool(USER32.IsWindow(hwnd))
        if action == "close":
            verified = not exists
        elif exists and action == "focus":
            verified = int(USER32.GetForegroundWindow()) == hwnd
        elif exists and action == "minimize":
            verified = bool(USER32.IsIconic(hwnd))
        elif exists and action == "maximize":
            verified = bool(USER32.IsZoomed(hwnd))
        elif exists and action == "restore":
            verified = (
                not bool(USER32.IsIconic(hwnd))
                and int(USER32.GetForegroundWindow()) == hwnd
            )
        if verified:
            break
        time.sleep(0.05)

    exists_after = bool(USER32.IsWindow(hwnd))
    after = _window_info(hwnd) if exists_after else {"exists": False, "handle": hwnd}
    ok = bool(dispatched and verified)
    return {
        "ok": ok,
        "verified": bool(verified),
        "verification_scope": "window_state",
        "action": action,
        "requested": query,
        "window": window,
        "dispatched": bool(dispatched),
        "evidence": {"before": window, "after": after},
        **({} if ok else {"reason": "window_state_not_confirmed"}),
    }


def show_desktop() -> dict[str, Any]:
    USER32.keybd_event(VK_LWIN, 0, 0, 0)
    USER32.keybd_event(ord("D"), 0, 0, 0)
    USER32.keybd_event(ord("D"), 0, KEYEVENTF_KEYUP, 0)
    USER32.keybd_event(VK_LWIN, 0, KEYEVENTF_KEYUP, 0)
    return {"ok": True}


def switch_window() -> dict[str, Any]:
    """Switch once through the native Windows task order (Alt+Tab)."""
    before = active_window()
    USER32.keybd_event(VK_MENU, 0, 0, 0)
    USER32.keybd_event(VK_TAB, 0, 0, 0)
    USER32.keybd_event(VK_TAB, 0, KEYEVENTF_KEYUP, 0)
    USER32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.5)
    after = active_window()
    verified = (
        before.get("handle") is not None
        and after.get("handle") is not None
        and before.get("handle") != after.get("handle")
    )
    return {
        "ok": bool(verified),
        "verified": bool(verified),
        "verification_scope": "foreground_window",
        "before": before,
        "after": after,
        **({} if verified else {"reason": "foreground_window_did_not_change"}),
    }


def _read_clipboard_text() -> str | None:
    if not USER32.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return None
    handle = USER32.GetClipboardData(CF_UNICODETEXT)
    if not handle:
        return None
    pointer = KERNEL32.GlobalLock(handle)
    if not pointer:
        return None
    try:
        return ctypes.wstring_at(pointer)
    finally:
        KERNEL32.GlobalUnlock(handle)


def _set_clipboard_text(text: str) -> None:
    payload = (str(text) + "\0").encode("utf-16-le")
    handle = KERNEL32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
    if not handle:
        raise RuntimeError("Não foi possível alocar a área de transferência")
    pointer = KERNEL32.GlobalLock(handle)
    try:
        ctypes.memmove(pointer, payload, len(payload))
    finally:
        KERNEL32.GlobalUnlock(handle)
    if not USER32.SetClipboardData(CF_UNICODETEXT, handle):
        KERNEL32.GlobalFree(handle)
        raise RuntimeError("Não foi possível escrever na área de transferência")


def _with_clipboard(callback: Callable[[str | None], Any]) -> Any:
    for _attempt in range(10):
        if USER32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("Área de transferência está ocupada")
    try:
        previous = _read_clipboard_text()
        USER32.EmptyClipboard()
        return callback(previous)
    finally:
        USER32.CloseClipboard()


def type_text(text: str, window: str | None = None) -> dict[str, Any]:
    text = str(text)
    if not text or len(text) > 100_000:
        raise ValueError("Texto vazio ou acima do limite de 100 mil caracteres")
    target = None
    if window:
        target = focus_window(window)
        time.sleep(0.2)

    previous_holder: list[str | None] = []

    def write(previous):
        previous_holder.append(previous)
        _set_clipboard_text(text)

    _with_clipboard(write)
    USER32.keybd_event(VK_CONTROL, 0, 0, 0)
    USER32.keybd_event(ord("V"), 0, 0, 0)
    USER32.keybd_event(ord("V"), 0, KEYEVENTF_KEYUP, 0)
    USER32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.25)

    previous = previous_holder[0]
    if previous is not None:
        def restore(_ignored):
            _set_clipboard_text(previous)
        _with_clipboard(restore)
    return {
        "ok": True,
        "characters": len(text),
        "window": target,
        "clipboard_restored": previous is not None,
    }


def recycle_path(path: str) -> dict[str, Any]:
    target_path = Path(path).resolve()
    if not target_path.exists():
        raise FileNotFoundError(str(target_path))
    profile = Path.home().resolve()
    if target_path == profile or profile not in target_path.parents:
        raise PermissionError("A Lixeira do Nexo aceita apenas itens dentro do perfil atual")
    protected = {
        profile / "Desktop", profile / "Documents", profile / "Downloads",
        profile / "Pictures", profile / "Music", profile / "Videos",
    }
    if target_path in protected:
        raise PermissionError("Essa pasta raiz do usuário não pode ser removida")
    target = str(target_path)
    # SHFileOperation requires a double-NUL terminated path list.
    source = target + "\0\0"
    operation = SHFILEOPSTRUCTW()
    operation.wFunc = FO_DELETE
    operation.pFrom = source
    operation.fFlags = FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI
    result = SHELL32.SHFileOperationW(ctypes.byref(operation))
    if result != 0 or operation.fAnyOperationsAborted:
        raise RuntimeError(f"Falha enviando para a Lixeira: código {result}")
    return {"ok": True, "path": target, "recycled": True}


def lock_workstation() -> dict[str, Any]:
    if not USER32.LockWorkStation():
        raise RuntimeError("O Windows recusou o bloqueio da estação")
    return {"ok": True, "action": "lock"}


def power_action(action: str) -> dict[str, Any]:
    if action == "sleep":
        if not POWRPROF.SetSuspendState(False, False, False):
            raise RuntimeError("O Windows recusou a suspensão")
    elif action in {"restart", "shutdown"}:
        flag = "/r" if action == "restart" else "/s"
        subprocess.Popen(
            [str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "shutdown.exe"), flag, "/t", "0"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    else:
        raise ValueError("Ação de energia inválida")
    return {"ok": True, "action": action, "dispatched": True}


METHODS: dict[str, Callable[..., Any]] = {
    "health": lambda: {
        "status": "ok", "version": "1.1", "pid": os.getpid(),
        "session": os.environ.get("SESSIONNAME", ""),
        "capabilities": {
            "visual_observation": True,
            "guarded_input": True,
            "media_keys": True,
            "elevated_ui": False,
        },
    },
    "list_windows": list_windows,
    "active_window": active_window,
    "observe_desktop": observe_desktop,
    "computer_action": computer_action,
    "focus_window": focus_window,
    "window_action": window_action,
    "show_desktop": show_desktop,
    "switch_window": switch_window,
    "type_text": type_text,
    "recycle_path": recycle_path,
    "lock_workstation": lock_workstation,
    "power_action": power_action,
}


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "NexoBridge/1.0"

    def log_message(self, fmt, *args):
        LOG.debug("HTTP %s", fmt % args)

    def _reply(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        self._reply(404, {"ok": False, "error": {"message": "not found"}})

    def do_POST(self):
        if self.path != "/rpc":
            self._reply(404, {"ok": False, "error": {"message": "not found"}})
            return
        authorization = self.headers.get("Authorization", "")
        expected = "Bearer " + self.server.bridge_token
        if not hmac.compare_digest(authorization, expected):
            self._reply(401, {"ok": False, "error": {"message": "unauthorized"}})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 65536:
            self._reply(400, {"ok": False, "error": {"message": "invalid body"}})
            return
        try:
            request = json.loads(self.rfile.read(length).decode("utf-8"))
            method = request.get("method")
            params = request.get("params") or {}
            if method not in METHODS or not isinstance(params, dict):
                raise ValueError("Método ou parâmetros inválidos")
            LOG.info("RPC method=%s parameter_keys=%s", method, sorted(params))
            result = METHODS[method](**params)
            self._reply(200, {"ok": True, "result": result})
        except Exception as exc:
            LOG.exception("RPC failed")
            self._reply(
                400,
                {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}},
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--token-file",
        default=str(Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Nexo" / "bridge-token"),
    )
    parser.add_argument("--log-file")
    parser.add_argument("--pid-file")
    args = parser.parse_args()
    token = Path(args.token_file).read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise RuntimeError("Token do bridge ausente ou inválido")
    handlers = [logging.StreamHandler()]
    if args.log_file:
        Path(args.log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(args.log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )
    server = ThreadingHTTPServer((args.host, args.port), BridgeHandler)
    server.bridge_token = token
    if args.pid_file:
        Path(args.pid_file).write_text(str(os.getpid()), encoding="ascii")
    LOG.info("Nexo Windows Bridge listening host=%s port=%s pid=%s", args.host, args.port, os.getpid())
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if args.pid_file:
            try:
                Path(args.pid_file).unlink()
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
