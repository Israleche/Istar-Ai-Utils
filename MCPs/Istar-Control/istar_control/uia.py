"""Window-scoped UI Automation. Requires the optional Windows package comtypes.

COM work runs on a fresh MTA thread; elements never escape that apartment.
UIA providers can block and can expose sensitive text as names. Values and
password controls are not read. Foreground checks cannot eliminate races.
"""

from __future__ import annotations

import ctypes
import sys
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from dataclasses import dataclass


class UIAError(RuntimeError):
    """UI Automation could not safely complete the requested operation."""


@dataclass(frozen=True)
class WindowTarget:
    hwnd: int
    pid: int


@dataclass(frozen=True)
class Selector:
    automation_id: str | None = None
    control_type: int | None = None


def _validate(target: WindowTarget, selector: Selector | None, limit: int, depth: int):
    if not isinstance(target, WindowTarget) or any(
        type(n) is not int or n <= 0 for n in (target.hwnd, target.pid)
    ):
        raise ValueError("target requires positive integer hwnd and pid")
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    if type(depth) is not int or not 0 <= depth <= 20:
        raise ValueError("depth must be between 0 and 20")
    if selector is not None:
        if not isinstance(selector, Selector):
            raise ValueError("invalid selector")
        if selector.automation_id is None and selector.control_type is None:
            raise ValueError("selector must specify an automation_id or control_type")
        if selector.automation_id is not None and (
            not isinstance(selector.automation_id, str)
            or not 1 <= len(selector.automation_id) <= 256
        ):
            raise ValueError("automation_id must contain 1 to 256 characters")
        if selector.control_type is not None and (
            type(selector.control_type) is not int
            or not 50000 <= selector.control_type <= 60000
        ):
            raise ValueError("invalid control_type")


class _WindowsBackend:
    def __init__(self):
        if sys.platform != "win32":
            raise UIAError("Windows is required")
        try:
            import comtypes
            import comtypes.client
        except ImportError:
            raise UIAError(
                "UI Automation requires the optional comtypes package"
            ) from None
        self.comtypes = comtypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        for name, args, result in (
            ("IsWindow", [wintypes.HWND], wintypes.BOOL),
            ("IsWindowVisible", [wintypes.HWND], wintypes.BOOL),
            ("IsIconic", [wintypes.HWND], wintypes.BOOL),
            ("GetForegroundWindow", [], wintypes.HWND),
            (
                "GetWindowThreadProcessId",
                [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)],
                wintypes.DWORD,
            ),
        ):
            fn = getattr(self.user32, name)
            fn.argtypes, fn.restype = args, result
        module = comtypes.client.GetModule("UIAutomationCore.dll")
        self.module = module
        self.automation = comtypes.client.CreateObject(
            module.CUIAutomation, interface=module.IUIAutomation
        )
        self.walker = self.automation.ControlViewWalker

    def check(self, target: WindowTarget, foreground: bool = False):
        u = self.user32
        pid = wintypes.DWORD()
        if (
            not u.IsWindow(target.hwnd)
            or not u.IsWindowVisible(target.hwnd)
            or u.IsIconic(target.hwnd)
            or not u.GetWindowThreadProcessId(target.hwnd, ctypes.byref(pid))
            or pid.value != target.pid
        ):
            raise UIAError("target window is unavailable or identity changed")
        if foreground and u.GetForegroundWindow() != target.hwnd:
            raise UIAError("target window is not foreground")

    def root(self, target):
        return self.automation.ElementFromHandle(target.hwnd)

    def child(self, element):
        return self.walker.GetFirstChildElement(element)

    def sibling(self, element):
        return self.walker.GetNextSiblingElement(element)

    def describe(self, element):
        if element.CurrentIsPassword:
            return None
        automation_id = element.CurrentAutomationId or ""
        return {
            "name": (element.CurrentName or "")[:256],
            "automation_id": automation_id if len(automation_id) <= 256 else None,
            "control_type": int(element.CurrentControlType),
            "enabled": bool(element.CurrentIsEnabled),
        }

    def invoke(self, element):
        pattern = element.GetCurrentPattern(10000)  # UIA_InvokePatternId
        if not pattern:
            raise UIAError("element does not support Invoke")
        pattern.QueryInterface(self.module.IUIAutomationInvokePattern).Invoke()

    def set_value(self, element, value: str):
        pattern = element.GetCurrentPattern(10002)  # UIA_ValuePatternId
        if not pattern:
            raise UIAError("element does not support Value")
        pattern.QueryInterface(self.module.IUIAutomationValuePattern).SetValue(value)

    def toggle(self, element):
        pattern = element.GetCurrentPattern(10015)  # UIA_TogglePatternId
        if not pattern:
            raise UIAError("element does not support Toggle")
        pattern.QueryInterface(self.module.IUIAutomationTogglePattern).Toggle()


def _walk(backend, root, limit, depth):
    # Stack stores the next unvisited sibling, so traversal is bounded without
    # materializing a provider's potentially enormous child array.
    stack = [(root, 0, False)]
    found = []
    truncated = False
    while stack:
        if len(found) >= limit:
            truncated = True
            break
        element, level, siblings = stack.pop()
        if siblings:
            sibling = backend.sibling(element)
            if sibling:
                stack.append((sibling, level, True))
        info = backend.describe(element)
        # Never traverse a password control's descendants.
        if info is not None and level < depth:
            child = backend.child(element)
            if child:
                stack.append((child, level + 1, True))
        elif info is not None and level == depth and backend.child(element):
            truncated = True
        found.append((element, level, info))
    return found, truncated


def _run(operation, timeout: float = 10.0):
    # A new, non-UI thread provides an MTA and keeps COM pointers on one thread.
    # A timeout abandons a stuck provider rather than hanging the caller; the
    # worker thread may continue in the background after the timeout.
    if sys.platform != "win32":
        raise UIAError("Windows is required")
    try:
        # comtypes initializes COM on its first importing thread. Import it
        # before creating our worker, then explicitly initialize the worker.
        import comtypes
        import comtypes.client
    except ImportError:
        raise UIAError("UI Automation requires the optional comtypes package") from None

    def worker():
        initialized = False
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
            initialized = True
            backend = _WindowsBackend()
            try:
                return operation(backend)
            finally:
                del backend
        except UIAError:
            raise
        except Exception:
            raise UIAError("UI Automation provider failed or is unavailable") from None
        finally:
            if initialized:
                comtypes.CoUninitialize()

    import concurrent.futures

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        return executor.submit(worker).result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        executor.shutdown(wait=False)
        raise UIAError(
            "UI Automation operation timed out; the provider may be stuck"
        ) from None
    except BaseException:
        executor.shutdown(wait=False)
        raise


def _check_timeout(timeout):
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout must be a number of seconds")
    if not 1 <= timeout <= 60:
        raise ValueError("timeout must be between 1 and 60 seconds")


def observe(target: WindowTarget, *, limit: int = 100, depth: int = 8, timeout: float = 10.0) -> dict:
    """Return bounded control metadata, not values; names may be sensitive."""
    _validate(target, None, limit, depth)
    _check_timeout(timeout)

    def operation(backend):
        backend.check(target)
        nodes, truncated = _walk(backend, backend.root(target), limit, depth)
        backend.check(target)
        return {
            "elements": [
                {"depth": level, **info} for _, level, info in nodes if info is not None
            ],
            "truncated": truncated,
        }

    return _run(operation, timeout)


def _single_match(backend, target, selector, limit, depth):
    backend.check(target, foreground=True)
    nodes, truncated = _walk(backend, backend.root(target), limit, depth)
    matches = [
        (element, info)
        for element, _, info in nodes
        if info is not None
        and (
            selector.automation_id is None
            or info["automation_id"] == selector.automation_id
        )
        and (
            selector.control_type is None
            or info["control_type"] == selector.control_type
        )
    ]
    if truncated:
        raise UIAError("search was truncated; uniqueness cannot be established")
    if len(matches) != 1:
        raise UIAError("selector must match exactly one element")
    return matches[0]


def invoke(
    target: WindowTarget,
    selector: Selector,
    *,
    limit: int = 100,
    depth: int = 8,
    timeout: float = 10.0,
) -> None:
    """Invoke exactly one matching non-password control, never physical input."""
    _validate(target, selector, limit, depth)
    _check_timeout(timeout)

    def operation(backend):
        element, info = _single_match(backend, target, selector, limit, depth)
        if not info["enabled"]:
            raise UIAError("element is disabled")
        backend.check(target, foreground=True)
        backend.invoke(element)

    return _run(operation, timeout)


def set_value(
    target: WindowTarget,
    selector: Selector,
    value: str,
    *,
    limit: int = 100,
    depth: int = 8,
    timeout: float = 10.0,
) -> None:
    """Set the value of exactly one non-password ValuePattern control.

    Accepts a bounded string. Password controls never match; very long
    values are rejected. The value is never logged or read back.
    """
    _validate(target, selector, limit, depth)
    _check_timeout(timeout)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("value must be a non-empty string")
    if len(value) > 4096:
        raise ValueError("value must contain at most 4096 characters")

    def operation(backend):
        element, info = _single_match(backend, target, selector, limit, depth)
        if not info["enabled"]:
            raise UIAError("element is disabled")
        backend.check(target, foreground=True)
        backend.set_value(element, value)

    return _run(operation, timeout)


def toggle(
    target: WindowTarget,
    selector: Selector,
    *,
    limit: int = 100,
    depth: int = 8,
    timeout: float = 10.0,
) -> None:
    """Toggle exactly one non-password TogglePattern control."""
    _validate(target, selector, limit, depth)
    _check_timeout(timeout)

    def operation(backend):
        element, info = _single_match(backend, target, selector, limit, depth)
        if not info["enabled"]:
            raise UIAError("element is disabled")
        backend.check(target, foreground=True)
        backend.toggle(element)

    return _run(operation, timeout)
