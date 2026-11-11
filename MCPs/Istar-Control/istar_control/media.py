"""Read-only, bounded metadata inspection of local media artifacts."""

import array
import json
import math
import os
import re
import subprocess
import sys
import threading
from pathlib import Path


class MediaProbeError(Exception):
    """A sanitized media-probing failure."""


_MAX_OUTPUT = 256 * 1024
_ENTRIES = (
    "format=duration:stream=index,codec_type,codec_name,width,height,"
    "avg_frame_rate,sample_rate,channels,duration"
)


def _local_file(path, timeout):
    try:
        name = os.fspath(path)
        if not isinstance(name, str) or not name or "\0" in name:
            raise ValueError
        if name.startswith(("\\\\", "//")) or (
            re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", name)
            and not re.match(r"^[a-zA-Z]:[\\/]", name)
        ):
            raise ValueError
        target = Path(name).resolve(strict=True)
        if not target.is_file() or str(target).startswith(("\\\\", "//")):
            raise ValueError
        if (
            not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or not 0 < timeout <= 30
        ):
            raise ValueError
    except (TypeError, ValueError, OSError, RuntimeError):
        raise MediaProbeError(
            "Expected a local regular file and a timeout in (0, 30] seconds"
        ) from None
    return target


def probe_media(path: str | os.PathLike[str], *, timeout: float = 10.0) -> dict:
    """Return local media metadata, not semantic quality.

    Protocol restrictions are not a sandbox: manifests may access local files,
    mapped network drives and filesystem races are not detected.
    """
    target = _local_file(path, timeout)

    command = [
        "ffprobe",
        "-v",
        "error",
        "-protocol_whitelist",
        "file,pipe",
        "-show_entries",
        _ENTRIES,
        "-of",
        "json",
        str(target),
    ]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            env={k: v for k, v in os.environ.items() if k.upper() != "FFREPORT"},
        )
    except OSError:
        raise MediaProbeError("Unable to start ffprobe") from None

    chunks = []

    def read_output():
        try:
            chunks.append(process.stdout.read(_MAX_OUTPUT + 1))
        except OSError:
            pass

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        reader.join()
        raise MediaProbeError("Media probe timed out") from None
    reader.join()
    if not chunks or len(chunks[0]) > _MAX_OUTPUT:
        raise MediaProbeError("Media probe output exceeded limit or could not be read")
    if process.returncode:
        raise MediaProbeError("Media probe failed")
    try:
        data = json.loads(chunks[0])
        streams = data.get("streams", [])
        if not isinstance(streams, list) or not isinstance(
            data.get("format", {}), dict
        ):
            raise ValueError
        result_streams = []
        for stream in streams:
            if not isinstance(stream, dict) or stream.get("codec_type") not in (
                "video",
                "audio",
            ):
                continue
            result = {}
            for key in ("index", "width", "height", "channels"):
                value = stream.get(key)
                if type(value) is int and 0 <= value <= 2**31 - 1:
                    result[key] = value
            for key in (
                "codec_type",
                "codec_name",
                "avg_frame_rate",
                "sample_rate",
                "duration",
            ):
                value = stream.get(key)
                if isinstance(value, str) and re.fullmatch(
                    r"[A-Za-z0-9_./-]{1,64}", value
                ):
                    result[key] = value
            result_streams.append(result)
        duration = data.get("format", {}).get("duration")
        if not isinstance(duration, str) or not re.fullmatch(r"[0-9.]{1,64}", duration):
            duration = None
        return {"duration": duration, "streams": result_streams}
    except (ValueError, TypeError, AttributeError, UnicodeDecodeError):
        raise MediaProbeError("Invalid media probe output") from None


def _number(value, minimum, maximum, name):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise MediaProbeError(f"Invalid {name}")
    return value


def _decode(path, timeout, options, limit):
    target = _local_file(path, timeout)
    command = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-protocol_whitelist",
        "file,pipe",
        "-i",
        str(target),
        *options,
        "pipe:1",
    ]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            env={k: v for k, v in os.environ.items() if k.upper() != "FFREPORT"},
        )
    except OSError:
        raise MediaProbeError("Unable to start ffmpeg") from None
    chunks = []

    def read_output():
        try:
            chunks.append(process.stdout.read(limit + 1))
        except OSError:
            pass

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        reader.join()
        raise MediaProbeError("Media decode timed out") from None
    reader.join()
    if not chunks or len(chunks[0]) > limit:
        raise MediaProbeError("Media decode output exceeded limit or could not be read")
    if process.returncode:
        raise MediaProbeError("Media decode failed")
    if not chunks[0]:
        raise MediaProbeError("No media samples at requested time")
    return chunks[0]


def extract_frame(
    path: str | os.PathLike[str], timestamp: float = 0, *, timeout: float = 10.0
) -> bytes:
    """Return one scaled PNG frame at or after timestamp, not a quality judgment.

    Inputs have the same manifest, mapped-drive and race risks as probe_media.
    """
    timestamp = _number(timestamp, 0, 86400, "timestamp")
    result = _decode(
        path,
        timeout,
        [
            "-ss",
            str(timestamp),
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-vf",
            "scale=w=1024:h=1024:force_original_aspect_ratio=decrease",
            "-c:v",
            "png",
            "-f",
            "image2pipe",
        ],
        5 * 1024 * 1024,
    )
    if not result.startswith(b"\x89PNG\r\n\x1a\n"):
        raise MediaProbeError("Invalid frame output")
    return result


def audio_statistics(
    path: str | os.PathLike[str],
    start: float = 0,
    duration: float = 10,
    *,
    silence_db: float = -50,
    timeout: float = 10.0,
) -> dict:
    """Measure mono downmixed 8 kHz PCM: peak/RMS and fraction below threshold.

    Silence is a per-sample amplitude fraction, not silence intervals or a
    perceptual/semantic judgment. Input restrictions are not a sandbox.
    """
    start = _number(start, 0, 86400, "start")
    duration = _number(duration, 0.001, 10, "duration")
    silence_db = _number(silence_db, -120, 0, "silence threshold")
    raw = _decode(
        path,
        timeout,
        [
            "-ss",
            str(start),
            "-t",
            str(duration),
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            "8000",
            "-c:a",
            "pcm_s16le",
            "-f",
            "s16le",
        ],
        160000,
    )
    if len(raw) % 2:
        raise MediaProbeError("Invalid audio sample output")
    samples = array.array("h")
    samples.frombytes(raw)
    if sys.byteorder != "little":
        samples.byteswap()
    peak = max(abs(sample) for sample in samples) / 32768
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples)) / 32768
    threshold = 32768 * 10 ** (silence_db / 20)
    return {
        "sample_rate": 8000,
        "sample_count": len(samples),
        "duration": len(samples) / 8000,
        "peak": peak,
        "rms": rms,
        "silence_threshold_db": silence_db,
        "silent_sample_fraction": sum(abs(s) <= threshold for s in samples)
        / len(samples),
    }


def compare_frames(
    path: str | os.PathLike[str],
    timestamp_a: float = 0,
    timestamp_b: float = 1,
    *,
    timeout: float = 10.0,
) -> dict:
    """Return a structural comparison of two frames of one local video.

    Both frames are decoded with the same bounded settings used by
    ``extract_frame`` and compared on a grayscale difference. This is a
    coarse signal, not a judgment of visible or semantic differences.
    """
    import io

    from PIL import Image, ImageChops

    timestamp_a = _number(timestamp_a, 0, 86400, "timestamp_a")
    timestamp_b = _number(timestamp_b, 0, 86400, "timestamp_b")
    frame_a = extract_frame(path, timestamp_a, timeout=timeout)
    frame_b = extract_frame(path, timestamp_b, timeout=timeout)
    with Image.open(io.BytesIO(frame_a)) as img_a, Image.open(
        io.BytesIO(frame_b)
    ) as img_b:
        gray_a = img_a.convert("L")
        gray_b = img_b.convert("L")
        if gray_a.size != gray_b.size:
            raise MediaProbeError("Frames decode to different sizes")
        diff = ImageChops.difference(gray_a, gray_b)
        width, height = diff.size
        histogram = diff.histogram()
        total = width * height
        weighted = sum(level * count for level, count in enumerate(histogram))
        mean = weighted / total if total else 0.0
        changed = sum(count for level, count in enumerate(histogram) if level > 16)
        bbox = diff.getbbox()
        return {
            "size": [width, height],
            "mean_abs_diff": mean / 255.0,
            "changed_pixel_fraction": changed / total if total else 0.0,
            "change_bbox": list(bbox) if bbox else None,
            "identical": mean == 0,
        }


def audio_profile(
    path: str | os.PathLike[str],
    start: float = 0,
    duration: float = 10,
    *,
    timeout: float = 10.0,
) -> dict:
    """Return bounded descriptive stats for one mono 8 kHz PCM window.

    Not silence-interval detection, speech recognition or quality judgment.
    """
    start = _number(start, 0, 86400, "start")
    duration = _number(duration, 0.001, 10, "duration")
    raw = _decode(
        path,
        timeout,
        [
            "-ss",
            str(start),
            "-t",
            str(duration),
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            "8000",
            "-c:a",
            "pcm_s16le",
            "-f",
            "s16le",
        ],
        160000,
    )
    if len(raw) % 2:
        raise MediaProbeError("Invalid audio sample output")
    samples = array.array("h")
    samples.frombytes(raw)
    if sys.byteorder != "little":
        samples.byteswap()
    n = len(samples)
    half = n // 2
    rms_first = (
        math.sqrt(sum(s * s for s in samples[:half]) / half) / 32768 if half else 0.0
    )
    rms_second = (
        math.sqrt(sum(s * s for s in samples[half:]) / (n - half)) / 32768
        if n - half
        else 0.0
    )
    peak = max(abs(s) for s in samples) / 32768
    rms = math.sqrt(sum(s * s for s in samples) / n) / 32768
    crossings = sum(
        1 for i in range(1, n) if (samples[i - 1] < 0) != (samples[i] < 0)
    )
    return {
        "sample_rate": 8000,
        "sample_count": n,
        "duration": n / 8000,
        "peak": peak,
        "rms": rms,
        "dc_offset": (sum(samples) / n) / 32768 if n else 0.0,
        "zero_crossing_rate": crossings / (n - 1) if n > 1 else 0.0,
        "rms_first_half": rms_first,
        "rms_second_half": rms_second,
        "rms_delta": rms_second - rms_first,
    }
