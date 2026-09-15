import csv
from datetime import datetime

from PySide6.QtCore import Qt, QTime, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListView,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


def _heading(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("color: #ddd; font-size: 13px; font-weight: bold;")
    return label


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: #888; font-size: 11px;")
    return label


def _line() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


def _button_row(*buttons: QPushButton) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addStretch()
    for button in buttons:
        row.addWidget(button)
    return row


def _combo(items) -> QComboBox:
    """Use a stable list popup instead of the animated macOS menu popup."""
    combo = QComboBox()
    view = QListView(combo)
    view.setUniformItemSizes(True)
    combo.setView(view)
    combo.addItems(items)
    return combo


def _instrument_label(instrument) -> str:
    identifier = getattr(instrument, "display_identifier", "")
    return f"{instrument.name}[{identifier}]" if identifier else instrument.name


def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().deleteLater()
        if item.layout() is not None:
            _clear_layout(item.layout())


class CorrectionDialog(QDialog):
    patch_requested = Signal(int, int, int, str)

    PATCHES = (
        ("R", (242, 0, 0)),
        ("G", (0, 242, 0)),
        ("B", (0, 0, 242)),
        ("W", (242, 242, 242)),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Correction")
        self.resize(720, 650)
        self._run_index = -1
        self._output_folder_dialog = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        layout.addWidget(_heading("Probe Matching Correction"))
        layout.addWidget(_hint(
            "Measure RGBW with one or more instruments, review the readings, then export "
            "ColourSpace BPD and DisplayCAL correction files."
        ))

        display_row = QHBoxLayout()
        display_row.addWidget(QLabel("Display name"))
        self.display_name = QLineEdit()
        self.display_name.setPlaceholderText("Optional")
        display_row.addWidget(self.display_name, 1)
        layout.addLayout(display_row)

        tabs = QTabWidget()
        tabs.addTab(self._build_setup_tab(), "Setup")
        tabs.addTab(self._build_measure_tab(), "Measure")
        tabs.addTab(self._build_results_tab(), "Results")
        layout.addWidget(tabs, 1)
        self.tabs = tabs

        self.status_label = QLabel("Ready — UI preview; no instrument is being accessed.")
        self.status_label.setStyleSheet("color: #888;")
        layout.addWidget(self.status_label)

        self.start_button = QPushButton("Start RGBW")
        self.start_button.setDefault(True)
        self.clear_button = QPushButton("Clear")
        self.save_button = QPushButton("Save Results…")
        self.save_button.setEnabled(False)
        close_button = QPushButton("Close")
        layout.addLayout(_button_row(self.clear_button, self.start_button, self.save_button, close_button))

        self.clear_button.clicked.connect(self._clear_results)
        self.start_button.clicked.connect(self._start_preview)
        self.save_button.clicked.connect(self._show_preview_notice)
        close_button.clicked.connect(self.close)

    def _build_setup_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)

        instruments = QGroupBox("Instruments")
        instrument_layout = QVBoxLayout(instruments)
        self.instrument_list_layout = QVBoxLayout()
        instrument_layout.addLayout(self.instrument_list_layout)
        self.instrument_checks = []
        self.instrument_placeholder = QLabel("Scanning instruments…")
        self.instrument_placeholder.setStyleSheet("color: #888;")
        self.instrument_list_layout.addWidget(self.instrument_placeholder)
        scan = QPushButton("Scan Again")
        self.scan_button = scan
        instrument_layout.addLayout(_button_row(scan))
        layout.addWidget(instruments)

        patch = QGroupBox("Patch")
        form = QFormLayout(patch)
        self.range_combo = _combo(("Full Range", "Legal", "Extended"))
        self.level_spin = QSpinBox()
        self.level_spin.setRange(1, 255)
        self.level_spin.setValue(242)
        self.readings_spin = QSpinBox()
        self.readings_spin.setRange(1, 10)
        self.readings_spin.setValue(3)
        form.addRow("Signal range", self.range_combo)
        form.addRow("RGBW level", self.level_spin)
        form.addRow("Readings per patch", self.readings_spin)
        layout.addWidget(patch)

        output = QGroupBox("Output")
        output_layout = QGridLayout(output)
        self.output_path = QLineEdit()
        self.output_path.setPlaceholderText("Choose when saving")
        browse = QPushButton("Choose…")
        browse.clicked.connect(self._choose_output)
        output_layout.addWidget(QLabel("Folder"), 0, 0)
        output_layout.addWidget(self.output_path, 0, 1)
        output_layout.addWidget(browse, 0, 2)
        self.csv_check = QCheckBox("CSV")
        self.bpd_check = QCheckBox("BPD")
        self.ccmx_check = QCheckBox("CCMX (two instruments)")
        for checkbox in (self.csv_check, self.bpd_check, self.ccmx_check):
            checkbox.setChecked(True)
        output_layout.addWidget(self.csv_check, 1, 1)
        output_layout.addWidget(self.bpd_check, 1, 2)
        output_layout.addWidget(self.ccmx_check, 2, 1, 1, 2)
        layout.addWidget(output)
        layout.addStretch()
        return page

    def set_scan_callback(self, callback):
        try:
            self.scan_button.clicked.disconnect()
        except RuntimeError:
            pass
        self.scan_button.clicked.connect(callback)

    def set_instruments(self, instruments):
        _clear_layout(self.instrument_list_layout)
        self.instrument_checks = []
        if not instruments:
            label = QLabel("No compatible instruments found")
            label.setStyleSheet("color: #888;")
            self.instrument_list_layout.addWidget(label)
            return
        for instrument in instruments:
            check = QCheckBox(_instrument_label(instrument))
            check.setChecked(True)
            check.instrument_info = instrument
            self.instrument_checks.append(check)
            self.instrument_list_layout.addWidget(check)

    def _build_measure_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(_hint(
            "The main viewer displays each patch. Selected instruments are measured together "
            "and each colour can be repeated without restarting the session."
        ))

        self.patch_rows = {}
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.addWidget(QLabel("Patch"), 0, 0)
        grid.addWidget(QLabel("Status"), 0, 1)
        grid.addWidget(QLabel("Action"), 0, 2)
        for row_index, (name, rgb) in enumerate(self.PATCHES, start=1):
            chip = QLabel(name)
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chip.setFixedSize(34, 24)
            color = QColor(*rgb).name()
            text_color = "#111" if name in ("G", "W") else "#fff"
            chip.setStyleSheet(f"background: {color}; color: {text_color}; border-radius: 3px;")
            status = QLabel("Not measured")
            status.setStyleSheet("color: #888;")
            button = QPushButton("Measure")
            button.clicked.connect(lambda _=False, n=name, c=rgb: self._measure_one(n, c))
            grid.addWidget(chip, row_index, 0)
            grid.addWidget(status, row_index, 1)
            grid.addWidget(button, row_index, 2)
            self.patch_rows[name] = (status, button)
        layout.addLayout(grid)
        layout.addStretch()
        return page

    def _build_results_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        self.results_table = QTableWidget(8, 6)
        self.results_table.setHorizontalHeaderLabels(("Instrument", "Patch", "Y", "x", "y", "Status"))
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 6):
            self.results_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.results_table)
        layout.addWidget(_hint("Preview readings are illustrative and are never written to disk."))
        return page

    def _choose_output(self):
        if self._output_folder_dialog is not None:
            self._output_folder_dialog.raise_()
            self._output_folder_dialog.activateWindow()
            return

        dialog = QFileDialog(self, "Choose Output Folder", self.output_path.text())
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.fileSelected.connect(self.output_path.setText)
        dialog.finished.connect(self._output_folder_finished)
        self._output_folder_dialog = dialog
        dialog.open()

    def _output_folder_finished(self, _result):
        dialog = self._output_folder_dialog
        self._output_folder_dialog = None
        if dialog is not None:
            dialog.deleteLater()
        QTimer.singleShot(0, self._restore_after_output_folder)

    def _restore_after_output_folder(self):
        if self.isVisible():
            self.raise_()
            self.activateWindow()

    def _start_preview(self):
        if not any(check.isChecked() for check in self.instrument_checks):
            QMessageBox.warning(self, "No Instrument", "Select at least one instrument.")
            return
        self._run_index = 0
        self.start_button.setEnabled(False)
        self.clear_button.setEnabled(False)
        self.tabs.setCurrentIndex(1)
        self._run_next_patch()

    def _run_next_patch(self):
        if self._run_index >= len(self.PATCHES):
            self.start_button.setEnabled(True)
            self.clear_button.setEnabled(True)
            self.start_button.setText("Run RGBW Again")
            self.save_button.setEnabled(True)
            self.status_label.setText("Preview RGBW session complete. Review Results before saving.")
            self.tabs.setCurrentIndex(2)
            self._populate_preview_results()
            return
        name, rgb = self.PATCHES[self._run_index]
        status, _ = self.patch_rows[name]
        status.setText("Displaying patch…")
        self.status_label.setText(f"Measuring {name} — preview")
        self.patch_requested.emit(*rgb, f"Measurement {name}")
        QTimer.singleShot(550, lambda: self._finish_patch(name))

    def _finish_patch(self, name: str):
        status, button = self.patch_rows[name]
        status.setText("3 readings complete")
        status.setStyleSheet("")
        button.setText("Remeasure")
        self._run_index += 1
        QTimer.singleShot(250, self._run_next_patch)

    def _measure_one(self, name: str, rgb):
        status, button = self.patch_rows[name]
        status.setText("Displaying patch…")
        self.tabs.setCurrentIndex(1)
        self.status_label.setText(f"Measuring {name} — preview")
        self.patch_requested.emit(*rgb, f"Measurement {name}")

        def finish():
            status.setText("3 readings complete")
            status.setStyleSheet("")
            button.setText("Remeasure")
            self.save_button.setEnabled(True)
            self.status_label.setText(f"{name} preview measurement complete.")
            self._populate_preview_results()

        QTimer.singleShot(550, finish)

    def _clear_results(self):
        self._run_index = -1
        self.results_table.clearContents()
        self.results_table.setRowCount(0)
        for status, button in self.patch_rows.values():
            status.setText("Not measured")
            status.setStyleSheet("color: #888;")
            button.setText("Measure")
        self.start_button.setText("Start RGBW")
        self.start_button.setEnabled(True)
        self.save_button.setEnabled(False)
        self.status_label.setText("Ready — UI preview; no instrument is being accessed.")

    def _populate_preview_results(self):
        preview = {
            "R": (25.56, .6740, .3157),
            "G": (83.08, .3036, .6536),
            "B": (9.56, .1506, .0652),
            "W": (117.20, .3230, .3343),
        }
        row = 0
        selected = [check.instrument_info for check in self.instrument_checks if check.isChecked()]
        self.results_table.setRowCount(len(selected) * 4)
        for instrument_index, instrument in enumerate(selected):
            for patch, values in preview.items():
                scale = 1.0 - instrument_index * .035
                cells = (_instrument_label(instrument), patch, f"{values[0] * scale:.4f}", f"{values[1]:.6f}", f"{values[2]:.6f}", "Preview")
                for column, value in enumerate(cells):
                    self.results_table.setItem(row, column, QTableWidgetItem(value))
                row += 1

    def _show_preview_notice(self):
        QMessageBox.information(
            self,
            "UI Preview",
            "Saving will be connected after the interface and workflow are approved.",
        )


class ReportDialog(QDialog):
    patch_requested = Signal(int, int, int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Display Report")
        self.resize(620, 480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        layout.addWidget(_heading("Display Report"))
        layout.addWidget(_hint("Run a guided measurement sequence and create a concise display performance report."))

        form = QFormLayout()
        display = QLineEdit()
        display.setPlaceholderText("Optional")
        self.instrument = _combo(("Scanning instruments…",))
        report_type = _combo(("Quick — RGBW", "Standard — Greyscale and gamut", "Custom…"))
        standard = _combo(("Rec.709 / sRGB", "Display P3", "DCI-P3", "Rec.2020"))
        form.addRow("Display name", display)
        form.addRow("Instrument", self.instrument)
        form.addRow("Report type", report_type)
        form.addRow("Target", standard)
        layout.addLayout(form)
        layout.addWidget(_line())
        layout.addWidget(_heading("Report contents"))
        layout.addWidget(QLabel("White point · Luminance · Black level · Contrast · Gamut · Greyscale · Gamma"))
        preview = QFrame()
        preview.setFrameShape(QFrame.Shape.StyledPanel)
        preview_layout = QVBoxLayout(preview)
        self.preview_title = QLabel("No report yet")
        self.preview_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_title.setStyleSheet("color: #888;")
        preview_layout.addStretch()
        preview_layout.addWidget(self.preview_title)
        preview_layout.addStretch()
        layout.addWidget(preview, 1)
        start = QPushButton("Start Report")
        self.clear_button = QPushButton("Clear")
        self.export_button = QPushButton("Export…")
        self.export_button.setEnabled(False)
        close = QPushButton("Close")
        start.clicked.connect(lambda: QMessageBox.information(self, "UI Preview", "The report measurement engine is not connected yet."))
        self.clear_button.clicked.connect(self._clear_report)
        close.clicked.connect(self.close)
        layout.addLayout(_button_row(self.clear_button, start, self.export_button, close))

    def set_instruments(self, instruments):
        self.instrument.clear()
        if instruments:
            self.instrument.addItems(_instrument_label(item) for item in instruments)
            self.instrument.setEnabled(True)
        else:
            self.instrument.addItem("No compatible instruments found")
            self.instrument.setEnabled(False)

    def _clear_report(self):
        self.preview_title.setText("No report yet")
        self.export_button.setEnabled(False)


class ManualMeasurementDialog(QDialog):
    measurement_requested = Signal(object)
    continue_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manual Measurement")
        self.resize(720, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        layout.addWidget(_heading("Manual Measurement"))
        layout.addWidget(_hint("Measure an external target and save its readings."))

        setup = QGroupBox("Measurement")
        setup_form = QFormLayout(setup)
        self.instrument = _combo(("Scanning instruments…",))
        self.instrument.setEnabled(False)
        self._instruments = []
        self._records = []
        self._display_mode = "Yxy"
        setup_form.addRow("Instrument", self.instrument)
        layout.addWidget(setup)

        measure_row = QHBoxLayout()
        self.measure_status = QLabel("Ready")
        self.measure_status.setStyleSheet("color: #888;")
        measure_row.addWidget(self.measure_status, 1)
        self.measure_button = QPushButton("Measure")
        self.measure_button.setDefault(True)
        measure_row.addWidget(self.measure_button)
        layout.addLayout(measure_row)

        self.action_frame = QFrame()
        self.action_frame.setFrameShape(QFrame.Shape.StyledPanel)
        action_layout = QVBoxLayout(self.action_frame)
        action_layout.setContentsMargins(12, 10, 12, 10)
        self.action_title = _heading("")
        self.action_message = QLabel()
        self.action_message.setWordWrap(True)
        self.action_cancel = QPushButton("Cancel")
        self.action_continue = QPushButton("Continue")
        self.action_continue.setDefault(True)
        action_layout.addWidget(self.action_title)
        action_layout.addWidget(self.action_message)
        action_layout.addLayout(_button_row(self.action_cancel, self.action_continue))
        self.action_frame.hide()
        layout.addWidget(self.action_frame)

        display_row = QHBoxLayout()
        display_row.addWidget(QLabel("Display"))
        self.display_mode_group = QButtonGroup(self)
        self.display_mode_group.setExclusive(True)
        for mode in ("Yxy", "Yuv", "XYZ", "RGB8", "RGB"):
            button = QPushButton(mode)
            button.setCheckable(True)
            button.setChecked(mode == self._display_mode)
            button.setFixedWidth(58)
            button.clicked.connect(lambda _checked=False, value=mode: self._set_display_mode(value))
            self.display_mode_group.addButton(button)
            display_row.addWidget(button)
        display_row.addStretch()
        self.correct_check = QCheckBox("Correct")
        self.correct_check.setToolTip("Correction processing is not connected yet.")
        self.correct_check.toggled.connect(self._correct_toggled)
        display_row.addWidget(self.correct_check)
        layout.addLayout(display_row)

        self.table = QTableWidget(0, 7)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 1)
        self._render_records()

        delete = QPushButton("Delete Selected")
        clear = QPushButton("Clear")
        save = QPushButton("Save CSV…")
        close = QPushButton("Close")
        delete.clicked.connect(self._delete_selected)
        clear.clicked.connect(self._clear_readings)
        save.clicked.connect(self._save_csv)
        close.clicked.connect(self.close)
        layout.addLayout(_button_row(delete, clear, save, close))
        self.measure_button.clicked.connect(self._request_measurement)
        self.action_continue.clicked.connect(self._continue_action)
        self.action_cancel.clicked.connect(self._cancel_action)

    def _request_measurement(self):
        if not self._instruments:
            QMessageBox.warning(self, "No Instrument", "No compatible instruments are connected.")
            return
        if self.instrument.currentIndex() == 0:
            selected = list(self._instruments)
        else:
            selected = [self._instruments[self.instrument.currentIndex() - 1]]
        self.measurement_requested.emit(selected)

    def set_busy(self, busy: bool, count: int = 0):
        self.measure_button.setEnabled(not busy and bool(self._instruments))
        self.instrument.setEnabled(not busy and bool(self._instruments))
        if busy:
            self.measure_status.setText(f"Measuring {count} instrument(s)…")

    def show_action_prompt(self, title: str, message: str):
        self.action_title.setText(title)
        self.action_message.setText(message)
        self.action_frame.show()
        self.action_continue.setFocus()

    def hide_action_prompt(self):
        self.action_frame.hide()

    def _continue_action(self):
        self.hide_action_prompt()
        self.measure_status.setText("Continuing…")
        self.continue_requested.emit()

    def _cancel_action(self):
        self.hide_action_prompt()
        self.measure_status.setText("Cancelling…")
        self.cancel_requested.emit()

    def add_reading(self, instrument, reading):
        self.hide_action_prompt()
        self._records.append({
            "time": QTime.currentTime().toString("HH:mm:ss"),
            "instrument": instrument,
            "reading": reading,
            "status": "Measured",
        })
        self._render_records()
        self.measure_status.setText(f"Reading received from {instrument.name}")

    def add_error(self, instrument, message: str):
        self.hide_action_prompt()
        self._records.append({
            "time": QTime.currentTime().toString("HH:mm:ss"),
            "instrument": instrument,
            "reading": None,
            "status": f"Error: {message}",
        })
        self._render_records()

    def measurement_finished(self):
        self.hide_action_prompt()
        self.set_busy(False)
        self.measure_status.setText("Ready")

    def set_instruments(self, instruments):
        self._instruments = list(instruments)
        self.instrument.clear()
        if self._instruments:
            self.instrument.addItem("All connected instruments")
            self.instrument.addItems(_instrument_label(item) for item in self._instruments)
            self.instrument.setEnabled(True)
            self.measure_button.setEnabled(True)
            self.measure_status.setText(f"Ready — {len(self._instruments)} instrument(s) connected")
        else:
            self.instrument.addItem("No compatible instruments found")
            self.instrument.setEnabled(False)
            self.measure_button.setEnabled(False)
            self.measure_status.setText("No compatible instruments found")

    def _delete_selected(self):
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            if 0 <= row < len(self._records):
                self._records.pop(row)
        self._render_records()

    def _clear_readings(self):
        self._records.clear()
        self._render_records()
        self.measure_status.setText("Ready")

    def _set_display_mode(self, mode: str):
        self._display_mode = mode
        self._render_records()

    def _correct_toggled(self, checked: bool):
        state = "on" if checked else "off"
        self.measure_status.setText(f"Correct: {state} — processing is not connected yet")

    def _render_records(self):
        value_headers = {
            "Yxy": ("Y", "x", "y"),
            "Yuv": ("Y", "u′", "v′"),
            "XYZ": ("X", "Y", "Z"),
            "RGB8": ("R8", "G8", "B8"),
            "RGB": ("R", "G", "B"),
        }[self._display_mode]
        headers = ("#", "Time", *value_headers, "Instrument", "Status")
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(self._records))
        for row, record in enumerate(self._records):
            reading = record["reading"]
            if reading is None:
                values = ("", "", "")
            elif self._display_mode == "Yxy":
                values = (f"{reading.Y:.6f}", f"{reading.x:.6f}", f"{reading.y:.6f}")
            elif self._display_mode == "XYZ":
                values = (f"{reading.X:.6f}", f"{reading.Y:.6f}", f"{reading.Z:.6f}")
            elif self._display_mode == "Yuv":
                denominator = reading.X + 15 * reading.Y + 3 * reading.Z
                if denominator:
                    values = (
                        f"{reading.Y:.6f}",
                        f"{4 * reading.X / denominator:.6f}",
                        f"{9 * reading.Y / denominator:.6f}",
                    )
                else:
                    values = (f"{reading.Y:.6f}", "0.000000", "0.000000")
            else:
                values = ("—", "—", "—")
            cells = (
                row + 1,
                record["time"],
                *values,
                _instrument_label(record["instrument"]),
                record["status"],
            )
            for column, value in enumerate(cells):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))
        for column in range(len(headers)):
            mode = QHeaderView.ResizeMode.Stretch if column == len(headers) - 2 else QHeaderView.ResizeMode.ResizeToContents
            self.table.horizontalHeader().setSectionResizeMode(column, mode)

    def _save_csv(self):
        if self.table.rowCount() == 0:
            QMessageBox.information(self, "No Measurements", "There are no readings to save.")
            return
        default_name = f"manual_measurements_{datetime.now():%Y%m%d_%H%M}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Save Measurements", default_name, "CSV Files (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        with open(path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(self.table.horizontalHeaderItem(column).text() for column in range(self.table.columnCount()))
            for row in range(self.table.rowCount()):
                writer.writerow(
                    self.table.item(row, column).text() if self.table.item(row, column) else ""
                    for column in range(self.table.columnCount())
                )
        self.measure_status.setText(f"Saved {self.table.rowCount()} reading(s)")
