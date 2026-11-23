"""Проверки размеров, разделителей и завершения безрамного окна Qt."""

import unittest
from pathlib import Path

from PyQt6.QtCore import QObject, QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from branding import APP_NAME
from ui import Window
from ui_chrome import resized_rectangle


class GeometryTests(unittest.TestCase):
    def test_corner_moves_only_its_edges_and_keeps_minimum_size(self):
        rectangle = QRect(100, 180, 1100, 720)
        edges = Qt.Edge.LeftEdge | Qt.Edge.TopEdge
        result = resized_rectangle(rectangle, QPoint(400, 450), edges, QSize(960, 640), QSize(4096, 4096))
        self.assertEqual(result, QRect(240, 260, 960, 640))
        result = resized_rectangle(rectangle, QPoint(50, 70), Qt.Edge.RightEdge | Qt.Edge.BottomEdge,
                                   QSize(960, 640), QSize(4096, 4096))
        self.assertEqual(result, QRect(100, 180, 1150, 790))


class StubWorker(QObject):
    session_event = pyqtSignal(str, object)
    finished = pyqtSignal()

    def start(self):
        pass

    def stop(self):
        self.finished.emit()

    def send(self, *args):
        pass


class CompactWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([APP_NAME, "-platform", "offscreen"])
        # У offscreen-плагина Windows нет каталога системных шрифтов.
        for name in ("segoeui.ttf", "segoeuib.ttf", "consola.ttf"):
            path = Path("C:/Windows/Fonts") / name
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))

    def test_compact_layout_theme_and_closing_fade(self):
        window = Window("Тест", StubWorker, ephemeral=True)
        window.notifications.set_sound(False)
        try:
            window.resize(960, 640)
            window.show()
            self.assertEqual(window.fade.animation.duration(), 220)
            QTest.qWait(245)
            self.assertEqual(window.size(), QSize(960, 640))
            self.assertTrue(window.windowFlags() & Qt.WindowType.FramelessWindowHint)
            self.assertTrue(window.compact_call)
            self.assertEqual(window.columns.count(), 3)
            self.assertEqual(window.room_split.count(), 2)
            self.assertLessEqual(window.inspector_scroll.widget().width(), window.inspector_scroll.viewport().width())
            window.inspector_toggle.click()
            QTest.qWait(235)
            self.assertTrue(window.inspector.isHidden())
            window.inspector_toggle.click()
            QTest.qWait(235)
            self.assertTrue(window.inspector.isVisible())
            light = window.header_logo.pixmap().toImage()
            window.set_theme("dark")
            self.assertNotEqual(light, window.header_logo.pixmap().toImage())
            window.close()
            self.assertEqual(window.fade.animation.duration(), 180)
            self.assertTrue(window.isVisible())
        finally:
            if not window.closing:
                window.close()
            QTest.qWait(230)
        self.assertTrue(window.worker_done)
        self.assertTrue(window.fade_done)
        self.assertFalse(window.isVisible())
