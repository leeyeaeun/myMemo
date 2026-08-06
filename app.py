from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QEvent, QIODevice, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QImage, QPainter, QPen, QPixmap, QTextCharFormat, QTextCursor, QTextImageFormat, QTextListFormat
from PySide6.QtWidgets import (
    QApplication, QColorDialog, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
    QGraphicsDropShadowEffect, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QPushButton, QSizeGrip, QSlider, QSplitter, QTextEdit, QToolButton, QVBoxLayout, QWidget,
)

APP_NAME = "Memo"
LEGACY_APP_NAME = "Morrow Notes"
NOTE_COLORS = ["#FFF3B8", "#E9E0FF", "#DDF5E8", "#FFDDE3", "#DCEEFF", "#F2EEE8"]
THEMES = {
    "Sorbet": ["#FFD9E2", "#FFE7C2", "#FFF2B8", "#DDF5E8", "#DCEEFF", "#E9E0FF"],
    "Nordic": ["#E7ECEF", "#D6E4E5", "#E8E3DB", "#DFE7DD", "#E0E6EF", "#EEE9E4"],
    "Earth": ["#EADBC8", "#D8E2C5", "#C9D8D1", "#E6CFC2", "#D9D0E3", "#E8DEB5"],
    "Mono": ["#F6F6F4", "#ECECEA", "#E3E3E0", "#DADAD6", "#F0EFEB", "#E8E7E3"],
}


def asset_path(name: str) -> Path:
    return Path(__file__).resolve().parent / "assets" / name


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def storage_path() -> Path:
    local_root = Path(os.getenv("LOCALAPPDATA", Path.home()))
    folder = local_root / APP_NAME
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "notes.json"
    legacy_path = local_root / LEGACY_APP_NAME / "notes.json"
    if not path.exists() and legacy_path.exists():
        shutil.copy2(legacy_path, path)
    return path


def new_note(color: str = NOTE_COLORS[0]) -> dict:
    stamp = now_iso()
    return {
        "id": str(uuid.uuid4()), "title": "새 메모", "html": "", "color": color,
        "created_at": stamp, "updated_at": stamp,
        "window": {"open": False, "x": None, "y": None, "w": 330, "h": 390, "pinned": False},
    }


def normalize_note(note: dict) -> dict:
    note.setdefault("id", str(uuid.uuid4()))
    note.setdefault("title", "제목 없음")
    note.setdefault("html", "")
    note.setdefault("color", NOTE_COLORS[0])
    note.setdefault("created_at", now_iso())
    note.setdefault("updated_at", now_iso())
    note.setdefault("window", {})
    win = note["window"]
    for key, value in {"open": False, "x": None, "y": None, "w": 330, "h": 390, "pinned": False}.items():
        win.setdefault(key, value)
    return note


def text_color(background: str) -> str:
    color = QColor(background)
    luminance = .299 * color.red() + .587 * color.green() + .114 * color.blue()
    return "#202020" if luminance > 150 else "#FFFFFF"


def pin_icon(color: str, active: bool = False) -> QIcon:
    pixmap = QPixmap(22, 22)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pin_color = QColor("#D65345") if active else QColor(color)
    pen = QPen(pin_color, 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(pin_color if active else Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(7, 3, 8, 7, 2, 2)
    painter.drawLine(5, 10, 17, 10)
    painter.drawLine(11, 10, 11, 19)
    painter.end()
    return QIcon(pixmap)


def qimage_data_uri(image: QImage, image_format: str = "PNG") -> tuple[str, int, int] | None:
    if image.isNull():
        return None
    fmt = "JPEG" if image_format.upper() in {"JPG", "JPEG"} else "PNG"
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, fmt)
    mime = "image/jpeg" if fmt == "JPEG" else "image/png"
    uri = f"data:{mime};base64,{base64.b64encode(bytes(data)).decode('ascii')}"
    return uri, image.width(), image.height()


def image_data_uri(path: str) -> tuple[str, int, int] | None:
    suffix = Path(path).suffix.lower()
    image_format = "JPEG" if suffix in {".jpg", ".jpeg"} else "PNG"
    return qimage_data_uri(QImage(path), image_format)


class Store:
    def __init__(self) -> None:
        self.path = storage_path()
        self.notes: list[dict] = []
        self.load()

    def load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            self.notes = payload.get("notes", []) if isinstance(payload, dict) else []
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self.notes = []
        self.notes = [normalize_note(n) for n in self.notes if isinstance(n, dict)]
        if any(isinstance(n.get("order"), int) for n in self.notes):
            self.notes.sort(key=lambda n: n.get("order", len(self.notes)))
        else:
            self.notes.sort(key=lambda n: n.get("updated_at", ""), reverse=True)
        if not self.notes:
            note = new_note()
            note.update(title="첫 번째 메모", html="<p>여기에 가볍게 기록해 보세요.</p>")
            self.notes = [note]
            self.save()

    def save(self) -> None:
        for index, note in enumerate(self.notes):
            note["order"] = index
        payload = {"version": 2, "app": APP_NAME, "notes": self.notes}
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def by_id(self, note_id: str | None) -> dict | None:
        return next((n for n in self.notes if n.get("id") == note_id), None)


class RichEditor(QTextEdit):
    image_resize_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptRichText(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setPlaceholderText("생각을 적어보세요…")
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.context_menu)

    def context_menu(self, pos) -> None:
        menu = self.createStandardContextMenu()
        cursor = self.cursorForPosition(pos)
        image = cursor.charFormat().toImageFormat()
        resize = None
        if image.isValid():
            menu.addSeparator()
            resize = menu.addAction("이미지 크기 조정…")
        selected = menu.exec(self.mapToGlobal(pos))
        if resize and selected == resize:
            self.setTextCursor(cursor)
            self.image_resize_requested.emit()

    def insert_image_file(self, path: str, max_width: int = 560) -> None:
        result = image_data_uri(path)
        if not result:
            return
        self.insert_embedded_image(result, max_width)

    def insert_embedded_image(self, result: tuple[str, int, int], max_width: int) -> None:
        uri, source_w, source_h = result
        width = min(source_w, max_width)
        image = QTextImageFormat()
        image.setName(uri)
        image.setWidth(width)
        image.setHeight(round(width * source_h / max(source_w, 1)))
        self.textCursor().insertImage(image)

    def canInsertFromMimeData(self, source) -> bool:
        return source.hasImage() or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source) -> None:
        if source.hasImage():
            clipboard_image = source.imageData()
            if hasattr(clipboard_image, "toImage"):
                clipboard_image = clipboard_image.toImage()
            if isinstance(clipboard_image, QImage) and not clipboard_image.isNull():
                result = qimage_data_uri(clipboard_image)
                if result:
                    available_width = max(180, self.viewport().width() - 28)
                    self.insert_embedded_image(result, available_width)
                    return
        super().insertFromMimeData(source)


class ReorderableNoteList(QListWidget):
    order_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

    def dropEvent(self, event) -> None:
        super().dropEvent(event)
        if event.isAccepted():
            self.order_changed.emit()


class StickyWindow(QWidget):
    changed = Signal(str)
    closed = Signal(str)
    delete_requested = Signal(str)
    manager_requested = Signal()
    RESIZE_MARGIN = 22

    def __init__(self, note: dict, save_callback) -> None:
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.note = note
        self.save_callback = save_callback
        self.app_shutdown = False
        self.drag_origin: QPoint | None = None
        self.window_origin: QPoint | None = None
        self.resize_edges = Qt.Edge(0)
        self.resize_origin: QPoint | None = None
        self.resize_geometry = None
        self.loading = True
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(300)
        self.save_timer.timeout.connect(self.save_note)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setMinimumSize(260, 240)
        self.build_ui()
        self.register_shortcuts()
        self.restore_geometry()
        self.refresh_style()
        self.loading = False

    def build_ui(self) -> None:
        self.setObjectName("stickyWindow")
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(0)

        self.surface = QFrame()
        self.surface.setObjectName("stickySurface")
        self.surface.setMouseTracking(True)
        shadow = QGraphicsDropShadowEffect(self.surface)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 5)
        shadow.setColor(QColor(0, 0, 0, 62))
        self.surface.setGraphicsEffect(shadow)
        root.addWidget(self.surface)
        surface_layout = QVBoxLayout(self.surface)
        surface_layout.setContentsMargins(1, 1, 1, 1)
        surface_layout.setSpacing(0)

        self.header = QWidget()
        self.header.setObjectName("stickyHeader")
        self.header.setFixedHeight(46)
        row = QHBoxLayout(self.header)
        row.setContentsMargins(13, 7, 7, 5)
        self.title = QLineEdit(self.note.get("title", ""))
        self.title.setObjectName("stickyTitle")
        self.title.textChanged.connect(self.queue_save)
        row.addWidget(self.title, 1)
        self.manager_btn = self.button("☰", "전체 메모 보기", self.manager_requested.emit)
        self.pin_btn = self.button("", "항상 위 고정", self.toggle_pin)
        self.pin_btn.setIconSize(QSize(20, 20))
        self.color_btn = self.button("●", "색상 변경", self.choose_color)
        self.delete_btn = self.button("⌫", "메모 삭제", self.request_delete)
        self.close_btn = self.button("×", "메모 창 닫기", self.close)
        row.addWidget(self.manager_btn)
        row.addWidget(self.pin_btn)
        row.addWidget(self.color_btn)
        row.addWidget(self.delete_btn)
        row.addWidget(self.close_btn)
        surface_layout.addWidget(self.header)

        self.toolbar = QWidget()
        self.toolbar.setObjectName("stickyToolbar")
        tools = QHBoxLayout(self.toolbar)
        tools.setContentsMargins(9, 5, 9, 6)
        tools.setSpacing(1)
        self.size_box = QComboBox()
        self.size_box.addItems(["12", "14", "16", "18", "22", "28", "36"])
        self.size_box.setEditable(True)
        self.size_box.setCurrentText("16")
        self.size_box.activated.connect(self.font_size)
        tools.addWidget(self.size_box)
        tools.addWidget(self.button("•", "글머리표", self.bullets))
        tools.addWidget(self.button("B", "굵게  Ctrl+B", self.bold))
        tools.addWidget(self.button("I", "기울임  Ctrl+I", self.italic))
        tools.addWidget(self.button("U", "밑줄  Ctrl+U", self.underline))
        tools.addWidget(self.button("S", "취소선  Ctrl+Shift+X", self.strikeout))
        tools.addWidget(self.button("L", "왼쪽 정렬", lambda: self.editor.setAlignment(Qt.AlignmentFlag.AlignLeft)))
        tools.addWidget(self.button("C", "가운데 정렬", lambda: self.editor.setAlignment(Qt.AlignmentFlag.AlignCenter)))
        tools.addWidget(self.button("R", "오른쪽 정렬", lambda: self.editor.setAlignment(Qt.AlignmentFlag.AlignRight)))
        tools.addWidget(self.button("IMG", "이미지", self.add_image))
        tools.addStretch()
        surface_layout.addWidget(self.toolbar)

        self.editor = RichEditor()
        self.editor.setObjectName("stickyEditor")
        self.editor.setHtml(self.note.get("html", ""))
        self.editor.textChanged.connect(self.queue_save)
        self.editor.image_resize_requested.connect(lambda: resize_image(self, self.editor))
        surface_layout.addWidget(self.editor, 1)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(10, 2, 4, 4)
        self.saved = QLabel("자동 저장")
        self.saved.setObjectName("stickySaved")
        bottom.addWidget(self.saved)
        bottom.addStretch()
        self.size_grip = QSizeGrip(self.surface)
        self.size_grip.setFixedSize(28, 28)
        self.size_grip.setToolTip("드래그해서 메모 크기 조절")
        bottom.addWidget(self.size_grip)
        surface_layout.addLayout(bottom)

        self.header.installEventFilter(self)
        self.title.installEventFilter(self)

    def register_shortcuts(self) -> None:
        shortcuts = [
            ("Ctrl+B", self.bold),
            ("Ctrl+I", self.italic),
            ("Ctrl+U", self.underline),
            ("Ctrl+Shift+X", self.strikeout),
            ("Ctrl+Shift+.", lambda: self.adjust_font_size(2)),
            ("Ctrl+Shift+,", lambda: self.adjust_font_size(-2)),
            ("Ctrl+]", lambda: self.adjust_font_size(2)),
            ("Ctrl+[", lambda: self.adjust_font_size(-2)),
        ]
        self.shortcut_actions = []
        for keys, callback in shortcuts:
            action = QAction(self)
            action.setShortcut(QKeySequence(keys))
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            action.triggered.connect(callback)
            self.addAction(action)
            self.shortcut_actions.append(action)

    def button(self, text, tip, callback) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tip)
        button.setFixedHeight(30)
        button.clicked.connect(callback)
        return button

    def eventFilter(self, watched, event) -> bool:
        if watched in {self.header, self.title}:
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                if watched is self.title and self.title.hasSelectedText():
                    return False
                self.drag_origin = event.globalPosition().toPoint()
                self.window_origin = self.pos()
            elif event.type() == QEvent.Type.MouseMove and self.drag_origin and event.buttons() & Qt.MouseButton.LeftButton:
                self.move(self.window_origin + event.globalPosition().toPoint() - self.drag_origin)
                return True
            elif event.type() == QEvent.Type.MouseButtonRelease:
                self.drag_origin = None
                self.persist_geometry()
        return super().eventFilter(watched, event)

    def edges_at(self, pos: QPoint):
        margin = self.RESIZE_MARGIN
        edges = Qt.Edge(0)
        if pos.x() <= margin:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= self.width() - margin:
            edges |= Qt.Edge.RightEdge
        if pos.y() <= margin:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= self.height() - margin:
            edges |= Qt.Edge.BottomEdge
        return edges

    def update_resize_cursor(self, edges) -> None:
        if edges in (Qt.Edge.LeftEdge | Qt.Edge.TopEdge, Qt.Edge.RightEdge | Qt.Edge.BottomEdge):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif edges in (Qt.Edge.RightEdge | Qt.Edge.TopEdge, Qt.Edge.LeftEdge | Qt.Edge.BottomEdge):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.unsetCursor()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            edges = self.edges_at(event.position().toPoint())
            if edges:
                self.resize_edges = edges
                self.resize_origin = event.globalPosition().toPoint()
                self.resize_geometry = self.geometry()
                handle = self.windowHandle()
                if handle and handle.startSystemResize(edges):
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.resize_origin and self.resize_edges and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self.resize_origin
            rect = self.resize_geometry
            left, top, right, bottom = rect.left(), rect.top(), rect.right(), rect.bottom()
            if self.resize_edges & Qt.Edge.LeftEdge:
                left = min(left + delta.x(), right - self.minimumWidth() + 1)
            if self.resize_edges & Qt.Edge.RightEdge:
                right = max(right + delta.x(), left + self.minimumWidth() - 1)
            if self.resize_edges & Qt.Edge.TopEdge:
                top = min(top + delta.y(), bottom - self.minimumHeight() + 1)
            if self.resize_edges & Qt.Edge.BottomEdge:
                bottom = max(bottom + delta.y(), top + self.minimumHeight() - 1)
            self.setGeometry(left, top, right - left + 1, bottom - top + 1)
            event.accept()
            return
        self.update_resize_cursor(self.edges_at(event.position().toPoint()))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self.resize_origin:
            self.resize_origin = None
            self.resize_edges = Qt.Edge(0)
            self.resize_geometry = None
            self.persist_geometry()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        if not self.resize_origin:
            self.unsetCursor()
        super().leaveEvent(event)

    def restore_geometry(self) -> None:
        win = self.note["window"]
        self.resize(max(240, int(win.get("w", 330))), max(220, int(win.get("h", 390))))
        x, y = win.get("x"), win.get("y")
        if x is None or y is None:
            screen = QApplication.primaryScreen().availableGeometry()
            offset = int(self.note["id"][-2:], 16) % 140
            self.move(screen.right() - self.width() - 24 - offset, screen.top() + 40 + offset)
        else:
            self.move(int(x), int(y))
        self.apply_pin(bool(win.get("pinned", False)))

    def persist_geometry(self) -> None:
        win = self.note["window"]
        win.update(x=self.x(), y=self.y(), w=self.width(), h=self.height())
        self.save_callback()

    def queue_save(self) -> None:
        if self.loading:
            return
        self.saved.setText("저장 중…")
        self.save_timer.start()

    def save_note(self) -> None:
        self.note["title"] = self.title.text().strip() or "제목 없음"
        self.note["html"] = self.editor.toHtml()
        self.note["updated_at"] = now_iso()
        self.persist_geometry()
        self.saved.setText("방금 저장됨")
        self.changed.emit(self.note["id"])

    def sync_from_note(self) -> None:
        self.loading = True
        if self.title.text() != self.note.get("title", ""):
            self.title.setText(self.note.get("title", ""))
        if self.editor.toHtml() != self.note.get("html", ""):
            cursor = self.editor.textCursor()
            self.editor.setHtml(self.note.get("html", ""))
            self.editor.setTextCursor(cursor)
        self.refresh_style()
        self.loading = False

    def char_format(self, fmt: QTextCharFormat) -> None:
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.SelectionType.WordUnderCursor)
        cursor.mergeCharFormat(fmt)
        self.editor.mergeCurrentCharFormat(fmt)

    def bold(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Weight.Normal if self.editor.fontWeight() > QFont.Weight.Normal else QFont.Weight.Bold)
        self.char_format(fmt)

    def italic(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontItalic(not self.editor.fontItalic())
        self.char_format(fmt)

    def underline(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontUnderline(not self.editor.fontUnderline())
        self.char_format(fmt)

    def strikeout(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontStrikeOut(not self.editor.currentCharFormat().fontStrikeOut())
        self.char_format(fmt)

    def font_size(self, *_args) -> None:
        fmt = QTextCharFormat()
        try:
            size = max(8.0, min(96.0, float(self.size_box.currentText())))
        except ValueError:
            size = 16.0
        fmt.setFontPointSize(size)
        self.char_format(fmt)

    def adjust_font_size(self, delta: int) -> None:
        current = self.editor.textCursor().charFormat().fontPointSize()
        if current <= 0:
            current = self.editor.fontPointSize() or 16
        target = max(8, min(96, round(current + delta)))
        self.size_box.setEditText(str(target))
        self.font_size()

    def bullets(self) -> None:
        cursor = self.editor.textCursor()
        current = cursor.currentList()
        if current:
            block = cursor.blockFormat()
            block.setIndent(0)
            cursor.setBlockFormat(block)
        else:
            fmt = QTextListFormat()
            fmt.setStyle(QTextListFormat.Style.ListDisc)
            fmt.setIndent(1)
            cursor.createList(fmt)
        self.editor.setFocus()

    def add_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "이미지 넣기", "", "이미지 (*.png *.jpg *.jpeg *.webp *.bmp)")
        if path:
            self.editor.insert_image_file(path, max(180, self.width() - 40))

    def choose_color(self) -> None:
        color = QColorDialog.getColor(QColor(self.note["color"]), self, "메모 색상")
        if color.isValid():
            self.note["color"] = color.name()
            self.refresh_style()
            self.save_callback()
            self.changed.emit(self.note["id"])

    def request_delete(self) -> None:
        answer = QMessageBox.question(
            self,
            "메모 삭제",
            f"‘{self.note.get('title') or '제목 없음'}’ 메모를 삭제할까요?",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.delete_requested.emit(self.note["id"])

    def refresh_style(self) -> None:
        bg = self.note.get("color", NOTE_COLORS[0])
        fg = text_color(bg)
        self.setStyleSheet(f"""
            #stickyWindow {{ background: transparent; }}
            #stickySurface {{ background: {bg}; border: 1px solid rgba(0,0,0,28); border-radius: 18px; }}
            #stickyHeader, #stickyToolbar {{ background: transparent; }}
            #stickyToolbar {{ border-top: 1px solid rgba(0,0,0,18); border-bottom: 1px solid rgba(0,0,0,18); }}
            #stickyTitle {{ border: none; background: transparent; color: {fg}; font-size: 15px; font-weight: 650; padding: 2px; }}
            #stickyEditor {{ border: none; background: transparent; color: {fg}; font-size: 16px; padding: 9px 14px; selection-background-color: rgba(50,90,180,90); }}
            QToolButton {{ border: none; background: rgba(255,255,255,48); color: {fg}; border-radius: 9px; padding: 3px 7px; font-size: 10px; }}
            QToolButton:hover {{ background: rgba(255,255,255,135); }}
            QComboBox {{ border: none; background: rgba(255,255,255,62); color: {fg}; border-radius: 7px; padding: 3px; min-width: 44px; }}
            #stickySaved {{ color: {fg}; opacity: .6; font-size: 10px; }}
        """)
        self.color_btn.setStyleSheet(f"color: {bg}; background: {fg};")
        self.refresh_pin_icon()

    def refresh_pin_icon(self) -> None:
        pinned = bool(self.note["window"].get("pinned", False))
        fg = text_color(self.note.get("color", NOTE_COLORS[0]))
        self.pin_btn.setIcon(pin_icon(fg, pinned))
        self.pin_btn.setToolTip("항상 위 고정 해제" if pinned else "항상 위에 고정")

    def apply_pin(self, pinned: bool) -> None:
        was_visible = self.isVisible()
        self.note["window"]["pinned"] = pinned
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, pinned)
        self.refresh_pin_icon()
        if was_visible:
            self.show()
            self.raise_()
            self.activateWindow()

    def toggle_pin(self) -> None:
        self.apply_pin(not bool(self.note["window"].get("pinned")))
        self.save_callback()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self.loading:
            self.persist_geometry()

    def closeEvent(self, event) -> None:
        self.save_timer.stop()
        self.save_note()
        if not self.app_shutdown:
            self.note["window"]["open"] = False
        self.save_callback()
        self.closed.emit(self.note["id"])
        event.accept()


def resize_image(parent, editor: RichEditor) -> None:
    cursor = editor.textCursor()
    image = cursor.charFormat().toImageFormat()
    if not image.isValid():
        return
    dialog = QDialog(parent)
    dialog.setWindowTitle("이미지 크기")
    layout = QVBoxLayout(dialog)
    label = QLabel(f"너비: {round(image.width())} px")
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(100, 1000)
    slider.setValue(max(100, min(1000, round(image.width()))))
    slider.valueChanged.connect(lambda v: label.setText(f"너비: {v} px"))
    apply_button = QPushButton("적용")
    apply_button.clicked.connect(dialog.accept)
    layout.addWidget(label); layout.addWidget(slider); layout.addWidget(apply_button)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        ratio = image.height() / max(image.width(), 1)
        image.setWidth(slider.value())
        image.setHeight(round(slider.value() * ratio))
        cursor.setCharFormat(image)


class NotesWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.store = Store()
        self.current_id: str | None = None
        self.loading = False
        self.stickies: dict[str, StickyWindow] = {}
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(350)
        self.save_timer.timeout.connect(self.save_current)
        self.setWindowTitle(APP_NAME)
        self.resize(1060, 730)
        self.setMinimumSize(780, 540)
        self.build_ui()
        self.apply_style()
        self.refresh_list(select_id=self.store.notes[0]["id"])
        QTimer.singleShot(100, self.restore_stickies)

    def button(self, text, tip, callback, width=36) -> QToolButton:
        button = QToolButton()
        button.setText(text); button.setToolTip(tip); button.setFixedSize(width, 34)
        button.clicked.connect(callback)
        return button

    def build_ui(self) -> None:
        root = QWidget(); root.setObjectName("root"); self.setCentralWidget(root)
        outer = QVBoxLayout(root); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        top = QWidget(); top.setObjectName("topbar")
        top_row = QHBoxLayout(top); top_row.setContentsMargins(22, 14, 18, 14)
        brand = QLabel("Memo"); brand.setObjectName("brand"); top_row.addWidget(brand); top_row.addStretch()
        top_row.addWidget(self.button("▣", "현재 메모를 스티키 창으로 열기", self.open_current_sticky))
        self.more_button = self.button("•••", "백업과 테마", self.more_menu)
        top_row.addWidget(self.more_button); outer.addWidget(top)

        splitter = QSplitter(Qt.Orientation.Horizontal); splitter.setChildrenCollapsible(False); outer.addWidget(splitter, 1)
        sidebar = QWidget(); sidebar.setObjectName("sidebar")
        side = QVBoxLayout(sidebar); side.setContentsMargins(18, 18, 14, 18); side.setSpacing(12)
        head = QHBoxLayout(); title = QLabel("모든 메모"); title.setObjectName("sectionTitle")
        self.count = QLabel(); self.count.setObjectName("count")
        add = QPushButton("+"); add.setObjectName("addButton"); add.clicked.connect(self.add_note)
        head.addWidget(title); head.addWidget(self.count); head.addStretch(); head.addWidget(add); side.addLayout(head)
        self.search = QLineEdit(); self.search.setPlaceholderText("메모 검색"); self.search.setClearButtonEnabled(True); self.search.textChanged.connect(self.refresh_list); side.addWidget(self.search)
        self.list = ReorderableNoteList(); self.list.setObjectName("noteList"); self.list.setSpacing(7); self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.currentItemChanged.connect(self.select_note); self.list.itemDoubleClicked.connect(lambda _: self.open_current_sticky())
        self.list.customContextMenuRequested.connect(self.list_context_menu); self.list.order_changed.connect(self.persist_list_order); side.addWidget(self.list, 1)
        hint = QLabel("드래그로 순서 변경 · 더블 클릭으로 스티키 열기"); hint.setObjectName("hint"); hint.setWordWrap(True); side.addWidget(hint)
        splitter.addWidget(sidebar)

        content = QWidget(); content.setObjectName("content")
        body = QVBoxLayout(content); body.setContentsMargins(30, 18, 30, 25); body.setSpacing(12)
        title_row = QHBoxLayout(); self.title_edit = QLineEdit(); self.title_edit.setObjectName("titleEdit"); self.title_edit.textChanged.connect(self.queue_save)
        title_row.addWidget(self.title_edit, 1)
        self.sticky_btn = self.button("▣", "스티키 창으로 열기", self.open_current_sticky)
        self.color_btn = self.button("●", "메모 색상", self.color_menu)
        title_row.addWidget(self.sticky_btn); title_row.addWidget(self.color_btn); title_row.addWidget(self.button("⌫", "메모 삭제", self.delete_note)); body.addLayout(title_row)

        toolbar = QWidget(); toolbar.setObjectName("toolbar"); tools = QHBoxLayout(toolbar); tools.setContentsMargins(8, 6, 8, 6); tools.setSpacing(2)
        tools.addWidget(self.button("B", "굵게", self.bold)); tools.addWidget(self.button("I", "기울임", self.italic)); tools.addWidget(self.button("U", "밑줄", self.underline)); tools.addWidget(self.button("S", "취소선", self.strikeout))
        self.size_box = QComboBox(); self.size_box.addItems(["12", "14", "16", "18", "22", "28", "36", "48"]); self.size_box.setCurrentText("16"); self.size_box.activated.connect(self.font_size); tools.addWidget(self.size_box)
        self.size_box.setEditable(True)
        tools.addWidget(self.button("L", "왼쪽 정렬", lambda: self.editor.setAlignment(Qt.AlignmentFlag.AlignLeft)))
        tools.addWidget(self.button("C", "가운데 정렬", lambda: self.editor.setAlignment(Qt.AlignmentFlag.AlignCenter)))
        tools.addWidget(self.button("R", "오른쪽 정렬", lambda: self.editor.setAlignment(Qt.AlignmentFlag.AlignRight)))
        tools.addWidget(self.button("•", "글머리표", self.bullets)); tools.addWidget(self.button("IMG", "이미지 삽입", self.add_image, 44)); tools.addStretch()
        tools.addWidget(self.button("↶", "실행 취소", lambda: self.editor.undo())); tools.addWidget(self.button("↷", "다시 실행", lambda: self.editor.redo())); body.addWidget(toolbar)
        self.editor = RichEditor(); self.editor.setObjectName("editor"); self.editor.textChanged.connect(self.queue_save); self.editor.image_resize_requested.connect(lambda: resize_image(self, self.editor)); body.addWidget(self.editor, 1)
        self.status = QLabel("모든 변경사항은 자동으로 저장됩니다"); self.status.setObjectName("status"); body.addWidget(self.status)
        splitter.addWidget(content); splitter.setSizes([290, 770])

        shortcuts = [
            ("Ctrl+N", self.add_note), ("Ctrl+Shift+S", self.export_backup),
            ("Ctrl+Shift+O", self.import_backup), ("Ctrl+Shift+P", self.open_current_sticky),
            ("Ctrl+B", self.bold), ("Ctrl+I", self.italic), ("Ctrl+U", self.underline),
            ("Ctrl+Shift+X", self.strikeout),
            ("Ctrl+Shift+.", lambda: self.adjust_font_size(2)),
            ("Ctrl+Shift+,", lambda: self.adjust_font_size(-2)),
            ("Ctrl+]", lambda: self.adjust_font_size(2)),
            ("Ctrl+[", lambda: self.adjust_font_size(-2)),
        ]
        for keys, callback in shortcuts:
            action = self.addAction
            shortcut = QAction(self); shortcut.setShortcut(QKeySequence(keys)); shortcut.triggered.connect(callback); action(shortcut)

    def apply_style(self) -> None:
        self.setStyleSheet("""
            * { font-family: "Segoe UI Variable", "Malgun Gothic", "Segoe UI"; color: #252526; }
            QMainWindow, #root, #content { background: #FCFCFB; }
            #topbar { background: white; border-bottom: 1px solid #E8E8E5; }
            #brand { font-size: 20px; font-weight: 700; letter-spacing: 1px; }
            #sidebar { background: #F4F4F1; border-right: 1px solid #E5E5E1; }
            #sectionTitle { font-size: 17px; font-weight: 650; } #count, #hint, #status { color: #92928E; font-size: 11px; }
            #addButton { border: none; background: #1D1D1F; color: white; border-radius: 15px; width: 30px; height: 30px; font-size: 18px; }
            QLineEdit { border: 1px solid #E2E2DE; background: white; border-radius: 11px; padding: 9px 12px; }
            #noteList { background: transparent; outline: none; }
            #noteList::item { background: white; border: 1px solid #E8E8E4; border-radius: 13px; padding: 11px 12px; margin: 0 1px; }
            #noteList::item:selected { background: #222223; color: white; border-color: #222223; }
            #noteList::indicator { background: #1D1D1F; height: 3px; border-radius: 1px; }
            #titleEdit { border: none; background: transparent; padding: 4px 2px; font-size: 25px; font-weight: 700; }
            #toolbar { background: #F2F2EF; border: 1px solid #E4E4E0; border-radius: 13px; }
            QToolButton { border: none; background: transparent; border-radius: 8px; font-size: 12px; }
            QToolButton:hover { background: #E4E4E0; }
            QComboBox { border: none; background: transparent; min-width: 48px; padding: 4px; }
            #editor { background: transparent; border: none; font-size: 16px; padding: 8px 3px; }
            QMenu { background: white; border: 1px solid #DEDEDA; padding: 6px; }
            QMenu::item { padding: 8px 25px 8px 12px; border-radius: 7px; } QMenu::item:selected { background: #EFEFEC; }
        """)

    def refresh_list(self, _text="", select_id=None) -> None:
        wanted = select_id or self.current_id; query = self.search.text().strip().lower(); selected = None
        self.list.blockSignals(True); self.list.clear()
        for note in self.store.notes:
            doc = QTextEdit(); doc.setHtml(note.get("html", "")); preview = " ".join(doc.toPlainText().split())
            if query and query not in f"{note.get('title', '')} {preview}".lower(): continue
            marker = "  ·  열림" if note["window"].get("open") else ""
            item = QListWidgetItem(f"{note.get('title') or '제목 없음'}{marker}\n{preview[:45] or '내용 없음'}")
            item.setData(Qt.ItemDataRole.UserRole, note["id"]); item.setSizeHint(QSize(230, 65)); item.setForeground(QColor(text_color(note["color"])))
            item.setBackground(QColor(note["color"])); self.list.addItem(item)
            if note["id"] == wanted: self.list.setCurrentItem(item); selected = item
        self.list.blockSignals(False); self.count.setText(str(len(self.store.notes)))
        if not selected and self.list.count(): self.list.setCurrentRow(0); selected = self.list.currentItem()
        if selected and selected.data(Qt.ItemDataRole.UserRole) != self.current_id: self.select_note(selected, None)

    def select_note(self, current, previous) -> None:
        if not current: return
        next_id = current.data(Qt.ItemDataRole.UserRole)
        if self.current_id and self.current_id != next_id: self.save_current(False)
        note = self.store.by_id(next_id)
        if not note: return
        self.loading = True; self.current_id = next_id; self.title_edit.setText(note["title"]); self.editor.setHtml(note["html"]); self.editor.moveCursor(QTextCursor.MoveOperation.Start)
        self.color_btn.setStyleSheet(f"color: {note['color']}; font-size: 19px;"); self.loading = False

    def queue_save(self) -> None:
        if self.loading or not self.current_id: return
        self.status.setText("저장 중…"); self.save_timer.start()

    def save_current(self, refresh=True) -> None:
        note = self.store.by_id(self.current_id)
        if not note or self.loading: return
        note["title"] = self.title_edit.text().strip() or "제목 없음"; note["html"] = self.editor.toHtml(); note["updated_at"] = now_iso()
        self.store.save(); self.status.setText("방금 저장됨")
        sticky = self.stickies.get(note["id"])
        if sticky: sticky.sync_from_note()
        if refresh: self.refresh_list(select_id=note["id"])

    def save_store(self) -> None:
        self.store.save()

    def sticky_changed(self, note_id: str) -> None:
        if note_id == self.current_id:
            note = self.store.by_id(note_id); self.loading = True; self.title_edit.setText(note["title"]); self.editor.setHtml(note["html"]); self.loading = False
        self.refresh_list(select_id=self.current_id)

    def open_current_sticky(self) -> None:
        self.save_current(False); note = self.store.by_id(self.current_id)
        if not note: return
        self.open_sticky(note)

    def open_sticky(self, note: dict) -> None:
        existing = self.stickies.get(note["id"])
        if existing: existing.show(); existing.raise_(); existing.activateWindow(); return
        note["window"]["open"] = True
        sticky = StickyWindow(note, self.save_store); sticky.changed.connect(self.sticky_changed); sticky.closed.connect(self.sticky_closed)
        sticky.delete_requested.connect(lambda note_id: self.delete_note_by_id(note_id, confirm=False))
        sticky.manager_requested.connect(self.show_manager)
        self.stickies[note["id"]] = sticky; self.store.save(); sticky.show(); sticky.raise_(); self.refresh_list(select_id=note["id"])

    def sticky_closed(self, note_id: str) -> None:
        self.stickies.pop(note_id, None); self.refresh_list(select_id=self.current_id)

    def show_manager(self) -> None:
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def list_context_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if not item:
            return
        self.list.setCurrentItem(item)
        menu = QMenu(self)
        open_action = menu.addAction("스티키 메모로 열기")
        menu.addSeparator()
        delete_action = menu.addAction("메모 삭제…")
        selected = menu.exec(self.list.mapToGlobal(pos))
        if selected == open_action:
            self.open_current_sticky()
        elif selected == delete_action:
            self.delete_note()

    def persist_list_order(self) -> None:
        visible_ids = [self.list.item(row).data(Qt.ItemDataRole.UserRole) for row in range(self.list.count())]
        if not visible_ids:
            return
        reordered = iter(visible_ids)
        visible_set = set(visible_ids)
        by_id = {note["id"]: note for note in self.store.notes}
        new_order = []
        for note in self.store.notes:
            if note["id"] in visible_set:
                new_order.append(by_id[next(reordered)])
            else:
                new_order.append(note)
        self.store.notes = new_order
        self.store.save()
        self.status.setText("메모 순서를 저장했습니다")

    def restore_stickies(self) -> None:
        for note in self.store.notes:
            if note["window"].get("open"):
                self.open_sticky(note)
        self.refresh_list(select_id=self.current_id)

    def add_note(self) -> None:
        self.save_current(False); note = new_note(NOTE_COLORS[len(self.store.notes) % len(NOTE_COLORS)]); self.store.notes.insert(0, note); self.store.save(); self.search.clear(); self.refresh_list(select_id=note["id"]); self.title_edit.selectAll(); self.title_edit.setFocus()

    def delete_note(self) -> None:
        self.delete_note_by_id(self.current_id, confirm=True)

    def delete_note_by_id(self, note_id: str | None, confirm: bool = True) -> None:
        note = self.store.by_id(note_id)
        if not note: return
        if confirm and QMessageBox.question(
            self,
            "메모 삭제",
            f"‘{note['title']}’ 메모를 삭제할까요?",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        sticky = self.stickies.pop(note["id"], None)
        if sticky: sticky.close()
        self.store.notes = [n for n in self.store.notes if n["id"] != note["id"]]
        if not self.store.notes: self.store.notes.append(new_note())
        self.current_id = None; self.store.save(); self.refresh_list(select_id=self.store.notes[0]["id"])

    def format_chars(self, fmt) -> None:
        cursor = self.editor.textCursor()
        if not cursor.hasSelection(): cursor.select(QTextCursor.SelectionType.WordUnderCursor)
        cursor.mergeCharFormat(fmt); self.editor.mergeCurrentCharFormat(fmt)

    def bold(self):
        fmt = QTextCharFormat(); fmt.setFontWeight(QFont.Weight.Normal if self.editor.fontWeight() > QFont.Weight.Normal else QFont.Weight.Bold); self.format_chars(fmt)
    def italic(self):
        fmt = QTextCharFormat(); fmt.setFontItalic(not self.editor.fontItalic()); self.format_chars(fmt)
    def underline(self):
        fmt = QTextCharFormat(); fmt.setFontUnderline(not self.editor.fontUnderline()); self.format_chars(fmt)
    def strikeout(self):
        fmt = QTextCharFormat(); fmt.setFontStrikeOut(not self.editor.currentCharFormat().fontStrikeOut()); self.format_chars(fmt)
    def font_size(self, *_args):
        try: size = max(8.0, min(96.0, float(self.size_box.currentText())))
        except ValueError: size = 16.0
        fmt = QTextCharFormat(); fmt.setFontPointSize(size); self.format_chars(fmt)
    def adjust_font_size(self, delta: int):
        current = self.editor.textCursor().charFormat().fontPointSize()
        if current <= 0: current = self.editor.fontPointSize() or 16
        target = max(8, min(96, round(current + delta)))
        self.size_box.setEditText(str(target)); self.font_size()
    def bullets(self):
        cursor = self.editor.textCursor(); current = cursor.currentList()
        if current:
            block = cursor.blockFormat(); block.setIndent(0); cursor.setBlockFormat(block)
        else:
            fmt = QTextListFormat(); fmt.setStyle(QTextListFormat.Style.ListDisc); cursor.createList(fmt)
    def add_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "이미지 넣기", "", "이미지 (*.png *.jpg *.jpeg *.webp *.bmp)")
        if path: self.editor.insert_image_file(path)

    def set_note_color(self, color: str) -> None:
        note = self.store.by_id(self.current_id)
        if not note: return
        note["color"] = color; self.color_btn.setStyleSheet(f"color: {color}; font-size: 19px;"); self.store.save()
        sticky = self.stickies.get(note["id"])
        if sticky: sticky.refresh_style()
        self.refresh_list(select_id=note["id"])

    def color_menu(self) -> None:
        menu = QMenu(self)
        for color in NOTE_COLORS:
            action = menu.addAction(f"●  {color.upper()}"); action.setData(color)
        menu.addSeparator(); custom = menu.addAction("직접 색상 고르기…")
        chosen = menu.exec(self.color_btn.mapToGlobal(self.color_btn.rect().bottomLeft()))
        if chosen == custom:
            current = self.store.by_id(self.current_id); color = QColorDialog.getColor(QColor(current["color"]), self, "메모 색상")
            if color.isValid(): self.set_note_color(color.name())
        elif chosen: self.set_note_color(chosen.data())

    def apply_palette(self, colors: list[str]) -> None:
        self.save_current(False)
        for index, note in enumerate(self.store.notes):
            note["color"] = colors[index % len(colors)]
            if note["id"] in self.stickies: self.stickies[note["id"]].refresh_style()
        self.store.save(); self.refresh_list(select_id=self.current_id); self.color_btn.setStyleSheet(f"color: {self.store.by_id(self.current_id)['color']}; font-size: 19px;")
        self.status.setText("전체 메모 테마를 변경했습니다")

    def more_menu(self) -> None:
        menu = QMenu(self); theme_menu = menu.addMenu("전체 메모 테마")
        theme_actions = {}
        for name in THEMES:
            theme_actions[theme_menu.addAction(name)] = name
        menu.addSeparator(); export = menu.addAction("백업 내보내기…"); imp = menu.addAction("백업 불러오기…"); location = menu.addAction("저장 위치 보기")
        chosen = menu.exec(self.more_button.mapToGlobal(self.more_button.rect().bottomLeft()))
        if chosen in theme_actions: self.apply_palette(THEMES[theme_actions[chosen]])
        elif chosen == export: self.export_backup()
        elif chosen == imp: self.import_backup()
        elif chosen == location: QMessageBox.information(self, "저장 위치", str(self.store.path))

    def export_backup(self) -> None:
        self.save_current(False); suggested = f"morrow-backup-{datetime.now().strftime('%Y%m%d')}.json"
        path, _ = QFileDialog.getSaveFileName(self, "백업 내보내기", suggested, "Morrow 백업 (*.json)")
        if path:
            payload = {"version": 2, "app": APP_NAME, "exported_at": now_iso(), "notes": self.store.notes}; Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"); self.status.setText("백업을 저장했습니다")

    def import_backup(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "백업 불러오기", "", "Morrow 백업 (*.json)")
        if not path: return
        try:
            notes = json.loads(Path(path).read_text(encoding="utf-8")).get("notes")
            if not isinstance(notes, list) or not notes: raise ValueError("올바른 백업 파일이 아닙니다.")
        except (OSError, json.JSONDecodeError, ValueError) as exc: QMessageBox.warning(self, "불러오기 오류", str(exc)); return
        if QMessageBox.question(self, "백업 불러오기", "현재 메모를 백업 내용으로 교체할까요?", QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes) != QMessageBox.StandardButton.Yes: return
        safety = self.store.path.with_name(f"notes-before-restore-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
        if self.store.path.exists(): safety.write_bytes(self.store.path.read_bytes())
        for sticky in list(self.stickies.values()): sticky.close()
        self.store.notes = [normalize_note(n) for n in notes]; self.current_id = None; self.store.save(); self.refresh_list(select_id=self.store.notes[0]["id"]); self.restore_stickies()

    def closeEvent(self, event) -> None:
        self.save_timer.stop(); self.save_current(False)
        pinned_stickies = [
            sticky for sticky in self.stickies.values()
            if sticky.note["window"].get("pinned") and sticky.isVisible()
        ]
        if pinned_stickies:
            for sticky in list(self.stickies.values()):
                if sticky not in pinned_stickies:
                    sticky.close()
            self.store.save()
            event.accept()
            return
        for sticky in list(self.stickies.values()):
            sticky.app_shutdown = True
            sticky.close()
        self.store.save(); event.accept()


def main() -> int:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Memo.Local.1")
        except (AttributeError, OSError):
            pass
    app = QApplication(sys.argv); app.setApplicationName(APP_NAME); app.setStyle("Fusion")
    icon_file = asset_path("memo-icon.ico")
    if icon_file.exists():
        app.setWindowIcon(QIcon(str(icon_file)))
    window = NotesWindow(); window.show(); return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
