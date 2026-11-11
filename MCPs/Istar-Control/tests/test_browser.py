import asyncio
import http.server
import sys
import threading
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from istar_control.browser import BrowserError, BrowserSession, _http_url


class BrowserTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session = BrowserSession()
        self.page = MagicMock()
        self.page.goto = AsyncMock()
        self.page.screenshot = AsyncMock(return_value=b"png")
        self.locator = MagicMock()
        self.locator.inner_text = AsyncMock(return_value="  Hello\n world  ")
        self.locator.click = AsyncMock()
        self.locator.fill = AsyncMock()
        self.page.locator.return_value = self.locator
        self.context = MagicMock()
        self.context.route = AsyncMock()
        self.context.new_page = AsyncMock(return_value=self.page)
        self.context.close = AsyncMock()
        self.browser = MagicMock()
        self.browser.new_context = AsyncMock(return_value=self.context)
        self.browser.close = AsyncMock()
        self.playwright = MagicMock()
        self.playwright.chromium.launch = AsyncMock(return_value=self.browser)
        self.playwright.stop = AsyncMock()
        manager = MagicMock()
        manager.start = AsyncMock(return_value=self.playwright)
        api = types.ModuleType("playwright.async_api")
        api.async_playwright = lambda: manager
        package = types.ModuleType("playwright")
        self.modules = patch.dict(sys.modules, {"playwright": package, "playwright.async_api": api})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    async def test_owned_lifecycle_and_operations(self):
        await self.session.open("https://example.test/")
        self.playwright.chromium.launch.assert_awaited_once_with(headless=True)
        self.browser.new_context.assert_awaited_once_with(
            service_workers="block", accept_downloads=False
        )
        self.context.route.assert_awaited_once()
        await self.session.navigate("http://example.test/next")
        self.assertEqual(await self.session.snapshot(max_chars=5), "Hello")
        self.assertEqual(await self.session.screenshot(), b"png")
        await self.session.click("#button")
        self.locator.click.assert_awaited_once()
        await self.session.fill("#input", "text")
        self.locator.fill.assert_awaited_once_with("text")
        await self.session.close()
        await self.session.close()
        self.context.close.assert_awaited_once()
        self.browser.close.assert_awaited_once()
        self.playwright.stop.assert_awaited_once()

    async def test_validation_and_interception(self):
        for url in (
            "file:///tmp/x",
            "javascript:alert(1)",
            "https://user:pass@host/",
            "http://",
            "https://a:99999",
            " https://a",
            "https://a\n",
        ):
            self.assertFalse(_http_url(url), url)
            with self.assertRaises(ValueError):
                await self.session.open(url)
        for url, allowed in (
            ("https://example.test/", True),
            ("file:///secret", False),
        ):
            route = MagicMock()
            route.request.url = url
            route.continue_ = AsyncMock()
            route.abort = AsyncMock()
            await self.session._filter_request(route)
            (route.continue_ if allowed else route.abort).assert_awaited_once()
        with self.assertRaises(BrowserError):
            await self.session.snapshot()
        with self.assertRaises(ValueError):
            await self.session.click(" ")
        with self.assertRaises(ValueError):
            await self.session.fill("#x", None)
        with self.assertRaises(ValueError):
            await self.session.snapshot(max_chars=True)

    async def test_extended_operations_and_validation(self):
        await self.session.open("https://example.test/")
        self.page.go_back = AsyncMock()
        self.page.keyboard.press = AsyncMock()
        self.page.mouse.wheel = AsyncMock()
        self.locator.aria_snapshot = AsyncMock(return_value="- button: Submit")
        self.locator.scroll_into_view_if_needed = AsyncMock()
        self.assertEqual(await self.session.aria_snapshot("#form", max_chars=8), "- button")
        self.locator.aria_snapshot.assert_awaited_once_with(timeout=5000)
        await self.session.press("Control+A")
        self.page.keyboard.press.assert_awaited_once_with("Control+A")
        await self.session.scroll(-100, 200)
        self.page.mouse.wheel.assert_awaited_once_with(-100, 200)
        await self.session.scroll_into_view("#form")
        self.locator.scroll_into_view_if_needed.assert_awaited_once_with(timeout=5000)
        await self.session.back()
        self.page.go_back.assert_awaited_once_with(wait_until="domcontentloaded", timeout=5000)
        for invalid in (True, 0, 20001):
            with self.assertRaises(ValueError):
                await self.session.aria_snapshot(max_chars=invalid)
        for invalid in (True, 2001, -2001, 1.5):
            with self.assertRaises(ValueError):
                await self.session.scroll(delta_y=invalid)
        for invalid in ("", "x\n", "x" * 101, None):
            with self.assertRaises(ValueError):
                await self.session.press(invalid)
        self.locator.aria_snapshot.side_effect = RuntimeError("private")
        with self.assertRaisesRegex(BrowserError, "^ARIA snapshot failed$"):
            await self.session.aria_snapshot()
        await self.session.close()

    async def test_failed_open_cleans_up_and_hides_exception(self):
        self.page.goto.side_effect = RuntimeError("secret url")
        with self.assertRaises(BrowserError) as error:
            await self.session.open("https://example.test/")
        self.assertNotIn("secret", str(error.exception))
        self.context.close.assert_awaited_once()
        self.browser.close.assert_awaited_once()
        self.playwright.stop.assert_awaited_once()

    async def test_cleanup_continues_after_failure(self):
        await self.session.open("https://example.test/")
        self.context.close.side_effect = RuntimeError("secret")
        with self.assertRaises(BrowserError) as error:
            await self.session.close()
        self.assertNotIn("secret", str(error.exception))
        self.browser.close.assert_awaited_once()
        self.playwright.stop.assert_awaited_once()

    async def test_open_failure_remains_clear_when_cleanup_fails(self):
        self.page.goto.side_effect = RuntimeError("secret url")
        self.context.close.side_effect = RuntimeError("secret cleanup")
        with self.assertRaisesRegex(BrowserError, "^Failed to open browser session$"):
            await self.session.open("https://example.test/")
        self.browser.close.assert_awaited_once()
        self.playwright.stop.assert_awaited_once()

    async def test_cancelled_open_cleans_up(self):
        self.page.goto.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.session.open("https://example.test/")
        self.context.close.assert_awaited_once()
        self.browser.close.assert_awaited_once()
        self.playwright.stop.assert_awaited_once()

    async def test_cancel_at_each_cleanup_step_is_deferred(self):
        for step in ("context", "browser", "playwright"):
            with self.subTest(step=step):
                session = BrowserSession()
                objects = {name: MagicMock() for name in ("context", "browser", "playwright")}
                started, release = asyncio.Event(), asyncio.Event()

                async def blocked(_started=started, _release=release):
                    _started.set()
                    await _release.wait()

                for name, obj in objects.items():
                    method = "stop" if name == "playwright" else "close"
                    setattr(
                        obj,
                        method,
                        AsyncMock(side_effect=blocked if name == step else None),
                    )
                    setattr(session, "_" + name, obj)
                task = asyncio.create_task(session.close())
                await started.wait()
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                self.assertIs(getattr(session, "_" + step), objects[step])
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                for name, obj in objects.items():
                    method = "stop" if name == "playwright" else "close"
                    getattr(obj, method).assert_awaited_once()
                    self.assertIsNone(getattr(session, "_" + name))
                await session.close()

    async def test_cleanup_failures_retain_handles_for_retry(self):
        await self.session.open("https://example.test/")
        self.context.close.side_effect = [RuntimeError("private"), None]
        self.browser.close.side_effect = [RuntimeError("private"), None]
        self.playwright.stop.side_effect = [RuntimeError("private"), None]
        with self.assertRaisesRegex(BrowserError, "^Session cleanup failed$"):
            await self.session.close()
        self.assertIs(self.session._context, self.context)
        self.assertIs(self.session._browser, self.browser)
        self.assertIs(self.session._playwright, self.playwright)
        with self.assertRaises(BrowserError):
            await self.session.open("https://example.test/")
        await self.session.close()
        self.assertIsNone(self.session._context)
        self.assertIsNone(self.session._browser)
        self.assertIsNone(self.session._playwright)

    async def test_cancel_with_cleanup_error_retains_handle(self):
        await self.session.open("https://example.test/")
        started, release = asyncio.Event(), asyncio.Event()

        async def fail_after_release():
            started.set()
            await release.wait()
            raise RuntimeError("private")

        self.context.close.side_effect = fail_after_release
        task = asyncio.create_task(self.session.close())
        await started.wait()
        task.cancel()
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIs(self.session._context, self.context)
        self.context.close.side_effect = None
        await self.session.close()
        self.assertIsNone(self.session._context)


class BrowserIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_page_operations(self):
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.skipTest("Playwright is not installed")
        async with async_playwright() as playwright:
            try:
                browser = await playwright.chromium.launch()
            except Exception as error:
                if "Executable doesn't exist" in str(error):
                    self.skipTest("Playwright Chromium headless shell is not installed")
                raise
            await browser.close()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = (
                    b'<html><body><h1>Local page</h1><input aria-label="Name">'
                    b'<a href="/next">Next</a><div style="height:2000px"></div>'
                    b"<button>End</button></body></html>"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        session = BrowserSession()
        try:
            url = f"http://127.0.0.1:{server.server_port}/"
            await session.open(url)
            self.assertIn('textbox "Name"', await session.aria_snapshot())
            await session.click("input")
            await session.press("A")
            self.assertEqual(await session._page.locator("input").input_value(), "A")
            await session.scroll(delta_y=500)
            await session.scroll_into_view("button")
            self.assertGreater(await session._page.evaluate("window.scrollY"), 500)
            await session.navigate(url + "next")
            await session.back()
            self.assertEqual(session._page.url, url)
        finally:
            await session.close()
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
