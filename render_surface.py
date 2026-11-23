"""Подключаемый OpenGL-холст для анимированных областей Qt Widgets."""

from PyQt6.QtGui import QOpenGLContext, QPainter
from PyQt6.QtCore import Qt
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtWidgets import QApplication


def use_opengl():
    app = QApplication.instance()
    if not app.property("sekretHardware") or app.platformName() in ("offscreen", "minimal"):
        return False
    if not hasattr(app, "sekret_gl_available"):
        context = QOpenGLContext()
        app.sekret_gl_available = context.create() and context.isValid()
    return app.sekret_gl_available


class AnimatedSurface(QOpenGLWidget):
    def __init__(self, parent, draw):
        super().__init__(parent)
        self.draw = draw
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintGL(self):
        painter = QPainter(self)
        self.draw(painter)
        painter.end()
