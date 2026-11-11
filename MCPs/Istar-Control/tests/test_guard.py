"""Tests for the in-process guard: consent, stop, budget and audit."""

import json
import os
import time
from pathlib import Path

import pytest

from istar_control import guard


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("ISTAR_CONTROL_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ISTAR_CONTROL_AUTO_APPROVE", raising=False)
    yield


def make_guard(**kwargs):
    return guard.Guard(**kwargs)


def fake_window(monkeypatch, pids=None):
    """Make the guard believe an HWND exists, visible, with a fixed pid/title."""
    state = {"pid": 4242, "title": "Istar SM63 (DEBUG)"}

    class FakeUser32:
        def IsWindow(self, hwnd):
            return 1

        def IsWindowVisible(self, hwnd):
            return 1

        def GetWindowTextLengthW(self, hwnd):
            return len(state["title"])

        def GetWindowTextW(self, hwnd, buf, size):
            for i, ch in enumerate(state["title"]):
                buf[i] = ch
            buf[len(state["title"])] = "\0"
            return len(state["title"])

        def GetWindowThreadProcessId(self, hwnd, pid_ptr):
            pid_ptr._obj.value = state["pid"]
            return 1

    class FakeKernel32:
        def OpenProcess(self, *args):
            return 1

        def QueryFullProcessImageNameW(self, handle, flags, buf, size):
            for i, ch in enumerate("game.exe"):
                buf[i] = ch
            buf[len("game.exe")] = "\0"
            return 1

        def CloseHandle(self, handle):
            return 1

    class FakeCtypesModule:
        class WinDLL:
            def __init__(self, name, **kwargs):
                self.name = name

            def __getattr__(self, name):
                raise AttributeError(name)

    monkeypatch.setattr(
        guard.ctypes,
        "WinDLL",
        lambda name, **kw: FakeUser32() if name == "user32" else FakeKernel32(),
    )
    return state


def auto_guard(**kwargs):
    g = make_guard(**kwargs)
    g._auto_approve = True
    return g


def test_request_grants_and_check_allows(monkeypatch):
    fake_window(monkeypatch)
    g = auto_guard()
    result = g.request(123, ["input", "capture"])
    assert result["approved"] is True
    assert result["capabilities"] == ["capture", "input"]
    g.check(123, "input")


def test_check_fails_without_grant(monkeypatch):
    fake_window(monkeypatch)
    g = make_guard()
    with pytest.raises(guard.GuardError) as error:
        g.check(123, "input")
    assert "control_request" in str(error.value)


def test_check_fails_for_capability_not_granted(monkeypatch):
    fake_window(monkeypatch)
    g = auto_guard()
    g.request(123, ["capture"])
    with pytest.raises(guard.GuardError) as error:
        g.check(123, "input")
    assert "input" in str(error.value)


def test_grant_expires(monkeypatch):
    fake_window(monkeypatch)
    g = auto_guard()
    g.request(123, ["input"], ttl_seconds=1)
    g._grants[123].expires_at = time.time() - 1
    with pytest.raises(guard.GuardError) as error:
        g.check(123, "input")
    assert "expired" in str(error.value).lower()


def test_grant_revoked_on_process_change(monkeypatch):
    fake_window(monkeypatch)
    g = auto_guard()
    g.request(123, ["input"])

    class FakeUser32:
        def IsWindow(self, hwnd):
            return 1

        def IsWindowVisible(self, hwnd):
            return 1

        def GetWindowTextLengthW(self, hwnd):
            return 0

        def GetWindowTextW(self, hwnd, buf, size):
            return 0

        def GetWindowThreadProcessId(self, hwnd, pid_ptr):
            pid_ptr._obj.value = 9999
            return 1

    monkeypatch.setattr(guard.ctypes, "WinDLL", lambda name, **kw: FakeUser32())
    with pytest.raises(guard.GuardError) as error:
        g.check(123, "input")
    assert "process changed" in str(error.value).lower()


def test_stop_blocks_everything_and_revokes(monkeypatch):
    fake_window(monkeypatch)
    g = auto_guard()
    g.request(123, ["input"])
    g.stop()
    with pytest.raises(guard.GuardError) as error:
        g.check(123, "input")
    assert "stopped" in str(error.value).lower()
    assert g.stopped is True
    assert g._grants == {}  # stop() revokes every grant
    g.resume()


def test_session_budget(monkeypatch):
    fake_window(monkeypatch)
    g = auto_guard(session_actions=2)
    g.request(123, ["input"])
    g.check(123, "input")
    g.check(123, "input")
    with pytest.raises(guard.GuardError) as error:
        g.check(123, "input")
    assert "session limit" in str(error.value).lower()


def test_rate_budget(monkeypatch):
    fake_window(monkeypatch)
    g = auto_guard(actions_per_minute=3)
    g.request(123, ["input"])
    for _ in range(3):
        g.check(123, "input")
    with pytest.raises(guard.GuardError) as error:
        g.check(123, "input")
    assert "rate limit" in str(error.value).lower()


def test_capabilities_are_validated(monkeypatch):
    fake_window(monkeypatch)
    g = auto_guard()
    with pytest.raises(ValueError):
        g.request(123, ["shell"])
    with pytest.raises(ValueError):
        g.request(123, [])
    with pytest.raises(ValueError):
        g.request(123, "input", ttl_seconds=0)
    with pytest.raises(ValueError):
        g.request(123, "input", ttl_seconds=99999)


def test_audit_records_without_typed_text(monkeypatch):
    fake_window(monkeypatch)
    g = auto_guard()
    g.request(123, ["input"], ttl_seconds=60)
    g.audit("action", 123, "completed", method="dialog")
    path = Path(os.environ["ISTAR_CONTROL_DATA_DIR"]) / "audit.jsonl"
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert any(json.loads(line)["state"] == "granted" for line in lines)
    assert any(json.loads(line)["state"] == "completed" for line in lines)
    assert "secret text" not in path.read_text(encoding="utf-8")


def test_audit_rotates(monkeypatch):
    fake_window(monkeypatch)
    g = auto_guard()
    path = Path(os.environ["ISTAR_CONTROL_DATA_DIR"]) / "audit.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * (guard.AUDIT_ROTATE_BYTES), encoding="utf-8")
    g.audit("action", 1, "completed")
    backup = Path(str(path) + ".1")
    assert backup.exists()
    assert path.stat().st_size < guard.AUDIT_ROTATE_BYTES


def test_auto_approve_is_off_by_default(monkeypatch):
    fake_window(monkeypatch)
    monkeypatch.setattr(guard, "GuardError", RuntimeError)  # not used
    g = make_guard()
    assert g._auto_approve is False


def test_snapshot_reports_state(monkeypatch):
    fake_window(monkeypatch)
    g = auto_guard()
    g.request(123, ["input"], ttl_seconds=60)
    g.check(123, "input")
    snap = g.snapshot()
    assert snap["stopped"] is False
    assert snap["grants"][0]["hwnd"] == 123
    assert snap["grants"][0]["title"] == "Istar SM63 (DEBUG)"
    assert snap["session_actions"] >= 1
