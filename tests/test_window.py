"""Проверки размеров, разделителей и завершения безрамного окна Qt."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from time import time
from unittest.mock import patch

from PyQt6.QtCore import QObject, QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication

from branding import APP_NAME
from crypto import b64, new_invite_key, seal_document
from network import DIRECT, STUN
from ui import Window
from ui_chrome import resized_rectangle
from ui_widgets import ProfileHeader


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

    def __init__(self, parent):
        super().__init__(parent)
        self.commands = []

    def start(self):
        pass

    def stop(self):
        self.finished.emit()

    def send(self, *args):
        self.commands.append(args)


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

    def test_full_settings_select_animated_local_banner_and_decoration(self):
        window = Window("Тест", StubWorker, ephemeral=True)
        window.show()
        self.addCleanup(lambda: QTest.qWait(205))
        self.addCleanup(window.close)
        window.show_settings()
        self.assertIs(window.body_stack.currentWidget(), window.settings)
        editor = window.settings.editor
        frames = bytes.fromhex(
            "21f90400080000002c0000000001000100000202440100"
            "21f90400080000002c00000000010001000002024c0100"
        )
        with TemporaryDirectory() as folder:
            source = Path(folder) / "banner.gif"
            source.write_bytes(
                b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"
                b"\x21\xff\x0bNETSCAPE2.0\x03\x01\0\0\0" + frames + b"\x3b"
            )
            with patch("ui_dialogs.QFileDialog.getOpenFileName", return_value=(str(source), "GIF (*.gif)")):
                editor.gif_button.click()
            next(control for control in editor.gallery.buttons() if control.accessibleName() == "Скан-радар").click()
            movie = editor.preview.banner.movie
            spy = QSignalSpy(movie.frameChanged)
            self.assertTrue(spy.wait(1000))
            self.assertTrue(spy.wait(1000))
            self.assertGreaterEqual(len(spy), 2)
            editor.submit()
            self.assertEqual(window.profile.decoration, "radar")
            self.assertEqual(window.profile.public().decoration, "none")
            mini = window.self_layout.itemAt(0).widget()
            self.assertIsInstance(mini, ProfileHeader)
            self.assertIsNotNone(mini.banner.movie)
        window.settings.show_section("voice")
        settled = QSignalSpy(window.settings.noise.animation.finished)
        window.settings.noise.click()
        self.assertTrue(settled.wait(1000))
        self.assertEqual(window.settings.noise.thumb, 0.0)
        self.assertIn(("audio", "noise", False), window.worker.commands)
        window.settings.show_section("appearance")
        window.set_scale(125)
        window.close_settings()
        self.assertIs(window.body_stack.currentWidget(), window.main_body)

    def test_invitation_warns_and_switches_only_on_consent(self):
        window = Window("Гость", StubWorker, ephemeral=True)
        window.show()
        self.addCleanup(lambda: QTest.qWait(205))
        self.addCleanup(window.close)
        window.choose_mode(DIRECT)
        window.open_join()
        dialog = window.signal_dialog
        key = new_invite_key()
        dialog.token.setText(seal_document(b"invite", {"mode": STUN, "stun": True, "expires": int(time()) + 30}, key))
        dialog.key.setText(b64(new_invite_key()))
        QTest.qWait(355)
        self.assertIn("не совпадают", dialog.notice.text())
        dialog.key.setText(b64(key))
        QTest.qWait(355)
        self.assertIn("Хост выбрал STUN", dialog.notice.text())
        dialog.primary.click()
        self.assertFalse(any(command[0] == "join" for command in window.worker.commands))
        self.assertTrue(dialog.strict.isChecked())
        dialog.match_mode.click()
        self.assertFalse(dialog.strict.isChecked())
        dialog.primary.click()
        self.assertTrue(window.connecting)
        self.assertEqual(window.worker.commands[-1][:3], ("join", window.profile, STUN))
        dialog.reject()

    def test_viewer_fullscreen_volume_and_stop(self):
        window = Window("Тест", StubWorker, ephemeral=True)
        window.show()
        self.addCleanup(lambda: QTest.qWait(205))
        self.addCleanup(window.close)
        window.host_room(invite=False)
        voice = window.current_channel("voice").key
        window.dispatch("members", [("self", "Тест", True, False, voice), ("guest", "Гость", False, True, voice)])
        window.show_screen_frame("guest", 8, 8, bytes(range(192)))
        viewer = window.screen_viewer
        self.assertTrue(viewer.isVisible())
        viewer.volume.setValue(45)
        self.assertIn(("volume", "guest", 45), window.worker.commands)
        viewer.toggle_fullscreen()
        self.assertTrue(viewer.fullscreen.isFullScreen())
        window.end_screen_view("guest")
        self.assertIsNone(viewer.fullscreen)
        self.assertTrue(viewer.isHidden())
