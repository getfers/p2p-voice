"""Один таймер перерисовывает видимые процедурные украшения профилей."""

import math
import time
from weakref import WeakSet

from PyQt6.QtCore import QObject, QPointF, QRectF, QTimer, Qt
from PyQt6.QtGui import QColor, QFont, QPainterPath, QPen, QRadialGradient
from PyQt6.QtWidgets import QApplication, QWidget


class ProfileClock(QObject):
    def __init__(self, app):
        super().__init__(app)
        self.widgets = WeakSet()
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.tick)

    def add(self, widget):
        self.widgets.add(widget)
        if not self.timer.isActive():
            self.timer.start()

    def remove(self, widget):
        self.widgets.discard(widget)
        if not self.widgets:
            self.timer.stop()

    def tick(self):
        for widget in list(self.widgets):
            try:
                if widget.isVisible() and not widget.window().isMinimized():
                    widget.update()
            except RuntimeError:
                self.remove(widget)
        if not self.widgets:
            self.timer.stop()


class EffectWidget(QWidget):
    def __init__(self, animated):
        super().__init__()
        self.animated = animated
        self.elapsed = 0.0
        self.clock = None
        if animated:
            app = QApplication.instance()
            if not hasattr(app, "profile_clock"):
                app.profile_clock = ProfileClock(app)
            self.clock = app.profile_clock
            self.elapsed = time.monotonic()

    def phase(self):
        return time.monotonic() - self.elapsed if self.animated else 0.0

    def showEvent(self, event):
        super().showEvent(event)
        if self.clock is not None:
            self.clock.add(self)

    def hideEvent(self, event):
        if self.clock is not None:
            self.clock.remove(self)
        super().hideEvent(event)


def tint(color, alpha):
    value = QColor(color)
    value.setAlpha(round(max(0, min(255, alpha))))
    return value


def arc(painter, rect, angle, span, color, alpha, width):
    pen = QPen(tint(color, alpha), width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(rect, round(angle * 16), round(span * 16))


def point(painter, x, y, radius, color, alpha):
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(tint(color, alpha))
    painter.drawEllipse(QPointF(x, y), radius, radius)


def avatar_decoration(painter, name, side, time):
    center = side / 2
    radius = center - 3.2
    bounds = QRectF(center - radius, center - radius, radius * 2, radius * 2)
    stroke = max(1.25, side / 27)
    if name == "eyes":
        arc(painter, bounds, time * 33, 314, "#655a94", 220, stroke)
        for direction in (-1, 1):
            angle = time * .85 + direction * .21
            x = center + math.sin(angle) * radius
            y = center - math.cos(angle) * radius
            point(painter, x, y, stroke * 1.4, "#28233b", 255)
            point(painter, x + math.sin(time * 2) * .75, y, stroke * .69, "#dccaff", 225)
    elif name == "smoke":
        for i in range(5):
            offset = math.sin(time * 1.3 + i) * 18
            arc(painter, bounds.adjusted(i * .31, i * .31, -i * .31, -i * .31),
                72 * i + offset + time * 13, 52, "#9d79c0" if i % 2 else "#87a2bf",
                123 + 47 * math.sin(time * 1.9 + i), stroke * 1.5)
    elif name == "neon":
        pulse = .5 + .5 * math.sin(time * 4.3)
        arc(painter, bounds, 0, 360, "#0fbdad", 95 + 142 * pulse, stroke * (1.5 + pulse))
        arc(painter, bounds, time * 80, 97, "#9df3e6", 225, stroke)
    elif name == "cyber":
        for i in range(8):
            arc(painter, bounds, 45 * i + time * 64, 26, "#2b647d" if i % 2 else "#51b6e3",
                235, stroke if i % 2 else stroke * 1.65)
    elif name == "radar":
        arc(painter, bounds, 0, 360, "#41856b", 130, stroke)
        for i in range(4):
            arc(painter, bounds, time * 110 - i * 20, 20, "#24d49c", 255 - 49 * i, stroke * 1.55)
    elif name == "sparks":
        arc(painter, bounds, 0, 360, "#c58a41", 148, stroke)
        for i in range(7):
            angle = i * math.tau / 7 + time * .6
            strength = .5 + .5 * math.sin(time * 4 + i * 1.8)
            x, y = center + math.cos(angle) * radius, center + math.sin(angle) * radius
            point(painter, x, y, .65 + stroke * strength, "#ffca63", 92 + strength * 160)


def banner_effect(painter, name, width, height, time):
    if name == "shadows":
        for i in range(13):
            x = width * ((i * .618 + .03) % 1)
            y = (time * (17 + i % 5) + i * 23) % (height + 26) - 13
            point(painter, x, y, 4 + i % 4, "#121321", 60)
            point(painter, x, y + 6, 2 + i % 3, "#17172b", 42)
    elif name == "waves":
        for i in range(4):
            path = QPainterPath()
            for step in range(36):
                x = width * step / 35
                y = height * (.28 + i * .14) + math.sin(step / 6 - time * 1.5 + i) * height * .15
                if step == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            painter.setPen(QPen(tint("#daf8ff", 70 + i * 10), 1.4 + i * .45))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
    elif name == "matrix":
        font = QFont("Consolas")
        font.setPixelSize(max(8, height // 7))
        painter.setFont(font)
        for i in range(0, width, 13):
            base = (time * (19 + i % 7) + i * 2.6) % max(1, height)
            for trail in range(4):
                y = (base - trail * 10) % max(1, height)
                painter.setPen(tint("#a1ffde", 198 - trail * 38))
                painter.drawText(QPointF(i, y), str((int(time * 6) + i + trail) % 2))
    elif name == "aura":
        for i in range(3):
            x = width * (.50 + .37 * math.sin(time * .7 + i * 1.9))
            y = height * (.47 + .26 * math.sin(time * .75 + i))
            radius = max(18, height * .72)
            glow = QRadialGradient(x, y, radius)
            glow.setColorAt(0.0, tint(("#caaaff", "#a1f7e0", "#fff0b7")[i], 157))
            glow.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(QPointF(x, y), radius, radius)
