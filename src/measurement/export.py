# Copyright (C) 2026 WhARTS Ltd. — SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import csv
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape


COLOURS = ("R", "G", "B", "W")


def _safe(value: str, fallback: str) -> str:
    value = re.sub(r'[\\/:*?"<>|]+', "-", value.strip())
    return value or fallback


def _short_name(instrument) -> str:
    text = instrument.name.lower()
    if "displaypro" in text or "display pro" in text:
        return "i1DisplayPro"
    if "colormunki" in text:
        return "ColorMunki"
    return _safe(instrument.name.replace("X-Rite", ""), "Instrument")


def _instrument_token(instrument) -> str:
    identifier = _safe(str(getattr(instrument, "display_identifier", "")), "Unknown")
    return f"{_short_name(instrument)}_{identifier}"


def _averages(records, instrument, role):
    result = {}
    for colour in COLOURS:
        readings = [
            record[3] for record in records
            if record[0] == colour
            and (record[2].port, record[2].path) == (instrument.port, instrument.path)
            and record[6] == role
            and record[3] is not None
        ]
        if not readings:
            raise ValueError(f"{instrument.name} is missing the {colour} reading.")
        result[colour] = {
            key: sum(getattr(item, key) for item in readings) / len(readings)
            for key in ("X", "Y", "Z", "x", "y")
        }
    return result


def _write_bpd(path: Path, data) -> None:
    lines = [
        '<?xml version="1.0" encoding="UTF-8" ?>',
        f'<builder_color_space name="{escape(path.stem)}" version="2">',
        "    <head>",
        '        <x red="{R[x]:.4f}" green="{G[x]:.4f}" blue="{B[x]:.4f}" white="{W[x]:.4f}" />'.format(**data),
        '        <y red="{R[y]:.4f}" green="{G[y]:.4f}" blue="{B[y]:.4f}" white="{W[y]:.4f}" />'.format(**data),
        '        <L red="{R[Y]:.4f}" green="{G[Y]:.4f}" blue="{B[Y]:.4f}" white="{W[Y]:.4f}" />'.format(**data),
        "        <gamma>1.000000</gamma>",
        "    </head>",
        "</builder_color_space>", "",
    ]
    path.write_text("\r\n".join(lines), encoding="utf-8")


def _write_ti3(path: Path, instrument, data, spectral: bool) -> None:
    rgb = {"W": (100, 100, 100), "R": (100, 0, 0), "G": (0, 100, 0), "B": (0, 0, 100)}
    lines = ["CTI3", "", 'DESCRIPTOR "RGBW measurements for Argyll ccxxmake"',
             'ORIGINATOR "ColourSpace Patch RX"', 'DEVICE_CLASS "DISPLAY"',
             'COLOR_REP "RGB_XYZ"', f'TARGET_INSTRUMENT "{instrument.name}"',
             'DISPLAY_TYPE_BASE_ID "1"', 'DISPLAY_TYPE_REFRESH "NO"',
             f'INSTRUMENT_TYPE_SPECTRAL "{"YES" if spectral else "NO"}"',
             'NORMALIZED_TO_Y_100 "NO"', 'OBSERVER "1931_2"', "",
             "NUMBER_OF_FIELDS 7", "BEGIN_DATA_FORMAT",
             "SAMPLE_ID RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z", "END_DATA_FORMAT", "",
             "NUMBER_OF_SETS 4", "BEGIN_DATA"]
    for index, colour in enumerate(("W", "R", "G", "B"), 1):
        r, g, b = rgb[colour]
        value = data[colour]
        lines.append(f'{index} {r} {g} {b} {value["X"]:.10f} {value["Y"]:.10f} {value["Z"]:.10f}')
    lines.extend(("END_DATA", ""))
    path.write_text("\n".join(lines), encoding="utf-8")


def export_results(root: Path, display_name: str, instruments, records, formats, spotread=None) -> Path:
    now = datetime.now()
    display = _safe(display_name, "Display")
    target, reference = instruments
    slots = [
        (role, instrument)
        for role, instrument in (("Target", target), ("Reference", reference))
        if instrument is not None
    ]
    if not slots:
        raise ValueError("Select a Target or Reference instrument before saving.")
    formats = set(formats)
    warnings = []
    if "CCMX" in formats and (target is None or reference is None):
        formats.remove("CCMX")
        warnings.append("CCMX was not saved because both Target and Reference are required.")
    values = {}
    if "BPD" in formats or "CCMX" in formats:
        values = {
            role: _averages(records, instrument, role)
            for role, instrument in slots
        }

    stem = f"{now:%Y%m%d_%H%M}_{display}"
    folder = root / stem
    suffix = 2
    while folder.exists():
        folder = root / f"{stem}_{suffix}"
        suffix += 1
    folder.mkdir(parents=True)
    if "CSV" in formats:
        with (folder / f"{now:%Y%m%d}_{display}_{len(slots)}eq.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(("role", "instrument_id", "instrument_name", "display_name", "colour", "sample", "measured_at", "X", "Y", "Z", "x", "y"))
            for role, item in slots:
                for colour, sample, _instrument, reading, _error, measured_at, record_role in records:
                    if (reading is not None and record_role == role
                            and (item.port, item.path) == (_instrument.port, _instrument.path)):
                        writer.writerow((role, item.display_identifier, item.name, display_name, colour, sample, measured_at, reading.X, reading.Y, reading.Z, reading.x, reading.y))
    if "BPD" in formats:
        for role, item in slots:
            _write_bpd(
                folder / f"{role}_{_instrument_token(item)}_{display}.bpd",
                values[role],
            )
    if "CCMX" in formats:
        if spotread is None:
            raise ValueError("ArgyllCMS is unavailable for CCMX creation.")
        ccxxmake = Path(spotread).with_name("ccxxmake.exe" if Path(spotread).suffix.lower() == ".exe" else "ccxxmake")
        if not ccxxmake.is_file():
            raise ValueError("ccxxmake was not found in the selected ArgyllCMS folder.")
        with tempfile.TemporaryDirectory(prefix="patch-rx-ccmx-") as temporary:
            reference_ti3 = Path(temporary) / "reference.ti3"
            target_ti3 = Path(temporary) / "target.ti3"
            _write_ti3(reference_ti3, reference, values["Reference"], True)
            _write_ti3(target_ti3, target, values["Target"], False)
            output = folder / (
                f"{now:%Y%m%d}_Target-{_instrument_token(target)}_to_"
                f"Reference-{_instrument_token(reference)}_{display}.ccmx"
            )
            result = subprocess.run([str(ccxxmake), "-t", "u", "-Y", "n", "-f", f"{reference_ti3},{target_ti3}", "-I", display_name or "Display", str(output)], input="3\n", capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            if result.returncode or not output.is_file():
                raise RuntimeError((result.stdout + result.stderr).strip() or "CCMX creation failed.")
    return folder, warnings
