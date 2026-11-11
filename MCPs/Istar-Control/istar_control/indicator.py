"""Optional, bounded Win32 desktop-control indicator.

Call ``show_indicator(seconds, action=..., hwnd=...)`` to start it; call
``complete()`` when the action has finished, and ``close()`` to stop it early.
Only fixed action labels and an optional numeric window handle are displayed.
Visibility is best-effort: other topmost windows and secure desktops may cover it.
"""

from __future__ import annotations

import ctypes
import math
import sys
import threading
import time
from ctypes import wintypes


class IndicatorError(RuntimeError):
    """The indicator could not be created or stopped safely."""


_EX_STYLE = 0x00080000 | 0x00000020 | 0x08000000 | 0x00000080 | 0x00000008
_WS_POPUP = 0x80000000
_SW_SHOWNOACTIVATE = 4
_MAX_SECONDS = 60.0
_ACTIONS = {
    "control": "Desktop control",
    "focus": "Focus window",
    "screenshot": "Inspect screen",
    "key": "Press key",
    "keys": "Press keys",
    "type": "Type text",
    "click": "Click",
    "move": "Move pointer",
    "scroll": "Scroll",
    "drag": "Drag",
}


def _label(action, hwnd, completed=False):
    if not isinstance(action, str) or action not in _ACTIONS:
        raise ValueError("action must be a supported action identifier")
    if hwnd is not None and (type(hwnd) is not int or not 0 < hwnd < 2**64):
        raise ValueError("hwnd must be a positive 64-bit integer")
    target = f"Window  0x{hwnd:X}" if hwnd is not None else "Window  not specified"
    state = "COMPLETED" if completed else "ACTIVE"
    return f"ISTAR CONTROL   /   {state}\r\n\r\n{_ACTIONS[action]}\r\n{target}"


def _api():
    if sys.platform != "win32":
        raise IndicatorError("Windows is required")
    u = ctypes.WinDLL("user32", use_last_error=True)
    signatures = {
        "CreateWindowExW": (
            [
                wintypes.DWORD,
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                wintypes.DWORD,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.HWND,
                wintypes.HMENU,
                wintypes.HINSTANCE,
                ctypes.c_void_p,
            ],
            wintypes.HWND,
        ),
        "SetLayeredWindowAttributes": (
            [wintypes.HWND, wintypes.DWORD, ctypes.c_ubyte, wintypes.DWORD],
            wintypes.BOOL,
        ),
        "ShowWindow": ([wintypes.HWND, ctypes.c_int], wintypes.BOOL),
        "SetWindowTextW": ([wintypes.HWND, wintypes.LPCWSTR], wintypes.BOOL),
        "DestroyWindow": ([wintypes.HWND], wintypes.BOOL),
        "PeekMessageW": (
            [
                ctypes.POINTER(wintypes.MSG),
                wintypes.HWND,
                wintypes.UINT,
                wintypes.UINT,
                wintypes.UINT,
            ],
            wintypes.BOOL,
        ),
        "TranslateMessage": ([ctypes.POINTER(wintypes.MSG)], wintypes.BOOL),
        "DispatchMessageW": ([ctypes.POINTER(wintypes.MSG)], ctypes.c_ssize_t),
    }
    for name, (args, result) in signatures.items():
        fn = getattr(u, name)
        fn.argtypes, fn.restype = args, result
    return u


class Indicator:
    """Own a single indicator window on its own thread, with bounded lifetime."""

    def __init__(
        self, seconds: float = 5, *, action: str = "control", hwnd: int | None = None
    ):
        seconds = float(seconds)
        if not math.isfinite(seconds) or not 0 < seconds <= _MAX_SECONDS:
            raise ValueError("seconds must be greater than 0 and at most 60")
        self._active_label = _label(action, hwnd)
        self._completed_label = _label(action, hwnd, True)
        self._complete = threading.Event()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._error = None
        self._thread = threading.Thread(target=self._run, args=(seconds,), daemon=True)
        self._thread.start()
        if not self._ready.wait(5):
            self._stop.set()
            raise IndicatorError("indicator startup timed out")
        if self._error:
            raise IndicatorError("indicator startup failed") from self._error

    def _run(self, seconds):
        hwnd = None
        u = None
        try:
            u = _api()
            # STATIC is a predefined system class; no callback or custom DLL needed.
            hwnd = u.CreateWindowExW(
                _EX_STYLE,
                "STATIC",
                self._active_label,
                _WS_POPUP | 0x00800000 | 0x00000001,
                20,
                20,
                340,
                100,
                None,
                None,
                None,
                None,
            )
            if not hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            if not u.SetLayeredWindowAttributes(hwnd, 0, 255, 2):
                raise ctypes.WinError(ctypes.get_last_error())
            if not self._stop.is_set():
                u.ShowWindow(hwnd, _SW_SHOWNOACTIVATE)
            deadline = time.monotonic() + seconds
            self._ready.set()
            msg = wintypes.MSG()
            completed = False
            while not self._stop.is_set() and time.monotonic() < deadline:
                if self._complete.is_set() and not completed:
                    if not u.SetWindowTextW(hwnd, self._completed_label):
                        raise IndicatorError("indicator update failed")
                    completed = True
                while (
                    not self._stop.is_set()
                    and time.monotonic() < deadline
                    and u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1)
                ):
                    u.TranslateMessage(ctypes.byref(msg))
                    u.DispatchMessageW(ctypes.byref(msg))
                self._stop.wait(min(0.05, max(0, deadline - time.monotonic())))
        except BaseException as exc:
            self._error = exc
        finally:
            if hwnd and u and not u.DestroyWindow(hwnd):
                self._error = IndicatorError("indicator window destruction failed")
            self._ready.set()

    def complete(self):
        """Mark the action finished (not necessarily successful), without extending expiry."""
        if self._error:
            raise IndicatorError("indicator failed") from self._error
        if not self._thread.is_alive() or self._stop.is_set():
            raise IndicatorError("indicator is no longer active")
        self._complete.set()

    def close(self):
        """Stop and join the owner thread; repeated calls are safe."""
        self._stop.set()
        self._thread.join(5)
        if self._thread.is_alive():
            raise IndicatorError("indicator shutdown timed out")
        if self._error:
            raise IndicatorError("indicator failed") from self._error

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def show_indicator(
    seconds: float = 5, *, action: str = "control", hwnd: int | None = None
) -> Indicator:
    return Indicator(seconds, action=action, hwnd=hwnd)
