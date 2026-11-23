"""Виджеты и палитра четырёхзонного интерфейса sekret."""

import hashlib
from html import escape
from math import sin

from PyQt6.QtCore import (
    QByteArray, QBuffer, QEasingCurve, QIODevice, QPointF, QPropertyAnimation,
    QRectF, QSize, Qt, pyqtProperty,
)
from PyQt6.QtGui import (
    QColor, QFont, QIcon, QImageReader, QLinearGradient, QMovie, QPainter, QPainterPath,
    QPen, QPixmap,
)
from PyQt6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from branding import APP_NAME, HEADER_LOGO
from models import STATUS
from profile_effects import EffectWidget, avatar_decoration, banner_effect
from render_surface import AnimatedSurface, use_opengl
from ui_theme import GREEN, active_palette


class ShortLabel(QLabel):
    def __init__(self, text):
        super().__init__()
        self.setText(text)

    def setText(self, text):
        self.caption = text
        self.setToolTip(f"<p>{escape(text)}</p>" if text else "")
        self.fit_caption()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_caption()

    def fit_caption(self):
        text = self.fontMetrics().elidedText(self.caption, Qt.TextElideMode.ElideRight, self.contentsRect().width())
        QLabel.setText(self, text)


def label(text="", style=None, wrap=False, *, elide=False):
    widget = ShortLabel(text) if elide else QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if style:
        widget.setObjectName(style)
    widget.setWordWrap(wrap)
    return widget


class BrandLogo(QLabel):
    def __init__(self, height):
        super().__init__()
        self.source = QPixmap(str(HEADER_LOGO))
        self.setObjectName("brandLogo")
        self.setAccessibleName(APP_NAME)
        self.setScaledContents(True)
        if not self.source.isNull():
            self.setFixedSize(round(height * self.source.width() / self.source.height()), height)
        self.refresh_theme()

    def refresh_theme(self):
        if self.source.isNull():
            self.setText(APP_NAME)
            return
        image = QPixmap(self.source)
        painter = QPainter(image)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(image.rect(), QColor(active_palette()["text"]))
        painter.end()
        self.setPixmap(image)


class PillSwitch(QCheckBox):
    def __init__(self, name, checked=False):
        super().__init__()
        self.setAccessibleName(name)
        self.setToolTip(name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(54, 31)
        self.position = float(checked)
        self.setChecked(checked)
        self.animation = QPropertyAnimation(self, b"thumb", self)
        self.animation.setDuration(165)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self.slide)

    @pyqtProperty(float)
    def thumb(self):
        return self.position

    @thumb.setter
    def thumb(self, value):
        self.position = value
        self.update()

    def slide(self, enabled):
        self.animation.stop()
        self.animation.setStartValue(self.position)
        self.animation.setEndValue(float(enabled))
        self.animation.start()

    def hitButton(self, point):
        return self.rect().contains(point)

    def paintEvent(self, _event):
        colors = active_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(.56)
        track = QRectF(1, 2, 52, 27)
        shade = QColor(colors["selected"])
        active = QColor(colors["primary"])
        t = self.position
        shade = QColor(*[round(a + (b - a) * t) for a, b in zip(shade.getRgb()[:3], active.getRgb()[:3])])
        gradient = QLinearGradient(0, 3, 0, 30)
        gradient.setColorAt(0, shade.darker(112))
        gradient.setColorAt(1, shade)
        painter.setPen(QPen(QColor(colors["focus"] if self.hasFocus() else colors["stroke"]), 1))
        painter.setBrush(gradient)
        painter.drawRoundedRect(track, 13.5, 13.5)
        x = 5 + t * 25
        painter.setPen(Qt.PenStyle.NoPen)
        shadow = QColor(0, 0, 0, 33)
        painter.setBrush(shadow)
        painter.drawEllipse(QRectF(x + .5, 6.5, 22, 22))
        painter.setBrush(QColor("#ffffff" if t < .5 else colors["on_primary"]))
        painter.drawEllipse(QRectF(x, 5, 21, 21))


def switch_row(layout, text, checked, callback, *, detail=""):
    row = QHBoxLayout()
    copy = QVBoxLayout()
    copy.setSpacing(2)
    copy.addWidget(label(text, "subheading", True))
    if detail:
        copy.addWidget(label(detail, "muted", True))
    row.addLayout(copy, 1)
    switch = PillSwitch(text, checked)
    switch.toggled.connect(callback)
    row.addWidget(switch, alignment=Qt.AlignmentFlag.AlignVCenter)
    layout.addLayout(row)
    return switch


class AnimatedButton(QPushButton):
    def __init__(self, text, glyph=None):
        super().__init__(text)
        self.glyph = glyph
        self.full_caption = None
        self.progress = 0.0
        self.animation = QPropertyAnimation(self, b"hoverLevel", self)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.pressed.connect(lambda: self.animate_hover(1.0, 85))
        self.released.connect(lambda: self.animate_hover(.65 if self.underMouse() else 0.0, 135))

    @pyqtProperty(float)
    def hoverLevel(self):
        return self.progress

    @hoverLevel.setter
    def hoverLevel(self, value):
        self.progress = value
        self.update()

    def animate_hover(self, value, duration=145):
        self.animation.stop()
        self.animation.setDuration(duration)
        self.animation.setStartValue(self.progress)
        self.animation.setEndValue(value)
        self.animation.start()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.animate_hover(.65)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.animate_hover(0.0)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.isEnabled() or self.progress < .02:
            return
        palette = active_palette()
        role = self.property("role")
        tone = QColor(
            "#c93e4d" if role == "closeWindow" else
            palette["on_primary"] if role == "primary" else palette["text"]
        )
        tone.setAlpha(round(self.progress * (64 if role == "closeWindow" else 30)))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(tone)
        radius = 12 if role in ("round", "member") else 8
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), radius, radius)

    def set_glyph(self, glyph):
        self.glyph = glyph
        self.refresh_theme()

    def refresh_theme(self):
        if self.glyph:
            colors = active_palette()
            tone = colors["on_primary"] if self.property("role") == "primary" else colors["text"]
            self.setIcon(icon(self.glyph, tone))

    def elide_caption(self):
        self.full_caption = self.text()
        self.setToolTip(f"<p>{escape(self.full_caption)}</p>")
        self.fit_caption()

    def fit_caption(self):
        if self.full_caption is not None:
            available = max(0, self.width() - (37 if self.glyph else 19))
            self.setText(self.fontMetrics().elidedText(self.full_caption, Qt.TextElideMode.ElideRight, available))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_caption()


class Symbol(QLabel):
    def __init__(self, name, tone="muted", size=16):
        super().__init__()
        self.name, self.tone, self.side = name, tone, size
        self.setFixedSize(size, size)
        self.refresh_theme()

    def set_tone(self, tone):
        if tone != self.tone:
            self.tone = tone
            self.refresh_theme()

    def refresh_theme(self):
        self.setPixmap(icon(self.name, active_palette()[self.tone]).pixmap(self.side, self.side))


def button(text="", callback=None, *, role=None, icon_name=None, toggle=False, width=None):
    widget = AnimatedButton(text, icon_name)
    widget.setCheckable(toggle)
    if role:
        widget.setProperty("role", role)
    if callback:
        widget.clicked.connect(callback)
    if width is not None:
        widget.setFixedWidth(width)
    if icon_name:
        widget.setIconSize(QSize(15, 15))
        widget.refresh_theme()
    return widget


def refresh(widget, prop, value):
    if widget.property(prop) != value:
        widget.setProperty(prop, value)
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()


def column(widget, margins=(0, 0, 0, 0), spacing=8):
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return layout


def panel(kind="card", *, margins=(14, 12, 14, 12), spacing=7):
    frame = QFrame()
    frame.setObjectName(kind)
    return frame, column(frame, margins, spacing)


def scroll_area():
    view = QScrollArea()
    view.setWidgetResizable(True)
    view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    view.viewport().setObjectName("scrollViewport")
    body = QWidget()
    body.setObjectName("scrollBody")
    view.setWidget(body)
    return view, column(body)


def divider():
    widget = QFrame()
    widget.setObjectName("divider")
    return widget


PATHS = {
    "plus": [[(5, 12), (19, 12)], [(12, 5), (12, 19)]],
    "home": [[(3, 11), (12, 3), (21, 11)], [(6, 9), (6, 20), (18, 20), (18, 9)],
             [(10, 20), (10, 14), (14, 14), (14, 20)]],
    "group": [[(3, 18), (10, 6), (15, 13), (17, 10), (22, 18), (3, 18)],
              [(8, 10), (10, 12), (12, 10)]],
    "hash": [[(8, 4), (6, 20)], [(17, 4), (15, 20)], [(4, 9), (21, 9)], [(3, 15), (20, 15)]],
    "voice": [[(3, 10), (7, 10), (12, 5), (12, 19), (7, 14), (3, 14), (3, 10)],
              [(16, 8), (18, 10), (18, 14), (16, 16)], [(19, 5), (22, 9), (22, 15), (19, 19)]],
    "mic": [[(5, 10), (5, 13), (7, 16), (10, 18), (14, 18), (17, 16), (19, 13), (19, 10)],
            [(12, 18), (12, 22)], [(8, 22), (16, 22)]],
    "headphones": [[(4, 14), (4, 10), (6, 5), (10, 3), (14, 3), (18, 5), (20, 10), (20, 14)]],
    "wave": [[(x, 12 - dy), (x, 12 + dy)] for x, dy in ((3, 2), (7, 5), (12, 9), (17, 6), (21, 3))],
    "shield": [[(12, 2), (21, 6), (20, 14), (17, 18), (12, 22), (7, 18), (4, 14), (3, 6), (12, 2)],
               [(8, 12), (11, 15), (17, 9)]],
    "key": [[(11, 11), (20, 20), (22, 18)], [(16, 16), (19, 13)]],
    "copy": [],
    "edit": [[(5, 15), (4, 20), (9, 19), (20, 8), (16, 4), (5, 15)], [(14, 6), (18, 10)]],
    "banner": [[(4, 17), (9, 11), (13, 16), (16, 14), (20, 18)]],
    "color": [],
    "close": [[(6, 6), (18, 18)], [(18, 6), (6, 18)]],
    "send": [[(3, 5), (22, 12), (3, 20), (7, 12), (3, 5)], [(7, 12), (22, 12)]],
    "play": [[(8, 4), (19, 12), (8, 20), (8, 4)]],
    "radio": [[(7, 7), (7, 5), (14, 2)], [(7, 11), (17, 11)]],
    "robot": [[(12, 7), (12, 3)], [(9, 16), (15, 16)]],
    "bolt": [[(13, 3), (5, 13), (11, 13), (10, 21), (19, 10), (13, 10), (13, 3)]],
    "spark": [[(12, 2), (14, 9), (22, 12), (14, 15), (12, 22), (10, 15), (2, 12), (10, 9), (12, 2)]],
    "mask": [[(5, 8), (8, 5), (16, 5), (19, 8)], [(8, 19), (12, 22), (16, 19)]],
    "sliders": [[(3, y), (21, y)] for y in (5, 12, 19)],
    "panel-left": [[(9, 5), (9, 19)]],
    "panel-right": [[(15, 5), (15, 19)]],
    "minimize": [[(6, 12), (18, 12)]],
    "maximize": [],
    "restore": [[(8, 7), (8, 4), (20, 4), (20, 16), (17, 16)]],
    "screen": [[(8, 21), (16, 21)], [(12, 17), (12, 21)]],
    "fullscreen": [[(4, 9), (4, 4), (9, 4)], [(15, 4), (20, 4), (20, 9)],
                   [(20, 15), (20, 20), (15, 20)], [(9, 20), (4, 20), (4, 15)]],
}


def icon(name, color=None):
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(2, 2)
    pen = QPen(QColor(color or active_palette()["text"]), 1.65)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    for points in PATHS[name]:
        path = QPainterPath(QPointF(*points[0]))
        for coords in points[1:]:
            path.lineTo(QPointF(*coords))
        painter.drawPath(path)
    boxes = {
        "mic": [(9, 2, 6, 12, 3)], "headphones": [(3, 12, 5, 8, 2), (16, 12, 5, 8, 2)],
        "copy": [(7, 7, 13, 14, 2), (3, 3, 13, 14, 2)], "banner": [(3, 5, 18, 14, 2)],
        "radio": [(4, 7, 16, 12, 3)], "robot": [(4, 7, 16, 12, 3)], "mask": [(4, 7, 16, 12, 3)],
        "panel-left": [(3, 4, 18, 16, 2)], "panel-right": [(3, 4, 18, 16, 2)],
        "maximize": [(5, 5, 14, 14, 1)], "restore": [(4, 8, 12, 12, 1)],
        "screen": [(3, 4, 18, 13, 2)],
    }
    for x, y, w, h, radius in boxes.get(name, ()):
        painter.drawRoundedRect(QRectF(x, y, w, h), radius, radius)
    circles = {
        "key": [(8, 8, 4.5)], "color": [(9, 10, 4), (15, 10, 4), (12, 15, 4)],
        "robot": [(8.5, 12, 1.2), (15.5, 12, 1.2)], "mask": [(8.5, 12, 1.2), (15.5, 12, 1.2)],
        "sliders": [(8, 5, 2.1), (15, 12, 2.1), (11, 19, 2.1)],
    }
    for x, y, radius in circles.get(name, ()):
        painter.drawEllipse(QPointF(x, y), radius, radius)
    painter.end()
    return QIcon(pixmap)


def avatar_image(data):
    if not data:
        return QPixmap()
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer, b"jpeg")
    if reader.size() == QSize(128, 128):
        return QPixmap.fromImage(reader.read())
    return QPixmap()


class Avatar(EffectWidget):
    def __init__(self, side, profile, *, show_status=False, ring=False, decorations=True):
        self.decoration = profile.decoration if decorations else "none"
        super().__init__(self.decoration != "none")
        self.setFixedSize(side, side)
        self.profile = profile
        self.photo = avatar_image(profile.avatar)
        self.show_status = show_status
        self.ring = ring

    def paintEvent(self, _event):
        width = self.width()
        canvas = QPainter(self)
        canvas.setRenderHint(QPainter.RenderHint.Antialiasing)
        margin = max(4, round(width / 10)) if self.animated else 3 if self.ring else 1
        circle = QRectF(margin, margin, width - margin * 2, width - margin * 2)
        path = QPainterPath()
        path.addEllipse(circle)
        canvas.save()
        canvas.setClipPath(path)
        if not self.photo.isNull():
            canvas.drawPixmap(circle, self.photo, QRectF(self.photo.rect()))
        else:
            tone = hashlib.blake2s(self.profile.name.encode(), digest_size=1).digest()[0] % 33
            gradient = QLinearGradient(0, 0, width, width)
            gradient.setColorAt(0, QColor(72 + tone, 72 + tone, 72 + tone))
            gradient.setColorAt(1, QColor(41 + tone, 41 + tone, 41 + tone))
            canvas.fillRect(circle, gradient)
            canvas.setPen(QColor("#ececec"))
            font = QFont()
            font.setBold(True)
            font.setPixelSize(round(width * .40))
            canvas.setFont(font)
            canvas.drawText(circle, Qt.AlignmentFlag.AlignCenter, self.profile.name[:1].upper())
        canvas.restore()
        if self.animated:
            avatar_decoration(canvas, self.decoration, width, self.phase())
        if self.ring:
            canvas.setPen(QPen(QColor(GREEN), 1.5))
            canvas.drawEllipse(QRectF(1, 1, width - 2, width - 2))
        if self.show_status:
            colors = active_palette()
            status_color = {"online": colors["text"], "busy": colors["muted"], "invisible": colors["disabled"]}
            canvas.setPen(QPen(QColor(colors["card"]), 2))
            canvas.setBrush(QColor(status_color[self.profile.status]))
            canvas.drawEllipse(QPointF(width * .82, width * .82), max(3, width * .1), max(3, width * .1))


class Banner(EffectWidget):
    def __init__(self, profile, height, gif=b""):
        super().__init__(profile.effect != "none" and not gif)
        self.profile = profile
        self.setFixedHeight(height)
        self.movie = None
        self.surface = AnimatedSurface(self, self.draw_banner) if (self.animated or gif) and use_opengl() else None
        if gif:
            self.gif_data = QByteArray(gif)
            self.gif_buffer = QBuffer(self.gif_data, self)
            self.gif_buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            self.movie = QMovie(self.gif_buffer, QByteArray(b"gif"), self)
            self.movie.frameChanged.connect(lambda _frame: self.repaint_frame())
            self.movie.finished.connect(self.restart_movie)

    def restart_movie(self):
        if self.isVisible():
            self.movie.jumpToFrame(0)
            self.movie.start()

    def showEvent(self, event):
        super().showEvent(event)
        if self.movie is not None:
            if self.movie.state() == QMovie.MovieState.Paused:
                self.movie.setPaused(False)
            else:
                self.movie.start()

    def hideEvent(self, event):
        if self.movie is not None:
            self.movie.setPaused(True)
        super().hideEvent(event)

    def repaint_frame(self):
        (self.surface or self).update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.surface is not None:
            self.surface.setGeometry(self.rect())

    def paintEvent(self, _event):
        if self.surface is None:
            self.draw_banner(QPainter(self))

    def draw_banner(self, canvas):
        canvas.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self.width(), self.height()
        if self.surface is not None:
            canvas.fillRect(self.rect(), QColor(active_palette()["card"]))
        mask = QPainterPath()
        mask.addRoundedRect(QRectF(0, 0, width, height), 8, 8)
        canvas.setClipPath(mask)
        gradient = QLinearGradient(0, 0, width, height)
        gradient.setColorAt(0, QColor(self.profile.banner_first))
        gradient.setColorAt(1, QColor(self.profile.banner_last))
        canvas.fillRect(self.rect(), gradient)
        pixmap = self.movie.currentPixmap() if self.movie is not None else None
        if pixmap is not None and not pixmap.isNull():
            size = pixmap.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding)
            target = QRectF((width - size.width()) / 2, (height - size.height()) / 2, size.width(), size.height())
            canvas.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            canvas.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
            return
        accent = QColor(self.profile.accent).lighter(155)
        accent.setAlpha(95)
        canvas.setPen(Qt.PenStyle.NoPen)
        canvas.setBrush(accent)
        canvas.drawEllipse(QPointF(width * .82, height * .31), height * .15, height * .15)
        for row in range(4):
            accent.setAlpha(100 - row * 17)
            canvas.setPen(QPen(accent, 1))
            path = QPainterPath()
            for index in range(47):
                point = QPointF(index * width / 45, height * (.73 + row * .09)
                                + sin(index / (6.8 + row * .35) + row / 2) * height * .17)
                if index == 0:
                    path.moveTo(point)
                else:
                    path.lineTo(point)
            canvas.drawPath(path)
        if self.animated:
            banner_effect(canvas, self.profile.effect, width, height, self.phase())


class ProfileHeader(QWidget):
    def __init__(self, profile, *, own=False, edit=None, on_select=None, gif=b""):
        super().__init__()
        self.banner_h = 40 if own else 76
        self.side = 37 if own else 52
        self.setFixedHeight(self.banner_h + (40 if own else 47))
        self.banner = Banner(profile, self.banner_h, gif)
        self.banner.setParent(self)
        self.avatar = Avatar(self.side, profile, show_status=True)
        self.avatar.setParent(self)
        self.name = label(profile.name, "subheading" if not own else None, elide=True)
        self.name.setParent(self)
        status = STATUS[profile.status] if own or edit is not None or profile.status != "invisible" else "Не в сети"
        self.status = label(status, "muted", elide=True)
        self.status.setParent(self)
        self.edit = None
        if edit is not None:
            self.edit = button(callback=edit, role="flat", icon_name="edit", width=27)
            self.edit.setToolTip("Изменить профиль")
            self.edit.setAccessibleName("Изменить профиль")
            self.edit.setParent(self)
        if on_select is not None:
            self.avatar.mousePressEvent = lambda _event: on_select()
            self.avatar.setCursor(Qt.CursorShape.PointingHandCursor)

    def resizeEvent(self, _event):
        width = self.width()
        self.banner.setGeometry(0, 0, width, self.banner_h)
        self.avatar.move(7, self.banner_h - self.side // 2)
        left = 16 + self.side
        reserved = 28 if self.edit is not None else 0
        self.name.setGeometry(left, self.banner_h + 2, max(0, width - left - reserved), 19)
        self.status.setGeometry(left, self.banner_h + 22, max(0, width - left), 17)
        if self.edit is not None:
            self.edit.move(width - 26, self.banner_h - 2)


class Meter(QWidget):
    def __init__(self, threshold=None):
        super().__init__()
        self.level = -90.0
        self.threshold = threshold
        self.setFixedHeight(11)
        self.setAccessibleName("Уровень микрофона")

    def set_level(self, value):
        self.level = value
        self.update()

    def set_threshold(self, value):
        self.threshold = value
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        step = self.width() / 32
        active = min(32, max(0, round((self.level + 75) / 75 * 32)))
        for index in range(32):
            fill = active_palette()["meter"] if index >= active else GREEN
            painter.setBrush(QColor(fill))
            painter.drawRoundedRect(QRectF(index * step, 1, max(1, step - 3), 9), 2, 2)
        if self.threshold is not None:
            position = self.width() * (self.threshold + 75) / 75
            painter.setPen(QPen(QColor(active_palette()["text"]), 2))
            painter.drawLine(QPointF(position, 0), QPointF(position, self.height()))
