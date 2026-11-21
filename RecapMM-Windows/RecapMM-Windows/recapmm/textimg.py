"""Text -> PNG with Qt (Qt shapes Burmese properly, so the overlays look right). Needs a running QApplication."""
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QLinearGradient, QPainter

FLAGS = Qt.AlignmentFlag.AlignHCenter.value | Qt.TextFlag.TextWordWrap.value


def _font(px, bold=True):
    f = QFont()
    f.setFamilies(["Segoe UI", "Myanmar Text", "Noto Sans Myanmar"])
    f.setPixelSize(max(8, int(px)))
    f.setBold(bold)
    return f


def _image(w, h):
    img = QImage(int(w), int(h), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    return img


def _painter(img):
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    return p


def _wrapped(text, px, max_w):
    fm = QFontMetrics(_font(px))
    r = fm.boundingRect(QRect(0, 0, int(max_w), 100000), FLAGS, text)
    return r.height() + 4


def card_png(path, W, H, parts):
    """Full-screen card: dark gradient with centred text blocks. parts = [(text, size as a fraction of the short side, '#RRGGBB')]."""
    short = min(W, H)
    img = QImage(int(W), int(H), QImage.Format.Format_ARGB32_Premultiplied)
    p = _painter(img)
    g = QLinearGradient(0, 0, 0, H)
    g.setColorAt(0, QColor("#1B1646"))
    g.setColorAt(1, QColor("#0B0A1F"))
    p.fillRect(0, 0, W, H, g)
    max_w = int(W * 0.84)
    gap = short * 0.04
    sizes = [short * s for _, s, _ in parts]
    heights = [_wrapped(t, px, max_w) for (t, _, _), px in zip(parts, sizes)]
    y = (H - (sum(heights) + gap * (len(parts) - 1))) / 2.0
    for (t, _, col), px, hgt in zip(parts, sizes, heights):
        p.setFont(_font(px))
        p.setPen(QColor(col))
        p.drawText(QRect(int((W - max_w) / 2), int(y), max_w, int(hgt)), FLAGS, t)
        y += hgt + gap
    p.end()
    img.save(path)


def title_png(path, W, H, title, subtitle=""):
    """Title (+ subtitle) on a transparent image with a soft dark outline so it reads on any picture. Returns (w, h)."""
    short = min(W, H)
    max_w = int(W * 0.84)
    t_px, s_px = short * 0.06, short * 0.035
    th = _wrapped(title, t_px, max_w)
    sh = _wrapped(subtitle, s_px, max_w) if subtitle else 0
    gap = short * 0.01 if subtitle else 0
    w, h = max_w + 8, int(th + sh + gap + 8)
    img = _image(w, h)
    p = _painter(img)
    blocks = [(title, t_px, th, 0)] + ([(subtitle, s_px, sh, th + gap)] if subtitle else [])
    for text, px, hgt, y in blocks:
        p.setFont(_font(px))
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (2, 2), (-2, -2)):
            p.setPen(QColor(0, 0, 0, 200))
            p.drawText(QRect(4 + dx, int(y) + 4 + dy, max_w, int(hgt)), FLAGS, text)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(QRect(4, int(y) + 4, max_w, int(hgt)), FLAGS, text)
    p.end()
    img.save(path)
    return w, h


def ticker_png(path, W, H, text, size, color):
    """One long strip with the text on a transparent background (the dark bar is drawn by ffmpeg). Returns (strip_w, bar_h)."""
    short = min(W, H)
    bar_h = max(24, int(round(short * size / 2.0)) * 2)
    line = " ".join(text.split())[:1500]
    px = bar_h * 0.56
    while True:                                         # Myanmar letters are tall: shrink until they fit inside the bar
        fm = QFontMetrics(_font(px))
        if fm.height() <= bar_h * 0.94 or px <= 8:
            break
        px *= 0.95
    sw = fm.horizontalAdvance(line) + 12
    img = _image(sw, bar_h)
    p = _painter(img)
    p.setFont(_font(px))
    p.setPen(QColor(color))
    p.drawText(QRect(6, 0, sw, bar_h), Qt.AlignmentFlag.AlignVCenter.value | Qt.AlignmentFlag.AlignLeft.value, line)
    p.end()
    img.save(path)
    return sw, bar_h


def blank_png(path, W, H):
    img = _image(W, H)
    img.save(path)


def sub_png(path, W, H, text, px, color="#FFFFFF", box=True):
    """One subtitle line on a transparent W x H strip: wrapped, centred, with a dark outline and (optionally) a soft dark box."""
    img = _image(W, H)
    p = _painter(img)
    max_w = int(W * 0.90)
    px = float(px)
    while True:                                           # shrink until it fits the strip (Burmese letters are tall)
        hgt = _wrapped(text, px, max_w)
        if hgt <= H - 8 or px <= 12:
            break
        px *= 0.92
    y = int((H - hgt) / 2)
    if box:
        fm = QFontMetrics(_font(px))
        r = fm.boundingRect(QRect(0, 0, max_w, 100000), FLAGS, text)
        pad = int(px * 0.35)
        bw = min(W, r.width() + 2 * pad + 8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 120))
        p.drawRoundedRect(int((W - bw) / 2), max(0, y - pad // 2), bw, min(H, int(hgt + pad)), pad, pad)
    p.setFont(_font(px))
    for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (2, 2), (-2, -2), (2, -2), (-2, 2)):
        p.setPen(QColor(0, 0, 0, 230))
        p.drawText(QRect(int((W - max_w) / 2) + dx, y + dy, max_w, int(hgt)), FLAGS, text)
    p.setPen(QColor(color))
    p.drawText(QRect(int((W - max_w) / 2), y, max_w, int(hgt)), FLAGS, text)
    p.end()
    img.save(path)
