"""Виджеты и палитра четырёхзонного интерфейса sekret."""

import hashlib
from math import sin

from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import (
    QColor, QFont, QIcon, QImageReader, QLinearGradient, QPainter, QPainterPath,
    QPen, QPixmap,
)
from PyQt6.QtWidgets import QFrame, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from branding import APP_NAME, HEADER_LOGO
from models import STATUS


FOREGROUND = "#ececec"
MUTED = "#9b9b9b"
GREEN = "#10a37f"

STYLE = """
QWidget { color: #ececec; font-family: "Segoe UI"; font-size: 13px; }
QMainWindow, QDialog, QWidget#workspace { background: #212121; }
QWidget#titlebar, QWidget#groupRail, QWidget#navigation, QWidget#inspector { background: #171717; }
QWidget#titlebar { border-bottom: 1px solid #383838; }
QWidget#groupRail, QWidget#navigation { border-right: 1px solid #383838; }
QWidget#inspector { border-left: 1px solid #383838; }
QFrame#card, QFrame#inset { background: #2f2f2f; border: 1px solid #383838; border-radius: 14px; }
QFrame#divider { min-height: 1px; max-height: 1px; background: #383838; border: none; }
QLabel#muted { color: #9b9b9b; }
QLabel#section { color: #9b9b9b; font-size: 11px; font-weight: 600; }
QLabel#subheading { font-size: 15px; font-weight: 600; }
QLabel#groupTitle { font-size: 22px; font-weight: 600; }
QLabel#channelTitle { font-size: 34px; font-weight: 600; }
QLabel#sas { font-family: "Consolas", monospace; font-size: 20px; font-weight: 700; }
QLabel#secure, QLabel#meterLevel[active="true"] { color: #81cbb8; }
QLabel#secure[protected="false"], QLabel#meterLevel { color: #9b9b9b; }
QLabel#badge { background: #233c35; color: #81cbb8; border-radius: 11px; padding: 5px 9px; }
QLabel#badge[encrypted="false"] { background: #383838; color: #b3b3b3; }
QLabel#notice { color: #cfcfcf; }
QPushButton, QLineEdit, QComboBox, QSpinBox {
  background: #2f2f2f; border: 1px solid #383838; border-radius: 9px;
  selection-background-color: #686868; selection-color: #ffffff;
}
QPushButton { min-height: 28px; padding: 1px 9px; font-size: 12px; font-weight: 600; }
QPushButton:hover { border-color: #515151; background: #3b3b3b; }
QPushButton:pressed { background: #424242; }
QPushButton:focus, QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #737373; }
QPushButton:disabled { color: #808080; border-color: #383838; background: #292929; }
QPushButton:checked { background: #414141; border-color: #646464; }
QPushButton[role="primary"] { background: #ececec; border-color: #ececec; color: #171717; }
QPushButton[role="primary"]:hover { background: #ffffff; border-color: #ffffff; }
QPushButton[role="primary"]:pressed { background: #d2d2d2; border-color: #d2d2d2; }
QPushButton[role="primary"]:disabled { background: #383838; border-color: #414141; color: #8b8b8b; }
QPushButton[role="flat"], QPushButton[role="row"] {
  background: transparent; border: 1px solid transparent; color: #9b9b9b;
}
QPushButton[role="row"] { text-align: left; padding-left: 9px; }
QPushButton[role="flat"]:hover, QPushButton[role="row"]:hover { background: #353535; color: #ececec; }
QPushButton[role="flat"]:focus, QPushButton[role="row"]:focus { border-color: #737373; }
QPushButton[role="row"]:checked { background: #2f2f2f; color: #ececec; border-left: 2px solid #a5a5a5; }
QPushButton[role="flat"]:disabled { color: #808080; }
QPushButton#microphone:checked, QPushButton#microphoneCheck:checked, QPushButton#microphoneMonitor:checked {
  background: #263932; border: 1px solid #10a37f; color: #a2ded0;
}
QPushButton[role="round"] {
  background: #262626; border: 1px solid #383838; border-radius: 14px;
  min-height: 0; padding: 0; font-size: 17px; font-weight: 600;
}
QPushButton[role="round"]:hover { background: #383838; }
QPushButton[role="round"]:checked { background: #414141; border-color: #6a6a6a; }
QPushButton[role="round"]:focus { border-color: #818181; }
QPushButton[role="member"] { min-height: 104px; padding: 0; background: #292929; border-radius: 13px; }
QPushButton[role="member"]:hover { background: #343434; }
QPushButton[role="member"][selected="true"] { border-color: #686868; }
QPushButton[role="member"][speaking="true"] { border: 1px solid #10a37f; background: #263632; }
QPushButton[role="presence"] { min-height: 26px; padding: 0; background: transparent; border: 1px solid transparent; }
QPushButton[role="presence"]:hover { background: #2f2f2f; }
QPushButton[role="presence"][speaking="true"] { border-color: #10a37f; background: #20312b; }
QLineEdit, QComboBox, QSpinBox { min-height: 28px; padding: 2px 9px; }
QLineEdit[readOnly="true"] { color: #d6d6d6; }
QLineEdit:disabled { color: #858585; }
QComboBox QAbstractItemView, QMenu {
  background: #2f2f2f; border: 1px solid #383838; selection-background-color: #4b4b4b;
}
QMenu::item { padding: 6px 22px 6px 10px; }
QMenu::item:selected { background: #454545; }
QCheckBox { spacing: 7px; }
QScrollArea { border: none; background: transparent; }
QWidget#scrollBody, QWidget#scrollViewport { background: transparent; }
QScrollBar:vertical { background: transparent; width: 7px; margin: 2px; }
QScrollBar::handle:vertical { background: #505050; border-radius: 3px; min-height: 19px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0px; width: 0px; }
"""


def label(text="", style=None, wrap=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if style:
        widget.setObjectName(style)
    widget.setWordWrap(wrap)
    return widget


def brand_logo(height):
    image = QPixmap(str(HEADER_LOGO))
    if image.isNull():
        return label(APP_NAME, "subheading")
    widget = label(style="brandLogo")
    widget.setPixmap(image)
    widget.setScaledContents(True)
    widget.setFixedSize(round(height * image.width() / image.height()), height)
    widget.setAccessibleName(APP_NAME)
    return widget


def button(text="", callback=None, *, role=None, icon_name=None, toggle=False, width=None):
    widget = QPushButton(text)
    widget.setCheckable(toggle)
    if role:
        widget.setProperty("role", role)
    if callback:
        widget.clicked.connect(callback)
    if width is not None:
        widget.setFixedWidth(width)
    if icon_name:
        widget.setIcon(icon(icon_name, "#171717" if role == "primary" else FOREGROUND))
        widget.setIconSize(QSize(15, 15))
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
}


def icon(name, color=FOREGROUND):
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(2, 2)
    pen = QPen(QColor(color), 1.65)
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


class Avatar(QWidget):
    def __init__(self, side, profile, *, show_status=False, ring=False):
        super().__init__()
        self.setFixedSize(side, side)
        self.profile = profile
        self.photo = avatar_image(profile.avatar)
        self.show_status = show_status
        self.ring = ring

    def paintEvent(self, _event):
        width = self.width()
        canvas = QPainter(self)
        canvas.setRenderHint(QPainter.RenderHint.Antialiasing)
        margin = 3 if self.ring else 1
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
            canvas.setPen(QColor(FOREGROUND))
            font = QFont()
            font.setBold(True)
            font.setPixelSize(round(width * .40))
            canvas.setFont(font)
            canvas.drawText(circle, Qt.AlignmentFlag.AlignCenter, self.profile.name[:1].upper())
        canvas.restore()
        if self.ring:
            canvas.setPen(QPen(QColor(GREEN), 1.5))
            canvas.drawEllipse(QRectF(1, 1, width - 2, width - 2))
        if self.show_status:
            status_color = {"online": "#dedede", "busy": "#969696", "invisible": "#676767"}
            canvas.setPen(QPen(QColor("#2f2f2f"), 2))
            canvas.setBrush(QColor(status_color[self.profile.status]))
            canvas.drawEllipse(QPointF(width * .82, width * .82), max(3, width * .1), max(3, width * .1))


class Banner(QWidget):
    def __init__(self, profile, height):
        super().__init__()
        self.profile = profile
        self.setFixedHeight(height)

    def paintEvent(self, _event):
        canvas = QPainter(self)
        canvas.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self.width(), self.height()
        mask = QPainterPath()
        mask.addRoundedRect(QRectF(0, 0, width, height), 8, 8)
        canvas.setClipPath(mask)
        gradient = QLinearGradient(0, 0, width, height)
        gradient.setColorAt(0, QColor(self.profile.banner_first))
        gradient.setColorAt(1, QColor(self.profile.banner_last))
        canvas.fillRect(self.rect(), gradient)
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


class ProfileHeader(QWidget):
    def __init__(self, profile, *, own=False, edit=None, on_select=None):
        super().__init__()
        self.banner_h = 40 if own else 76
        self.side = 37 if own else 52
        self.setFixedHeight(self.banner_h + (40 if own else 47))
        self.banner = Banner(profile, self.banner_h)
        self.banner.setParent(self)
        self.avatar = Avatar(self.side, profile, show_status=True)
        self.avatar.setParent(self)
        self.name = label(profile.name, "subheading" if not own else None)
        self.name.setParent(self)
        status = STATUS[profile.status] if own or edit is not None or profile.status != "invisible" else "Не в сети"
        self.status = label(status, "muted")
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
        self.name.setToolTip(self.name.text())
        if self.edit is not None:
            self.edit.move(width - 26, self.banner_h - 2)


class Meter(QWidget):
    def __init__(self):
        super().__init__()
        self.level = -90.0
        self.setFixedHeight(11)
        self.setAccessibleName("Уровень микрофона")

    def set_level(self, value):
        self.level = value
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        step = self.width() / 32
        active = min(32, max(0, round((self.level + 60) / 60 * 32)))
        for index in range(32):
            fill = "#444444" if index >= active else GREEN
            painter.setBrush(QColor(fill))
            painter.drawRoundedRect(QRectF(index * step, 1, max(1, step - 3), 9), 2, 2)
