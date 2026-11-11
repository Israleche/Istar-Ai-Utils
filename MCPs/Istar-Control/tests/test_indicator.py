import time
import unittest
from unittest.mock import Mock, patch

from istar_control.indicator import (
    _EX_STYLE,
    _SW_SHOWNOACTIVATE,
    Indicator,
    IndicatorError,
    show_indicator,
)


class IndicatorTests(unittest.TestCase):
    def setUp(self):
        self.u = Mock()
        self.u.CreateWindowExW.return_value = 123
        self.u.SetLayeredWindowAttributes.return_value = 1
        self.u.PeekMessageW.return_value = 0
        self.u.DestroyWindow.return_value = 1
        self.u.SetWindowTextW.return_value = 1
        self.api = patch("istar_control.indicator._api", return_value=self.u)
        self.api.start()
        self.addCleanup(self.api.stop)

    def test_creation_flags_and_early_close(self):
        i = Indicator(5)
        i.close()
        i.close()
        self.assertEqual(self.u.CreateWindowExW.call_args.args[0], _EX_STYLE)
        self.assertEqual(_EX_STYLE & (0x80000 | 0x20 | 0x8000000), 0x8080020)
        self.u.ShowWindow.assert_called_once_with(123, _SW_SHOWNOACTIVATE)
        self.u.DestroyWindow.assert_called_once_with(123)

    def test_automatic_expiry(self):
        i = Indicator(0.05)
        i._thread.join(1)
        self.assertFalse(i._thread.is_alive())
        self.u.DestroyWindow.assert_called_once_with(123)
        i.close()

    def test_duration_validation(self):
        for value in (0, -1, 61, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                Indicator(value)
        self.u.CreateWindowExW.assert_not_called()

    def test_api_failure_is_reported(self):
        with patch("istar_control.indicator._api", side_effect=OSError("failure")):
            with self.assertRaises(IndicatorError):
                Indicator()

    def test_destroy_failure_is_reported(self):
        self.u.DestroyWindow.return_value = 0
        i = Indicator()
        with self.assertRaises(IndicatorError):
            i.close()

    def test_action_target_and_completion(self):
        i = show_indicator(1, action="click", hwnd=0x1234)
        self.addCleanup(i.close)
        label = self.u.CreateWindowExW.call_args.args[2]
        self.assertIn("ACTIVE", label)
        self.assertIn("Click", label)
        self.assertIn("0x1234", label)
        i.complete()
        deadline = time.monotonic() + 0.5
        while not self.u.SetWindowTextW.called and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIn("COMPLETED", self.u.SetWindowTextW.call_args.args[1])

    def test_rejects_arbitrary_text_and_bad_handles(self):
        for kwargs in (
            {"action": "password"},
            {"hwnd": "secret title"},
            {"hwnd": True},
            {"hwnd": 0},
            {"hwnd": 2**64},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Indicator(**kwargs)
        self.u.CreateWindowExW.assert_not_called()

    def test_update_failure_closes_and_reports(self):
        self.u.SetWindowTextW.return_value = 0
        i = Indicator(1)
        i.complete()
        i._thread.join(1)
        self.assertFalse(i._thread.is_alive())
        with self.assertRaises(IndicatorError):
            i.close()
        self.u.DestroyWindow.assert_called_once_with(123)


if __name__ == "__main__":
    unittest.main()
