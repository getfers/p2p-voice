"""Захват экрана в потоке Qt и просмотр демонстрации в звонке."""

from PyQt6.QtCore import QObject, QRect, QSize, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtWidgets import QApplication, QComboBox, QDialog, QFormLayout, QHBoxLayout, QSlider, QWidget

from screen_media import FPS, MAX_WIDTH, QUALITY
from render_surface import AnimatedSurface, use_opengl
from ui_chrome import AnimatedDialog
from ui_widgets import Symbol, button, column, label


LOCAL_SCREEN = "local-screen"


def video_choices(height, fps):
    resolution = QComboBox()
    frames = QComboBox()
    for value in QUALITY:
        resolution.addItem(f"До {value}p", value)
    for value in FPS:
        frames.addItem(f"{value} кадров/с", value)
    resolution.setCurrentIndex(resolution.findData(height))
    frames.setCurrentIndex(frames.findData(fps))
    return resolution, frames


class ShareDialog(AnimatedDialog):
    def __init__(self, window, start):
        super().__init__(window)
        self.setWindowTitle("Демонстрация экрана")
        self.setMinimumWidth(415)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        layout = column(self, (21, 20, 21, 20), 12)
        layout.addWidget(label("Демонстрация экрана", "subheading"))
        layout.addWidget(label("Проверенные собеседники в этом голосовом канале увидят весь выбранный экран.",
                               "muted", True))
        form = QFormLayout()
        monitor = QComboBox()
        for screen in QApplication.screens():
            size = screen.size()
            monitor.addItem(f"{screen.name()} · {size.width()}×{size.height()}", screen)
        monitor.setCurrentIndex(monitor.findData(QApplication.primaryScreen()))
        height, fps = video_choices(window.screen_height, window.screen_fps)
        form.addRow("Весь экран", monitor)
        form.addRow("Разрешение", height)
        form.addRow("Частота кадров", fps)
        layout.addLayout(form)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(button("Отмена", self.reject))
        confirm = button("Начать показ", self.accept, role="primary", icon_name="screen")
        confirm.setEnabled(monitor.count() > 0)
        self.accepted.connect(lambda: start(monitor.currentData(), height.currentData(), fps.currentData()))
        row.addWidget(confirm)
        layout.addLayout(row)


class ScreenCapture(QObject):
    failed = pyqtSignal(str)

    def __init__(self, parent, deliver):
        super().__init__(parent)
        self.deliver = deliver
        self.screen = None
        self.height = 720
        self.timer = QTimer(self, timerType=Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.capture)
        QApplication.instance().screenRemoved.connect(self.removed)

    def start(self, screen, height, fps):
        self.screen, self.height = screen, height
        if self.capture():
            self.timer.start(round(1000 / fps))

    def stop(self):
        self.timer.stop()
        self.screen = None

    def removed(self, screen):
        if self.screen == screen:
            self.stop()
            self.failed.emit("Экран отключён. Демонстрация остановлена.")

    def capture(self):
        if self.screen is None:
            return False
        screenshot = self.screen.grabWindow(0)
        if screenshot.isNull():
            self.stop()
            self.failed.emit("Не удалось захватить экран. Проверьте разрешения на запись экрана.")
            return False
        limit = QSize(round(MAX_WIDTH * self.height / 1080), self.height)
        image = screenshot.toImage()
        if image.width() > limit.width() or image.height() > limit.height():
            image = image.scaled(limit, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        width, height = image.width() & ~1, image.height() & ~1
        if width != image.width() or height != image.height():
            image = image.copy(0, 0, width, height)
        self.deliver(image.convertToFormat(QImage.Format.Format_ARGB32))
        return True


class ScreenCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self.image = QImage()
        self.setMinimumHeight(100)
        self.surface = AnimatedSurface(self, self.draw_frame) if use_opengl() else None

    def set_frame(self, image):
        self.image = image
        (self.surface or self).update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.surface is not None:
            self.surface.setGeometry(self.rect())

    def paintEvent(self, _event):
        if self.surface is None:
            self.draw_frame(QPainter(self))

    def draw_frame(self, painter):
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        if not self.image.isNull():
            size = self.image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRect(0, 0, size.width(), size.height())
            target.moveCenter(self.rect().center())
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(target, self.image)


class ScreenViewer(QWidget):
    def __init__(self, change_volume):
        super().__init__()
        self.change_volume = change_volume
        self.frames = {}
        self.volumes = {}
        self.fullscreen = None
        layout = self.viewer_layout = column(self, spacing=5)
        top = QHBoxLayout()
        top.addWidget(Symbol("screen"))
        top.addWidget(label("Демонстрация экрана", "subheading"))
        self.source = QComboBox()
        self.source.currentIndexChanged.connect(self.selected)
        top.addWidget(self.source, 1)
        layout.addLayout(top)
        self.canvas = ScreenCanvas()
        layout.addWidget(self.canvas, 1)
        options = QHBoxLayout()
        options.addWidget(label("Громкость голоса", "muted"))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setAccessibleName("Громкость голоса демонстрирующего")
        self.volume.setRange(0, 200)
        self.volume.valueChanged.connect(self.set_volume)
        options.addWidget(self.volume, 1)
        self.volume_caption = label("100%", "muted")
        options.addWidget(self.volume_caption)
        self.expand = button(callback=self.toggle_fullscreen, icon_name="fullscreen", width=36)
        self.expand.setToolTip("На весь экран")
        self.expand.setAccessibleName("На весь экран")
        options.addWidget(self.expand)
        layout.addLayout(options)
        self.hide()

    def add_frame(self, public, name, width, height, pixels):
        image = QImage(pixels, width, height, width * 3, QImage.Format.Format_RGB888).copy()
        self.add_image(public, name, image)

    def add_image(self, public, name, image):
        self.frames[public] = image
        if self.source.findData(public) < 0:
            self.source.addItem(name, public)
            self.show()
            if public == LOCAL_SCREEN:
                self.source.setCurrentIndex(self.source.findData(public))
        if public == self.source.currentData():
            self.canvas.set_frame(image)

    def selected(self, _index):
        public = self.source.currentData()
        self.canvas.set_frame(self.frames.get(public, QImage()))
        self.volume.blockSignals(True)
        self.volume.setValue(self.volumes.get(public, 100))
        self.volume.blockSignals(False)
        self.volume.setEnabled(public not in (None, LOCAL_SCREEN))
        self.volume_caption.setText(f"{self.volume.value()}%" if self.volume.isEnabled() else "—")

    def set_volume(self, value):
        public = self.source.currentData()
        if public not in (None, LOCAL_SCREEN):
            self.volumes[public] = value
            self.volume_caption.setText(f"{value}%")
            self.change_volume(public, value)

    def update_peer_volume(self, public, value):
        self.volumes[public] = value
        if self.source.currentData() == public:
            self.selected(self.source.currentIndex())

    def end(self, public):
        self.frames.pop(public, None)
        index = self.source.findData(public)
        if index >= 0:
            self.source.removeItem(index)
        if not self.frames:
            if self.fullscreen is not None:
                self.fullscreen.reject()
            self.hide()

    def clear(self):
        for public in list(self.frames):
            self.end(public)
        self.volumes.clear()

    def toggle_fullscreen(self):
        if self.fullscreen is not None:
            self.fullscreen.reject()
            return
        dialog = self.fullscreen = QDialog(self.window())
        dialog.setWindowTitle("Демонстрация экрана")
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        layout = column(dialog, (12, 12, 12, 12))
        layout.addWidget(self.canvas, 1)
        row = QHBoxLayout()
        row.addWidget(label("Громкость голоса", "muted"))
        row.addWidget(self.volume, 1)
        row.addWidget(self.volume_caption)
        row.addWidget(button("Выйти из полноэкранного режима", dialog.reject, icon_name="fullscreen"))
        layout.addLayout(row)
        dialog.finished.connect(self.restore_canvas)
        dialog.showFullScreen()

    def restore_canvas(self):
        self.fullscreen = None
        self.viewer_layout.insertWidget(1, self.canvas, 1)
        self.viewer_layout.itemAt(2).layout().insertWidget(1, self.volume, 1)
        self.viewer_layout.itemAt(2).layout().insertWidget(2, self.volume_caption)
        self.canvas.show()
        self.volume.show()
        self.volume_caption.show()
