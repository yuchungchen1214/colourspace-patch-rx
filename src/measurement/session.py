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


def _safe_identifier(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")


def extract_instrument_identifier(text: str) -> str:
    clean = text.replace("\r", "").replace("\x00", "")
    decoded_chunks = []
    for payload in re.findall(r"got\s+'((?:[0-9a-fA-F]{2}\s+){8,}[0-9a-fA-F]{2})'", clean):
        try:
            decoded_chunks.append(bytes.fromhex(payload).decode("latin-1", errors="ignore"))
        except ValueError:
            pass
    searchable = clean + "\n" + "\n".join(decoded_chunks)
    matches = re.findall(
        r"(?<![A-Za-z0-9])([A-Z0-9]{2,8}-\d{2}\.[A-Z0-9]{1,4}-\d{2}\.\d{4,}\.\d{2})(?![A-Za-z0-9])",
        searchable,
        re.I,
    )
    if matches:
        return _safe_identifier(matches[-1].upper())
    for pattern in (
        r"serial number\s*[:=]?\s*([A-Za-z0-9._-]+)",
        r"production no\.?\s*[:=]?\s*([A-Za-z0-9._-]+)",
        r"HW ID\s*[:=]?\s*([A-Za-z0-9._-]+)",
    ):
        matches = re.findall(pattern, searchable, re.I)
        if matches:
            return _safe_identifier(matches[-1])
    return ""


class SpotreadSession:
    def __init__(self, spotread: Path, device: InstrumentInfo):
        command = [str(spotread), "-c", str(device.port), "-e", "-x", "-D", "9"]
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
    preparation_finished = Signal()
    instrument_identified = Signal(object, str)

    def __init__(self, environment, logger, parent=None):
        super().__init__(parent)
        self.environment = environment
        self.logger = logger
        self._busy = False
        self._continue_event = threading.Event()
        self._cancelled = False
        self._sessions = {}
        self._states = {}
        self._session_locks = {}
        self._pool_lock = threading.Lock()
        self._generation = 0

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

    @staticmethod
    def _device_key(instrument):
        return instrument.port, instrument.path

    def prepare(self, instruments):
        if self.environment.info.spotread is None:
            return
        threading.Thread(target=self._prepare_worker, args=(list(instruments),), daemon=True).start()

    def _lock_for(self, instrument):
        key = self._device_key(instrument)
        with self._pool_lock:
            return self._session_locks.setdefault(key, threading.Lock())

    def _ensure_session(self, instrument, warmup=False):
        key = self._device_key(instrument)
        with self._pool_lock:
            existing = self._sessions.get(key)
            state = self._states.get(key)
        if existing is not None:
            return existing, state

        session = SpotreadSession(self.environment.info.spotread, instrument)
        try:
            state = session.wait_state(20.0)
            identifier = extract_instrument_identifier(session.text)
            if identifier and identifier != instrument.identifier:
                self.instrument_identified.emit(instrument, identifier)
            if warmup and state == "ready" and _is_i1d3(instrument):
                session.measure(25.0)  # Discard one background reading to warm the meter.
            with self._pool_lock:
                self._sessions[key] = session
                self._states[key] = state
            return session, state
        except Exception:
            session.close()
            raise

    def _prepare_worker(self, instruments):
        with self._pool_lock:
            generation = self._generation
        threads = []
        for instrument in instruments:
            def prepare_one(item=instrument):
                lock = self._lock_for(item)
                with lock:
                    try:
                        self._ensure_session(item, warmup=True)
                        with self._pool_lock:
                            stale = generation != self._generation
                            stale_session = self._sessions.pop(self._device_key(item), None) if stale else None
                            if stale:
                                self._states.pop(self._device_key(item), None)
                        if stale_session is not None:
                            stale_session.close()
                            return
                        self.logger.log(f"[MEASUREMENT] background ready: {item.name}")
                    except Exception as exc:
                        self.logger.log(f"[MEASUREMENT] background preparation deferred for {item.name}: {exc}")
            thread = threading.Thread(target=prepare_one, daemon=True)
            thread.start()
            threads.append(thread)
        for thread in threads:
            thread.join()
        self.preparation_finished.emit()

    def release_sessions(self):
        with self._pool_lock:
            self._generation += 1
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._states.clear()
        for session in sessions:
            session.close()

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
        try:
            for instrument in instruments:
                if self._cancelled:
                    break
                key = self._device_key(instrument)
                session = None
                lock = self._lock_for(instrument)
                try:
                    with lock:
                        session, state = self._ensure_session(instrument)
                        if state == "calibration":
                            self.calibration_required.emit(instrument)
                            self._wait_for_user()
                            session.trigger()
                            session.wait_state(12.0, allow_calibration=False)
                            with self._pool_lock:
                                self._states[key] = "ready"
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
                    if session is not None:
                        session.close()
                    with self._pool_lock:
                        self._sessions.pop(key, None)
                        self._states.pop(key, None)
        finally:
            self._busy = False
            self.finished.emit()
