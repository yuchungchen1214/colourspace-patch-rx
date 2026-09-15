from __future__ import annotations

import os
import queue
import re
import select
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

if sys.platform != "win32":
    import pty

from .argyll import InstrumentInfo


RESULT_RE = re.compile(
    r"Result is XYZ:\s*([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+),\s*"
    r"Yxy:\s*([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)"
)
CAL_RE = re.compile(r"Set instrument sensor to calibration position", re.I)
READY_RE = re.compile(r"Place instrument on spot to be measured", re.I)


@dataclass(frozen=True)
class Measurement:
    X: float
    Y: float
    Z: float
    x: float
    y: float


def _is_i1d3(device: InstrumentInfo) -> bool:
    text = f"{device.path} {device.name}".lower()
    return text.startswith("hid") or "displaypro" in text or "display pro" in text


class SpotreadSession:
    def __init__(self, spotread: Path, device: InstrumentInfo):
        command = [str(spotread), "-c", str(device.port), "-e", "-x"]
        if _is_i1d3(device):
            command.extend(("-Y", "A"))
        self.text = ""
        self.cursor = 0
        self._master = None
        self._output_queue = None
        if sys.platform == "win32":
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )
            self._output_queue = queue.Queue()
            threading.Thread(target=self._read_windows_pipe, daemon=True).start()
        else:
            master, slave = pty.openpty()
            self._master = master
            self.process = subprocess.Popen(
                command,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                close_fds=True,
            )
            os.close(slave)

    def _read_windows_pipe(self):
        if self.process.stdout is None or self._output_queue is None:
            return
        while True:
            try:
                chunk = self.process.stdout.read(1)
            except OSError:
                return
            if not chunk:
                return
            self._output_queue.put(chunk)

    def _read(self, wait: float = .2):
        if sys.platform == "win32":
            if self._output_queue is None:
                return
            chunks = []
            try:
                chunks.append(self._output_queue.get(timeout=wait))
            except queue.Empty:
                return
            while True:
                try:
                    chunks.append(self._output_queue.get_nowait())
                except queue.Empty:
                    break
            self.text += b"".join(chunks).decode(errors="replace")
            return
        if self._master is None or not select.select([self._master], [], [], wait)[0]:
            return
        try:
            self.text += os.read(self._master, 8192).decode(errors="replace")
        except OSError:
            pass

    def wait_state(self, timeout: float, allow_calibration: bool = True) -> str:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self._read()
            unread = self.text[self.cursor:]
            if allow_calibration and CAL_RE.search(unread):
                self.cursor = len(self.text)
                return "calibration"
            if READY_RE.search(unread):
                self.cursor = len(self.text)
                return "ready"
            if self.process.poll() is not None:
                raise RuntimeError("spotread stopped during initialization")
        raise TimeoutError("Timed out while initializing the instrument")

    def trigger(self):
        self.cursor = len(self.text)
        if sys.platform == "win32":
            if self.process.stdin is None:
                raise RuntimeError("spotread input is unavailable")
            self.process.stdin.write(b"\n")
            self.process.stdin.flush()
        elif self._master is not None:
            os.write(self._master, b"\n")

    def measure(self, timeout: float = 20.0) -> Measurement:
        self.trigger()
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self._read()
            match = RESULT_RE.search(self.text[self.cursor:])
            if match:
                self.cursor += match.end()
                X, _Yxyz, Z, Y, x, y = (float(value) for value in match.groups())
                return Measurement(X, Y, Z, x, y)
            if self.process.poll() is not None:
                raise RuntimeError("spotread stopped before returning a reading")
        raise TimeoutError("Timed out while waiting for a reading")

    def close(self):
        try:
            if self.process.poll() is None:
                if sys.platform == "win32" and self.process.stdin is not None:
                    self.process.stdin.write(b"q")
                    self.process.stdin.flush()
                elif self._master is not None:
                    os.write(self._master, b"q")
                self.process.wait(timeout=2)
        except Exception:
            self.process.kill()
        if self._master is not None:
            try:
                os.close(self._master)
            except OSError:
                pass


class ManualMeasurementController(QObject):
    started = Signal(int)
    calibration_required = Signal(object)
    measurement_position_required = Signal(object)
    reading_ready = Signal(object, object)
    measurement_error = Signal(object, str)
    finished = Signal()

    def __init__(self, environment, logger, parent=None):
        super().__init__(parent)
        self.environment = environment
        self.logger = logger
        self._busy = False
        self._continue_event = threading.Event()
        self._cancelled = False

    @property
    def busy(self):
        return self._busy

    def measure(self, instruments):
        if self._busy or not instruments:
            return False
        if self.environment.info.spotread is None:
            return False
        self._busy = True
        self._cancelled = False
        self.started.emit(len(instruments))
        threading.Thread(target=self._worker, args=(list(instruments),), daemon=True).start()
        return True

    def continue_current(self):
        self._continue_event.set()

    def cancel_current(self):
        self._cancelled = True
        self._continue_event.set()

    def _wait_for_user(self):
        self._continue_event.clear()
        self._continue_event.wait()
        if self._cancelled:
            raise RuntimeError("Measurement cancelled")

    def _worker(self, instruments):
        spotread = self.environment.info.spotread
        try:
            for instrument in instruments:
                if self._cancelled:
                    break
                session = None
                try:
                    session = SpotreadSession(spotread, instrument)
                    state = session.wait_state(20.0)
                    if state == "calibration":
                        self.calibration_required.emit(instrument)
                        self._wait_for_user()
                        session.trigger()
                        session.wait_state(12.0, allow_calibration=False)
                        self.measurement_position_required.emit(instrument)
                        self._wait_for_user()
                    reading = session.measure(25.0)
                    self.reading_ready.emit(instrument, reading)
                    self.logger.log(
                        f"[MEASUREMENT] {instrument.name}: X={reading.X:.6f} "
                        f"Y={reading.Y:.6f} Z={reading.Z:.6f} x={reading.x:.6f} y={reading.y:.6f}"
                    )
                except Exception as exc:
                    self.measurement_error.emit(instrument, str(exc))
                    self.logger.log(f"[MEASUREMENT ERROR] {instrument.name}: {exc}")
                finally:
                    if session is not None:
                        session.close()
        finally:
            self._busy = False
            self.finished.emit()
