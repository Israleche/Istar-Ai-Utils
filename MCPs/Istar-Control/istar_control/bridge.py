"""Local, network-free activity status for companion OpenCode surfaces.

The server publishes a small JSON status file so an OpenCode plugin can show
what is being controlled without opening a port. The file contains no typed
text, no screenshots and no credentials. It is a status hint, not an
authorization channel: any local process can write it.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


class BridgeError(RuntimeError):
    """The status file could not be published or read."""


MAX_STATUS_BYTES = 64 * 1024
MAX_TITLE = 200
MAX_DETAIL = 120
ACTIONS = {
    "focus",
    "screenshot",
    "capture",
    "key",
    "keys",
    "type",
    "click",
    "move",
    "scroll",
    "drag",
    "uia",
    "browser",
}
# Only allowlisted operation names may be published, so arbitrary user text
# (for example typed content) can never reach the status file.
OPERATIONS = {
    "open",
    "navigate",
    "back",
    "snapshot",
    "aria",
    "click",
    "fill",
    "press",
    "scroll",
    "shot",
}


def status_path() -> Path:
    """Return the status file path, honouring an explicit override."""
    override = os.environ.get("ISTAR_CONTROL_STATUS_PATH")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".local" / "share"
    return root / "opencode" / "istar-control" / "status.json"


def _text(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("text fields must be strings")
    cleaned = " ".join(value.split())
    return cleaned[:limit] or None


def publish(
    *,
    action: str,
    state: str,
    hwnd: int | None = None,
    title: str | None = None,
    operation: str | None = None,
) -> dict:
    """Write the current activity status atomically and return it."""
    if action not in ACTIONS:
        raise ValueError("unsupported action")
    if state not in {"active", "completed", "failed", "idle"}:
        raise ValueError("unsupported state")
    if operation is not None and operation not in OPERATIONS:
        raise ValueError("unsupported operation")
    if hwnd is not None and (type(hwnd) is not int or hwnd <= 0):
        raise ValueError("hwnd must be a positive integer")
    payload = {
        "server": "Istar-Control",
        "action": action,
        "state": state,
        "hwnd": hwnd,
        "title": _text(title, MAX_TITLE),
        "operation": operation,
        "pid": os.getpid(),
        "updated_at": time.time(),
    }
    path = status_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(temporary, path)
    except OSError as error:
        raise BridgeError("Unable to publish the control status") from error
    return payload


def read_status() -> dict:
    """Read the last published status, or an idle status when none exists."""
    path = status_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {
            "server": "Istar-Control",
            "state": "idle",
            "action": None,
            "hwnd": None,
            "title": None,
            "operation": None,
            "updated_at": None,
            "pid": None,
        }
    except OSError as error:
        raise BridgeError("Unable to read the control status") from error
    if len(raw) > MAX_STATUS_BYTES:
        raise BridgeError("Control status file is unexpectedly large")
    try:
        data = json.loads(raw)
    except ValueError:
        raise BridgeError("Control status file is not valid JSON") from None
    if not isinstance(data, dict):
        raise BridgeError("Control status file is not a JSON object")
    return data


def clear() -> None:
    """Remove the status file if it exists; failures are not fatal."""
    try:
        status_path().unlink(missing_ok=True)
    except OSError:
        pass
