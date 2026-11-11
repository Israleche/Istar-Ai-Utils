import asyncio
import base64
import io
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image as PILImage

from istar_control import guard as guard_module
from istar_control import server

_ORIGINAL_WINDOW_IDENTITY = guard_module.Guard._window_identity


class StdioTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        os.environ["ISTAR_CONTROL_AUTO_APPROVE"] = "1"
        os.environ["ISTAR_CONTROL_DATA_DIR"] = tempfile.mkdtemp(
            prefix="istar-control-test-"
        )
        guard_module.reset_guard()
        guard_module.Guard._window_identity = staticmethod(
            lambda hwnd: (4242, "Test Window", "test.exe")
        )

    def tearDown(self):
        guard_module.Guard._window_identity = _ORIGINAL_WINDOW_IDENTITY
        os.environ.pop("ISTAR_CONTROL_AUTO_APPROVE", None)
        os.environ.pop("ISTAR_CONTROL_DATA_DIR", None)
        guard_module.reset_guard()

    async def test_desktop_worker_does_not_block_loop_or_unlock_on_cancel(self):
        started = threading.Event()
        release = threading.Event()
        calls = []

        def hold():
            started.set()
            release.wait(2)
            calls.append("released")

        first = asyncio.create_task(server._desktop_call(hold))
        self.assertTrue(await asyncio.to_thread(started.wait, 1))
        first.cancel()
        second = asyncio.create_task(
            server._desktop_call(lambda: calls.append("second"))
        )
        await asyncio.sleep(0.02)
        self.assertEqual(calls, [])
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await first
        await second
        self.assertEqual(calls, ["released", "second"])

    async def test_stdio_image_and_targeted_actions(self):
        # Stub OS input in the child, while exercising the actual MCP transport.
        script = """
import os
import tempfile
os.environ["ISTAR_CONTROL_AUTO_APPROVE"] = "1"
os.environ["ISTAR_CONTROL_DATA_DIR"] = tempfile.mkdtemp(prefix="istar-control-child-")
from PIL import Image
from istar_control import server
from istar_control import guard as guard_module
guard_module.Guard._window_identity = staticmethod(lambda hwnd: (4242, "Test Window", "test.exe"))
class Desktop:
    def focus_window(self, hwnd):
        assert hwnd == 42
    def press_key(self, hwnd, key, duration):
        assert (hwnd, key, duration) == (42, "A", 0.05)
    def press_keys(self, hwnd, keys, duration):
        assert (hwnd, keys, duration) == (42, ["CTRL", "A"], 0.05)
    def screenshot(self, hwnd):
        assert hwnd == 42
        return Image.new("RGB", (2, 3))
server.Desktop = Desktop
server.show_indicator = lambda *args, **kwargs: type("Indicator", (), {"complete": lambda self: None, "close": lambda self: None})()
server.main()
"""
        params = StdioServerParameters(
            command=sys.executable,
            args=["-c", script],
            env={
                "ISTAR_CONTROL_AUTO_APPROVE": "1",
                "ISTAR_CONTROL_DATA_DIR": tempfile.mkdtemp(
                    prefix="istar-control-child-"
                ),
            },
        )
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                for name, args in (
                    ("focus_window", {"hwnd": 42}),
                    ("desktop_press_key", {"hwnd": 42, "key": "A"}),
                    ("desktop_press_keys", {"hwnd": 42, "keys": ["CTRL", "A"]}),
                ):
                    result = await session.call_tool(name, args)
                    self.assertFalse(result.isError)
                result = await session.call_tool("desktop_screenshot", {"hwnd": 42})
                self.assertFalse(result.isError)
                image = result.content[0]
                self.assertEqual((image.type, image.mimeType), ("image", "image/png"))
                self.assertEqual(
                    PILImage.open(io.BytesIO(base64.b64decode(image.data))).size, (2, 3)
                )

    async def test_initialize_list_and_call_errors(self):
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "istar_control.server"]
        )
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                initialized = await session.initialize()
                self.assertEqual(initialized.serverInfo.name, "Istar-Control")
                self.assertIn("physical global", initialized.instructions)
                resource = await session.read_resource("istar-control://guide")
                self.assertIn("focus", resource.contents[0].text.lower())
                prompts = await session.list_prompts()
                self.assertIn(
                    "computer_use_workflow", {p.name for p in prompts.prompts}
                )
                listed = await session.list_tools()
                names = {tool.name for tool in listed.tools}
                self.assertTrue(
                    {
                        "list_windows",
                        "focus_window",
                        "desktop_screenshot",
                        "desktop_click",
                        "desktop_press_key",
                        "desktop_press_keys",
                        "desktop_show_indicator",
                        "desktop_capture_window",
                        "desktop_move",
                        "desktop_scroll",
                        "desktop_drag",
                        "inspect_media",
                        "media_frame",
                        "media_audio_statistics",
                        "browser_aria_snapshot",
                        "browser_press",
                        "browser_back",
                        "browser_scroll",
                        "desktop_type_text",
                        "browser_open",
                        "browser_close",
                        "browser_screenshot",
                        "control_request",
                        "control_revoke",
                        "control_stop",
                        "control_resume",
                        "control_status",
                        "uia_observe",
                        "uia_invoke",
                        "uia_set_value",
                        "uia_toggle",
                        "media_compare_frames",
                        "media_audio_profile",
                    }
                    <= names
                )
                result = await session.call_tool("browser_close", {})
                self.assertFalse(result.isError)
                self.assertIn("closed", result.content[0].text)
                result = await session.call_tool("browser_snapshot", {})
                self.assertTrue(result.isError)
                self.assertIn("not open", result.content[0].text)
                result = await session.call_tool(
                    "browser_open", {"url": "file:///secret"}
                )
                self.assertTrue(result.isError)
                self.assertIn("HTTP(S)", result.content[0].text)
                result = await session.call_tool("desktop_click", {"hwnd": 1})
                self.assertTrue(result.isError)

    async def test_screenshots_and_shutdown(self):
        desktop = MagicMock()
        desktop.screenshot.return_value = PILImage.new("RGB", (1, 1))
        browser = MagicMock()
        browser.screenshot = AsyncMock(return_value=b"png bytes")
        browser.close = AsyncMock()
        with (
            patch.object(server, "Desktop", return_value=desktop),
            patch.object(server, "browser", browser),
        ):
            image = await server.desktop_screenshot(12)
            self.assertEqual(PILImage.open(io.BytesIO(image.data)).size, (1, 1))
            desktop.screenshot.assert_called_once_with(12)
            result = await server.server.call_tool("browser_screenshot", {})
            self.assertEqual(result[0].type, "image")
            self.assertEqual(result[0].mimeType, "image/png")
            self.assertEqual(base64.b64decode(result[0].data), b"png bytes")
            async with server.lifespan(server.server):
                pass
            browser.close.assert_awaited_once()
