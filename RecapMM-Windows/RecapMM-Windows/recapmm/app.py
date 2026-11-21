"""RecapMM for Windows — Liquid Glass window (PySide6). No server, no subscription: Whisper + Gemini + TTS + ffmpeg on this PC."""
import os
import subprocess
import sys
import threading
import traceback
from math import cos, sin

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QUrl, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QDesktopServices, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
                           QPixmap, QRadialGradient)
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QScrollArea, QSlider, QSpinBox, QStackedWidget, QVBoxLayout,
                               QWidget)

from . import __version__, config, media
from .gemini import KeyPoolExhausted, Stopped, redact
from . import clone, dubbing, movie, subtitle, textimg, tools
from .pipeline import LANGS, WHISPER_MODELS, Pipeline
from .tts import VOICES

AQUA, VIOLET, ROSE = QColor("#3FE6D0"), QColor("#8A6BFF"), QColor("#FF7AB0")
VIDEO_EXT = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".flv", ".ts")
SHADOW = 16     # room around every glass card for its soft shadow


# ------------------------------------------------------------------ look
def _assets():
    """Small arrow images (drawn once, referenced from the style sheet). Returns (down, up) file paths."""
    d = os.path.join(config.APPDIR, "assets")
    os.makedirs(d, exist_ok=True)
    out = []
    for name, pts in (("arrow.png", [QPointF(6, 9), QPointF(12, 15), QPointF(18, 9)]),
                      ("arrow_up.png", [QPointF(6, 15), QPointF(12, 9), QPointF(18, 15)])):
        p = os.path.join(d, name)
        pm = QPixmap(24, 24)
        pm.fill(Qt.GlobalColor.transparent)
        q = QPainter(pm)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        q.setPen(QPen(QColor(244, 246, 255, 220), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        q.drawPolyline(pts)
        q.end()
        pm.save(p)
        out.append(p.replace("\\", "/"))
    return tuple(out)


def style_sheet(assets):
    arrow, up = assets
    return """
* { color: #F4F6FF; }
QLabel { background: transparent; }
QLabel#title { font-size: 26px; font-weight: 700; }
QLabel#sub { color: rgba(244,246,255,0.70); }
QLabel#card { font-size: 16px; font-weight: 600; }
QLabel#hint { color: rgba(244,246,255,0.62); font-size: 12px; }
QLabel#badge { background: rgba(255,255,255,0.20); border: 1px solid rgba(255,255,255,0.45); border-radius: 10px;
               padding: 2px 10px; font-size: 11px; font-weight: 700; }
QLineEdit, QPlainTextEdit, QSpinBox, QComboBox {
    background: rgba(255,255,255,0.10); border: 1px solid rgba(255,255,255,0.30); border-radius: 14px;
    padding: 7px 12px; selection-background-color: rgba(63,230,208,0.55); }
QPlainTextEdit { border-radius: 16px; padding: 10px 12px; }
QSpinBox { padding: 6px 30px 6px 12px; min-height: 22px; }
QSpinBox QLineEdit, QAbstractSpinBox QLineEdit, QLineEdit#qt_spinbox_lineedit { background: transparent; border: none; padding: 0px; margin: 0px; border-radius: 0px; }
QSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 26px; border: none; background: transparent; margin-top: 3px; }
QSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 26px; border: none; background: transparent; margin-bottom: 3px; }
QSpinBox::up-arrow { image: url(@UP@); width: 12px; height: 12px; }
QSpinBox::down-arrow { image: url(@ARROW@); width: 12px; height: 12px; }
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus { border: 1px solid rgba(63,230,208,0.95); }
QComboBox { min-height: 22px; padding-right: 30px; }
QComboBox::drop-down { border: none; width: 30px; }
QComboBox::down-arrow { image: url(@ARROW@); width: 14px; height: 14px; }
QComboBox QAbstractItemView { background: #1a1650; border: 1px solid rgba(255,255,255,0.30); outline: 0;
    selection-background-color: rgba(63,230,208,0.40); padding: 4px; }
QPushButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 rgba(255,255,255,56), stop:1 rgba(255,255,255,20));
    border: 1px solid rgba(255,255,255,0.45); border-radius: 21px; padding: 10px 22px; font-weight: 600; }
QPushButton:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 rgba(255,255,255,84), stop:1 rgba(255,255,255,34)); }
QPushButton:pressed { background: rgba(255,255,255,0.10); }
QPushButton:disabled { color: rgba(244,246,255,0.40); border: 1px solid rgba(255,255,255,0.15); background: rgba(255,255,255,0.05); }
QPushButton#primary { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 rgba(63,230,208,170), stop:1 rgba(138,107,255,160));
    border: 1px solid rgba(255,255,255,0.65); }
QPushButton#primary:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 rgba(63,230,208,220), stop:1 rgba(138,107,255,210)); }
QPushButton#primary:disabled { background: rgba(255,255,255,0.06); }
QPushButton#chip { padding: 8px 26px; border-radius: 19px; background: rgba(255,255,255,0.08); }
QPushButton#chip:checked { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 rgba(255,255,255,96), stop:1 rgba(255,255,255,44));
    border: 1px solid rgba(255,255,255,0.8); }
QProgressBar { background: rgba(255,255,255,0.12); border: 1px solid rgba(255,255,255,0.28); border-radius: 10px;
    min-height: 20px; max-height: 20px; text-align: center; font-weight: 600; }
QProgressBar::chunk { border-radius: 9px; background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #3FE6D0, stop:1 #8A6BFF); }
QCheckBox { spacing: 10px; background: transparent; }
QCheckBox::indicator { width: 20px; height: 20px; border-radius: 7px; border: 1px solid rgba(255,255,255,0.45);
    background: rgba(255,255,255,0.10); }
QCheckBox::indicator:checked { border: 1px solid rgba(255,255,255,0.80);
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #3FE6D0, stop:1 #8A6BFF); }
QSlider::groove:horizontal { height: 6px; border-radius: 3px; background: rgba(255,255,255,0.20); }
QSlider::sub-page:horizontal { border-radius: 3px; background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #3FE6D0, stop:1 #8A6BFF); }
QSlider::handle:horizontal { width: 20px; height: 20px; margin: -8px 0; border-radius: 10px; background: #F4F6FF;
    border: 1px solid rgba(255,255,255,0.9); }
QListWidget { background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.28); border-radius: 16px; padding: 6px; outline: 0; }
QListWidget::item { padding: 5px 8px; border-radius: 8px; }
QListWidget::item:selected { background: rgba(63,230,208,0.30); }
QScrollArea, QScrollArea > QWidget > QWidget#page { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: rgba(255,255,255,0.28); border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QMessageBox, QDialog { background: #14123f; }
QToolTip { background: #1a1650; border: 1px solid rgba(255,255,255,0.3); padding: 4px; }
""".replace("@ARROW@", arrow).replace("@UP@", up)


class LiquidBackground(QWidget):
    """Deep gradient with three slowly drifting colour blobs — the liquid the glass sits on (same recipe as the phone app)."""

    def __init__(self):
        super().__init__()
        self.a = self.b = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(50)

    def tick(self):
        self.a = (self.a + 0.01209) % 6.2832      # ~26 s per turn (50 ms ticks)
        self.b = (self.b + 0.00924) % 6.2832      # ~34 s per turn
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0.0, QColor("#090D24"))
        g.setColorAt(0.5, QColor("#171046"))
        g.setColorAt(1.0, QColor("#07263E"))
        p.fillRect(self.rect(), g)
        s = max(w, h)

        def blob(c, cx, cy, r):
            rg = QRadialGradient(QPointF(cx, cy), r)
            c0, c1 = QColor(c), QColor(c)
            c0.setAlphaF(0.60)
            c1.setAlphaF(0.0)
            rg.setColorAt(0.0, c0)
            rg.setColorAt(1.0, c1)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(rg))
            p.drawEllipse(QPointF(cx, cy), r, r)

        a, b = self.a, self.b
        blob(VIOLET, w * (0.25 + 0.18 * cos(a)), h * (0.20 + 0.10 * sin(a)), s * 0.62)
        blob(AQUA, w * (0.80 + 0.12 * sin(b)), h * (0.45 + 0.12 * cos(b)), s * 0.58)
        blob(ROSE, w * (0.30 + 0.15 * sin(a + 2.0)), h * (0.85 + 0.06 * cos(b)), s * 0.52)


class Glass(QFrame):
    """A frosted-glass card: translucent gradient fill, bright gradient edge, specular highlight, soft shadow."""

    def __init__(self, title=None, radius=26, badge=None):
        super().__init__()
        self.r = radius
        self._key, self._pix = None, None
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(SHADOW + 20, SHADOW + 16, SHADOW + 20, SHADOW + 18)
        self.lay.setSpacing(10)
        if title:
            row = QHBoxLayout()
            t = QLabel(title)
            t.setObjectName("card")
            row.addWidget(t)
            row.addStretch(1)
            if badge:
                bl = QLabel(badge)
                bl.setObjectName("badge")
                row.addWidget(bl)
            self.lay.addLayout(row)

    def paintEvent(self, _):
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr)
        if key != self._key:                                   # re-draw only when the size changes
            pm = QPixmap(max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr)))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.GlobalColor.transparent)
            q = QPainter(pm)
            self._draw(q)
            q.end()
            self._pix, self._key = pm, key
        p = QPainter(self)
        p.drawPixmap(0, 0, self._pix)
        p.end()

    def _draw(self, p):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 0, self.width(), self.height()).adjusted(SHADOW, SHADOW, -SHADOW, -SHADOW)
        # soft shadow: outlines that fade away from the card (never under the translucent glass itself)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(SHADOW, 2, -1):
            a = int(38 * (1 - i / SHADOW) ** 2)
            p.setPen(QPen(QColor(0, 0, 0, a), 1))
            p.drawRoundedRect(rect.adjusted(-i, -i + 4, i, i + 4), self.r + i, self.r + i)
        path = QPainterPath()
        path.addRoundedRect(rect, self.r, self.r)
        fill = QLinearGradient(rect.topLeft(), rect.bottomRight())
        fill.setColorAt(0, QColor(255, 255, 255, 52))
        fill.setColorAt(1, QColor(255, 255, 255, 15))
        p.fillPath(path, fill)
        p.save()
        p.setClipPath(path)
        hl = QLinearGradient(0, rect.top(), 0, rect.top() + rect.height() * 0.30)
        hl.setColorAt(0, QColor(255, 255, 255, 42))
        hl.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillRect(rect, hl)
        p.restore()
        edge = QLinearGradient(rect.topLeft(), rect.bottomRight())
        edge.setColorAt(0.0, QColor(255, 255, 255, 204))
        edge.setColorAt(0.5, QColor(255, 255, 255, 22))
        edge.setColorAt(1.0, QColor(255, 255, 255, 97))
        p.setPen(QPen(QBrush(edge), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)


class Bus(QObject):
    """Worker thread -> window (queued signals)."""
    log = Signal(str)
    stage = Signal(str)
    prog = Signal(float)
    done = Signal(str)


def _row(*widgets, stretch_first=True):
    h = QHBoxLayout()
    h.setSpacing(10)
    for i, w in enumerate(widgets):
        h.addWidget(w, 1 if (i == 0 and stretch_first) else 0)
    return h


def _labeled(text, widget, hint=None):
    box = QVBoxLayout()
    box.setSpacing(4)
    l = QLabel(text)
    box.addWidget(l)
    box.addWidget(widget)
    if hint:
        h = QLabel(hint)
        h.setObjectName("hint")
        h.setWordWrap(True)
        box.addWidget(h)
    return box


def _combo(values, current):
    c = QComboBox()
    c.addItems([str(v) for v in values])
    c.setCurrentText(str(current))
    return c


def _spin(lo, hi, val, width=90):
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(int(val))
    s.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
    s.setFixedWidth(max(width, 110))
    return s


def _slider(lo, hi, val, suffix):
    box = QHBoxLayout()
    s = QSlider(Qt.Orientation.Horizontal)
    s.setRange(lo, hi)
    s.setValue(int(val))
    lab = QLabel()
    lab.setFixedWidth(64)
    lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    s.valueChanged.connect(lambda v: lab.setText("%+d%s" % (v, suffix) if lo < 0 else "%d%s" % (v, suffix)))
    lab.setText("%+d%s" % (s.value(), suffix) if lo < 0 else "%d%s" % (s.value(), suffix))
    box.addWidget(s, 1)
    box.addWidget(lab)
    return s, box


# ------------------------------------------------------------------ tools (pages)
AUDIO_FILTER = "Audio / Video (*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.mp4 *.mkv *.mov *.webm);;All files (*.*)"
VIDEO_FILTER = "Video (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.flv *.ts);;All files (*.*)"
IMAGE_FILTER = "Image (*.png *.jpg *.jpeg *.webp);;All files (*.*)"


def _file_row(placeholder, button_text="ရွေးမယ်…"):
    line = QLineEdit()
    line.setPlaceholderText(placeholder)
    btn = QPushButton(button_text)
    return line, btn, _row(line, btn)


class Tile(Glass):
    """A tool on the home screen: glass card, big symbol, name, one-line help, optional corner tag."""
    clicked = Signal()

    def __init__(self, icon, title, sub, badge=None, enabled=True):
        super().__init__(radius=24)
        self.enabled_tool = enabled
        self.lay.setContentsMargins(SHADOW + 18, SHADOW + 12, SHADOW + 18, SHADOW + 14)
        self.lay.setSpacing(4)
        top = QHBoxLayout()
        ic = QLabel(icon)
        ic.setStyleSheet("font-size: 26px;")
        top.addWidget(ic)
        top.addStretch(1)
        if badge:
            b = QLabel(badge)
            b.setObjectName("badge")
            top.addWidget(b, 0, Qt.AlignmentFlag.AlignTop)
        self.lay.addLayout(top)
        t = QLabel(title)
        t.setObjectName("card")
        self.lay.addWidget(t)
        s = QLabel(sub)
        s.setObjectName("hint")
        s.setWordWrap(True)
        self.lay.addWidget(s)
        if enabled:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            for w in (ic, t, s):
                w.setStyleSheet(w.styleSheet() + " color: rgba(244,246,255,0.45);")

    def mouseReleaseEvent(self, e):
        if self.enabled_tool and e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class ToolBase(QWidget):
    """Shared page: back button, scrollable cards, a run panel (stage / progress / start / stop) and a log."""
    title, icon = "", ""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.bus = Bus()
        self.bus.log.connect(lambda s: self.logbox.appendPlainText(s))
        self.bus.stage.connect(lambda s: self.stage.setText(s))
        self.bus.prog.connect(lambda f: self.bar.setValue(int(f * 1000)))
        self.bus.done.connect(self._on_done)
        self.stop_flag = threading.Event()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)
        top = QHBoxLayout()
        top.setContentsMargins(4, 0, 0, 0)
        back = QPushButton("←  ပြန်")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(app.go_home)
        ttl = QLabel("%s  %s" % (self.icon, self.title))
        ttl.setObjectName("card")
        top.addWidget(back)
        top.addSpacing(10)
        top.addWidget(ttl)
        top.addStretch(1)
        outer.addLayout(top)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc.viewport().setAutoFillBackground(False)
        page = QWidget()
        page.setObjectName("page")
        self.lay = QVBoxLayout(page)
        self.lay.setContentsMargins(0, 0, 6, 0)
        self.lay.setSpacing(-SHADOW + 2)
        sc.setWidget(page)
        outer.addWidget(sc, 1)

        self.build()          # tool-specific cards

        run = Glass()
        self.stage = QLabel("အဆင်သင့်")
        run.lay.addWidget(self.stage)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        run.lay.addWidget(self.bar)
        self.btn_start = QPushButton("▶   စတင်မယ်")
        self.btn_start.setObjectName("primary")
        self.btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start.clicked.connect(self._start)
        self.btn_stop = QPushButton("■   ရပ်မယ်")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop)
        btn_open = QPushButton("📁  Output folder")
        btn_open.clicked.connect(app.open_out)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self.btn_start)
        row.addWidget(self.btn_stop)
        row.addStretch(1)
        row.addWidget(btn_open)
        self.run_row = row
        run.lay.addLayout(row)
        self.lay.addWidget(run)
        lg = Glass("မှတ်တမ်း")
        self.logbox = QPlainTextEdit()
        self.logbox.setReadOnly(True)
        self.logbox.setMaximumBlockCount(2000)
        self.logbox.setFixedHeight(170)
        lg.lay.addWidget(self.logbox)
        self.lay.addWidget(lg)
        self.lay.addStretch(1)
        self.post_build()

    # --- to be provided by each tool
    def build(self):
        raise NotImplementedError

    def post_build(self):
        pass

    def job(self, cfg, out):
        """Validate the inputs (raise ValueError with a message) and return fn(log, stage, prog, cancelled) -> path."""
        raise NotImplementedError

    def needs_gemini(self):
        return False

    # --- run
    def _start(self):
        cfg = self.app.collect()
        if self.needs_gemini() and not [k for k in cfg["keys"].split("\n") if k.strip()]:
            self.app.show_settings()
            QMessageBox.warning(self, "RecapMM", "Gemini API key ထည့်ပါ")
            return
        try:
            fn = self.job(cfg, self.app.out_dir())
        except ValueError as e:
            QMessageBox.warning(self, "RecapMM", str(e))
            return
        self.app.cfg = cfg
        config.save(cfg)
        self.stop_flag.clear()
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.bar.setValue(0)
        threading.Thread(target=self._work, args=(fn,), daemon=True).start()

    def _stop(self):
        self.stop_flag.set()
        self.bus.log.emit("⏹ ရပ်နေတယ်… (ပြီးသမျှ သိမ်းထားပါတယ်၊ ပြန်စရင် ဆက်လုပ်မယ်)")

    def _work(self, fn):
        result = ""
        try:
            result = fn(self.bus.log.emit, self.bus.stage.emit, self.bus.prog.emit, self.stop_flag.is_set) or ""
        except (Stopped, InterruptedError):
            self.bus.log.emit("⏹ ရပ်လိုက်ပါပြီ — ပြန်စတင်ရင် ရပ်ခဲ့တဲ့နေရာကနေ ဆက်မယ်")
        except KeyPoolExhausted as e:
            self.bus.log.emit("⛔ " + str(e))
        except Exception as e:
            self.bus.log.emit("❌ " + redact(str(e)))
            self.bus.log.emit(redact(traceback.format_exc())[-800:])
        self.bus.done.emit(result)

    def _on_done(self, path):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        if path:
            self.stage.setText("✅ ပြီးပါပြီ")
            QMessageBox.information(self, "RecapMM", "ပြီးပါပြီ\n" + path)


class RecapPage(ToolBase):
    title, icon = "Recap", "🎬"

    def needs_gemini(self):
        return True

    def build(self):
        g = Glass("ဗီဒီယို")
        self.src, b, row = _file_row("ဗီဒီယိုကို ဒီ window ပေါ် drag လုပ်ပါ (သို့) ရွေးပါ")
        b.clicked.connect(lambda: self._pick())
        g.lay.addLayout(row)
        yt = QHBoxLayout()
        ytl = QLabel("🔗  YouTube Link")
        ytl.setStyleSheet("color: rgba(244,246,255,0.55);")
        yb = QLabel("Coming Soon")
        yb.setObjectName("badge")
        yt.addWidget(ytl)
        yt.addStretch(1)
        yt.addWidget(yb)
        g.lay.addLayout(yt)
        self.lay.addWidget(g)

    def _pick(self):
        p, _ = QFileDialog.getOpenFileName(self, "ဗီဒီယို ရွေးပါ", "", VIDEO_FILTER)
        if p:
            self.src.setText(p)

    def job(self, cfg, out):
        src = self.src.text().strip()
        if not os.path.isfile(src):
            raise ValueError("ဗီဒီယို ဖိုင် ရွေးပါ")
        return lambda log, stage, prog, can: Pipeline(cfg, src, out, log, lambda s, f: stage(s), prog, can, config.JOBS).run()


class SrtPage(ToolBase):
    title, icon = "SRT — စာတန်းဖိုင် ထုတ်", "💬"

    def needs_gemini(self):
        return self.translate.isChecked()

    def build(self):
        g = Glass("ဗီဒီယို / အသံ ဖိုင်")
        self.src, b, row = _file_row("ဗီဒီယို (သို့) အသံဖိုင် ရွေးပါ")
        b.clicked.connect(self._pick)
        g.lay.addLayout(row)
        self.translate = QCheckBox("မြန်မာလို ဘာသာပြန်မယ်  (မအမှတ်ရင် မူရင်းစကားအတိုင်း)")
        self.translate.setChecked(True)
        g.lay.addWidget(self.translate)
        h = QLabel("မူရင်းဘာသာ၊ Whisper model၊ Gemini key ကို ဆက်တင်မှာ ရွေးပါ။")
        h.setObjectName("hint")
        g.lay.addWidget(h)
        self.lay.addWidget(g)

    def _pick(self):
        p, _ = QFileDialog.getOpenFileName(self, "ဖိုင် ရွေးပါ", "", AUDIO_FILTER)
        if p:
            self.src.setText(p)

    def job(self, cfg, out):
        src = self.src.text().strip()
        if not os.path.isfile(src):
            raise ValueError("ဖိုင် ရွေးပါ")
        tr = self.translate.isChecked()
        return lambda log, stage, prog, can: tools.run_srt(cfg, src, tr, out, log, lambda s, f: stage(s), prog, can, config.JOBS)


class TtsPage(ToolBase):
    title, icon = "TTS — စာကို အသံ ပြောင်း", "🔊"

    def build(self):
        g = Glass("မြန်မာစာ")
        self.text = QPlainTextEdit()
        self.text.setFixedHeight(230)
        self.text.setPlaceholderText("ဖတ်ခိုင်းချင်တဲ့ မြန်မာစာကို ဒီမှာ ရိုက်ထည့်ပါ…")
        g.lay.addWidget(self.text)
        h = QLabel("Engine / အသံ / အမြန်နှုန်းကို ဆက်တင်မှာ ရွေးပါ။ ရလဒ်က .mp3 ဖိုင်။")
        h.setObjectName("hint")
        g.lay.addWidget(h)
        self.lay.addWidget(g)

    def job(self, cfg, out):
        text = self.text.toPlainText().strip()
        if not text:
            raise ValueError("စာ ရိုက်ထည့်ပါ")
        return lambda log, stage, prog, can: tools.run_tts(cfg, text, out, log, lambda s, f: stage(s), prog, can, config.JOBS)


class SplitPage(ToolBase):
    title, icon = "Splitter — အပိုင်းလိုက် ဖြတ်", "✂️"

    def build(self):
        g = Glass("ဗီဒီယို")
        self.src, b, row = _file_row("ဖြတ်မယ့် ဗီဒီယို ရွေးပါ")
        b.clicked.connect(self._pick)
        g.lay.addLayout(row)
        self.mins = _spin(1, 240, 10)
        self.ep = _spin(1, 999, 1)
        r = QHBoxLayout()
        r.addWidget(QLabel("အပိုင်းတစ်ခု အရှည် (မိနစ်)"))
        r.addStretch(1)
        r.addWidget(self.mins)
        g.lay.addLayout(r)
        r = QHBoxLayout()
        r.addWidget(QLabel("EP နံပါတ် စတင်မယ့်နေရာ"))
        r.addStretch(1)
        r.addWidget(self.ep)
        g.lay.addLayout(r)
        h = QLabel("ပုံ/အသံ ပြန်မ encode ဘူး (အရည်အသွေးမကျ၊ စက္ကန့်ပိုင်းနဲ့ပြီး)။ ဖြတ်ချက်တွေက key frame ပေါ်ကျလို့ တစ်ပိုင်းနဲ့ တစ်ပိုင်း စက္ကန့်အနည်းငယ် ကွာနိုင်တယ်။")
        h.setObjectName("hint")
        h.setWordWrap(True)
        g.lay.addWidget(h)
        self.lay.addWidget(g)

    def _pick(self):
        p, _ = QFileDialog.getOpenFileName(self, "ဗီဒီယို ရွေးပါ", "", VIDEO_FILTER)
        if p:
            self.src.setText(p)

    def job(self, cfg, out):
        src = self.src.text().strip()
        if not os.path.isfile(src):
            raise ValueError("ဗီဒီယို ရွေးပါ")
        m, e = self.mins.value(), self.ep.value()
        return lambda log, stage, prog, can: tools.run_split(cfg, src, m, e, out, log, lambda s, f: stage(s), prog, can, config.JOBS)


class DhammaPage(ToolBase):
    title, icon = "Dhamma Studio — တရား ဗီဒီယို", "☸️"
    COLORS = {"အဖြူ": "#FFFFFF", "အဝါ": "#FFD84D", "အစိမ်း (Aqua)": "#3FE6D0", "ပန်းရောင်": "#FF9EC4"}

    def build(self):
        g = Glass("Video clip များ")
        self.clips = QListWidget()
        self.clips.setFixedHeight(130)
        g.lay.addWidget(self.clips)
        r = QHBoxLayout()
        add = QPushButton("＋ ထည့်မယ်…")
        add.clicked.connect(self._add)
        rm = QPushButton("ဖယ်မယ်")
        rm.clicked.connect(lambda: [self.clips.takeItem(self.clips.row(i)) for i in self.clips.selectedItems()])
        clr = QPushButton("အကုန်ဖျက်")
        clr.clicked.connect(self.clips.clear)
        for b in (add, rm, clr):
            r.addWidget(b)
        r.addStretch(1)
        g.lay.addLayout(r)
        self.audio, b, row = _file_row("တရားအသံဖိုင် (mp3 / wav / m4a …)")
        b.clicked.connect(self._audio)
        g.lay.addLayout(row)
        h = QLabel("Clip တွေကို ကျပန်းစီမယ် (အကုန်သုံးပြီးမှ ပြန်စ)၊ clip ရဲ့ မူရင်းအသံ ဖြုတ်ပြီး audio တစ်ခုတည်းပဲ ကြားရမယ်။")
        h.setObjectName("hint")
        h.setWordWrap(True)
        g.lay.addWidget(h)
        self.lay.addWidget(g)

        f = Glass("ဗီဒီယို ပုံစံ")
        self.ratio = _combo(["9:16 (ဒေါင်လိုက်)", "16:9 (ကျယ်)", "1:1 (လေးထောင့်)"], "9:16 (ဒေါင်လိုက်)")
        self.short = _combo([480, 720, 1080], 720)
        r = QHBoxLayout()
        r.setSpacing(10)
        r.addLayout(_labeled("အချိုး", self.ratio), 1)
        r.addLayout(_labeled("ဘေးတိုဘက် (px)", self.short), 1)
        f.lay.addLayout(r)
        self.lay.addWidget(f)

        t = Glass("ပြေးစာ (ticker)")
        self.tick = QLineEdit()
        self.tick.setPlaceholderText("အောက်ဘက်မှာ ညာကနေ ဘယ်ကို ပြေးမယ့် စာ (ဗလာ = မထည့်)")
        t.lay.addWidget(self.tick)
        self.tspeed, sl = _slider(3, 40, 12, "")
        t.lay.addWidget(QLabel("အမြန်နှုန်း"))
        t.lay.addLayout(sl)
        self.tsize, sl = _slider(4, 14, 7, "%")
        t.lay.addWidget(QLabel("ဘားအမြင့်"))
        t.lay.addLayout(sl)
        self.tcolor = _combo(list(self.COLORS), "အဖြူ")
        t.lay.addLayout(_labeled("စာအရောင်", self.tcolor))
        self.lay.addWidget(t)

        b_ = Glass("ခေါင်းစဉ် / Intro / Outro / Logo")
        self.title_in = QLineEdit()
        self.title_in.setPlaceholderText("ခေါင်းစဉ်")
        self.sub_in = QLineEdit()
        self.sub_in.setPlaceholderText("ခေါင်းစဉ်ခွဲ (မထည့်လည်းရ)")
        b_.lay.addWidget(self.title_in)
        b_.lay.addWidget(self.sub_in)
        self.tmode = _combo(["ခေါင်းစဉ် မပြ", "ပထမ ၈ စက္ကန့်", "ဗီဒီယိုတစ်ခုလုံး"], "ခေါင်းစဉ် မပြ")
        b_.lay.addLayout(_labeled("ခေါင်းစဉ် ပြပုံ", self.tmode))
        self.intro = QCheckBox("Intro card (၃ စက္ကန့်)")
        self.outro = QCheckBox("Outro card (၄ စက္ကန့်)")
        b_.lay.addWidget(self.intro)
        b_.lay.addWidget(self.outro)
        self.outro_text = QLineEdit()
        self.outro_text.setPlaceholderText("Outro စာ  (ဥပမာ - သာဓု သာဓု သာဓု)")
        b_.lay.addWidget(self.outro_text)
        self.logo, lb, lrow = _file_row("Logo ပုံ (မထည့်လည်းရ)")
        lb.clicked.connect(self._logo)
        b_.lay.addLayout(lrow)
        self.logo_pos = _combo(["ညာဘက်အပေါ်", "ဘယ်ဘက်အပေါ်"], "ညာဘက်အပေါ်")
        b_.lay.addLayout(_labeled("Logo နေရာ", self.logo_pos))
        self.lay.addWidget(b_)

    def _add(self):
        ps, _ = QFileDialog.getOpenFileNames(self, "Video clip များ ရွေးပါ", "", VIDEO_FILTER)
        have = {self.clips.item(i).text() for i in range(self.clips.count())}
        for p in ps:
            if p not in have:
                self.clips.addItem(p)

    def _audio(self):
        p, _ = QFileDialog.getOpenFileName(self, "Audio ရွေးပါ", "", AUDIO_FILTER)
        if p:
            self.audio.setText(p)

    def _logo(self):
        p, _ = QFileDialog.getOpenFileName(self, "Logo ရွေးပါ", "", IMAGE_FILTER)
        if p:
            self.logo.setText(p)

    def job(self, cfg, out):
        clips = [self.clips.item(i).text() for i in range(self.clips.count())]
        audio = self.audio.text().strip()
        if not clips:
            raise ValueError("Video clip တစ်ခုမှ မရွေးရသေးပါ")
        if not os.path.isfile(audio):
            raise ValueError("Audio ဖိုင် ရွေးပါ")
        ratio = self.ratio.currentIndex()
        short = int(self.short.currentText())
        ticker = tools.Ticker(self.tick.text(), self.tspeed.value() / 100.0, self.tsize.value() / 100.0,
                              self.COLORS[self.tcolor.currentText()]) if self.tick.text().strip() else None
        brand = tools.Branding(self.title_in.text(), self.sub_in.text(), self.tmode.currentIndex(), self.intro.isChecked(),
                               self.outro.isChecked(), self.outro_text.text(), self.logo.text().strip(),
                               self.logo_pos.currentIndex() == 0)
        return lambda log, stage, prog, can: tools.run_dhamma(cfg, clips, audio, ratio, short, ticker, brand, out, log,
                                                              lambda s, f: stage(s), prog, can, config.JOBS, textimg=textimg)


class SubPage(ToolBase):
    """Subtitle Only: a whole folder of episodes -> each one comes back with Burmese subtitles (only while they speak) over a blur."""
    title, icon = "Subtitle Only — မြန်မာစာတန်းထိုး (အပိုင်းအများကြီး)", "📝"
    COLORS = {"အဖြူ": "#FFFFFF", "အဝါ": "#FFE14D"}

    def needs_gemini(self):
        return True

    def build(self):
        g = Glass("၁ · ဗီဒီယိုများ")
        self.files = QListWidget()
        self.files.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.files.setFixedHeight(230)
        g.lay.addWidget(self.files)
        self.count = QLabel("")
        self.count.setObjectName("hint")
        g.lay.addWidget(self.count)
        r = QHBoxLayout()
        r.setSpacing(10)
        b1 = QPushButton("＋  ဖိုင်များ…")
        b1.clicked.connect(self._add_files)
        b2 = QPushButton("📂  Folder (အားလုံး)")
        b2.clicked.connect(self._add_folder)
        b3 = QPushButton("ဖယ်မယ်")
        b3.clicked.connect(self._remove)
        b4 = QPushButton("အကုန်ဖျက်")
        b4.clicked.connect(lambda: (self.files.clear(), self._recount()))
        for b in (b1, b2, b3, b4):
            r.addWidget(b)
        r.addStretch(1)
        g.lay.addLayout(r)
        h = QLabel("ဖိုင်များ၊ folder (အထဲက subfolder အပါအဝင်) ကို window ပေါ် drag လုပ်လည်းရ။ EP1, EP2 … EP10 အစီအစဉ်အတိုင်း စီပေးမယ်။ "
                   "တစ်ပိုင်းချင်း အလုပ်လုပ်ပြီး အပိုင်းတိုင်း ကိုယ်ပိုင်ဖိုင် ပြန်ထွက်မယ်။ ဇာတ်ကောင်နာမည်တွေ အပိုင်းအကြား တူညီအောင် ရှေ့အပိုင်းက မှတ်ဉာဏ်ကို ဆက်သုံးမယ်။")
        h.setObjectName("hint")
        h.setWordWrap(True)
        g.lay.addWidget(h)
        self.lay.addWidget(g)

        s = Glass("၂ · စာတန်း ပုံစံ")
        self.mode = _combo(["ဗီဒီယိုပေါ် စာတန်းထိုး + SRT", "SRT ဖိုင်ပဲ ထုတ် (မြန်)"], "ဗီဒီယိုပေါ် စာတန်းထိုး + SRT")
        s.lay.addLayout(_labeled("ရလဒ်", self.mode))
        self.blur = QCheckBox("မူရင်းစာတန်းကို Blur နဲ့ အုပ်မယ်")
        self.blur.setChecked(True)
        s.lay.addWidget(self.blur)
        self.blur_when = _combo(["စာတန်းပြတဲ့အချိန်ပဲ", "ဗီဒီယိုတစ်ခုလုံး"], "စာတန်းပြတဲ့အချိန်ပဲ")
        s.lay.addLayout(_labeled("Blur ဘယ်အချိန်", self.blur_when))
        self.bottom, sl = _slider(0, 40, 5, "%")
        s.lay.addWidget(QLabel("နေရာ — အောက်ခြေကနေ မြင့်တာ"))
        s.lay.addLayout(sl)
        self.band, sl = _slider(6, 35, 16, "%")
        s.lay.addWidget(QLabel("Blur ဘားရဲ့ အမြင့် (မူရင်းစာတန်း အကုန်ဖုံးအောင်)"))
        s.lay.addLayout(sl)
        self.txt_size, sl = _slider(3, 9, 5, "%")
        s.lay.addWidget(QLabel("မြန်မာစာ အရွယ်အစား"))
        s.lay.addLayout(sl)
        self.color = _combo(list(self.COLORS), "အဖြူ")
        s.lay.addLayout(_labeled("စာအရောင်", self.color))
        self.box = QCheckBox("စာနောက်မှာ အမည်းရောင် ဘောင်ခံ (ဖတ်ရလွယ်)")
        self.box.setChecked(True)
        s.lay.addWidget(self.box)
        self.skip = QCheckBox("ပြီးသားဖိုင် ရှိရင် ကျော်မယ် (ရပ်ပြီးပြန်စရင် ဆက်လုပ်)")
        self.skip.setChecked(True)
        s.lay.addWidget(self.skip)
        h2 = QLabel("စာတန်းထိုးရင် ဗီဒီယိုကို encode ပြန်လုပ်ရလို့ ကြာတယ်၊ မူရင်းအသံ မပြောင်းပါ။ မူရင်းဘာသာ၊ Whisper model၊ Gemini key ကို ဆက်တင်မှာ ရွေးပါ။")
        h2.setObjectName("hint")
        h2.setWordWrap(True)
        s.lay.addWidget(h2)
        self.lay.addWidget(s)
        self._recount()

    def _recount(self):
        self.count.setText("ဗီဒီယို %d ခု" % self.files.count())

    def add_paths(self, paths):
        have = [self.files.item(i).text() for i in range(self.files.count())]
        allv = subtitle.collect(have + list(paths))
        self.files.clear()
        for f in allv:
            self.files.addItem(f)
        self._recount()

    def _add_files(self):
        ps, _ = QFileDialog.getOpenFileNames(self, "ဗီဒီယိုများ ရွေးပါ", "", VIDEO_FILTER)
        if ps:
            self.add_paths(ps)

    def _add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Folder ရွေးပါ")
        if d:
            self.add_paths([d])
            if not self.files.count():
                self.bus.log.emit("⚠️ ဒီ folder ထဲမှာ ဗီဒီယို မတွေ့ပါ")

    def _remove(self):
        for it in self.files.selectedItems():
            self.files.takeItem(self.files.row(it))
        self._recount()

    def job(self, cfg, out):
        files = [self.files.item(i).text() for i in range(self.files.count())]
        if not files:
            raise ValueError("ဗီဒီယို ထည့်ပါ (ဖိုင်များ သို့ folder)")
        opts = subtitle.SubOpts(burn=self.mode.currentIndex() == 0, blur=self.blur.isChecked(),
                                blur_always=self.blur_when.currentIndex() == 1, bottom_pct=float(self.bottom.value()),
                                band_pct=float(self.band.value()), size_pct=float(self.txt_size.value()),
                                color=self.COLORS[self.color.currentText()], box=self.box.isChecked(), skip_done=self.skip.isChecked())
        return lambda log, stage, prog, can: subtitle.run_subtitle_batch(cfg, files, opts, out, log, stage, prog, can, config.JOBS, textimg=textimg)


class DubbingPage(ToolBase):
    """The person's own narration is the master clock; the picture is re-timed to it. No TTS, no translation."""
    title, icon = "Dubbing Studio — ကိုယ်ပိုင် voice နဲ့ ရုပ်ချိန်ညှိ", "🎙️"

    def build(self):
        g = Glass("၁ · မူရင်း ဗီဒီယို")
        self.src, b, row = _file_row("မူရင်းဗီဒီယို ရွေးပါ (ဗီဒီယိုကို window ပေါ် drag လုပ်လည်းရ)")
        b.clicked.connect(self._pick_src)
        g.lay.addLayout(row)
        self.lay.addWidget(g)

        v = Glass("၂ · Voice ဖိုင် (မြန်မာ narration)")
        self.voice, vb, vrow = _file_row("ကိုယ်တိုင်ဖတ်ထားတဲ့ (သို့) ရှိပြီးသား မြန်မာအသံဖိုင် — mp3 / wav / m4a / aac")
        vb.clicked.connect(self._pick_voice)
        v.lay.addLayout(vrow)
        self.lead = _spin(0, 300, 40, 90)
        r = QHBoxLayout()
        r.addWidget(QLabel("ရုပ်ပြောင်းတာ စကားမစခင် (ms)"))
        r.addStretch(1)
        r.addWidget(self.lead)
        v.lay.addLayout(r)
        h = QLabel("Voice ကို မဆွဲ မရွှေ့ပါဘူး — ရုပ်ကိုပဲ voice ရဲ့ ရပ်နားချိန်တွေမှာ ဖြတ်ပြီး voice အရှည်နဲ့ ကိုက်အောင် ညှိတယ်။ "
                   "မူရင်းဘာသာ၊ Whisper model၊ volume ကို ဆက်တင်မှာ ရွေးပါ။ Gemini key / TTS မလိုပါ။")
        h.setObjectName("hint")
        h.setWordWrap(True)
        v.lay.addWidget(h)
        self.lay.addWidget(v)

    def _pick_src(self):
        p, _ = QFileDialog.getOpenFileName(self, "ဗီဒီယို ရွေးပါ", "", VIDEO_FILTER)
        if p:
            self.src.setText(p)

    def _pick_voice(self):
        p, _ = QFileDialog.getOpenFileName(self, "Voice ဖိုင် ရွေးပါ", "", AUDIO_FILTER)
        if p:
            self.voice.setText(p)

    def job(self, cfg, out):
        src, voice = self.src.text().strip(), self.voice.text().strip()
        if not os.path.isfile(src):
            raise ValueError("မူရင်းဗီဒီယို ရွေးပါ")
        if not os.path.isfile(voice):
            raise ValueError("Voice ဖိုင် ရွေးပါ")
        lead = self.lead.value()
        return lambda log, stage, prog, can: dubbing.run_dubbing(cfg, src, voice, lead, out, log, lambda s, f: stage(s), prog, can, config.JOBS)


class MoviePage(ToolBase):
    """Movie Recap: whole-movie analysis -> key points -> Burmese narration + voice + picture, joined into one video."""
    title, icon = "Movie Recap — ရုပ်ရှင်အပြည့်ကနေ Recap", "🍿"
    ui_call = Signal(object)

    def needs_gemini(self):
        return True

    def build(self):
        self.ui_call.connect(lambda f: f())
        self.analysis_for = ""
        self._analyze = False

        g = Glass("၁ · ရုပ်ရှင်")
        self.src, b, row = _file_row("ရုပ်ရှင်ဖိုင် ရွေးပါ (၂ မိနစ်ထက်ရှည်)")
        b.clicked.connect(self._pick_src)
        g.lay.addLayout(row)
        self.srt, sb, srow = _file_row("Subtitle .srt (ရှိရင်ထည့်ပါ — Whisper ထက်မြန်၊ မှန်)")
        sb.clicked.connect(self._pick_srt)
        g.lay.addLayout(srow)
        self.lang = _combo(list(LANGS), self.app.cfg["lang"])
        g.lay.addLayout(_labeled("ရုပ်ရှင်ထဲက စကားပြောဘာသာ (Subtitle မထည့်ရင်)", self.lang))
        self.lay.addWidget(g)

        o = Glass("၂ · Recap ရဲ့ ပုံစံ")
        self.t0 = QLineEdit("0:00:00")
        self.t1 = QLineEdit()
        self.t1.setPlaceholderText("ဗလာ = အဆုံးထိ")
        r = QHBoxLayout()
        r.setSpacing(10)
        r.addLayout(_labeled("စမယ့်အချိန် (H:MM:SS) — ad / intro ဖြတ်", self.t0), 1)
        r.addLayout(_labeled("ဆုံးမယ့်အချိန် — credit ဖြတ်", self.t1), 1)
        o.lay.addLayout(r)
        self.mins = _spin(3, 40, 12, 90)
        r = QHBoxLayout()
        r.addWidget(QLabel("Recap အရှည် (မိနစ်)"))
        r.addStretch(1)
        r.addWidget(self.mins)
        o.lay.addLayout(r)
        hh = QLabel("အကြံပြုချက် — ၂ နာရီကား ≈ ၁၁–၁၂ မိနစ်၊ ၄ နာရီကား ≈ ၁၆ မိနစ်။")
        hh.setObjectName("hint")
        o.lay.addWidget(hh)
        self.voice = QComboBox()
        o.lay.addLayout(_labeled("အသံ", self.voice))
        self.bg, sl = _slider(0, 60, 10, "%")
        o.lay.addWidget(QLabel("မူရင်းအသံ (နောက်ခံ) volume"))
        o.lay.addLayout(sl)
        self.height_c = _combo([480, 720, 1080], 720)
        o.lay.addLayout(_labeled("ဗီဒီယို အမြင့် (px)", self.height_c))
        self.fx = QCheckBox("ရုပ်ထဲ slow zoom + fade (ပိုလှ၊ နည်းနည်းနှေး)")
        self.fx.setChecked(True)
        o.lay.addWidget(self.fx)
        self.lay.addWidget(o)

        k = Glass("၃ · Key point များ (စိစစ်ပြီးမှ ပေါ်မယ်)")
        self.story = QLabel("")
        self.story.setObjectName("hint")
        self.story.setWordWrap(True)
        k.lay.addWidget(self.story)
        self.points = QListWidget()
        self.points.setFixedHeight(220)
        k.lay.addWidget(self.points)
        h2 = QLabel("🔍 စိစစ်မယ် နှိပ်ရင် ဇာတ်ကားတစ်ခုလုံးကို AI က ကြည့်ပြီး key point တွေ ထုတ်ပေးမယ်။ ထည့်ချင်တာ ✓ ပေး၊ မထည့်ချင်တာ ✓ ဖြုတ်ပြီး "
                    "🎬 Recap ထုတ်မယ် ကို နှိပ်ပါ။ မစိစစ်ဘဲ တန်း ထုတ်လည်းရ (AI က ကိုယ်တိုင်ရွေးမယ်)။ ပြီးသမျှ သိမ်းထားလို့ ရပ်ပြီးပြန်စရင် ဆက်မယ်။")
        h2.setObjectName("hint")
        h2.setWordWrap(True)
        k.lay.addWidget(h2)
        self.lay.addWidget(k)

    def post_build(self):
        self.btn_start.setText("🎬  Recap ထုတ်မယ်")
        ab = QPushButton("🔍  စိစစ်မယ်")
        ab.clicked.connect(self._start_analyze)
        self.run_row.insertWidget(0, ab)
        self._fill_voices()

    def showEvent(self, e):
        super().showEvent(e)
        self._fill_voices()

    def _fill_voices(self):
        cur = self.voice.currentData()
        self.voice.clear()
        for name in VOICES:
            self.voice.addItem("Edge — " + name, "edge:" + name)
        self.voice.addItem("Google (အခမဲ့)", "google")
        for v in clone.list_voices():
            self.voice.addItem("🧬 Voice Clone — " + v["name"], "clone:" + v["id"])
        i = self.voice.findData(cur)
        self.voice.setCurrentIndex(i if i >= 0 else 0)

    def _pick_src(self):
        p, _ = QFileDialog.getOpenFileName(self, "ရုပ်ရှင် ရွေးပါ", "", VIDEO_FILTER)
        if p:
            self.src.setText(p)

    def _pick_srt(self):
        p, _ = QFileDialog.getOpenFileName(self, "Subtitle ရွေးပါ", "", "Subtitle (*.srt);;All files (*.*)")
        if p:
            self.srt.setText(p)

    def _start_analyze(self):
        self._analyze = True
        try:
            self._start()                  # job() reads the flag synchronously
        finally:
            self._analyze = False          # an early return (no key, bad input) must not leave it set

    def _show_analysis(self, a):
        self.analysis_for = self.src.text().strip()
        self.story.setText("📖 %s\n%s" % (a["title"], a["logline"]))
        self.points.clear()
        for p in a["points"]:
            it = QListWidgetItem("★%d · %s · %s — %s  (~%ds)" % (p["imp"], p["kind"], movie.hms(p["start"]), p["title"], int(p["seconds"])))
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if p["selected"] else Qt.CheckState.Unchecked)
            it.setData(Qt.ItemDataRole.UserRole, p["id"])
            it.setToolTip(p["what"])
            self.points.addItem(it)

    def job(self, cfg, out):
        analyze_only, self._analyze = self._analyze, False      # read AND reset first: a failed check must not leave it set
        src = self.src.text().strip()
        if not os.path.isfile(src):
            raise ValueError("ရုပ်ရှင်ဖိုင် ရွေးပါ")
        srt = self.srt.text().strip()
        if srt and not os.path.isfile(srt):
            raise ValueError("Subtitle ဖိုင် မတွေ့ပါ")
        include = None
        if self.points.count() and self.analysis_for == src and not analyze_only:
            include = {self.points.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.points.count())
                       if self.points.item(i).checkState() == Qt.CheckState.Checked}
            if not include:
                raise ValueError("Key point တစ်ခုမှ မရွေးထားပါ")
        voice = self.voice.currentData() or "edge:" + next(iter(VOICES))
        if voice.startswith("clone:") and not self.app.cfg.get("own_url"):
            raise ValueError("Voice Clone GPU မချိတ်ရသေးပါ — Tools › Voice Clone မှာ Colab ကို အရင်ချိတ်ပါ")
        cfg = dict(cfg, **{k: self.app.cfg.get(k, "") for k in ("own_url", "own_key", "own_gpu")})
        req = dict(movie=src, srt=srt or None, lang=self.lang.currentText(), trim_start=movie.parse_time(self.t0.text()),
                   trim_end=movie.parse_time(self.t1.text()), target_min=self.mins.value(), bg_pct=self.bg.value(),
                   height=int(self.height_c.currentText()), fx=self.fx.isChecked(), voice=voice, include=include,
                   analyze_only=analyze_only, out_dir=out)

        def on_analysis(a):
            self.ui_call.emit(lambda: self._show_analysis(a))
            if analyze_only:
                self.bus.stage.emit("✅ စိစစ်ပြီးပါပြီ — Key point တွေ ကြည့်ပြီး Recap ထုတ်ပါ")
        return lambda log, stage, prog, can: movie.run_movie(cfg, req, log, lambda s, f: stage(s), prog, can, config.JOBS, on_analysis)


class ClonePage(ToolBase):
    """Voice Clone on your own free Colab GPU — no server of ours involved."""
    title, icon = "Voice Clone — ကိုယ်ပိုင်အသံ (Colab)", "🧬"
    ui_call = Signal(object)
    QUALITY = {"မြန် (6)": 6, "ပုံမှန် (10)": 10, "ကောင်း (16)": 16, "အကောင်းဆုံး (24)": 24}
    SIMILAR = {"သဘာဝကျ (1.5)": 1.5, "ပုံမှန် (2)": 2.0, "တူ (2.5)": 2.5, "အတူဆုံး (3)": 3.0}
    BEST = {"၁ ခါ (မြန်)": 1, "၃ ခါ": 3, "၅ ခါ (အတူဆုံး)": 5}
    SOFT = {"မလျှော့": 0, "နည်းနည်း": 1, "အလယ်": 2, "များများ": 3}
    PAUSE = {"တို (50)": 50, "ပုံမှန် (100)": 100, "ရှည် (150)": 150, "အရမ်းရှည် (220)": 220}

    @staticmethod
    def _label_for(table, value):
        return next((k for k, v in table.items() if v == value), next(iter(table)))

    def build(self):
        c = self.app.cfg
        self.ui_call.connect(lambda f: f())
        self.vid = ""
        self._last_clip = ""

        # ---- 1 GPU
        g = Glass("၁ · GPU ချိတ်ဆက် (အခမဲ့ Colab)")
        self.gpu_lbl = QLabel("စစ်နေတယ်…")
        g.lay.addWidget(self.gpu_lbl)
        h = QLabel("၁) Notebook သိမ်းပြီး Colab ဖွင့်  ၂) Colab မှာ File › Upload notebook  ၃) Runtime › Change runtime type › GPU  "
                   "၄) cell ကို ▶ နှိပ်  ၅) ပေါ်လာတဲ့ RCP1: ကုဒ်ကို ကူးပြီး ဒီမှာ paste")
        h.setObjectName("hint")
        h.setWordWrap(True)
        g.lay.addWidget(h)
        r = QHBoxLayout()
        r.setSpacing(10)
        b1 = QPushButton("📓  Notebook သိမ်း + Colab ဖွင့်")
        b1.clicked.connect(self._open_colab)
        b2 = QPushButton("📋  Clipboard က ယူ")
        b2.clicked.connect(lambda: self.clipboard_check(auto=False))
        r.addWidget(b1)
        r.addWidget(b2)
        r.addStretch(1)
        g.lay.addLayout(r)
        self.code = QLineEdit()
        self.code.setPlaceholderText("RCP1:…  ကုဒ်ကို ဒီမှာ paste")
        cb = QPushButton("ချိတ်မယ်")
        cb.setObjectName("primary")
        cb.clicked.connect(lambda: self._connect(self.code.text(), False))
        db = QPushButton("ဖြတ်မယ်")
        db.clicked.connect(self._disconnect)
        g.lay.addLayout(_row(self.code, cb, db))
        self.lay.addWidget(g)

        # ---- 2 voices
        v = Glass("၂ · အသံနမူနာ")
        self.voices = QListWidget()
        self.voices.setFixedHeight(120)
        self.voices.currentItemChanged.connect(self._pick_voice)
        v.lay.addWidget(self.voices)
        self.vwarn = QLabel("")
        self.vwarn.setObjectName("hint")
        self.vwarn.setWordWrap(True)
        v.lay.addWidget(self.vwarn)
        r = QHBoxLayout()
        r.setSpacing(10)
        addb = QPushButton("📁  ဖိုင်ရွေး…")
        addb.clicked.connect(self._add_voice)
        delb = QPushButton("🗑  ဖျက်မယ်")
        delb.clicked.connect(self._delete_voice)
        r.addWidget(addb)
        r.addWidget(delb)
        r.addStretch(1)
        v.lay.addLayout(r)
        hh = QLabel("၁၀–၁၅ စက္ကန့်၊ တစ်ယောက်တည်း ရှင်းရှင်းပြောတဲ့ အသံ (ဂီတ / ဆူညံသံ မပါ၊ မြန်မာလိုဆိုရင် ပိုတူ)။ mp3 / wav / m4a / ဗီဒီယို ရတယ်။")
        hh.setObjectName("hint")
        hh.setWordWrap(True)
        v.lay.addWidget(hh)
        self.vtext = QPlainTextEdit()
        self.vtext.setFixedHeight(84)
        self.vtext.setPlaceholderText("နမူနာအသံထဲက ပြောထားတဲ့ စာသား (အတိအကျ၊ မှန်အောင် ပြင်ပါ) — ပါရင် အသံ အများကြီး ပိုတူတယ်")
        v.lay.addWidget(self.vtext)
        r = QHBoxLayout()
        r.setSpacing(10)
        self.btn_read = QPushButton("🔍  AI နဲ့ ဖတ်ခိုင်း")
        self.btn_read.clicked.connect(self._read_ai)
        sv = QPushButton("💾  စာသား သိမ်းမယ်")
        sv.clicked.connect(self._save_text)
        tb = QPushButton("🔈  စမ်းနားထောင်")
        tb.clicked.connect(self._test_voice)
        r.addWidget(self.btn_read)
        r.addWidget(sv)
        r.addWidget(tb)
        r.addStretch(1)
        v.lay.addLayout(r)
        self.lay.addWidget(v)

        # ---- 3 text
        tx = Glass("၃ · စာ")
        self.text = QPlainTextEdit()
        self.text.setFixedHeight(190)
        self.text.setPlaceholderText("အသံထွက်ချင်တဲ့ စာ (ဘယ်လောက်ရှည်ရှည် ရတယ်)")
        tx.lay.addWidget(self.text)
        r = QHBoxLayout()
        r.setSpacing(10)
        fb = QPushButton("📄  .txt ဖိုင်")
        fb.clicked.connect(self._load_txt)
        pb = QPushButton("📋  Paste")
        pb.clicked.connect(lambda: self.text.setPlainText(QApplication.clipboard().text()))
        xb = QPushButton("ရှင်း")
        xb.clicked.connect(self.text.clear)
        for b in (fb, pb, xb):
            r.addWidget(b)
        r.addStretch(1)
        tx.lay.addLayout(r)
        self.lay.addWidget(tx)

        # ---- 4 settings
        s = Glass("၄ · ချိန်ညှိချက်")
        self.q_c = _combo(list(self.QUALITY), self._label_for(self.QUALITY, c["clone_steps"]))
        self.s_c = _combo(list(self.SIMILAR), self._label_for(self.SIMILAR, float(c["clone_cfg"])))
        r = QHBoxLayout()
        r.setSpacing(10)
        r.addLayout(_labeled("အရည်အသွေး (မြင့်ရင် ပိုကြည်၊ ပိုကြာ)", self.q_c), 1)
        r.addLayout(_labeled("နမူနာနဲ့ ဘယ်လောက်တူစေမလဲ", self.s_c), 1)
        s.lay.addLayout(r)
        self.b_c = _combo(list(self.BEST), self._label_for(self.BEST, c["clone_best"]))
        self.so_c = _combo(list(self.SOFT), self._label_for(self.SOFT, c["clone_soft"]))
        r = QHBoxLayout()
        r.setSpacing(10)
        r.addLayout(_labeled("တူအောင် ထုတ်ခါရေ (များရင် ပိုတူ၊ ပိုကြာ)", self.b_c), 1)
        r.addLayout(_labeled("အသံစူးရင် ပျော့ပေး", self.so_c), 1)
        s.lay.addLayout(r)
        self.p_c = _combo(list(self.PAUSE), self._label_for(self.PAUSE, c["clone_pause"]))
        s.lay.addLayout(_labeled("ဝါကျကြား ရပ်ချိန် (ms)", self.p_c))
        self.lay.addWidget(s)

        self._refresh_voices()

    # ---------------------------------------------------------------- helpers
    def _bg(self, fn, then=None):
        """Run fn() off the UI thread; then(result_or_exception) runs back on the UI thread."""
        def work():
            try:
                res = fn()
            except Exception as e:                  # noqa: BLE001 - shown to the person
                res = e
            if then:
                self.ui_call.emit(lambda: then(res))
        threading.Thread(target=work, daemon=True).start()

    def _say(self, msg):
        self.bus.log.emit(msg)

    def _set_gpu(self, ok, info):
        self.gpu_lbl.setText(("🟢 GPU ချိတ်ပြီး — %s" % info) if ok else ("🔴 %s" % info))

    def showEvent(self, e):
        super().showEvent(e)
        self._check_saved()

    # ---------------------------------------------------------------- GPU
    def _check_saved(self):
        c = self.app.cfg
        if not c.get("own_url"):
            self._set_gpu(False, "GPU မချိတ်ရသေးပါ")
            return
        self.gpu_lbl.setText("စစ်နေတယ်…")
        self._bg(lambda: clone.check(c["own_url"], c["own_key"]), lambda r: self._set_gpu(*r) if isinstance(r, tuple) else None)

    def _save_notebook(self):
        dest = os.path.join(os.path.expanduser("~"), "Downloads", "RecapMM")
        os.makedirs(dest, exist_ok=True)
        out = os.path.join(dest, "RecapMM_Voice_Clone.ipynb")
        import shutil
        shutil.copyfile(clone.NOTEBOOK, out)
        return dest, out

    def _open_colab(self):
        try:
            dest, out = self._save_notebook()
            self._say("📓 Notebook သိမ်းပြီး: " + out)
            QDesktopServices.openUrl(QUrl.fromLocalFile(dest))
        except Exception as e:
            self._say("❌ Notebook သိမ်းမရ: %s" % e)
        QDesktopServices.openUrl(QUrl("https://colab.research.google.com/"))

    def clipboard_check(self, auto=False):
        txt = QApplication.clipboard().text()
        if "RCP1:" not in txt or (auto and txt == self._last_clip):
            if not auto:
                self._say("Clipboard ထဲမှာ RCP1: ကုဒ် မတွေ့ပါ")
            return
        self._last_clip = txt
        self._connect(txt, auto)

    def _connect(self, raw, auto):
        parsed = clone.parse_code(raw)
        if not parsed:
            if not auto:
                QMessageBox.warning(self, "RecapMM", "ကုဒ် မမှန်ပါ — RCP1: နဲ့စတဲ့ ကုဒ် အပြည့်အစုံ ကူးထည့်ပါ")
            return
        url, key, _ = parsed
        c = self.app.cfg
        if auto and url == c.get("own_url") and key == c.get("own_key"):
            return
        self.gpu_lbl.setText("ချိတ်နေတယ်…")

        def done(r):
            if isinstance(r, Exception):
                self._set_gpu(False, str(r))
                return
            ok, info = r
            if ok:
                if c.get("own_url") != url:
                    clone.forget_sent()
                c.update({"own_url": url, "own_key": key, "own_gpu": info})
                config.save(c)
                self.code.clear()
            self._set_gpu(ok, info)
            self._say(("✅ ချိတ်ပြီးပါပြီ — %s" % info) if ok else ("⚠️ " + info))
        self._bg(lambda: clone.check(url, key), done)

    def _disconnect(self):
        c = self.app.cfg
        c.update({"own_url": "", "own_key": "", "own_gpu": ""})
        clone.forget_sent()
        config.save(c)
        self._set_gpu(False, "GPU မချိတ်ရသေးပါ")

    # ---------------------------------------------------------------- voices
    def _refresh_voices(self, select=None):
        self.voices.blockSignals(True)
        self.voices.clear()
        for v in clone.list_voices():
            it = v["name"] + ("  ·  %ds" % v["sec"]) + ("  ·  🎯" if v["text"] else "  ·  ⚠ စာသားမပါ")
            self.voices.addItem(it)
            self.voices.item(self.voices.count() - 1).setData(Qt.ItemDataRole.UserRole, v["id"])
        self.voices.blockSignals(False)
        want = select or self.vid
        for i in range(self.voices.count()):
            if self.voices.item(i).data(Qt.ItemDataRole.UserRole) == want:
                self.voices.setCurrentRow(i)
                return
        self.voices.setCurrentRow(0 if self.voices.count() else -1)
        if not self.voices.count():
            self.vid = ""
            self.vtext.clear()

    def _pick_voice(self, cur, _prev):
        self.vid = cur.data(Qt.ItemDataRole.UserRole) if cur else ""
        txt = next((v["text"] for v in clone.list_voices() if v["id"] == self.vid), "")
        self.vtext.setPlainText(txt)

    def _add_voice(self):
        p, _ = QFileDialog.getOpenFileName(self, "နမူနာအသံ ရွေးပါ", "", AUDIO_FILTER)
        if not p:
            return
        name = os.path.splitext(os.path.basename(p))[0]
        self._say("🎙 နမူနာ ပြင်နေတယ်…")

        def done(r):
            if isinstance(r, Exception):
                self._say("❌ နမူနာ သိမ်းမရ: %s" % r)
                return
            vid, note = r
            self.vwarn.setText("⚠ " + note if note else "")
            self._refresh_voices(select=vid)
            self._say("✅ အသံနမူနာ သိမ်းပြီးပါပြီ")
            if self.app.collect()["keys"].strip():
                self._read_ai()
            else:
                self._say("ℹ️ Gemini key မထည့်ရသေးလို့ စာသား မဖတ်နိုင်ပါ — အပေါ်က ကွက်ထဲ ကိုယ်တိုင်ရိုက်ထည့်ပါ (စာသားမပါရင် အသံ သိပ်မတူပါ)")
        self._bg(lambda: clone.add_voice_from_file(p, name), done)

    def _delete_voice(self):
        if self.vid and clone.delete_voice(self.vid):
            self.vid = ""
            self._refresh_voices()

    def _read_ai(self):
        vid = self.vid
        pcm = clone.ref_pcm(vid) if vid else None
        if pcm is None:
            self._say("အသံနမူနာ အရင်ရွေးပါ")
            return
        cfg = self.app.collect()
        self.btn_read.setEnabled(False)
        self._say("🔍 AI က နားထောင်နေတယ်…")

        def done(r):
            self.btn_read.setEnabled(True)
            if isinstance(r, Exception):
                self._say("❌ ဖတ်မရပါ: %s" % redact(str(r)))
            elif not r:
                self._say("AI က စာသား မဖတ်နိုင်ပါ — ကိုယ်တိုင် ရိုက်ထည့်ပါ")
            else:
                if vid == self.vid:
                    self.vtext.setPlainText(r)
                clone.set_text(vid, r)
                self._refresh_voices(select=vid)
                self._say("✅ AI က စာသား ဖတ်ပေးပြီးပြီ — မှန်လား စစ်ပါ")
        self._bg(lambda: clone.transcribe(cfg, pcm, self._say), done)

    def _save_text(self):
        if not self.vid:
            return
        ok = clone.set_text(self.vid, self.vtext.toPlainText())
        self._say("✅ စာသား သိမ်းပြီးပါပြီ" if ok else "စာသား သိမ်းမရပါ")
        self._refresh_voices(select=self.vid)

    def _params(self):
        return (self.QUALITY[self.q_c.currentText()], self.SIMILAR[self.s_c.currentText()], self.BEST[self.b_c.currentText()],
                self.SOFT[self.so_c.currentText()], self.PAUSE[self.p_c.currentText()])

    def _test_voice(self):
        if not self.vid:
            self._say("အသံနမူနာ အရင်ရွေးပါ")
            return
        if not self.app.cfg.get("own_url"):
            self._say("GPU အရင် ချိတ်ပါ")
            return
        cfg, vid = dict(self.app.cfg), self.vid
        steps, cs, best, soft, _ = self._params()
        sample = (self.text.toPlainText().strip() or "မင်္ဂလာပါ၊ ဒါက အသံစမ်းသပ်ချက် ဖြစ်ပါတယ်။")[:120]
        self._say("🔈 စမ်းနေတယ်…")

        def work():
            pcm = clone.speak(cfg, sample, vid, steps, cs, best, soft)
            wav = os.path.join(config.JOBS, "clone_test.wav")
            os.makedirs(config.JOBS, exist_ok=True)
            media.write_wav(wav, pcm, clone.RATE)
            out = os.path.join(config.JOBS, "clone_test.mp3")
            media.encode_audio(wav, out)
            return out

        def done(r):
            if isinstance(r, Exception):
                self._say("❌ " + redact(str(r)))
            else:
                self._say("✅ စမ်းအသံ ဖွင့်မယ်")
                try:
                    os.startfile(r)
                except AttributeError:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(r))
        self._bg(work, done)

    def _load_txt(self):
        p, _ = QFileDialog.getOpenFileName(self, "စာဖိုင် ရွေးပါ", "", "Text (*.txt);;All files (*.*)")
        if p:
            try:
                self.text.setPlainText(open(p, encoding="utf-8-sig").read())
            except Exception as e:
                self._say("စာဖိုင် ဖတ်မရပါ: %s" % e)

    # ---------------------------------------------------------------- run
    def job(self, cfg, out):
        text = self.text.toPlainText().strip()
        if not text:
            raise ValueError("စာ ရိုက်ထည့်ပါ")
        if not self.vid:
            raise ValueError("အသံနမူနာ ရွေးပါ (သို့) ထည့်ပါ")
        if not self.app.cfg.get("own_url"):
            raise ValueError("GPU အရင် ချိတ်ပါ (Colab notebook ပြေးပြီး ကုဒ်ထည့်ပါ)")
        steps, cs, best, soft, pause = self._params()
        cfg = dict(cfg, **{k: self.app.cfg.get(k, "") for k in ("own_url", "own_key", "own_gpu")})
        self.app.cfg.update({"clone_steps": steps, "clone_cfg": cs, "clone_best": best, "clone_soft": soft, "clone_pause": pause})
        vid = self.vid
        return lambda log, stage, prog, can: clone.run_clone(cfg, text, vid, steps, cs, best, soft, pause, out, log,
                                                             lambda s, f: stage(s), prog, can, config.JOBS)


# ------------------------------------------------------------------ window
class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RecapMM %s — English → မြန်မာ" % __version__)
        self.resize(1000, 820)
        self.setMinimumSize(820, 640)
        self.setAcceptDrops(True)
        self.cfg = config.load()
        self._dark_done = False

        self.bg = LiquidBackground()
        self.setCentralWidget(self.bg)
        root = QVBoxLayout(self.bg)
        root.setContentsMargins(22, 18, 22, 14)
        root.setSpacing(6)

        head = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(0)
        t = QLabel("RecapMM")
        t.setObjectName("title")
        s = QLabel("English → မြန်မာ · Server မလို · ဒီ PC ပေါ်မှာပဲ")
        s.setObjectName("sub")
        col.addWidget(t)
        col.addWidget(s)
        head.addLayout(col, 1)
        self.chip_tools, self.chip_set = QPushButton("Tools"), QPushButton("ဆက်တင်")
        for c in (self.chip_tools, self.chip_set):
            c.setObjectName("chip")
            c.setCheckable(True)
            c.setCursor(Qt.CursorShape.PointingHandCursor)
            head.addWidget(c)
        self.chip_tools.clicked.connect(self.go_home)
        self.chip_set.clicked.connect(self.show_settings)
        root.addLayout(head)

        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)
        self.home = self._home_page()
        self.settings = self._settings_page()
        self.pages.addWidget(self.home)
        self.pages.addWidget(self.settings)
        self.tools = {"recap": RecapPage(self), "srt": SrtPage(self), "tts": TtsPage(self),
                      "split": SplitPage(self), "dhamma": DhammaPage(self), "clone": ClonePage(self),
                      "dubbing": DubbingPage(self), "movie": MoviePage(self), "sub": SubPage(self)}
        for pg in self.tools.values():
            self.pages.addWidget(pg)
        self.go_home()

    # ---------- pages
    def _home_page(self):
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc.viewport().setAutoFillBackground(False)
        page = QWidget()
        page.setObjectName("page")
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 6, 0)
        grid = QGridLayout()
        grid.setHorizontalSpacing(-SHADOW + 2)
        grid.setVerticalSpacing(-SHADOW + 2)
        items = [
            ("🎬", "Recap", "ဗီဒီယိုကို မြန်မာ Recap လုပ်", None, "recap"),
            ("🍿", "Movie Recap", "ရုပ်ရှင်အပြည့်ကနေ ၁၀–၁၅ မိနစ် Recap", None, "movie"),
            ("🎙️", "Dubbing Studio", "ကိုယ်ပိုင် voice နဲ့ ရုပ်ချိန်ညှိ", None, "dubbing"),
            ("✂️", "Splitter Studio", "ဗီဒီယိုကို အပိုင်းလိုက် ဖြတ်", None, "split"),
            ("🧬", "Voice Clone", "ကိုယ်ပိုင်အသံနဲ့ ဖတ်ခိုင်း (အခမဲ့ Colab)", None, "clone"),
            ("🔊", "TTS", "စာကို အသံ ပြောင်း", None, "tts"),
            ("💬", "SRT", "စာတန်းဖိုင် ထုတ်", None, "srt"),
            ("📝", "Subtitle Only", "စကားပြောချိန်ပဲ မြန်မာစာတန်းထိုး + blur (အပိုင်းအများကြီး)", None, "sub"),
            ("☸️", "Dhamma Studio", "တရား ဗီဒီယို", None, "dhamma"),
            ("▶️", "YouTube Link", "YouTube ကနေ တိုက်ရိုက်ယူ (Server လို)", "Coming Soon", None),
        ]
        self._tiles = {}
        for i, (ic, ti, su, badge, key) in enumerate(items):
            tile = Tile(ic, ti, su, badge, enabled=key is not None)
            if key:
                tile.clicked.connect(lambda k=key: self.open_tool(k))
            grid.addWidget(tile, i // 2, i % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        v.addLayout(grid)
        self.ff_warn = QLabel("")
        self.ff_warn.setObjectName("hint")
        self.ff_warn.setWordWrap(True)
        v.addWidget(self.ff_warn)
        v.addStretch(1)
        sc.setWidget(page)
        if not media.check_ffmpeg():
            self.ff_warn.setText("⚠️ ffmpeg.exe မတွေ့ပါ — RecapMM.exe နဲ့ folder တူတူထဲ ထည့်ပါ (README ကြည့်ပါ)")
        return sc

    def _settings_page(self):
        c = self.cfg
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc.viewport().setAutoFillBackground(False)
        page = QWidget()
        page.setObjectName("page")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(-SHADOW + 2)

        o = Glass("သိမ်းမယ့်နေရာ")
        self.out = QLineEdit(c["out_dir"] or os.path.join(os.path.expanduser("~"), "Videos", "RecapMM"))
        ob = QPushButton("Folder…")
        ob.clicked.connect(self._pick_out)
        o.lay.addLayout(_row(self.out, ob))
        lay.addWidget(o)

        g = Glass("Gemini")
        self.keys = QPlainTextEdit(c["keys"])
        self.keys.setFixedHeight(92)
        self.keys.setPlaceholderText("AIza…  (တစ်ကြောင်း တစ်ခု၊ အများကြီးထည့်လို့ရ)")
        g.lay.addLayout(_labeled("API key", self.keys,
                                 "Key ကို ဒီ PC ထဲမှာပဲ သိမ်းတယ်၊ Google ကို တိုက်ရိုက်ခေါ်တယ်။ Gemini ပိတ်ထားတဲ့နေရာဆိုရင် VPN ဖွင့်ပါ။"))
        self.models = QLineEdit(c["models"])
        self.models.setPlaceholderText("ဗလာ = default model စာရင်း (ကော်မာခြား)")
        g.lay.addLayout(_labeled("Model", self.models))
        self.interval = _spin(0, 30, c["min_interval"])
        r = QHBoxLayout()
        r.addWidget(QLabel("Request အကြား (စက္ကန့်)"))
        r.addStretch(1)
        r.addWidget(self.interval)
        g.lay.addLayout(r)
        lay.addWidget(g)

        v = Glass("မြန်မာအသံ")
        self.engine = _combo(["edge", "google"], c["engine"])
        self.voice = _combo(list(VOICES), c["voice"])
        r = QHBoxLayout()
        r.setSpacing(10)
        r.addLayout(_labeled("Engine", self.engine), 1)
        r.addLayout(_labeled("အသံ (Edge)", self.voice), 1)
        v.lay.addLayout(r)
        self.speed, sl = _slider(-50, 50, c["speed_pct"], "%")
        v.lay.addWidget(QLabel("အမြန်နှုန်း (Edge)"))
        v.lay.addLayout(sl)
        self.volume, sl2 = _slider(20, 300, c["volume_pct"], "%")
        v.lay.addWidget(QLabel("အသံ volume"))
        v.lay.addLayout(sl2)
        self.short = QCheckBox("အသံရှေ့နောက် တိတ်ဆိတ်တာ ဖြတ်မယ် (ပိုကျစ်)")
        self.short.setChecked(bool(c["short_pauses"]))
        v.lay.addWidget(self.short)
        lay.addWidget(v)

        w = Glass("Whisper & ဗီဒီယို")
        self.whisper = _combo(WHISPER_MODELS, c["whisper"])
        self.lang = _combo(list(LANGS), c["lang"])
        r = QHBoxLayout()
        r.setSpacing(10)
        r.addLayout(_labeled("Whisper model (ကြီးရင် တိကျ၊ နှေး)", self.whisper), 1)
        r.addLayout(_labeled("မူရင်းဘာသာ", self.lang), 1)
        w.lay.addLayout(r)
        self.height_c = _combo([480, 720, 1080, 1440, 2160], c["max_height"])
        self.workers = _spin(1, 8, c["workers"])
        r = QHBoxLayout()
        r.setSpacing(10)
        r.addLayout(_labeled("ဗီဒီယို အမြင့်ဆုံး (px)", self.height_c), 1)
        r.addLayout(_labeled("Render တစ်ပြိုင်နက်", self.workers), 0)
        w.lay.addLayout(r)
        lay.addWidget(w)

        gl = Glass("ဝေါဟာရ (glossary)")
        self.gloss = QPlainTextEdit(c["glossary"])
        self.gloss.setFixedHeight(110)
        self.gloss.setPlaceholderText("English = မြန်မာ   (တစ်ကြောင်း တစ်ခု)")
        gl.lay.addWidget(self.gloss)
        lay.addWidget(gl)

        save = QPushButton("💾   သိမ်းမယ်")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        h = QHBoxLayout()
        h.addStretch(1)
        h.addWidget(save)
        h.setContentsMargins(0, 4, 20, 10)
        lay.addLayout(h)
        lay.addStretch(1)
        sc.setWidget(page)
        return sc

    def event(self, e):
        if e.type() == QEvent.Type.WindowActivate and hasattr(self, "tools"):
            pg = self.tools.get("clone")
            if pg is not None and self.pages.currentWidget() is pg:
                QTimer.singleShot(400, lambda: pg.clipboard_check(auto=True))
        return super().event(e)

    # ---------- navigation
    def go_home(self):
        self.pages.setCurrentWidget(self.home)
        self.chip_tools.setChecked(True)
        self.chip_set.setChecked(False)

    def show_settings(self):
        self.pages.setCurrentWidget(self.settings)
        self.chip_tools.setChecked(False)
        self.chip_set.setChecked(True)

    def open_tool(self, key):
        self.pages.setCurrentWidget(self.tools[key])
        self.chip_tools.setChecked(True)
        self.chip_set.setChecked(False)

    # ---------- events
    def showEvent(self, e):
        super().showEvent(e)
        if not self._dark_done and sys.platform == "win32":       # dark title bar to match the glass
            self._dark_done = True
            try:
                import ctypes
                val = ctypes.c_int(1)
                hwnd = int(self.winId())
                for attr in (20, 19):
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), 4)
            except Exception:
                pass

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile()]
        if not paths:
            return
        cur = self.pages.currentWidget()
        if hasattr(cur, "add_paths"):                       # the Subtitle Only list takes files and whole folders
            cur.add_paths(paths)
            return
        many = len(paths) > 1 or os.path.isdir(paths[0])
        if many:
            self.open_tool("sub")
            self.tools["sub"].add_paths(paths)
            return
        p = paths[0]
        if p.lower().endswith(VIDEO_EXT):
            target = cur if hasattr(cur, "src") else self.tools["recap"]
            target.src.setText(p)
            self.open_tool(next(k for k, v in self.tools.items() if v is target))

    # ---------- settings
    def out_dir(self):
        return self.out.text().strip() or os.path.join(os.path.expanduser("~"), "Videos", "RecapMM")

    def open_out(self):
        d = self.out_dir()
        os.makedirs(d, exist_ok=True)
        try:
            os.startfile(d)  # Windows
        except AttributeError:
            subprocess.Popen(["xdg-open", d])

    def _pick_out(self):
        p = QFileDialog.getExistingDirectory(self, "Output folder", self.out.text())
        if p:
            self.out.setText(p)

    def collect(self):
        c = dict(self.cfg)
        c.update({
            "keys": self.keys.toPlainText().strip(), "models": self.models.text().strip(),
            "min_interval": self.interval.value(), "engine": self.engine.currentText(), "voice": self.voice.currentText(),
            "speed_pct": self.speed.value(), "volume_pct": self.volume.value(), "short_pauses": self.short.isChecked(),
            "whisper": self.whisper.currentText(), "lang": self.lang.currentText(),
            "max_height": int(self.height_c.currentText()), "workers": self.workers.value(),
            "glossary": self.gloss.toPlainText().strip(), "out_dir": self.out.text().strip(),
        })
        return c

    def _save(self):
        self.cfg = self.collect()
        config.save(self.cfg)
        QMessageBox.information(self, "RecapMM", "သိမ်းပြီးပါပြီ")


def _make_app():
    app = QApplication.instance() or QApplication(sys.argv)
    f = QFont()
    f.setFamilies(["Segoe UI", "Myanmar Text", "Noto Sans Myanmar"])
    f.setPointSize(10)
    app.setFont(f)
    app.setStyleSheet(style_sheet(_assets()))
    return app


def main():
    app = _make_app()
    w = App()
    w.show()
    sys.exit(app.exec())


def render_test(outdir="."):
    """CI: build the window headless and save screenshots (also proves the UI code runs)."""
    app = _make_app()
    w = App()
    w.show()
    for _ in range(5):
        w.bg.tick()
        app.processEvents()
    w.grab().save(os.path.join(outdir, "ui-preview-home.png"))
    w.open_tool("recap")
    app.processEvents()
    w.grab().save(os.path.join(outdir, "ui-preview-recap.png"))
    w.open_tool("dhamma")
    app.processEvents()
    w.grab().save(os.path.join(outdir, "ui-preview-dhamma.png"))
    w.show_settings()
    app.processEvents()
    w.grab().save(os.path.join(outdir, "ui-preview-settings.png"))
    print("render ok")
