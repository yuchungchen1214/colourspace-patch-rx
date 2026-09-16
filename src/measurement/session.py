# Copyright (C) 2026 WhARTS Ltd. — SPDX-License-Identifier: AGPL-3.0-or-later

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


def _is_colormunki_spectrometer(device: InstrumentInfo) -> bool:
    text = f"{device.path} {device.name}".lower()
    return "colormunki" in text and not _is_i1d3(device)


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
    def __init__(
        self,
        spotread: Path,
        device: InstrumentInfo,
        skip_initial_calibration=False,
        correction_path=None,
    ):
        command = [str(spotread), "-c", str(device.port), "-e", "-x", "-D", "9"]
        if correction_path:
            command.extend(("-X", str(correction_path)))
        if skip_initial_calibration:
            command.append("-N")
        if _is_i1d3(device):
            command.extend(("-Y", "A"))
        self.text = ""
        self.cursor = 0
        self._master = None
        self._output_queue = None
        self._close_lock = threading.Lock()
        self._reader_thread = None
        if sys.platform == "win32":
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
                env={**os.environ, "ARGYLL_NOT_INTERACTIVE": "1"},
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self._output_queue = queue.Queue()
            self._reader_thread = threading.Thread(target=self._read_windows_pipe, daemon=True)
            self._reader_thread.start()
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
                chunk = self.process.stdout.read(8192)
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
            self.process.stdin.write(b" \n")
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
        with self._close_lock:
            self._close()

    def _close(self):
        try:
            if self.process.poll() is None:
                if sys.platform == "win32" and self.process.stdin is not None:
                    self.process.stdin.write(b"q\n")
                    self.process.stdin.flush()
                elif self._master is not None:
                    os.write(self._master, b"q")
                self.process.wait(timeout=2)
        except Exception:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=5)
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2)
        for stream in (self.process.stdin, self.process.stdout):
            if stream is not None:
                stream.close()
        if self._master is not None:
            try:
                os.close(self._master)
            except OSError:
                pass
            self._master = None


class ManualMeasurementController(QObject):
    started = Signal(int)
    calibration_required = Signal(object)
    measurement_position_required = Signal(object)
    reading_ready = Signal(object, object)
    measurement_error = Signal(object, str)
    finished = Signal()
    preparation_finished = Signal()
    instrument_identified = Signal(object, str)

    def __init__(self, environment, logger, parent=None, correction_provider=None):
        super().__init__(parent)
        self.environment = environment
        self.logger = logger
        self._busy = False
        self._continue_event = threading.Event()
        self._cancelled = False
        self._sessions = {}
        self._states = {}
        self._session_locks = {}
        self._retiring_events = {}
        self._known_sessions = set()
        self._shutting_down = False
        self._calibrated_keys = set()
        self._pool_lock = threading.Lock()
        self._generation = 0
        self._correction_provider = correction_provider
        self._corrections_enabled = True

    def set_corrections_enabled(self, enabled):
        enabled = bool(enabled)
        if self._corrections_enabled == enabled:
            return
        self.release_sessions()
        self._corrections_enabled = enabled

    @property
    def busy(self):
        return self._busy

    @property
    def cancelled(self):
        return self._cancelled

    def measure(self, instruments):
        if self._shutting_down or self._busy or not instruments:
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
        if self._shutting_down or self.environment.info.spotread is None:
            return
        threading.Thread(target=self._prepare_worker, args=(list(instruments),), daemon=True).start()

    def _lock_for(self, instrument):
        key = self._device_key(instrument)
        with self._pool_lock:
            return self._session_locks.setdefault(key, threading.Lock())

    def _ensure_session(self, instrument, warmup=False):
        key = self._device_key(instrument)
        with self._pool_lock:
            if self._shutting_down:
                raise RuntimeError("Measurement controller is shutting down")
            retiring = self._retiring_events.get(key)
        if retiring is not None:
            retiring.wait()
        with self._pool_lock:
            existing = self._sessions.get(key)
            state = self._states.get(key)
        if existing is not None:
            identifier = extract_instrument_identifier(existing.text)
            if identifier and identifier != instrument.identifier:
                self.instrument_identified.emit(instrument, identifier)
            return existing, state

        with self._pool_lock:
            if self._shutting_down:
                raise RuntimeError("Measurement controller is shutting down")
            skip_initial_calibration = key in self._calibrated_keys
            # Register child creation atomically with shutdown's snapshot.
            session = SpotreadSession(
                self.environment.info.spotread,
                instrument,
                skip_initial_calibration=skip_initial_calibration,
                correction_path=(
                    self._correction_provider(instrument)
                    if self._corrections_enabled and self._correction_provider is not None
                    else None
                ),
            )
            self._known_sessions.add(session)
        try:
            state = session.wait_state(20.0)
            identifier = extract_instrument_identifier(session.text)
            if identifier and identifier != instrument.identifier:
                self.instrument_identified.emit(instrument, identifier)
            if warmup and state == "ready" and _is_i1d3(instrument):
                session.measure(25.0)  # Discard one background reading to warm the meter.
            with self._pool_lock:
                if self._shutting_down:
                    raise RuntimeError("Measurement controller is shutting down")
                self._sessions[key] = session
                self._states[key] = state
            return session, state
        except Exception:
            session.close()
            with self._pool_lock:
                self._known_sessions.discard(session)
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
                        session, _state = self._ensure_session(item, warmup=True)
                        with self._pool_lock:
                            stale = generation != self._generation
                            item_key = self._device_key(item)
                            current = self._sessions.get(item_key)
                            stale_session = current if stale and current is session else None
                            if stale_session is not None:
                                self._sessions.pop(item_key, None)
                                self._states.pop(item_key, None)
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
            sessions = list(self._sessions.items())
            self._sessions.clear()
            self._states.clear()
            retirements = []
            for key, session in sessions:
                event = threading.Event()
                self._retiring_events[key] = event
                retirements.append((key, session, event))

        # Closing spotread can wait for each process for up to two seconds.
        # Detach sessions immediately so UI actions never block, then close the
        # processes in the background. A replacement session waits for the
        # matching retirement event before reconnecting to the instrument.
        for key, session, event in retirements:
            def retire(item_key=key, item_session=session, item_event=event):
                try:
                    item_session.close()
                finally:
                    with self._pool_lock:
                        self._known_sessions.discard(item_session)
                    item_event.set()
                    with self._pool_lock:
                        if self._retiring_events.get(item_key) is item_event:
                            self._retiring_events.pop(item_key, None)

            threading.Thread(target=retire, daemon=True).start()

    def shutdown(self):
        """Synchronously close every child process before the application exits."""
        self._cancelled = True
        self._continue_event.set()
        with self._pool_lock:
            self._shutting_down = True
            self._generation += 1
            sessions = list(self._known_sessions)
            events = list(self._retiring_events.values())
            self._sessions.clear()
            self._states.clear()
        for session in sessions:
            session.close()
        for event in events:
            event.wait(timeout=2.5)
        with self._pool_lock:
            self._known_sessions.clear()

    def continue_current(self):
        self._continue_event.set()

    def cancel_current(self):
        self._cancelled = True
        self._continue_event.set()

    def _wait_for_user(self):
        self._continue_event.wait()
        self._continue_event.clear()
        if self._cancelled:
            raise RuntimeError("Measurement cancelled")

    def _worker(self, instruments):
        try:
            for instrument in instruments:
                if self._cancelled:
                    break
                key = self._device_key(instrument)
                session = None
                phase = "initialization"
                lock = self._lock_for(instrument)
                try:
                    with lock:
                        session, state = self._ensure_session(instrument)
                        if state == "calibration":
                            phase = "calibration"
                            self._continue_event.clear()
                            if self._cancelled:
                                raise RuntimeError("Measurement cancelled")
                            self.calibration_required.emit(instrument)
                            self._wait_for_user()
                            session.trigger()
                            calibration_timeout = 6.0 if _is_colormunki_spectrometer(instrument) else 12.0
                            session.wait_state(calibration_timeout, allow_calibration=False)
                            with self._pool_lock:
                                self._states[key] = "ready"
                                self._calibrated_keys.add(key)
                            self._continue_event.clear()
                            if self._cancelled:
                                raise RuntimeError("Measurement cancelled")
                            self.measurement_position_required.emit(instrument)
                            self._wait_for_user()
                        if self._cancelled:
                            break
                        phase = "measurement"
                        # A ColorMunki normally returns an emissive reading in a few
                        # seconds.  A much longer wait almost always means that its
                        # selector is still at the calibration position.
                        timeout = 6.0 if _is_colormunki_spectrometer(instrument) else 12.0
                        reading = session.measure(timeout)
                        self.reading_ready.emit(instrument, reading)
                        self.logger.log(
                            f"[MEASUREMENT] {instrument.name}: X={reading.X:.6f} "
                            f"Y={reading.Y:.6f} Z={reading.Z:.6f} x={reading.x:.6f} y={reading.y:.6f}"
                        )
                except Exception as exc:
                    if isinstance(exc, TimeoutError) and _is_colormunki_spectrometer(instrument):
                        if phase == "calibration":
                            message = (
                                "Calibration did not complete within 6 seconds. Confirm that the "
                                "instrument is in its calibration position and try again."
                            )
                        else:
                            message = (
                                "No reading was received within 6 seconds. Confirm that the instrument "
                                "is in its display measurement position and try this patch again."
                            )
                    else:
                        message = str(exc)
                    self.measurement_error.emit(instrument, message)
                    self.logger.log(f"[MEASUREMENT ERROR] {instrument.name}: {message}")
                    if session is not None:
                        session.close()
                    with self._pool_lock:
                        self._sessions.pop(key, None)
                        self._states.pop(key, None)
        finally:
            self._busy = False
            self.finished.emit()
