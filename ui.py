"""Окна входа, комнаты и ручного обмена приглашениями."""

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QFont, QTextCursor
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QPushButton, QScrollArea, QSpinBox, QStackedWidget,
    QTextEdit, QVBoxLayout, QWidget,
)

from crypto import MAX_TOKEN, Identity
from network import DIRECT, PROXY, STUN


STYLE = """
QWidget { color: #edf0f8; font-size: 14px; }
QMainWindow, QDialog, QWidget#root { background: #10121a; }
QFrame#card { background: #191c29; border: 1px solid #2c3041; border-radius: 14px; }
QFrame#person { background: #202435; border: 1px solid #343b51; border-radius: 9px; }
QFrame#person[speaking="true"] { background: #19332f; border: 2px solid #46d9a4; }
QLabel#title { font-size: 37px; font-weight: 750; color: #f6f7ff; }
QLabel#heading { font-size: 19px; font-weight: 650; }
QLabel#muted { color: #a8b0c6; }
QLabel#secure { color: #7de6bd; font-weight: 650; }
QLabel#sas { font-size: 34px; letter-spacing: 7px; color: #c6b7ff; font-weight: 750; }
QPushButton, QLineEdit, QComboBox, QSpinBox {
    border: 1px solid #383d55; border-radius: 8px; padding: 8px 11px; background: #24283a;
}
QPushButton { font-weight: 600; min-height: 18px; }
QPushButton:hover { background: #30354e; border-color: #7770a9; }
QPushButton:checked { background: #392f59; border-color: #a49af4; }
QPushButton#primary { background: #a79bf4; border-color: #a79bf4; color: #15112b; }
QPushButton#primary:hover { background: #c2b8ff; }
QPushButton#danger { color: #ffc0c8; }
QPushButton:disabled, QPushButton#primary:disabled { background: #242636; color: #858ba1; border-color: #303448; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPushButton:focus { border-color: #b3a6ff; }
QLineEdit[readOnly="true"] { color: #c8c1f3; }
QTextEdit { background: #131621; border: 1px solid #303448; border-radius: 8px; padding: 8px; }
QScrollArea { border: none; background: transparent; }
QScrollArea QWidget#people { background: transparent; }
QCheckBox { spacing: 8px; }
QComboBox QAbstractItemView { background: #24283a; selection-background-color: #4a416c; }
"""


def label(text="", style=None, wrap=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if style:
        widget.setObjectName(style)
    widget.setWordWrap(wrap)
    return widget


def button(text, callback=None, style=None, toggle=False):
    widget = QPushButton(text)
    widget.setCheckable(toggle)
    if style:
        widget.setObjectName(style)
    if callback:
        widget.clicked.connect(callback)
    return widget


def field(placeholder="", readonly=False, limit=MAX_TOKEN):
    widget = QLineEdit()
    widget.setPlaceholderText(placeholder)
    widget.setReadOnly(readonly)
    widget.setMaxLength(limit)
    return widget


def card():
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(12)
    return frame, layout


class SasDialog(QDialog):
    def __init__(self, partner, code, confirm, parent):
        super().__init__(parent)
        self.setWindowTitle("Проверка собеседника")
        self.setMinimumWidth(390)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addWidget(label(partner, "heading"))
        layout.addWidget(label(code, "sas"))
        layout.addWidget(label(
            "Сверьте код с собеседником по доверенному каналу. При несовпадении завершите разговор.",
            "muted", True,
        ))
        row = QHBoxLayout()
        row.addWidget(button("Закрыть", self.reject))
        row.addWidget(button("Коды совпали", self.accept, "primary"))
        layout.addLayout(row)
        self.accepted.connect(confirm)


class Lobby(QWidget):
    def __init__(self, name, host, join):
        super().__init__()
        self.join = join
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 26, 28, 26)
        outer.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        panel, layout = card()
        panel.setMinimumWidth(490)
        panel.setMaximumWidth(590)
        layout.addWidget(label("Эфир", "title"))
        layout.addWidget(label(f"Голосовые комнаты · ваш позывной {name}", "muted", True))
        self.strict = QCheckBox("Строгий режим инкогнито · без публичного STUN")
        self.strict.setChecked(True)
        self.transport = QComboBox()
        self.transport.addItem("Прямое соединение по IP", DIRECT)
        self.transport.addItem("Локальный SOCKS5 / Tor", PROXY)
        self.strict.toggled.connect(self.transport.setEnabled)
        layout.addWidget(self.strict)
        layout.addWidget(self.transport)
        actions = QHBoxLayout()
        actions.addWidget(button("Создать комнату", host, "primary"))
        actions.addWidget(button("Подключиться по токену", self.toggle_join))
        layout.addLayout(actions)
        self.form = QWidget()
        form = QFormLayout(self.form)
        form.setContentsMargins(0, 5, 0, 0)
        self.token = field("Вставьте приглашение")
        self.token_key = field("Ключ из отдельного канала", limit=64)
        self.token_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.proxy = field("127.0.0.1:9050", limit=70)
        self.proxy.setText("127.0.0.1:9050")
        self.address = field("Необязательно · внешний IP:порт UDP", limit=100)
        form.addRow("Приглашение", self.token)
        form.addRow("Ключ", self.token_key)
        form.addRow("Прокси", self.proxy)
        form.addRow("Мой адрес", self.address)
        action = button("Войти", self.submit, "primary")
        form.addRow("", action)
        for widget in (self.token, self.token_key, self.proxy, self.address):
            widget.returnPressed.connect(self.submit)
        layout.addWidget(self.form)
        self.form.hide()
        self.strict.toggled.connect(self.update_mode)
        self.transport.currentIndexChanged.connect(self.update_mode)
        self.note = label("", "muted", True)
        layout.addWidget(self.note)
        self.update_mode()
        row.addWidget(panel, 1)
        row.addStretch()
        outer.addLayout(row)
        outer.addStretch()

    def mode(self):
        return self.transport.currentData() if self.strict.isChecked() else STUN

    def update_mode(self):
        mode = self.mode()
        self.proxy.setVisible(mode == PROXY)
        self.address.setVisible(mode == DIRECT)
        form = self.form.layout()
        form.labelForField(self.proxy).setVisible(mode == PROXY)
        form.labelForField(self.address).setVisible(mode == DIRECT)
        self.note.setText({
            DIRECT: "Для локальной сети или доступного UDP-адреса. Собеседники видят IP друг друга.",
            PROXY: "Гость подключается через локальный прокси. Хост публикует onion-адрес или адрес моста.",
            STUN: "Публичный STUN определяет внешний UDP-адрес. Голос и чат передаются между участниками.",
        }[mode])

    def toggle_join(self):
        self.form.setVisible(self.form.isHidden())
        if self.form.isVisible():
            self.token.setFocus()

    def submit(self):
        token, key = self.token.text().strip(), self.token_key.text().strip()
        if not token or not key:
            self.note.setText("Введите приглашение и ключ.")
            return
        address = self.address.text().strip() if self.mode() == DIRECT else ""
        self.join(token, key, self.proxy.text().strip(), address)
        self.token.clear()
        self.token_key.clear()


class RoomPanel(QWidget):
    def __init__(self, host, mode, send):
        super().__init__()
        self.host, self.mode, self.send = host, mode, send
        self.rows = {}
        self.speaking = set()
        self.dialogs = set()
        self.busy = False
        self.has_invite = False
        self.keyboard = self.mouse = False
        self.audio_error = {True: False, False: False}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(13)
        bar = QHBoxLayout()
        bar.addWidget(label("Эфир", "title"))
        badge = label("E2EE · X25519", "secure")
        badge.setToolTip("Аудио и чат шифруются отдельным ключом для каждой пары участников.")
        bar.addWidget(badge)
        bar.addStretch()
        transport = {DIRECT: "Без STUN", STUN: "STUN", PROXY: "SOCKS5 / Tor"}[mode]
        bar.addWidget(label(f"{transport} · {'Хост' if host else 'Гость'}", "muted"))
        self.leave = button("Выйти из комнаты", style="danger")
        bar.addWidget(self.leave)
        outer.addLayout(bar)

        columns = QHBoxLayout()
        columns.setSpacing(13)
        left, room = card()
        room.addWidget(label("Участники", "heading"))
        people = QWidget()
        people.setObjectName("people")
        self.people = QVBoxLayout(people)
        self.people.setContentsMargins(0, 0, 0, 0)
        self.people.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(people)
        room.addWidget(scroll, 1)
        self.status = label("Комната открыта." if host else "Подключение…", "muted", True)
        room.addWidget(self.status)
        self.signaling_controls(room)
        columns.addWidget(left, 3)

        right, chat = card()
        right.setMinimumWidth(310)
        chat.addWidget(label("Чат", "heading"))
        self.history = QTextEdit()
        self.history.setReadOnly(True)
        self.history.document().setMaximumBlockCount(400)
        self.history.setPlaceholderText("Сообщения получают участники, с которыми вы подтвердили SAS.")
        chat.addWidget(self.history, 1)
        composer = QHBoxLayout()
        self.message = field("Сообщение", limit=1024)
        self.message.returnPressed.connect(self.submit_chat)
        self.send_chat = button("Отправить", self.submit_chat, "primary")
        self.message.setEnabled(False)
        self.send_chat.setEnabled(False)
        composer.addWidget(self.message, 1)
        composer.addWidget(self.send_chat)
        chat.addLayout(composer)
        columns.addWidget(right, 2)
        outer.addLayout(columns, 1)

        control, controls = card()
        row = QHBoxLayout()
        self.mute = button("Микрофон выкл.", self.set_mute, toggle=True)
        self.mute.setChecked(True)
        self.deafen = button("Звук вкл.", self.set_deafen, toggle=True)
        self.noise = QCheckBox("Шумовой порог")
        self.noise.setChecked(True)
        self.noise.toggled.connect(lambda enabled: send("audio", "noise", enabled))
        self.ptt = QCheckBox("Push-to-Talk")
        self.ptt.toggled.connect(self.toggle_ptt)
        self.speak = button("Говорить · F8")
        self.speak.setEnabled(False)
        self.speak.pressed.connect(lambda: self.set_pressed("mouse", True))
        self.speak.released.connect(lambda: self.set_pressed("mouse", False))
        for widget in (self.mute, self.deafen, self.noise, self.ptt, self.speak):
            row.addWidget(widget)
        row.addStretch()
        controls.addLayout(row)
        devices_row = QHBoxLayout()
        self.inputs = QComboBox()
        self.outputs = QComboBox()
        for capture, combo, name in (
            (True, self.inputs, "Вход"), (False, self.outputs, "Выход")
        ):
            combo.setMinimumContentsLength(15)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.addItem("По умолчанию", None)
            combo.currentIndexChanged.connect(
                lambda _index, cap=capture, box=combo: send("device", cap, box.currentData())
            )
            devices_row.addWidget(label(name, "muted"))
            devices_row.addWidget(combo, 1)
        self.audio_status = label("", "muted", True)
        controls.addLayout(devices_row)
        controls.addWidget(self.audio_status)
        outer.addWidget(control)

    def signaling_controls(self, layout):
        self.token = field("Приглашение", readonly=True)
        self.token_key = field("Ключ приглашения", readonly=True, limit=64)
        self.answer = field("Вставьте ответ гостя")
        self.reply = field("Передайте этот ответ хосту", readonly=True)
        self.note = label("", "muted", True)
        self.note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if self.host:
            self.endpoint = field(
                "Onion-адрес с портом" if self.mode == PROXY else "Внешний IP:порт UDP · необязательно",
                limit=255,
            )
            self.port = QSpinBox()
            self.port.setRange(1, 65535)
            self.port.setValue(17840)
            if self.mode == PROXY:
                ports = QHBoxLayout()
                ports.addWidget(self.endpoint, 1)
                ports.addWidget(label("Локальный порт", "muted"))
                ports.addWidget(self.port)
                layout.addLayout(ports)
            elif self.mode == DIRECT:
                layout.addWidget(self.endpoint)
            row = QHBoxLayout()
            self.invite = button("Создать приглашение", self.make_invite, "primary")
            self.cancel = button("Отменить", lambda: self.send("cancel"))
            row.addWidget(self.invite)
            row.addWidget(self.cancel)
            layout.addLayout(row)
            layout.addWidget(self.token)
            layout.addWidget(self.token_key)
            if self.mode != PROXY:
                response = QHBoxLayout()
                response.addWidget(self.answer, 1)
                self.accept = button("Принять ответ", self.accept_reply)
                response.addWidget(self.accept)
                self.answer.returnPressed.connect(self.accept_reply)
                layout.addLayout(response)
        elif self.mode != PROXY:
            layout.addWidget(self.reply)
        layout.addWidget(self.note)
        self.update_signaling()

    def make_invite(self):
        self.note.clear()
        self.update_signaling(True)
        self.send("invite", self.endpoint.text().strip() if self.mode != STUN else "", self.port.value())

    def accept_reply(self):
        answer = self.answer.text().strip()
        if answer and self.has_invite and not self.busy:
            self.note.clear()
            self.update_signaling(True)
            self.send("answer", answer)
            self.answer.clear()

    def update_signaling(self, busy=None):
        if busy is not None:
            self.busy = busy
        if self.host:
            self.invite.setEnabled(not self.busy and not self.has_invite)
            self.cancel.setEnabled(self.has_invite or self.busy)
            if self.mode != PROXY:
                self.accept.setEnabled(not self.busy and self.has_invite)
                self.answer.setEnabled(not self.busy and self.has_invite)

    def submit_chat(self):
        message = self.message.text().strip()
        if message:
            self.send("text", message)
            self.message.clear()

    def set_mute(self, enabled):
        self.mute.setText("Микрофон выкл." if enabled else "Микрофон вкл.")
        self.send("audio", "muted", enabled)

    def set_deafen(self, enabled):
        self.deafen.setText("Звук выкл." if enabled else "Звук вкл.")
        self.send("audio", "deafened", enabled)

    def toggle_ptt(self, enabled):
        self.send("audio", "ptt", enabled)
        self.speak.setEnabled(enabled)
        if not enabled:
            self.release_ptt()

    def set_pressed(self, source, value):
        setattr(self, source, value)
        self.send("audio", "pressed", self.keyboard or self.mouse)

    def release_ptt(self):
        self.keyboard = self.mouse = False
        self.send("audio", "pressed", False)

    def roster(self, members):
        chat_ready = any(verified for _, _, _, verified in members)
        self.message.setEnabled(chat_ready)
        self.send_chat.setEnabled(chat_ready)
        self.speaking.intersection_update(public for public, *_ in members)
        while self.people.count():
            row = self.people.takeAt(0).widget()
            row.deleteLater()
        self.rows.clear()
        for public, name, own, verified in members:
            row = QFrame()
            row.setObjectName("person")
            row.setProperty("speaking", public in self.speaking)
            layout = QHBoxLayout(row)
            layout.addWidget(label(name + (" · вы" if own else "")), 1)
            if not own:
                layout.addWidget(label("Проверен" if verified else "Не проверен", "secure" if verified else "muted"))
                layout.addWidget(button("SAS", lambda _=False, key=public: self.send("sas", key)))
            self.people.addWidget(row)
            self.rows[public] = row

    def activity(self, public, speaking):
        if speaking:
            self.speaking.add(public)
        else:
            self.speaking.discard(public)
        if public in self.rows:
            widget = self.rows[public]
            widget.setProperty("speaking", speaking)
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def chat(self, name, message):
        cursor = self.history.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.history.document().isEmpty():
            cursor.insertBlock()
        cursor.insertText(f"{name}  ·  {message}")
        self.history.setTextCursor(cursor)
        self.history.ensureCursorVisible()

    def show_devices(self, devices):
        for index, name, inputs, outputs in devices:
            if inputs:
                self.inputs.addItem(name, index)
            if outputs:
                self.outputs.addItem(name, index)

    def device_status(self, capture, available):
        self.audio_error[capture] = not available
        self.audio_status.setText(" · ".join(
            text for direction, text in ((True, "Микрофон недоступен"), (False, "Выход недоступен"))
            if self.audio_error[direction]
        ))

    def show_sas(self, public, name, code):
        dialog = SasDialog(name, code, lambda: self.send("verify", public), self)
        self.dialogs.add(dialog)
        dialog.finished.connect(lambda _: (self.dialogs.discard(dialog), dialog.deleteLater()))
        dialog.open()

    def clear(self):
        for dialog in list(self.dialogs):
            dialog.reject()
        for widget in (self.token, self.token_key, self.answer, self.reply, self.message):
            widget.clear()
        self.history.clear()
        self.roster([])


class Window(QMainWindow):
    def __init__(self, name, worker_type):
        super().__init__()
        self.name = name
        self.identity = Identity()
        self.worker_type = worker_type
        self.worker = None
        self.panel = None
        self.exiting = False
        self.stopping = False
        self.lobby_notice = ""
        self.setWindowTitle("Эфир · P2P голос")
        self.setMinimumSize(900, 640)
        self.resize(1100, 760)
        self.setFont(QFont("Segoe UI", 10))
        self.setStyleSheet(STYLE)
        self.stack = QStackedWidget()
        self.stack.setObjectName("root")
        self.lobby = Lobby(name, self.host, self.join)
        self.stack.addWidget(self.lobby)
        self.setCentralWidget(self.stack)
        app = QApplication.instance()
        app.installEventFilter(self)
        app.applicationStateChanged.connect(self.focus_changed)

    def host(self):
        self.start_room(True)

    def join(self, *args):
        self.start_room(False)
        self.worker.send("join", *args)

    def start_room(self, host):
        self.lobby_notice = ""
        mode = self.lobby.mode()
        self.worker = self.worker_type(host, self.name, mode, self.identity, self)
        self.identity = None
        self.panel = RoomPanel(host, mode, self.worker.send)
        self.panel.leave.clicked.connect(self.leave_room)
        self.stack.addWidget(self.panel)
        self.stack.setCurrentWidget(self.panel)
        self.worker.event.connect(self.dispatch)
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def dispatch(self, kind, data):
        panel = self.panel
        if panel is None or self.stopping:
            return
        if kind == "members":
            panel.roster(data)
        elif kind == "speaking":
            panel.activity(*data)
        elif kind == "chat":
            panel.chat(*data)
        elif kind == "devices":
            panel.show_devices(data)
        elif kind == "device":
            panel.device_status(*data)
        elif kind == "audio_error":
            panel.audio_status.setText(data)
        elif kind == "sas":
            panel.show_sas(*data)
        elif kind == "status":
            panel.status.setText(data)
        elif kind == "addresses":
            panel.note.setText(f"Локальные адреса UDP: {data}")
        elif kind == "error":
            panel.status.setText(data)
            self.lobby_notice = data
        elif kind == "busy":
            panel.update_signaling(data)
        elif kind == "invite":
            token, key = data
            panel.has_invite = bool(token)
            panel.token.setText(token)
            panel.token_key.setText(key)
            panel.update_signaling()
        elif kind == "answer":
            panel.reply.setText(data)
            panel.status.setText("Передайте зашифрованный ответ хосту.")
        elif kind == "connected":
            panel.reply.clear()
        elif kind == "ended":
            self.leave_room(data or self.lobby_notice)

    def leave_room(self, notice=""):
        if not self.worker or self.stopping:
            return
        self.stopping = True
        self.lobby_notice = notice if isinstance(notice, str) else ""
        self.panel.release_ptt()
        self.panel.clear()
        self.panel.setEnabled(False)
        self.panel.status.setText("Закрытие комнаты…")
        self.worker.stop()

    def finished(self):
        self.worker.deleteLater()
        self.worker = None
        self.panel.clear()
        self.stack.setCurrentWidget(self.lobby)
        self.stack.removeWidget(self.panel)
        self.panel.deleteLater()
        self.panel = None
        self.stopping = False
        self.lobby.update_mode()
        if self.lobby_notice:
            self.lobby.note.setText(self.lobby_notice)
        if self.exiting:
            self.close()
        else:
            self.identity = Identity()

    def focus_changed(self, state):
        if self.panel is not None and state != Qt.ApplicationState.ApplicationActive:
            self.panel.release_ptt()

    def eventFilter(self, watched, event):
        if self.panel is not None and not self.stopping and self.panel.ptt.isChecked():
            if event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease) and event.key() == Qt.Key.Key_F8:
                if not event.isAutoRepeat():
                    self.panel.set_pressed("keyboard", event.type() == QEvent.Type.KeyPress)
                return True
        return super().eventFilter(watched, event)

    def closeEvent(self, event):
        if self.worker is None:
            if self.identity is not None:
                self.identity.close()
                self.identity = None
            super().closeEvent(event)
        else:
            self.exiting = True
            self.leave_room()
            event.ignore()
