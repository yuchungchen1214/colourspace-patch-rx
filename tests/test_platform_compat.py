# Copyright (C) 2026 WhARTS Ltd. — SPDX-License-Identifier: AGPL-3.0-or-later

import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication
from main import MainWindow
from measurement.argyll import InstrumentInfo, _parse_instruments, inspect_spotread
from measurement.session import ManualMeasurementController


APP = QApplication.instance() or QApplication([])


class PlatformCompatibility(unittest.TestCase):
    def test_argyll_device_paths_for_macos_and_windows(self):
        devices = _parse_instruments(
            "1 = 'hid515:/dev/device (X-Rite)'\n"
            "2 = 'usb17:/dev/device (ColorMunki)'\n"
            "3 = 'hid:/9 (X-Rite)'\n"
            "4 = 'hid:/26 (X-Rite)'\n"
            "5 = 'libusb0-0002 (ColorMunki)'\n"
        )
        self.assertEqual([item.port for item in devices], [1, 2, 3, 4, 5])

    def test_scan_timeout_accepts_partial_byte_output(self):
        timeout = subprocess.TimeoutExpired(
            "spotread", 1, output=b"1 = 'hid:/9 (X-Rite)'\n"
        )
        with patch("measurement.argyll.subprocess.run", side_effect=timeout):
            _version, devices, error = inspect_spotread(Path("spotread.exe"))
        self.assertEqual(len(devices), 1)
        self.assertIn("timed out", error)

    def test_cancel_before_wait_is_not_lost(self):
        controller = ManualMeasurementController(Mock(), Mock())
        controller.cancel_current()
        result = []

        def wait():
            try:
                controller._wait_for_user()
            except RuntimeError:
                result.append("cancelled")

        thread = threading.Thread(target=wait, daemon=True)
        thread.start()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, ["cancelled"])

    def test_shutdown_prevents_new_session(self):
        controller = ManualMeasurementController(Mock(), Mock())
        controller.shutdown()
        with patch("measurement.session.SpotreadSession") as launch:
            with self.assertRaises(RuntimeError):
                controller._ensure_session(InstrumentInfo(1, "hid:/9", "X-Rite"))
            launch.assert_not_called()

    @staticmethod
    def report_owner():
        controller = Mock(busy=False, cancelled=False)
        controller.measure.return_value = True
        owner = SimpleNamespace(
            _report_state={
                "instrument": object(),
                "position": 0,
                "sequence": [0],
                "paused": False,
                "stop_requested": False,
            },
            _measurement_owner="report",
            manual_measurement_controller=controller,
            report_dialog=Mock(PATCHES=[("White", (255, 255, 255))]),
            _show_measurement_patch=Mock(),
            _schedule_measurement_session_release=Mock(),
        )
        for method in (
            "_begin_report_patch",
            "_measure_report_patch",
            "_set_report_paused",
            "_stop_report_measurement",
            "_advance_report_measurement",
        ):
            setattr(owner, method, getattr(MainWindow, method).__get__(owner))
        return owner

    def test_pause_invalidates_pending_report_read(self):
        owner = self.report_owner()
        callbacks = []
        with patch("main.QTimer.singleShot", side_effect=lambda _ms, cb: callbacks.append(cb)):
            owner._begin_report_patch()
            owner._set_report_paused(True)
            callbacks[0]()
            owner.manual_measurement_controller.measure.assert_not_called()
            owner._set_report_paused(False)
            callbacks[1]()
            owner.manual_measurement_controller.measure.assert_called_once()

    def test_stopped_report_rejects_old_timer(self):
        owner = self.report_owner()
        callbacks = []
        with patch("main.QTimer.singleShot", side_effect=lambda _ms, cb: callbacks.append(cb)):
            owner._begin_report_patch()
            owner._stop_report_measurement()
            owner._report_state = self.report_owner()._report_state
            owner._measurement_owner = "report"
            callbacks[0]()
            owner.manual_measurement_controller.measure.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
