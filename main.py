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

from audio import Audio, DEVICES_ERRORS, devices
from crypto import Identity, b64
from network import VOICE, Room
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
        else:
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
            await self.room.close()
            self.room = None
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
    app = QApplication([sys.argv[0], "-platform", "offscreen"] if smoke else sys.argv)
    app.setApplicationName("Эфир")
    window = Window(callsign(), SessionWorker, ephemeral=smoke)
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
        return 0 if ready else 1
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
