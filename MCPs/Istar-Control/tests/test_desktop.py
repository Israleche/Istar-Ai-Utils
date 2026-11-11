import unittest
from unittest.mock import Mock, patch

from istar_control.desktop import _INPUT, Desktop, DesktopError


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.d = Desktop.__new__(Desktop)
        self.u = self.d._user32 = Mock()
        self.u.IsWindow.return_value = True
        self.u.IsWindowVisible.return_value = True
        self.u.IsIconic.return_value = False
        self.u.GetForegroundWindow.return_value = 123
        self.u.MapVirtualKeyW.return_value = 0x4B
        self.d._set_thread_dpi = Mock(return_value=1234)
        self.u.GetSystemMetrics.side_effect = lambda n: {
            76: -100,
            77: -50,
            78: 2000,
            79: 1100,
        }[n]

        def clip(ptr):
            ptr._obj.left, ptr._obj.top, ptr._obj.right, ptr._obj.bottom = (
                -100,
                -50,
                1900,
                1050,
            )
            return True

        self.u.GetClipCursor.side_effect = clip
        self.u.SendInput.return_value = 2
        self.events = []
        self.d._send = lambda event: self.events.append(event)

    def test_arrow_scan_code_and_release(self):
        with patch("istar_control.desktop.time.sleep"):
            self.d.press_key(123, "left")
        self.u.MapVirtualKeyW.assert_called_once_with(0x25, 0)
        self.assertEqual(
            [(e.ki.wVk, e.ki.wScan, e.ki.dwFlags) for e in self.events],
            [(0, 0x4B, 9), (0, 0x4B, 11)],
        )

    def test_combo_reverse_release_and_extended_key(self):
        self.u.MapVirtualKeyW.side_effect = [0x1D, 0x4B]
        with patch("istar_control.desktop.time.sleep"):
            self.d.press_keys(123, ["CTRL", "LEFT"], 0.1)
        self.assertEqual(
            [(e.ki.wScan, e.ki.dwFlags) for e in self.events],
            [(0x1D, 8), (0x4B, 9), (0x4B, 11), (0x1D, 10)],
        )

    def test_combo_failed_intermediate_down_releases_all(self):
        self.u.MapVirtualKeyW.side_effect = [0x1D, 0x1E]

        def send(event):
            self.events.append((event.ki.wScan, event.ki.dwFlags))
            if len(self.events) == 2:
                raise DesktopError("down failed")

        self.d._send = send
        with self.assertRaisesRegex(DesktopError, "down failed"):
            self.d.press_keys(123, ["CTRL", "A"])
        self.assertEqual(self.events, [(0x1D, 8), (0x1E, 8), (0x1E, 10), (0x1D, 10)])

    def test_combo_release_failure_does_not_prevent_other_release(self):
        self.u.MapVirtualKeyW.side_effect = [0x1D, 0x1E]

        def send(event):
            self.events.append((event.ki.wScan, event.ki.dwFlags))
            if event.ki.wScan == 0x1E and event.ki.dwFlags == 10:
                raise DesktopError("release failed")

        self.d._send = send
        with self.assertRaisesRegex(DesktopError, "release failed"):
            self.d.press_keys(123, ["CTRL", "A"], 0)
        self.assertEqual(self.events[-2:], [(0x1E, 10), (0x1D, 10)])

    def test_combo_focus_loss_releases_pressed_keys(self):
        self.u.GetForegroundWindow.side_effect = [123, 123, 123, 999]
        self.u.MapVirtualKeyW.side_effect = [0x1D, 0x1E]
        with patch("istar_control.desktop.time.sleep"):
            with self.assertRaises(DesktopError):
                self.d.press_keys(123, ["CTRL", "A"], 1)
        self.assertEqual(
            [(e.ki.wScan, e.ki.dwFlags) for e in self.events][-2:],
            [(0x1E, 10), (0x1D, 10)],
        )

    def test_combo_rejects_invalid_and_sensitive_before_injection(self):
        for keys in (
            [],
            ["A"] * 5,
            ["a", "A"],
            ["WIN"],
            ["ALT", "TAB"],
            ["CTRL", "ALT", "DELETE"],
            ["CTRL", "ESC"],
        ):
            with self.subTest(keys=keys), self.assertRaises(ValueError):
                self.d.press_keys(123, keys)
        self.assertFalse(self.events)

    def test_release_on_interruption(self):
        with patch("istar_control.desktop.time.sleep", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.d.press_key(123, "A")
        self.assertEqual([e.ki.dwFlags for e in self.events], [8, 10])

    def test_release_when_focus_changes_during_hold(self):
        self.u.GetForegroundWindow.side_effect = [123, 123, 999]
        with patch("istar_control.desktop.time.sleep"):
            with self.assertRaises(DesktopError):
                self.d.press_key(123, "A", 1)
        self.assertEqual([e.ki.dwFlags for e in self.events], [8, 10])

    def test_unicode_text_uses_utf16_and_releases(self):
        self.d.type_text(123, "A😀")
        self.assertEqual(
            [(e.ki.wScan, e.ki.dwFlags) for e in self.events],
            [(0x41, 4), (0x41, 6), (0xD83D, 4), (0xD83D, 6), (0xDE00, 4), (0xDE00, 6)],
        )

    def test_unicode_release_on_focus_change(self):
        self.u.GetForegroundWindow.side_effect = [123, 123, 999]
        with self.assertRaises(DesktopError):
            self.d.type_text(123, "a")
        self.assertEqual([e.ki.dwFlags for e in self.events], [4, 6])

    def test_unicode_limits_and_invalid_text(self):
        for text in ("a" * 1025, "\ud800", None):
            with self.assertRaises(ValueError):
                self.d.type_text(123, text)
        self.assertFalse(self.events)

    def test_click_does_not_inject_obscured_point(self):
        def rect(_, ptr):
            ptr._obj.right, ptr._obj.bottom = 100, 80
            return True

        self.u.GetClientRect.side_effect = rect
        self.u.ClientToScreen.return_value = True
        self.u.WindowFromPoint.return_value = 789
        self.u.GetAncestor.return_value = 999
        with self.assertRaises(DesktopError):
            self.d.click(123, 10, 20)
        self.assertFalse(self.events)
        self.u.SendInput.assert_not_called()

    def test_reject_wrong_foreground_and_bad_duration(self):
        self.u.GetForegroundWindow.return_value = 999
        with self.assertRaises(DesktopError):
            self.d.press_key(123, "A")
        with self.assertRaises(ValueError):
            self.d.press_key(123, "A", float("nan"))
        self.assertEqual(self.events, [])

    def test_focus_only_explicit_window(self):
        self.u.GetForegroundWindow.side_effect = [999, 123]
        self.u.SetForegroundWindow.return_value = True
        self.d.focus_window(123)
        self.u.SetForegroundWindow.assert_called_once_with(123)

    def test_click_and_release_on_interruption(self):
        def rect(_, ptr):
            ptr._obj.right, ptr._obj.bottom = 100, 80
            return True

        self.u.GetClientRect.side_effect = rect
        self.u.ClientToScreen.return_value = True
        self.u.WindowFromPoint.return_value = 456
        self.u.GetAncestor.return_value = 123
        batches = []

        def send_batch(count, events, size):
            batches.append(
                [
                    (events[i].mi.dx, events[i].mi.dy, events[i].mi.dwFlags)
                    for i in range(count)
                ]
            )
            return count

        self.u.SendInput.side_effect = send_batch
        with patch(
            "istar_control.desktop.time.sleep", side_effect=RuntimeError("interrupt")
        ):
            with self.assertRaises(RuntimeError):
                self.d.click(123, 10, 20)
        self.assertEqual(batches, [[(3605, 4171, 0xC001), (0, 0, 2)]])
        self.assertEqual([e.mi.dwFlags for e in self.events], [4])
        self.u.SetCursorPos.assert_not_called()

    def test_click_obscured_does_not_move_or_send(self):
        def rect(_, ptr):
            ptr._obj.right, ptr._obj.bottom = 100, 80
            return True

        self.u.GetClientRect.side_effect = rect
        self.u.ClientToScreen.return_value = True
        self.u.WindowFromPoint.return_value = 456
        self.u.GetAncestor.return_value = 999
        with self.assertRaises(DesktopError):
            self.d.click(123, 10, 20)
        self.u.SetCursorPos.assert_not_called()
        self.assertFalse(self.events)

    def test_click_partial_batch_does_not_send_release(self):
        def rect(_, ptr):
            ptr._obj.right, ptr._obj.bottom = 100, 80
            return True

        self.u.GetClientRect.side_effect = rect
        self.u.ClientToScreen.return_value = True
        self.u.WindowFromPoint.return_value = 456
        self.u.GetAncestor.return_value = 123
        self.u.SendInput.return_value = 1
        with self.assertRaisesRegex(DesktopError, "incomplete"):
            self.d.click(123, 10, 20)
        self.assertFalse(self.events)

    def test_click_rejects_point_outside_cursor_clip(self):
        def rect(_, ptr):
            ptr._obj.right, ptr._obj.bottom = 100, 80
            return True

        def clip(ptr):
            ptr._obj.left, ptr._obj.top, ptr._obj.right, ptr._obj.bottom = 0, 0, 5, 5
            return True

        self.u.GetClientRect.side_effect = rect
        self.u.ClientToScreen.return_value = True
        self.u.WindowFromPoint.return_value = 456
        self.u.GetAncestor.return_value = 123
        self.u.GetClipCursor.side_effect = clip
        with self.assertRaisesRegex(DesktopError, "clipping"):
            self.d.click(123, 10, 20)
        self.u.SendInput.assert_not_called()

    def test_dpi_failure_prevents_capture_and_click(self):
        self.d._set_thread_dpi.return_value = 0
        with self.assertRaises(DesktopError):
            self.d.screenshot(123)
        with self.assertRaises(DesktopError):
            self.d.click(123, 0, 0)
        self.u.SendInput.assert_not_called()
        self.u.GetWindowRect.assert_not_called()

    def test_dpi_context_restored_after_capture_error(self):
        self.u.GetWindowRect.return_value = False
        with self.assertRaises(DesktopError):
            self.d.screenshot(123)
        self.assertEqual(self.d._set_thread_dpi.call_count, 2)
        self.d._set_thread_dpi.assert_called_with(1234)

    def test_send_failure_is_explicit(self):
        self.u.SendInput.return_value = 0
        with self.assertRaises(DesktopError):
            Desktop._send(self.d, _INPUT())

    def mouse_target(self):
        def rect(_, ptr):
            ptr._obj.right, ptr._obj.bottom = 100, 80
            return True

        self.u.GetClientRect.side_effect = rect
        self.u.ClientToScreen.return_value = True
        self.u.WindowFromPoint.return_value = 456
        self.u.GetAncestor.return_value = 123

    def test_move_and_scroll_events(self):
        self.mouse_target()
        self.d.move_mouse(123, 10, 20)
        self.assertEqual(
            (self.events[0].mi.dx, self.events[0].mi.dy, self.events[0].mi.dwFlags),
            (3605, 4171, 0xC001),
        )
        batches = []

        def send(count, events, size):
            batches.append(
                [(events[i].mi.dwFlags, events[i].mi.mouseData) for i in range(count)]
            )
            return count

        self.u.SendInput.side_effect = send
        self.d.scroll(123, 10, 20, -120)
        self.assertEqual(batches, [[(0xC001, 0), (0x0800, (-120) & 0xFFFFFFFF)]])

    def test_new_mouse_actions_reject_unsafe_input(self):
        self.mouse_target()
        for delta in (0, 1201, True, 1.5):
            with self.subTest(delta=delta), self.assertRaises(ValueError):
                self.d.scroll(123, 1, 1, delta)
        for coords in ((0, 0, 0, 0), (0, 0, 257, 0), (True, 0, 1, 0)):
            with self.subTest(coords=coords), self.assertRaises(ValueError):
                self.d.drag(123, *coords)
        self.u.GetAncestor.return_value = 999
        for action in (
            lambda: self.d.move_mouse(123, 1, 1),
            lambda: self.d.scroll(123, 1, 1, 120),
            lambda: self.d.drag(123, 1, 1, 2, 1),
        ):
            with self.assertRaises(DesktopError):
                action()
        self.assertFalse(self.events)
        self.u.SendInput.assert_not_called()

    def test_drag_checks_path_and_releases_on_interruption(self):
        self.mouse_target()

        def send(event):
            self.events.append(event.mi.dwFlags)
            if event.mi.dwFlags == 0xC001:
                raise KeyboardInterrupt()

        self.d._send = send
        with self.assertRaises(KeyboardInterrupt):
            self.d.drag(123, 1, 1, 3, 1)
        self.assertEqual(self.events, [0xC001, 4])
        self.assertEqual(self.d._set_thread_dpi.call_count, 2)

    def test_drag_releases_on_focus_loss_and_ambiguous_down_failure(self):
        self.mouse_target()
        self.u.SendInput.side_effect = RuntimeError("unknown injection")
        with self.assertRaisesRegex(RuntimeError, "unknown injection"):
            self.d.drag(123, 1, 1, 2, 1)
        self.assertEqual([e.mi.dwFlags for e in self.events], [4])
        self.events.clear()
        self.u.SendInput.side_effect = lambda *args: 2

        def lose_focus(*args):
            self.u.GetForegroundWindow.return_value = 999
            return 2

        self.u.SendInput.side_effect = lose_focus
        with self.assertRaisesRegex(DesktopError, "foreground"):
            self.d.drag(123, 1, 1, 2, 1)
        self.assertEqual([e.mi.dwFlags for e in self.events], [4])

    def test_drag_preflight_rejects_obscured_intermediate_point(self):
        self.mouse_target()
        self.u.GetAncestor.side_effect = [123, 999]
        with self.assertRaisesRegex(DesktopError, "obscured"):
            self.d.drag(123, 1, 1, 3, 1)
        self.u.SendInput.assert_not_called()

    def test_drag_release_failure_preserves_cancellation(self):
        self.mouse_target()

        def send(event):
            if event.mi.dwFlags == 0xC001:
                raise KeyboardInterrupt()
            raise DesktopError("release failed")

        self.d._send = send
        with self.assertRaises(KeyboardInterrupt) as caught:
            self.d.drag(123, 1, 1, 2, 1)
        self.assertIn("release failed", caught.exception.__notes__[0])

    def test_screenshot_is_foreground_and_has_bbox(self):
        def rect(_, ptr):
            ptr._obj.left, ptr._obj.top, ptr._obj.right, ptr._obj.bottom = 1, 2, 30, 40
            return True

        self.u.GetWindowRect.side_effect = rect
        with patch("PIL.ImageGrab.grab", return_value="image") as grab:
            self.assertEqual(self.d.screenshot(123), "image")
            grab.assert_called_once_with(bbox=(1, 2, 30, 40), all_screens=True)


if __name__ == "__main__":
    unittest.main()
