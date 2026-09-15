from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
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


PREVIEW_INSTRUMENTS = (
    ("X-Rite i1 DisplayPro, ColorMunki Display", "I1-18.B-02.312611.09", "Ready"),
    ("X-Rite ColorMunki", "01-d63ce21e00001e", "Calibration required"),
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
        self.instrument_checks = []
        for index, (name, serial, status) in enumerate(PREVIEW_INSTRUMENTS):
            row = QHBoxLayout()
            check = QCheckBox(name)
            check.setChecked(True)
            self.instrument_checks.append(check)
            row.addWidget(check, 1)
            serial_label = QLabel(serial)
            serial_label.setStyleSheet("color: #888;")
            row.addWidget(serial_label)
            status_label = QLabel(status)
            status_label.setStyleSheet("color: #888;")
            status_label.setMinimumWidth(125)
            row.addWidget(status_label)
            instrument_layout.addLayout(row)
        scan = QPushButton("Scan Again")
        scan.clicked.connect(lambda: self.status_label.setText("Instrument scan preview refreshed."))
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
        path = QFileDialog.getExistingDirectory(
            self,
            "Choose Output Folder",
            self.output_path.text(),
            QFileDialog.Option.ShowDirsOnly,
        )
        if path:
            self.output_path.setText(path)

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
        self.tabs.setCurrentIndex(0)

    def _populate_preview_results(self):
        preview = {
            "R": (25.56, .6740, .3157),
            "G": (83.08, .3036, .6536),
            "B": (9.56, .1506, .0652),
            "W": (117.20, .3230, .3343),
        }
        row = 0
        selected = [item for item, check in zip(PREVIEW_INSTRUMENTS, self.instrument_checks) if check.isChecked()]
        self.results_table.setRowCount(len(selected) * 4)
        for instrument_index, (instrument, _serial, _status) in enumerate(selected):
            for patch, values in preview.items():
                scale = 1.0 - instrument_index * .035
                cells = (instrument, patch, f"{values[0] * scale:.4f}", f"{values[1]:.6f}", f"{values[2]:.6f}", "Preview")
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
        instrument = _combo(item[0] for item in PREVIEW_INSTRUMENTS)
        report_type = _combo(("Quick — RGBW", "Standard — Greyscale and gamut", "Custom…"))
        standard = _combo(("Rec.709 / sRGB", "Display P3", "DCI-P3", "Rec.2020"))
        form.addRow("Display name", display)
        form.addRow("Instrument", instrument)
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

    def _clear_report(self):
        self.preview_title.setText("No report yet")
        self.export_button.setEnabled(False)


class ManualMeasurementDialog(QDialog):
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
        self.instrument = _combo(("All connected instruments",) + tuple(item[0] for item in PREVIEW_INSTRUMENTS))
        setup_form.addRow("Instrument", self.instrument)
        layout.addWidget(setup)

        measure_row = QHBoxLayout()
        self.measure_status = QLabel("Ready — UI preview")
        self.measure_status.setStyleSheet("color: #888;")
        measure_row.addWidget(self.measure_status, 1)
        measure = QPushButton("Measure")
        measure.setDefault(True)
        measure_row.addWidget(measure)
        layout.addLayout(measure_row)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(("#", "Time", "X", "Y", "Z", "x", "y", "Instrument", "Status"))
        self.table.verticalHeader().setVisible(False)
        for column in range(7):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(8, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)

        delete = QPushButton("Delete Selected")
        clear = QPushButton("Clear")
        save = QPushButton("Save CSV…")
        close = QPushButton("Close")
        delete.clicked.connect(self._delete_selected)
        clear.clicked.connect(self._clear_readings)
        save.clicked.connect(lambda: QMessageBox.information(self, "UI Preview", "CSV export is not connected yet."))
        close.clicked.connect(self.close)
        layout.addLayout(_button_row(delete, clear, save, close))
        measure.clicked.connect(self._add_preview_reading)

    def _add_preview_reading(self):
        row = self.table.rowCount()
        self.table.insertRow(row)
        values = (
            row + 1,
            "20:15:32",
            "45.7231",
            "48.0000",
            "52.2814",
            "0.312700",
            "0.329000",
            self.instrument.currentText(),
            "Preview",
        )
        for column, value in enumerate(values):
            self.table.setItem(row, column, QTableWidgetItem(str(value)))
        self.measure_status.setText("Preview reading added. External target was not changed.")

    def _delete_selected(self):
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)

    def _clear_readings(self):
        self.table.setRowCount(0)
        self.measure_status.setText("Ready — UI preview")
