"""Local activity status shared with companion OpenCode surfaces."""

import json
import os

import pytest

from istar_control import bridge


@pytest.fixture(autouse=True)
def isolated_status(tmp_path, monkeypatch):
    monkeypatch.setenv("ISTAR_CONTROL_STATUS_PATH", str(tmp_path / "status.json"))
    yield


def test_publish_and_read_round_trip():
    published = bridge.publish(
        action="click", state="active", hwnd=4242, title="Istar SM63 (DEBUG)"
    )
    assert published["action"] == "click"
    assert published["state"] == "active"
    assert published["hwnd"] == 4242
    assert published["pid"] == os.getpid()
    read = bridge.read_status()
    assert read["title"] == "Istar SM63 (DEBUG)"
    assert read["state"] == "active"


def test_read_status_is_idle_without_file():
    status = bridge.read_status()
    assert status["state"] == "idle"
    assert status["action"] is None


def test_publish_rejects_unknown_action_and_state():
    with pytest.raises(ValueError):
        bridge.publish(action="shell", state="active")
    with pytest.raises(ValueError):
        bridge.publish(action="click", state="running")


def test_publish_rejects_invalid_hwnd():
    with pytest.raises(ValueError):
        bridge.publish(action="click", state="active", hwnd=0)


def test_title_is_trimmed_and_bounded():
    bridge.publish(action="focus", state="active", title="  spaced   title  ")
    assert bridge.read_status()["title"] == "spaced title"
    bridge.publish(action="focus", state="active", title="x" * 500)
    assert len(bridge.read_status()["title"]) == bridge.MAX_TITLE


def test_invalid_json_is_reported():
    bridge.status_path().write_text("{not json", encoding="utf-8")
    with pytest.raises(bridge.BridgeError):
        bridge.read_status()


def test_oversized_status_is_rejected():
    bridge.status_path().write_text(json.dumps({"pad": "x" * 100000}), encoding="utf-8")
    with pytest.raises(bridge.BridgeError):
        bridge.read_status()


def test_status_file_cannot_carry_arbitrary_text():
    # The status file must never carry typed text or screenshots.
    with pytest.raises(ValueError):
        bridge.publish(action="type", state="active", operation="typed secret")
    bridge.publish(action="type", state="active", operation="press")
    raw = bridge.status_path().read_text(encoding="utf-8")
    assert "secret" not in raw
    assert bridge.read_status()["operation"] == "press"


def test_clear_removes_the_file():
    bridge.publish(action="click", state="active")
    bridge.clear()
    assert not bridge.status_path().exists()


def test_default_path_follows_xdg_data_home(monkeypatch, tmp_path):
    monkeypatch.delenv("ISTAR_CONTROL_STATUS_PATH", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert (
        bridge.status_path() == tmp_path / "opencode" / "istar-control" / "status.json"
    )
