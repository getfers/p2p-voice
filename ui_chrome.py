"""Безрамное окно, разделители и короткие переходы Qt."""

from PyQt6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, QRect, QVariantAnimation, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication, QDialog, QGraphicsOpacityEffect, QMainWindow, QSizePolicy, QSplitter, QWidget


EDGE = Qt.Edge
CURSOR = Qt.CursorShape
LEFT_BUTTON = Qt.MouseButton.LeftButton
MAX_SIZE = 16777215


class Fade:
    def __init__(self, widget):
        self.widget = widget
        self.animation = None
        self.effect = None

    def start(self, end, duration, callback=None, *, start=None):
        if self.animation is not None:
            self.animation.stop()
            self.animation.deleteLater()
        if QApplication.platformName() in ("windows", "xcb", "cocoa"):
            target, prop = self.widget, b"windowOpacity"
        else:
            if self.effect is None:
                self.effect = QGraphicsOpacityEffect(self.widget)
                self.effect.setOpacity(1.0)
                self.widget.setGraphicsEffect(self.effect)
            target, prop = self.effect, b"opacity"
        animation = QPropertyAnimation(target, prop, self.widget)
        self.animation = animation
        animation.setDuration(duration)
        animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
        animation.setStartValue(target.property(prop.decode()) if start is None else start)
        animation.setEndValue(end)

        def finish():
            self.animation = None
            animation.deleteLater()
            if end == 1.0 and self.effect is not None:
                self.widget.setGraphicsEffect(None)
                self.effect = None
            if callback is not None:
                callback()

        animation.finished.connect(finish)
        animation.start()


class AnimatedDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.fade = Fade(self)
        self.finishing = False

    def showEvent(self, event):
        super().showEvent(event)
        self.fade.start(1.0, 165, start=0.0)

    def done(self, result):
        if self.finishing:
            return
        if not self.isVisible():
            super().done(result)
            return
        self.finishing = True
        self.setEnabled(False)

        def end():
            self.setEnabled(True)
            self.finishing = False
            QDialog.done(self, result)

        self.fade.start(0.0, 135, end)

    def closeEvent(self, event):
        # Кнопка заголовка ОС использует тот же переход, что Escape и кнопки диалога.
        event.ignore()
        self.reject()


def resized_rectangle(rect, delta, edges, minimum, maximum):
    x, y, w, h = rect.getRect()
    def bound(size, lower, upper):
        return min(upper, max(lower, size))

    if edges & EDGE.LeftEdge:
        width = bound(w - delta.x(), minimum.width(), maximum.width())
        x, w = x + w - width, width
    elif edges & EDGE.RightEdge:
        w = bound(w + delta.x(), minimum.width(), maximum.width())
    if edges & EDGE.TopEdge:
        height = bound(h - delta.y(), minimum.height(), maximum.height())
        y, h = y + h - height, height
    elif edges & EDGE.BottomEdge:
        h = bound(h + delta.y(), minimum.height(), maximum.height())
    return QRect(x, y, w, h)


class EdgeGrip(QWidget):
    def __init__(self, window, edges, cursor):
        super().__init__(window)
        self.host = window
        self.edges = edges
        self.anchor = None
        self.original = None
        self.setCursor(cursor)

    def mousePressEvent(self, event):
        if event.button() == LEFT_BUTTON:
            self.anchor = None
            if not self.host.native_resize(self.edges):
                self.anchor = event.globalPosition().toPoint()
                self.original = self.host.geometry()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.anchor is not None and event.buttons() & LEFT_BUTTON:
            delta = event.globalPosition().toPoint() - self.anchor
            self.host.setGeometry(resized_rectangle(
                self.original, delta, self.edges, self.host.minimumSize(), self.host.maximumSize(),
            ))
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.anchor = None
        super().mouseReleaseEvent(event)


class FramelessWindow(QMainWindow):
    state_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.grips = [
            EdgeGrip(self, edges, shape) for edges, shape in (
                (EDGE.LeftEdge, CURSOR.SizeHorCursor), (EDGE.RightEdge, CURSOR.SizeHorCursor),
                (EDGE.TopEdge, CURSOR.SizeVerCursor), (EDGE.BottomEdge, CURSOR.SizeVerCursor),
                (EDGE.TopEdge | EDGE.LeftEdge, CURSOR.SizeFDiagCursor),
                (EDGE.TopEdge | EDGE.RightEdge, CURSOR.SizeBDiagCursor),
                (EDGE.BottomEdge | EDGE.LeftEdge, CURSOR.SizeBDiagCursor),
                (EDGE.BottomEdge | EDGE.RightEdge, CURSOR.SizeFDiagCursor),
            )
        ]
        self.fade = Fade(self)
        self.first_show = True

    def native_move(self):
        handle = self.windowHandle()
        return bool(handle and handle.startSystemMove())

    def native_resize(self, edges):
        handle = self.windowHandle()
        return bool(handle and handle.startSystemResize(edges))

    def toggle_maximized(self):
        if self.isFullScreen() or self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def showEvent(self, event):
        super().showEvent(event)
        self.place_grips()
        if self.first_show:
            self.first_show = False
            self.fade.start(1.0, 220, start=0.0)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "grips"):
            self.place_grips()
            self.state_changed.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.place_grips()

    def place_grips(self):
        if not hasattr(self, "grips"):
            return
        width, height = self.width(), self.height()
        edge, corner = 6, 10
        normal = not (self.isMaximized() or self.isFullScreen() or self.isMinimized())
        positions = (
            (0, corner, edge, height - corner * 2), (width - edge, corner, edge, height - corner * 2),
            (corner, 0, width - corner * 2, edge), (corner, height - edge, width - corner * 2, edge),
            (0, 0, corner, corner), (width - corner, 0, corner, corner),
            (0, height - corner, corner, corner), (width - corner, height - corner, corner, corner),
        )
        for grip, position in zip(self.grips, positions):
            grip.setGeometry(*position)
            grip.setVisible(normal)
            grip.raise_()


class TitleBar(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.host = window
        self.anchor = None
        self.offset = None
        self.dragging = False
        self.setObjectName("titlebar")

    def mousePressEvent(self, event):
        if event.button() == LEFT_BUTTON:
            self.anchor = event.globalPosition().toPoint()
            self.offset = self.host.mapFromGlobal(self.anchor)
            self.dragging = False
            if QApplication.platformName().startswith("wayland") and not self.host.isMaximized():
                if self.host.native_move():
                    self.anchor = None
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.anchor is None or not event.buttons() & LEFT_BUTTON:
            super().mouseMoveEvent(event)
            return
        point = event.globalPosition().toPoint()
        if not self.dragging:
            if (point - self.anchor).manhattanLength() < QApplication.startDragDistance():
                return
            if self.host.isMaximized() or self.host.isFullScreen():
                fraction = self.offset.x() / max(1, self.host.width())
                self.host.showNormal()
                self.offset = QPoint(round(self.host.width() * fraction), self.offset.y())
                self.host.move(point - self.offset)
            if self.host.native_move():
                self.anchor = None
                return
            self.dragging = True
        self.host.move(point - self.offset)
        event.accept()

    def mouseReleaseEvent(self, event):
        self.anchor = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == LEFT_BUTTON:
            self.anchor = None
            self.host.toggle_maximized()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)


class Splitter(QSplitter):
    def __init__(self, orientation):
        super().__init__(orientation)
        self.setChildrenCollapsible(False)
        self.setHandleWidth(8)
        self.setOpaqueResize(True)


class PaneTransition(QObject):
    changed = pyqtSignal(bool)

    def __init__(self, splitter, pane, workspace, width):
        super().__init__(splitter)
        self.splitter = splitter
        self.pane = pane
        self.workspace = workspace
        self.width = width
        self.minimum = pane.minimumWidth()
        self.policy = pane.sizePolicy()
        self.expanded = True
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(190)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.animation.valueChanged.connect(self.step)
        self.animation.finished.connect(self.finish)

    def toggle(self):
        self.set_expanded(not self.expanded)

    def set_expanded(self, expanded):
        if self.expanded == expanded:
            return
        self.animation.stop()
        start = 0 if self.pane.isHidden() else self.pane.width()
        if not expanded and start >= self.minimum:
            self.width = start
        self.expanded = expanded
        self.pane.setMinimumWidth(0)
        self.pane.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.pane.setMaximumWidth(start)
        self.pane.show()
        self.animation.setStartValue(start)
        self.animation.setEndValue(self.width if expanded else 0)
        self.animation.start()
        self.changed.emit(expanded)

    def step(self, width):
        sizes = self.splitter.sizes()
        pane_index = self.splitter.indexOf(self.pane)
        center_index = self.splitter.indexOf(self.workspace)
        total = sizes[pane_index] + sizes[center_index]
        maximum = max(0, total - self.workspace.minimumWidth())
        width = min(width, maximum)
        self.pane.setMaximumWidth(width)
        sizes[pane_index], sizes[center_index] = width, total - width
        self.splitter.setSizes(sizes)

    def finish(self):
        if not self.expanded:
            self.pane.hide()
        self.pane.setMaximumWidth(MAX_SIZE)
        self.pane.setMinimumWidth(self.minimum)
        self.pane.setSizePolicy(self.policy)
