"""Приложение Qt и единый фоновый цикл для комнат и звука."""

import asyncio
import queue
import secrets
import sys
import threading

sys.dont_write_bytecode = True

from cryptography.exceptions import InvalidTag
from PyQt6.QtCore import QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication
from av.error import FFmpegError

from audio import Audio, DEVICES_ERRORS, devices
from branding import APP_NAME, ASSETS
from crypto import Identity, b64, unb64
from network import VOICE, Room
from screen_media import FPS, LatestImage, ScreenEncoder, ScreenTrack
from ui import Window


def callsign():
    nouns = ("Барс", "Ворон", "Ирбис", "Кедр", "Лотос", "Риф", "Сокол", "Ясень")
    return f"{secrets.choice(nouns)}-{secrets.randbelow(10000):04d}"


class SessionWorker(QThread):
    session_event = pyqtSignal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.commands = queue.SimpleQueue()
        self.stopping = threading.Event()
        self.room = None
        self.signaling = None
        self.ending = None
        self.audio = None
        self.first_identity = None
        self.screen_images = LatestImage()
        self.screen_task = None
        self.screen_frames = {}
        self.screen_frames_lock = threading.Lock()

    def screen_image(self, image):
        if not self.stopping.is_set():
            self.screen_images.put(image)

    def take_screen_frame(self, public):
        with self.screen_frames_lock:
            return self.screen_frames.pop(public, None)

    def send(self, command, *args):
        if not self.stopping.is_set():
            self.commands.put((command, args))

    def stop(self):
        self.stopping.set()

    def emit(self, kind, data):
        if not self.stopping.is_set():
            if kind == "speaking":
                public, speaking = data
                data = (b64(public), speaking)
            self.session_event.emit(kind, data)

    def room_event(self, kind, data):
        if kind == "ended":
            self.ending = data or ""
        elif kind == "screen_frame":
            public = data[0]
            with self.screen_frames_lock:
                pending = public in self.screen_frames
                self.screen_frames[public] = data
            if not pending:
                self.emit("screen_ready", public)
        else:
            if kind == "screen_ended":
                self.take_screen_frame(data)
            if kind == "screen_stopped" and self.screen_task is not None:
                self.screen_task.cancel()
            if kind == "connected":
                self.audio.session(self.room.identity.public)
            self.emit(kind, data)

    def run(self):
        try:
            asyncio.run(self.serve())
        except Exception as error:
            self.emit("fatal", f"Фоновый поток завершился с ошибкой ({type(error).__name__}).")
        finally:
            while not self.commands.empty():
                self.commands.get_nowait()

    async def signal(self, operation, action, args):
        failed = False
        try:
            await action(*args)
        except InvalidTag:
            self.emit("error", "Не удалось расшифровать токен. Проверьте приглашение и ключ.")
            failed = True
        except (ValueError, OSError, KeyError, TypeError, AssertionError) as error:
            message = str(error) if isinstance(error, ValueError) else ""
            self.emit("error", message or f"Не удалось установить соединение ({type(error).__name__}).")
            failed = True
        except Exception as error:
            self.emit("error", f"Ошибка сигналинга ({type(error).__name__}).")
            failed = True
        finally:
            self.emit("busy", False)
        if failed and operation == "join":
            self.ending = ""

    def start_signal(self, command, args):
        if self.signaling is not None and not self.signaling.done():
            return
        operations = {"invite": self.room.invite, "join": self.room.join, "answer": self.room.accept_answer}
        self.emit("busy", True)
        self.signaling = asyncio.create_task(self.signal(command, operations[command], args))

    async def cancel_signal(self):
        if self.signaling is not None:
            self.signaling.cancel()
            await asyncio.gather(self.signaling, return_exceptions=True)
            self.signaling = None

    async def end_room(self, status):
        await self.cancel_signal()
        if self.room is not None:
            await self.stop_screen()
            await self.room.close()
            self.room = None
            with self.screen_frames_lock:
                self.screen_frames.clear()
            if not self.stopping.is_set():
                self.audio.session(None)
                self.emit("ended", status)
        self.ending = None

    async def dispatch(self, command, args):
        if command == "audio":
            self.audio.settings(*args)
            return
        if command == "device":
            self.audio.change_device(*args)
            return
        if command == "volume":
            public, value = args
            self.audio.peer_volume(unb64(public, 32), value)
            return
        if command in ("host", "join"):
            if self.room is not None:
                raise ValueError("Сначала выйдите из текущей комнаты.")
            profile, mode, *options = args
            host = command == "host"
            group, voice = options if host else (None, None)
            self.room = Room(host, profile.name, mode, self.room_event, self.audio, self.first_identity,
                             group=group, voice=voice, profile=profile)
            self.first_identity = None
            if host:
                self.audio.session(self.room.identity.public)
                self.room.show_roster()
            else:
                self.start_signal("join", options)
            return
        if self.room is None:
            return
        if command == "leave":
            await self.end_room("")
        elif command == "screen_start":
            await self.stop_screen()
            fps, = args
            try:
                if fps not in FPS:
                    raise ValueError("Частота кадров не поддерживается.")
                self.room.start_screen()
                self.screen_task = asyncio.create_task(self.share_screen(self.room, fps))
                self.emit("screen_started", None)
            except ValueError as error:
                self.emit("screen_stopped", str(error))
        elif command == "screen_stop":
            await self.stop_screen()
        elif command == "cancel":
            await self.cancel_signal()
            await self.room.cancel_invite()
            self.emit("busy", False)
        elif command in ("invite", "answer"):
            self.start_signal(command, args)
        else:
            handlers = {
                "text": self.room.text, "sas": self.room.show_sas, "verify": self.room.verify,
                "profile": self.room.update_profile, "move": self.room.move,
                "channel": self.room.add_channel,
            }
            handlers[command](*args)

    async def share_screen(self, room, fps):
        track, encoder = ScreenTrack(self.screen_images, fps), ScreenEncoder(fps)
        try:
            while room.screen_sharing:
                frame = await track.recv()
                data = await asyncio.to_thread(encoder.encode, frame)
                if data is not None:
                    room.send_screen(data)
        except (FFmpegError, OSError, ValueError):
            room.stop_screen()
            self.emit("screen_stopped", "Не удалось закодировать демонстрацию экрана.")
        finally:
            track.stop()

    async def stop_screen(self):
        task, self.screen_task = self.screen_task, None
        self.screen_images.take()
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.room.stop_screen()

    def send_voice(self, payload):
        if self.room is not None:
            self.room.broadcast(VOICE, payload)

    async def serve(self):
        self.audio = Audio(self.emit)
        self.first_identity = Identity()
        try:
            try:
                self.emit("devices", devices())
            except DEVICES_ERRORS:
                self.emit("audio_error", "Не удалось получить список аудиоустройств.")
            while not self.stopping.is_set():
                for _ in range(32):
                    try:
                        command, args = self.commands.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        await self.dispatch(command, args)
                    except (ValueError, OSError) as error:
                        self.emit("error", str(error))
                    finally:
                        del args
                if self.room is not None:
                    await self.room.tick()
                if self.ending is not None:
                    await self.end_room(self.ending)
                self.audio.capture(self.send_voice)
                self.audio.tick()
                await asyncio.sleep(.006)
        finally:
            if self.first_identity is not None:
                self.first_identity.close()
            self.audio.close()
            await self.end_room("")


def main():
    smoke = "--self-test" in sys.argv
    if sys.platform == "win32" and not smoke:
        from ctypes import c_wchar_p, windll

        windll.shell32.SetCurrentProcessExplicitAppUserModelID(c_wchar_p(f"getfers.{APP_NAME}"))
    app = QApplication([sys.argv[0], "-platform", "offscreen"] if smoke else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    window = Window(callsign(), SessionWorker, ephemeral=smoke)
    app.setWindowIcon(window.windowIcon())
    window.show()
    if smoke:
        window.host_room(invite=False)
        ready = False
        timer = QTimer(window)

        def check_room():
            nonlocal ready
            if window.members:
                ready = True
                timer.stop()
                window.close()

        timer.timeout.connect(check_room)
        timer.start(20)
        QTimer.singleShot(10000, window.close)
        app.exec()
        header = window.header_logo.pixmap()
        assets_ok = (
            not window.windowIcon().isNull() and not window.tray.icon().isNull()
            and header is not None and not header.isNull()
            and (ASSETS / "notification.wav").is_file()
        )
        return 0 if ready and assets_ok else 1
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
