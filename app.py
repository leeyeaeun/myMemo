from __future__ import annotations

import base64
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QEvent, QIODevice, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QImage, QTextCharFormat, QTextCursor, QTextImageFormat, QTextListFormat
from PySide6.QtWidgets import (
    QApplication, QColorDialog, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QPushButton, QSizeGrip, QSlider, QSplitter, QTextEdit, QToolButton, QVBoxLayout, QWidget,
)

APP_NAME = "Morrow Notes"
NOTE_COLORS = ["#FFF3B8", "#E9E0FF", "#DDF5E8", "#FFDDE3", "#DCEEFF", "#F2EEE8"]
THEMES = {
    "Sorbet": ["#FFD9E2", "#FFE7C2", "#FFF2B8", "#DDF5E8", "#DCEEFF", "#E9E0FF"],
    "Nordic": ["#E7ECEF", "#D6E4E5", "#E8E3DB", "#DFE7DD", "#E0E6EF", "#EEE9E4"],
    "Earth": ["#EADBC8", "#D8E2C5", "#C9D8D1", "#E6CFC2", "#D9D0E3", "#E8DEB5"],
    "Mono": ["#F6F6F4", "#ECECEA", "#E3E3E0", "#DADAD6", "#F0EFEB", "#E8E7E3"],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def storage_path() -> Path:
    folder = Path(os.getenv("LOCALAPPDATA", Path.home())) / APP_NAME
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "notes.json"


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


def image_data_uri(path: str) -> tuple[str, int, int] | None:
    image = QImage(path)
    if image.isNull():
        return None
    suffix = Path(path).suffix.lower()
    fmt = "JPEG" if suffix in {".jpg", ".jpeg"} else "PNG"
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, fmt)
    mime = "image/jpeg" if fmt == "JPEG" else "image/png"
    uri = f"data:{mime};base64,{base64.b64encode(bytes(data)).decode('ascii')}"
    return uri, image.width(), image.height()


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
        if not self.notes:
            note = new_note()
            note.update(title="첫 번째 메모", html="<p>여기에 가볍게 기록해 보세요.</p>")
            self.notes = [note]
            self.save()

    def save(self) -> None:
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
        uri, source_w, source_h = result
        width = min(source_w, max_width)
        image = QTextImageFormat()
        image.setName(uri)
        image.setWidth(width)
        image.setHeight(round(width * source_h / max(source_w, 1)))
        self.textCursor().insertImage(image)


class StickyWindow(QWidget):
    changed = Signal(str)
    closed = Signal(str)

    def __init__(self, note: dict, save_callback) -> None:
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.note = note
        self.save_callback = save_callback
        self.app_shutdown = False
        self.drag_origin: QPoint | None = None
        self.window_origin: QPoint | None = None
        self.loading = True
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(300)
        self.save_timer.timeout.connect(self.save_note)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setMinimumSize(240, 220)
        self.build_ui()
        self.restore_geometry()
        self.refresh_style()
        self.loading = False

    def build_ui(self) -> None:
        self.setObjectName("stickyWindow")
        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)

        self.header = QWidget()
        self.header.setObjectName("stickyHeader")
        self.header.setFixedHeight(46)
        row = QHBoxLayout(self.header)
        row.setContentsMargins(13, 7, 7, 5)
        self.title = QLineEdit(self.note.get("title", ""))
        self.title.setObjectName("stickyTitle")
        self.title.textChanged.connect(self.queue_save)
        row.addWidget(self.title, 1)
        self.format_btn = self.button("Aa", "서식 도구", self.toggle_toolbar)
        self.pin_btn = self.button("PIN", "항상 위 고정", self.toggle_pin)
        self.color_btn = self.button("●", "색상 변경", self.choose_color)
        self.close_btn = self.button("×", "메모 창 닫기", self.close)
        row.addWidget(self.format_btn)
        row.addWidget(self.pin_btn)
        row.addWidget(self.color_btn)
        row.addWidget(self.close_btn)
        root.addWidget(self.header)

        self.toolbar = QWidget()
        self.toolbar.setObjectName("stickyToolbar")
        tools = QHBoxLayout(self.toolbar)
        tools.setContentsMargins(9, 3, 9, 5)
        tools.setSpacing(1)
        tools.addWidget(self.button("B", "굵게", self.bold))
        tools.addWidget(self.button("U", "밑줄", self.underline))
        self.size_box = QComboBox()
        self.size_box.addItems(["12", "14", "16", "18", "22", "28", "36"])
        self.size_box.setCurrentText("16")
        self.size_box.activated.connect(self.font_size)
        tools.addWidget(self.size_box)
        tools.addWidget(self.button("L", "왼쪽 정렬", lambda: self.editor.setAlignment(Qt.AlignmentFlag.AlignLeft)))
        tools.addWidget(self.button("C", "가운데 정렬", lambda: self.editor.setAlignment(Qt.AlignmentFlag.AlignCenter)))
        tools.addWidget(self.button("R", "오른쪽 정렬", lambda: self.editor.setAlignment(Qt.AlignmentFlag.AlignRight)))
        tools.addWidget(self.button("IMG", "이미지", self.add_image))
        tools.addStretch()
        root.addWidget(self.toolbar)
        self.toolbar.hide()

        self.editor = RichEditor()
        self.editor.setObjectName("stickyEditor")
        self.editor.setHtml(self.note.get("html", ""))
        self.editor.textChanged.connect(self.queue_save)
        self.editor.image_resize_requested.connect(lambda: resize_image(self, self.editor))
        root.addWidget(self.editor, 1)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(10, 2, 4, 4)
        self.saved = QLabel("자동 저장")
        self.saved.setObjectName("stickySaved")
        bottom.addWidget(self.saved)
        bottom.addStretch()
        bottom.addWidget(QSizeGrip(self))
        root.addLayout(bottom)

        self.header.installEventFilter(self)
        self.title.installEventFilter(self)

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

    def toggle_toolbar(self) -> None:
        self.toolbar.setVisible(not self.toolbar.isVisible())

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

    def underline(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontUnderline(not self.editor.fontUnderline())
        self.char_format(fmt)

    def font_size(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontPointSize(float(self.size_box.currentText()))
        self.char_format(fmt)

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

    def refresh_style(self) -> None:
        bg = self.note.get("color", NOTE_COLORS[0])
        fg = text_color(bg)
        self.setStyleSheet(f"""
            #stickyWindow {{ background: {bg}; border: 1px solid rgba(0,0,0,35); }}
            #stickyHeader, #stickyToolbar {{ background: transparent; }}
            #stickyTitle {{ border: none; background: transparent; color: {fg}; font-size: 15px; font-weight: 700; padding: 2px; }}
            #stickyEditor {{ border: none; background: transparent; color: {fg}; font-size: 16px; padding: 8px 13px; selection-background-color: rgba(50,90,180,90); }}
            QToolButton {{ border: none; background: rgba(255,255,255,70); color: {fg}; border-radius: 7px; padding: 3px 7px; font-size: 10px; }}
            QToolButton:hover {{ background: rgba(255,255,255,135); }}
            QComboBox {{ border: none; background: rgba(255,255,255,80); color: {fg}; border-radius: 6px; padding: 3px; min-width: 44px; }}
            #stickySaved {{ color: {fg}; opacity: .6; font-size: 10px; }}
        """)
        self.color_btn.setStyleSheet(f"color: {bg}; background: {fg};")

    def apply_pin(self, pinned: bool) -> None:
        self.note["window"]["pinned"] = pinned
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, pinned)
        self.pin_btn.setText("TOP" if pinned else "PIN")
        if self.isVisible():
            self.show()

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
        brand = QLabel("morrow"); brand.setObjectName("brand"); top_row.addWidget(brand); top_row.addStretch()
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
        self.list = QListWidget(); self.list.setObjectName("noteList"); self.list.setSpacing(7); self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.currentItemChanged.connect(self.select_note); self.list.itemDoubleClicked.connect(lambda _: self.open_current_sticky()); side.addWidget(self.list, 1)
        hint = QLabel("더블 클릭하면 바탕화면 메모로 열립니다"); hint.setObjectName("hint"); hint.setWordWrap(True); side.addWidget(hint)
        splitter.addWidget(sidebar)

        content = QWidget(); content.setObjectName("content")
        body = QVBoxLayout(content); body.setContentsMargins(30, 18, 30, 25); body.setSpacing(12)
        title_row = QHBoxLayout(); self.title_edit = QLineEdit(); self.title_edit.setObjectName("titleEdit"); self.title_edit.textChanged.connect(self.queue_save)
        title_row.addWidget(self.title_edit, 1)
        self.sticky_btn = self.button("▣", "스티키 창으로 열기", self.open_current_sticky)
        self.color_btn = self.button("●", "메모 색상", self.color_menu)
        title_row.addWidget(self.sticky_btn); title_row.addWidget(self.color_btn); title_row.addWidget(self.button("⌫", "메모 삭제", self.delete_note)); body.addLayout(title_row)

        toolbar = QWidget(); toolbar.setObjectName("toolbar"); tools = QHBoxLayout(toolbar); tools.setContentsMargins(8, 6, 8, 6); tools.setSpacing(2)
        tools.addWidget(self.button("B", "굵게", self.bold)); tools.addWidget(self.button("I", "기울임", self.italic)); tools.addWidget(self.button("U", "밑줄", self.underline))
        self.size_box = QComboBox(); self.size_box.addItems(["12", "14", "16", "18", "22", "28", "36", "48"]); self.size_box.setCurrentText("16"); self.size_box.activated.connect(self.font_size); tools.addWidget(self.size_box)
        tools.addWidget(self.button("L", "왼쪽 정렬", lambda: self.editor.setAlignment(Qt.AlignmentFlag.AlignLeft)))
        tools.addWidget(self.button("C", "가운데 정렬", lambda: self.editor.setAlignment(Qt.AlignmentFlag.AlignCenter)))
        tools.addWidget(self.button("R", "오른쪽 정렬", lambda: self.editor.setAlignment(Qt.AlignmentFlag.AlignRight)))
        tools.addWidget(self.button("•", "글머리표", self.bullets)); tools.addWidget(self.button("IMG", "이미지 삽입", self.add_image, 44)); tools.addStretch()
        tools.addWidget(self.button("↶", "실행 취소", lambda: self.editor.undo())); tools.addWidget(self.button("↷", "다시 실행", lambda: self.editor.redo())); body.addWidget(toolbar)
        self.editor = RichEditor(); self.editor.setObjectName("editor"); self.editor.textChanged.connect(self.queue_save); self.editor.image_resize_requested.connect(lambda: resize_image(self, self.editor)); body.addWidget(self.editor, 1)
        self.status = QLabel("모든 변경사항은 자동으로 저장됩니다"); self.status.setObjectName("status"); body.addWidget(self.status)
        splitter.addWidget(content); splitter.setSizes([290, 770])

        shortcuts = [("Ctrl+N", self.add_note), ("Ctrl+Shift+S", self.export_backup), ("Ctrl+Shift+O", self.import_backup), ("Ctrl+Shift+P", self.open_current_sticky)]
        for keys, callback in shortcuts:
            action = self.addAction
            from PySide6.QtGui import QAction
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
        for note in sorted(self.store.notes, key=lambda n: n.get("updated_at", ""), reverse=True):
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
        self.stickies[note["id"]] = sticky; self.store.save(); sticky.show(); sticky.raise_(); self.refresh_list(select_id=note["id"])

    def sticky_closed(self, note_id: str) -> None:
        self.stickies.pop(note_id, None); self.refresh_list(select_id=self.current_id)

    def restore_stickies(self) -> None:
        for note in self.store.notes:
            if note["window"].get("open"):
                self.open_sticky(note)
        self.refresh_list(select_id=self.current_id)

    def add_note(self) -> None:
        self.save_current(False); note = new_note(NOTE_COLORS[len(self.store.notes) % len(NOTE_COLORS)]); self.store.notes.append(note); self.store.save(); self.search.clear(); self.refresh_list(select_id=note["id"]); self.title_edit.selectAll(); self.title_edit.setFocus()

    def delete_note(self) -> None:
        note = self.store.by_id(self.current_id)
        if not note: return
        if QMessageBox.question(self, "메모 삭제", f"‘{note['title']}’ 메모를 삭제할까요?", QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes) != QMessageBox.StandardButton.Yes: return
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
    def font_size(self):
        fmt = QTextCharFormat(); fmt.setFontPointSize(float(self.size_box.currentText())); self.format_chars(fmt)
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
        for sticky in list(self.stickies.values()):
            sticky.app_shutdown = True
            sticky.close()
        self.store.save(); event.accept()


def main() -> int:
    app = QApplication(sys.argv); app.setApplicationName(APP_NAME); app.setStyle("Fusion")
    window = NotesWindow(); window.show(); return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
