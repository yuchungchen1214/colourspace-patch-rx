# Copyright (C) 2026 WhARTS Ltd. — SPDX-License-Identifier: AGPL-3.0-or-later

import csv
import math
import re
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTime, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPalette, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QCheckBox,
    QAbstractSpinBox,
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
    QSizePolicy,
    QStyle,
    QStyleOptionComboBox,
    QStyleOptionSpinBox,
    QDoubleSpinBox,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


from measurement.export import export_results
from measurement.argyll import InstrumentInfo
from measurement.session import Measurement


def _heading(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("font-size: 13px; font-weight: bold;")
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


def _compact_spin_width(default_width: int) -> int:
    """Reserve space for Windows styles with side-by-side step buttons."""
    return 100 if sys.platform == "win32" else default_width


def _combo(items) -> QComboBox:
    """Use a stable list popup instead of the animated macOS menu popup."""
    combo = QComboBox()
    view = QListView(combo)
    view.setUniformItemSizes(True)
    combo.setView(view)
    combo.addItems(items)
    return combo


class ComboTextAlignedDoubleSpinBox(QDoubleSpinBox):
    """Align editable text with the native text inset of a combo box."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)

    def _update_text_margin(self):
        combo_option = QStyleOptionComboBox()
        combo_option.initFrom(self)
        combo_option.rect = self.rect()
        combo_edit = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            combo_option,
            QStyle.SubControl.SC_ComboBoxEditField,
            self,
        )
        spin_option = QStyleOptionSpinBox()
        spin_option.initFrom(self)
        spin_option.rect = self.rect()
        spin_option.buttonSymbols = QAbstractSpinBox.ButtonSymbols.NoButtons
        spin_edit = self.style().subControlRect(
            QStyle.ComplexControl.CC_SpinBox,
            spin_option,
            QStyle.SubControl.SC_SpinBoxEditField,
            self,
        )
        inset = max(0, combo_edit.left() - spin_edit.left())
        self.lineEdit().setTextMargins(inset, 0, 0, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_text_margin()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.StyleChange, QEvent.Type.PaletteChange):
            self._update_text_margin()


def _instrument_label(instrument) -> str:
    identifier = getattr(instrument, "display_identifier", "")
    return f"{instrument.name}[{identifier}]" if identifier else instrument.name


def _instrument_header(instrument) -> str:
    identifier = getattr(instrument, "display_identifier", "")
    return f"{instrument.name}\n[{identifier}]" if identifier else instrument.name


class EotfChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._measured = []
        self._reference = []
        self.setMinimumHeight(220)

    def set_data(self, measured, reference):
        self._measured = list(measured)
        self._reference = list(reference)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        text = palette.windowText().color()
        grid = QColor(text)
        grid.setAlpha(55)
        available = self.rect().adjusted(40, 24, -12, -30)
        side = min(available.width(), available.height())
        plot = QRectF(available.left(), available.top(), side, side)
        painter.setPen(text)
        painter.drawText(plot.left(), 17, "EOTF")
        painter.setPen(QPen(grid, 1))
        for step in range(6):
            fraction = step / 5
            x = plot.left() + fraction * plot.width()
            y = plot.bottom() - fraction * plot.height()
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        painter.setPen(QPen(text, 1))
        painter.drawRect(plot)
        for value in (0.0, 0.5, 1.0):
            x = plot.left() + value * plot.width()
            y = plot.bottom() - value * plot.height()
            painter.drawText(int(x - 9), int(plot.bottom() + 18), f"{value:.1f}")
            painter.drawText(2, int(y + 4), f"{value:.1f}")

        def point(x, y):
            return QPointF(
                plot.left() + max(0, min(1, x)) * plot.width(),
                plot.bottom() - max(0, min(1, y)) * plot.height(),
            )

        if self._reference:
            painter.setPen(QPen(QColor(210, 210, 210), 2, Qt.PenStyle.DashLine))
            painter.drawPolyline(QPolygonF([point(x, y) for x, y in self._reference]))
        for channel, color in enumerate((QColor(235, 55, 55), QColor(45, 190, 80), QColor(60, 105, 245)), 1):
            if not self._measured:
                continue
            points = QPolygonF([point(item[0], item[channel]) for item in self._measured])
            painter.setPen(QPen(color, 2))
            painter.drawPolyline(points)
            painter.setBrush(QBrush(color))
            for chart_point in points:
                painter.drawEllipse(chart_point, 2.5, 2.5)


class CieXyChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._reference = []
        self._measured = []
        self._reference_white = None
        self._measured_white = None
        self.setMinimumHeight(220)

    def set_data(self, reference, measured, reference_white, measured_white):
        self._reference = list(reference)
        self._measured = list(measured)
        self._reference_white = reference_white
        self._measured_white = measured_white
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        text = palette.windowText().color()
        grid = QColor(text)
        grid.setAlpha(55)
        available = self.rect().adjusted(40, 24, -12, -30)
        side = min(available.width(), available.height())
        plot = QRectF(available.left(), available.top(), side, side)
        painter.setPen(text)
        painter.drawText(plot.left(), 17, "CIE xy")
        painter.setPen(QPen(grid, 1))
        for step in range(4):
            value = step * 0.3
            x = plot.left() + value / 0.9 * plot.width()
            y = plot.bottom() - value / 0.9 * plot.height()
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        painter.setPen(QPen(text, 1))
        painter.drawRect(plot)
        for value in (0.0, 0.3, 0.6, 0.9):
            x = plot.left() + value / 0.9 * plot.width()
            painter.drawText(int(x - 9), int(plot.bottom() + 18), f"{value:.1f}")
        for value in (0.0, 0.3, 0.6, 0.9):
            y = plot.bottom() - value / 0.9 * plot.height()
            painter.drawText(2, int(y + 4), f"{value:.1f}")

        def point(xy):
            return QPointF(
                plot.left() + max(0, min(0.9, xy[0])) / 0.9 * plot.width(),
                plot.bottom() - max(0, min(0.9, xy[1])) / 0.9 * plot.height(),
            )

        def triangle(values, pen):
            if len(values) != 3:
                return
            points = [point(value) for value in values]
            points.append(points[0])
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolyline(QPolygonF(points))

        triangle(self._reference, QPen(QColor(190, 190, 190), 2, Qt.PenStyle.DashLine))
        measured_points = [
            (index, point(value))
            for index, value in enumerate(self._measured)
            if value is not None
        ]
        if measured_points:
            painter.setPen(QPen(QColor(225, 145, 45), 2))
            painter.setBrush(QBrush(QColor(225, 145, 45)))
            polyline = [chart_point for _index, chart_point in measured_points]
            if len(measured_points) == 3:
                polyline.append(polyline[0])
            if len(polyline) >= 2:
                painter.drawPolyline(QPolygonF(polyline))
            for _index, chart_point in measured_points:
                painter.drawEllipse(chart_point, 3.0, 3.0)
        painter.setPen(text)
        for label, value in zip(("R", "G", "B"), self._reference):
            centre = point(value)
            painter.drawText(int(centre.x() + 5), int(centre.y() - 5), label)
        if self._reference_white is not None:
            centre = point(self._reference_white)
            painter.setPen(QPen(QColor(190, 190, 190), 2))
            painter.drawLine(centre + QPointF(-4, 0), centre + QPointF(4, 0))
            painter.drawLine(centre + QPointF(0, -4), centre + QPointF(0, 4))
        if self._measured_white is not None:
            centre = point(self._measured_white)
            painter.setPen(QPen(QColor(225, 145, 45), 2))
            painter.setBrush(QBrush(QColor(225, 145, 45)))
            painter.drawEllipse(centre, 3.5, 3.5)
            painter.setPen(text)
            painter.drawText(int(centre.x() + 6), int(centre.y() - 5), "W")
        elif self._reference_white is not None:
            centre = point(self._reference_white)
            painter.setPen(text)
            painter.drawText(int(centre.x() + 6), int(centre.y() - 5), "W")


def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().deleteLater()
        if item.layout() is not None:
            _clear_layout(item.layout())


class InlineValueEditor(QLineEdit):
    """An inline editor that selects its complete value whenever clicked."""

    commit_requested = Signal()
    cancel_requested = Signal()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        QTimer.singleShot(0, self.selectAll)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        QTimer.singleShot(0, self.selectAll)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()
            self.commit_requested.emit()
            return
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
            self.cancel_requested.emit()
            return
        super().keyPressEvent(event)


class EditableValueCell(QLabel):
    """A normal table label that becomes an editor on a single click."""

    committed = Signal()

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._editor = None
        self._manual = False

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        if self._editor is not None:
            return
        editor = InlineValueEditor(self.text(), self)
        editor.setAlignment(Qt.AlignmentFlag.AlignCenter)
        editor.setFrame(False)
        editor.setGeometry(self.rect())
        background = self.palette().color(QPalette.ColorRole.Window).name()
        text = (
            "#d99a3e"
            if self._manual
            else self.palette().color(QPalette.ColorRole.Text).name()
        )
        editor.setStyleSheet(
            f"QLineEdit {{ background: {background}; color: {text}; "
            "border: none; padding: 4px; }}"
        )
        editor.commit_requested.connect(self._finish_editing)
        editor.cancel_requested.connect(self._cancel_editing)
        editor.editingFinished.connect(self._finish_editing)
        self._editor = editor
        editor.show()
        editor.setFocus()
        QTimer.singleShot(0, editor.selectAll)
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._editor is not None:
            self._editor.setGeometry(self.rect())

    def _finish_editing(self):
        editor = self._editor
        if editor is None:
            return
        self.setText(editor.text())
        self._editor = None
        editor.deleteLater()
        self.committed.emit()

    def _cancel_editing(self):
        editor = self._editor
        if editor is None:
            return
        self._editor = None
        editor.deleteLater()


class CorrectionDialog(QDialog):
    patch_requested = Signal(int, int, int, str)
    measurement_requested = Signal(object)
    continue_requested = Signal()
    cancel_requested = Signal()

    PATCHES = (
        ("R", (242, 0, 0)),
        ("G", (0, 242, 0)),
        ("B", (0, 0, 242)),
        ("W", (242, 242, 242)),
    )
    MEASURE_BUTTON_WIDTH = 112
    MEASURE_BUTTON_HEIGHT = 30
    PATCH_BUTTON_WIDTH = 64
    PATCH_BUTTON_HEIGHT = 34
    SIDE_BUTTON_WIDTH = 34
    SIDE_BUTTON_HEIGHT = 34
    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("Correction")
        self.setMinimumWidth(940)
        self._run_index = -1
        self._records = []
        self._active_instruments = []
        self._pending_progress = {}
        self._expected_counts = {}
        self._completed_actions = set()
        self._measurement_generation = 0
        self._measurement_running = False
        self._measurement_widgets = {}
        self._palette_refresh_pending = False
        self._measurement_warnings = []
        self._manual_overrides = {}
        self._output_folder_dialog = None
        self._save_after_choose = False
        self._argyll_spotread = None
        self.save_button = QPushButton("Save")
        self.save_button.setEnabled(False)
        self.cancel_button = QPushButton("Cancel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        content = QWidget()
        content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)
        instrument_section = self._build_measure_tab()
        patch_section = self._build_setup_tab()
        output_section = self._build_output_section()
        content_layout.addWidget(instrument_section)
        content_layout.addSpacing(12)
        lower_sections = QHBoxLayout()
        lower_sections.setContentsMargins(0, 0, 0, 0)
        lower_sections.setSpacing(10)
        lower_sections.addWidget(patch_section, 1)
        lower_sections.addWidget(output_section, 1)
        content_layout.addLayout(lower_sections)
        layout.addWidget(content)

        self.message_frame = QFrame()
        self.message_frame.setObjectName("messageFrame")
        self.message_frame.setFixedHeight(124)
        self.message_frame.setFrameShape(QFrame.Shape.NoFrame)
        message_layout = QVBoxLayout(self.message_frame)
        message_layout.setContentsMargins(0, 0, 0, 0)
        message_layout.setSpacing(8)
        self.status_label = QLabel("Ready")
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.status_label.setStyleSheet("color: #888; border: none; background: transparent;")
        message_layout.addWidget(self.status_label)

        self.action_frame = QFrame()
        self.action_frame.setFrameShape(QFrame.Shape.NoFrame)
        action_layout = QVBoxLayout(self.action_frame)
        action_layout.setContentsMargins(0, 0, 0, 0)
        self.action_title = _heading("")
        self.action_message = QLabel()
        self.action_message.setWordWrap(True)
        self.action_cancel = QPushButton("Cancel")
        self.action_continue = QPushButton("Continue")
        # Do not let the fixed message area compress native macOS buttons.
        # A compressed native button loses its rounded bezel and appears square.
        for button in (self.action_cancel, self.action_continue):
            button.setMinimumHeight(button.sizeHint().height())
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        action_layout.addWidget(self.action_title)
        action_layout.addWidget(self.action_message)
        action_layout.addLayout(_button_row(self.action_cancel, self.action_continue))
        self.action_frame.hide()
        message_layout.addWidget(self.action_frame)
        message_layout.addStretch()
        layout.addWidget(self.message_frame)

        layout.addLayout(_button_row(self.cancel_button))

        self.action_continue.clicked.connect(self._continue_action)
        self.action_cancel.clicked.connect(self._cancel_action)
        self.save_button.clicked.connect(self._save_results)
        self.cancel_button.clicked.connect(self.close)

        # Derive the window height from the finished layout so later UI changes
        # cannot silently leave obsolete hard-coded empty space or clipping.
        layout.activate()
        preferred_height = max(680, self.sizeHint().height())
        self.resize(940, preferred_height)
        self.setMinimumHeight(preferred_height)

    def _build_setup_tab(self) -> QWidget:
        patch = QWidget()
        layout = QVBoxLayout(patch)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(_heading("Patch"))
        patch_layout = QGridLayout()
        patch_layout.setContentsMargins(0, 0, 0, 0)
        patch_layout.setHorizontalSpacing(10)
        patch_layout.setVerticalSpacing(6)
        self.level_spin = QSpinBox()
        self.level_spin.setRange(1, 255)
        self.level_spin.setValue(242)
        self.level_spin.setFixedWidth(_compact_spin_width(72))
        self.level_spin.setToolTip(
            "242 for Full Range. Use 224 only with a correctly configured "
            "Legal or Extended signal path."
        )
        patch_layout.addWidget(QLabel("Patch level (8-bit)"), 0, 0)
        patch_layout.addWidget(self.level_spin, 0, 1)
        self.readings_spin = QSpinBox()
        self.readings_spin.setRange(1, 10)
        self.readings_spin.setValue(1)
        self.readings_spin.setFixedWidth(_compact_spin_width(72))
        patch_layout.addWidget(QLabel("Measurements per patch"), 1, 0)
        patch_layout.addWidget(self.readings_spin, 1, 1)
        patch_layout.setColumnStretch(2, 1)
        layout.addLayout(patch_layout)
        layout.addStretch()
        return patch

    def _build_output_section(self) -> QWidget:
        output = QWidget()
        layout = QVBoxLayout(output)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(_heading("Output"))
        output_layout = QGridLayout()
        self.display_name = QLineEdit()
        self.display_name.setPlaceholderText("Optional; used in folder and file names")
        output_layout.addWidget(QLabel("Display name"), 0, 0)
        output_layout.addWidget(self.display_name, 0, 1, 1, 2)
        self.output_path = QLineEdit()
        self.output_path.setPlaceholderText("Choose when saving")
        browse = QPushButton("Choose…")
        browse.setFixedWidth(92)
        self.save_button.setFixedWidth(92)
        browse.clicked.connect(self._choose_output)
        output_layout.addWidget(QLabel("Storage Path"), 1, 0)
        output_layout.addWidget(self.output_path, 1, 1)
        output_layout.addWidget(browse, 1, 2)
        self.csv_check = QCheckBox("CSV")
        self.bpd_check = QCheckBox("BPD")
        self.ccmx_check = QCheckBox("CCMX")
        for checkbox in (self.csv_check, self.bpd_check, self.ccmx_check):
            checkbox.setChecked(True)
        file_row = QHBoxLayout()
        file_row.addWidget(self.csv_check)
        file_row.addWidget(self.bpd_check)
        file_row.addWidget(self.ccmx_check)
        file_row.addStretch()
        file_row.addWidget(self.save_button)
        output_layout.addWidget(QLabel("Output Format"), 2, 0)
        output_layout.addLayout(file_row, 2, 1, 1, 2)
        layout.addLayout(output_layout)
        layout.addStretch()
        return output

    def set_scan_callback(self, callback):
        try:
            self.scan_button.clicked.disconnect()
        except RuntimeError:
            pass
        self.scan_button.clicked.connect(callback)

    def set_argyll_spotread(self, path):
        self._argyll_spotread = path

    def set_instruments(self, instruments):
        previous_target = self._instrument_key(self._target_instrument)
        previous_reference = self._instrument_key(self._reference_instrument)
        self._instruments = list(instruments)

        self._target_instrument = self._find_instrument(previous_target)
        self._reference_instrument = self._find_instrument(previous_reference)
        if previous_target is None:
            self._target_instrument = self._find_saved_instrument("target")
        if previous_reference is None:
            self._reference_instrument = self._find_saved_instrument("reference")
        self._populate_instrument_selectors()
        self._render_real_results()

    @staticmethod
    def _instrument_key(instrument):
        if instrument is None:
            return None
        return instrument.port, instrument.path

    def _find_instrument(self, key):
        if key is None:
            return None
        return next(
            (item for item in self._instruments if self._instrument_key(item) == key), None
        )

    def _find_saved_instrument(self, side):
        if self._settings is None:
            return None
        saved_path = str(
            self._settings.value(f"measurement/correction_{side}_instrument", "") or ""
        )
        if not saved_path:
            return None
        return next(
            (item for item in self._instruments if item.path == saved_path), None
        )

    def _save_instrument_selection(self, side, instrument):
        if self._settings is None:
            return
        key = f"measurement/correction_{side}_instrument"
        if instrument is None:
            self._settings.remove(key)
        else:
            self._settings.setValue(key, instrument.path)

    def _populate_instrument_selectors(self):
        for selector, selected, empty_label in (
            (self.target_selector, self._target_instrument, "Target"),
            (self.reference_selector, self._reference_instrument, "Reference"),
        ):
            selector.blockSignals(True)
            selector.clear()
            selector.addItem(empty_label, None)
            selected_index = 0
            for index, instrument in enumerate(self._instruments, start=1):
                selector.addItem(_instrument_label(instrument), instrument)
                if self._instrument_key(instrument) == self._instrument_key(selected):
                    selected_index = index
            selector.setCurrentIndex(selected_index)
            selector.blockSignals(False)

    def _instrument_selection_changed(self, side, index):
        selector = self.target_selector if side == "target" else self.reference_selector
        instrument = selector.itemData(index)
        role = "Target" if side == "target" else "Reference"
        if side == "target":
            self._target_instrument = instrument
        else:
            self._reference_instrument = instrument
        self._save_instrument_selection(side, instrument)
        # A table slot represents a separate measurement set. Changing its
        # instrument starts that slot clean, even when both slots select the
        # same physical device.
        self._records = [record for record in self._records if record[6] != role]
        self._manual_overrides = {
            key: value for key, value in self._manual_overrides.items() if key[0] != role
        }
        self._pending_progress = {
            key: value for key, value in self._pending_progress.items() if key[0] != role
        }
        self._expected_counts = {
            key: value for key, value in self._expected_counts.items() if key[0] != role
        }
        self._completed_actions = {
            key for key in self._completed_actions if key[0] != role
        }
        self.save_button.setEnabled(any(record[3] is not None for record in self._records))
        self._render_real_results()

    def _build_measure_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(_heading("Instrument"))
        selector_row = QHBoxLayout()
        selector_row.setContentsMargins(0, 0, 0, 0)
        selector_row.setSpacing(8)
        self.target_selector = _combo(["Target"])
        self.reference_selector = _combo(["Reference"])
        self.target_selector.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.reference_selector.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        selector_row.addWidget(self.target_selector, 1)
        self.scan_button = QPushButton("Scan Instruments")
        selector_row.addWidget(self.scan_button)
        selector_row.addWidget(self.reference_selector, 1)
        layout.addLayout(selector_row)
        self.measurement_panel = QFrame()
        self.measurement_panel.setObjectName("measurementPanel")
        self.measurement_panel.setFrameShape(QFrame.Shape.StyledPanel)
        self.measurement_grid = QGridLayout(self.measurement_panel)
        self.measurement_grid.setContentsMargins(0, 0, 0, 0)
        self.measurement_grid.setHorizontalSpacing(1)
        self.measurement_grid.setVerticalSpacing(1)
        layout.addWidget(self.measurement_panel)
        self.start_button = QPushButton("All")
        self.start_button.setDefault(True)
        self.start_button.setFixedSize(self.PATCH_BUTTON_WIDTH, self.PATCH_BUTTON_HEIGHT)
        self.start_button.clicked.connect(self._start_preview)
        self.target_all_button = QPushButton("‹")
        self.target_all_button.setFixedSize(
            self.SIDE_BUTTON_WIDTH, self.SIDE_BUTTON_HEIGHT
        )
        self.target_all_button.clicked.connect(
            lambda: self._measure_all_for_role("Target")
        )
        self.reference_all_button = QPushButton("›")
        self.reference_all_button.setFixedSize(
            self.SIDE_BUTTON_WIDTH, self.SIDE_BUTTON_HEIGHT
        )
        self.reference_all_button.clicked.connect(
            lambda: self._measure_all_for_role("Reference")
        )
        self._instruments = []
        self._target_instrument = None
        self._reference_instrument = None
        self.target_selector.currentIndexChanged.connect(
            lambda index: self._instrument_selection_changed("target", index)
        )
        self.reference_selector.currentIndexChanged.connect(
            lambda index: self._instrument_selection_changed("reference", index)
        )
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
        dialog.fileSelected.connect(self._output_folder_selected)
        dialog.finished.connect(self._output_folder_finished)
        self._output_folder_dialog = dialog
        dialog.open()

    def _output_folder_selected(self, path):
        self.output_path.setText(path)
        if self._save_after_choose:
            self._save_after_choose = False
            QTimer.singleShot(0, self._save_results)

    def _output_folder_finished(self, _result):
        if not _result:
            self._save_after_choose = False
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
        self._request_real_measurement([name for name, _rgb in self.PATCHES])

    def _measure_all_for_role(self, role):
        instrument = (
            self._target_instrument if role == "Target" else self._reference_instrument
        )
        if instrument is None:
            return
        self._request_real_measurement(
            [name for name, _rgb in self.PATCHES], [(role, instrument)]
        )

    def _request_real_measurement(self, colours, targets=None):
        if targets is None:
            targets = [
                (role, instrument)
                for role, instrument in (
                    ("Target", self._target_instrument),
                    ("Reference", self._reference_instrument),
                )
                if instrument is not None
            ]
        if not targets:
            QMessageBox.warning(self, "No Instrument", "No compatible instrument is selected.")
            return
        self.measurement_requested.emit({
            "targets": targets,
            "colours": list(colours),
            "repetitions": self.readings_spin.value(),
            "level": self.level_spin.value(),
        })

    def begin_measurement(self, colours, targets, repetitions):
        self._measurement_generation += 1
        self._measurement_running = True
        self._measurement_warnings.clear()
        target_roles = {role for role, _instrument in targets}
        for role in target_roles:
            for colour in colours:
                self._discard_active_value_editor(role, colour)
                self._clear_manual_overrides(role, colour)
        self._records = [
            record for record in self._records
            if record[0] not in colours or record[6] not in target_roles
        ]
        self._active_instruments = list(targets)
        for colour in colours:
            for role, _instrument in targets:
                key = (role, colour)
                self._pending_progress.pop(key, None)
                self._completed_actions.discard(key)
                self._expected_counts[key] = repetitions
        self.start_button.setEnabled(False)
        self.target_all_button.setEnabled(False)
        self.reference_all_button.setEnabled(False)
        self.status_label.setStyleSheet("color: #888;")
        self._update_measurement_values()

    def begin_patch(self, name, repetition, total):
        for role, _instrument in self._active_instruments:
            key = (role, name)
            self._pending_progress[key] = repetition - 1
            self._expected_counts[key] = total
        self._update_measurement_values()
        self.status_label.setStyleSheet("color: #888;")
        self.status_label.setText(f"Measuring {name} — {repetition}/{total}")

    def _measure_one(self, name: str, role: str, instrument):
        self._request_real_measurement([name], [(role, instrument)])

    def _measure_pair(self, name: str):
        self._request_real_measurement([name])

    def add_reading(self, colour, sample, role, instrument, reading):
        # A real instrument result always supersedes any manual value that was
        # displayed in this table slot, including a late focus-out commit.
        self._discard_active_value_editor(role, colour)
        self._clear_manual_overrides(role, colour)
        if sample == 1:
            self._records = [
                record for record in self._records
                if not (record[0] == colour and record[6] == role)
            ]
        measured_at = datetime.now().astimezone().isoformat(timespec="milliseconds")
        self._records.append((colour, sample, instrument, reading, "", measured_at, role))
        self.action_frame.hide()
        self._update_measurement_values()

    def add_error(self, colour, sample, role, instrument, message):
        self._discard_active_value_editor(role, colour)
        self._clear_manual_overrides(role, colour)
        if sample == 1:
            self._records = [
                record for record in self._records
                if not (record[0] == colour and record[6] == role)
            ]
        measured_at = datetime.now().astimezone().isoformat(timespec="milliseconds")
        self._records.append((colour, sample, instrument, None, message, measured_at, role))
        self.action_frame.hide()
        self._update_measurement_values()

    def finish_real_patch(self, name):
        self._update_measurement_values()
        generation = self._measurement_generation
        targets = list(self._active_instruments)
        unexpected = self._unexpected_patch_instruments(name, targets)
        if unexpected:
            names = ", ".join(
                f"{role} ({_instrument_label(instrument)})"
                for role, instrument in unexpected
            )
            warning = (
                f"Warning: {name} reading from {names} does not resemble the expected patch. "
                "Values were kept; check the displayed patch and instrument position."
            )
            self._measurement_warnings.append(warning)
            self.status_label.setText(warning)
            self.status_label.setStyleSheet("color: #d99a3e;")
        QTimer.singleShot(
            500,
            lambda: self._reset_completed_actions(name, targets, generation),
        )

    def _unexpected_patch_instruments(self, colour, targets):
        unexpected = []
        for role, instrument in targets:
            readings = [
                record[3] for record in self._records
                if record[0] == colour
                and record[6] == role
                and (record[2].port, record[2].path) == (instrument.port, instrument.path)
                and record[3] is not None
            ]
            if not readings:
                continue
            x = sum(reading.x for reading in readings) / len(readings)
            y = sum(reading.y for reading in readings) / len(readings)
            resembles = {
                "R": x >= 0.45 and x > y,
                "G": y >= 0.45 and y > x,
                "B": x <= 0.25 and y <= 0.20,
                "W": 0.20 <= x <= 0.45 and 0.20 <= y <= 0.45,
            }[colour]
            if not resembles:
                unexpected.append((role, instrument))
        return unexpected

    def _reset_completed_actions(self, name, targets, generation):
        if generation != self._measurement_generation:
            return
        for role, instrument in targets:
            key = (role, name)
            values = [
                record for record in self._records
                if record[0] == name and record[6] == role
                and (record[2].port, record[2].path) == (instrument.port, instrument.path)
            ]
            if values and all(record[3] is not None for record in values):
                self._completed_actions.add(key)
        self._update_measurement_values()

    def finish_real_measurement(self):
        self._measurement_running = False
        self.action_frame.hide()
        self.start_button.setEnabled(True)
        self.target_all_button.setEnabled(self._target_instrument is not None)
        self.reference_all_button.setEnabled(self._reference_instrument is not None)
        self.start_button.setText("All")
        self.save_button.setEnabled(bool(self._records))
        if self._measurement_warnings:
            self.status_label.setStyleSheet("color: #d99a3e;")
            self.status_label.setText(
                "Measurement complete with warnings. " + " ".join(self._measurement_warnings)
            )
            generation = self._measurement_generation
            QTimer.singleShot(
                4000,
                lambda: self._clear_completed_warning(generation),
            )
        else:
            self.status_label.setStyleSheet("color: #888;")
            self.status_label.setText("Measurement complete. Review Results before saving.")
        self._update_measurement_values()

    def _clear_completed_warning(self, generation):
        if generation != self._measurement_generation:
            return
        self.status_label.setStyleSheet("color: #888;")
        self.status_label.setText("Measurement complete. Review Results before saving.")

    def fail_real_measurement(self, message):
        self._measurement_running = False
        self.action_frame.hide()
        self.start_button.setEnabled(True)
        self.target_all_button.setEnabled(self._target_instrument is not None)
        self.reference_all_button.setEnabled(self._reference_instrument is not None)
        self.save_button.setEnabled(any(record[3] is not None for record in self._records))
        self.status_label.setText(message)
        self.status_label.setStyleSheet("color: #d99a3e;")
        self._update_measurement_values()

    def _clear_manual_overrides(self, role, colour):
        self._manual_overrides = {
            key: value for key, value in self._manual_overrides.items()
            if key[:2] != (role, colour)
        }

    def _discard_active_value_editor(self, role, colour):
        for labels, _button, _default_text in self._measurement_widgets.get(
            (role, colour), []
        ):
            for label in labels:
                if isinstance(label, EditableValueCell):
                    label._cancel_editing()

    def _instrument_for_role(self, role):
        instrument = (
            self._target_instrument if role == "Target" else self._reference_instrument
        )
        if instrument is not None:
            return instrument
        return InstrumentInfo(
            port=-1,
            path=f"manual:{role.lower()}",
            name=role,
            identifier="Manual",
        )

    def _base_yxy(self, role, colour):
        readings = [
            record[3] for record in self._records
            if record[0] == colour and record[6] == role and record[3] is not None
        ]
        if not readings:
            return {}
        return {
            "Y": sum(reading.Y for reading in readings) / len(readings),
            "x": sum(reading.x for reading in readings) / len(readings),
            "y": sum(reading.y for reading in readings) / len(readings),
        }

    def _effective_yxy(self, role, colour):
        values = self._base_yxy(role, colour)
        for component in ("Y", "x", "y"):
            key = (role, colour, component)
            if key in self._manual_overrides:
                values[component] = self._manual_overrides[key]
        return values

    def _style_value_editor(self, editor, manual=False):
        palette = self.palette()
        background = palette.color(QPalette.ColorRole.Window).name()
        text = "#d99a3e" if manual else palette.color(QPalette.ColorRole.Text).name()
        editor._manual = manual
        editor.setStyleSheet(
            f"background: {background}; color: {text}; padding: 4px;"
        )

    def _commit_manual_value(self, role, colour, component, editor):
        text = editor.text().strip()
        try:
            value = float(text)
        except (TypeError, ValueError):
            self.status_label.setStyleSheet("color: #d99a3e;")
            self.status_label.setText(f"Invalid {component} value.")
            self._update_measurement_values()
            return
        if not math.isfinite(value) or value < 0.0:
            self.status_label.setStyleSheet("color: #d99a3e;")
            self.status_label.setText(f"Invalid {component} value.")
            self._update_measurement_values()
            return

        if component in ("x", "y") and value > 1.0:
            while value >= 1.0:
                value /= 10.0
        value = round(value, 6)

        candidate = self._effective_yxy(role, colour)
        candidate[component] = value
        if "x" in candidate and "y" in candidate:
            if candidate["y"] <= 0.0 or candidate["x"] + candidate["y"] > 1.0:
                self.status_label.setStyleSheet("color: #d99a3e;")
                self.status_label.setText("y must be above 0 and x + y cannot exceed 1.")
                self._update_measurement_values()
                return

        self._manual_overrides[(role, colour, component)] = value
        effective = self._effective_yxy(role, colour)
        if all(name in effective for name in ("Y", "x", "y")):
            Y, x, y = effective["Y"], effective["x"], effective["y"]
            reading = Measurement(
                X=x * Y / y,
                Y=Y,
                Z=(1.0 - x - y) * Y / y,
                x=x,
                y=y,
            )
            self._records = [
                record for record in self._records
                if not (record[0] == colour and record[6] == role)
            ]
            measured_at = datetime.now().astimezone().isoformat(timespec="milliseconds")
            self._records.append(
                (colour, 1, self._instrument_for_role(role), reading, "", measured_at, role)
            )
            self.save_button.setEnabled(True)
        self._update_measurement_values()

    def _render_real_results(self):
        palette = self.palette()
        base = palette.color(QPalette.ColorRole.Window).name()
        alternate = base
        header_background = base
        text_colour = palette.color(QPalette.ColorRole.Text).name()
        muted_colour = palette.color(QPalette.ColorRole.PlaceholderText).name()
        border_colour = palette.color(QPalette.ColorRole.Mid).name()
        dark_mode = palette.color(QPalette.ColorRole.Window).lightness() < 128
        button_text = "#f2f2f2" if dark_mode else "#202020"
        button_background = palette.color(QPalette.ColorRole.Button).name()
        button_hover = palette.color(QPalette.ColorRole.Light).name()
        button_pressed = palette.color(QPalette.ColorRole.Dark).name()
        disabled_button_text = palette.color(
            QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText
        ).name()
        disabled_button_background = palette.color(
            QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button
        ).name()
        button_style = f"""
            QPushButton {{ color: {button_text}; background: {button_background};
                border: 1px solid {border_colour}; border-radius: 7px; padding: 0; }}
            QPushButton:hover {{ background: {button_hover}; }}
            QPushButton:pressed {{ background: {button_pressed}; }}
            QPushButton:disabled {{ color: {disabled_button_text};
                background: {disabled_button_background}; }}
        """
        self.start_button.setStyleSheet("""
            QPushButton {
                color: white; background: rgb(128, 128, 128);
                border: 1px solid rgb(145, 145, 145); border-radius: 7px; padding: 0;
            }
            QPushButton:hover { background: rgb(142, 142, 142); }
            QPushButton:pressed { background: rgb(112, 112, 112); }
            QPushButton:disabled { color: rgb(205, 205, 205); background: rgb(128, 128, 128); }
        """)
        self.measurement_panel.setStyleSheet(
            f"QFrame#measurementPanel {{ background: {border_colour}; "
            f"border: 1px solid {border_colour}; border-radius: 8px; }}"
        )
        groups = {}
        for colour, _sample, instrument, reading, error, _measured_at, role in self._records:
            groups.setdefault(
                (role, instrument.port, instrument.path, colour), []
            ).append((reading, error))
        target_instrument = self._target_instrument
        reference_instrument = self._reference_instrument
        self._measurement_widgets = {}

        self.target_all_button.setStyleSheet(button_style)
        self.reference_all_button.setStyleSheet(button_style)
        self.target_all_button.setEnabled(
            target_instrument is not None and not self._measurement_running
        )
        self.reference_all_button.setEnabled(
            reference_instrument is not None and not self._measurement_running
        )

        self.start_button.setParent(None)
        self.target_all_button.setParent(None)
        self.reference_all_button.setParent(None)
        while self.measurement_grid.count():
            item = self.measurement_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self.measurement_grid.setRowMinimumHeight(0, 48)
        self.measurement_grid.setRowMinimumHeight(1, 32)
        for row in range(2, 2 + len(self.PATCHES)):
            self.measurement_grid.setRowMinimumHeight(row, 44)
        self.measurement_panel.setFixedHeight(48 + 32 + len(self.PATCHES) * 44 + 8)

        for column in (0, 1, 2, 4, 5, 6):
            self.measurement_grid.setColumnMinimumWidth(column, 96)
            self.measurement_grid.setColumnStretch(column, 1)
        self.measurement_grid.setColumnMinimumWidth(3, 190)
        self.measurement_grid.setColumnStretch(3, 0)

        def cell(text="", header=False, muted=False):
            label = QLabel(text)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setMinimumHeight(48 if header and "\n" in text else (32 if header else 40))
            if header:
                label.setStyleSheet(
                    f"background: {header_background}; color: {text_colour}; font-weight: 600; padding: 4px;"
                )
            elif muted:
                label.setStyleSheet(f"background: {alternate}; color: {muted_colour}; padding: 4px;")
            else:
                label.setStyleSheet(f"background: {base}; color: {text_colour}; padding: 4px;")
            return label

        for instrument, start, placeholder in (
            (target_instrument, 0, "Target"),
            (reference_instrument, 4, "Reference"),
        ):
            title = _instrument_header(instrument) if instrument is not None else placeholder
            self.measurement_grid.addWidget(
                cell(title, header=True, muted=instrument is None), 0, start, 1, 3
            )
            for offset, title in enumerate(("Y", "x", "y")):
                self.measurement_grid.addWidget(cell(title, header=True), 1, start + offset)

        measure_header = QFrame()
        measure_header.setStyleSheet(f"background: {header_background};")
        measure_header_layout = QVBoxLayout(measure_header)
        measure_header_layout.setContentsMargins(0, 5, 0, 5)
        measure_header_layout.setSpacing(3)
        measure_title = QLabel("Measure")
        measure_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        measure_title.setStyleSheet(f"color: {text_colour}; font-weight: 600;")
        measure_header_layout.addWidget(measure_title)
        all_controls = QHBoxLayout()
        # Match the RGBW control rows exactly: fixed left inset and spacing,
        # without centring the group inside the wider Measure column.
        all_controls.setContentsMargins(5, 0, 5, 0)
        all_controls.setSpacing(5)
        all_controls.addWidget(self.target_all_button)
        all_controls.addWidget(self.start_button)
        all_controls.addWidget(self.reference_all_button)
        measure_header_layout.addLayout(all_controls)
        self.measurement_grid.addWidget(measure_header, 0, 3, 2, 1)

        for patch_index, (colour, rgb) in enumerate(self.PATCHES):
            row = patch_index + 2
            chip = QPushButton(colour)
            chip.setFixedSize(self.PATCH_BUTTON_WIDTH, self.PATCH_BUTTON_HEIGHT)
            chip.setStyleSheet(
                "QPushButton { border: none; "
                f"background: {QColor(*rgb).name()}; "
                f"color: {'#111' if colour in ('G', 'W') else '#fff'}; "
                "border-radius: 6px; font-weight: 600; padding: 0; }"
            )
            chip.clicked.connect(lambda _=False, name=colour: self._measure_pair(name))
            control_holder = QFrame()
            control_holder.setStyleSheet(f"background: {base};")
            control_layout = QHBoxLayout(control_holder)
            control_layout.setContentsMargins(5, 4, 5, 4)
            control_layout.setSpacing(5)
            self.measurement_grid.addWidget(control_holder, row, 3)

            side_buttons = []
            for role, instrument, value_start, default_text in (
                ("Target", target_instrument, 0, "‹"),
                ("Reference", reference_instrument, 4, "›"),
            ):
                if instrument is None:
                    value_labels = []
                    effective = self._effective_yxy(role, colour)
                    for offset, component in enumerate(("Y", "x", "y")):
                        value = (
                            f"{effective[component]:.6f}" if component in effective else ""
                        )
                        editor = EditableValueCell(value)
                        editor.setAlignment(Qt.AlignmentFlag.AlignCenter)
                        editor.setMinimumHeight(40)
                        self._style_value_editor(
                            editor,
                            (role, colour, component) in self._manual_overrides,
                        )
                        editor.committed.connect(
                            lambda slot=role, name=colour, field=component, widget=editor:
                            self._commit_manual_value(slot, name, field, widget)
                        )
                        self.measurement_grid.addWidget(editor, row, value_start + offset)
                        value_labels.append(editor)
                    button = QPushButton(default_text)
                    button.setFixedSize(self.SIDE_BUTTON_WIDTH, self.SIDE_BUTTON_HEIGHT)
                    button.setStyleSheet(button_style)
                    button.setEnabled(False)
                    key = (role, colour)
                    self._measurement_widgets.setdefault(key, []).append(
                        (value_labels, button, default_text)
                    )
                    side_buttons.append(button)
                    continue
                values = groups.get((role, instrument.port, instrument.path, colour), [])
                valid = [reading for reading, _error in values if reading is not None]
                has_error = any(error for _reading, error in values)
                key = (role, colour)
                expected = self._expected_counts.get(key, self.readings_spin.value())
                if has_error:
                    cells = ("–", "–", "–")
                    action_text = default_text
                elif valid:
                    Y = sum(value.Y for value in valid) / len(valid)
                    x = sum(value.x for value in valid) / len(valid)
                    y = sum(value.y for value in valid) / len(valid)
                    cells = (f"{Y:.6f}", f"{x:.6f}", f"{y:.6f}")
                    action_text = default_text if key in self._completed_actions else f"{len(valid)}/{expected}"
                elif key in self._pending_progress:
                    cells = ("", "", "")
                    action_text = f"{self._pending_progress[key]}/{expected}"
                else:
                    cells = ("", "", "")
                    action_text = default_text
                components = ("Y", "x", "y")
                effective = self._effective_yxy(role, colour)
                display_cells = list(cells)
                for offset, component in enumerate(components):
                    if component in effective:
                        display_cells[offset] = f"{effective[component]:.6f}"
                for offset, (component, value) in enumerate(zip(components, display_cells)):
                    value_label = EditableValueCell(value)
                    value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    value_label.setMinimumHeight(40)
                    self._style_value_editor(
                        value_label,
                        (role, colour, component) in self._manual_overrides,
                    )
                    value_label.committed.connect(
                        lambda slot=role, name=colour, field=component, editor=value_label:
                        self._commit_manual_value(slot, name, field, editor)
                    )
                    self.measurement_grid.addWidget(value_label, row, value_start + offset)
                    if offset == 0:
                        value_labels = [value_label]
                    else:
                        value_labels.append(value_label)
                button = QPushButton(action_text)
                button.setFixedSize(self.SIDE_BUTTON_WIDTH, self.SIDE_BUTTON_HEIGHT)
                button.setStyleSheet(button_style)
                button.setEnabled(True)
                self._measurement_widgets.setdefault(key, []).append(
                    (value_labels, button, default_text)
                )
                button.clicked.connect(
                    lambda _=False, name=colour, slot=role, item=instrument: self._measure_one(
                        name, slot, item
                    )
                )
                side_buttons.append(button)

            control_layout.addWidget(side_buttons[0])
            control_layout.addWidget(chip)
            control_layout.addWidget(side_buttons[1])

    def _update_measurement_values(self):
        if not self._measurement_widgets:
            return
        groups = {}
        for colour, _sample, instrument, reading, error, _measured_at, role in self._records:
            groups.setdefault(
                (role, instrument.port, instrument.path, colour), []
            ).append((reading, error))
        for key, widget_groups in self._measurement_widgets.items():
            role, colour = key
            selected = (
                self._target_instrument if role == "Target" else self._reference_instrument
            )
            values = (
                groups.get((role, selected.port, selected.path, colour), [])
                if selected is not None else []
            )
            valid = [reading for reading, _error in values if reading is not None]
            has_error = any(error for _reading, error in values)
            expected = self._expected_counts.get(key, self.readings_spin.value())
            if has_error:
                numbers = ("–", "–", "–")
            elif valid:
                numbers = (
                    f"{sum(value.Y for value in valid) / len(valid):.6f}",
                    f"{sum(value.x for value in valid) / len(valid):.6f}",
                    f"{sum(value.y for value in valid) / len(valid):.6f}",
                )
            elif key in self._pending_progress:
                numbers = ("", "", "")
            else:
                numbers = ("", "", "")
            effective = self._effective_yxy(role, colour)
            numbers = tuple(
                f"{effective[component]:.6f}"
                if component in effective else numbers[index]
                for index, component in enumerate(("Y", "x", "y"))
            )
            for labels, button, default_text in widget_groups:
                if has_error:
                    action_text = default_text
                elif valid:
                    action_text = (
                        default_text
                        if key in self._completed_actions
                        else f"{len(valid)}/{expected}"
                    )
                elif key in self._pending_progress:
                    action_text = f"{self._pending_progress[key]}/{expected}"
                else:
                    action_text = default_text
                for component, label, value in zip(("Y", "x", "y"), labels, numbers):
                    if label.text() != value:
                        label.setText(value)
                    self._style_value_editor(
                        label,
                        (role, colour, component) in self._manual_overrides,
                    )
                if button.text() != action_text:
                    button.setText(action_text)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange):
            if self._measurement_widgets and not self._palette_refresh_pending:
                self._palette_refresh_pending = True
                QTimer.singleShot(0, self._refresh_measurement_palette)

    def _refresh_measurement_palette(self):
        self._palette_refresh_pending = False
        if self.isVisible():
            self._render_real_results()

    def show_action_prompt(self, title, message):
        self.action_title.setText(title)
        self.action_message.setText(message)
        self.action_title.setStyleSheet("color: #d99a3e; font-size: 13px; font-weight: bold;")
        self.action_message.setStyleSheet("color: #d99a3e;")
        self.action_message.show()
        self.action_cancel.show()
        self.action_continue.show()
        self.action_frame.show()

    def _continue_action(self):
        action = self.action_title.text()
        if action == "Instrument Calibration":
            self.action_title.setText("Calibrating Instrument…")
        elif action == "Measurement Position":
            self.action_title.setText("Waiting for instrument reading…")
        else:
            self.action_title.setText("Continuing measurement…")
        self.action_message.clear()
        self.action_message.hide()
        self.action_cancel.hide()
        self.action_continue.hide()
        self.action_frame.show()
        self.continue_requested.emit()

    def _cancel_action(self):
        self.action_frame.hide()
        self.cancel_requested.emit()

    def _save_results(self):
        instruments = tuple(
            selected if selected is not None else (
                self._instrument_for_role(role)
                if any(record[6] == role and record[3] is not None for record in self._records)
                else None
            )
            for role, selected in (
                ("Target", self._target_instrument),
                ("Reference", self._reference_instrument),
            )
        )
        formats = {
            name for name, checkbox in (
                ("CSV", self.csv_check), ("BPD", self.bpd_check), ("CCMX", self.ccmx_check)
            ) if checkbox.isChecked()
        }
        if not formats:
            self.status_label.setStyleSheet("color: #d99a3e;")
            self.status_label.setText("Select at least one output file type.")
            return
        if not self.output_path.text().strip():
            self._save_after_choose = True
            self._choose_output()
            return
        try:
            folder, warnings = export_results(
                Path(self.output_path.text()).expanduser(), self.display_name.text(),
                instruments, self._records, formats, self._argyll_spotread,
            )
        except Exception as error:
            self.status_label.setStyleSheet("color: #d99a3e;")
            self.status_label.setText(f"Save failed: {error}")
            return
        if warnings:
            self.status_label.setStyleSheet("color: #d99a3e;")
            self.status_label.setText(f"Saved to {folder}. {' '.join(warnings)}")
        else:
            self.status_label.setStyleSheet("color: #888;")
            self.status_label.setText(f"Saved to {folder}")


class ReportDialog(QDialog):
    patch_requested = Signal(int, int, int, str)
    measurement_requested = Signal(object)
    remeasure_requested = Signal(int, object)
    pause_requested = Signal(bool)
    stop_requested = Signal()
    continue_requested = Signal()
    cancel_requested = Signal()

    PATCHES = tuple(
        (f"Grey {level}", (level, level, level))
        for level in (0, 32, 64, 96, 128, 160, 192, 224, 255)
    ) + (
        ("Red", (255, 0, 0)),
        ("Green", (0, 255, 0)),
        ("Blue", (0, 0, 255)),
        ("Cyan", (0, 255, 255)),
        ("Magenta", (255, 0, 255)),
        ("Yellow", (255, 255, 0)),
    )

    TARGET_PRIMARIES = {
        "sRGB": ((0.640, 0.330), (0.300, 0.600), (0.150, 0.060)),
        "Rec.709": ((0.640, 0.330), (0.300, 0.600), (0.150, 0.060)),
        "Display P3": ((0.680, 0.320), (0.265, 0.690), (0.150, 0.060)),
        "DCI-P3": ((0.680, 0.320), (0.265, 0.690), (0.150, 0.060)),
        "Adobe RGB (1998)": ((0.640, 0.330), (0.210, 0.710), (0.150, 0.060)),
        "Rec.2020": ((0.708, 0.292), (0.170, 0.797), (0.131, 0.046)),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Display Report")
        self.setMinimumHeight(760)
        self._instruments = []
        self._records = []
        self._correction_status_provider = None
        self._scan_callback = None
        self._updating_reference_fields = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        instrument_section = QWidget()
        instrument_layout = QVBoxLayout(instrument_section)
        instrument_layout.setContentsMargins(0, 0, 0, 0)
        instrument_layout.setSpacing(6)
        instrument_layout.addWidget(_heading("Instrument"))
        self.instrument = _combo(("Scanning instruments…",))
        self.instrument.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.instrument.setMinimumContentsLength(36)
        self.scan_button = QPushButton("Scan Instruments")
        instrument_row = QHBoxLayout()
        instrument_row.setContentsMargins(0, 0, 0, 0)
        instrument_row.setSpacing(8)
        instrument_row.addWidget(self.instrument, 1)
        instrument_row.addWidget(self.scan_button)
        instrument_layout.addLayout(instrument_row)
        layout.addWidget(instrument_section)
        layout.addSpacing(12)

        measurement_section = QWidget()
        measurement_layout = QVBoxLayout(measurement_section)
        measurement_layout.setContentsMargins(0, 0, 0, 0)
        measurement_layout.setSpacing(6)
        measurement_layout.addWidget(_heading("Measurement"))

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(("Color", "RGB", "Y", "x", "y", "ΔE2000"))
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for column in range(1, 6):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        measurement_layout.addWidget(self.table, 1)

        self.start_button = QPushButton("Start")
        self.clear_button = QPushButton("Clear")
        self.pause_button = QPushButton("Pause")
        self.stop_button = QPushButton("Stop")
        self.rgb_check = QCheckBox("RGB")
        self.rgb_check.setChecked(True)
        self.cmy_check = QCheckBox("CMY")
        self.cmy_check.setChecked(False)
        self.grey_check = QCheckBox("Grey Steps")
        self.grey_check.setChecked(True)
        self.grey_steps = QSpinBox()
        self.grey_steps.setRange(2, 33)
        self.grey_steps.setValue(5)
        self.grey_steps.setFixedWidth(_compact_spin_width(80))
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        measurement_buttons = QHBoxLayout()
        measurement_buttons.setContentsMargins(0, 0, 0, 0)
        measurement_buttons.setSpacing(8)
        measurement_buttons.addWidget(self.start_button)
        measurement_buttons.addWidget(self.pause_button)
        measurement_buttons.addWidget(self.stop_button)
        measurement_buttons.addWidget(self.clear_button)
        measurement_buttons.addSpacing(12)
        measurement_buttons.addWidget(self.rgb_check)
        measurement_buttons.addWidget(self.cmy_check)
        measurement_buttons.addWidget(self.grey_check)
        measurement_buttons.addWidget(self.grey_steps)
        measurement_buttons.addStretch()
        measurement_layout.addLayout(measurement_buttons)
        content_layout = QHBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(24)
        content_layout.addWidget(measurement_section, 1)

        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color: #888;")

        self.action_panel = QWidget()
        action_layout = QHBoxLayout(self.action_panel)
        action_layout.setContentsMargins(0, 0, 0, 0)
        self.action_message = QLabel()
        self.action_message.setWordWrap(True)
        self.action_message.setStyleSheet("color: #d99a3e;")
        self.action_cancel = QPushButton("Cancel")
        self.action_continue = QPushButton("Continue")
        self.action_cancel.clicked.connect(self.cancel_requested)
        self.action_continue.clicked.connect(self.continue_requested)
        action_layout.addWidget(self.action_message, 1)
        action_layout.addWidget(self.action_cancel)
        action_layout.addWidget(self.action_continue)
        self.action_panel.hide()

        output_section = QWidget()
        output_layout = QVBoxLayout(output_section)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.setSpacing(6)
        output_layout.addWidget(_heading("Output"))
        output_form = QGridLayout()
        output_form.setContentsMargins(0, 0, 0, 0)
        output_form.setHorizontalSpacing(10)
        output_form.setVerticalSpacing(6)
        self.display_name = QLineEdit()
        self.display_name.setPlaceholderText("Optional; used in file name")
        output_form.addWidget(QLabel("Display name"), 0, 0)
        output_form.addWidget(self.display_name, 0, 1, 1, 2)

        reference_section = QWidget()
        reference_layout = QVBoxLayout(reference_section)
        reference_layout.setContentsMargins(0, 0, 0, 0)
        reference_layout.setSpacing(6)
        reference_layout.addWidget(_heading("Reference"))
        reference_controls = QHBoxLayout()
        reference_controls.setContentsMargins(0, 0, 0, 0)
        reference_controls.setSpacing(28)
        selectors_layout = QGridLayout()
        selectors_layout.setContentsMargins(0, 0, 0, 0)
        selectors_layout.setHorizontalSpacing(10)
        selectors_layout.setVerticalSpacing(6)
        coordinates_layout = QGridLayout()
        coordinates_layout.setContentsMargins(0, 0, 0, 0)
        coordinates_layout.setHorizontalSpacing(8)
        coordinates_layout.setVerticalSpacing(6)
        self.target = _combo((*tuple(self.TARGET_PRIMARIES), "Custom Primaries"))
        self.target.setMinimumWidth(180)
        self.target.currentTextChanged.connect(self._reference_options_changed)
        self.custom_primary_fields = {}
        srgb = self.TARGET_PRIMARIES["sRGB"]
        for row, (channel, values) in enumerate(zip(("R", "G", "B"), srgb)):
            coordinates_layout.addWidget(QLabel(channel), row, 0)
            for column, (axis, value) in enumerate(zip(("x", "y"), values)):
                field = QDoubleSpinBox()
                field.setRange(0.0001, 0.9999)
                field.setDecimals(4)
                field.setSingleStep(0.0001)
                field.setFixedWidth(100)
                field.setValue(value)
                field.valueChanged.connect(self._custom_primaries_edited)
                self.custom_primary_fields[(channel, axis)] = field
                coordinates_layout.addWidget(QLabel(axis), row, 1 + column * 2)
                coordinates_layout.addWidget(field, row, 2 + column * 2)
        self.reference_white = _combo(("D65", "D50", "DCI White", "Measured White", "Custom xy"))
        self.reference_white.setMinimumWidth(180)
        self.reference_white.currentTextChanged.connect(self._reference_options_changed)
        self.custom_white_x = QDoubleSpinBox()
        self.custom_white_y = QDoubleSpinBox()
        for field, value in ((self.custom_white_x, 0.3127), (self.custom_white_y, 0.3290)):
            field.setRange(0.0001, 0.9999)
            field.setDecimals(4)
            field.setSingleStep(0.0001)
            field.setFixedWidth(100)
            field.setValue(value)
            field.valueChanged.connect(self._custom_white_edited)
        coordinates_layout.addWidget(QLabel("W"), 3, 0)
        coordinates_layout.addWidget(QLabel("x"), 3, 1)
        coordinates_layout.addWidget(self.custom_white_x, 3, 2)
        coordinates_layout.addWidget(QLabel("y"), 3, 3)
        coordinates_layout.addWidget(self.custom_white_y, 3, 4)

        self.reference_gamma = _combo((
            "sRGB Curve", "Gamma 2.2", "Gamma 2.4", "Gamma 2.6",
            "BT.1886", "Measured Gamma", "Custom",
        ))
        self.reference_gamma.setMinimumWidth(180)
        self.reference_gamma.currentTextChanged.connect(self._reference_options_changed)
        self.custom_gamma = ComboTextAlignedDoubleSpinBox()
        self.custom_gamma.setRange(0.00, 5.00)
        self.custom_gamma.setDecimals(2)
        self.custom_gamma.setSingleStep(0.05)
        self.custom_gamma.setSpecialValueText("-")
        self.custom_gamma.setFixedWidth(100)
        self.custom_gamma.setValue(2.20)
        self.custom_gamma.valueChanged.connect(self._custom_gamma_edited)
        selectors_layout.addWidget(QLabel("Gamut"), 0, 0)
        selectors_layout.addWidget(self.target, 0, 1, 1, 3)
        selectors_layout.addWidget(QLabel("Gamma"), 1, 0)
        selectors_layout.addWidget(self.custom_gamma, 1, 1)
        selectors_layout.addWidget(self.reference_gamma, 1, 2, 1, 2)
        selectors_layout.addWidget(QLabel("White Point"), 2, 0)
        selectors_layout.addWidget(self.reference_white, 2, 1, 1, 3)
        self.reference_gamma.blockSignals(True)
        self.reference_white.blockSignals(True)
        self.reference_gamma.setCurrentText("Gamma 2.2")
        self.reference_white.setCurrentText("Measured White")
        self.reference_gamma.blockSignals(False)
        self.reference_white.blockSignals(False)
        selectors_layout.setColumnStretch(1, 1)
        reference_controls.addLayout(selectors_layout, 1)
        reference_controls.addLayout(coordinates_layout)
        reference_layout.addLayout(reference_controls)
        reference_layout.addSpacing(12)
        reference_layout.addWidget(_heading("Report"))
        charts_layout = QHBoxLayout()
        charts_layout.setContentsMargins(0, 0, 0, 0)
        charts_layout.setSpacing(12)
        self.eotf_chart = EotfChart()
        self.cie_xy_chart = CieXyChart()
        charts_layout.addWidget(self.eotf_chart, 1)
        charts_layout.addWidget(self.cie_xy_chart, 1)
        reference_layout.addLayout(charts_layout, 1)
        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        self.result_label.setStyleSheet("color: #d99a3e;")
        reference_layout.addWidget(self.result_label)
        self._reference_options_changed()
        self.output_path = QLineEdit()
        self.output_path.setPlaceholderText("Choose when saving")
        self.choose_output_button = QPushButton("Choose…")
        self.choose_output_button.setFixedWidth(92)
        output_form.addWidget(QLabel("Storage Path"), 1, 0)
        output_form.addWidget(self.output_path, 1, 1)
        output_form.addWidget(self.choose_output_button, 1, 2)
        self.summary_csv_check = QCheckBox("Summary CSV")
        self.summary_csv_check.setChecked(True)
        self.measurements_csv_check = QCheckBox("Measurements CSV")
        self.measurements_csv_check.setChecked(True)
        self.png_check = QCheckBox("PNG")
        self.png_check.setChecked(True)
        output_formats = QHBoxLayout()
        output_formats.setContentsMargins(0, 0, 0, 0)
        output_formats.addWidget(self.summary_csv_check)
        output_formats.addWidget(self.measurements_csv_check)
        output_formats.addWidget(self.png_check)
        output_formats.addStretch()
        output_form.addWidget(QLabel("Output Format"), 2, 0)
        output_form.addLayout(output_formats, 2, 1)
        self.export_button = QPushButton("Save")
        self.export_button.setFixedWidth(92)
        self.export_button.setEnabled(False)
        output_form.addWidget(self.export_button, 2, 2)
        output_layout.addLayout(output_form)
        right_section = QWidget()
        right_layout = QVBoxLayout(right_section)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(18)
        right_layout.addWidget(reference_section, 1)
        right_layout.addWidget(output_section)
        content_layout.addWidget(right_section, 1)
        layout.addLayout(content_layout, 1)
        layout.addWidget(self.status_label)
        layout.addWidget(self.action_panel)

        close = QPushButton("Cancel")
        self.start_button.clicked.connect(self._request_measurement)
        self.scan_button.clicked.connect(self._scan_instruments)
        self.clear_button.clicked.connect(self._clear_report)
        self.pause_button.clicked.connect(self._toggle_pause)
        self.stop_button.clicked.connect(self.stop_requested)
        self.table.cellDoubleClicked.connect(self._request_remeasurement)
        self.rgb_check.toggled.connect(self._measurement_plan_changed)
        self.cmy_check.toggled.connect(self._measurement_plan_changed)
        self.grey_check.toggled.connect(self._measurement_plan_changed)
        self.grey_steps.valueChanged.connect(self._measurement_plan_changed)
        self.export_button.clicked.connect(self._save_outputs)
        self.choose_output_button.clicked.connect(self._choose_output)
        close.clicked.connect(self.close)
        layout.addLayout(_button_row(close))
        self._measurement_plan_changed()
        layout.activate()
        controls_width = measurement_buttons.sizeHint().width()
        required_width = max(
            1180,
            layout.contentsMargins().left()
            + layout.contentsMargins().right()
            + content_layout.spacing()
            + controls_width * 2,
        )
        default_width = max(1280, required_width)
        self.setMinimumWidth(default_width)
        self.resize(default_width, 900)

    def set_correction_status_provider(self, provider):
        self._correction_status_provider = provider
        self.set_instruments(self._instruments)

    def set_scan_callback(self, callback):
        self._scan_callback = callback

    def _scan_instruments(self):
        if self._scan_callback is not None:
            self._scan_callback()

    def set_instruments(self, instruments):
        selected_key = None
        if 0 <= self.instrument.currentIndex() < len(self._instruments):
            item = self._instruments[self.instrument.currentIndex()]
            selected_key = (item.port, item.path)
        self._instruments = list(instruments)
        self.instrument.clear()
        if instruments:
            selected_index = 0
            for index, item in enumerate(self._instruments):
                status = (
                    self._correction_status_provider(item)
                    if self._correction_status_provider is not None else "Raw"
                )
                self.instrument.addItem(f"{_instrument_label(item)} — {status}")
                if selected_key == (item.port, item.path):
                    selected_index = index
            self.instrument.setCurrentIndex(selected_index)
            self.instrument.setEnabled(True)
            self.start_button.setEnabled(bool(self.PATCHES))
        else:
            self.instrument.addItem("No compatible instruments found")
            self.instrument.setEnabled(False)
            self.start_button.setEnabled(False)

    def _request_measurement(self):
        index = self.instrument.currentIndex()
        if not 0 <= index < len(self._instruments):
            self.status_label.setText("Select an instrument first.")
            return
        self.measurement_requested.emit(self._instruments[index])

    def _request_remeasurement(self, row, _column):
        index = self.instrument.currentIndex()
        if self.start_button.isEnabled() and 0 <= index < len(self._instruments):
            self.remeasure_requested.emit(row, self._instruments[index])

    def _toggle_pause(self):
        paused = self.pause_button.text() == "Pause"
        self.pause_button.setText("Resume" if paused else "Pause")
        self.pause_requested.emit(paused)

    def _reference_options_changed(self, _value=None):
        readings = {
            name: reading for name, _rgb, reading, _path, _time in self._records
        }
        self._updating_reference_fields = True
        try:
            if self.target.currentText() in self.TARGET_PRIMARIES:
                for channel, (x, y) in zip(
                    ("R", "G", "B"), self.TARGET_PRIMARIES[self.target.currentText()]
                ):
                    self.custom_primary_fields[(channel, "x")].setValue(x)
                    self.custom_primary_fields[(channel, "y")].setValue(y)

            white_choice = self.reference_white.currentText()
            white_values = {
                "D65": (0.3127, 0.3290),
                "D50": (0.3457, 0.3585),
                "DCI White": (0.3140, 0.3510),
            }.get(white_choice)
            if white_choice == "Measured White" and readings.get("Grey 255") is not None:
                measured_white = readings["Grey 255"]
                white_values = measured_white.x, measured_white.y
            if white_values is not None:
                self.custom_white_x.setValue(white_values[0])
                self.custom_white_y.setValue(white_values[1])

            gamma_choice = self.reference_gamma.currentText()
            gamma_value = {
                "sRGB Curve": 0.00,
                "Gamma 2.2": 2.20,
                "Gamma 2.4": 2.40,
                "Gamma 2.6": 2.60,
                "BT.1886": 0.00,
            }.get(gamma_choice)
            if gamma_choice == "Measured Gamma" and readings:
                gamma_value = self._measured_gamma(readings)
            if gamma_value is not None:
                self.custom_gamma.setValue(gamma_value)
        finally:
            self._updating_reference_fields = False
        if hasattr(self, "status_label"):
            self._refresh_result_message()

    def _custom_primaries_edited(self, _value=None):
        if self._updating_reference_fields:
            return
        self.target.setCurrentText("Custom Primaries")
        self._refresh_result_message()

    def _custom_white_edited(self, _value=None):
        if self._updating_reference_fields:
            return
        self.reference_white.setCurrentText("Custom xy")
        self._refresh_result_message()

    def _custom_gamma_edited(self, _value=None):
        if self._updating_reference_fields:
            return
        if self.custom_gamma.value() <= 0:
            return
        self.reference_gamma.setCurrentText("Custom")
        self._refresh_result_message()

    def _measurement_plan_changed(self, _value=None):
        steps = self.grey_steps.value()
        grey_levels = tuple(
            min(255, round(index * 256 / (steps - 1))) for index in range(steps)
        )
        patches = []
        if self.grey_check.isChecked():
            patches.extend(
                (f"Grey {level}", (level, level, level)) for level in grey_levels
            )
        if self.rgb_check.isChecked():
            patches.extend((
                ("Red", (255, 0, 0)),
                ("Green", (0, 255, 0)),
                ("Blue", (0, 0, 255)),
            ))
        if self.cmy_check.isChecked():
            patches.extend((
                ("Cyan", (0, 255, 255)),
                ("Magenta", (255, 0, 255)),
                ("Yellow", (255, 255, 0)),
            ))
        self.PATCHES = tuple(patches)
        self.grey_steps.setEnabled(
            self.grey_check.isChecked() and self.grey_check.isEnabled()
        )
        self._populate_patch_table()
        self._restore_visible_readings()
        complete = self._refresh_result_message()
        self.export_button.setEnabled(complete)
        self.start_button.setEnabled(bool(self.PATCHES) and bool(self._instruments))
        self.status_label.setStyleSheet("color: #888;")
        self.status_label.setText("Ready")

    def _set_measurement_plan_enabled(self, enabled):
        self.rgb_check.setEnabled(enabled)
        self.cmy_check.setEnabled(enabled)
        self.grey_check.setEnabled(enabled)
        self.grey_steps.setEnabled(enabled and self.grey_check.isChecked())

    def _populate_patch_table(self):
        self.table.setRowCount(len(self.PATCHES))
        for row, (name, rgb) in enumerate(self.PATCHES):
            swatch_holder = QWidget()
            swatch_layout = QHBoxLayout(swatch_holder)
            swatch_layout.setContentsMargins(4, 2, 4, 2)
            swatch_layout.addStretch()
            swatch = QFrame()
            swatch.setFixedSize(18, 18)
            swatch.setStyleSheet(
                f"background-color: rgb({rgb[0]}, {rgb[1]}, {rgb[2]});"
                "border: 1px solid palette(mid); border-radius: 2px;"
            )
            swatch_layout.addWidget(swatch)
            swatch_layout.addStretch()
            self.table.setCellWidget(row, 0, swatch_holder)
            values = (", ".join(map(str, rgb)), "—", "—", "—", "—")
            for column, value in enumerate(values, 1):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, item)

    def _restore_visible_readings(self):
        records = {item[0]: item[2] for item in self._records}
        for row, (name, _rgb) in enumerate(self.PATCHES):
            reading = records.get(name)
            if reading is None:
                continue
            for column, value in enumerate(
                (f"{reading.Y:.6f}", f"{reading.x:.6f}", f"{reading.y:.6f}"), 2
            ):
                self.table.item(row, column).setText(value)

    def begin_report(self):
        self._restore_visible_readings()
        self._refresh_result_message()
        self.status_label.setText("Starting report…")
        self.status_label.setStyleSheet("color: #888;")
        self.start_button.setEnabled(False)
        self.clear_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.pause_button.setText("Pause")
        self.pause_button.setEnabled(True)
        self.stop_button.setEnabled(True)
        self.instrument.setEnabled(False)
        self._set_measurement_plan_enabled(False)

    def begin_patch(self, index, position=None, total=None):
        self.action_panel.hide()
        name, _rgb = self.PATCHES[index]
        current = index + 1 if position is None else position + 1
        count = len(self.PATCHES) if total is None else total
        self.status_label.setText(f"Measuring {current}/{count} — {name}")

    def has_reading(self, name):
        return any(record[0] == name for record in self._records)

    def add_reading(self, index, reading, correction_path):
        name, rgb = self.PATCHES[index]
        record = (name, rgb, reading, correction_path, datetime.now())
        existing = next(
            (position for position, item in enumerate(self._records) if item[0] == name),
            None,
        )
        if existing is None:
            self._records.append(record)
        else:
            self._records[existing] = record
        for column, value in enumerate(
            (f"{reading.Y:.6f}", f"{reading.x:.6f}", f"{reading.y:.6f}"), 2
        ):
            item = self.table.item(index, column)
            item.setText(value)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._refresh_result_message()

    def add_error(self, index, message):
        self.status_label.setText(message)

    @staticmethod
    def _triangle_area(points):
        return abs(sum(
            points[i][0] * points[(i + 1) % 3][1]
            - points[(i + 1) % 3][0] * points[i][1]
            for i in range(3)
        )) / 2

    @staticmethod
    def _signed_polygon_area(points):
        return sum(
            points[index][0] * points[(index + 1) % len(points)][1]
            - points[(index + 1) % len(points)][0] * points[index][1]
            for index in range(len(points))
        ) / 2

    @classmethod
    def _gamut_coverage(cls, measured, reference):
        """Return the percentage of the reference xy triangle covered by measured."""
        subject = list(measured)
        clip = list(reference)
        if cls._signed_polygon_area(subject) < 0:
            subject.reverse()
        if cls._signed_polygon_area(clip) < 0:
            clip.reverse()

        def inside(point, edge_start, edge_end):
            return (
                (edge_end[0] - edge_start[0]) * (point[1] - edge_start[1])
                - (edge_end[1] - edge_start[1]) * (point[0] - edge_start[0])
            ) >= -1e-12

        def intersection(start, end, edge_start, edge_end):
            dx1, dy1 = end[0] - start[0], end[1] - start[1]
            dx2, dy2 = edge_end[0] - edge_start[0], edge_end[1] - edge_start[1]
            denominator = dx1 * dy2 - dy1 * dx2
            if abs(denominator) < 1e-12:
                return end
            t = (
                (edge_start[0] - start[0]) * dy2
                - (edge_start[1] - start[1]) * dx2
            ) / denominator
            return start[0] + t * dx1, start[1] + t * dy1

        output = subject
        for index, edge_start in enumerate(clip):
            edge_end = clip[(index + 1) % len(clip)]
            input_points = output
            output = []
            if not input_points:
                break
            start = input_points[-1]
            for end in input_points:
                if inside(end, edge_start, edge_end):
                    if not inside(start, edge_start, edge_end):
                        output.append(intersection(start, end, edge_start, edge_end))
                    output.append(end)
                elif inside(start, edge_start, edge_end):
                    output.append(intersection(start, end, edge_start, edge_end))
                start = end

        reference_area = abs(cls._signed_polygon_area(clip))
        intersection_area = abs(cls._signed_polygon_area(output)) if len(output) >= 3 else 0
        return intersection_area / reference_area * 100 if reference_area else 0

    @staticmethod
    def _xy_to_xyz(x, y):
        return (x / y, 1.0, (1.0 - x - y) / y)

    @staticmethod
    def _solve_3x3(matrix, vector):
        a, b, c = matrix
        determinant = (
            a[0] * (b[1] * c[2] - b[2] * c[1])
            - a[1] * (b[0] * c[2] - b[2] * c[0])
            + a[2] * (b[0] * c[1] - b[1] * c[0])
        )
        if abs(determinant) < 1e-12:
            raise ValueError("Reference gamut cannot form a valid RGB matrix")

        def determinant_with_column(column):
            replaced = [list(row) for row in matrix]
            for row in range(3):
                replaced[row][column] = vector[row]
            x, y, z = replaced
            return (
                x[0] * (y[1] * z[2] - y[2] * z[1])
                - x[1] * (y[0] * z[2] - y[2] * z[0])
                + x[2] * (y[0] * z[1] - y[1] * z[0])
            )

        return tuple(determinant_with_column(column) / determinant for column in range(3))

    @classmethod
    def _rgb_matrix(cls, primaries, white_xy):
        primary_xyz = [cls._xy_to_xyz(*point) for point in primaries]
        matrix = tuple(
            tuple(primary_xyz[column][row] for column in range(3))
            for row in range(3)
        )
        scales = cls._solve_3x3(matrix, cls._xy_to_xyz(*white_xy))
        return tuple(
            tuple(matrix[row][column] * scales[column] for column in range(3))
            for row in range(3)
        )

    def _selected_primaries(self):
        primaries = tuple(
            (
                self.custom_primary_fields[(channel, "x")].value(),
                self.custom_primary_fields[(channel, "y")].value(),
            )
            for channel in ("R", "G", "B")
        )
        if any(x + y > 1 + 1e-9 for x, y in primaries) or self._triangle_area(primaries) < 1e-8:
            raise ValueError("Custom primary xy values are invalid")
        return primaries

    @staticmethod
    def _xyz_to_lab(xyz, white_xyz):
        delta = 6 / 29

        def f(value):
            return value ** (1 / 3) if value > delta ** 3 else value / (3 * delta ** 2) + 4 / 29

        fx, fy, fz = (f(value / white) for value, white in zip(xyz, white_xyz))
        return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)

    @staticmethod
    def _delta_e_2000(lab1, lab2):
        l1, a1, b1 = lab1
        l2, a2, b2 = lab2
        c1 = math.hypot(a1, b1)
        c2 = math.hypot(a2, b2)
        c_bar = (c1 + c2) / 2
        g = 0.5 * (1 - math.sqrt(c_bar ** 7 / (c_bar ** 7 + 25 ** 7)))
        ap1, ap2 = (1 + g) * a1, (1 + g) * a2
        cp1, cp2 = math.hypot(ap1, b1), math.hypot(ap2, b2)

        def hue(a, b):
            angle = math.degrees(math.atan2(b, a))
            return angle + 360 if angle < 0 else angle

        hp1 = hue(ap1, b1) if cp1 else 0
        hp2 = hue(ap2, b2) if cp2 else 0
        delta_l = l2 - l1
        delta_c = cp2 - cp1
        hue_difference = hp2 - hp1
        if cp1 * cp2 == 0:
            delta_h_angle = 0
        elif abs(hue_difference) <= 180:
            delta_h_angle = hue_difference
        elif hue_difference > 180:
            delta_h_angle = hue_difference - 360
        else:
            delta_h_angle = hue_difference + 360
        delta_h = 2 * math.sqrt(cp1 * cp2) * math.sin(math.radians(delta_h_angle / 2))
        l_bar = (l1 + l2) / 2
        cp_bar = (cp1 + cp2) / 2
        if cp1 * cp2 == 0:
            hp_bar = hp1 + hp2
        elif abs(hp1 - hp2) <= 180:
            hp_bar = (hp1 + hp2) / 2
        elif hp1 + hp2 < 360:
            hp_bar = (hp1 + hp2 + 360) / 2
        else:
            hp_bar = (hp1 + hp2 - 360) / 2
        t = (
            1 - 0.17 * math.cos(math.radians(hp_bar - 30))
            + 0.24 * math.cos(math.radians(2 * hp_bar))
            + 0.32 * math.cos(math.radians(3 * hp_bar + 6))
            - 0.20 * math.cos(math.radians(4 * hp_bar - 63))
        )
        sl = 1 + 0.015 * (l_bar - 50) ** 2 / math.sqrt(20 + (l_bar - 50) ** 2)
        sc = 1 + 0.045 * cp_bar
        sh = 1 + 0.015 * cp_bar * t
        rt = (
            -2 * math.sqrt(cp_bar ** 7 / (cp_bar ** 7 + 25 ** 7))
            * math.sin(math.radians(60 * math.exp(-((hp_bar - 275) / 25) ** 2)))
        )
        dl, dc, dh = delta_l / sl, delta_c / sc, delta_h / sh
        return math.sqrt(max(0.0, dl * dl + dc * dc + dh * dh + rt * dc * dh))

    def _measured_gamma(self, readings):
        black = readings.get("Grey 0")
        white = readings.get("Grey 255")
        if black is None or white is None or white.Y <= black.Y:
            return 2.2
        samples = []
        for name, reading in readings.items():
            if not name.startswith("Grey ") or name in ("Grey 0", "Grey 255"):
                continue
            try:
                level = int(name.split(" ", 1)[1])
            except ValueError:
                continue
            if reading is None:
                continue
            normalized = (reading.Y - black.Y) / (white.Y - black.Y)
            if normalized > 0:
                samples.append((math.log(level / 255), math.log(normalized)))
        denominator = sum(x * x for x, _y in samples)
        return sum(x * y for x, y in samples) / denominator if denominator else 2.2

    def _reference_white_xy(self, readings):
        return self.custom_white_x.value(), self.custom_white_y.value()

    def _linear_value(self, encoded, readings):
        choice = self.reference_gamma.currentText()
        if choice == "sRGB Curve":
            return encoded / 12.92 if encoded <= 0.04045 else ((encoded + 0.055) / 1.055) ** 2.4
        if choice == "BT.1886":
            black = readings.get("Grey 0")
            white = readings.get("Grey 255")
            ratio = max(0.0, black.Y / white.Y) if black and white and white.Y > 0 else 0.0
            black_root = ratio ** (1 / 2.4)
            return (encoded * (1 - black_root) + black_root) ** 2.4
        gamma = {
            "Gamma 2.2": 2.2,
            "Gamma 2.4": 2.4,
            "Gamma 2.6": 2.6,
            "Measured Gamma": self._measured_gamma(readings),
            "Custom": max(0.1, self.custom_gamma.value()),
        }.get(choice, 2.2)
        return encoded ** gamma

    def _delta_e_results(self, readings):
        white = readings.get("Grey 255")
        if white is None or white.Y <= 0:
            return {}
        white_xy = self._reference_white_xy(readings)
        white_xyz = self._xy_to_xyz(*white_xy)
        matrix = self._rgb_matrix(self._selected_primaries(), white_xy)
        results = {}
        for name, rgb in self.PATCHES:
            reading = readings.get(name)
            if reading is None:
                continue
            linear = tuple(self._linear_value(channel / 255, readings) for channel in rgb)
            reference_xyz = tuple(
                sum(matrix[row][column] * linear[column] for column in range(3))
                for row in range(3)
            )
            measured_xyz = (reading.X / white.Y, reading.Y / white.Y, reading.Z / white.Y)
            results[name] = self._delta_e_2000(
                self._xyz_to_lab(measured_xyz, white_xyz),
                self._xyz_to_lab(reference_xyz, white_xyz),
            )
        return results

    def finish_report(self, error=None):
        self.action_panel.hide()
        self.start_button.setEnabled(bool(self._instruments))
        self.clear_button.setEnabled(True)
        self.instrument.setEnabled(bool(self._instruments))
        self.target.setEnabled(True)
        self._set_measurement_plan_enabled(True)
        self.pause_button.setText("Pause")
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        if error:
            self.export_button.setEnabled(bool(self._records))
            self.status_label.setStyleSheet("color: #d99a3e;")
            self.status_label.setText(error)
            return
        if not self._refresh_result_message():
            self.status_label.setStyleSheet("color: #d99a3e;")
            self.status_label.setText("Report incomplete.")
            return
        self.status_label.setStyleSheet("color: #888;")
        self.status_label.setText("Report complete.")
        self.export_button.setEnabled(True)

    def _update_reference_charts(self, readings):
        try:
            reference_primaries = self._selected_primaries()
            reference_white = self._reference_white_xy(readings)
        except ValueError:
            self.eotf_chart.set_data([], [])
            self.cie_xy_chart.set_data([], [], None, None)
            return
        reference_eotf = [
            (step / 100.0, self._linear_value(step / 100.0, readings))
            for step in range(101)
        ]
        self.eotf_chart.set_data([], reference_eotf)
        self.cie_xy_chart.set_data(
            reference_primaries, [], reference_white, None
        )

    def _refresh_result_message(self, _text=None):
        readings = {name: reading for name, _rgb, reading, _path, _time in self._records}
        self._update_reference_charts(readings)
        if not readings:
            self.result_label.clear()
            self._update_delta_e_column({})
            return False
        black = readings.get("Grey 0")
        white = readings.get("Grey 255")
        self._updating_reference_fields = True
        try:
            if self.reference_white.currentText() == "Measured White":
                if white is not None:
                    self.custom_white_x.setValue(white.x)
                    self.custom_white_y.setValue(white.y)
            if self.reference_gamma.currentText() == "Measured Gamma" and readings:
                self.custom_gamma.setValue(self._measured_gamma(readings))
        finally:
            self._updating_reference_fields = False
        try:
            reference_primaries = self._selected_primaries()
            reference_white = self._reference_white_xy(readings)
            delta_e = self._delta_e_results(readings)
            matrix = self._rgb_matrix(reference_primaries, reference_white)
        except ValueError as error:
            self.result_label.setText(str(error))
            self._update_delta_e_column({})
            self.eotf_chart.set_data([], [])
            self.cie_xy_chart.set_data([], [], None, None)
            return False

        measured_eotf = []
        if white is not None and white.Y > 0:
            for name, rgb in self.PATCHES:
                if not name.startswith("Grey "):
                    continue
                level = rgb[0]
                reading = readings.get(name)
                if reading is None:
                    continue
                normalized_xyz = (
                    reading.X / white.Y,
                    reading.Y / white.Y,
                    reading.Z / white.Y,
                )
                red, green, blue = self._solve_3x3(matrix, normalized_xyz)
                measured_eotf.append((level / 255.0, red, green, blue))
        reference_eotf = [
            (step / 100.0, self._linear_value(step / 100.0, readings))
            for step in range(101)
        ]
        self.eotf_chart.set_data(measured_eotf, reference_eotf)
        measured_primaries = tuple(
            (readings[name].x, readings[name].y) if name in readings else None
            for name in ("Red", "Green", "Blue")
        )
        self.cie_xy_chart.set_data(
            reference_primaries,
            measured_primaries,
            reference_white,
            (white.x, white.y) if white is not None else None,
        )

        complete_primaries = all(value is not None for value in measured_primaries)
        gamut_coverage = (
            self._gamut_coverage(measured_primaries, reference_primaries)
            if complete_primaries else None
        )
        contrast = None
        if black is not None and white is not None:
            contrast = "∞" if black.Y <= 0.000001 else f"{white.Y / black.Y:,.0f}:1"
        has_gamma_samples = (
            black is not None
            and white is not None
            and any(
                name.startswith("Grey ") and name not in ("Grey 0", "Grey 255")
                for name in readings
            )
        )
        gamma = self._measured_gamma(readings) if has_gamma_samples else None
        delta_values = list(delta_e.values())
        average_delta = sum(delta_values) / len(delta_values) if delta_values else None
        maximum_delta = max(delta_values) if delta_values else None
        self._update_delta_e_column(delta_e)
        missing = "—"
        self.result_label.setText(
            f"White {f'{white.Y:.2f} cd/m²' if white is not None else missing} · "
            f"Black {f'{black.Y:.4f} cd/m²' if black is not None else missing} · "
            f"Contrast {contrast if contrast is not None else missing}\n"
            f"White xy {f'{white.x:.4f}, {white.y:.4f}' if white is not None else missing} · "
            f"Approx. gamma {f'{gamma:.2f}' if gamma is not None else missing} · "
            f"Gamut coverage {f'{gamut_coverage:.1f}%' if gamut_coverage is not None else missing}\n"
            f"Average ΔE2000 {f'{average_delta:.2f}' if average_delta is not None else missing} · "
            f"Maximum ΔE2000 {f'{maximum_delta:.2f}' if maximum_delta is not None else missing}"
        )
        return all(name in readings for name, _rgb in self.PATCHES)

    def _update_delta_e_column(self, delta_e):
        for row, (name, _rgb) in enumerate(self.PATCHES):
            item = self.table.item(row, 5)
            if item is not None:
                item.setText(f"{delta_e[name]:.2f}" if name in delta_e else "—")
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

    def begin_remeasurement(self, index):
        self.action_panel.hide()
        self.start_button.setEnabled(False)
        self.clear_button.setEnabled(False)
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.instrument.setEnabled(False)
        self._set_measurement_plan_enabled(False)
        self.status_label.setText(f"Remeasuring {self.PATCHES[index][0]}…")

    def set_paused(self, completed_count, total=None):
        self.pause_button.setText("Resume")
        count = len(self.PATCHES) if total is None else total
        self.status_label.setText(
            f"Paused after {completed_count}/{count}. Click Resume to continue."
        )

    def show_action_prompt(self, title, message):
        self.action_message.setText(f"{title}\n{message}")
        self.action_panel.show()

    def _save_outputs(self):
        if not self._records:
            return
        if not any((
            self.summary_csv_check.isChecked(),
            self.measurements_csv_check.isChecked(),
            self.png_check.isChecked(),
        )):
            self.status_label.setStyleSheet("color: #d99a3e;")
            self.status_label.setText("Select at least one output format.")
            return
        if not self.output_path.text().strip():
            self._choose_output()
        if not self.output_path.text().strip():
            return
        output_root = Path(self.output_path.text()).expanduser()
        output_root.mkdir(parents=True, exist_ok=True)
        display = re.sub(
            r'[\\/:*?"<>|]+', "-", self.display_name.text().strip()
        ).strip(" .") or "Display"
        stem = f"{datetime.now():%Y%m%d_%H%M%S}_{display}_Report"
        root = output_root / stem
        root.mkdir(parents=True, exist_ok=True)
        instrument = self._instruments[self.instrument.currentIndex()]
        readings = {
            name: reading for name, _rgb, reading, _path, _time in self._records
        }
        delta_e = self._delta_e_results(readings)
        black = readings["Grey 0"]
        white = readings["Grey 255"]
        measured_gamma = self._measured_gamma(readings)
        reference_primaries = self._selected_primaries()
        reference_white = self._reference_white_xy(readings)
        measured_primaries = tuple(
            (readings[name].x, readings[name].y) if name in readings else None
            for name in ("Red", "Green", "Blue")
        )
        gamut_coverage = (
            self._gamut_coverage(measured_primaries, reference_primaries)
            if all(value is not None for value in measured_primaries) else None
        )
        delta_values = list(delta_e.values())
        average_delta = sum(delta_values) / len(delta_values)
        maximum_delta = max(delta_values)
        contrast = "Infinity" if black.Y <= 0.000001 else white.Y / black.Y
        correction_path = next(
            (item[3] for item in self._records if item[3] is not None), None
        )
        saved = []

        if self.summary_csv_check.isChecked():
            summary_path = root / f"{stem}_Summary.csv"
            with summary_path.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(("Field", "Value"))
                writer.writerow(("Display Name", self.display_name.text().strip()))
                writer.writerow(("Instrument Name", instrument.name))
                writer.writerow(("Instrument ID", instrument.display_identifier))
                writer.writerow(("CCMX", correction_path.name if correction_path else "Raw"))
                writer.writerow(("CCMX Path", str(correction_path or "")))
                writer.writerow(("Reference Gamut", self.target.currentText()))
                for channel, (x, y) in zip(("R", "G", "B"), reference_primaries):
                    writer.writerow((f"Reference {channel} x", f"{x:.6f}"))
                    writer.writerow((f"Reference {channel} y", f"{y:.6f}"))
                writer.writerow(("Reference White Point", self.reference_white.currentText()))
                writer.writerow(("Reference White x", f"{reference_white[0]:.6f}"))
                writer.writerow(("Reference White y", f"{reference_white[1]:.6f}"))
                writer.writerow(("Reference Gamma", self.reference_gamma.currentText()))
                writer.writerow((
                    "Reference Gamma Value",
                    "—" if self.custom_gamma.value() <= 0 else f"{self.custom_gamma.value():.4f}",
                ))
                writer.writerow(("White cd/m²", f"{white.Y:.6f}"))
                writer.writerow(("Black cd/m²", f"{black.Y:.6f}"))
                writer.writerow(("Contrast", contrast))
                writer.writerow(("White x", f"{white.x:.6f}"))
                writer.writerow(("White y", f"{white.y:.6f}"))
                writer.writerow(("Approx. Gamma", f"{measured_gamma:.6f}"))
                writer.writerow((
                    "Gamut Coverage %",
                    f"{gamut_coverage:.4f}" if gamut_coverage is not None else "—",
                ))
                writer.writerow(("Average Delta E 2000", f"{average_delta:.4f}"))
                writer.writerow(("Maximum Delta E 2000", f"{maximum_delta:.4f}"))
            saved.append("Summary CSV")

        if self.measurements_csv_check.isChecked():
            measurements_path = root / f"{stem}_Measurements.csv"
            with measurements_path.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow((
                    "Patch", "R", "G", "B", "Time", "Y", "x", "y", "X", "Z",
                    "Delta E 2000", "CCMX", "CCMX Path",
                ))
                for name, rgb, reading, item_correction, measured_at in self._records:
                    writer.writerow((
                        name, *rgb, measured_at.strftime("%H:%M:%S"), reading.Y,
                        reading.x, reading.y, reading.X, reading.Z,
                        f"{delta_e[name]:.4f}" if name in delta_e else "—",
                        item_correction.name if item_correction else "Raw",
                        str(item_correction or ""),
                    ))
            saved.append("Measurements CSV")

        if self.png_check.isChecked():
            try:
                self._save_chart_pngs(root, stem)
            except Exception as error:
                self.status_label.setStyleSheet("color: #d99a3e;")
                self.status_label.setText(
                    f"Saved {', '.join(saved)}, but PNG export failed: {error}"
                )
                return
            saved.append("4 PNG files")

        self.status_label.setStyleSheet("color: #888;")
        self.status_label.setText(f"Saved {', '.join(saved)} to {root}.")

    def _legacy_save_csv_removed(self):
        """Kept as a marker for the former combined CSV layout."""
        return
        with Path(path).open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            instrument = self._instruments[self.instrument.currentIndex()]
            readings = {
                name: reading for name, _rgb, reading, _path, _time in self._records
            }
            delta_e = self._delta_e_results(readings)
            black = readings["Grey 0"]
            white = readings["Grey 255"]
            measured_gamma = self._measured_gamma(readings)
            reference_primaries = self._selected_primaries()
            reference_white = self._reference_white_xy(readings)
            measured_primaries = tuple(
                (readings[name].x, readings[name].y) if name in readings else None
                for name in ("Red", "Green", "Blue")
            )
            gamut_coverage = (
                self._gamut_coverage(measured_primaries, reference_primaries)
                if all(value is not None for value in measured_primaries) else None
            )
            delta_values = list(delta_e.values())
            average_delta = sum(delta_values) / len(delta_values)
            maximum_delta = max(delta_values)
            contrast = "Infinity" if black.Y <= 0.000001 else white.Y / black.Y
            correction_path = next(
                (item[3] for item in self._records if item[3] is not None), None
            )
            writer.writerow(("Reference", "Value"))
            writer.writerow(("Display Name", self.display_name.text().strip()))
            writer.writerow(("Instrument Name", instrument.name))
            writer.writerow(("Instrument ID", instrument.display_identifier))
            writer.writerow(("CCMX", correction_path.name if correction_path else "Raw"))
            writer.writerow(("CCMX Path", str(correction_path or "")))
            writer.writerow(("Reference Gamut", self.target.currentText()))
            for channel, (x, y) in zip(("R", "G", "B"), reference_primaries):
                writer.writerow((f"Reference {channel} x", f"{x:.6f}"))
                writer.writerow((f"Reference {channel} y", f"{y:.6f}"))
            writer.writerow(("Reference White Point", self.reference_white.currentText()))
            writer.writerow(("Reference White x", f"{reference_white[0]:.6f}"))
            writer.writerow(("Reference White y", f"{reference_white[1]:.6f}"))
            writer.writerow(("Reference Gamma", self.reference_gamma.currentText()))
            writer.writerow((
                "Reference Gamma Value",
                "—" if self.custom_gamma.value() <= 0 else f"{self.custom_gamma.value():.4f}",
            ))
            writer.writerow(())
            writer.writerow(("Report", "Value"))
            writer.writerow(("White cd/m²", f"{white.Y:.6f}"))
            writer.writerow(("Black cd/m²", f"{black.Y:.6f}"))
            writer.writerow(("Contrast", contrast))
            writer.writerow(("White x", f"{white.x:.6f}"))
            writer.writerow(("White y", f"{white.y:.6f}"))
            writer.writerow(("Approx. Gamma", f"{measured_gamma:.6f}"))
            writer.writerow((
                "Gamut Coverage %",
                f"{gamut_coverage:.4f}" if gamut_coverage is not None else "—",
            ))
            writer.writerow(("Average Delta E 2000", f"{average_delta:.4f}"))
            writer.writerow(("Maximum Delta E 2000", f"{maximum_delta:.4f}"))
            writer.writerow(())
            writer.writerow(("Measurements",))
            writer.writerow((
                "Patch", "R", "G", "B", "Time", "Y", "x", "y", "X", "Z",
                "Delta E 2000", "CCMX", "CCMX Path",
            ))
            for name, rgb, reading, correction_path, measured_at in self._records:
                writer.writerow((
                    name, *rgb,
                    measured_at.strftime("%H:%M:%S"), reading.Y, reading.x,
                    reading.y, reading.X, reading.Z,
                    f"{delta_e.get(name, float('nan')):.4f}",
                    correction_path.name if correction_path else "Raw",
                    str(correction_path or ""),
                ))
        if self.png_check.isChecked():
            try:
                self._save_chart_pngs(root, stem)
            except Exception as error:
                self.status_label.setStyleSheet("color: #d99a3e;")
                self.status_label.setText(f"CSV saved, but PNG export failed: {error}")
                return
            self.status_label.setText(f"Saved CSV and 4 PNG files to {root}.")
        else:
            self.status_label.setText(f"Saved to {path}.")

    @staticmethod
    def _chart_export_palette(dark):
        palette = QPalette()
        background = QColor("#181818") if dark else QColor("#f5f5f5")
        foreground = QColor("#eeeeee") if dark else QColor("#202020")
        palette.setColor(QPalette.ColorRole.Window, background)
        palette.setColor(QPalette.ColorRole.WindowText, foreground)
        palette.setColor(QPalette.ColorRole.Base, background)
        palette.setColor(QPalette.ColorRole.Text, foreground)
        return palette

    @staticmethod
    def _render_chart_png(chart, path, dark):
        logical_size = 500
        scale = 4
        chart.resize(logical_size, logical_size)
        palette = ReportDialog._chart_export_palette(dark)
        chart.setPalette(palette)
        chart.setAutoFillBackground(True)
        pixmap = QPixmap(logical_size * scale, logical_size * scale)
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(palette.color(QPalette.ColorRole.Window))
        chart.render(pixmap)
        if not pixmap.save(str(path), "PNG"):
            raise OSError(f"Could not save {path.name}")

    def _save_chart_pngs(self, root, stem):
        for theme_name, dark in (("Light", False), ("Dark", True)):
            eotf = EotfChart()
            eotf.set_data(self.eotf_chart._measured, self.eotf_chart._reference)
            self._render_chart_png(
                eotf, root / f"{stem}_EOTF_{theme_name}.png", dark
            )
            cie_xy = CieXyChart()
            cie_xy.set_data(
                self.cie_xy_chart._reference,
                self.cie_xy_chart._measured,
                self.cie_xy_chart._reference_white,
                self.cie_xy_chart._measured_white,
            )
            self._render_chart_png(
                cie_xy, root / f"{stem}_CIE-xy_{theme_name}.png", dark
            )

    def _reference_gamut_description(self):
        if self.target.currentText() != "Custom Primaries":
            return self.target.currentText()
        primaries = self._selected_primaries()
        return "Custom Primaries " + " ".join(
            f"{channel}({x:.4f},{y:.4f})"
            for channel, (x, y) in zip(("R", "G", "B"), primaries)
        )

    def _reference_white_description(self):
        if self.reference_white.currentText() == "Custom xy":
            return f"Custom xy {self.custom_white_x.value():.4f}, {self.custom_white_y.value():.4f}"
        return self.reference_white.currentText()

    def _reference_gamma_description(self, readings):
        if self.reference_gamma.currentText() == "Custom":
            return f"Gamma {self.custom_gamma.value():.2f}"
        if self.reference_gamma.currentText() == "Measured Gamma":
            return f"Measured Gamma {self._measured_gamma(readings):.4f}"
        return self.reference_gamma.currentText()

    def _choose_output(self):
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose Output Folder",
            self.output_path.text().strip() or str(Path.home()),
        )
        if selected:
            self.output_path.setText(selected)

    def _clear_report(self):
        self._records = []
        self._populate_patch_table()
        self.result_label.clear()
        self._update_reference_charts({})
        self.status_label.setStyleSheet("color: #888;")
        self.status_label.setText("Ready")
        self.export_button.setEnabled(False)


class ManualMeasurementDialog(QDialog):
    measurement_requested = Signal(object)
    continue_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manual Measurement")
        self.setMinimumWidth(900)
        self._instruments = []
        self._records = []
        self._instrument_checks = {}
        self._column_checks = {}
        self._output_folder_dialog = None
        self._save_after_choose = False
        self._scan_callback = None
        self._correction_status_provider = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        instrument_section = QWidget()
        instrument_layout = QVBoxLayout(instrument_section)
        instrument_layout.setContentsMargins(0, 0, 0, 0)
        instrument_layout.setSpacing(6)
        instrument_layout.addWidget(_heading("Instrument"))
        self.scan_button = QPushButton("Scan Instruments")
        self.clear_button = QPushButton("Clear Measurement")
        self.instrument_choices = QWidget()
        self.instrument_choices_layout = QGridLayout(self.instrument_choices)
        self.instrument_choices_layout.setContentsMargins(0, 0, 0, 0)
        self.instrument_choices_layout.setHorizontalSpacing(8)
        self.instrument_choices_layout.setVerticalSpacing(6)
        self.instrument_choices_layout.setColumnStretch(0, 1)
        self.instrument_placeholder = QLabel("Scanning instruments…")
        self.instrument_placeholder.setStyleSheet("color: #888;")
        self.instrument_choices_layout.addWidget(self.instrument_placeholder, 0, 0)
        self.instrument_choices_layout.addWidget(self.scan_button, 0, 1)
        instrument_layout.addWidget(self.instrument_choices)
        layout.addWidget(instrument_section)
        layout.addSpacing(12)

        measurement_section = QWidget()
        measurement_layout = QVBoxLayout(measurement_section)
        measurement_layout.setContentsMargins(0, 0, 0, 0)
        measurement_layout.setSpacing(6)
        measurement_layout.addWidget(_heading("Measurement"))
        measurement_controls = QHBoxLayout()
        measurement_controls.setContentsMargins(0, 0, 0, 0)
        measurement_controls.setSpacing(8)
        for key, label, checked in (
            ("index", "Item Number", True),
            ("target_color", "Target Color", True),
            ("target_rgb", "Target RGB", True),
            ("time", "Measurement Time", True),
            ("yxy", "Yxy", True),
            ("yuv", "Yuv", False),
            ("xyz", "XYZ", False),
            ("instrument_name", "Instrument Name", True),
            ("instrument_id", "Instrument ID", True),
            ("ccmx", "CCMX", True),
        ):
            checkbox = QCheckBox(label)
            checkbox.setChecked(checked)
            checkbox.toggled.connect(self._render_records)
            self._column_checks[key] = checkbox
            measurement_controls.addWidget(checkbox)
        measurement_controls.addStretch()
        self.measure_button = QPushButton("Measure")
        self.measure_button.setDefault(True)
        measurement_layout.addLayout(measurement_controls)

        self.table = QTableWidget(0, 7)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        measurement_layout.addWidget(self.table, 1)
        measure_row = QHBoxLayout()
        measure_row.setContentsMargins(0, 0, 0, 0)
        measure_row.addStretch()
        measure_row.addWidget(self.measure_button)
        measurement_layout.addLayout(measure_row)
        layout.addWidget(measurement_section, 1)
        self._render_records()

        output_section = QWidget()
        output_layout = QVBoxLayout(output_section)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.setSpacing(6)
        output_layout.addWidget(_heading("Output"))
        output_form = QGridLayout()
        output_form.setContentsMargins(0, 0, 0, 0)
        output_form.setHorizontalSpacing(10)
        output_form.setVerticalSpacing(6)
        self.display_name = QLineEdit()
        self.display_name.setPlaceholderText("Optional; used in file name")
        output_form.addWidget(QLabel("Display name"), 0, 0)
        output_form.addWidget(self.display_name, 0, 1, 1, 2)
        self.output_path = QLineEdit()
        self.output_path.setPlaceholderText("Choose when saving")
        self.choose_output_button = QPushButton("Choose…")
        self.choose_output_button.setFixedWidth(92)
        output_form.addWidget(QLabel("Storage Path"), 1, 0)
        output_form.addWidget(self.output_path, 1, 1)
        output_form.addWidget(self.choose_output_button, 1, 2)
        self.csv_check = QCheckBox("CSV")
        self.csv_check.setChecked(True)
        self.clear_button.setText("Delete All")
        self.delete_button = QPushButton("Delete Selected")
        self.save_button = QPushButton("Save")
        self.save_button.setFixedWidth(92)
        format_row = QHBoxLayout()
        format_row.setContentsMargins(0, 0, 0, 0)
        format_row.setSpacing(output_form.horizontalSpacing())
        format_row.addWidget(self.csv_check)
        format_row.addStretch()
        format_row.addWidget(self.clear_button)
        format_row.addWidget(self.delete_button)
        format_row.addWidget(self.save_button)
        output_form.addWidget(QLabel("Output Format"), 2, 0)
        output_form.addLayout(format_row, 2, 1, 1, 2)
        output_layout.addLayout(output_form)
        layout.addWidget(output_section)

        self.message_frame = QFrame()
        self.message_frame.setFixedHeight(124)
        self.message_frame.setFrameShape(QFrame.Shape.NoFrame)
        message_layout = QVBoxLayout(self.message_frame)
        message_layout.setContentsMargins(0, 0, 0, 0)
        message_layout.setSpacing(8)
        self.measure_status = QLabel("Ready")
        self.measure_status.setWordWrap(True)
        self.measure_status.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self.measure_status.setStyleSheet(
            "color: #888; border: none; background: transparent;"
        )
        message_layout.addWidget(self.measure_status)

        self.action_frame = QFrame(self.message_frame)
        self.action_frame.setFrameShape(QFrame.Shape.NoFrame)
        action_layout = QVBoxLayout(self.action_frame)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(8)
        self.action_title = _heading("")
        self.action_message = QLabel()
        self.action_message.setWordWrap(True)
        self.action_cancel = QPushButton("Cancel")
        self.action_continue = QPushButton("Continue")
        self.action_continue.setDefault(True)
        for button in (self.action_cancel, self.action_continue):
            button.setMinimumHeight(button.sizeHint().height())
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        action_layout.addWidget(self.action_title)
        action_layout.addWidget(self.action_message)
        action_layout.addLayout(_button_row(self.action_cancel, self.action_continue))
        self.action_frame.hide()
        message_layout.addWidget(self.action_frame)
        message_layout.addStretch()
        layout.addWidget(self.message_frame)

        self.cancel_button = QPushButton("Cancel")
        layout.addLayout(_button_row(self.cancel_button))

        self.measure_button.clicked.connect(self._request_measurement)
        self.scan_button.clicked.connect(self._scan_instruments)
        self.clear_button.clicked.connect(self._clear_readings)
        self.delete_button.clicked.connect(self._delete_selected)
        self.save_button.clicked.connect(self._save_csv)
        self.choose_output_button.clicked.connect(self._choose_output)
        self.csv_check.toggled.connect(self._update_record_actions)
        self.cancel_button.clicked.connect(self.close)
        self.action_continue.clicked.connect(self._continue_action)
        self.action_cancel.clicked.connect(self._cancel_action)

        self._update_record_actions()
        layout.activate()
        preferred_height = max(650, self.sizeHint().height())
        self.resize(900, preferred_height)
        self.setMinimumHeight(preferred_height)
        self.measure_button.setDefault(True)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._focus_measure_button)

    def _focus_measure_button(self):
        self.measure_button.setDefault(True)
        self.measure_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def set_scan_callback(self, callback):
        self._scan_callback = callback

    def set_correction_status_provider(self, callback):
        self._correction_status_provider = callback

    def _scan_instruments(self):
        if self._scan_callback is not None:
            self._scan_callback()

    def _request_measurement(self):
        selected = [
            instrument for instrument in self._instruments
            if self._instrument_checks.get((instrument.port, instrument.path)) is not None
            and self._instrument_checks[(instrument.port, instrument.path)].isChecked()
        ]
        if not selected:
            QMessageBox.warning(self, "No Instrument", "Select at least one instrument.")
            return
        self.measurement_requested.emit(selected)

    def set_busy(self, busy: bool, count: int = 0):
        has_selection = any(
            checkbox.isChecked() for checkbox in self._instrument_checks.values()
        )
        self.measure_button.setEnabled(not busy and has_selection)
        for checkbox in self._instrument_checks.values():
            checkbox.setEnabled(not busy)
        self.scan_button.setEnabled(not busy)
        self.clear_button.setEnabled(not busy and bool(self._records))
        if busy:
            self.measure_status.setText(f"Measuring {count} instrument(s)…")

    def show_action_prompt(self, title: str, message: str):
        self.action_title.setText(title)
        self.action_message.setText(message)
        self.action_title.setStyleSheet("color: #d99a3e; font-size: 13px; font-weight: bold;")
        self.action_message.setStyleSheet("color: #d99a3e;")
        self.action_message.show()
        self.action_cancel.show()
        self.action_continue.show()
        self.action_frame.show()
        self.action_continue.setFocus()

    def hide_action_prompt(self):
        self.action_frame.hide()

    def _continue_action(self):
        action = self.action_title.text()
        if action == "Instrument Calibration":
            self.action_title.setText("Calibrating Instrument…")
        elif action == "Measurement Position":
            self.action_title.setText("Waiting for instrument reading…")
        else:
            self.action_title.setText("Continuing measurement…")
        self.action_message.clear()
        self.action_message.hide()
        self.action_cancel.hide()
        self.action_continue.hide()
        self.continue_requested.emit()

    def _cancel_action(self):
        self.hide_action_prompt()
        self.measure_status.setText("Cancelling…")
        self.cancel_requested.emit()

    def add_reading(self, instrument, reading, target_rgb=None, correction_path=None):
        self.hide_action_prompt()
        instrument = next(
            (
                current for current in self._instruments
                if current.port == instrument.port and current.path == instrument.path
            ),
            instrument,
        )
        self._records.append({
            "time": QTime.currentTime().toString("HH:mm:ss"),
            "instrument": instrument,
            "reading": reading,
            "target_rgb": target_rgb,
            "correction_path": str(correction_path) if correction_path else "",
        })
        self._render_records()
        self._update_record_actions()
        self.measure_status.setText(f"Reading received from {instrument.name}")

    def show_warning(self, message: str):
        self.hide_action_prompt()
        self.measure_status.setStyleSheet("color: #d99a3e;")
        self.measure_status.setText(message)

    def add_error(self, instrument, message: str):
        self.hide_action_prompt()
        self.measure_status.setStyleSheet("color: #d99a3e;")
        self.measure_status.setText(f"Measurement failed for {instrument.name}: {message}")

    def measurement_finished(self):
        self.hide_action_prompt()
        self.set_busy(False)
        if not self.measure_status.text().startswith("Measurement failed"):
            self.measure_status.setStyleSheet("color: #888;")
            self.measure_status.setText("Measurement complete.")

    def set_instruments(self, instruments):
        previously_checked = {
            key for key, checkbox in self._instrument_checks.items()
            if checkbox.isChecked()
        }
        had_choices = bool(self._instrument_checks)
        self._instruments = list(instruments)
        instruments_by_key = {
            (instrument.port, instrument.path): instrument
            for instrument in self._instruments
        }
        for record in self._records:
            old_instrument = record["instrument"]
            updated = instruments_by_key.get((old_instrument.port, old_instrument.path))
            if updated is not None:
                record["instrument"] = updated
        while self.instrument_choices_layout.count():
            item = self.instrument_choices_layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self.scan_button:
                widget.deleteLater()
        self._instrument_checks = {}
        if self._instruments:
            for row, instrument in enumerate(self._instruments):
                key = (instrument.port, instrument.path)
                correction_status = (
                    self._correction_status_provider(instrument)
                    if self._correction_status_provider is not None else "Raw"
                )
                checkbox = QCheckBox(
                    f"{_instrument_label(instrument)} — {correction_status}"
                )
                checkbox.setChecked(key in previously_checked if had_choices else True)
                checkbox.toggled.connect(self._instrument_selection_changed)
                self._instrument_checks[key] = checkbox
                self.instrument_choices_layout.addWidget(checkbox, row, 0)
            self.instrument_choices_layout.addWidget(
                self.scan_button, len(self._instruments) - 1, 1
            )
            self.measure_button.setEnabled(True)
            self.measure_status.setText(f"Ready — {len(self._instruments)} instrument(s) connected")
        else:
            self.instrument_placeholder = QLabel("No compatible instruments found")
            self.instrument_placeholder.setStyleSheet("color: #888;")
            self.instrument_choices_layout.addWidget(self.instrument_placeholder, 0, 0)
            self.instrument_choices_layout.addWidget(self.scan_button, 0, 1)
            self.measure_button.setEnabled(False)
            self.measure_status.setText("No compatible instruments found")
        self._render_records()

    def _instrument_selection_changed(self):
        self.measure_button.setEnabled(
            any(checkbox.isChecked() for checkbox in self._instrument_checks.values())
        )

    def _delete_selected(self):
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            if 0 <= row < len(self._records):
                self._records.pop(row)
        self._render_records()
        self._update_record_actions()

    def _clear_readings(self):
        self._records.clear()
        self._render_records()
        self._update_record_actions()
        self.measure_status.setStyleSheet("color: #888;")
        self.measure_status.setText("Ready")

    def _update_record_actions(self):
        has_records = bool(self._records)
        self.clear_button.setEnabled(has_records)
        self.delete_button.setEnabled(has_records)
        self.save_button.setEnabled(has_records and self.csv_check.isChecked())

    def _render_records(self):
        columns = []
        if self._column_checks.get("index") and self._column_checks["index"].isChecked():
            columns.append(("index", "#", lambda row, _record, _reading: row + 1))
        show_target_rgb = bool(
            self._column_checks.get("target_rgb")
            and self._column_checks["target_rgb"].isChecked()
        )
        show_target_color = bool(
            self._column_checks.get("target_color")
            and self._column_checks["target_color"].isChecked()
        )
        if show_target_rgb or show_target_color:
            columns.append((
                "target",
                "Target",
                lambda _row, record, _reading: self._target_rgb_text(record),
            ))
        if self._column_checks.get("time") and self._column_checks["time"].isChecked():
            columns.append(("time", "Time", lambda _row, record, _reading: record["time"]))
        if self._column_checks.get("yxy") and self._column_checks["yxy"].isChecked():
            columns.extend((
                ("yxy_y", "Y", lambda _row, _record, reading: f"{reading.Y:.6f}"),
                ("yxy_x", "x", lambda _row, _record, reading: f"{reading.x:.6f}"),
                ("yxy_small_y", "y", lambda _row, _record, reading: f"{reading.y:.6f}"),
            ))
        if self._column_checks.get("yuv") and self._column_checks["yuv"].isChecked():
            def yuv(reading):
                denominator = reading.X + 15 * reading.Y + 3 * reading.Z
                if not denominator:
                    return reading.Y, 0.0, 0.0
                return (
                    reading.Y,
                    4 * reading.X / denominator,
                    9 * reading.Y / denominator,
                )
            columns.extend((
                ("yuv_y", "Y (Yuv)", lambda _row, _record, reading: f"{yuv(reading)[0]:.6f}"),
                ("yuv_u", "u′", lambda _row, _record, reading: f"{yuv(reading)[1]:.6f}"),
                ("yuv_v", "v′", lambda _row, _record, reading: f"{yuv(reading)[2]:.6f}"),
            ))
        if self._column_checks.get("xyz") and self._column_checks["xyz"].isChecked():
            columns.extend((
                ("xyz_x", "X", lambda _row, _record, reading: f"{reading.X:.6f}"),
                ("xyz_y", "Y", lambda _row, _record, reading: f"{reading.Y:.6f}"),
                ("xyz_z", "Z", lambda _row, _record, reading: f"{reading.Z:.6f}"),
            ))
        if (
            self._column_checks.get("instrument_name")
            and self._column_checks["instrument_name"].isChecked()
        ):
            columns.append((
                "instrument_name",
                "Instrument Name",
                lambda _row, record, _reading: record["instrument"].name,
            ))
        if (
            self._column_checks.get("instrument_id")
            and self._column_checks["instrument_id"].isChecked()
        ):
            columns.append((
                "instrument_id",
                "Instrument ID",
                lambda _row, record, _reading: record["instrument"].display_identifier,
            ))
        if self._column_checks.get("ccmx") and self._column_checks["ccmx"].isChecked():
            columns.append((
                "ccmx",
                "CCMX",
                lambda _row, record, _reading: self._correction_text(record),
            ))

        headers = [header for _key, header, _getter in columns]
        # Custom Target cell widgets otherwise remain attached to their old
        # column when display fields are toggled, and can overlap the new data.
        self.table.setRowCount(0)
        self.table.clearContents()
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(self._records))
        for row, record in enumerate(self._records):
            reading = record["reading"]
            for column, (key, _header, getter) in enumerate(columns):
                value = getter(row, record, reading)
                self.table.setItem(
                    row,
                    column,
                    QTableWidgetItem("" if key == "target" else str(value)),
                )
                if key == "target":
                    holder = QWidget()
                    holder_layout = QHBoxLayout(holder)
                    holder_layout.setContentsMargins(8, 0, 8, 0)
                    holder_layout.setSpacing(8)
                    rgb = record.get("target_rgb")
                    if rgb is None:
                        missing = QLabel("-")
                        missing.setAlignment(Qt.AlignmentFlag.AlignCenter)
                        holder_layout.addWidget(missing, 1)
                    else:
                        if show_target_color:
                            if not show_target_rgb:
                                holder_layout.addStretch()
                            swatch = QFrame()
                            swatch.setFixedSize(18, 18)
                            r, g, b = rgb
                            swatch.setStyleSheet(
                                f"background-color: rgb({r}, {g}, {b});"
                                "border: 1px solid palette(mid); border-radius: 2px;"
                            )
                            holder_layout.addWidget(swatch)
                        if show_target_rgb:
                            holder_layout.addWidget(QLabel(self._target_rgb_text(record)))
                    holder_layout.addStretch()
                    self.table.setCellWidget(row, column, holder)
        for column in range(len(headers)):
            if headers[column] == "Instrument Name":
                self.table.horizontalHeader().setSectionResizeMode(
                    column, QHeaderView.ResizeMode.Interactive
                )
                self.table.setColumnWidth(column, 240)
            elif headers[column] == "Target":
                self.table.horizontalHeader().setSectionResizeMode(
                    column, QHeaderView.ResizeMode.ResizeToContents
                )
                self.table.setColumnWidth(column, 150 if show_target_rgb else 82)
            else:
                self.table.horizontalHeader().setSectionResizeMode(
                    column, QHeaderView.ResizeMode.ResizeToContents
                )

    @staticmethod
    def _target_rgb_text(record):
        rgb = record.get("target_rgb")
        return ", ".join(str(value) for value in rgb) if rgb is not None else "-"

    @staticmethod
    def _target_color_text(record):
        rgb = record.get("target_rgb")
        return "#{:02X}{:02X}{:02X}".format(*rgb) if rgb is not None else "-"

    @staticmethod
    def _correction_text(record):
        path = record.get("correction_path", "")
        return Path(path).name if path else "Raw"

    def _csv_columns(self):
        def yuv(reading):
            denominator = reading.X + 15 * reading.Y + 3 * reading.Z
            if not denominator:
                return reading.Y, 0.0, 0.0
            return reading.Y, 4 * reading.X / denominator, 9 * reading.Y / denominator

        return (
            ("#", lambda row, _record, _reading: row + 1),
            ("Target Color", lambda _row, record, _reading: self._target_color_text(record)),
            ("Target RGB", lambda _row, record, _reading: self._target_rgb_text(record)),
            ("Time", lambda _row, record, _reading: record["time"]),
            ("Y", lambda _row, _record, reading: f"{reading.Y:.6f}"),
            ("x", lambda _row, _record, reading: f"{reading.x:.6f}"),
            ("y", lambda _row, _record, reading: f"{reading.y:.6f}"),
            ("Y (Yuv)", lambda _row, _record, reading: f"{yuv(reading)[0]:.6f}"),
            ("u′", lambda _row, _record, reading: f"{yuv(reading)[1]:.6f}"),
            ("v′", lambda _row, _record, reading: f"{yuv(reading)[2]:.6f}"),
            ("X", lambda _row, _record, reading: f"{reading.X:.6f}"),
            ("Y", lambda _row, _record, reading: f"{reading.Y:.6f}"),
            ("Z", lambda _row, _record, reading: f"{reading.Z:.6f}"),
            ("Instrument Name", lambda _row, record, _reading: record["instrument"].name),
            ("Instrument ID", lambda _row, record, _reading: record["instrument"].display_identifier),
            ("CCMX", lambda _row, record, _reading: self._correction_text(record)),
            ("CCMX Path", lambda _row, record, _reading: record.get("correction_path", "")),
        )

    def _save_csv(self):
        if self.table.rowCount() == 0:
            QMessageBox.information(self, "No Measurements", "There are no readings to save.")
            return
        if not self.csv_check.isChecked():
            self.measure_status.setStyleSheet("color: #d99a3e;")
            self.measure_status.setText("Select CSV before saving.")
            return
        if not self.output_path.text().strip():
            self._save_after_choose = True
            self._choose_output()
            return
        root = Path(self.output_path.text()).expanduser()
        try:
            root.mkdir(parents=True, exist_ok=True)
            display = re.sub(
                r'[\\/:*?"<>|]+', "-", self.display_name.text().strip()
            ).strip(" .") or "Display"
            path = root / f"{datetime.now():%Y%m%d_%H%M%S}_{display}_Manual.csv"
            self._write_csv(path)
        except OSError as error:
            self.measure_status.setStyleSheet("color: #d99a3e;")
            self.measure_status.setText(f"Save failed: {error}")

    def _write_csv(self, path):
        path = Path(path)
        columns = self._csv_columns()
        with open(path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(header for header, _getter in columns)
            for row, record in enumerate(self._records):
                reading = record["reading"]
                writer.writerow(getter(row, record, reading) for _header, getter in columns)
        self.measure_status.setStyleSheet("color: #888;")
        self.measure_status.setText(f"Saved {self.table.rowCount()} reading(s) to {path}")

    def _choose_output(self):
        if self._output_folder_dialog is not None:
            self._output_folder_dialog.raise_()
            self._output_folder_dialog.activateWindow()
            return
        dialog = QFileDialog(self, "Choose Output Folder", self.output_path.text())
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.fileSelected.connect(self._output_folder_selected)
        dialog.finished.connect(self._output_folder_finished)
        self._output_folder_dialog = dialog
        dialog.open()

    def _output_folder_selected(self, path):
        self.output_path.setText(path)
        if self._save_after_choose:
            self._save_after_choose = False
            QTimer.singleShot(0, self._save_csv)

    def _output_folder_finished(self, result):
        if not result:
            self._save_after_choose = False
        dialog = self._output_folder_dialog
        self._output_folder_dialog = None
        if dialog is not None:
            dialog.deleteLater()
        QTimer.singleShot(0, self._restore_after_output_folder)

    def _restore_after_output_folder(self):
        if self.isVisible():
            self.raise_()
            self.activateWindow()
