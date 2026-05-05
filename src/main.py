# ===== Standard Library =====
import sys
import socket
import struct
import threading
import traceback
import time
import json
import secrets
import html

import xml.etree.ElementTree as ET
from datetime import datetime
from functools import partial
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ===== Third Party =====
from PySide6.QtCore import (
    Qt,
    Signal,
    QTimer,
    QSettings,
    QByteArray,
    QBuffer,
    QIODevice,
    QObject,
    QEvent,
)

from PySide6.QtGui import (
    QIcon,
    QAction,
    QPainter,
    QColor,
    QImage,
    QGuiApplication,
    QImageReader,
    QShortcut,
    QKeySequence,
    QPixmap,
)

from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QMainWindow,
    QDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QMessageBox,
    QTextEdit,
    QSlider,
    QSpinBox,
    QFrame,
    QGridLayout,
    QToolButton,
    QFileDialog,
    QSizePolicy,
)

APP_ORG = "WhARTS"
APP_NAME = "ColourSpacePatchClient"
APP_DISPLAY_NAME = "ColourSpace Patch Rx"
APP_VERSION = "2.0.0"
APP_YEAR = "2026"
APP_COMPANY = "WhARTS Ltd."

DEFAULT_HOST = "192.168.1.100"
DEFAULT_PORT = 20002
DEFAULT_DISPLAYCAL_HOST = "192.168.1.100"
DEFAULT_DISPLAYCAL_PORT = 20002
DEFAULT_BRIDGE_PORT = 8765
DEFAULT_SYNC_PORT = 8766
DEFAULT_ACCESS_TOKEN = "wharts"

INITIAL_CONNECT_TIMEOUT_SEC = 1
MAX_RECONNECT_DELAY_SEC = 5
MAX_LOG_LINES = 1000

# ===== Viewer Status Labels =====
VIEWER_SOURCE_MANUAL = "From this app"
VIEWER_SOURCE_REMOTE = "From another app"

VIEWER_TYPE_COLOURSPACE_RX = "Rx from ColourSpace"
VIEWER_TYPE_DISPLAYCAL_RX = "Rx from DisplayCAL"
VIEWER_TYPE_PATTERN = "Pattern"
VIEWER_TYPE_COLOR = "Custom Color"

VIEWER_CONTENT_WAITING = "Waiting..."


def format_rgb(r: int, g: int, b: int) -> str:
    return f"RGB = {r},{g},{b}"


# ===== Web Viewer Labels =====
WEB_VIEWER_SOURCE_MAP = {
    VIEWER_SOURCE_MANUAL: "Direct",
    VIEWER_SOURCE_REMOTE: "Synced",
}

# =====
LOG_FILE = Path.home() / "Library" / "Logs" / "ColourSpacePatchClient.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

SETTINGS_KEY_HOST = "connection/host"
SETTINGS_KEY_PORT = "connection/port"
SETTINGS_KEY_DISPLAYCAL_HOST = "displaycal/host"
SETTINGS_KEY_DISPLAYCAL_PORT = "displaycal/port"
SETTINGS_KEY_ENABLE_FILE_LOG = "logging/enable_file_log"
SETTINGS_KEY_SHOW_STATUS_BAR = "ui/show_status_bar"

SETTINGS_KEY_CUSTOM_R = "patterns/custom_r"
SETTINGS_KEY_CUSTOM_G = "patterns/custom_g"
SETTINGS_KEY_CUSTOM_B = "patterns/custom_b"
SETTINGS_KEY_SWATCHES = "patterns/swatches"
SETTINGS_KEY_USER_PATTERN_DIR = "patterns/user_pattern_dir"

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys._MEIPASS)
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

TEST_PATTERN_DIR = BASE_DIR / "assets" / "test_patterns"
TEST_PATTERN_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}

SHORTCUT_HELP = [
    ("Display", ""),
    ("Show patch from ColourSpace", "⌘/Ctrl + R"),
    ("Show patch from DisplayCAL", "⌘/Ctrl + E"),
    ("Show local custom color", "⌘/Ctrl + L"),

    ("Patterns", ""),
    ("Show test pattern (01–09)", "⌘/Ctrl + 1~9"),
    ("Show user pattern (01–09)", "⌘/Ctrl + Shift + 1~9"),
    ("Next/previous pattern", "⌘/Ctrl + ←→"),

    ("Windows", ""),
    ("Add additional viewer", "⌘/Ctrl + N"),
    ("Close current viewer", "⌘/Ctrl + W"),
    ("Close all additional viewers", "⌘/Ctrl + Shift + W"),

    ("Interface", ""),
    ("Show/Hide connected devices", "⌘/Ctrl + D"),
    ("Show/Hide status bar", "⌘/Ctrl + B"),
    ("Show shortcut page", "⌘/Ctrl + /"),
]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Logger:
    def __init__(self, settings: QSettings):
        self.settings = settings
        self.buffer = []

    @property
    def enable_file_log(self) -> bool:
        value = self.settings.value(SETTINGS_KEY_ENABLE_FILE_LOG, False)
        if isinstance(value, bool):
            return value
        return str(value).lower() == "true"

    @enable_file_log.setter
    def enable_file_log(self, value: bool) -> None:
        self.settings.setValue(SETTINGS_KEY_ENABLE_FILE_LOG, value)

    def log(self, message: str) -> None:
        text = f"[{now_text()}] {message}"
        print(text)

        self.buffer.append(text)

        if len(self.buffer) > MAX_LOG_LINES:
            self.buffer.pop(0)

        if self.enable_file_log:
            try:
                with LOG_FILE.open("a", encoding="utf-8") as f:
                    f.write(text + "\n")
            except Exception:
                pass


class PatchWidget(QWidget):
    def __init__(self, logger: Logger):
        super().__init__()
        self.logger = logger
        self.rectangles = []
        self._pending_rectangles = None

        self.display_mode = "external"  # "external" or "internal"
        self.internal_pattern = None  # e.g. ("solid", (255,255,255))
        self.internal_image = None

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.apply_pending_update)
        self._timer.start(16)

        self.logger.log("[SYSTEM] PatchWidget initialized")

    def set_rectangles_threadsafe(self, rects):
        self._pending_rectangles = rects

    def clear_rectangles_threadsafe(self):
        self._pending_rectangles = []

    def set_internal_pattern(self, pattern):
        self.display_mode = "internal"
        self.internal_pattern = pattern
        self.internal_image = None
        self.update()

    def set_internal_image(self, image: QImage, label: str):
        self.display_mode = "internal"
        self.internal_pattern = ("image", label)
        self.internal_image = image
        self.update()

    def set_external_mode(self):
        self.display_mode = "external"
        self.internal_pattern = None
        self.internal_image = None
        self.update()

    def apply_pending_update(self):
        if self._pending_rectangles is not None:
            self.rectangles = self._pending_rectangles
            self._pending_rectangles = None
            if self.display_mode == "external":
                self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        width = self.width()
        height = self.height()

        # 先畫黑底
        painter.fillRect(0, 0, width, height, QColor(0, 0, 0))

        if self.display_mode == "internal":
            self.paint_internal_pattern(painter, width, height)
        else:
            self.paint_external_rectangles(painter, width, height)

        painter.end()

    def paint_external_rectangles(self, painter: QPainter, width: int, height: int):
        for rect in self.rectangles:
            r, g, b, x, y, cx, cy = rect

            color = QColor(r, g, b)

            x1 = int(x * width)
            y1 = int(y * height)
            w = int(cx * width)
            h = int(cy * height)

            painter.fillRect(x1, y1, w, h, color)

    def paint_internal_pattern(self, painter: QPainter, width: int, height: int):
        if not self.internal_pattern:
            return

        pattern_type, value = self.internal_pattern

        if pattern_type == "solid":
            r, g, b = value
            painter.fillRect(0, 0, width, height, QColor(r, g, b))

        elif pattern_type == "image" and self.internal_image is not None:
            image = self.internal_image

            img_w = image.width()
            img_h = image.height()

            if img_w <= 0 or img_h <= 0:
                return

            scale = min(width / img_w, height / img_h)

            draw_w = int(img_w * scale)
            draw_h = int(img_h * scale)

            x = (width - draw_w) // 2
            y = (height - draw_h) // 2

            painter.drawImage(
                x, y, image.scaled(
                    draw_w,
                    draw_h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
            )


class SettingsDialog(QDialog):
    def __init__(self, host: str, port: int, dc_host: str, dc_port: int, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Connection Settings")
        self.setMinimumWidth(380)

        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("e.g. 192.168.1.100")

        self.port_edit = QLineEdit()
        self.port_edit.setPlaceholderText("e.g. 20002")

        self.dc_host_edit = QLineEdit()
        self.dc_host_edit.setPlaceholderText("e.g. 192.168.1.100")

        self.dc_port_edit = QLineEdit()
        self.dc_port_edit.setPlaceholderText("e.g. 20002")

        if host:
            self.host_edit.setText(host)

        self.port_edit.setText(str(port))

        if dc_host:
            self.dc_host_edit.setText(dc_host)

        self.dc_port_edit.setText(str(dc_port))

        host_row = QHBoxLayout()
        host_row.addWidget(QLabel("IP"))
        host_row.addWidget(self.host_edit)

        port_row = QHBoxLayout()
        port_row.addWidget(QLabel("Port"))
        port_row.addWidget(self.port_edit)

        dc_host_row = QHBoxLayout()
        dc_host_row.addWidget(QLabel("IP"))
        dc_host_row.addWidget(self.dc_host_edit)

        dc_port_row = QHBoxLayout()
        dc_port_row.addWidget(QLabel("Port"))
        dc_port_row.addWidget(self.dc_port_edit)

        self.save_button = QPushButton("Save")
        self.cancel_button = QPushButton("Cancel")

        button_row = QHBoxLayout()
        button_row.addStretch()
        button_row.addWidget(self.save_button)
        button_row.addWidget(self.cancel_button)

        layout = QVBoxLayout()

        # ---- ColourSpace ----
        cs_label = QLabel("ColourSpace")
        cs_label.setStyleSheet("color: #ddd; font-size: 13px; font-weight: bold;")

        cs_hint = QLabel("Find and set in Hardware Options")
        cs_hint.setStyleSheet("color: #888; font-size: 11px;")

        # ---- DisplayCAL ----
        dc_label = QLabel("DisplayCAL")
        dc_label.setStyleSheet("color: #ddd; font-size: 13px; font-weight: bold;")

        dc_hint = QLabel("Choose “Resolve” as Display, then find the IP and port")
        dc_hint.setStyleSheet("color: #888; font-size: 11px;")

        # ---- warning ----
        warning_label = QLabel(
            "If using both at the same time, avoid port 20002 for ColourSpace, "
            "as it is used by DisplayCAL by default."
        )
        warning_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        warning_label.setStyleSheet("color: #888; font-size: 11px;")
        warning_label.setWordWrap(True)

        # ---- layout ----

        layout.addWidget(cs_label)
        layout.addWidget(cs_hint)
        layout.addLayout(host_row)
        layout.addLayout(port_row)

        layout.addSpacing(12)

        layout.addWidget(dc_label)
        layout.addWidget(dc_hint)
        layout.addLayout(dc_host_row)
        layout.addLayout(dc_port_row)

        layout.addSpacing(10)
        layout.addWidget(warning_label)

        layout.addSpacing(6)
        layout.addLayout(button_row)

        self.setLayout(layout)

        self.setLayout(layout)

        self.save_button.clicked.connect(self.validate_and_accept)
        self.cancel_button.clicked.connect(self.reject)

        self.host = host
        self.port = port
        self.dc_host = dc_host
        self.dc_port = dc_port

    def validate_and_accept(self):
        host = self.host_edit.text().strip()
        port_text = self.port_edit.text().strip()
        dc_host = self.dc_host_edit.text().strip()
        dc_port_text = self.dc_port_edit.text().strip()

        if not host:
            QMessageBox.warning(self, "Invalid Input", "ColourSpace IP 不能空白")
            return

        if not dc_host:
            QMessageBox.warning(self, "Invalid Input", "DisplayCAL IP 不能空白")
            return

        try:
            port = int(port_text)
        except ValueError:
            QMessageBox.warning(self, "Invalid Input", "ColourSpace Port 必須是整數")
            return

        try:
            dc_port = int(dc_port_text)
        except ValueError:
            QMessageBox.warning(self, "Invalid Input", "DisplayCAL Port 必須是整數")
            return

        if not (1 <= port <= 65535):
            QMessageBox.warning(self, "Invalid Input", "ColourSpace Port 必須在 1 到 65535 之間")
            return

        if not (1 <= dc_port <= 65535):
            QMessageBox.warning(self, "Invalid Input", "DisplayCAL Port 必須在 1 到 65535 之間")
            return

        self.host = host
        self.port = port
        self.dc_host = dc_host
        self.dc_port = dc_port
        self.accept()


class LogDialog(QDialog):
    def __init__(self, logger: Logger, parent=None):
        super().__init__(parent)
        self.logger = logger

        self.setWindowTitle("Log")
        self.resize(800, 500)

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)

        self.refresh_button = QPushButton("Refresh")
        self.close_button = QPushButton("Close")

        button_row = QHBoxLayout()
        button_row.addStretch()
        button_row.addWidget(self.refresh_button)
        button_row.addWidget(self.close_button)

        layout = QVBoxLayout()
        layout.addWidget(self.text_edit)
        layout.addLayout(button_row)
        self.setLayout(layout)

        self.refresh_button.clicked.connect(self.refresh_log)
        self.close_button.clicked.connect(self.close)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh_log)
        self._timer.start(1000)

        self.refresh_log()

    def refresh_log(self):
        lines = []

        try:
            lines.extend(self.logger.buffer)
        except Exception:
            pass

        if self.logger.enable_file_log and LOG_FILE.exists():
            try:
                content = LOG_FILE.read_text(encoding="utf-8")
                if content.strip():
                    lines.append("")
                    lines.append("--- File Log ---")
                    lines.append("")
                    lines.append(content)
            except Exception as e:
                lines.append(f"[Error reading file log] {e}")

        if not lines:
            text = "(no log yet)"
        else:
            text = "\n".join(lines)

        if self.text_edit.toPlainText() != text:
            html_text = self._colorize_log_text(text)
            self.text_edit.setHtml(html_text)

            cursor = self.text_edit.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self.text_edit.setTextCursor(cursor)

    def _colorize_log_text(self, text: str) -> str:
        lines = text.splitlines()

        tag_colors = {
            "[SYSTEM]": "#ffffff",  # 白（系統）
            "[STATUS]": "#9E9E9E",  # 灰（狀態）

            "[CONNECTION]": "#81C784",  # 綠（連線）

            "[SYNC]": "#64B5F6",  # 藍（同步）
            "[SYNC SEND]": "#3b6e98",
            "[SYNC RECV]": "#3b6e98",
            "[SYNC APPLY]": "#3b6e98",

            "[BRIDGE]": "#BA68C8",  # 紫（Bridge）

            "[PATCH]": "#9E9E9E",  # 灰（色塊）

            "[PATCH RECV]": "#4a744c",  # 綠（接收）
            "[PATCH DATA]": "#4a744c",  # 綠（數據）

            "[PATTERN]": "#FFB74D",  # 橘（圖卡）
            "[LOG]": "#A1887F",  # 棕（一般 log）
            "[RESET]": "#f062ee",  # 粉（重置）

            "[ERROR]": "#EF5350",  # 紅（錯誤）
            "[SYSTEM ERROR]": "#EF5350",
            "[BRIDGE ERROR]": "#EF5350",
            "[PATCH ERROR]": "#EF5350",
            "[SYNC ERROR]": "#EF5350",
        }

        html_lines = []

        for line in lines:
            escaped = html.escape(line)
            color = None

            for tag, tag_color in tag_colors.items():
                if tag in line:
                    color = tag_color
                    break

            if color:
                html_lines.append(f'<span style="color: {color};">{escaped}</span>')
            else:
                html_lines.append(escaped)

        return (
                '<div style="font-family: Menlo, Consolas, monospace; '
                'font-size: 12px; white-space: pre-wrap;">'
                + "<br>".join(html_lines)
                + "</div>"
        )


class ConnectedDevicesDialog(QDialog):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window

        self.setWindowTitle("Connected Devices")
        self.resize(520, 320)

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setPlainText("Loading device information...")

        self.refresh_button = QPushButton("Refresh")
        self.close_button = QPushButton("Close")

        button_row = QHBoxLayout()
        button_row.addStretch()
        button_row.addWidget(self.refresh_button)
        button_row.addWidget(self.close_button)

        layout = QVBoxLayout()
        layout.addWidget(self.text_edit)
        layout.addLayout(button_row)
        self.setLayout(layout)

        self.refresh_button.clicked.connect(self.refresh_content)
        self.close_button.clicked.connect(self.close)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(3000)
        self.refresh_timer.timeout.connect(self.refresh_content)

    def refresh_content(self):
        mw = self.main_window

        try:
            local_ip = mw._detect_lan_host()
        except Exception:
            local_ip = "Unknown"

        local_mode = getattr(mw.sync_manager, "mode", "off")

        try:
            device_name = mw.get_sync_device_name()
        except Exception:
            device_name = "Patch Client"

        port = getattr(mw.sync_manager, "port", 8766)

        if local_mode == "off":
            listening_text = "No"
        else:
            listening_text = "Yes"

        now = time.time()
        stale_ids = []
        device_lines = []

        for sender, info in mw.sync_seen_devices.items():
            last_seen_ts = float(info.get("last_seen_ts", 0))
            online_age = now - last_seen_ts

            if online_age > 60:
                stale_ids.append(sender)
                continue

            name = str(info.get("name", "")).strip()
            ip = str(info.get("ip", "Unknown"))
            source_port = info.get("port", "")
            remote_mode = str(info.get("mode", "unknown"))
            last_action = str(info.get("last_action", "")).strip()
            action_label_map = {
                "test_pattern": "test pattern",
                "solid": "custom color",
                "return": "rx color",
                "take_control": "take control",
            }

            last_action_display = action_label_map.get(last_action, last_action)
            last_action_ts = float(info.get("last_action_ts", 0))

            if not name:
                name = ip

            if last_action and last_action_ts > 0:
                action_age = now - last_action_ts
                action_text = f"{last_action_display} | {action_age:.1f}s ago"
            else:
                action_text = "no recent action"

            device_lines.append(
                f"- {name} | {ip} | {remote_mode} | {action_text}"
            )

        for sender in stale_ids:
            mw.sync_seen_devices.pop(sender, None)

        if device_lines:
            known_devices_text = "\n".join(device_lines)
        else:
            known_devices_text = "(no recent sync sources)"

        text = (
            "Local Device\n"
            "------------\n"
            f"Name: {device_name}\n"
            f"IP: {local_ip}\n"
            f"Mode: {local_mode}\n"
            f"Sync Port: {port}\n"
            f"Listening: {listening_text}\n"
            "\n"
            "Known Devices\n"
            "-------------\n"
            f"{known_devices_text}"
        )

        if self.text_edit.toPlainText() != text:
            self.text_edit.setPlainText(text)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_timer.start()
        self.refresh_content()

    def hideEvent(self, event):
        self.refresh_timer.stop()
        super().hideEvent(event)


class SwatchButton(QToolButton):
    def __init__(self, index: int, parent_panel, fixed_slot=False):
        super().__init__(parent_panel)
        self.index = index
        self.parent_panel = parent_panel
        self.fixed_slot = fixed_slot
        self.rgb_value = None

        self.setFixedSize(28, 28)
        self.setToolTip("")
        self.refresh_style()

    def set_rgb_value(self, rgb):
        self.rgb_value = rgb
        self.refresh_style()

    def refresh_style(self):
        if self.rgb_value is None:
            self.setStyleSheet("""
                QToolButton {
                    background-color: rgba(255,255,255,0.06);
                    border: 1px solid rgba(255,255,255,0.18);
                    border-radius: 4px;
                }
            """)
            self.setText("")

            if self.fixed_slot:
                self.setToolTip("Fixed swatch")
            else:
                self.setToolTip("Empty\nRight: Store current color")

        else:
            r, g, b = self.rgb_value
            self.setStyleSheet(f"""
                QToolButton {{
                    background-color: rgb({r}, {g}, {b});
                    border: 1px solid rgba(255,255,255,0.35);
                    border-radius: 4px;
                }}
            """)
            self.setText("")

            if self.fixed_slot:
                self.setToolTip(f"RGB=({r},{g},{b}) | Left: Apply")
            else:
                self.setToolTip(f"RGB=({r},{g},{b})\nLeft: Apply | Right: Overwrite")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.rgb_value is not None:
                self.parent_panel.apply_swatch(self.index)
            return

        if event.button() == Qt.MouseButton.RightButton:
            self.parent_panel.store_current_to_swatch(self.index)
            return

        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.parent_panel.clear_swatch(self.index)
            return

        super().mouseDoubleClickEvent(event)


class CustomColorPanel(QFrame):
    def __init__(self, parent=None, initial_rgb=(128, 128, 128)):
        super().__init__(parent)

        self.setFrameShape(QFrame.Shape.Box)
        self.setObjectName("customColorPanel")
        self.setStyleSheet("""
            QFrame#customColorPanel {
                background-color: rgba(30, 30, 30, 210);
                border: 1px solid rgba(255, 255, 255, 40);
                border-radius: 8px;
            }
            QLabel {
                color: white;
            }
        """)

        r0, g0, b0 = initial_rgb

        self.title_label = QLabel("Custom Color")
        self.toggle_btn = QPushButton("–")
        self.toggle_btn.setFixedSize(20, 20)
        self.toggle_btn.setStyleSheet("""
            QPushButton {
                padding: 0px;
                margin: 0px;
                border-radius: 10px;
                background-color: rgba(255,255,255,0.1);
                font-size: 12px;
            }
        """)
        self.toggle_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.r_slider = self._create_slider(r0)
        self.g_slider = self._create_slider(g0)
        self.b_slider = self._create_slider(b0)

        self.r_spin = self._create_spinbox(r0)
        self.g_spin = self._create_spinbox(g0)
        self.b_spin = self._create_spinbox(b0)

        self._bind_slider_spin(self.r_slider, self.r_spin)
        self._bind_slider_spin(self.g_slider, self.g_spin)
        self._bind_slider_spin(self.b_slider, self.b_spin)

        self.fixed_swatches = [
            (255, 0, 0),  # Red
            (0, 255, 0),  # Green
            (0, 0, 255),  # Blue
            (255, 255, 255),  # White
            (192, 192, 192),  # 75% Gray
            (128, 128, 128),  # 50% Gray
            (64, 64, 64),  # 25% Gray
            (0, 0, 0),  # Black
        ]

        self.user_swatches: list[tuple[int, int, int] | None] = [None] * (24 - len(self.fixed_swatches))
        self.swatch_buttons = []

        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.addWidget(self.title_label)
        title_row.addStretch()
        title_row.addWidget(self.toggle_btn)
        layout.addLayout(title_row)
        self.r_row_widget = QWidget()
        self.r_row_widget.setLayout(self._create_row("R", self.r_slider, self.r_spin))

        self.g_row_widget = QWidget()
        self.g_row_widget.setLayout(self._create_row("G", self.g_slider, self.g_spin))

        self.b_row_widget = QWidget()
        self.b_row_widget.setLayout(self._create_row("B", self.b_slider, self.b_spin))

        layout.addWidget(self.r_row_widget)
        layout.addWidget(self.g_row_widget)
        layout.addWidget(self.b_row_widget)

        swatch_title = QLabel("Swatches")

        swatch_desc = QLabel("Click to apply  •  Right-click to save  •  Double right-click to clear")
        swatch_desc.setStyleSheet("color: rgba(255,255,255,0.3); font-size: 8px;")

        title_layout = QVBoxLayout()
        title_layout.setSpacing(4)
        title_layout.addWidget(swatch_title)
        title_layout.addWidget(swatch_desc)

        layout.addLayout(title_layout)

        swatch_grid = QGridLayout()
        swatch_grid.setHorizontalSpacing(8)
        swatch_grid.setVerticalSpacing(8)

        columns = 8  # 每排幾個

        for i in range(24):
            fixed_slot = i < columns
            btn = SwatchButton(i, self, fixed_slot=fixed_slot)
            self.swatch_buttons.append(btn)

            row = i // columns
            col = i % columns
            swatch_grid.addWidget(btn, row, col)

        layout.addLayout(swatch_grid)

        self.setLayout(layout)
        self.setFixedWidth(300)

        self._collapsible_widgets = []

        self._collapsible_widgets.extend([
            self.r_row_widget,
            self.g_row_widget,
            self.b_row_widget,
        ])

        self._collapsible_widgets.extend(self.swatch_buttons)
        self._collapsible_widgets.extend([
            swatch_title,
            swatch_desc,
        ])

        self.toggle_btn.clicked.connect(self.toggle_collapsed)

    def _create_slider(self, value: int) -> QSlider:
        slider = QSlider()
        slider.setOrientation(Qt.Orientation.Horizontal)
        slider.setRange(0, 255)
        slider.setValue(value)
        return slider

    def _create_spinbox(self, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(0, 255)
        spin.setValue(value)
        if sys.platform == "win32":
            spin.setFixedWidth(80)
        else:
            spin.setFixedWidth(48)

        spin.setKeyboardTracking(False)

        return spin

    def _bind_slider_spin(self, slider: QSlider, spin: QSpinBox):
        slider.valueChanged.connect(spin.setValue)
        spin.valueChanged.connect(slider.setValue)

    def _create_row(self, label_text: str, slider: QSlider, spin: QSpinBox):
        row = QHBoxLayout()
        row.setSpacing(8)

        label = QLabel(label_text)
        label.setFixedWidth(16)

        row.addWidget(label)
        row.addWidget(slider)
        row.addWidget(spin)

        return row

    def rgb(self):
        return (
            self.r_slider.value(),
            self.g_slider.value(),
            self.b_slider.value(),
        )

    def set_rgb(self, r: int, g: int, b: int):
        self.r_slider.setValue(r)
        self.g_slider.setValue(g)
        self.b_slider.setValue(b)

    def load_swatches(self, user_swatches):
        self.user_swatches = list(user_swatches)

        all_swatches = self.fixed_swatches + self.user_swatches
        for i, rgb in enumerate(all_swatches):
            self.swatch_buttons[i].set_rgb_value(rgb)

    def export_user_swatches(self):
        return self.user_swatches

    def apply_swatch(self, index: int):
        fixed_count = len(self.fixed_swatches)

        if index < len(self.fixed_swatches):
            rgb = self.fixed_swatches[index]
        else:
            rgb = self.user_swatches[index - fixed_count]

        if rgb is None:
            return

        r, g, b = rgb
        self.set_rgb(r, g, b)

    def store_current_to_swatch(self, index: int):
        fixed_count = len(self.fixed_swatches)

        if index < len(self.fixed_swatches):
            return

        rgb = self.rgb()
        self.user_swatches[index - fixed_count] = rgb
        self.swatch_buttons[index].set_rgb_value(rgb)

        parent = self.parent()
        if parent and hasattr(parent, "save_custom_swatches"):
            parent.save_custom_swatches()

    def clear_swatch(self, index: int):
        fixed_count = len(self.fixed_swatches)
        if index < fixed_count:
            return

        self.user_swatches[index - fixed_count] = None
        self.swatch_buttons[index].set_rgb_value(None)

        parent = self.parent()
        if parent and hasattr(parent, "save_custom_swatches"):
            parent.save_custom_swatches()

    def toggle_collapsed(self):
        self.is_collapsed = not getattr(self, "is_collapsed", False)

        for w in self._collapsible_widgets:
            w.setVisible(not self.is_collapsed)

        if self.is_collapsed:
            self.toggle_btn.setText("+")
            self.setFixedWidth(150)
            self.setFixedHeight(45)
        else:
            self.toggle_btn.setText("–")
            self.setFixedWidth(300)
            self.setFixedHeight(self.sizeHint().height())

        parent = self.parent()
        if parent and hasattr(parent, "position_custom_color_panel"):
            parent.position_custom_color_panel()


def recv_exact(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("連線中斷，未收到完整資料")
        data += chunk
    return data


def parse_rectangles(xml_text: str):
    root = ET.fromstring(xml_text)
    rects = []

    for rect in root.findall(".//rectangle"):
        color = rect.find("color")
        colex = rect.find("colex")
        geometry = rect.find("geometry")

        if geometry is None:
            continue

        x = float(geometry.get("x", "0"))
        y = float(geometry.get("y", "0"))
        cx = float(geometry.get("cx", "0"))
        cy = float(geometry.get("cy", "0"))

        if colex is not None:
            bits = int(colex.get("bits", "8"))
            r = int(colex.get("red", "0"))
            g = int(colex.get("green", "0"))
            b = int(colex.get("blue", "0"))

            if bits != 8:
                max_val = (2 ** bits) - 1
                r = int(r * 255 / max_val)
                g = int(g * 255 / max_val)
                b = int(b * 255 / max_val)

        elif color is not None:
            r = int(color.get("red", "0"))
            g = int(color.get("green", "0"))
            b = int(color.get("blue", "0"))
        else:
            r, g, b = 0, 0, 0

        rects.append((r, g, b, x, y, cx, cy))

    return rects


def format_patch_info(rects) -> str:
    if not rects:
        return "No patch"

    r, g, b, x, y, cx, cy = rects[0]
    rect_count = len(rects)

    parts = [
        f"RGB=({r},{g},{b})",
    ]

    if rect_count > 1:
        parts.append(f"Rect={rect_count}")

    parts.append(f"x={x:.3f} y={y:.3f} w={cx:.3f} h={cy:.3f}")

    return " | ".join(parts)


class ConnectionManager:
    def __init__(self, patch_widget: PatchWidget, logger: Logger):
        self.patch_widget = patch_widget
        self.logger = logger

        self._host = DEFAULT_HOST
        self._port = DEFAULT_PORT

        self._lock = threading.Lock()
        self._should_run = True
        self._connection_enabled = True
        self._generation = 0

        self._status = "Disconnected"
        self._last_waiting_log_time = 0
        self._last_connection_error_text = None

        self.on_status_changed = None
        self.on_patch_info_changed = None

        self.on_rectangles_changed = None

        self._thread = threading.Thread(target=self._network_loop, daemon=True)
        self._thread.start()

    def _set_status(self, text: str):
        self._status = text
        self.logger.log(f"[STATUS] {text}")
        if self.on_status_changed is not None:
            self.on_status_changed(text)

    def _set_patch_info(self, text: str):
        if self.on_patch_info_changed is not None:
            self.on_patch_info_changed(text)

    def configure(self, host: str, port: int, reconnect_now: bool = True):
        with self._lock:
            self._host = host
            self._port = port
            self._connection_enabled = True
            if reconnect_now:
                self._generation += 1

        self.logger.log(f"[CONNECTION] target set to {host}:{port}")
        if reconnect_now:
            self.patch_widget.clear_rectangles_threadsafe()
            self._set_patch_info("No patch")
            self._set_status(f"Reconnecting to {host}:{port}...")

    def reconnect(self):
        with self._lock:
            self._connection_enabled = True
            self._generation += 1
            host = self._host
            port = self._port

        self.patch_widget.clear_rectangles_threadsafe()
        self._set_patch_info("No patch")
        self.logger.log("[CONNECTION] manual reconnect")
        self._set_status(f"Reconnecting to {host}:{port}...")

    def disconnect(self):
        with self._lock:
            self._connection_enabled = False
            self._generation += 1

        self.patch_widget.clear_rectangles_threadsafe()
        self._set_patch_info("No patch")
        self.logger.log("[CONNECTION] manual disconnect")
        self._set_status("Disconnected")

    def stop(self):
        with self._lock:
            self._should_run = False
            self._generation += 1

    def current_host(self) -> str:
        with self._lock:
            return self._host

    def current_port(self) -> int:
        with self._lock:
            return self._port

    def _snapshot(self):
        with self._lock:
            return (
                self._should_run,
                self._connection_enabled,
                self._generation,
                self._host,
                self._port,
            )

    def _network_loop(self):
        retry_delay = 1

        while True:
            should_run, enabled, generation, host, port = self._snapshot()

            if not should_run:
                self.logger.log("[CONNECTION] network loop exited")
                break

            if not enabled:
                time.sleep(0.2)
                continue

            try:
                self._set_status(f"ColourSpace connecting to {host}:{port}...")

                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(INITIAL_CONNECT_TIMEOUT_SEC)
                    sock.connect((host, port))
                    self._set_status(f"ColourSpace connected to {host}:{port}")

                    retry_delay = 1
                    sock.settimeout(1.0)

                    while True:
                        should_run2, enabled2, generation2, host2, port2 = self._snapshot()

                        if not should_run2:
                            return

                        if not enabled2:
                            self.logger.log("[CONNECTION] disabled")
                            break

                        if generation2 != generation:
                            self.logger.log("[CONNECTION] reconnect requested")
                            break

                        try:
                            header = recv_exact(sock, 4)
                        except socket.timeout:
                            continue

                        length = struct.unpack(">i", header)[0]
                        self.logger.log(f"[PATCH RECV] bytes={length}")

                        if length < 0:
                            self.logger.log("[PATCH RECV] end-of-stream")
                            break

                        if length == 0:
                            self.logger.log("[PATCH RECV] empty payload")
                            continue

                        payload = recv_exact(sock, length)
                        xml_text = payload.decode("utf-8", errors="replace")

                        rects = parse_rectangles(xml_text)

                        if rects:
                            r, g, b, x, y, w, h = rects[0]

                            if len(rects) == 1 and x == 0 and y == 0 and w == 1 and h == 1:
                                self.logger.log(f"[PATCH DATA] rgb=({r},{g},{b}) full-frame")
                            else:
                                self.logger.log(
                                    f"[PATCH DATA] rectangles={len(rects)} first_rgb=({r},{g},{b})"
                                )
                        else:
                            self.logger.log("[PATCH DATA] rectangles=0")

                        self.patch_widget.window().apply_patch_from_source("colourspace", rects)
                        self._set_patch_info(format_patch_info(rects))

                        if self.on_rectangles_changed is not None:
                            self.on_rectangles_changed(rects)

            except Exception as e:
                now = time.monotonic()

                if isinstance(e, (ConnectionRefusedError, TimeoutError, socket.timeout)):
                    if now - self._last_waiting_log_time >= 5:
                        if self.patch_widget.window().active_source == "colourspace":
                            self.logger.log("[STATUS] Waiting for ColourSpace...")
                            self._last_waiting_log_time = now
                else:
                    error_text = str(e)
                    if error_text != self._last_connection_error_text:
                        self.logger.log(f"[CONNECTION ERROR] {error_text}")
                        self.logger.log(f"[CONNECTION ERROR] {traceback.format_exc().strip()}")
                        self._last_connection_error_text = error_text

                self.patch_widget.window().apply_patch_from_source("colourspace", [])
                self._set_patch_info("No patch")


            should_run3, enabled3, generation3, host3, port3 = self._snapshot()
            if not should_run3:
                break

            if not enabled3:
                self._set_status("Disconnected")
                continue

            self._set_status(f"ColourSpace reconnect in {retry_delay}s...")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, MAX_RECONNECT_DELAY_SEC)


class DisplayCALConnectionManager:
    def __init__(self, patch_widget: PatchWidget, logger: Logger):
        self.patch_widget = patch_widget
        self.logger = logger

        self._lock = threading.Lock()
        self._should_run = False
        self._host = "127.0.0.1"
        self._port = 20002
        self._thread = None

    def connect_to(self, host: str, port: int):
        with self._lock:
            self._host = host
            self._port = port
            self._should_run = True

        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._network_loop, daemon=True)
            self._thread.start()

        self.logger.log(f"[STATUS] DisplayCAL target set to {host}:{port}")

    def disconnect(self):
        with self._lock:
            self._should_run = False

        self.logger.log("[STATUS] DisplayCAL disconnected")

    def _snapshot(self):
        with self._lock:
            return self._should_run, self._host, self._port

    def _network_loop(self):
        last_error_text = None
        has_logged_waiting = False
        has_logged_connecting = False
        last_connect_log_time = 0

        while True:
            should_run, host, port = self._snapshot()

            if not should_run:
                break

            try:
                if (
                        self.patch_widget.window().active_source == "displaycal"
                        and not has_logged_connecting
                ):
                    self.logger.log(f"[STATUS] DisplayCAL connecting to {host}:{port}...")
                    has_logged_connecting = True

                with socket.create_connection((host, port), timeout=10) as sock:
                    sock.settimeout(1.0)
                    self.logger.log(f"[STATUS] DisplayCAL connected to {host}:{port}")
                    has_logged_waiting = False
                    has_logged_connecting = False
                    last_error_text = None

                    while True:
                        should_run2, host2, port2 = self._snapshot()

                        if not should_run2:
                            return

                        if host2 != host or port2 != port:
                            self.logger.log("[STATUS] DisplayCAL reconnect requested")
                            break

                        try:
                            data = sock.recv(4096)
                        except socket.timeout:
                            continue

                        if not data:
                            window = self.patch_widget.window()
                            window.latest_displaycal_rects = []

                            if window.active_source == "displaycal":
                                window.current_display_rects = []
                                window._set_viewer_status(
                                    window.viewer_source_text,
                                    VIEWER_TYPE_DISPLAYCAL_RX,
                                    VIEWER_CONTENT_WAITING,
                                )

                            break

                        idx = data.find(b"<?xml")
                        if idx < 0:
                            continue

                        try:
                            root = ET.fromstring(data[idx:].decode("utf-8", errors="replace"))
                        except Exception as e:
                            self.logger.log(f"[DISPLAYCAL ERROR] XML parse error: {e}")
                            continue

                        color = root.find("color")
                        background = root.find("background")

                        if color is None:
                            continue

                        r = int(color.get("red"))
                        g = int(color.get("green"))
                        b = int(color.get("blue"))
                        bits = int(color.get("bits"))

                        maxv = (1 << bits) - 1

                        r8 = round(r / maxv * 255)
                        g8 = round(g / maxv * 255)
                        b8 = round(b / maxv * 255)

                        rects = [(r8, g8, b8, 0, 0, 1, 1)]

                        self.logger.log(f"[PATCH RECV] bytes={len(data)}")
                        self.logger.log(f"[PATCH DATA] rgb=({r8},{g8},{b8}) full-frame")

                        self.patch_widget.window().apply_patch_from_source("displaycal", rects)

            except Exception as e:
                now = time.monotonic()

                if isinstance(e, (ConnectionRefusedError, TimeoutError, socket.timeout)):
                    if (
                            self.patch_widget.window().active_source == "displaycal"
                            and not has_logged_waiting
                    ):
                        self.logger.log("[STATUS] Waiting for DisplayCAL...")
                        has_logged_waiting = True
                else:
                    error_text = str(e)
                    if error_text != last_error_text:
                        self.logger.log(f"[DISPLAYCAL ERROR] {error_text}")
                        last_error_text = error_text

                window = self.patch_widget.window()
                window.latest_displaycal_rects = []

                if window.active_source == "displaycal":
                    window.current_display_rects = []
                    window._set_viewer_status(
                        window.viewer_source_text,
                        VIEWER_TYPE_DISPLAYCAL_RX,
                        VIEWER_CONTENT_WAITING,
                    )

            time.sleep(1)


class BridgeServerManager:
    def __init__(self, state_provider, logger: Logger, host="0.0.0.0", port=DEFAULT_BRIDGE_PORT):
        self.state_provider = state_provider
        self.logger = logger
        self.host = host
        self.port = port

        self.require_secure = False
        self.access_token = ""
        self.local_only = True

        self._httpd = None
        self._thread = None

    def start(self):
        if self._httpd is not None:
            return

        manager = self

        class RequestHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                from urllib.parse import urlparse, parse_qs

                parsed = urlparse(self.path)
                route = parsed.path
                params = parse_qs(parsed.query)
                token = params.get("token", [""])[0]

                client_ip = self.client_address[0]

                is_local = client_ip in ("127.0.0.1", "::1")

                if manager.local_only and not is_local and route in ("/view", "/state", "/current-image"):
                    self.send_response(403)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(
                        b"This page is currently limited to the localhost computer.\n"
                        b"To allow other devices, switch the access mode in the app."
                    )
                    return

                if manager.require_secure and not is_local and route in ("/view", "/state", "/current-image"):
                    if token != manager.access_token:
                        self.send_response(403)
                        self.send_header("Content-Type", "text/plain; charset=utf-8")
                        self.end_headers()
                        self.wfile.write(
                            b"This page currently requires an access token.\n"
                            b"Please use the secure viewer URL provided by the app."
                        )
                        return

                if route == "/view":
                    html = """
                <!doctype html>
                <html lang="zh-Hant">
                <head>
                  <meta charset="utf-8">
                  <meta name="viewport" content="width=device-width, initial-scale=1">
                  <title>ColourSpace Bridge Viewer</title>
                  <style>
                    html, body {
                      margin: 0;
                      width: 100%;
                      height: 100%;
                      background: black;
                      color: white;
                      font-family: sans-serif;
                      overflow: hidden;
                    }

                    #screen {
                      position: fixed;
                      inset: 0;
                      background: black;
                      overflow: hidden;
                    }

                    #rect-layer {
                      position: absolute;
                      inset: 0;
                    }

                    #image-layer {
                      position: absolute;
                      inset: 0;
                      width: 100%;
                      height: 100%;
                      object-fit: contain;
                      display: none;
                    }

                    .rect {
                      position: absolute;
                      box-sizing: border-box;
                    }

                    #hud {
                      position: fixed;
                      left: 16px;
                      bottom: 16px;
                      padding: 10px 12px;
                      background: rgba(0, 0, 0, 0.55);
                      border: 1px solid rgba(255,255,255,0.15);
                      border-radius: 8px;
                      font-size: 14px;
                      line-height: 1.5;
                      white-space: pre-line;
                      opacity: 0;
                      transition: opacity 0.2s ease;
                      pointer-events: none;
                    }
                  </style>
                </head>
                <body>
                  <div id="screen">
                    <img id="image-layer" alt="">
                    <div id="rect-layer"></div>
                  </div>
                  <div id="hud">Loading...</div>

                  <script>
                    const screen = document.getElementById("screen");
                    const imageLayer = document.getElementById("image-layer");
                    const rectLayer = document.getElementById("rect-layer");
                    const hud = document.getElementById("hud");
                    const pageParams = new URLSearchParams(window.location.search);
                    const accessToken = pageParams.get("token") || "";

                    let lastHudStateKey = "";
                    let hudHideTimer = null;

                    function showHudTemporarily() {
                      hud.style.opacity = "1";

                      if (hudHideTimer) {
                        clearTimeout(hudHideTimer);
                      }

                      hudHideTimer = setTimeout(() => {
                        hud.style.opacity = "0";
                      }, 1800);
                    }

                    window.addEventListener("mousemove", showHudTemporarily);
                    window.addEventListener("mousedown", showHudTemporarily);
                    window.addEventListener("click", showHudTemporarily);
                    window.addEventListener("keydown", showHudTemporarily);
                    window.addEventListener("touchstart", showHudTemporarily);
                    window.addEventListener("touchmove", showHudTemporarily);

                    function withToken(path) {
                      if (!accessToken) {
                        return path;
                      }
                      const separator = path.includes("?") ? "&" : "?";
                      return `${path}${separator}token=${encodeURIComponent(accessToken)}`;
                    }

                    function renderRectangles(rectangles) {
                      rectLayer.innerHTML = "";

                      for (const rect of rectangles) {
                        const [r, g, b, x, y, w, h] = rect;

                        const el = document.createElement("div");
                        el.className = "rect";
                        el.style.left = `${x * 100}%`;
                        el.style.top = `${y * 100}%`;
                        el.style.width = `${w * 100}%`;
                        el.style.height = `${h * 100}%`;
                        el.style.background = `rgb(${r}, ${g}, ${b})`;

                        rectLayer.appendChild(el);
                      }
                    }

                    function showImageMode() {
                      imageLayer.style.display = "block";
                      rectLayer.style.display = "none";
                    }

                    function showRectMode() {
                      imageLayer.style.display = "none";
                      rectLayer.style.display = "block";
                    }

                    async function updateState() {
                      try {
                        const res = await fetch(withToken("/state"), { cache: "no-store" });

                    if (!res.ok) {
                      const message = await res.text();

                      screen.style.background = "black";
                      imageLayer.style.display = "none";
                      rectLayer.style.display = "none";
                      rectLayer.innerHTML = "";

                      hud.textContent = `Bridge｜HTTP Error｜${message || `Status ${res.status}`}`;
                      return;
                    }

                        const state = await res.json();

                        const stateKey = JSON.stringify({
                          mode: state.mode,
                          label: state.label,
                          solid_rgb: state.solid_rgb,
                          image_path: state.image_path,
                          rectangles: state.rectangles
                        });

                        if (stateKey !== lastHudStateKey) {
                          showHudTemporarily();
                          lastHudStateKey = stateKey;
                        }

                        let hudText = `${state.web_viewer_source_text || "-"}｜${state.viewer_type_text || "-"}｜${state.viewer_content_text || "-"}`;

                    if (state.mode === "solid" && state.solid_rgb) {
                      const [r, g, b] = state.solid_rgb;
                      screen.style.background = `rgb(${r}, ${g}, ${b})`;
                      imageLayer.style.display = "none";
                      rectLayer.style.display = "none";
                      rectLayer.innerHTML = "";
                    }
                    else if (state.mode === "external") {
                      screen.style.background = "black";
                      showRectMode();
                      renderRectangles(state.rectangles || []);
                     const rectCount = Array.isArray(state.rectangles)
                      ? state.rectangles.length
                      : (state.rectangles || 0);

                    if (rectCount > 1) {
                    }
                    }
                    else if (state.mode === "image") {
                      screen.style.background = "black";
                      showImageMode();

                      const imageUrl = withToken(`/current-image?t=${Date.now()}`);
                      if (imageLayer.dataset.currentSrc !== imageUrl) {
                        imageLayer.src = imageUrl;
                        imageLayer.dataset.currentSrc = imageUrl;
                      }

                    }
                    else {
                      screen.style.background = "black";
                      imageLayer.style.display = "none";
                      rectLayer.style.display = "none";
                      rectLayer.innerHTML = "";
                    }

                        hud.textContent = hudText;
                        } catch (err) {
                          hud.textContent = `Bridge｜Connection Lost｜Please check network or app`;
                        }
                    }

                    updateState();
                    showHudTemporarily();
                    setInterval(updateState, 250);
                  </script>
                </body>
                </html>
                    """.strip().encode("utf-8")

                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(html)))
                    self.end_headers()
                    self.wfile.write(html)
                    return

                if route == "/current-image":
                    state = manager.state_provider()
                    image_path = state.get("image_path")

                    if not image_path:
                        self.send_response(404)
                        self.send_header("Content-Type", "text/plain; charset=utf-8")
                        self.end_headers()
                        self.wfile.write(b"No current image")
                        return

                    path = Path(image_path)

                    if not path.exists() or not path.is_file():
                        self.send_response(404)
                        self.send_header("Content-Type", "text/plain; charset=utf-8")
                        self.end_headers()
                        self.wfile.write(b"Image file not found")
                        return

                    suffix = path.suffix.lower()

                    # 瀏覽器原生支援格式：直接回傳原檔
                    if suffix == ".png":
                        content_type = "image/png"
                        try:
                            body = path.read_bytes()
                        except Exception as e:
                            self.send_response(500)
                            self.send_header("Content-Type", "text/plain; charset=utf-8")
                            self.end_headers()
                            self.wfile.write(f"Failed to read image: {e}".encode("utf-8", errors="replace"))
                            return

                        self.send_response(200)
                        self.send_header("Content-Type", content_type)
                        self.send_header("Content-Length", str(len(body)))
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        try:
                            self.wfile.write(body)
                        except (BrokenPipeError, ConnectionResetError):
                            return
                        return

                    elif suffix in (".jpg", ".jpeg"):
                        content_type = "image/jpeg"
                        try:
                            body = path.read_bytes()
                        except Exception as e:
                            self.send_response(500)
                            self.send_header("Content-Type", "text/plain; charset=utf-8")
                            self.end_headers()
                            self.wfile.write(f"Failed to read image: {e}".encode("utf-8", errors="replace"))
                            return

                        self.send_response(200)
                        self.send_header("Content-Type", content_type)
                        self.send_header("Content-Length", str(len(body)))
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        try:
                            self.wfile.write(body)
                        except (BrokenPipeError, ConnectionResetError):
                            return
                        return

                    elif suffix == ".webp":
                        content_type = "image/webp"
                        try:
                            body = path.read_bytes()
                        except Exception as e:
                            self.send_response(500)
                            self.send_header("Content-Type", "text/plain; charset=utf-8")
                            self.end_headers()
                            self.wfile.write(f"Failed to read image: {e}".encode("utf-8", errors="replace"))
                            return

                        self.send_response(200)
                        self.send_header("Content-Type", content_type)
                        self.send_header("Content-Length", str(len(body)))
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        try:
                            self.wfile.write(body)
                        except (BrokenPipeError, ConnectionResetError):
                            return
                        return

                    # 其他格式：嘗試用 QImageReader 讀取後轉成 PNG 再送給瀏覽器
                    else:
                        reader = QImageReader(str(path))
                        reader.setAutoTransform(True)
                        image = reader.read()

                        if image.isNull():
                            self.send_response(415)
                            self.send_header("Content-Type", "text/plain; charset=utf-8")
                            self.end_headers()
                            self.wfile.write(b"Unsupported image type")
                            return

                        byte_array = QByteArray()
                        buffer = QBuffer(byte_array)
                        buffer.open(QIODevice.OpenModeFlag.WriteOnly)

                        ok = image.save(buffer, "PNG")
                        buffer.close()

                        if not ok:
                            self.send_response(500)
                            self.send_header("Content-Type", "text/plain; charset=utf-8")
                            self.end_headers()
                            self.wfile.write(b"Failed to convert image to PNG")
                            return

                        body = bytes(byte_array)

                        if not body:
                            self.send_response(500)
                            self.send_header("Content-Type", "text/plain; charset=utf-8")
                            self.end_headers()
                            self.wfile.write(b"Failed to generate PNG data")
                            return

                        self.send_response(200)
                        self.send_header("Content-Type", "image/png")
                        self.send_header("Content-Length", str(len(body)))
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        try:
                            self.wfile.write(body)
                        except (BrokenPipeError, ConnectionResetError):
                            return
                        return

                if route == "/state":
                    payload = manager.state_provider()
                    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    try:
                        self.wfile.write(body)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    return

                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Not Found")

            def log_message(self, format, *args):
                # 不要每次 request 都在 console 狂洗 log
                return

        self._httpd = ThreadingHTTPServer((self.host, self.port), RequestHandler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

        self.logger.log(f"[BRIDGE] HTTP server started at http://{self.host}:{self.port}/state")

    def stop(self):
        if self._httpd is None:
            return

        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None
        self._thread = None

        self.logger.log("[BRIDGE] HTTP server stopped")


class ViewerLabel(QLabel):
    def __init__(self):
        super().__init__()
        self._rectangles = []

    def set_rectangles(self, rectangles):
        self._rectangles = rectangles or []
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)

        if not self._rectangles:
            return

        painter = QPainter(self)
        w = self.width()
        h = self.height()

        for rect in self._rectangles:
            r, g, b, x, y, rw, rh = rect

            painter.fillRect(
                int(x * w),
                int(y * h),
                int(rw * w),
                int(rh * h),
                QColor(r, g, b),
            )


class MainWindow(QMainWindow):
    connection_status_signal = Signal(str)
    patch_info_signal = Signal(str)
    external_rectangles_signal = Signal(object)

    def __init__(self, settings: QSettings, logger: Logger):
        super().__init__()

        self.settings = settings
        self.logger = logger

        self.connection_status_text = "Starting..."
        self.patch_info_text = "No patch"
        self.viewer_source_text = VIEWER_SOURCE_MANUAL
        self.viewer_type_text = VIEWER_TYPE_COLOURSPACE_RX
        self.viewer_content_text = VIEWER_CONTENT_WAITING

        self._pending_solid_patch_log_label = None

        self._solid_patch_log_timer = QTimer(self)
        self._solid_patch_log_timer.setSingleShot(True)
        self._solid_patch_log_timer.setInterval(150)
        self._solid_patch_log_timer.timeout.connect(self._flush_solid_patch_log)

        self.current_access_token = DEFAULT_ACCESS_TOKEN
        self.settings.setValue("access_token", self.current_access_token)

        self.current_display_state = {
            "mode": "external",  # external / solid / image
            "label": "No patch",
            "rectangles": [],
            "solid_rgb": None,
            "image_path": None,
        }
        self.latest_external_display_state = {
            "mode": "external",
            "label": "No patch",
            "rectangles": [],
            "solid_rgb": None,
            "image_path": None,
        }
        self.latest_external_rectangles = []
        self.latest_external_label = "No patch"
        self.current_image_pattern_path = None
        self.rgb_selected_channel = 0

        show_status = self.settings.value(SETTINGS_KEY_SHOW_STATUS_BAR, True)
        if isinstance(show_status, str):
            show_status = show_status.lower() == "true"

        self.connection_status_signal.connect(self.update_connection_status)
        self.patch_info_signal.connect(self.update_patch_info)
        self.external_rectangles_signal.connect(self.handle_external_rectangles_changed)

        self.setWindowTitle(APP_DISPLAY_NAME)
        self.resize(960, 540)

        if sys.platform == "darwin":
            icon_path = BASE_DIR / "assets" / "icon.icns"
        elif sys.platform == "win32":
            icon_path = BASE_DIR / "assets" / "icon.ico"
        else:
            icon_path = None

        if icon_path and icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.patch_widget = PatchWidget(logger=self.logger)
        self.setCentralWidget(self.patch_widget)
        self.patch_widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.custom_color_panel = CustomColorPanel(self)

        r_value = self.settings.value(SETTINGS_KEY_CUSTOM_R, 128)
        g_value = self.settings.value(SETTINGS_KEY_CUSTOM_G, 128)
        b_value = self.settings.value(SETTINGS_KEY_CUSTOM_B, 128)

        try:
            initial_r = int(str(r_value))
        except ValueError:
            initial_r = 128

        try:
            initial_g = int(str(g_value))
        except ValueError:
            initial_g = 128

        try:
            initial_b = int(str(b_value))
        except ValueError:
            initial_b = 128

        self.custom_color_panel.set_rgb(initial_r, initial_g, initial_b)
        self.custom_color_panel.hide()
        self.patch_widget.setFocus()

        self.custom_color_panel.load_swatches(self.load_custom_swatches())

        self.custom_color_panel.r_slider.valueChanged.connect(self.handle_custom_color_changed)
        self.custom_color_panel.g_slider.valueChanged.connect(self.handle_custom_color_changed)
        self.custom_color_panel.b_slider.valueChanged.connect(self.handle_custom_color_changed)
        self.custom_color_panel.r_spin.installEventFilter(self)
        self.custom_color_panel.g_spin.installEventFilter(self)
        self.custom_color_panel.b_spin.installEventFilter(self)
        self.custom_color_panel.r_spin.lineEdit().installEventFilter(self)
        self.custom_color_panel.g_spin.lineEdit().installEventFilter(self)
        self.custom_color_panel.b_spin.lineEdit().installEventFilter(self)

        self.connection_manager = ConnectionManager(
            patch_widget=self.patch_widget,
            logger=self.logger,
        )

        self.displaycal_connection_manager = DisplayCALConnectionManager(
            patch_widget=self.patch_widget,
            logger=self.logger,
        )

        self.latest_colourspace_rects = []
        self.latest_displaycal_rects = []
        self.current_display_rects = []

        self.active_source = "colourspace"

        bridge_port_value = self.settings.value("bridge_port", DEFAULT_BRIDGE_PORT)
        try:
            self.bridge_port = int(bridge_port_value)
        except (TypeError, ValueError):
            self.bridge_port = DEFAULT_BRIDGE_PORT

        self.bridge_server = BridgeServerManager(
            state_provider=self.get_display_state_snapshot,
            logger=self.logger,
            host="0.0.0.0",
            port=self.bridge_port,
        )
        try:
            self.bridge_server.start()
        except OSError as e:
            self.logger.log(f"[BRIDGE ERROR] Port already in use, skipping server start: {e}")

        self.connection_manager.on_status_changed = self.connection_status_signal.emit
        self.connection_manager.on_patch_info_changed = self.patch_info_signal.emit
        self.connection_manager.on_rectangles_changed = self.external_rectangles_signal.emit

        self.log_dialog = None
        self.shortcuts_dialog = None
        self.connected_devices_dialog = None
        self.sync_seen_devices = {}

        self.viewer_windows = {}
        self._build_menu(show_status=show_status)
        self._update_token_display()
        self._update_bridge_port_display()
        self._update_connected_target_display()
        self._update_device_name_display()
        self._apply_bridge_security_settings()

        self.viewer_status_label = QLabel()
        self.viewer_status_label.setContentsMargins(8, 0, 0, 0)
        self.viewer_status_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.statusBar().addWidget(self.viewer_status_label, 1)
        self.sync_status_label = QLabel("SYNC: OFF")
        self.local_ip_label = QLabel(f"IP: {self._detect_lan_host()}")
        self.local_ip_label.setStyleSheet("color: #888888;")
        self.statusBar().addPermanentWidget(self.local_ip_label)
        self.statusBar().addPermanentWidget(self.sync_status_label)
        self.sync_manager = SyncManager(self)
        self._update_sync_port_display()
        QApplication.instance().installEventFilter(self)

        sync_port_value = self.settings.value("sync/port", 8766)
        try:
            self.sync_manager.port = int(sync_port_value)
        except (TypeError, ValueError):
            self.sync_manager.port = 8766

        self.refresh_status_bar()
        self._update_sync_status_label()
        self._update_sync_action_states()
        self.installEventFilter(self)

        if not show_status:
            self.statusBar().hide()

        host_value = self.settings.value(SETTINGS_KEY_HOST, DEFAULT_HOST)
        host: str = str(host_value)

        port_value = self.settings.value(SETTINGS_KEY_PORT, DEFAULT_PORT)
        if isinstance(port_value, int):
            port: int = port_value
        elif isinstance(port_value, str):
            try:
                port = int(port_value)
            except ValueError:
                port = DEFAULT_PORT
        else:
            port = DEFAULT_PORT

        self.connection_manager.configure(host=host, port=port, reconnect_now=False)
        self.connection_manager.reconnect()

        self.logger.log("[SYSTEM] MainWindow initialized")

    def apply_patch_from_source(self, source, rects):
        if source == "colourspace":
            self.latest_colourspace_rects = rects
        elif source == "displaycal":
            self.latest_displaycal_rects = rects

        if source != self.active_source:
            return

        if self.current_display_state.get("mode") != "external":
            return

        self.patch_widget.set_rectangles_threadsafe(rects)
        self.current_display_rects = rects

        viewer_type = (
            VIEWER_TYPE_DISPLAYCAL_RX
            if source == "displaycal"
            else VIEWER_TYPE_COLOURSPACE_RX
        )

        if rects:
            r, g, b, *_ = rects[0]
            self._set_viewer_status(
                self.viewer_source_text,
                viewer_type,
                format_rgb(r, g, b),
            )
        else:
            self._set_viewer_status(
                self.viewer_source_text,
                viewer_type,
                VIEWER_CONTENT_WAITING,
            )

        self.current_display_state = {
            "mode": "external",
            "label": "DisplayCAL Rx" if source == "displaycal" else "ColourSpace Rx",
            "rectangles": list(rects),
            "solid_rgb": None,
            "image_path": None,
        }

        for window in self.viewer_windows.values():
            self.apply_state_to_viewer(window.viewer)

    def _build_menu(self, show_status: bool):
        menu_bar = self.menuBar()

        connection_menu = menu_bar.addMenu("RxSource")
        patterns_menu = menu_bar.addMenu("Patterns")
        sync_menu = menu_bar.addMenu("Sync")
        bridge_menu = menu_bar.addMenu("Bridge")
        view_menu = menu_bar.addMenu("Viewer")
        help_menu = menu_bar.addMenu("Help")

        self.settings_action = QAction("Connection Settings…", self)
        self.settings_action.setMenuRole(QAction.MenuRole.NoRole)
        self.settings_action.triggered.connect(self.open_settings_dialog)
        connection_menu.addAction(self.settings_action)

        connection_menu.addSeparator()

        self.reconnect_action = QAction("Reconnect", self)
        self.reconnect_action.setMenuRole(QAction.MenuRole.NoRole)
        self.reconnect_action.triggered.connect(self.handle_reconnect)
        connection_menu.addAction(self.reconnect_action)

        self.disconnect_action = QAction("Disconnect", self)
        self.disconnect_action.setMenuRole(QAction.MenuRole.NoRole)
        self.disconnect_action.triggered.connect(self.handle_disconnect)
        connection_menu.addAction(self.disconnect_action)

        connection_menu.addSeparator()

        self.action_current_connected_ip = QAction("", self)
        self.action_current_connected_ip.setEnabled(False)
        self.action_current_connected_ip.setVisible(False)
        connection_menu.addAction(self.action_current_connected_ip)

        self.action_current_connected_port = QAction("", self)
        self.action_current_connected_port.setEnabled(False)
        self.action_current_connected_port.setVisible(False)
        connection_menu.addAction(self.action_current_connected_port)

        self.new_viewer_action = QAction("Add Viewer", self)
        self.new_viewer_action.setShortcut(QKeySequence("Ctrl+N"))
        self.new_viewer_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.new_viewer_action.triggered.connect(self.open_new_viewer)
        view_menu.addAction(self.new_viewer_action)

        view_menu.addSeparator()

        self.viewer_windows_menu = view_menu.addMenu("Viewer Manager")
        self.refresh_viewers_menu()

        view_menu.addSeparator()

        self.toggle_statusbar_action = QAction("Show Status Bar", self)
        self.toggle_statusbar_action.setMenuRole(QAction.MenuRole.NoRole)
        self.toggle_statusbar_action.setCheckable(True)
        self.toggle_statusbar_action.setChecked(show_status)
        self.toggle_statusbar_action.setShortcut(QKeySequence("Ctrl+B"))
        self.toggle_statusbar_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.toggle_statusbar_action.triggered.connect(self.toggle_status_bar)
        view_menu.addAction(self.toggle_statusbar_action)

        self.show_log_action = QAction("Show Log", self)
        self.show_log_action.setMenuRole(QAction.MenuRole.NoRole)
        self.show_log_action.triggered.connect(self.open_log_dialog)
        help_menu.addAction(self.show_log_action)

        self.enable_file_log_action = QAction("Enable File Log", self)
        self.enable_file_log_action.setMenuRole(QAction.MenuRole.NoRole)
        self.enable_file_log_action.setCheckable(True)
        self.enable_file_log_action.setChecked(self.logger.enable_file_log)
        self.enable_file_log_action.triggered.connect(self.toggle_file_log)
        help_menu.addAction(self.enable_file_log_action)

        help_menu.addSeparator()

        self.show_shortcuts_action = QAction("Keyboard Shortcuts", self)
        self.show_shortcuts_action.setMenuRole(QAction.MenuRole.NoRole)
        self.show_shortcuts_action.setShortcut(QKeySequence("Ctrl+/"))
        self.show_shortcuts_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.show_shortcuts_action.triggered.connect(self.show_shortcuts_dialog)
        help_menu.addAction(self.show_shortcuts_action)

        help_menu.addSeparator()

        self.reset_connection_settings_action = QAction("Reset Connection Settings", self)
        self.reset_connection_settings_action.setMenuRole(QAction.MenuRole.NoRole)
        self.reset_connection_settings_action.triggered.connect(self._on_reset_connection_settings)
        help_menu.addAction(self.reset_connection_settings_action)

        self.reset_all_custom_settings_action = QAction("Restore Factory Settings", self)
        self.reset_all_custom_settings_action.setMenuRole(QAction.MenuRole.NoRole)
        self.reset_all_custom_settings_action.triggered.connect(self._on_reset_all_custom_settings)
        help_menu.addAction(self.reset_all_custom_settings_action)

        help_menu.addSeparator()

        self.about_action = QAction("About", self)
        self.about_action.setMenuRole(QAction.MenuRole.NoRole)
        self.about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(self.about_action)

        self.pattern_return_action = QAction("Rx from ColourSpace", self)
        self.pattern_return_action.setShortcut(QKeySequence("Ctrl+R"))
        self.pattern_return_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.pattern_return_action.triggered.connect(self.return_to_colourspace)
        patterns_menu.addAction(self.pattern_return_action)

        self.pattern_displaycal_action = QAction("Rx from DisplayCAL", self)
        self.pattern_displaycal_action.setShortcut(QKeySequence("Ctrl+E"))
        self.pattern_displaycal_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.pattern_displaycal_action.triggered.connect(self.return_to_displaycal)
        patterns_menu.addAction(self.pattern_displaycal_action)

        patterns_menu.addSeparator()

        self.pattern_custom_color_action = QAction("Local Custom Color…", self)
        self.pattern_custom_color_action.setShortcut(QKeySequence("Ctrl+L"))
        self.pattern_custom_color_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.pattern_custom_color_action.triggered.connect(self.show_pattern_custom_color)
        patterns_menu.addAction(self.pattern_custom_color_action)

        self.test_patterns_menu = patterns_menu.addMenu("Test Patterns")
        self.rebuild_image_test_pattern_menu()

        self.user_patterns_menu = patterns_menu.addMenu("User Patterns")
        self.rebuild_user_pattern_menu()

        # ===== Sync =====
        self.sync_controller_action = QAction("Controller", self)
        self.sync_controller_action.setCheckable(True)
        self.sync_controller_action.setChecked(False)
        sync_menu.addAction(self.sync_controller_action)

        self.sync_follower_action = QAction("Follower", self)
        self.sync_follower_action.setCheckable(True)
        self.sync_follower_action.setChecked(False)
        sync_menu.addAction(self.sync_follower_action)

        self.sync_stop_action = QAction("Stop Sync", self)
        self.sync_stop_action.setCheckable(True)
        self.sync_stop_action.setChecked(True)
        sync_menu.addAction(self.sync_stop_action)

        sync_menu.addSeparator()

        self.sync_take_control_action = QAction("Take Control", self)
        self.sync_take_control_action.setEnabled(False)
        self.sync_take_control_action.triggered.connect(self._on_sync_take_control_triggered)
        sync_menu.addAction(self.sync_take_control_action)

        self.sync_allow_local_override_action = QAction("Allow Local Override", self)
        self.sync_allow_local_override_action.setCheckable(True)
        self.sync_allow_local_override_action.setChecked(True)
        sync_menu.addAction(self.sync_allow_local_override_action)

        self.sync_allow_incoming_override_action = QAction("Allow Incoming Override", self)
        self.sync_allow_incoming_override_action.setCheckable(True)
        self.sync_allow_incoming_override_action.setChecked(True)
        sync_menu.addAction(self.sync_allow_incoming_override_action)

        sync_menu.addSeparator()

        self.sync_settings_action = QAction("Sync Settings…", self)
        self.sync_settings_action.triggered.connect(self._on_sync_settings)
        sync_menu.addAction(self.sync_settings_action)

        self.sync_show_devices_action = QAction("Show Connected Devices…", self)
        self.sync_show_devices_action.setShortcut(QKeySequence("Ctrl+D"))
        self.sync_show_devices_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.sync_show_devices_action.triggered.connect(self.open_connected_devices_dialog)
        sync_menu.addAction(self.sync_show_devices_action)

        self.sync_controller_action.toggled.connect(self._on_sync_controller_toggled)
        self.sync_follower_action.toggled.connect(self._on_sync_follower_toggled)
        self.sync_stop_action.toggled.connect(self._on_sync_stop_toggled)

        sync_menu.addSeparator()

        self.action_current_device_name = QAction("", self)
        self.action_current_device_name.setEnabled(False)
        sync_menu.addAction(self.action_current_device_name)

        self.action_current_sync_port = QAction("", self)
        self.action_current_sync_port.setEnabled(False)
        sync_menu.addAction(self.action_current_sync_port)

        self.action_local_only = QAction("Localhost Only", self)
        self.action_local_only.setCheckable(True)
        self.action_local_only.setChecked(True)
        bridge_menu.addAction(self.action_local_only)

        self.action_enable_lan = QAction("Enable LAN Access", self)
        self.action_enable_lan.setCheckable(True)
        self.action_enable_lan.setChecked(False)
        bridge_menu.addAction(self.action_enable_lan)

        self.action_enable_secure = QAction("Secure Mode", self)
        self.action_enable_secure.setCheckable(True)
        self.action_enable_secure.setChecked(False)
        bridge_menu.addAction(self.action_enable_secure)

        bridge_menu.addSeparator()

        self.action_copy_local = QAction("Copy Local Viewer URL", self)
        bridge_menu.addAction(self.action_copy_local)
        self.action_copy_local.triggered.connect(self._on_copy_local_viewer_url)

        self.action_copy_lan = QAction("Copy LAN Viewer URL", self)
        bridge_menu.addAction(self.action_copy_lan)
        self.action_copy_lan.triggered.connect(self._on_copy_lan_viewer_url)

        self.action_copy_secure = QAction("Copy Secure Viewer URL", self)
        bridge_menu.addAction(self.action_copy_secure)
        self.action_copy_secure.triggered.connect(self._on_copy_secure_viewer_url)

        bridge_menu.addSeparator()

        self.action_set_custom_token = QAction("Bridge Settings...", self)
        bridge_menu.addAction(self.action_set_custom_token)
        self.action_set_custom_token.triggered.connect(self._on_set_custom_token)

        self.action_current_token = QAction("", self)
        self.action_current_token.setEnabled(False)
        bridge_menu.addAction(self.action_current_token)

        self.action_current_bridge_port = QAction("", self)
        self.action_current_bridge_port.setEnabled(False)
        bridge_menu.addAction(self.action_current_bridge_port)

        self.action_local_only.toggled.connect(self._on_local_only_toggled)
        self.action_enable_lan.toggled.connect(self._on_enable_lan_toggled)
        self.action_enable_secure.toggled.connect(self._on_enable_secure_toggled)
        self.action_enable_secure.toggled.connect(lambda _: self._apply_bridge_security_settings())
        self.action_local_only.toggled.connect(lambda _: self._apply_bridge_security_settings())
        self.action_enable_lan.toggled.connect(lambda _: self._apply_bridge_security_settings())

        # === Global Shortcut ===
        self.shortcut_close_window = QShortcut(QKeySequence("Ctrl+W"), self)
        self.shortcut_close_window.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.shortcut_close_window.activated.connect(self.close_active_window)

    def close_active_window(self):
        window = QApplication.activeWindow()

        if window is None:
            return

        window.close()

    def _set_viewer_pixmap_scaled(self, viewer_widget, pixmap):
        if pixmap is None or pixmap.isNull():
            viewer_widget.clear()
            return

        viewer_widget._source_pixmap = pixmap
        scaled = pixmap.scaled(
            viewer_widget.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        viewer_widget.setPixmap(scaled)

    def _refresh_viewer_pixmap(self, viewer_widget):
        pixmap = getattr(viewer_widget, "_source_pixmap", None)
        if pixmap is None or pixmap.isNull():
            return

        scaled = pixmap.scaled(
            viewer_widget.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        viewer_widget.setPixmap(scaled)

    def get_last_visible_viewer_window(self):
        for viewer_id in sorted(self.viewer_windows.keys(), reverse=True):
            window = self.viewer_windows[viewer_id]
            if window.isVisible() and not window.isMinimized():
                return window
        return None

    def open_new_viewer(self):
        viewer_id = self.get_next_viewer_id()

        window = QWidget()
        window.setWindowTitle(f"Viewer {viewer_id}")

        last_window = QApplication.activeWindow()

        if last_window not in self.viewer_windows.values() and last_window != self:
            last_window = self.get_last_visible_viewer_window()

        if last_window is not None:
            window.resize(last_window.size())
        else:
            window.resize(960, 540)

        if last_window is not None:
            base_pos = last_window.pos()
            screen = last_window.screen()
        else:
            base_pos = self.pos()
            screen = self.screen()

        if screen is not None:
            available = screen.availableGeometry()
        else:
            available = QApplication.primaryScreen().availableGeometry()

        step = 40

        new_x = base_pos.x() + step
        new_y = base_pos.y() + step

        frame_w = window.width()
        frame_h = window.height()

        screen_start_x = available.x() + 60
        screen_start_y = available.y() + 60

        wrap_index = ((viewer_id - 2) // 6) % 6
        wrap_offset_y = wrap_index * 30

        if new_x + frame_w > available.right() or new_y + frame_h > available.bottom():
            new_x = screen_start_x
            new_y = screen_start_y + wrap_offset_y

        window.move(new_x, new_y)

        layout = QVBoxLayout(window)
        layout.setContentsMargins(0, 0, 0, 0)

        viewer = ViewerLabel()
        viewer.setStyleSheet("background-color: black;")
        viewer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        viewer.setScaledContents(False)
        viewer.setMinimumSize(0, 0)
        viewer.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        viewer._source_pixmap = None

        layout.addWidget(viewer)

        window.viewer = viewer
        window.viewer_id = viewer_id

        def _on_close(event):
            if viewer_id in self.viewer_windows:
                del self.viewer_windows[viewer_id]
            self.refresh_viewers_menu()
            QWidget.closeEvent(window, event)

        window.closeEvent = _on_close

        def _on_resize(event):
            self._refresh_viewer_pixmap(window.viewer)
            QWidget.resizeEvent(window, event)

        window.resizeEvent = _on_resize

        self.viewer_windows[viewer_id] = window

        window.show()
        self.apply_state_to_viewer(window.viewer)
        window.raise_()
        window.activateWindow()

        self.refresh_viewers_menu()

    def get_next_viewer_id(self):
        existing = set(self.viewer_windows.keys())
        i = 2
        while i in existing:
            i += 1
        return i

    def close_all_viewers(self):
        for window in list(self.viewer_windows.values()):
            window.close()

    def refresh_viewers_menu(self):
        self.viewer_windows_menu.clear()

        for viewer_id, window in sorted(self.viewer_windows.items()):
            action = QAction(f"Viewer {viewer_id}", self)

            def make_handler(w=window):
                return lambda: self._bring_viewer_to_front(w)

            action.triggered.connect(make_handler())
            self.viewer_windows_menu.addAction(action)

        if self.viewer_windows:
            self.viewer_windows_menu.addSeparator()

        action = QAction("Close All", self)
        action.setShortcut(QKeySequence("Ctrl+Shift+W"))
        action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        action.triggered.connect(self.close_all_viewers)
        self.viewer_windows_menu.addAction(action)
        action.setEnabled(bool(self.viewer_windows))

    def _bring_viewer_to_front(self, window):
        window.show()
        window.raise_()
        window.activateWindow()

    def _on_set_custom_token(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Bridge Settings")

        layout = QVBoxLayout()

        token_label = QLabel("Access Token:")
        layout.addWidget(token_label)

        line_edit = QLineEdit()
        line_edit.setText(self.current_access_token or "")
        layout.addWidget(line_edit)

        port_label = QLabel("Bridge Port:")
        layout.addWidget(port_label)

        port_spin = QSpinBox()
        port_spin.setRange(1, 65535)
        port_spin.setValue(int(getattr(self, "bridge_port", DEFAULT_BRIDGE_PORT)))
        layout.addWidget(port_spin)

        btn_row = QHBoxLayout()

        random_btn = QPushButton("Random")
        ok_btn = QPushButton("Save")
        cancel_btn = QPushButton("Cancel")

        ok_btn.setDefault(True)
        ok_btn.setAutoDefault(True)

        btn_row.addWidget(random_btn)
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)

        layout.addLayout(btn_row)

        hint_label = QLabel(
            "Applies and copies the secure URL."
        )
        hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint_label.setStyleSheet("color: #888; font-size: 11px;")
        hint_label.setWordWrap(True)

        layout.addWidget(hint_label)

        dialog.setLayout(layout)

        def generate_random():
            token = secrets.token_hex(3)
            line_edit.setText(token)

        random_btn.clicked.connect(generate_random)
        ok_btn.clicked.connect(dialog.accept)
        cancel_btn.clicked.connect(dialog.reject)

        if dialog.exec():
            text = line_edit.text().strip()
            new_port = port_spin.value()

            if text:
                self.current_access_token = text
                self.settings.setValue("access_token", self.current_access_token)
            else:
                self.current_access_token = ""
                self.settings.setValue("access_token", "")

            old_port = getattr(self, "bridge_port", DEFAULT_BRIDGE_PORT)
            self.bridge_port = new_port
            self.settings.setValue("bridge_port", self.bridge_port)

            self._apply_bridge_security_settings()
            self._update_token_display()
            self._update_bridge_port_display()

            if hasattr(self, "bridge_server") and self.bridge_server:
                try:
                    if old_port != self.bridge_port:
                        self.bridge_server.stop()
                        self.bridge_server.port = self.bridge_port
                        self.bridge_server.start()
                        self.logger.log(f"[BRIDGE] port changed: {old_port} -> {self.bridge_port}")

                    self.logger.log(
                        f"[BRIDGE] settings updated: port={self.bridge_port}, "
                        f"token={'set' if self.current_access_token else 'empty'}"
                    )
                except Exception as e:
                    self.logger.log(f"[BRIDGE ERROR] failed to apply settings: {e}")
            else:
                self.logger.log(
                    f"[BRIDGE] settings updated: port={self.bridge_port}, "
                    f"token={'set' if self.current_access_token else 'empty'}"
                )

            if self.current_access_token:
                self._copy_text_to_clipboard(self._get_secure_viewer_url())
                self.logger.log("[BRIDGE] secure viewer URL copied")

    def _on_sync_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Sync Settings")

        layout = QVBoxLayout()

        name_label = QLabel("Device Name:")
        layout.addWidget(name_label)

        name_edit = QLineEdit()
        name_edit.setText(self.get_sync_device_name())
        layout.addWidget(name_edit)

        port_label = QLabel("Sync Port:")
        layout.addWidget(port_label)

        port_spin = QSpinBox()
        port_spin.setRange(1, 65535)
        port_spin.setValue(int(getattr(self.sync_manager, "port", 8766)))
        layout.addWidget(port_spin)

        btn_row = QHBoxLayout()

        save_btn = QPushButton("Save")
        cancel_btn = QPushButton("Cancel")

        save_btn.setDefault(True)
        save_btn.setAutoDefault(True)

        btn_row.addStretch()
        btn_row.addWidget(save_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        dialog.setLayout(layout)

        save_btn.clicked.connect(dialog.accept)
        cancel_btn.clicked.connect(dialog.reject)

        if dialog.exec():
            new_name = name_edit.text().strip()
            new_port = port_spin.value()

            if new_name:
                self.settings.setValue("sync/device_name", new_name)

            old_port = getattr(self.sync_manager, "port", 8766)
            old_mode = getattr(self.sync_manager, "mode", "off")

            self.settings.setValue("sync/port", new_port)

            if new_port != old_port:
                self.sync_manager.send_offline()
                self.sync_manager.presence_timer.stop()
                self.sync_manager.stop_listener()
                self.sync_seen_devices.clear()

                self.sync_manager.port = new_port

                if old_mode in ("controller", "follower"):
                    self.sync_manager.set_mode(old_mode)
                    self.sync_manager.start_listener()
                    self.sync_manager.presence_timer.start()
                    self.sync_manager._send_presence()
                else:
                    self.sync_manager.set_mode("off")

                self.logger.log(f"[SYNC] Port changed: {old_port} -> {new_port}")

            if hasattr(self, "connected_devices_dialog") and self.connected_devices_dialog:
                try:
                    self.connected_devices_dialog.refresh_content()
                except Exception:
                    pass

            self._update_sync_status_label()
            self._update_sync_action_states()
            self._update_device_name_display()
            self._update_sync_port_display()

    def _update_token_display(self):
        token = self.current_access_token or "(no token)"
        self.action_current_token.setText(f"Bridge Token: {token}")

    def _update_bridge_port_display(self):
        port = getattr(self, "bridge_port", DEFAULT_BRIDGE_PORT)
        self.action_current_bridge_port.setText(f"Bridge Port: {port}")

    def _update_connected_target_display(self):
        text = getattr(self, "connection_status_text", "")

        if text.startswith("Connected to "):
            host = self.connection_manager.current_host()
            port = self.connection_manager.current_port()

            self.action_current_connected_ip.setText(f"Connected IP: {host}")
            self.action_current_connected_port.setText(f"Connected Port: {port}")

            self.action_current_connected_ip.setVisible(True)
            self.action_current_connected_port.setVisible(True)
        else:
            self.action_current_connected_ip.setVisible(False)
            self.action_current_connected_port.setVisible(False)

    def _update_device_name_display(self):
        self.action_current_device_name.setText(f"Device Name: {self.get_sync_device_name()}")

    def _update_sync_port_display(self):
        if not hasattr(self, "sync_manager") or self.sync_manager is None:
            return

        port = getattr(self.sync_manager, "port", DEFAULT_SYNC_PORT)
        self.action_current_sync_port.setText(f"Sync Port: {port}")

    def _apply_bridge_security_settings(self):
        if hasattr(self, "bridge_server") and self.bridge_server:
            self.bridge_server.local_only = self.action_local_only.isChecked()
            self.bridge_server.require_secure = self.action_enable_secure.isChecked()
            self.bridge_server.access_token = self.current_access_token

            if self.bridge_server.local_only:
                mode = "local"
            elif self.bridge_server.require_secure:
                mode = "secure"
            else:
                mode = "lan"

            self.logger.log(f"[BRIDGE] access mode set to {mode}")

    def _detect_lan_host(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()

            if ip and not ip.startswith("127."):
                return ip
        except Exception:
            pass

        return "127.0.0.1"

    def get_sync_device_name(self):
        name = self.settings.value("sync/device_name", "")
        if name:
            return name
        return socket.gethostname()

    def _get_local_viewer_url(self):
        return f"http://127.0.0.1:{self.bridge_port}/view"

    def _get_lan_viewer_url(self):
        lan_host = self._detect_lan_host()
        return f"http://{lan_host}:{self.bridge_port}/view"

    def _get_secure_viewer_url(self):
        token = self.current_access_token or ""
        return f"{self._get_lan_viewer_url()}?token={token}"

    def _copy_text_to_clipboard(self, text: str):
        QGuiApplication.clipboard().setText(text)
        self.logger.log(f"[SYSTEM] copied to clipboard: {text}")

    def _on_copy_local_viewer_url(self):
        self._copy_text_to_clipboard(self._get_local_viewer_url())

    def _on_copy_lan_viewer_url(self):
        self._copy_text_to_clipboard(self._get_lan_viewer_url())

    def _on_copy_secure_viewer_url(self):
        self._copy_text_to_clipboard(self._get_secure_viewer_url())

    def _on_local_only_toggled(self, checked: bool):
        if checked:
            self.action_enable_lan.blockSignals(True)
            self.action_enable_secure.blockSignals(True)

            self.action_enable_lan.setChecked(False)
            self.action_enable_secure.setChecked(False)

            self.action_enable_lan.blockSignals(False)
            self.action_enable_secure.blockSignals(False)
        else:
            # ❗避免三個都關
            if not self.action_enable_lan.isChecked() and not self.action_enable_secure.isChecked():
                self.action_local_only.blockSignals(True)
                self.action_local_only.setChecked(True)
                self.action_local_only.blockSignals(False)

    def _on_enable_lan_toggled(self, checked: bool):
        if checked:
            self.action_local_only.blockSignals(True)
            self.action_enable_secure.blockSignals(True)

            self.action_local_only.setChecked(False)
            self.action_enable_secure.setChecked(False)

            self.action_local_only.blockSignals(False)
            self.action_enable_secure.blockSignals(False)
        else:
            if not self.action_local_only.isChecked() and not self.action_enable_secure.isChecked():
                self.action_enable_lan.blockSignals(True)
                self.action_enable_lan.setChecked(True)
                self.action_enable_lan.blockSignals(False)

    def _on_enable_secure_toggled(self, checked: bool):
        if checked:
            self.action_local_only.blockSignals(True)
            self.action_enable_lan.blockSignals(True)

            self.action_local_only.setChecked(False)
            self.action_enable_lan.setChecked(False)

            self.action_local_only.blockSignals(False)
            self.action_enable_lan.blockSignals(False)
        else:
            if not self.action_local_only.isChecked() and not self.action_enable_lan.isChecked():
                self.action_enable_secure.blockSignals(True)
                self.action_enable_secure.setChecked(True)
                self.action_enable_secure.blockSignals(False)

    def _on_sync_controller_toggled(self, checked: bool):
        if checked:
            self.sync_follower_action.blockSignals(True)
            self.sync_stop_action.blockSignals(True)

            self.sync_follower_action.setChecked(False)
            self.sync_stop_action.setChecked(False)

            self.sync_follower_action.blockSignals(False)
            self.sync_stop_action.blockSignals(False)

            self.sync_manager.set_mode("controller")
            self.sync_manager.start_listener()
            self.sync_manager.presence_timer.start()
            self.sync_manager._send_presence()
            self._update_sync_status_label()

        else:
            if not self.sync_follower_action.isChecked() and not self.sync_stop_action.isChecked():
                self.sync_controller_action.blockSignals(True)
                self.sync_controller_action.setChecked(True)
                self.sync_controller_action.blockSignals(False)
        self._update_sync_action_states()
        self._refresh_connected_devices_dialog()

    def _on_sync_follower_toggled(self, checked: bool):
        if checked:
            self.sync_controller_action.blockSignals(True)
            self.sync_stop_action.blockSignals(True)

            self.sync_controller_action.setChecked(False)
            self.sync_stop_action.setChecked(False)

            self.sync_controller_action.blockSignals(False)
            self.sync_stop_action.blockSignals(False)

            self.sync_manager.set_mode("follower")
            self.sync_manager.start_listener()
            self.sync_manager.presence_timer.start()
            self.sync_manager._send_presence()
            self._update_sync_status_label()

        else:
            if not self.sync_controller_action.isChecked() and not self.sync_stop_action.isChecked():
                self.sync_follower_action.blockSignals(True)
                self.sync_follower_action.setChecked(True)
                self.sync_follower_action.blockSignals(False)
        self._update_sync_action_states()
        self._refresh_connected_devices_dialog()

    def _on_sync_stop_toggled(self, checked: bool):
        if checked:
            self.sync_controller_action.blockSignals(True)
            self.sync_follower_action.blockSignals(True)

            self.sync_controller_action.setChecked(False)
            self.sync_follower_action.setChecked(False)

            self.sync_controller_action.blockSignals(False)
            self.sync_follower_action.blockSignals(False)

            self.sync_manager.presence_timer.stop()
            self.sync_manager.stop_listener()
            self.sync_manager.set_mode("off")
            self._update_sync_status_label()

        else:
            if not self.sync_controller_action.isChecked() and not self.sync_follower_action.isChecked():
                self.sync_stop_action.blockSignals(True)
                self.sync_stop_action.setChecked(True)
                self.sync_stop_action.blockSignals(False)
        self._update_sync_action_states()
        self._refresh_connected_devices_dialog()

    def _update_sync_action_states(self):
        is_controller = self.sync_controller_action.isChecked()
        is_follower = self.sync_follower_action.isChecked()

        self.sync_take_control_action.setEnabled(is_follower)
        self.sync_allow_local_override_action.setEnabled(is_follower)
        self.sync_allow_incoming_override_action.setEnabled(is_controller)

    def _is_local_sync_action_blocked(self, from_sync=False):
        if from_sync:
            return False

        return (
                self.sync_follower_action.isChecked()
                and not self.sync_allow_local_override_action.isChecked()
        )

    def _on_sync_take_control_triggered(self):
        if not self.sync_follower_action.isChecked():
            return

        self.sync_controller_action.setChecked(True)

        self.sync_manager.broadcast({
            "action": "take_control"
        })

    def _apply_remote_take_control(self, msg: dict):
        sender = msg.get("sender")

        if sender == self.sync_manager.client_id:
            return

        # 不管目前畫面狀態如何，只要收到別台 take control，
        # 這台就明確切成 follower
        if not self.sync_follower_action.isChecked():
            self.sync_follower_action.setChecked(True)

        self._update_sync_status_label()
        self._update_sync_action_states()

        if self.connected_devices_dialog is not None and self.connected_devices_dialog.isVisible():
            self.connected_devices_dialog.refresh_content()

    def _generate_random_token(self):
        self.current_access_token = secrets.token_hex(3)
        self.settings.setValue("access_token", self.current_access_token)
        self.logger.log("[BRIDGE] token regenerated")
        self._update_token_display()

    def refresh_status_bar(self):
        left_text = f"{self.viewer_source_text}｜{self.viewer_type_text}｜{self.viewer_content_text}"
        self.viewer_status_label.setText(left_text)

    def _set_viewer_status(self, source: str, kind: str, content: str):
        self.viewer_source_text = source
        self.viewer_type_text = kind
        self.viewer_content_text = content
        self.refresh_status_bar()

    def _update_sync_status_label(self):
        if self.sync_controller_action.isChecked():
            text = "SYNC: CONTROLLER"
            color = "#4CAF50"
        elif self.sync_follower_action.isChecked():
            text = "SYNC: FOLLOWER"
            color = "#2196F3"
        else:
            text = "SYNC: OFF"
            color = "#888888"

        self.sync_status_label.setText(text)
        self.sync_status_label.setStyleSheet(f"color: {color};")

    def _remember_sync_source(self, msg: dict):
        sender = str(msg.get("sender", "")).strip()
        if not sender:
            return

        source_ip = str(msg.get("_source_ip", "Unknown"))
        source_port = msg.get("_source_port", "")
        action = str(msg.get("action", ""))
        device_name = str(msg.get("device_name", "")).strip()
        mode = str(msg.get("mode", "")).strip()
        now = time.time()

        if not device_name:
            device_name = source_ip

        if not mode:
            mode = "unknown"

        previous = self.sync_seen_devices.get(sender, {})

        info = {
            "name": device_name,
            "ip": source_ip,
            "port": source_port,
            "mode": mode,
            "last_seen_ts": now,
            "last_action": previous.get("last_action", ""),
            "last_action_ts": previous.get("last_action_ts", 0),
        }

        if action != "presence":
            info["last_action"] = action
            info["last_action_ts"] = now

        self.sync_seen_devices[sender] = info

        if self.connected_devices_dialog is not None and self.connected_devices_dialog.isVisible():
            self.connected_devices_dialog.refresh_content()

    def _remove_sync_source(self, msg: dict):
        sender = str(msg.get("sender", "")).strip()
        if not sender:
            return

        self.sync_seen_devices.pop(sender, None)

        if self.connected_devices_dialog is not None and self.connected_devices_dialog.isVisible():
            self.connected_devices_dialog.refresh_content()

    def update_connection_status(self, text: str):
        self.connection_status_text = text
        self._update_connected_target_display()
        self.refresh_status_bar()

    def set_display_state(
            self,
            mode: str,
            label: str,
            rectangles=None,
            solid_rgb=None,
            image_path=None,
       ):
        # ===== Resolve viewer source =====
        source = VIEWER_SOURCE_REMOTE if getattr(self, "_display_from_sync", False) else VIEWER_SOURCE_MANUAL
        self.viewer_source_text = source

        if rectangles is None:
            rectangles = []

        self.current_display_state = {
            "mode": mode,
            "label": label,
            "rectangles": list(rectangles),
            "solid_rgb": solid_rgb,
            "image_path": image_path,
        }

        # ===== Update status bar =====
        if mode == "external":
            if self.current_display_rects:
                r, g, b, *_ = self.current_display_rects[0]

                viewer_type = (
                    VIEWER_TYPE_COLOURSPACE_RX
                    if self.active_source == "colourspace"
                    else VIEWER_TYPE_DISPLAYCAL_RX
                )

                self._set_viewer_status(
                    self.viewer_source_text,
                    viewer_type,
                    format_rgb(r, g, b)
                )
            else:
                viewer_type = (
                    VIEWER_TYPE_COLOURSPACE_RX
                    if self.active_source == "colourspace"
                    else VIEWER_TYPE_DISPLAYCAL_RX
                )

                self._set_viewer_status(
                    self.viewer_source_text,
                    viewer_type,
                    VIEWER_CONTENT_WAITING
                )


        elif mode == "image":

            if isinstance(image_path, (str, Path)):

                name = Path(image_path).name

            else:

                name = "(unknown)"
            self._set_viewer_status(self.viewer_source_text, VIEWER_TYPE_PATTERN, name)


        elif mode == "solid":

            if isinstance(solid_rgb, (list, tuple)) and len(solid_rgb) == 3:
                r, g, b = solid_rgb

                self._set_viewer_status(self.viewer_source_text, VIEWER_TYPE_COLOR, format_rgb(r, g, b))

        # === mirror to all viewers ===
        for window in self.viewer_windows.values():
            self.apply_state_to_viewer(window.viewer)

    def apply_state_to_viewer(self, viewer_widget):
        state = self.current_display_state

        if state["mode"] == "solid" and state["solid_rgb"]:
            if hasattr(viewer_widget, "set_rectangles"):
                viewer_widget.set_rectangles([])

            viewer_widget.clear()
            viewer_widget._source_pixmap = None
            r, g, b = state["solid_rgb"]
            viewer_widget.setStyleSheet(f"background-color: rgb({r}, {g}, {b});")

        elif state["mode"] == "image":
            if hasattr(viewer_widget, "set_rectangles"):
                viewer_widget.set_rectangles([])

            viewer_widget.setStyleSheet("background-color: black;")

            image_path = state.get("image_path")
            if image_path:
                pixmap = QPixmap(str(image_path))
                if not pixmap.isNull():
                    self._set_viewer_pixmap_scaled(viewer_widget, pixmap)
                else:
                    viewer_widget.clear()
                    viewer_widget._source_pixmap = None
            else:
                viewer_widget.clear()
                viewer_widget._source_pixmap = None

        elif state["mode"] == "external":
            viewer_widget.clear()
            viewer_widget._source_pixmap = None
            viewer_widget.setStyleSheet("background-color: black;")

            rectangles = state.get("rectangles") or []
            if hasattr(viewer_widget, "set_rectangles"):
                viewer_widget.set_rectangles(rectangles)

        else:
            if hasattr(viewer_widget, "set_rectangles"):
                viewer_widget.set_rectangles([])

            viewer_widget.clear()
            viewer_widget._source_pixmap = None
            viewer_widget.setStyleSheet("background-color: black;")

    def get_display_state_snapshot(self):
        state = self.current_display_state
        image_path = state["image_path"]

        web_viewer_source_text = WEB_VIEWER_SOURCE_MAP.get(
            self.viewer_source_text,
            self.viewer_source_text
        )

        return {
            "mode": state["mode"],
            "label": state["label"],
            "rectangles": list(state["rectangles"]),
            "solid_rgb": state["solid_rgb"],
            "image_path": str(image_path) if image_path is not None else None,
            "viewer_source_text": self.viewer_source_text,
            "viewer_type_text": self.viewer_type_text,
            "viewer_content_text": self.viewer_content_text,
            "web_viewer_source_text": web_viewer_source_text,
        }

    def update_patch_info(self, text: str):
        if self.active_source != "colourspace":
            return

        self.latest_external_label = text

        if text == "No patch":
            self.latest_external_rectangles = []

            if self.patch_widget.display_mode == "external":
                self.patch_widget._pending_rectangles = None
                self.patch_widget.rectangles = []
                self.patch_widget.update()

                self.set_display_state(
                    mode="external",
                    label="No patch",
                    rectangles=[],
                    solid_rgb=None,
                    image_path=None,
                )

        if self.patch_widget.display_mode == "external":
            self.patch_info_text = text
            self.refresh_status_bar()

    def handle_external_rectangles_changed(self, rects):
        if self.active_source != "colourspace":
            return

        label = format_patch_info(rects)

        self.latest_external_rectangles = list(rects)
        self.latest_external_label = label

        if self.patch_widget.display_mode == "external":
            # 直接在 UI thread 同步更新 patch widget，避免比 web 慢一拍
            self.patch_widget._pending_rectangles = None
            self.patch_widget.rectangles = list(rects)
            self.patch_widget.update()

            self.set_display_state(
                mode="external",
                label=label,
                rectangles=rects,
                solid_rgb=None,
                image_path=None,
            )

    def toggle_status_bar(self, checked: bool):
        self.settings.setValue(SETTINGS_KEY_SHOW_STATUS_BAR, checked)

        if checked:
            self.statusBar().show()
        else:
            self.statusBar().hide()

    def toggle_status_bar_shortcut(self):
        checked = not self.statusBar().isVisible()
        self.toggle_statusbar_action.setChecked(checked)
        self.toggle_status_bar(checked)

    def position_custom_color_panel(self):
        if not hasattr(self, "custom_color_panel"):
            return

        margin = 16
        lift = 16  # ← 控制往上移多少（這個數字你可以調）

        panel = self.custom_color_panel
        panel.adjustSize()

        x = self.width() - panel.width() - margin
        y = self.height() - panel.height() - margin - lift

        panel.move(x, y)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.position_custom_color_panel()

    def open_settings_dialog(self):
        dc_host = self.settings.value(SETTINGS_KEY_DISPLAYCAL_HOST, DEFAULT_DISPLAYCAL_HOST)
        dc_port_value = self.settings.value(SETTINGS_KEY_DISPLAYCAL_PORT, DEFAULT_DISPLAYCAL_PORT)

        try:
            dc_port = int(dc_port_value)
        except (TypeError, ValueError):
            dc_port = 20002

        dialog = SettingsDialog(
            host=self.connection_manager.current_host(),
            port=self.connection_manager.current_port(),
            dc_host=str(dc_host),
            dc_port=dc_port,
            parent=self,
        )

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings.setValue(SETTINGS_KEY_HOST, dialog.host)
            self.settings.setValue(SETTINGS_KEY_PORT, dialog.port)
            self.settings.setValue(SETTINGS_KEY_DISPLAYCAL_HOST, dialog.dc_host)
            self.settings.setValue(SETTINGS_KEY_DISPLAYCAL_PORT, dialog.dc_port)

            self.logger.log(f"[CONNECTION] settings saved: {dialog.host}:{dialog.port}")
            self.logger.log(f"[DISPLAYCAL] settings saved: {dialog.dc_host}:{dialog.dc_port}")

            self.connection_manager.configure(dialog.host, dialog.port, reconnect_now=True)
            self.displaycal_connection_manager.connect_to(dialog.dc_host, dialog.dc_port)

    def set_test_pattern_solid(self, r, g, b, label, from_sync=False):
        if self._is_local_sync_action_blocked(from_sync):
            return

        self.current_image_pattern_path = None
        # [新增] 控制右下角 RGB 面板顯示/隱藏
        if "Custom RGB" in label:
            self.custom_color_panel.show()
            self.position_custom_color_panel()
        else:
            self.custom_color_panel.hide()
            self.patch_widget.setFocus()

        # ===== 原本邏輯（保持不變）=====
        self.patch_widget.set_internal_pattern(("solid", (r, g, b)))
        self._display_from_sync = from_sync
        self.set_display_state(
            mode="solid",
            label=label,
            solid_rgb=(r, g, b),
        )
        self.patch_info_text = f"Internal Pattern | {label}"
        self.refresh_status_bar()
        self.rebuild_image_test_pattern_menu()
        self.rebuild_user_pattern_menu()
        self._queue_solid_patch_log(label)
        self.update()

        if not from_sync:
            self.sync_manager.broadcast({
                "action": "solid",
                "rgb": [r, g, b],
                "label": label,
            })

    def _queue_solid_patch_log(self, label: str):
        self._pending_solid_patch_log_label = label
        self._solid_patch_log_timer.start()

    def _flush_solid_patch_log(self):
        if self._pending_solid_patch_log_label:
            self.logger.log(f"[PATCH] switched to local custom color: {self._pending_solid_patch_log_label}")
            self._pending_solid_patch_log_label = None

    def handle_custom_color_changed(self):
        if not self.custom_color_panel.isVisible():
            return

        r, g, b = self.custom_color_panel.rgb()

        self.settings.setValue(SETTINGS_KEY_CUSTOM_R, r)
        self.settings.setValue(SETTINGS_KEY_CUSTOM_G, g)
        self.settings.setValue(SETTINGS_KEY_CUSTOM_B, b)

        self.set_test_pattern_solid(r, g, b, f"Custom RGB=({r}, {g}, {b})")

    def show_pattern_custom_color(self):
        self.custom_color_panel.show()
        self.position_custom_color_panel()
        self.handle_custom_color_changed()
        self.update_rgb_focus()
        self.update()

    def load_custom_swatches(self):
        raw = self.settings.value(SETTINGS_KEY_SWATCHES, "")
        user_count = 24 - len(self.custom_color_panel.fixed_swatches)

        if not raw:
            return [None] * user_count

        if not isinstance(raw, str):
            raw = str(raw)

        result: list[tuple[int, int, int] | None] = []
        entries = raw.split(";")

        for item in entries[:user_count]:
            if not item:
                result.append(None)
                continue

            parts = item.split(",")
            if len(parts) != 3:
                result.append(None)
                continue

            try:
                r = int(parts[0])
                g = int(parts[1])
                b = int(parts[2])
                result.append((r, g, b))
            except ValueError:
                result.append(None)

        while len(result) < user_count:
            result.append(None)

        return result

    def save_custom_swatches(self):
        values = []
        for item in self.custom_color_panel.export_user_swatches():
            if item is None:
                values.append("")
            else:
                r, g, b = item
                values.append(f"{r},{g},{b}")

        raw = ";".join(values)
        self.settings.setValue(SETTINGS_KEY_SWATCHES, raw)

    def get_test_pattern_files(self):
        if not TEST_PATTERN_DIR.exists():
            return []

        files = []
        for path in sorted(TEST_PATTERN_DIR.iterdir()):
            if path.name.startswith("."): continue
            if path.is_file() and path.suffix.lower() in TEST_PATTERN_EXTENSIONS:
                files.append(path)

        return files

    def select_test_pattern_by_index(self, index: int):
        patterns = self.get_test_pattern_files()

        if 0 <= index < len(patterns):
            self.show_image_test_pattern(patterns[index])

    def select_user_pattern_by_index(self, index: int):
        patterns = self.get_user_pattern_files()

        if 0 <= index < len(patterns):
            self.show_image_test_pattern(patterns[index])

    def select_prev_rgb_channel(self):
        if not self.custom_color_panel.isVisible():
            return

        self.rgb_selected_channel = (self.rgb_selected_channel - 1) % 3
        self.update_rgb_focus()

    def select_next_rgb_channel(self):
        if not self.custom_color_panel.isVisible():
            return

        self.rgb_selected_channel = (self.rgb_selected_channel + 1) % 3
        self.update_rgb_focus()

    def update_rgb_focus(self):

        if self.rgb_selected_channel == 0:
            widget = self.custom_color_panel.r_spin
        elif self.rgb_selected_channel == 1:
            widget = self.custom_color_panel.g_spin
        else:
            widget = self.custom_color_panel.b_spin

        widget.setFocus()
        widget.selectAll()
        self.rgb_replace_on_next_digit = True

    def get_rgb_spin_and_line_edit(self, obj):
        if obj == self.custom_color_panel.r_spin or obj == self.custom_color_panel.r_spin.lineEdit():
            spin = self.custom_color_panel.r_spin
        elif obj == self.custom_color_panel.g_spin or obj == self.custom_color_panel.g_spin.lineEdit():
            spin = self.custom_color_panel.g_spin
        else:
            spin = self.custom_color_panel.b_spin

        return spin, spin.lineEdit()

    def apply_rgb_spin_value(self, spin, line_edit, value, new_cursor):
        spin.setValue(value)
        line_edit = spin.lineEdit()
        line_edit.deselect()
        line_edit.setCursorPosition(min(new_cursor, len(line_edit.text())))

    def handle_rgb_digit_input(self, spin, line_edit, digit: str):
        full_text = line_edit.text()
        selected_text = line_edit.selectedText()

        if self.rgb_replace_on_next_digit:
            new_text = digit
            new_cursor = 1
            self.rgb_replace_on_next_digit = False

        elif selected_text:
            start = line_edit.selectionStart()
            end = start + len(selected_text)
            new_text = full_text[:start] + digit + full_text[end:]
            new_cursor = start + 1

        else:
            cursor_pos = line_edit.cursorPosition()
            new_text = full_text[:cursor_pos] + digit + full_text[cursor_pos:]
            new_cursor = cursor_pos + 1

        if len(new_text) > 3:
            value = int(digit)
            new_cursor = 1
        else:
            value = int(new_text)
            if value > 255:
                value = 255
                new_cursor = len(str(value))

        self.apply_rgb_spin_value(spin, line_edit, value, new_cursor)

    def handle_rgb_delete_input(self, spin, line_edit, key):
        full_text = line_edit.text()
        selected_text = line_edit.selectedText()
        cursor_pos = line_edit.cursorPosition()

        if selected_text:
            start = line_edit.selectionStart()
            end = start + len(selected_text)
            new_text = full_text[:start] + full_text[end:]
            new_cursor = start

        elif key == Qt.Key.Key_Backspace:
            if cursor_pos > 0:
                new_text = full_text[:cursor_pos - 1] + full_text[cursor_pos:]
                new_cursor = cursor_pos - 1
            else:
                new_text = full_text
                new_cursor = cursor_pos

        else:  # Delete
            if cursor_pos < len(full_text):
                new_text = full_text[:cursor_pos] + full_text[cursor_pos + 1:]
                new_cursor = cursor_pos
            else:
                new_text = full_text
                new_cursor = cursor_pos

        if new_text == "":
            value = 0
            new_cursor = 1
        else:
            value = int(new_text)
            if value > 255:
                value = 255
                new_cursor = len(str(value))

        self.apply_rgㄑb_spin_value(spin, line_edit, value, new_cursor)
        self.rgb_replace_on_next_digit = False

    def eventFilter(self, obj, event):
        app = QApplication.instance()

        if self.shortcuts_dialog is not None:
            if (
                    obj is self.shortcuts_dialog
                    and event.type() in (
                            QEvent.Type.PaletteChange,
                            QEvent.Type.StyleChange,
                    )
            ) or (
                    obj is app
                    and event.type() == QEvent.Type.ApplicationPaletteChange
            ):
                self._apply_shortcuts_dialog_theme()

        if event.type() == event.Type.KeyPress:
            key = event.key()

            # 全域 Enter：不在 RGB 欄位時，回到目前欄位
            if self.custom_color_panel.isVisible() and key in (
                    Qt.Key.Key_Return,
                    Qt.Key.Key_Enter,
            ):
                if QApplication.focusWidget() not in self.get_rgb_widgets():
                    self.update_rgb_focus()
                    return True

        if obj in self.get_rgb_widgets() and event.type() == event.Type.KeyPress:
            spin, line_edit = self.get_rgb_spin_and_line_edit(obj)
            key = event.key()
            modifiers = event.modifiers()

            is_cmd_or_ctrl = bool(
                modifiers & Qt.KeyboardModifier.ControlModifier
                or modifiers & Qt.KeyboardModifier.MetaModifier
            )

            if self.custom_color_panel.isVisible() and Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
                digit = str(key - Qt.Key.Key_0)
                self.handle_rgb_digit_input(spin, line_edit, digit)
                return True

            if self.custom_color_panel.isVisible() and key in (
                    Qt.Key.Key_Backspace,
                    Qt.Key.Key_Delete,
            ):
                self.handle_rgb_delete_input(spin, line_edit, key)
                return True

            if self.custom_color_panel.isVisible() and is_cmd_or_ctrl:
                if key in (Qt.Key.Key_Left, Qt.Key.Key_Up):
                    self.select_prev_rgb_channel()
                    return True

                if key in (Qt.Key.Key_Right, Qt.Key.Key_Down):
                    self.select_next_rgb_channel()
                    return True

            if self.custom_color_panel.isVisible() and key in (
                    Qt.Key.Key_Return,
                    Qt.Key.Key_Enter,
            ):
                self.select_next_rgb_channel()
                return True

        return super().eventFilter(obj, event)

    def get_rgb_widgets(self):
        return (
            self.custom_color_panel.r_spin,
            self.custom_color_panel.g_spin,
            self.custom_color_panel.b_spin,
            self.custom_color_panel.r_spin.lineEdit(),
            self.custom_color_panel.g_spin.lineEdit(),
            self.custom_color_panel.b_spin.lineEdit(),
        )

    def get_current_pattern_group(self):
        current = self.current_image_pattern_path
        if current is None:
            return None

        test_patterns = self.get_test_pattern_files()
        if current in test_patterns:
            return test_patterns

        user_patterns = self.get_user_pattern_files()
        if current in user_patterns:
            return user_patterns

        return None

    def select_previous_pattern(self):
        group = self.get_current_pattern_group()
        current = self.current_image_pattern_path

        if not group or current is None:
            return

        try:
            index = group.index(current)
        except ValueError:
            return

        previous_index = (index - 1) % len(group)
        self.show_image_test_pattern(group[previous_index])

    def select_next_pattern(self):
        group = self.get_current_pattern_group()
        current = self.current_image_pattern_path

        if not group or current is None:
            return

        try:
            index = group.index(current)
        except ValueError:
            return

        next_index = (index + 1) % len(group)
        self.show_image_test_pattern(group[next_index])

    def handle_ctrl_left(self):
        if self.custom_color_panel.isVisible():
            focus_widget = QApplication.focusWidget()
            rgb_widgets = self.get_rgb_widgets()

            if focus_widget not in rgb_widgets:
                self.select_prev_rgb_channel()
                return

        self.select_previous_pattern()

    def handle_ctrl_right(self):
        if self.custom_color_panel.isVisible():
            focus_widget = QApplication.focusWidget()
            rgb_widgets = self.get_rgb_widgets()

            if focus_widget not in rgb_widgets:
                self.select_next_rgb_channel()
                return

        self.select_next_pattern()

    def make_test_pattern_label(self, path: Path):
        return path.name

    def make_test_pattern_full_label(self, path: Path):
        return path.name

    def show_image_test_pattern(self, path: Path, from_sync=False):
        if self._is_local_sync_action_blocked(from_sync):
            return

        image = QImage(str(path))
        if image.isNull():
            QMessageBox.warning(
                self,
                "Image Load Failed",
                f"無法載入圖卡：\n{path}"
            )
            return

        self.current_image_pattern_path = path

        self.custom_color_panel.hide()

        menu_label = self.make_test_pattern_label(path)
        full_label = self.make_test_pattern_full_label(path)

        self.patch_widget.set_internal_image(image, menu_label)
        self._display_from_sync = from_sync
        self.set_display_state(
            mode="image",
            label=menu_label,
            image_path=path,
        )
        self.patch_info_text = f"Internal Pattern | {full_label}"
        self.refresh_status_bar()
        self.rebuild_image_test_pattern_menu()
        self.rebuild_user_pattern_menu()
        self.logger.log(f"[PATCH] switched to test pattern: {full_label}")

        if not from_sync:
            try:
                relative_name = str(path.relative_to(TEST_PATTERN_DIR))
            except ValueError:
                relative_name = path.name

            self.sync_manager.broadcast({
                "action": "test_pattern",
                "name": relative_name,
            })

    def rebuild_image_test_pattern_menu(self):
        self.test_patterns_menu.clear()

        files = self.get_test_pattern_files()

        if not files:
            empty_action = QAction("No image patterns found", self)
            empty_action.setEnabled(False)
            self.test_patterns_menu.addAction(empty_action)
            return

        current_path = self.current_image_pattern_path

        for idx, path in enumerate(files):
            label = self.make_test_pattern_label(path)

            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(current_path == path)

            if idx < 9:
                action.setShortcut(QKeySequence(f"Ctrl+{idx + 1}"))
                action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)

            action.triggered.connect(partial(self.show_image_test_pattern, path))
            self.test_patterns_menu.addAction(action)

    def get_user_pattern_dir(self):
        raw = self.settings.value(SETTINGS_KEY_USER_PATTERN_DIR, "")
        if not raw:
            return None

        path = Path(str(raw))
        if path.exists() and path.is_dir():
            return path

        return None

    def get_user_pattern_files(self):
        user_dir = self.get_user_pattern_dir()
        if user_dir is None:
            return []

        files = []
        for path in sorted(user_dir.rglob("*")):
            if path.name.startswith("."): continue
            if path.is_file() and path.suffix.lower() in TEST_PATTERN_EXTENSIONS:
                files.append(path)

        return files

    def set_user_pattern_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select User Pattern Folder"
        )
        if not folder:
            return

        self.settings.setValue(SETTINGS_KEY_USER_PATTERN_DIR, folder)
        self.rebuild_user_pattern_menu()
        self.logger.log(f"[PATTERN] user patterns folder set: {folder}")

    def clear_user_pattern_folder(self):
        current_user_dir = self.get_user_pattern_dir()
        current_path = self.current_image_pattern_path

        self.settings.remove(SETTINGS_KEY_USER_PATTERN_DIR)
        self.rebuild_user_pattern_menu()
        self.logger.log("[PATTERN] user patterns folder cleared")

        if (
                current_user_dir is not None
                and current_path is not None
        ):
            try:
                current_path.relative_to(current_user_dir)
                self.set_test_pattern_solid(0, 0, 0, "Black")
            except ValueError:
                pass

    def rebuild_user_pattern_menu(self):
        self.user_patterns_menu.clear()

        self.user_pattern_set_folder_action = QAction("Set Folder…", self)
        self.user_pattern_set_folder_action.triggered.connect(self.set_user_pattern_folder)
        self.user_patterns_menu.addAction(self.user_pattern_set_folder_action)

        self.user_pattern_clear_folder_action = QAction("Reset Folder", self)
        self.user_pattern_clear_folder_action.triggered.connect(self.clear_user_pattern_folder)
        self.user_pattern_clear_folder_action.setEnabled(self.get_user_pattern_dir() is not None)
        self.user_patterns_menu.addAction(self.user_pattern_clear_folder_action)

        self.user_patterns_menu.addSeparator()

        files = self.get_user_pattern_files()

        if not files:
            empty_action = QAction("No user patterns found", self)
            empty_action.setEnabled(False)
            self.user_patterns_menu.addAction(empty_action)
            return

        current_path = self.current_image_pattern_path

        for idx, path in enumerate(files):
            relative_name = str(path.relative_to(self.get_user_pattern_dir()))

            action = QAction(relative_name, self)
            action.setCheckable(True)
            action.setChecked(current_path == path)

            if idx < 9:
                action.setShortcut(QKeySequence(f"Ctrl+Shift+{idx + 1}"))
                action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)

            action.triggered.connect(partial(self.show_image_test_pattern, path))
            self.user_patterns_menu.addAction(action)

    def return_to_colourspace(self, from_sync=False):
        if self._is_local_sync_action_blocked(from_sync):
            return

        self.active_source = "colourspace"
        self.viewer_type_text = VIEWER_TYPE_COLOURSPACE_RX

        if self.latest_colourspace_rects:
            self.current_display_rects = self.latest_colourspace_rects
            self.patch_widget.set_rectangles_threadsafe(self.latest_colourspace_rects)
            self.refresh_status_bar()

        self.custom_color_panel.hide()
        self.patch_widget.set_external_mode()
        self.patch_widget.setFocus()

        self._display_from_sync = from_sync
        self.set_display_state(
            mode="external",
            label="No patch",
            rectangles=self.latest_external_rectangles if self.latest_external_rectangles else [],
        )

        self.patch_info_text = "No patch"
        self.refresh_status_bar()
        self.rebuild_image_test_pattern_menu()
        self.rebuild_user_pattern_menu()
        self.logger.log("[PATCH] switched to ColourSpace external patch mode")
        self.update()

        if not from_sync:
            self.sync_manager.broadcast({
                "action": "return"
            })

    def return_to_displaycal(self):
        self.active_source = "displaycal"
        self.viewer_type_text = VIEWER_TYPE_DISPLAYCAL_RX

        if self.latest_displaycal_rects:
            self.current_display_rects = self.latest_displaycal_rects
            self.patch_widget.set_rectangles_threadsafe(self.latest_displaycal_rects)
            self._set_viewer_status(
                self.viewer_source_text,
                VIEWER_TYPE_DISPLAYCAL_RX,
                format_rgb(*self.latest_displaycal_rects[0][:3]),
            )

        else:
            self.current_display_rects = []
            self.patch_widget.set_rectangles_threadsafe([])

            self.current_display_state = {
                "mode": "external",
                "label": "DisplayCAL Rx",
                "rectangles": [],
                "solid_rgb": None,
                "image_path": None,
            }

            self._set_viewer_status(
                self.viewer_source_text,
                VIEWER_TYPE_DISPLAYCAL_RX,
                VIEWER_CONTENT_WAITING,
            )

            for window in self.viewer_windows.values():
                self.apply_state_to_viewer(window.viewer)


        dc_host = self.settings.value(SETTINGS_KEY_DISPLAYCAL_HOST, DEFAULT_DISPLAYCAL_HOST)
        dc_port_value = self.settings.value(SETTINGS_KEY_DISPLAYCAL_PORT, DEFAULT_DISPLAYCAL_PORT)

        try:
            dc_port = int(dc_port_value)
        except (TypeError, ValueError):
            dc_port = DEFAULT_DISPLAYCAL_PORT

        self.custom_color_panel.hide()
        self.patch_widget.set_external_mode()
        self.patch_widget.setFocus()

        self.displaycal_connection_manager.connect_to(str(dc_host), dc_port)

        self.patch_info_text = "DisplayCAL Rx"
        self.rebuild_image_test_pattern_menu()
        self.rebuild_user_pattern_menu()
        self.logger.log(f"[PATCH] switched to DisplayCAL external patch mode: {dc_host}:{dc_port}")
        self.update()

    def handle_reconnect(self):
        self.connection_manager.reconnect()

    def handle_disconnect(self):
        self.connection_manager.disconnect()

    def toggle_file_log(self, checked: bool):
        self.logger.enable_file_log = checked
        self.logger.log(f"[LOG] file logging {'enabled' if checked else 'disabled'}")

        if checked and not LOG_FILE.exists():
            try:
                LOG_FILE.touch(exist_ok=True)
            except Exception:
                pass

        if self.log_dialog is not None:
            self.log_dialog.refresh_log()

    def open_log_dialog(self):
        if self.log_dialog is None:
            self.log_dialog = LogDialog(logger=self.logger, parent=self)

        self.log_dialog.show()
        self.log_dialog.raise_()
        self.log_dialog.activateWindow()
        self.log_dialog.refresh_log()

    def open_connected_devices_dialog(self):
        if self.connected_devices_dialog is None:
            self.connected_devices_dialog = ConnectedDevicesDialog(self, self)

        if self.connected_devices_dialog.isVisible():
            self.connected_devices_dialog.hide()
            return

        self.connected_devices_dialog.refresh_content()
        self.connected_devices_dialog.show()
        self.connected_devices_dialog.raise_()
        self.connected_devices_dialog.activateWindow()

    def _refresh_connected_devices_dialog(self):
        if self.connected_devices_dialog is not None and self.connected_devices_dialog.isVisible():
            self.connected_devices_dialog.refresh_content()

    def closeEvent(self, event):
        confirmed = self._confirm_dialog(
            "Quit",
            "Do you want to close all viewers and quit the App?",
            confirm_text="Quit",
            cancel_text="Cancel",
        )

        if not confirmed:
            event.ignore()
            return

        self.logger.log("[SYSTEM] MainWindow closed")

        self.close_all_viewers()
        self.connection_manager.stop()

        if hasattr(self, "bridge_server"):
            self.bridge_server.stop()

            if hasattr(self.sync_manager, "presence_timer") and self.sync_manager.presence_timer:
                self.sync_manager.presence_timer.stop()

            self.sync_manager.stop_listener()

        QApplication.instance().quit()
        super().closeEvent(event)

    def _apply_shortcuts_dialog_theme(self):
        dialog = self.shortcuts_dialog
        if dialog is None:
            return

        palette = dialog.palette()
        text_color = palette.windowText().color()

        def rgba(c, a):
            return f"rgba({c.red()}, {c.green()}, {c.blue()}, {a})"

        is_dark = text_color.lightness() > 128

        section_color = rgba(text_color, 0.30)
        title_color = rgba(text_color, 0.95)
        shortcut_text_color = rgba(text_color, 0.75)

        if is_dark:
            shortcut_bg = "rgba(255,255,255,0.08)"
        else:
            shortcut_bg = "rgba(0,0,0,0.08)"

        for label in getattr(dialog, "_section_labels", []):
            label.setStyleSheet(f"""
                font-size: 12px;
                font-weight: 700;
                color: {section_color};
                letter-spacing: 0.5px;
            """)

        for label in getattr(dialog, "_title_labels", []):
            label.setStyleSheet(f"""
                font-size: 14px;
                color: {title_color};
            """)

        for label in getattr(dialog, "_shortcut_labels", []):
            label.setStyleSheet(f"""
                font-size: 12px;
                font-weight: 400;
                color: {shortcut_text_color};
                background: {shortcut_bg};
                padding: 3px 8px;
                border-radius: 6px;
            """)

    def show_shortcuts_dialog(self):
        if self.shortcuts_dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle("Keyboard Shortcuts")
            dialog.resize(520, dialog.sizeHint().height())
            dialog.setMinimumWidth(520)
            dialog.installEventFilter(self)

            dialog._section_labels = []
            dialog._title_labels = []
            dialog._shortcut_labels = []

            layout = QVBoxLayout()
            layout.setContentsMargins(20, 18, 20, 14)
            layout.setSpacing(10)

            first_section = True

            for title, shortcut in SHORTCUT_HELP:
                if shortcut == "":
                    if not first_section:
                        layout.addSpacing(10)
                    first_section = False

                    section_label = QLabel(title)
                    dialog._section_labels.append(section_label)
                    layout.addWidget(section_label)
                    continue

                row = QHBoxLayout()
                row.setContentsMargins(0, 2, 0, 2)
                row.setSpacing(16)

                title_label = QLabel(title)
                title_label.setWordWrap(True)
                dialog._title_labels.append(title_label)

                shortcut_label = QLabel(shortcut)
                shortcut_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                shortcut_label.setMinimumHeight(32)
                dialog._shortcut_labels.append(shortcut_label)

                row.addWidget(title_label, 1)
                row.addWidget(shortcut_label, 0)

                layout.addLayout(row)

            layout.addSpacing(10)

            close_btn = QPushButton("Close")
            close_btn.setDefault(True)
            close_btn.setAutoDefault(True)
            close_btn.clicked.connect(dialog.hide)

            btn_row = QHBoxLayout()
            btn_row.setSpacing(12)
            btn_row.addStretch()
            btn_row.addWidget(close_btn)
            btn_row.addStretch()

            layout.addLayout(btn_row)

            dialog.setLayout(layout)
            self.shortcuts_dialog = dialog

            self._apply_shortcuts_dialog_theme()

        else:
            self._apply_shortcuts_dialog_theme()

        if self.shortcuts_dialog.isVisible():
            self.shortcuts_dialog.hide()
        else:
            self._apply_shortcuts_dialog_theme()
            self.shortcuts_dialog.show()
            self.shortcuts_dialog.raise_()
            self.shortcuts_dialog.activateWindow()

    def show_about_dialog(self):
        if getattr(self, "about_dialog", None) is None:
            dialog = QDialog(self)
            dialog.setWindowTitle("About")
            dialog.setMinimumWidth(360)

            main_layout = QVBoxLayout()
            main_layout.setContentsMargins(20, 16, 20, 16)
            main_layout.setSpacing(12)

            # ===== 上半部（icon + 文字）=====
            top_layout = QHBoxLayout()
            top_layout.setSpacing(16)

            # icon（左）
            icon_label = QLabel()

            icon_path = BASE_DIR / "assets" / "icon.ico"
            if icon_path.exists():
                icon_pixmap = QPixmap(str(BASE_DIR / "assets" / "icon-about.png"))
                icon_label.setPixmap(icon_pixmap)

            top_layout.addWidget(icon_label, alignment=Qt.AlignmentFlag.AlignTop)

            # 文字（右）
            text_layout = QVBoxLayout()
            text_layout.setSpacing(6)

            title = QLabel("ColourSpace Patch Rx")
            title.setStyleSheet("font-size:16px; font-weight:600;")

            version = QLabel(f"Ver. {APP_VERSION}")

            desc = QLabel(
                "© 2026 WhARTS Ltd.<br>"
                "Compatible with ColourSpace by Light Illusion"
            )
            desc.setTextFormat(Qt.TextFormat.RichText)
            desc.setStyleSheet("color:#888;")

            text_layout.addWidget(title)
            text_layout.addWidget(version)
            text_layout.addWidget(desc)

            top_layout.addLayout(text_layout)
            main_layout.addLayout(top_layout)

            # ===== OK 按鈕（真正置中）=====
            ok_btn = QPushButton("OK")
            ok_btn.setFixedWidth(100)
            ok_btn.clicked.connect(dialog.accept)

            main_layout.addWidget(ok_btn, alignment=Qt.AlignmentFlag.AlignHCenter)

            dialog.setLayout(main_layout)
            self.about_dialog = dialog

        if self.about_dialog.isVisible():
            self.about_dialog.hide()
        else:
            self.about_dialog.show()
            self.about_dialog.raise_()
            self.about_dialog.activateWindow()

    def _confirm_dialog(
            self,
            title: str,
            message: str,
            confirm_text: str = "OK",
            cancel_text: str = "Cancel",
    ) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setMinimumWidth(420)

        layout = QVBoxLayout()
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(0)

        label = QLabel(message)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        layout.addWidget(label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        cancel_btn = QPushButton(cancel_text)
        confirm_btn = QPushButton(confirm_text)

        cancel_btn.setDefault(True)
        cancel_btn.setAutoDefault(True)

        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(confirm_btn)
        btn_row.addStretch()

        layout.addLayout(btn_row)
        dialog.setLayout(layout)

        cancel_btn.clicked.connect(dialog.reject)
        confirm_btn.clicked.connect(dialog.accept)

        return dialog.exec() == QDialog.DialogCode.Accepted

    def _on_reset_connection_settings(self):
        confirmed = self._confirm_dialog(
            "Reset Connection Settings",
            "Restore connection settings to their defaults.",
            confirm_text="Reset",
            cancel_text="Cancel",
        )

        if confirmed:
            new_sync_port = DEFAULT_SYNC_PORT
            new_bridge_port = DEFAULT_BRIDGE_PORT
            new_token = DEFAULT_ACCESS_TOKEN

            old_sync_port = getattr(self.sync_manager, "port", DEFAULT_SYNC_PORT)
            old_mode = getattr(self.sync_manager, "mode", "off")

            # 儲存設定
            self.settings.setValue("sync/port", new_sync_port)
            self.settings.setValue("bridge_port", new_bridge_port)
            self.settings.setValue("access_token", new_token)

            # 套用 token
            self.current_access_token = new_token

            # Reset Sync
            self.sync_manager.send_offline()
            self.sync_manager.presence_timer.stop()
            self.sync_manager.stop_listener()
            self.sync_seen_devices.clear()

            self.sync_manager.port = new_sync_port

            if old_mode in ("controller", "follower"):
                self.sync_manager.set_mode(old_mode)
                self.sync_manager.start_listener()
                self.sync_manager.presence_timer.start()
                self.sync_manager._send_presence()
            else:
                self.sync_manager.set_mode("off")

            # Reset Bridge
            self.bridge_server.stop()
            self.bridge_port = new_bridge_port
            self.bridge_server.port = new_bridge_port
            self.bridge_server.access_token = new_token
            self.bridge_server.start()

            # 更新 UI
            self._apply_bridge_security_settings()
            self._update_token_display()
            self._update_bridge_port_display()
            self._update_connected_target_display()
            self._update_device_name_display()
            self._update_sync_port_display()
            self._update_sync_status_label()
            self._update_sync_action_states()

            if hasattr(self, "connected_devices_dialog") and self.connected_devices_dialog:
                try:
                    self.connected_devices_dialog.refresh_content()
                except Exception:
                    pass

            self.logger.log("[RESET] Connection settings restored to default")

    def _on_reset_all_custom_settings(self):
        confirmed = self._confirm_dialog(
            "Restore Factory Settings",
            "Restore all custom settings to the factory defaults.",
            confirm_text="Reset",
            cancel_text="Cancel",
        )

        if not confirmed:
            return

        # ===== Clear all saved settings =====
        self.settings.clear()
        self.custom_color_panel.load_swatches(self.load_custom_swatches())
        self.rebuild_user_pattern_menu()
        self.return_to_colourspace()

        # ===== Reset runtime values =====

        # Reset ColourSpace connection settings
        self.settings.setValue(SETTINGS_KEY_HOST, DEFAULT_HOST)
        self.settings.setValue(SETTINGS_KEY_PORT, DEFAULT_PORT)
        self.connection_manager.configure(DEFAULT_HOST, DEFAULT_PORT, reconnect_now=True)

        # ColourSpace connection
        self.host = DEFAULT_HOST
        self.port = DEFAULT_PORT

        # Sync
        self.sync_manager.send_offline()
        self.sync_manager.presence_timer.stop()
        self.sync_manager.stop_listener()
        self.sync_seen_devices.clear()

        self.sync_manager.port = DEFAULT_SYNC_PORT
        self.sync_manager.set_mode("off")

        # Bridge
        self.bridge_server.stop()

        self.bridge_port = DEFAULT_BRIDGE_PORT
        self.current_access_token = DEFAULT_ACCESS_TOKEN

        self.bridge_server.port = DEFAULT_BRIDGE_PORT
        self.bridge_server.access_token = DEFAULT_ACCESS_TOKEN
        self.bridge_server.start()

        # ===== Apply bridge security settings =====
        self._apply_bridge_security_settings()

        # ===== Update UI =====
        self._update_token_display()
        self._update_bridge_port_display()
        self._update_connected_target_display()
        self._update_device_name_display()
        self._update_sync_port_display()
        self._update_sync_status_label()
        self._update_sync_action_states()

        if hasattr(self, "connected_devices_dialog") and self.connected_devices_dialog:
            try:
                self.connected_devices_dialog.refresh_content()
            except Exception:
                pass

        self.logger.log("[RESET] All custom settings restored to default")


class SyncManager(QObject):
    sync_message_received = Signal(dict)

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.mode = "off"
        self.port = 8766
        self._listening = False
        self._listener_socket = None
        self._listener_thread = None

        import uuid
        self.client_id = str(uuid.uuid4())

        self.sync_message_received.connect(self._apply_message_on_main_thread)
        self.presence_timer = QTimer(self)
        self.presence_timer.setInterval(2000)
        self.presence_timer.timeout.connect(self._send_presence)

        self._pending_solid_send = False
        self._pending_solid_recv_ip = None
        self._pending_solid_apply = False

        self._solid_send_log_timer = QTimer(self)
        self._solid_send_log_timer.setSingleShot(True)
        self._solid_send_log_timer.setInterval(150)
        self._solid_send_log_timer.timeout.connect(self._flush_solid_send_log)

        self._solid_recv_log_timer = QTimer(self)
        self._solid_recv_log_timer.setSingleShot(True)
        self._solid_recv_log_timer.setInterval(150)
        self._solid_recv_log_timer.timeout.connect(self._flush_solid_recv_log)

        self._solid_apply_log_timer = QTimer(self)
        self._solid_apply_log_timer.setSingleShot(True)
        self._solid_apply_log_timer.setInterval(150)
        self._solid_apply_log_timer.timeout.connect(self._flush_solid_apply_log)

    def set_mode(self, mode: str):
        self.mode = mode
        self.main_window.logger.log(f"[SYNC] mode = {mode}")

    def _queue_sync_send_log(self, action: str):
        if action == "solid":
            self._pending_solid_send = True
            self._solid_send_log_timer.start()
        else:
            self.main_window.logger.log(f"[SYNC SEND] action={action} → <broadcast>:{self.port}")

    def _flush_solid_send_log(self):
        if self._pending_solid_send:
            self.main_window.logger.log(f"[SYNC SEND] action=solid → <broadcast>:{self.port}")
            self._pending_solid_send = False

    def _queue_sync_recv_log(self, action: str, source_ip: str):
        if action == "solid":
            self._pending_solid_recv_ip = source_ip
            self._solid_recv_log_timer.start()
        else:
            self.main_window.logger.log(f"[SYNC RECV] action={action} from {source_ip}")

    def _flush_solid_recv_log(self):
        if self._pending_solid_recv_ip:
            self.main_window.logger.log(f"[SYNC RECV] action=solid from {self._pending_solid_recv_ip}")
            self._pending_solid_recv_ip = None

    def _queue_sync_apply_log(self, action: str):
        if action == "solid":
            self._pending_solid_apply = True
            self._solid_apply_log_timer.start()
        else:
            self.main_window.logger.log(f"[SYNC APPLY] action={action}")

    def _flush_solid_apply_log(self):
        if self._pending_solid_apply:
            self.main_window.logger.log("[SYNC APPLY] action=solid")
            self._pending_solid_apply = False

    # ===== Controller：發送 =====
    def broadcast(self, payload: dict):
        action = payload.get("action")

        if action == "presence":
            if self.mode == "off":
                return
        else:
            if self.mode != "controller":
                return

        import json, socket
        try:
            payload = {
                **payload,
                "sender": self.client_id,
                "device_name": self.main_window.get_sync_device_name(),
            }
            msg = json.dumps(payload).encode("utf-8")
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

            if payload.get("action") != "presence":
                self._queue_sync_send_log(payload.get("action"))

            sock.sendto(msg, ("<broadcast>", self.port))
            sock.close()
        except Exception as e:
            self.main_window.logger.log(f"[SYNC ERROR] broadcast error: {e}")

    def _send_presence(self):
        if self.mode == "off":
            return

        self.broadcast({
            "action": "presence",
            "mode": self.mode,
        })

    # ===== Offline：發送 =====
    def send_offline(self):
        if self.mode == "off":
            return

        self.broadcast({
            "action": "offline",
            "mode": self.mode,
        })

    # ===== Follower：接收 =====
    def start_listener(self):
        if self._listening:
            return

        import socket
        import threading

        self._listening = True

        def loop():
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self._listener_socket = sock

                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

                if hasattr(socket, "SO_REUSEPORT"):
                    try:
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                    except OSError:
                        pass

                sock.bind(("", self.port))
                sock.settimeout(0.5)

                while self.mode != "off":
                    try:
                        data, addr = sock.recvfrom(4096)
                        self.handle_message(data, addr)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    except Exception as e:
                        self.main_window.logger.log(f"[SYNC ERROR] recv error: {e}")

            except Exception as e:
                self.main_window.logger.log(f"[SYNC ERROR] listener start error: {e}")

            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except Exception:
                        pass

                self._listener_socket = None
                self._listening = False

        self._listener_thread = threading.Thread(target=loop, daemon=True)
        self._listener_thread.start()

    def stop_listener(self):
        self.mode = "off"

        sock = self._listener_socket
        self._listener_socket = None

        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass

        thread = self._listener_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

        self._listener_thread = None

    def handle_message(self, data: bytes, addr=None):
        import json
        try:
            msg = json.loads(data.decode("utf-8"))
        except:
            return

        if addr is not None:
            msg["_source_ip"] = addr[0]
            msg["_source_port"] = addr[1]

        if msg.get("sender") != self.client_id and msg.get("action") != "presence":
            self._queue_sync_recv_log(msg.get("action"), msg.get("_source_ip", "?"))

        self.sync_message_received.emit(msg)

    def _apply_message_on_main_thread(self, msg: dict):
        if msg.get("sender") == self.client_id:
            return

        action = msg.get("action")

        if action == "offline":
            self.main_window._remove_sync_source(msg)
            return

        self.main_window._remember_sync_source(msg)

        if action == "presence":
            return

        self._queue_sync_apply_log(action)

        # Controller 可選擇是否接受其他 controller 的一般覆蓋
        # 但 take_control 仍然允許通過
        if (
                action != "take_control"
                and self.main_window.sync_controller_action.isChecked()
                and not self.main_window.sync_allow_incoming_override_action.isChecked()
        ):
            return

        if action == "solid":
            r, g, b = msg["rgb"]
            label = msg.get("label", f"RGB=({r}, {g}, {b})")
            self.main_window.set_test_pattern_solid(r, g, b, label, from_sync=True)

        elif action == "return":
            self.main_window.return_to_colourspace(from_sync=True)

        elif action == "test_pattern":
            name = msg["name"]
            path = TEST_PATTERN_DIR / name
            self.main_window.show_image_test_pattern(path, from_sync=True)

        elif action == "take_control":
            self.main_window._apply_remote_take_control(msg)


def main():
    app = QApplication([])

    if sys.platform == "darwin":
        icon_path = BASE_DIR / "assets" / "icon.icns"
    elif sys.platform == "win32":
        icon_path = BASE_DIR / "assets" / "icon.ico"
    else:
        icon_path = None

    if icon_path and icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    settings = QSettings(APP_ORG, APP_NAME)

    logger = Logger(settings=settings)
    logger.log("[SYSTEM] started")

    window = MainWindow(settings=settings, logger=logger)
    window.show()

    try:
        app.exec()
    except Exception as e:
        logger.log(f"[SYSTEM ERROR] app.exec failed: {e}")
        logger.log(f"[SYSTEM ERROR] {traceback.format_exc().strip()}")

    logger.log("[SYSTEM] exited")


if __name__ == "__main__":
    main()