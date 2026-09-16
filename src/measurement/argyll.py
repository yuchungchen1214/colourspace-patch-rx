# Copyright (C) 2026 WhARTS Ltd. — SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QObject, Signal


SETTINGS_KEY_ARGYLL_SPOTREAD = "measurement/argyll_spotread"
SETTINGS_KEY_ARGYLL_USE_BUNDLED = "measurement/argyll_use_bundled"


@dataclass(frozen=True)
class ArgyllInfo:
    spotread: Path | None
    version: str = ""
    error: str = ""


@dataclass(frozen=True)
class InstrumentInfo:
    port: int
    path: str
    name: str
    identifier: str = ""

    @property
    def display_identifier(self) -> str:
        if self.identifier:
            return self.identifier
        if sys.platform == "win32":
            # Windows Argyll paths such as hid:/9 and hid:/26 need the full
            # path component to distinguish otherwise identical instruments.
            return self.path.split(" (", 1)[0]
        return self.path.split(":", 1)[0]


def _executable_name() -> str:
    return "spotread.exe" if sys.platform == "win32" else "spotread"


def bundled_spotread_path() -> Path | None:
    if sys.platform != "darwin" or platform.machine().lower() not in ("arm64", "aarch64"):
        return None

    roots = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        roots.append(Path(bundle_root) / "argyll")
    roots.append(
        Path(__file__).resolve().parents[2]
        / "vendor" / "argyll" / "macos-arm64" / "bin"
    )
    for root in roots:
        candidate = root / "spotread"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def candidate_paths(saved_path: str = "", prefer_bundled: bool = True) -> list[Path]:
    name = _executable_name()
    candidates: list[Path] = []
    bundled = bundled_spotread_path()
    if prefer_bundled and bundled is not None:
        candidates.append(bundled)
    if saved_path:
        saved = Path(saved_path).expanduser()
        candidates.append(saved / name if saved.is_dir() else saved)
    if not prefer_bundled and bundled is not None:
        candidates.append(bundled)

    home = Path.home()
    if sys.platform == "darwin":
        candidates.extend(sorted(Path("/Applications").glob(f"Argyll_*/bin/{name}"), reverse=True))
        candidates.extend(sorted(
            (home / "Library" / "Application Support" / "DisplayCAL" / "dl").glob(f"Argyll_*/bin/{name}"),
            reverse=True,
        ))
        candidates.append(Path("/opt/homebrew/bin") / name)
        candidates.append(Path("/usr/local/bin") / name)
    elif sys.platform == "win32":
        roots = [
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
            os.environ.get("APPDATA"),
        ]
        for root_text in filter(None, roots):
            root = Path(root_text)
            candidates.extend(sorted(root.glob(f"Argyll*/bin/{name}"), reverse=True))
            candidates.extend(sorted(root.glob(f"DisplayCAL/dl/Argyll*/bin/{name}"), reverse=True))
    else:
        candidates.extend((Path("/usr/bin") / name, Path("/usr/local/bin") / name))
        candidates.extend(sorted((home / ".local" / "share" / "DisplayCAL" / "dl").glob(f"Argyll*/bin/{name}"), reverse=True))

    from_path = shutil.which(name)
    if from_path:
        candidates.append(Path(from_path))

    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        try:
            resolved = str(path.resolve())
        except OSError:
            continue
        if resolved in seen or not path.is_file() or not os.access(path, os.X_OK):
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def inspect_spotread(spotread: Path, timeout: float = 15.0) -> tuple[str, list[InstrumentInfo], str]:
    try:
        result = subprocess.run(
            [str(spotread), "-?"],
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        def decoded(value):
            return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")

        output = decoded(exc.stdout) + decoded(exc.stderr)
        return "", _parse_instruments(output), "Instrument scan timed out."
    except OSError as exc:
        return "", [], str(exc)

    output = (result.stdout or "") + (result.stderr or "")
    version_match = re.search(r"Measure spot values,\s*Version\s+([^\s]+)", output, re.I)
    version = version_match.group(1) if version_match else ""
    instruments = _parse_instruments(output)
    # Some macOS Argyll builds terminate with SIGSEGV after printing a complete
    # usage/device list. Treat useful output as valid, while retaining errors
    # when no version and no device data were returned.
    error = ""
    if result.returncode not in (0, 1) and not version and not instruments:
        error = f"spotread exited with status {result.returncode}."
    return version, instruments, error


def _parse_instruments(output: str) -> list[InstrumentInfo]:
    instruments: list[InstrumentInfo] = []
    seen: set[tuple[int, str]] = set()
    for match in re.finditer(r"^\s*(\d+)\s*=\s*'([^']+)'\s*$", output, re.M):
        port = int(match.group(1))
        path = match.group(2).strip()
        if not re.match(r"^(?:(?:hid|usb)\d*:|libusb\d*[-:])", path, re.I):
            continue
        key = (port, path)
        if key in seen:
            continue
        seen.add(key)
        name_match = re.search(r"\(([^)]+)\)", path)
        name = name_match.group(1).strip() if name_match else path
        instruments.append(InstrumentInfo(port=port, path=path, name=name))
    return instruments


class ArgyllEnvironment(QObject):
    scan_started = Signal()
    scan_finished = Signal(object, object)
    instruments_changed = Signal(object)

    def __init__(self, settings, logger, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.logger = logger
        self.info = ArgyllInfo(None)
        self.instruments: list[InstrumentInfo] = []
        self._lock = threading.Lock()
        self._scanning = False

    def scan(self, force_auto: bool = False):
        with self._lock:
            if self._scanning:
                return
            self._scanning = True
        self.scan_started.emit()
        threading.Thread(target=self._scan_worker, args=(force_auto,), daemon=True).start()

    def set_spotread(self, path: Path):
        self.settings.setValue(SETTINGS_KEY_ARGYLL_SPOTREAD, str(path))
        self.settings.setValue(SETTINGS_KEY_ARGYLL_USE_BUNDLED, False)
        self.scan()

    def update_identifier(self, instrument: InstrumentInfo, identifier: str):
        updated = []
        changed = False
        for current in self.instruments:
            if current.port == instrument.port and current.path == instrument.path:
                if current.identifier != identifier:
                    current = replace(current, identifier=identifier)
                    changed = True
            updated.append(current)
        if changed:
            self.instruments = updated
            self.instruments_changed.emit(updated)

    def _scan_worker(self, force_auto: bool):
        try:
            if force_auto:
                self.settings.setValue(SETTINGS_KEY_ARGYLL_USE_BUNDLED, True)
            prefer_bundled = bool(
                self.settings.value(SETTINGS_KEY_ARGYLL_USE_BUNDLED, True, type=bool)
            )
            saved = "" if force_auto else str(
                self.settings.value(SETTINGS_KEY_ARGYLL_SPOTREAD, "") or ""
            )
            paths = candidate_paths(saved, prefer_bundled=prefer_bundled)
            if not paths:
                info = ArgyllInfo(None, error="ArgyllCMS was not found.")
                instruments: list[InstrumentInfo] = []
            else:
                spotread = paths[0]
                version, instruments, error = inspect_spotread(spotread)
                info = ArgyllInfo(spotread, version, error)
                self.settings.setValue(SETTINGS_KEY_ARGYLL_SPOTREAD, str(spotread))
            self.info = info
            self.instruments = instruments
            self.logger.log(
                f"[ARGYLL] {info.spotread or 'not found'}; version={info.version or 'unknown'}; "
                f"instruments={len(instruments)}"
            )
            self.scan_finished.emit(info, instruments)
        finally:
            with self._lock:
                self._scanning = False
