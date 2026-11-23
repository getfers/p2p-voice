"""Редактор профиля и модальные окна обмена приглашением."""

import json
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, QSaveFile, QSize, QStandardPaths, Qt
from PyQt6.QtGui import QColor, QImageReader
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QFileDialog, QFormLayout,
    QHBoxLayout, QLineEdit, QSpinBox, QWidget,
)

from branding import APP_NAME
from crypto import MAX_TOKEN, json_bytes
from models import MAX_AVATAR, MAX_PROFILE, Profile, STATUS, title
from network import DIRECT, PROXY
from ui_widgets import ProfileHeader, button, column, label


NETWORK_NAMES = {"stun": "STUN", "direct": "Прямой IP", "proxy": "Tor"}


def field(placeholder="", *, readonly=False, limit=MAX_TOKEN):
    widget = QLineEdit()
    widget.setPlaceholderText(placeholder)
    widget.setReadOnly(readonly)
    widget.setMaxLength(limit)
    return widget


class ProfileStore:
    def __init__(self, ephemeral=False, path=None):
        folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation))
        self.path = None if ephemeral else (path or folder / "profile.json")
        # Qt выводит имя каталога из названия приложения. Сохранённый профиль доступен после ребрендинга.
        self.previous_path = (
            folder.with_name("Эфир") / "profile.json"
            if not ephemeral and path is None and folder.name == APP_NAME else None
        )

    def load(self, fallback):
        for path in (self.path, self.previous_path):
            if path is None:
                continue
            try:
                with path.open("rb") as stream:
                    data = stream.read(MAX_PROFILE + 1)
                if len(data) <= MAX_PROFILE:
                    return Profile.read(json.loads(data)), False
            except (OSError, ValueError, TypeError, KeyError, RecursionError):
                continue
        return fallback, True

    def save(self, profile, ephemeral):
        if self.path is None:
            return
        if ephemeral:
            self.path.unlink(missing_ok=True)
            if self.previous_path is not None:
                self.previous_path.unlink(missing_ok=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file = QSaveFile(str(self.path))
        if not file.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError(file.errorString())
        data = json_bytes(profile.document())
        if file.write(data) != len(data) or not file.commit():
            raise OSError(file.errorString())


def load_avatar(path):
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    if not size.isValid() or size.width() * size.height() > 49_000_000:
        raise ValueError("Выберите изображение размером до 49 мегапикселей.")
    reader.setScaledSize(size.scaled(QSize(512, 512), Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        raise ValueError("Изображение не удалось открыть.")
    side = min(image.width(), image.height())
    image = image.copy((image.width() - side) // 2, (image.height() - side) // 2, side, side)
    image = image.scaled(128, 128, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    for quality in (88, 78, 64, 49, 33):
        encoded = QByteArray()
        buffer = QBuffer(encoded)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if image.save(buffer, "JPEG", quality) and len(encoded) <= MAX_AVATAR:
            return bytes(encoded)
    raise ValueError("Изображение не удалось сжать до допустимого размера.")


class ProfileDialog(QDialog):
    def __init__(self, parent, profile, ephemeral, save, focus=None):
        super().__init__(parent)
        self.setWindowTitle("Мой профиль")
        self.setMinimumWidth(440)
        self.profile = profile
        self.save = save
        self.layout = column(self, (20, 18, 20, 18), 12)
        self.preview = None
        self.draw_preview()
        form = QFormLayout()
        self.name = field(limit=30)
        self.name.setText(profile.name)
        form.addRow("Никнейм", self.name)
        self.status = QComboBox()
        for key, caption in STATUS.items():
            self.status.addItem(caption, key)
        self.status.setCurrentIndex(self.status.findData(profile.status))
        form.addRow("Статус", self.status)
        avatar = QHBoxLayout()
        avatar.addWidget(button("Загрузить…", self.pick_avatar, icon_name="banner"))
        avatar.addWidget(button("Сбросить", self.reset_avatar, role="flat"))
        form.addRow("Аватар · 128×128", avatar)
        colors = QHBoxLayout()
        self.first = button("Начало градиента", lambda: self.pick_color("banner_first"))
        self.last = button("Конец градиента", lambda: self.pick_color("banner_last"))
        colors.addWidget(self.first)
        colors.addWidget(self.last)
        form.addRow("Баннер", colors)
        self.accent = button("Выбрать цвет", lambda: self.pick_color("accent"), icon_name="color")
        form.addRow("Цвет профиля", self.accent)
        self.layout.addLayout(form)
        self.ephemeral = QCheckBox("Режим амнезии — не сохранять профиль на диск")
        self.ephemeral.setChecked(ephemeral)
        self.layout.addWidget(self.ephemeral)
        self.notice = label("", "notice", True)
        self.layout.addWidget(self.notice)
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(button("Отмена", self.reject))
        actions.addWidget(button("Сохранить", self.submit, role="primary"))
        self.layout.addLayout(actions)
        self.name.textChanged.connect(self.update_preview)
        self.status.currentIndexChanged.connect(self.update_preview)
        self.update_colors()
        {"banner": self.first, "color": self.accent}.get(focus, self.name).setFocus()

    def draw_preview(self):
        if self.preview is not None:
            self.layout.removeWidget(self.preview)
            self.preview.deleteLater()
        self.preview = ProfileHeader(self.profile)
        self.layout.insertWidget(0, self.preview)

    def update_preview(self):
        name = self.name.text().strip() or self.profile.name
        self.profile = replace(self.profile, name=name, status=self.status.currentData())
        self.draw_preview()

    def update_colors(self):
        for widget, color in ((self.first, self.profile.banner_first), (self.last, self.profile.banner_last),
                              (self.accent, self.profile.accent)):
            widget.setStyleSheet(f"border-bottom: 3px solid {color};")

    def pick_color(self, attribute):
        color = QColorDialog.getColor(QColor(getattr(self.profile, attribute)), self, "Выберите цвет")
        if color.isValid():
            self.profile = replace(self.profile, **{attribute: color.name()})
            self.update_colors()
            self.draw_preview()

    def reset_avatar(self):
        self.profile = replace(self.profile, avatar=b"")
        self.draw_preview()

    def pick_avatar(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Выбрать аватар", "", "Изображения (*.jpg *.jpeg *.png *.webp *.bmp)"
        )
        if path:
            try:
                self.profile = replace(self.profile, avatar=load_avatar(path))
                self.notice.clear()
                self.draw_preview()
            except ValueError as error:
                self.notice.setText(str(error))

    def submit(self):
        try:
            profile = replace(self.profile, name=title(self.name.text(), 30, "Имя"))
            self.save(profile, self.ephemeral.isChecked())
        except (ValueError, OSError) as error:
            self.notice.setText(str(error))
            return
        self.accept()


class ExchangeDialog(QDialog):
    def __init__(self, parent, host, mode, dispatch, copy):
        super().__init__(parent)
        self.host, self.mode, self.dispatch, self.copy = host, mode, dispatch, copy
        self.pending = False
        self.waiting = False
        self.is_busy = False
        self.setWindowTitle("Приглашение в комнату" if host else "Войти по токену")
        self.setMinimumWidth(525)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        layout = column(self, (22, 20, 22, 20), 10)
        layout.addWidget(label(self.windowTitle(), "subheading"))
        layout.addWidget(label(f"Режим сети · {NETWORK_NAMES[mode]}", "muted"))
        form = QFormLayout()
        self.endpoint = field("Необязательно · внешний IP:порт UDP", limit=255)
        self.local_port = QSpinBox()
        self.local_port.setRange(1, 65535)
        self.local_port.setValue(17840)
        self.proxy = field("127.0.0.1:9050", limit=90)
        self.proxy.setText("127.0.0.1:9050")
        if mode == PROXY and host:
            self.endpoint.setPlaceholderText("Например, адрес.onion:17840")
            form.addRow("Адрес onion / TCP", self.endpoint)
            form.addRow("Локальный порт", self.local_port)
        elif mode == PROXY:
            form.addRow("Локальный SOCKS5", self.proxy)
        elif mode == DIRECT:
            form.addRow("Внешний адрес", self.endpoint)
        self.token = field("Одноразовый токен", readonly=host)
        self.key = field("Ключ из отдельного канала", readonly=host, limit=64)
        self.answer = field("Ответ гостя" if host else "Передайте ответ хосту", readonly=not host)
        if host:
            form.addRow("Приглашение", self.copy_row(self.token))
            form.addRow("Ключ", self.copy_row(self.key))
        else:
            self.key.setEchoMode(QLineEdit.EchoMode.Password)
            form.addRow("Приглашение", self.token)
            form.addRow("Ключ", self.key)
        if mode != PROXY:
            form.addRow("Ответ", self.answer if host else self.copy_row(self.answer))
        layout.addLayout(form)
        layout.addWidget(label(
            "Передавайте токен и ключ по разным доверенным каналам."
            if host else "Передайте зашифрованный ответ хосту, если его запросит приложение.",
            "muted", True,
        ))
        self.notice = label("", "notice", True)
        layout.addWidget(self.notice)
        actions = QHBoxLayout()
        self.primary = button("Создать приглашение" if host else "Войти",
                              self.submit, role="primary", icon_name="plus" if host else "key")
        actions.addWidget(self.primary)
        self.accept_answer = None
        self.cancel = None
        if host:
            if mode != PROXY:
                self.accept_answer = button("Принять ответ", self.submit_answer)
                actions.addWidget(self.accept_answer)
                self.answer.returnPressed.connect(self.submit_answer)
            self.cancel = button("Отменить токен", lambda: dispatch("cancel"))
            actions.addWidget(self.cancel)
        actions.addStretch()
        actions.addWidget(button("Закрыть", self.reject))
        layout.addLayout(actions)
        self.token.returnPressed.connect(self.submit)
        self.key.returnPressed.connect(self.submit)
        self.update_state()

    def copy_row(self, source):
        row = QHBoxLayout()
        row.addWidget(source, 1)
        control = button(callback=lambda: self.copy(source.text()), icon_name="copy", width=35)
        control.setToolTip("Копировать")
        control.setAccessibleName(f"Копировать: {source.placeholderText()}")
        control.setEnabled(bool(source.text()))
        source.textChanged.connect(lambda text: control.setEnabled(bool(text)))
        row.addWidget(control)
        return row

    def submit(self):
        if not self.primary.isEnabled():
            return
        address = self.endpoint.text().strip()
        self.notice.clear()
        if self.host:
            if self.mode == PROXY and not address:
                self.notice.setText("Укажите адрес onion-сервиса или TCP-моста.")
                return
            self.set_busy(True)
            self.dispatch("invite", address, self.local_port.value())
        else:
            token, key = self.token.text().strip(), self.key.text().strip()
            if not token or not key:
                self.notice.setText("Введите токен и ключ приглашения.")
                return
            self.set_busy(True)
            self.dispatch("join", token, key, self.proxy.text().strip(), address)

    def submit_answer(self):
        if not self.accept_answer.isEnabled():
            return
        answer = self.answer.text().strip()
        if not answer:
            self.notice.setText("Вставьте ответ гостя.")
            return
        self.notice.clear()
        self.set_busy(True)
        self.dispatch("answer", answer)

    def set_invite(self, token, key):
        self.pending = bool(token)
        self.token.setText(token)
        self.key.setText(key)
        if not self.pending:
            self.answer.clear()
        self.update_state()

    def set_reply(self, answer):
        self.waiting = bool(answer)
        self.answer.setText(answer)
        if answer:
            self.token.clear()
            self.key.clear()
        self.update_state()

    def set_busy(self, busy):
        self.is_busy = busy
        self.update_state()

    def update_state(self):
        self.primary.setEnabled(not self.is_busy and not self.pending and not self.waiting)
        if self.accept_answer is not None:
            self.accept_answer.setEnabled(not self.is_busy and self.pending)
        if self.cancel is not None:
            self.cancel.setEnabled(self.is_busy or self.pending)


class SasDialog(QDialog):
    def __init__(self, parent, name, code, confirm):
        super().__init__(parent)
        self.setWindowTitle("Проверка собеседника")
        self.setMinimumWidth(390)
        layout = column(self, (22, 20, 22, 20), 10)
        layout.addWidget(label(name, "subheading"))
        layout.addWidget(label(f"{code[:3]} {code[3:]}", "sas"))
        layout.addWidget(label(
            "Сверьте код по доверенному каналу. При совпадении оба участника нажимают кнопку подтверждения.",
            "muted", True,
        ))
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(button("Закрыть", self.reject))
        row.addWidget(button("Коды совпали", self.accept, role="primary"))
        layout.addLayout(row)
        self.accepted.connect(confirm)
