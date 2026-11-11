"""Owned, ephemeral Playwright browser session; no existing profile is used."""

import asyncio
from urllib.parse import urlsplit


class BrowserError(RuntimeError):
    """An operation failed without exposing browser or page details."""


def _http_url(value: str) -> bool:
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if any(ord(char) < 33 or ord(char) == 127 for char in value):
        return False
    try:
        url = urlsplit(value)
        return (
            url.scheme.lower() in {"http", "https"}
            and bool(url.hostname)
            and url.username is None
            and url.password is None
            and (url.port is None or 0 < url.port < 65536)
        )
    except ValueError:
        return False


def _selector(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("A non-empty selector is required")
    return value


class BrowserSession:
    """An independently launched browser and fresh, in-memory context.

    Snapshot and screenshot contain page data and must be treated as sensitive.
    This is not a network sandbox: HTTP(S), including local addresses, is allowed.
    """

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._opening = False
        self._cleanup_task = None

    async def open(self, url: str, *, headless: bool = True) -> None:
        if not _http_url(url):
            raise ValueError("Only HTTP(S) URLs without credentials are allowed")
        if not isinstance(headless, bool):
            raise ValueError("headless must be a boolean")
        if self._opening or any(
            obj is not None
            for obj in (
                self._playwright,
                self._browser,
                self._context,
                self._cleanup_task,
            )
        ):
            raise BrowserError("Session is already open")
        self._opening = True
        try:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=headless)
            self._context = await self._browser.new_context(
                service_workers="block", accept_downloads=False
            )
            await self._context.route("**/*", self._filter_request)
            self._page = await self._context.new_page()
            await self._page.goto(url, wait_until="domcontentloaded")
        except BaseException as error:
            try:
                await self.close()
            except BrowserError:
                pass
            if not isinstance(error, Exception):
                raise
            raise BrowserError("Failed to open browser session") from None
        finally:
            self._opening = False

    @staticmethod
    async def _filter_request(route) -> None:
        if _http_url(route.request.url):
            await route.continue_()
        else:
            await route.abort()

    def _active_page(self):
        if self._page is None:
            raise BrowserError("Session is not open")
        return self._page

    async def navigate(self, url: str) -> None:
        if not _http_url(url):
            raise ValueError("Only HTTP(S) URLs without credentials are allowed")
        page = self._active_page()
        try:
            await page.goto(url, wait_until="domcontentloaded")
        except Exception:
            raise BrowserError("Navigation failed") from None

    async def back(self) -> None:
        page = self._active_page()
        try:
            await page.go_back(wait_until="domcontentloaded", timeout=5000)
        except Exception:
            raise BrowserError("Back navigation failed") from None

    async def aria_snapshot(
        self, selector: str = "body", *, max_chars: int = 4000
    ) -> str:
        selector = _selector(selector)
        if (
            isinstance(max_chars, bool)
            or not isinstance(max_chars, int)
            or not 1 <= max_chars <= 20000
        ):
            raise ValueError("max_chars must be an integer between 1 and 20000")
        page = self._active_page()
        try:
            return (await page.locator(selector).aria_snapshot(timeout=5000))[
                :max_chars
            ]
        except Exception:
            raise BrowserError("ARIA snapshot failed") from None

    async def press(self, key: str) -> None:
        if (
            not isinstance(key, str)
            or not key
            or len(key) > 100
            or any(ord(c) < 32 or ord(c) == 127 for c in key)
        ):
            raise ValueError(
                "key must be a non-empty key expression of at most 100 characters"
            )
        page = self._active_page()
        try:
            await page.keyboard.press(key)
        except Exception:
            raise BrowserError("Key press failed") from None

    async def scroll(self, delta_x: int = 0, delta_y: int = 0) -> None:
        if any(
            isinstance(n, bool) or not isinstance(n, int) or not -2000 <= n <= 2000
            for n in (delta_x, delta_y)
        ):
            raise ValueError("scroll deltas must be integers between -2000 and 2000")
        page = self._active_page()
        try:
            await page.mouse.wheel(delta_x, delta_y)
        except Exception:
            raise BrowserError("Scroll failed") from None

    async def scroll_into_view(self, selector: str) -> None:
        selector = _selector(selector)
        page = self._active_page()
        try:
            await page.locator(selector).scroll_into_view_if_needed(timeout=5000)
        except Exception:
            raise BrowserError("Scroll into view failed") from None

    async def snapshot(self, *, max_chars: int = 4000) -> str:
        if (
            isinstance(max_chars, bool)
            or not isinstance(max_chars, int)
            or not 1 <= max_chars <= 20000
        ):
            raise ValueError("max_chars must be an integer between 1 and 20000")
        page = self._active_page()
        try:
            text = await page.locator("body").inner_text(timeout=5000)
            return " ".join(text.split())[:max_chars]
        except Exception:
            raise BrowserError("Snapshot failed") from None

    async def screenshot(self) -> bytes:
        page = self._active_page()
        try:
            return await page.screenshot(type="png")
        except Exception:
            raise BrowserError("Screenshot failed") from None

    async def click(self, selector: str) -> None:
        selector = _selector(selector)
        page = self._active_page()
        try:
            await page.locator(selector).click()
        except Exception:
            raise BrowserError("Click failed") from None

    async def fill(self, selector: str, value: str) -> None:
        selector = _selector(selector)
        if not isinstance(value, str):
            raise ValueError("value must be a string")
        page = self._active_page()
        try:
            await page.locator(selector).fill(value)
        except Exception:
            raise BrowserError("Fill failed") from None

    async def close(self) -> None:
        self._page = None
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup())
        task = self._cleanup_task
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        failed = task.result()
        if self._cleanup_task is task:
            self._cleanup_task = None
        if cancelled:
            raise asyncio.CancelledError
        if failed:
            raise BrowserError("Session cleanup failed") from None

    async def _cleanup(self) -> bool:
        failed = False
        for attribute, method in (
            ("_context", "close"),
            ("_browser", "close"),
            ("_playwright", "stop"),
        ):
            obj = getattr(self, attribute)
            if obj is not None:
                try:
                    await getattr(obj, method)()
                except Exception:
                    failed = True
                else:
                    setattr(self, attribute, None)
        return failed
