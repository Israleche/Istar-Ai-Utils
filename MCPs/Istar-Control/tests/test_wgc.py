import io
import subprocess
import sys
import types

import pytest
from PIL import Image

from istar_control import wgc


class Buffer:
    def tobytes(self):
        return bytes([3, 2, 1, 255])


class FakeCapture:
    last = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.handlers = {}
        self.stopped = False
        FakeCapture.last = self

    def event(self, handler):
        self.handlers[handler.__name__] = handler
        return handler

    def start(self):
        control = types.SimpleNamespace(stop=lambda: setattr(self, "stopped", True))
        self.handlers["on_frame_arrived"](
            types.SimpleNamespace(width=1, height=1, frame_buffer=Buffer()), control
        )


@pytest.fixture
def fake_capture(monkeypatch):
    monkeypatch.setattr(wgc.metadata, "version", lambda _: "2.0.1")
    monkeypatch.setitem(
        sys.modules,
        "windows_capture",
        types.SimpleNamespace(WindowsCapture=FakeCapture),
    )


def test_exact_hwnd_and_bgra_conversion(fake_capture):
    image = Image.open(io.BytesIO(wgc._capture_one(123456)))
    assert image.getpixel((0, 0)) == (1, 2, 3, 255)
    assert FakeCapture.last.kwargs == dict(
        window_hwnd=123456,
        window_name=None,
        monitor_index=None,
        cursor_capture=False,
        secondary_window=False,
    )
    assert FakeCapture.last.stopped


def test_stop_on_frame_error(fake_capture, monkeypatch):
    monkeypatch.setattr(
        Buffer, "tobytes", lambda _: (_ for _ in ()).throw(ValueError())
    )
    with pytest.raises(ValueError):
        wgc._capture_one(42)
    assert FakeCapture.last.stopped


def test_missing_or_incompatible_package(monkeypatch):
    monkeypatch.setattr(wgc.metadata, "version", lambda _: "1.0")
    with pytest.raises(ImportError):
        wgc._capture_one(42)


@pytest.mark.parametrize(
    "hwnd,timeout", [(0, 1), (-1, 1), (True, 1), (1, 0), (1, float("nan")), (1, 16)]
)
def test_invalid_arguments(hwnd, timeout):
    with pytest.raises(wgc.WgcError):
        wgc.capture_window(hwnd, timeout=timeout)


def test_worker_success_and_no_fallback(monkeypatch):
    monkeypatch.setattr(wgc.sys, "platform", "win32")
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return types.SimpleNamespace(returncode=0, stdout=wgc._PNG + b"data")

    monkeypatch.setattr(wgc.subprocess, "run", run)
    assert wgc.capture_window(123, timeout=1) == wgc._PNG + b"data"
    assert calls[0][0][-1] == "123"
    assert calls[0][1]["timeout"] == 1
    assert calls[0][1]["stderr"] == subprocess.DEVNULL


@pytest.mark.parametrize("result,message", [(2, "Install compatible"), (1, "failed")])
def test_sanitized_worker_failure(monkeypatch, result, message):
    monkeypatch.setattr(wgc.sys, "platform", "win32")
    monkeypatch.setattr(
        wgc.subprocess,
        "run",
        lambda *a, **k: types.SimpleNamespace(returncode=result, stdout=b"secret"),
    )
    with pytest.raises(wgc.WgcError, match=message) as exc:
        wgc.capture_window(42)
    assert "secret" not in str(exc.value)


def test_timeout(monkeypatch):
    monkeypatch.setattr(wgc.sys, "platform", "win32")

    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired("secret", 1)

    monkeypatch.setattr(wgc.subprocess, "run", timed_out)
    with pytest.raises(wgc.WgcError, match="timed out") as exc:
        wgc.capture_window(42)
    assert "secret" not in str(exc.value)
