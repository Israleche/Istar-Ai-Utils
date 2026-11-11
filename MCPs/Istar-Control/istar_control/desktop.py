"""Targeted Windows desktop input. Input injection is subject to Windows UIPI.

Coordinates are relative to the target's client area. Foreground checks cannot
make global Windows input atomic with respect to an external focus change.
"""

from __future__ import annotations

import ctypes
import math
import sys
import time
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass


class DesktopError(RuntimeError):
    """A window operation failed or could not be safely targeted."""


@dataclass(frozen=True)
class Window:
    hwnd: int
    title: str


ULONG_PTR = ctypes.c_size_t


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", wintypes.DWORD), ("data", _INPUTUNION)]


_KEYS = {
    "LEFT": 0x25,
    "UP": 0x26,
    "RIGHT": 0x27,
    "DOWN": 0x28,
    "ENTER": 0x0D,
    "SPACE": 0x20,
    "ESC": 0x1B,
    "TAB": 0x09,
    "SHIFT": 0x10,
    "CTRL": 0x11,
    "ALT": 0x12,
    "BACKSPACE": 0x08,
    "HOME": 0x24,
    "END": 0x23,
    "PAGEUP": 0x21,
    "PAGEDOWN": 0x22,
    "INSERT": 0x2D,
    "DELETE": 0x2E,
}
_EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E}
_BUTTONS = {
    "left": (0x0002, 0x0004),
    "right": (0x0008, 0x0010),
    "middle": (0x0020, 0x0040),
}


def _duration(value: float) -> float:
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 2:
        raise ValueError("duration must be between 0 and 2 seconds")
    return value


class Desktop:
    """Windows desktop API; no input is sent during construction."""

    def __init__(self):
        if sys.platform != "win32":
            raise DesktopError("Windows is required")
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        u = self._user32
        self._callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )
        signatures = {
            "EnumWindows": ([self._callback_type, wintypes.LPARAM], wintypes.BOOL),
            "IsWindow": ([wintypes.HWND], wintypes.BOOL),
            "IsWindowVisible": ([wintypes.HWND], wintypes.BOOL),
            "IsIconic": ([wintypes.HWND], wintypes.BOOL),
            "GetWindowTextLengthW": ([wintypes.HWND], ctypes.c_int),
            "GetWindowTextW": (
                [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int],
                ctypes.c_int,
            ),
            "SetForegroundWindow": ([wintypes.HWND], wintypes.BOOL),
            "GetForegroundWindow": ([], wintypes.HWND),
            "GetWindowRect": (
                [wintypes.HWND, ctypes.POINTER(wintypes.RECT)],
                wintypes.BOOL,
            ),
            "GetClientRect": (
                [wintypes.HWND, ctypes.POINTER(wintypes.RECT)],
                wintypes.BOOL,
            ),
            "ClientToScreen": (
                [wintypes.HWND, ctypes.POINTER(wintypes.POINT)],
                wintypes.BOOL,
            ),
            "WindowFromPoint": ([wintypes.POINT], wintypes.HWND),
            "GetAncestor": ([wintypes.HWND, wintypes.UINT], wintypes.HWND),
            "GetSystemMetrics": ([ctypes.c_int], ctypes.c_int),
            "GetClipCursor": ([ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            "MapVirtualKeyW": ([wintypes.UINT, wintypes.UINT], wintypes.UINT),
            "SendInput": (
                [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int],
                wintypes.UINT,
            ),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(u, name)
            fn.argtypes, fn.restype = args, result
        try:
            self._set_thread_dpi = u.SetThreadDpiAwarenessContext
            self._set_thread_dpi.argtypes = [ctypes.c_void_p]
            self._set_thread_dpi.restype = ctypes.c_void_p
        except AttributeError:
            self._set_thread_dpi = None

    @contextmanager
    def _physical_pixels(self):
        """Use per-monitor-aware thread coordinates, restoring the prior context."""
        if self._set_thread_dpi is None:
            raise DesktopError("per-monitor DPI awareness is unavailable")
        previous = self._set_thread_dpi(ctypes.c_void_p(-4))
        if not previous:
            raise DesktopError("could not set per-monitor DPI awareness")
        try:
            yield
        finally:
            if not self._set_thread_dpi(previous):
                raise DesktopError("could not restore thread DPI awareness")

    def _valid(self, hwnd: int) -> None:
        if (
            not isinstance(hwnd, int)
            or isinstance(hwnd, bool)
            or hwnd <= 0
            or not self._user32.IsWindow(hwnd)
        ):
            raise DesktopError("invalid target window")
        if not self._user32.IsWindowVisible(hwnd) or self._user32.IsIconic(hwnd):
            raise DesktopError("target window must be visible and not minimized")

    def _foreground(self, hwnd: int) -> None:
        self._valid(hwnd)
        if self._user32.GetForegroundWindow() != hwnd:
            raise DesktopError("target window is not foreground")

    def list_windows(self) -> list[Window]:
        windows = []
        u = self._user32

        @self._callback_type
        def collect(hwnd, _):
            if u.IsWindowVisible(hwnd):
                n = u.GetWindowTextLengthW(hwnd)
                if n:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    u.GetWindowTextW(hwnd, buf, n + 1)
                    if buf.value:
                        windows.append(Window(int(hwnd), buf.value))
            return True

        if not u.EnumWindows(collect, 0):
            raise DesktopError("EnumWindows failed")
        return windows

    def window_title(self, hwnd: int) -> str | None:
        """Return the title of a window handle, or None when it has none."""
        if type(hwnd) is not int or hwnd <= 0:
            raise ValueError("hwnd must be a positive integer")
        u = self._user32
        if not u.IsWindow(hwnd):
            return None
        length = u.GetWindowTextLengthW(hwnd)
        if not length:
            return None
        buffer = ctypes.create_unicode_buffer(length + 1)
        u.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value or None

    def focus_window(self, hwnd: int) -> None:
        self._valid(hwnd)
        if self._user32.GetForegroundWindow() != hwnd:
            if not self._user32.SetForegroundWindow(hwnd):
                raise DesktopError("Windows refused to focus target window")
        self._foreground(hwnd)

    def screenshot(self, hwnd: int):
        """Return an in-memory screen crop, including any overlapping windows.

        Protected, accelerated or off-screen content may be blank or incomplete.
        This is not an isolated window capture and writes no image file.
        """
        with self._physical_pixels():
            return self._screenshot_physical(hwnd)

    def _screenshot_physical(self, hwnd: int):
        self._foreground(hwnd)
        rect = wintypes.RECT()
        if not self._user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise DesktopError("GetWindowRect failed")
        if rect.right <= rect.left or rect.bottom <= rect.top:
            raise DesktopError("window has an empty rectangle")
        try:
            from PIL import ImageGrab
        except ImportError as exc:
            raise DesktopError("Pillow is required for screenshots") from exc
        self._foreground(hwnd)
        try:
            return ImageGrab.grab(
                bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True
            )
        except OSError as exc:
            raise DesktopError("screenshot failed") from exc

    def _send(self, event: _INPUT) -> None:
        if self._user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_INPUT)) != 1:
            raise DesktopError("SendInput failed (possibly blocked by UIPI)")

    def _hold(self, hwnd: int, duration: float) -> None:
        # Stop holding promptly when foreground changes; key-up is still sent
        # by the caller even when the target has lost focus.
        remaining = duration
        while remaining > 0:
            interval = min(remaining, 0.05)
            time.sleep(interval)
            remaining -= interval
            self._foreground(hwnd)

    def press_key(self, hwnd: int, key: str, duration: float = 0.05) -> None:
        duration = _duration(duration)
        if not isinstance(key, str):
            raise ValueError("key must be a supported key name")
        name = key.upper()
        vk = _KEYS.get(name)
        if (
            vk is None
            and len(name) == 1
            and name in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        ):
            vk = ord(name)
        if vk is None:
            raise ValueError("unsupported key")
        self._foreground(hwnd)
        scan = self._user32.MapVirtualKeyW(vk, 0)
        if not scan or scan > 0xFF:
            raise DesktopError("could not map virtual key to scan code")
        flags = 0x0008 | (0x0001 if vk in _EXTENDED else 0)
        down = _INPUT(type=1, ki=_KEYBDINPUT(0, scan, flags, 0, 0))
        up = _INPUT(type=1, ki=_KEYBDINPUT(0, scan, flags | 0x0002, 0, 0))
        self._foreground(hwnd)
        self._send(down)
        try:
            self._hold(hwnd, duration)
        finally:
            self._send(up)

    def press_keys(self, hwnd: int, keys: list[str], duration: float = 0.05) -> None:
        """Hold 1–4 distinct physical keys for at most two seconds.

        Windows keys are unsupported; Alt+Tab, Alt+Esc, Ctrl+Esc and
        Ctrl+Alt+Delete are rejected. This is not a comprehensive shortcut
        blocklist. Releases are attempted in reverse even after focus loss or
        input errors, but Windows may reject them (for example due to UIPI).
        """
        duration = _duration(duration)
        if not isinstance(keys, (list, tuple)) or not 1 <= len(keys) <= 4:
            raise ValueError("keys must contain 1 to 4 key names")
        vks = []
        for key in keys:
            if not isinstance(key, str):
                raise ValueError("key must be a supported key name")
            name = key.upper()
            vk = _KEYS.get(name)
            if (
                vk is None
                and len(name) == 1
                and name in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
            ):
                vk = ord(name)
            if vk is None:
                raise ValueError("unsupported key")
            vks.append(vk)
        if len(set(vks)) != len(vks):
            raise ValueError("duplicate keys are not allowed")
        combo = set(vks)
        blocked = ({0x12, 0x09}, {0x12, 0x1B}, {0x11, 0x1B}, {0x11, 0x12, 0x2E})
        if any(shortcut <= combo for shortcut in blocked):
            raise ValueError("sensitive Windows shortcut is not allowed")
        self._foreground(hwnd)
        events = []
        for vk in vks:
            scan = self._user32.MapVirtualKeyW(vk, 0)
            if not scan or scan > 0xFF:
                raise DesktopError("could not map virtual key to scan code")
            flags = 0x0008 | (0x0001 if vk in _EXTENDED else 0)
            events.append(
                (
                    _INPUT(type=1, ki=_KEYBDINPUT(0, scan, flags, 0, 0)),
                    _INPUT(type=1, ki=_KEYBDINPUT(0, scan, flags | 0x0002, 0, 0)),
                )
            )
        releases = []
        try:
            for down, up in events:
                self._foreground(hwnd)
                # Failed injection may have ambiguous effects; attempt release.
                releases.append(up)
                self._send(down)
            self._hold(hwnd, duration)
        finally:
            active_error = sys.exception()
            release_error = None
            for up in reversed(releases):
                try:
                    self._send(up)
                except BaseException as exc:
                    if active_error is not None:
                        active_error.add_note(f"Key release failed: {exc!r}")
                    elif release_error is None:
                        release_error = exc
                    else:
                        release_error.add_note(f"Another key release failed: {exc!r}")
            if active_error is None and release_error is not None:
                raise release_error

    def type_text(self, hwnd: int, text: str) -> None:
        """Send at most 1024 UTF-16 code units via Unicode keyboard events.

        Applications that consume raw input or ignore VK_PACKET may not accept it.
        """
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        try:
            encoded = text.encode("utf-16-le")
        except UnicodeEncodeError as exc:
            raise ValueError("text must contain valid Unicode") from exc
        if len(encoded) > 2048:
            raise ValueError("text exceeds 1024 UTF-16 code units")
        self._foreground(hwnd)
        for offset in range(0, len(encoded), 2):
            unit = int.from_bytes(encoded[offset : offset + 2], "little")
            down = _INPUT(type=1, ki=_KEYBDINPUT(0, unit, 0x0004, 0, 0))
            up = _INPUT(type=1, ki=_KEYBDINPUT(0, unit, 0x0006, 0, 0))
            self._foreground(hwnd)
            self._send(down)
            try:
                self._foreground(hwnd)
            finally:
                self._send(up)

    def click(
        self, hwnd: int, x: int, y: int, button: str = "left", duration: float = 0.05
    ) -> None:
        """Click client-area physical pixels using a single move/down batch.

        Focus, window placement, cursor clipping or monitor layout can change
        after validation. Global input cannot guarantee the intended recipient.
        """
        with self._physical_pixels():
            self._click_physical(hwnd, x, y, button, duration)

    def _click_physical(self, hwnd, x, y, button, duration):
        duration = _duration(duration)
        if button not in _BUTTONS:
            raise ValueError("unsupported mouse button")
        move = self._mouse_move_event(hwnd, x, y)
        u = self._user32
        down_flag, up_flag = _BUTTONS[button]
        down = _INPUT(type=0, mi=_MOUSEINPUT(0, 0, 0, down_flag, 0, 0))
        up = _INPUT(type=0, mi=_MOUSEINPUT(0, 0, 0, up_flag, 0, 0))
        self._foreground(hwnd)
        batch = (_INPUT * 2)(move, down)
        sent = u.SendInput(2, batch, ctypes.sizeof(_INPUT))
        if sent != 2:
            raise DesktopError(f"SendInput mouse batch incomplete ({sent}/2)")
        try:
            self._hold(hwnd, duration)
        finally:
            self._send(up)

    def _mouse_move_event(self, hwnd, x, y):
        """Validate one client point and construct a virtual-desktop move."""
        if any(not isinstance(v, int) or isinstance(v, bool) for v in (x, y)):
            raise ValueError("coordinates must be integers")
        self._foreground(hwnd)
        u = self._user32
        rect = wintypes.RECT()
        if not u.GetClientRect(hwnd, ctypes.byref(rect)):
            raise DesktopError("GetClientRect failed")
        if not (0 <= x < rect.right and 0 <= y < rect.bottom):
            raise ValueError("coordinates outside target client area")
        point = wintypes.POINT(x, y)
        if not u.ClientToScreen(hwnd, ctypes.byref(point)):
            raise DesktopError("ClientToScreen failed")
        hit = u.WindowFromPoint(point)
        if not hit or u.GetAncestor(hit, 2) != hwnd:
            raise DesktopError("target point is obscured by another window")
        left, top, width, height = (u.GetSystemMetrics(n) for n in (76, 77, 78, 79))
        if not (1 <= width <= 65536 and 1 <= height <= 65536):
            raise DesktopError("invalid or unsupported virtual desktop dimensions")
        if not (left <= point.x < left + width and top <= point.y < top + height):
            raise DesktopError("target point is outside virtual desktop")
        clip = wintypes.RECT()
        if not u.GetClipCursor(ctypes.byref(clip)):
            raise DesktopError("GetClipCursor failed")
        if not (
            clip.left <= point.x < clip.right and clip.top <= point.y < clip.bottom
        ):
            raise DesktopError("target point is outside cursor clipping rectangle")
        nx = ((point.x - left) * 65536 + width - 1) // width
        ny = ((point.y - top) * 65536 + height - 1) // height
        return _INPUT(type=0, mi=_MOUSEINPUT(nx, ny, 0, 0xC001, 0, 0))

    def move_mouse(self, hwnd: int, x: int, y: int) -> None:
        """Move to a validated client-relative physical pixel; global input races remain."""
        with self._physical_pixels():
            move = self._mouse_move_event(hwnd, x, y)
            self._foreground(hwnd)
            self._send(move)

    def scroll(self, hwnd: int, x: int, y: int, delta: int) -> None:
        """Move and scroll vertically by -1200..1200 wheel units (nonzero)."""
        if (
            not isinstance(delta, int)
            or isinstance(delta, bool)
            or not 0 < abs(delta) <= 1200
        ):
            raise ValueError("delta must be a nonzero integer between -1200 and 1200")
        with self._physical_pixels():
            move = self._mouse_move_event(hwnd, x, y)
            wheel = _INPUT(
                type=0, mi=_MOUSEINPUT(0, 0, delta & 0xFFFFFFFF, 0x0800, 0, 0)
            )
            self._foreground(hwnd)
            batch = (_INPUT * 2)(move, wheel)
            sent = self._user32.SendInput(2, batch, ctypes.sizeof(_INPUT))
            if sent != 2:
                raise DesktopError(f"SendInput mouse batch incomplete ({sent}/2)")

    def drag(
        self, hwnd: int, x1: int, y1: int, x2: int, y2: int, button: str = "left"
    ) -> None:
        """Drag at most 256 pixels along a checked path, releasing even on interruption.

        Global input, mouse capture, focus and hit-testing can change between checks;
        neither the recipient nor a successful release is guaranteed.
        """
        if button not in _BUTTONS:
            raise ValueError("unsupported mouse button")
        if any(not isinstance(v, int) or isinstance(v, bool) for v in (x1, y1, x2, y2)):
            raise ValueError("coordinates must be integers")
        steps = max(abs(x2 - x1), abs(y2 - y1))
        if not 1 <= steps <= 256:
            raise ValueError("drag distance must be between 1 and 256 pixels")
        with self._physical_pixels():
            # Validate the complete path before pressing; revalidate each point
            # while dragging because window layout and clipping may have changed.
            points = [
                (x1 + round((x2 - x1) * i / steps), y1 + round((y2 - y1) * i / steps))
                for i in range(steps + 1)
            ]
            for x, y in points:
                self._mouse_move_event(hwnd, x, y)
            move = self._mouse_move_event(hwnd, x1, y1)
            down_flag, up_flag = _BUTTONS[button]
            down = _INPUT(type=0, mi=_MOUSEINPUT(0, 0, 0, down_flag, 0, 0))
            up = _INPUT(type=0, mi=_MOUSEINPUT(0, 0, 0, up_flag, 0, 0))
            self._foreground(hwnd)
            batch = (_INPUT * 2)(move, down)
            # If injection raises, the outcome is ambiguous; attempt release.
            attempted = False
            try:
                attempted = True
                sent = self._user32.SendInput(2, batch, ctypes.sizeof(_INPUT))
                if sent != 2:
                    # A return of 0 or 1 means the button-down was not inserted.
                    attempted = False
                    raise DesktopError(f"SendInput mouse batch incomplete ({sent}/2)")
                for x, y in points[1:]:
                    move = self._mouse_move_event(hwnd, x, y)
                    self._foreground(hwnd)
                    self._send(move)
            finally:
                if attempted:
                    active_error = sys.exception()
                    try:
                        self._send(up)
                    except BaseException as exc:
                        if active_error is None:
                            raise
                        active_error.add_note(f"Mouse release failed: {exc!r}")
