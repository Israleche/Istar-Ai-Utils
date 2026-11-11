"""Optional, isolated exact-HWND Windows Graphics Capture (not a screen crop)."""

import ctypes
import io
import math
import subprocess
import sys
from importlib import metadata


class WgcError(Exception):
    """A sanitized capture failure."""


_MAX_PIXELS = 16_000_000
_MAX_PNG = 65_000_000
_PNG = b"\x89PNG\r\n\x1a\n"


def capture_window(hwnd: int, *, timeout: float = 5.0) -> bytes:
    """Return one PNG of an exact window handle, or raise WgcError.

    The timeout covers startup, frame arrival, conversion and process cleanup.
    HWNDs may be recycled by Windows; callers must confirm target identity.
    Protected or minimized windows may yield blank or incomplete content.
    No monitor capture or screen-crop fallback is attempted.
    """
    if (
        type(hwnd) is not int
        or not 0 < hwnd < (1 << (ctypes.sizeof(ctypes.c_void_p) * 8 - 1))
        or isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or not 0 < timeout <= 15
    ):
        raise WgcError("Expected a positive HWND and timeout in (0, 15] seconds")
    if sys.platform != "win32":
        raise WgcError("Windows Graphics Capture requires Windows")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "istar_control.wgc", str(hwnd)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        raise WgcError("Windows Graphics Capture timed out") from None
    except OSError:
        raise WgcError("Unable to start capture worker") from None
    if result.returncode == 2:
        raise WgcError("Install compatible windows-capture==2.0.1 for this Python architecture")
    if (
        result.returncode != 0
        or not result.stdout.startswith(_PNG)
        or len(result.stdout) > _MAX_PNG
    ):
        raise WgcError("Windows Graphics Capture failed")
    return result.stdout


def _capture_one(hwnd: int) -> bytes:
    # Pin the version whose native source uses window_hwnd -> Window::from_raw_hwnd.
    try:
        if metadata.version("windows-capture") != "2.0.1":
            raise ImportError
        from windows_capture import WindowsCapture
    except (ImportError, OSError, metadata.PackageNotFoundError):
        raise ImportError("Optional capture dependency unavailable") from None

    from PIL import Image

    capture = WindowsCapture(
        window_hwnd=hwnd,
        window_name=None,
        monitor_index=None,
        cursor_capture=False,
        secondary_window=False,
    )
    frames = []

    @capture.event
    def on_frame_arrived(frame, control):
        try:
            width, height = frame.width, frame.height
            if (
                type(width) is not int
                or type(height) is not int
                or width <= 0
                or height <= 0
                or width * height > _MAX_PIXELS
            ):
                raise ValueError("Invalid frame dimensions")
            # Copy while native mapped memory is valid, before stopping the session.
            frames.append((width, height, frame.frame_buffer.tobytes()))
        finally:
            control.stop()

    @capture.event
    def on_closed():
        pass

    # Synchronous start owns and cleans up native capture before returning. The
    # parent kills the entire worker if startup or shutdown ever blocks.
    capture.start()
    if len(frames) != 1:
        raise RuntimeError("No frame")
    width, height, pixels = frames.pop()
    if len(pixels) != width * height * 4:
        raise ValueError("Invalid frame buffer")
    image = Image.frombytes("RGBA", (width, height), pixels, "raw", "BGRA")
    output = io.BytesIO()
    image.save(output, format="PNG")
    data = output.getvalue()
    if len(data) > _MAX_PNG:
        raise ValueError("Frame too large")
    return data


def _main() -> int:
    try:
        data = _capture_one(int(sys.argv[1]))
    except ImportError:
        return 2
    except Exception:
        return 1
    sys.stdout.buffer.write(data)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
