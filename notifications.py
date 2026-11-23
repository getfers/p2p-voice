"""Тосты, системное внимание и локальный звук уведомления."""

import time

from PyQt6.QtCore import QEasingCurve, QParallelAnimationGroup, QPoint, QPropertyAnimation, QTimer, Qt, QUrl
from PyQt6.QtMultimedia import QSoundEffect
from PyQt6.QtWidgets import QApplication, QFrame, QGraphicsOpacityEffect, QSystemTrayIcon

from branding import APP_NAME, ASSETS
from ui_widgets import column, label


class Toast(QFrame):
    def __init__(self, window):
        super().__init__(window)
        self.host = window
        self.setObjectName("toast")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout = column(self, (15, 12, 15, 12), 4)
        self.heading = label("", "subheading", True)
        self.body = label("", "muted", True)
        layout.addWidget(self.heading)
        layout.addWidget(self.body)
        self.opacity = QGraphicsOpacityEffect(self)
        self.opacity.setOpacity(0.0)
        self.setGraphicsEffect(self.opacity)
        self.sequence = QParallelAnimationGroup(self)
        self.position = QPropertyAnimation(self, b"pos")
        self.alpha = QPropertyAnimation(self.opacity, b"opacity")
        self.sequence.addAnimation(self.position)
        self.sequence.addAnimation(self.alpha)
        self.exiting = False
        self.sequence.finished.connect(self.reached_edge)
        self.expiry = QTimer(self, singleShot=True)
        self.expiry.timeout.connect(self.dismiss)
        self.hide()

    def anchor(self):
        return QPoint(self.host.width() - self.width() - 20,
                      self.host.height() - self.height() - 20)

    def place(self):
        if self.isVisible():
            self.move(self.anchor())

    def present(self, heading, body):
        self.sequence.stop()
        self.exiting = False
        self.heading.setText(heading)
        self.body.setText(body)
        self.setFixedWidth(min(348, self.host.width() - 40))
        self.adjustSize()
        self.setFixedHeight(max(68, self.layout().heightForWidth(self.width())))
        end = self.anchor()
        self.move(end + QPoint(22, 14))
        self.opacity.setOpacity(0.0)
        self.show()
        self.raise_()
        self.transition(end, 1.0, 205)
        self.expiry.start(3400)

    def dismiss(self):
        self.exiting = True
        self.transition(self.anchor() + QPoint(20, 12), 0.0, 155)

    def transition(self, point, opacity, duration):
        self.sequence.stop()
        for animation, start, end in (
            (self.position, self.pos(), point),
            (self.alpha, self.opacity.opacity(), opacity),
        ):
            animation.setDuration(duration)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            animation.setStartValue(start)
            animation.setEndValue(end)
        self.sequence.start()

    def reached_edge(self):
        if self.exiting:
            self.clear()
        else:
            self.place()

    def clear(self):
        self.expiry.stop()
        self.sequence.stop()
        self.hide()
        self.heading.clear()
        self.body.clear()


class Notifications:
    def __init__(self, window, tray):
        self.window, self.tray = window, tray
        self.sound_enabled = True
        self.last_sound = -1.0
        self.last_native = -1.0
        self.pending = None
        self.toast = Toast(window)
        self.sound = QSoundEffect(window)
        self.sound.setVolume(.34)
        self.sound.setSource(QUrl.fromLocalFile(str(ASSETS / "notification.wav")))
        self.tray.messageClicked.connect(window.restore_window)

    def publish(self, heading, body, system_body):
        body = " ".join(body.split())
        if len(body) > 145:
            body = body[:142].rstrip() + "…"
        if self.window.isMinimized():
            self.pending = (heading, body)
        else:
            self.toast.present(heading, body)
        now = time.monotonic()
        if self.sound_enabled and now - self.last_sound >= .55:
            self.sound.play()
            self.last_sound = now
        if self.window.isMinimized() or not self.window.isActiveWindow():
            QApplication.alert(self.window, 2800)
            if self.tray.isVisible() and QSystemTrayIcon.supportsMessages() and now - self.last_native >= .7:
                self.tray.showMessage(APP_NAME, system_body, self.window.windowIcon(), 4000)
                self.last_native = now

    def resume(self):
        if self.pending is not None and not self.window.isMinimized():
            self.toast.present(*self.pending)
            self.pending = None

    def set_sound(self, enabled):
        self.sound_enabled = enabled
        if not enabled:
            self.sound.stop()

    def clear(self):
        self.pending = None
        self.toast.clear()
        self.sound.stop()
