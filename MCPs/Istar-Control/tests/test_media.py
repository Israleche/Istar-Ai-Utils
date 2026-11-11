import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from istar_control.media import (
    MediaProbeError,
    audio_profile,
    audio_statistics,
    compare_frames,
    extract_frame,
    probe_media,
)


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "artifact.mp4"
        self.path.touch()

    def process(self, output, returncode=0):
        process = Mock()
        process.stdout = io.BytesIO(output)
        process.returncode = returncode
        return process

    def test_metadata_is_allowlisted_and_command_is_restricted(self):
        data = {
            "format": {"duration": "1.250", "filename": "secret"},
            "streams": [
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 64,
                    "height": 32,
                    "avg_frame_rate": "24/1",
                    "tags": {"secret": "secret"},
                },
                {
                    "index": 1,
                    "codec_type": "audio",
                    "sample_rate": "48000",
                    "channels": 2,
                },
                {"codec_type": "subtitle", "codec_name": "secret"},
            ],
        }
        process = self.process(json.dumps(data).encode())
        with patch(
            "istar_control.media.subprocess.Popen", return_value=process
        ) as popen:
            result = probe_media(self.path)
        args, kwargs = popen.call_args
        self.assertEqual(args[0][args[0].index("-protocol_whitelist") + 1], "file,pipe")
        self.assertEqual(args[0][-1], str(self.path.resolve()))
        self.assertFalse(kwargs["shell"])
        self.assertEqual(result["duration"], "1.250")
        self.assertEqual(len(result["streams"]), 2)
        self.assertNotIn("secret", str(result))
        self.assertEqual(result["streams"][0]["width"], 64)

    def test_rejects_protocols_missing_file_and_directory(self):
        for path in (
            "https://user:secret@example.com/a",
            "file:foo",
            "pipe:0",
            "concat:a|b",
            "\\\\server\\share\\file",
            str(self.path.parent),
            str(self.path.parent / "missing"),
        ):
            with (
                self.subTest(path=path),
                patch("istar_control.media.subprocess.Popen") as popen,
            ):
                with self.assertRaises(MediaProbeError) as error:
                    probe_media(path)
                self.assertNotIn("secret", str(error.exception))
                popen.assert_not_called()

    def test_errors_are_sanitized_and_output_bounded(self):
        for output, code in (
            (b"secret", 1),
            (b"not json secret", 0),
            (b"x" * (256 * 1024 + 1), 0),
        ):
            with (
                self.subTest(code=code, length=len(output)),
                patch(
                    "istar_control.media.subprocess.Popen",
                    return_value=self.process(output, code),
                ),
            ):
                with self.assertRaises(MediaProbeError) as error:
                    probe_media(self.path)
                self.assertNotIn("secret", str(error.exception))

    def test_timeout_kills_process(self):
        process = self.process(b"")
        process.wait.side_effect = [subprocess.TimeoutExpired("secret", 1), 0]
        with patch("istar_control.media.subprocess.Popen", return_value=process):
            with self.assertRaisesRegex(MediaProbeError, "timed out"):
                probe_media(self.path, timeout=1)
        process.kill.assert_called_once()

    def test_decode_validation_and_failures(self):
        for call in (
            lambda: extract_frame("https://example.org/x"),
            lambda: extract_frame(self.path, float("nan")),
            lambda: extract_frame(self.path, timeout=31),
            lambda: audio_statistics(self.path, duration=11),
            lambda: audio_statistics(self.path, silence_db=1),
        ):
            with (
                self.subTest(call=call),
                patch("istar_control.media.subprocess.Popen") as popen,
            ):
                with self.assertRaises(MediaProbeError):
                    call()
                popen.assert_not_called()
        for output, code in (
            (b"secret", 1),
            (b"", 0),
            (b"bad", 0),
            (b"x" * (5 * 1024 * 1024 + 1), 0),
        ):
            with patch(
                "istar_control.media.subprocess.Popen",
                return_value=self.process(output, code),
            ) as popen:
                with self.assertRaises(MediaProbeError) as error:
                    extract_frame(self.path)
                self.assertNotIn("secret", str(error.exception))
                command = popen.call_args.args[0]
                self.assertEqual(
                    command[command.index("-protocol_whitelist") + 1], "file,pipe"
                )
                self.assertEqual(command[-1], "pipe:1")
                self.assertFalse(popen.call_args.kwargs["shell"])

    def test_decode_timeout_kills_process(self):
        process = self.process(b"")
        process.wait.side_effect = [subprocess.TimeoutExpired("secret", 1), 0]
        with patch("istar_control.media.subprocess.Popen", return_value=process):
            with self.assertRaisesRegex(MediaProbeError, "timed out"):
                extract_frame(self.path, timeout=1)
        process.kill.assert_called_once()

    @unittest.skipUnless(
        shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg tools unavailable"
    )
    def test_real_generated_audio_video(self):
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=32x24:r=2:d=1",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=1",
                "-c:v",
                "mpeg4",
                "-c:a",
                "aac",
                "-y",
                str(self.path),
            ],
            check=True,
            timeout=30,
            stderr=subprocess.PIPE,
        )
        result = probe_media(self.path)
        self.assertAlmostEqual(float(result["duration"]), 1.0, delta=0.2)
        video = next(s for s in result["streams"] if s["codec_type"] == "video")
        audio = next(s for s in result["streams"] if s["codec_type"] == "audio")
        self.assertEqual((video["width"], video["height"]), (32, 24))
        self.assertEqual(audio["sample_rate"], "44100")
        from PIL import Image

        frame = extract_frame(self.path, 0.4)
        with Image.open(io.BytesIO(frame)) as image:
            self.assertEqual(image.size, (1024, 768))
        stats = audio_statistics(self.path, duration=0.5)
        self.assertAlmostEqual(stats["duration"], 0.5, delta=0.01)
        self.assertGreater(stats["peak"], 0.05)
        self.assertGreater(stats["rms"], 0.03)
        self.assertLess(stats["silent_sample_fraction"], 0.1)
        with self.assertRaises(MediaProbeError):
            extract_frame(self.path, 5)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg unavailable")
    def test_real_silence_and_missing_stream(self):
        path = self.path.with_suffix(".wav")
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=8000:cl=mono",
                "-t",
                "0.5",
                "-y",
                str(path),
            ],
            check=True,
            timeout=30,
            stderr=subprocess.PIPE,
        )
        stats = audio_statistics(path, duration=1)
        self.assertEqual(stats["silent_sample_fraction"], 1)
        self.assertEqual(stats["peak"], 0)
        self.assertEqual(stats["rms"], 0)
        with self.assertRaises(MediaProbeError):
            extract_frame(path)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg unavailable")
    def test_real_animated_gif_frames(self):
        from PIL import Image

        path = self.path.with_suffix(".gif")
        first = Image.new("RGB", (16, 16), "red")
        second = Image.new("RGB", (16, 16), "blue")
        first.save(path, save_all=True, append_images=[second], duration=500, loop=0)
        with Image.open(io.BytesIO(extract_frame(path, 0))) as image:
            self.assertEqual(image.convert("RGB").getpixel((0, 0)), (255, 0, 0))
        with Image.open(io.BytesIO(extract_frame(path, 0.5))) as image:
            self.assertEqual(image.convert("RGB").getpixel((0, 0)), (0, 0, 255))

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg unavailable")
    def test_real_compare_frames_detects_color_change(self):
        from PIL import Image

        path = self.path.with_suffix(".gif")
        red = Image.new("RGB", (32, 32), "red")
        blue = Image.new("RGB", (32, 32), "blue")
        red.save(path, save_all=True, append_images=[blue], duration=500, loop=0)
        result = compare_frames(path, 0, 0.5)
        self.assertFalse(result["identical"])
        self.assertGreater(result["changed_pixel_fraction"], 0.9)
        self.assertIsNotNone(result["change_bbox"])
        identical = compare_frames(path, 0, 0)
        self.assertTrue(identical["identical"])

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg unavailable")
    def test_real_audio_profile_describes_sine_tone(self):
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=8000:duration=0.5",
                "-y",
                str(self.path.with_suffix(".wav")),
            ],
            check=True,
            timeout=30,
            stderr=subprocess.PIPE,
        )
        stats = audio_profile(self.path.with_suffix(".wav"), duration=0.5)
        self.assertEqual(stats["sample_rate"], 8000)
        self.assertGreater(stats["peak"], 0.1)
        self.assertGreater(stats["zero_crossing_rate"], 0.01)
        self.assertLess(abs(stats["dc_offset"]), 0.1)


if __name__ == "__main__":
    unittest.main()
