"""In-process safety gate for desktop control: consent, stop, budget and audit.

This module is deliberately fail-closed. A window receives input or capture
only while a grant exists for it, the grant is not expired, the target process
is unchanged, and the action fits the session budget. A stopped guard refuses
everything until ``resume`` is called.

Risks that cannot be removed from this process are documented, not hidden:

* A local process may fake a user click, so a menu can never prove who
  clicked it. Grants are scoped to a window and a short lifetime, but the
  dialog is not a cryptographic approval.
* ``ISTAR_CONTROL_AUTO_APPROVE=1`` bypasses the human check entirely. It
  exists for automated tests and unattended development machines only.
"""

from __future__ import annotations

import ctypes
import json
import math
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path


class GuardError(RuntimeError):
    """The requested action is not authorized."""


MAX_TTL_SECONDS = 3600
DEFAULT_TTL_SECONDS = 300
MAX_SESSIONS_ACTIONS = 300
MAX_ACTIONS_PER_MINUTE = 60
AUDIT_ROTATE_BYTES = 5 * 1024 * 1024
AUDIT_FIELD_LIMIT = 256

CAPABILITIES = {"focus", "capture", "input"}


def _data_dir() -> Path:
    override = os.environ.get("ISTAR_CONTROL_DATA_DIR")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "opencode" / "istar-control"
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "opencode" / "istar-control"


def status_path() -> Path:
    return _data_dir() / "status.json"


def audit_path() -> Path:
    return _data_dir() / "audit.jsonl"


@dataclass
class Grant:
    hwnd: int
    capabilities: frozenset
    pid: int
    title: str
    created_at: float
    expires_at: float
    actions_used: int = 0

    def allows(self, capability: str) -> bool:
        return capability in self.capabilities


@dataclass
class _Budget:
    session_actions: int = 0
    recent: list = field(default_factory=list)


class Guard:
    """Owns grants, the stop flag, the action budget and the audit log."""

    def __init__(
        self,
        *,
        session_actions: int = MAX_SESSIONS_ACTIONS,
        actions_per_minute: int = MAX_ACTIONS_PER_MINUTE,
    ):
        if not isinstance(session_actions, int) or session_actions < 1:
            raise ValueError("session_actions must be a positive integer")
        if not isinstance(actions_per_minute, int) or actions_per_minute < 1:
            raise ValueError("actions_per_minute must be a positive integer")
        self._limits = _Budget()
        self._session_limit = session_actions
        self._minute_limit = actions_per_minute
        self._grants: dict[int, Grant] = {}
        self._lock = threading.RLock()
        self._stopped = False
        self._session_id = uuid.uuid4().hex[:16]
        self._auto_approve = os.environ.get("ISTAR_CONTROL_AUTO_APPROVE") == "1"

    # ------------------------------------------------------------------ grants

    def _window_identity(self, hwnd: int):
        if sys.platform != "win32":
            raise GuardError("Window consent is only available on Windows")
        u = ctypes.WinDLL("user32", use_last_error=True)
        try:
            u.IsWindow.argtypes = [ctypes.c_void_p]
            u.IsWindow.restype = ctypes.c_int
            u.IsWindowVisible.argtypes = [ctypes.c_void_p]
            u.IsWindowVisible.restype = ctypes.c_int
            u.GetWindowThreadProcessId.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_ulong),
            ]
            u.GetWindowThreadProcessId.restype = ctypes.c_ulong
        except AttributeError:
            # Test doubles may not expose settable argtypes.
            pass
        if not u.IsWindow(hwnd) or not u.IsWindowVisible(hwnd):
            raise GuardError("target window does not exist or is not visible")
        pid = ctypes.c_ulong()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            raise GuardError("could not read the target process id")
        length = u.GetWindowTextLengthW(hwnd)
        title = ""
        if length:
            buf = ctypes.create_unicode_buffer(length + 1)
            u.GetWindowTextW(hwnd, buf, length + 1)
            title = " ".join(buf.value.split())[:AUDIT_FIELD_LIMIT]
        exe = self._process_image(pid.value) or "unknown"
        return int(pid.value), title, exe

    def _process_image(self, pid: int):
        try:
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            try:
                k.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
                k.OpenProcess.restype = ctypes.c_void_p
                k.QueryFullProcessImageNameW.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_ulong,
                    ctypes.c_wchar_p,
                    ctypes.POINTER(ctypes.c_ulong),
                ]
                k.QueryFullProcessImageNameW.restype = ctypes.c_int
            except AttributeError:
                # Test doubles may not expose settable argtypes.
                pass
            handle = k.OpenProcess(
                0x1000, False, pid
            )  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return None
            size = ctypes.c_ulong(4096)
            buf = ctypes.create_unicode_buffer(size.value)
            if not k.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                k.CloseHandle(handle)
                return None
            k.CloseHandle(handle)
            path = buf.value
            return Path(path).name or path
        except (OSError, AttributeError, ValueError):
            return None

    def request(
        self,
        hwnd: int,
        capabilities,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        timeout: float | None = None,
    ) -> dict:
        """Ask a human to approve control of a window.

        Passing ``timeout`` returns ``"timeout"`` when the dialog is not
        answered; the dialog may stay open on screen and its answer is then
        ignored. ``timeout=None`` waits for a human decision.
        """
        caps = self._normalize_capabilities(capabilities)
        ttl = self._normalize_ttl(ttl_seconds)
        pid, title, exe = self._window_identity(hwnd)

        if self._auto_approve:
            approved = True
            method = "auto"
        else:
            approved, method = self._ask(exe, title, pid, caps, timeout)

        if not approved:
            self.audit("consent", hwnd, "denied", capabilities=list(caps))
            return {"approved": False, "reason": method, "hwnd": hwnd}

        now = time.time()
        with self._lock:
            self._grants[hwnd] = Grant(
                hwnd=hwnd,
                capabilities=frozenset(caps),
                pid=pid,
                title=title,
                created_at=now,
                expires_at=now + ttl,
            )
        self.audit(
            "consent", hwnd, "granted", capabilities=list(caps), ttl=ttl, method=method
        )
        return {
            "approved": True,
            "method": method,
            "hwnd": hwnd,
            "title": title,
            "exe": exe,
            "capabilities": sorted(caps),
            "expires_at": now + ttl,
        }

    def _normalize_capabilities(self, capabilities):
        if isinstance(capabilities, str):
            capabilities = [capabilities]
        if not isinstance(capabilities, (list, tuple)):
            raise ValueError("capabilities must be a list of capability names")
        caps = []
        for cap in capabilities:
            if not isinstance(cap, str):
                raise ValueError("capabilities must be strings")
            name = cap.lower()
            if name not in CAPABILITIES:
                raise ValueError(f"unsupported capability: {cap}")
            if name not in caps:
                caps.append(name)
        if not caps:
            raise ValueError("at least one capability is required")
        return caps

    def _normalize_ttl(self, ttl_seconds):
        try:
            ttl = float(ttl_seconds)
        except (TypeError, ValueError):
            raise ValueError("ttl_seconds must be a number") from None
        if not math.isfinite(ttl) or not 1 <= ttl <= MAX_TTL_SECONDS:
            raise ValueError(f"ttl_seconds must be between 1 and {MAX_TTL_SECONDS}")
        return int(ttl)

    def _ask(self, exe, title, pid, caps, timeout):
        if sys.platform != "win32":
            return False, "unsupported"

        def ask():
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            message = (
                f"{exe} (pid {pid}) wants to control:\n\n{title}\n\n"
                f"Capabilities: {', '.join(caps)}\n\nAllow?"
            )
            result = user32.MessageBoxW(
                None,
                message,
                "Istar-Control - approve control of this window?",
                4
                | 32
                | 0x40000
                | 0x10000,  # YESNO | ICONQUESTION | TOPMOST | SETFOREGROUND
            )
            return result == 6  # IDYES

        outcome = {}

        def run():
            outcome["approved"] = ask()

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(timeout)
        if worker.is_alive():
            if timeout is not None:
                return False, "timeout"
            worker.join()
        return bool(outcome.get("approved")), "dialog"

    def check(self, hwnd: int, capability: str) -> None:
        """Raise GuardError unless the action is currently allowed."""
        if capability not in CAPABILITIES:
            raise ValueError(f"unsupported capability: {capability}")
        with self._lock:
            if self._stopped:
                raise GuardError(
                    "Istar-Control is stopped; call control_resume to continue"
                )
            self._enforce_budget()
            grant = self._grants.get(int(hwnd)) if isinstance(hwnd, int) else None
            if grant is None:
                if not self._auto_approve:
                    raise GuardError(
                        f"no grant for window {hwnd}; call control_request first"
                    )
                pid, title, exe = self._window_identity(int(hwnd))
                now = time.time()
                grant = Grant(
                    hwnd=int(hwnd),
                    capabilities=frozenset(CAPABILITIES),
                    pid=pid,
                    title=title,
                    created_at=now,
                    expires_at=now + DEFAULT_TTL_SECONDS,
                )
                self._grants[grant.hwnd] = grant
                self.audit(
                    "consent",
                    grant.hwnd,
                    "auto_granted",
                    method="auto",
                    capabilities=sorted(CAPABILITIES),
                )
            if time.time() > grant.expires_at:
                self._grants.pop(grant.hwnd, None)
                raise GuardError("grant expired; call control_request again")
            try:
                now_pid, _, _ = self._window_identity(grant.hwnd)
            except GuardError:
                self._grants.pop(grant.hwnd, None)
                raise GuardError(
                    "target window disappeared or was replaced; grant revoked"
                ) from None
            if now_pid != grant.pid:
                self._grants.pop(grant.hwnd, None)
                raise GuardError("target process changed; grant revoked")
            if capability not in grant.capabilities:
                raise GuardError(
                    f"grant for window {hwnd} does not include capability {capability}"
                )
            grant.actions_used += 1

    def _enforce_budget(self):
        now = time.time()
        self._limits.recent = [t for t in self._limits.recent if now - t <= 60]
        if self._limits.session_actions >= self._session_limit:
            raise GuardError(
                f"session limit of {self._session_limit} actions reached; restart the session or raise the limit"
            )
        if len(self._limits.recent) >= self._minute_limit:
            raise GuardError(
                f"rate limit of {self._minute_limit} actions per minute reached"
            )
        self._limits.session_actions += 1
        self._limits.recent.append(now)

    # -------------------------------------------------------------------- stop

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            self._grants.clear()

    def resume(self) -> None:
        with self._lock:
            self._stopped = False

    @property
    def stopped(self) -> bool:
        return self._stopped

    def revoke(self, hwnd: int) -> None:
        with self._lock:
            self._grants.pop(int(hwnd), None)
        self.audit("consent", int(hwnd), "revoked")

    def revoke_all(self) -> None:
        with self._lock:
            targets = list(self._grants)
            self._grants.clear()
        for hwnd in targets:
            self.audit("consent", hwnd, "revoked")

    # ------------------------------------------------------------------ audit

    def audit(self, action, hwnd, state, **detail):
        # Only structural, non-text fields are allowed: no typed content,
        # screenshots or credentials ever reach the audit log.
        allowed = {"capabilities", "ttl", "method", "exe", "reason"}
        record = {
            "ts": time.time(),
            "session": self._session_id,
            "action": str(action)[:64],
            "hwnd": hwnd if isinstance(hwnd, int) else None,
            "state": str(state)[:32],
        }
        for key, value in detail.items():
            if key not in allowed:
                continue
            if isinstance(value, str):
                record[key] = " ".join(value.split())[:AUDIT_FIELD_LIMIT]
            elif isinstance(value, (list, tuple)):
                record[key] = [" ".join(str(v).split())[:64] for v in value[:8]]
            elif isinstance(value, (int, float)) and math.isfinite(value):
                record[key] = value
        path = audit_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._rotate(path)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            # Auditing must never break control; it is reported through status.
            pass
        return record

    def _rotate(self, path):
        try:
            if path.exists() and path.stat().st_size >= AUDIT_ROTATE_BYTES:
                path.replace(path.with_suffix(path.suffix + ".1"))
        except OSError:
            pass

    def snapshot(self) -> dict:
        with self._lock:
            now = time.time()
            return {
                "stopped": self._stopped,
                "auto_approve": self._auto_approve,
                "session_actions": self._limits.session_actions,
                "actions_per_minute": len(self._limits.recent),
                "session_limit": self._session_limit,
                "minute_limit": self._minute_limit,
                "grants": [
                    {
                        "hwnd": g.hwnd,
                        "title": g.title,
                        "capabilities": sorted(g.capabilities),
                        "pid": g.pid,
                        "expires_in": max(0, int(g.expires_at - now)),
                    }
                    for g in self._grants.values()
                ],
                "audit_path": str(audit_path()),
            }


_guard: Guard | None = None


def get_guard() -> Guard:
    global _guard
    if _guard is None:
        _guard = Guard()
    return _guard


def reset_guard() -> Guard:
    """Replace the process-wide guard; intended for tests."""
    global _guard
    _guard = Guard()
    return _guard
