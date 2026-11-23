"""Точка входа приложения и сетевой цикл в отдельном потоке Qt."""

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
from crypto import b64
from network import VOICE, Room
from ui import Window


def callsign():
    nouns = ("Барс", "Ворон", "Ирбис", "Кедр", "Лотос", "Риф", "Сокол", "Ясень")
    return f"{secrets.choice(nouns)}-{secrets.randbelow(10000):04d}"


class SessionWorker(QThread):
    session_event = pyqtSignal(str, object)

    def __init__(self, host, name, mode, identity, parent=None):
        super().__init__(parent)
        self.args = host, name, mode
        self.identity = identity
        self.commands = queue.SimpleQueue()
        self.stopping = threading.Event()

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

    def run(self):
        try:
            asyncio.run(self.serve())
        except Exception as error:
            self.emit("ended", f"Сеанс завершён из-за ошибки ({type(error).__name__}).")
        finally:
            self.identity.close()
            while not self.commands.empty():
                self.commands.get_nowait()

    async def signal(self, action, args):
        try:
            await action(*args)
        except InvalidTag:
            self.emit("error", "Не удалось расшифровать токен. Проверьте приглашение и ключ.")
        except (ValueError, OSError, KeyError, TypeError, AssertionError) as error:
            message = str(error) if isinstance(error, ValueError) else ""
            self.emit("error", message or f"Не удалось установить соединение ({type(error).__name__}).")
        except Exception as error:
            self.emit("error", f"Ошибка сигналинга ({type(error).__name__}).")
        else:
            return
        finally:
            self.emit("busy", False)
        if action.__name__ == "join":
            self.emit("ended", None)

    async def serve(self):
        audio = Audio(self.emit)
        room = Room(*self.args, self.emit, audio, self.identity)
        operations = {"invite": room.invite, "join": room.join, "answer": room.accept_answer}
        handlers = {
            "text": room.text, "sas": room.show_sas, "verify": room.verify,
            "audio": audio.settings, "device": audio.change_device,
        }
        signaling = None
        try:
            room.show_roster()
            try:
                self.emit("devices", devices())
            except DEVICES_ERRORS:
                self.emit("audio_error", "Не удалось получить список аудиоустройств.")
            audio.start(room.identity.public)
            while not self.stopping.is_set():
                for _ in range(32):
                    try:
                        command, args = self.commands.get_nowait()
                    except queue.Empty:
                        break
                    if command == "cancel":
                        if signaling is not None and not signaling.done():
                            signaling.cancel()
                            await asyncio.gather(signaling, return_exceptions=True)
                        await room.cancel_invite()
                        self.emit("busy", False)
                    elif command in operations:
                        if signaling is None or signaling.done():
                            self.emit("busy", True)
                            signaling = asyncio.create_task(self.signal(operations[command], args))
                    else:
                        try:
                            handlers[command](*args)
                        except (ValueError, OSError) as error:
                            self.emit("error", str(error))
                    del args
                await room.tick()
                audio.capture(lambda data: room.broadcast(VOICE, data))
                audio.tick()
                await asyncio.sleep(0.008)
        finally:
            if signaling is not None:
                signaling.cancel()
                await asyncio.gather(signaling, return_exceptions=True)
            audio.close()
            await room.close()


def main():
    smoke = "--self-test" in sys.argv
    app = QApplication([sys.argv[0], "-platform", "offscreen"] if smoke else sys.argv)
    app.setApplicationName("Эфир")
    window = Window(callsign(), SessionWorker)
    window.show()
    if smoke:
        window.host()
        ready = False
        timer = QTimer(window)

        def check_room():
            nonlocal ready
            if window.panel is not None and window.panel.rows:
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
