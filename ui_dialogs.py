"""Редактор профиля и модальные окна обмена приглашением."""

import json
from dataclasses import dataclass, replace
from pathlib import Path

from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, QSaveFile, QSize, QStandardPaths, QTimer, Qt
from PyQt6.QtGui import QColor, QImageReader
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QColorDialog, QComboBox, QDialog, QFileDialog, QFormLayout,
    QGridLayout, QHBoxLayout, QLineEdit, QSizePolicy, QSpinBox, QWidget,
)
from cryptography.exceptions import InvalidTag

from branding import APP_NAME
from crypto import MAX_TOKEN, json_bytes, unb64
from models import DECORATIONS, MAX_AVATAR, MAX_PROFILE, PROFILE_EFFECTS, Profile, STATUS, title
from network import DIRECT, PROXY, STUN, read_invitation
from ui_chrome import AnimatedDialog
from ui_widgets import Avatar, ProfileHeader, button, column, label, switch_row


NETWORK_NAMES = {"stun": "STUN", "direct": "Прямой IP", "proxy": "Tor"}
STRICT_TITLE = "Строгий режим инкогнито (без STUN)"
STRICT_HELP = "Локальная сеть, Radmin VPN / ZeroTier, доступный UDP-порт или Tor через SOCKS5."
STUN_HELP = "Рекомендуется для интернета. Публичный STUN определяет внешний адрес. Требуется совместимый NAT."
MAX_GIF = 8 * 1024 * 1024
LOCAL_PATH_LIMIT = 2048
LOCAL_PROFILE_LIMIT = MAX_PROFILE + LOCAL_PATH_LIMIT * 12 + 64


@dataclass
class NetworkOptions:
    direct_address: str = ""
    onion: str = ""
    proxy: str = "127.0.0.1:9050"
    port: int = 17840


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
        self.gif_path = ""
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
                    data = stream.read(LOCAL_PROFILE_LIMIT + 1)
                if len(data) <= LOCAL_PROFILE_LIMIT:
                    document = json.loads(data)
                    profile = Profile.read(document)
                    source = document.get("gif_path", "")
                    if isinstance(source, str) and len(source) <= LOCAL_PATH_LIMIT and Path(source).is_absolute():
                        self.gif_path = source
                    return profile, False
            except (OSError, ValueError, TypeError, KeyError, RecursionError):
                continue
        return fallback, True

    def save(self, profile, ephemeral, gif_path=""):
        if self.path is None:
            return
        if ephemeral:
            self.path.unlink(missing_ok=True)
            if self.previous_path is not None:
                self.previous_path.unlink(missing_ok=True)
            self.gif_path = ""
            return
        if len(gif_path) > LOCAL_PATH_LIMIT:
            raise ValueError("Путь к GIF-баннеру слишком длинный.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file = QSaveFile(str(self.path))
        if not file.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError(file.errorString())
        document = profile.document()
        if gif_path:
            document["gif_path"] = gif_path
        data = json_bytes(document)
        if file.write(data) != len(data) or not file.commit():
            raise OSError(file.errorString())
        self.gif_path = gif_path


def load_gif(path):
    try:
        with Path(path).open("rb") as stream:
            data = stream.read(MAX_GIF + 1)
    except OSError as error:
        raise ValueError("Не удалось открыть GIF-файл.") from error
    if not data.startswith((b"GIF87a", b"GIF89a")) or len(data) > MAX_GIF:
        raise ValueError("Выберите GIF-файл размером до 8 МБ.")
    encoded = QByteArray(data)
    buffer = QBuffer(encoded)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer, QByteArray(b"gif"))
    size = reader.size()
    if not size.isValid() or size.width() * size.height() > 2_073_600 or reader.read().isNull():
        raise ValueError("GIF не открывается или превышает 2 мегапикселя.")
    return data


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


class ProfileEditor(QWidget):
    def __init__(self, profile, ephemeral, gif, gif_path, save):
        super().__init__()
        self.profile = profile
        self.gif, self.gif_path = gif, gif_path
        self.save = save
        self.layout = column(self, (0, 0, 0, 0), 14)
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
        gif_actions = QHBoxLayout()
        self.gif_button = button("Загрузить GIF-баннер", self.pick_gif, icon_name="banner")
        gif_actions.addWidget(self.gif_button)
        self.remove_gif = button("Убрать GIF", self.reset_gif, role="flat")
        self.remove_gif.setEnabled(bool(gif))
        gif_actions.addWidget(self.remove_gif)
        form.addRow("Локальный GIF", gif_actions)
        self.accent = button("Выбрать цвет", lambda: self.pick_color("accent"), icon_name="color")
        form.addRow("Цвет профиля", self.accent)
        self.effect = QComboBox()
        for key, caption in PROFILE_EFFECTS.items():
            self.effect.addItem(caption, key)
        self.effect.setCurrentIndex(self.effect.findData(profile.effect))
        self.effect.currentIndexChanged.connect(self.update_preview)
        form.addRow("Эффект баннера", self.effect)
        self.layout.addLayout(form)
        self.layout.addWidget(label("Рамка аватара", "subheading"))
        self.layout.addWidget(label("GIF, рамка и эффект баннера видны только вам.", "muted", True))
        gallery = QGridLayout()
        self.gallery = QButtonGroup(self)
        for i, (key, caption) in enumerate(DECORATIONS.items()):
            control = button(callback=lambda _checked, selected=key: self.pick_decoration(selected), toggle=True)
            control.setAccessibleName(caption)
            control.setChecked(key == profile.decoration)
            self.gallery.addButton(control)
            control.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            if key == "none":
                control.setText(caption)
                gallery.addWidget(control, 2, 0, 1, 3)
                continue
            control.setFixedHeight(84)
            cell = column(control, (4, 7, 4, 7), 4)
            avatar = Avatar(37, replace(profile, decoration=key))
            avatar.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            cell.addWidget(avatar, alignment=Qt.AlignmentFlag.AlignHCenter)
            name = label(caption, wrap=True)
            name.setMinimumHeight(32)
            name.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            cell.addWidget(name)
            gallery.addWidget(control, (i - 1) // 3, (i - 1) % 3)
        for index in range(3):
            gallery.setColumnStretch(index, 1)
        self.layout.addLayout(gallery)
        self.ephemeral = switch_row(self.layout, "Режим амнезии", ephemeral, lambda _: None,
                                    detail="Не сохранять профиль и путь к GIF на диск.")
        self.notice = label("", "notice", True)
        self.layout.addWidget(self.notice)
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(button("Сохранить", self.submit, role="primary"))
        self.layout.addLayout(actions)
        self.name.textChanged.connect(self.update_preview)
        self.status.currentIndexChanged.connect(self.update_preview)
        self.update_colors()

    def focus_field(self, focus):
        {"banner": self.gif_button, "color": self.accent}.get(focus, self.name).setFocus()

    def draw_preview(self):
        if self.preview is not None:
            self.layout.removeWidget(self.preview)
            self.preview.deleteLater()
        self.preview = ProfileHeader(self.profile, gif=self.gif)
        self.layout.insertWidget(0, self.preview)

    def update_preview(self):
        name = self.name.text().strip() or self.profile.name
        self.profile = replace(
            self.profile, name=name, status=self.status.currentData(),
            effect=self.effect.currentData(),
        )
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

    def pick_decoration(self, name):
        self.profile = replace(self.profile, decoration=name)
        self.draw_preview()

    def reset_gif(self):
        self.gif, self.gif_path = b"", ""
        self.remove_gif.setDisabled(True)
        self.draw_preview()

    def pick_gif(self):
        path, _ = QFileDialog.getOpenFileName(self, "Загрузить GIF-баннер", "", "GIF (*.gif)")
        if path:
            try:
                self.gif = load_gif(path)
                self.gif_path = path
                self.notice.clear()
                self.remove_gif.setEnabled(True)
                self.draw_preview()
            except ValueError as error:
                self.notice.setText(str(error))

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
            self.save(profile, self.ephemeral.isChecked(), self.gif, self.gif_path)
        except (ValueError, OSError) as error:
            self.notice.setText(str(error))
            return
        self.notice.setText("Профиль сохранён.")


class ExchangeDialog(AnimatedDialog):
    def __init__(self, parent, host, mode, options, dispatch, copy, *, locked=False):
        super().__init__(parent)
        self.host, self.mode, self.dispatch, self.copy = host, mode, dispatch, copy
        self.locked = locked
        self.pending = False
        self.waiting = False
        self.is_busy = False
        self.setWindowTitle("Приглашение в комнату" if host else "Войти по токену")
        self.setMinimumWidth(565)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        layout = column(self, (22, 20, 22, 20), 10)
        layout.addWidget(label(self.windowTitle(), "subheading"))
        self.strict = switch_row(layout, STRICT_TITLE, mode != STUN, self.change_mode)
        self.mode_hint = label("", "muted", True)
        layout.addWidget(self.mode_hint)
        form = QFormLayout()
        self.form = form
        self.strict_route = QComboBox()
        self.strict_route.addItem("Прямой IP / VPN", DIRECT)
        self.strict_route.addItem("Tor / SOCKS5", PROXY)
        self.strict_route.setCurrentIndex(int(mode == PROXY))
        self.strict_route.currentIndexChanged.connect(lambda _: self.change_mode(self.strict.isChecked()))
        form.addRow("Строгое подключение", self.strict_route)
        self.endpoint = field("Необязательно · внешний IP:порт UDP", limit=255)
        self.endpoint.setText(options.direct_address)
        self.onion = field("Например, адрес.onion:17840", limit=255)
        self.onion.setText(options.onion)
        self.local_port = QSpinBox()
        self.local_port.setRange(1, 65535)
        self.local_port.setValue(options.port)
        self.proxy = field("127.0.0.1:9050", limit=90)
        self.proxy.setText(options.proxy)
        form.addRow("Внешний адрес", self.endpoint)
        if host:
            form.addRow("Адрес onion / TCP", self.onion)
            form.addRow("Локальный порт", self.local_port)
        else:
            form.addRow("Локальный SOCKS5", self.proxy)
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
        self.answer_row = self.answer if host else QWidget()
        if not host:
            self.answer_row.setLayout(self.copy_row(self.answer))
        form.addRow("Ответ", self.answer_row)
        layout.addLayout(form)
        layout.addWidget(label(
            "Передавайте токен и ключ по разным доверенным каналам."
            if host else "Передайте зашифрованный ответ хосту, если его запросит приложение.",
            "muted", True,
        ))
        self.ice_notice = label("", "notice", True)
        self.ice_notice.hide()
        layout.addWidget(self.ice_notice)
        self.notice = label("", "notice", True)
        layout.addWidget(self.notice)
        self.match_mode = button("Выбрать режим хоста", self.use_host_mode)
        self.match_mode.hide()
        layout.addWidget(self.match_mode, alignment=Qt.AlignmentFlag.AlignLeft)
        actions = QHBoxLayout()
        self.primary = button("Создать приглашение" if host else "Войти",
                              self.submit, role="primary", icon_name="plus" if host else "key")
        actions.addWidget(self.primary)
        self.accept_answer = None
        self.cancel = None
        if host:
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
        self.host_mode = None
        self.check_timer = QTimer(self, singleShot=True, interval=260)
        self.check_timer.timeout.connect(self.check_invite_mode)
        if not host:
            for field_value in (self.token, self.key):
                field_value.textChanged.connect(lambda _: self.check_timer.start())
        self.change_mode(mode != STUN)
        self.update_state()

    def change_mode(self, strict):
        self.mode = self.strict_route.currentData() if strict else STUN
        self.mode_hint.setText(STRICT_HELP if strict else STUN_HELP)
        for widget, show in (
            (self.strict_route, strict), (self.endpoint, self.mode == DIRECT),
            (self.onion, self.mode == PROXY and self.host), (self.local_port, self.mode == PROXY and self.host),
            (self.proxy, self.mode == PROXY and not self.host), (self.answer_row, self.mode != PROXY),
        ):
            if self.form.labelForField(widget) is not None:
                self.form.setRowVisible(widget, show)
        if self.accept_answer is not None:
            self.accept_answer.setVisible(self.mode != PROXY)
        if not self.host:
            self.check_invite_mode()
        QTimer.singleShot(0, self.adjustSize)

    def check_invite_mode(self):
        self.host_mode = None
        self.match_mode.hide()
        token, key = self.token.text().strip(), self.key.text().strip()
        if self.is_busy or self.waiting:
            return None
        if not token or not key:
            self.notice.clear()
            return None
        try:
            offer = read_invitation(token, unb64(key, 32))
        except InvalidTag:
            self.notice.setText("Приглашение и ключ не совпадают. Скопируйте оба поля из одного приглашения.")
            return None
        except (ValueError, TypeError, KeyError) as error:
            self.notice.setText(str(error) if isinstance(error, ValueError) else "Некорректное приглашение.")
            return None
        self.host_mode = offer["mode"]
        if self.host_mode != self.mode:
            self.notice.setText(f"Хост выбрал {NETWORK_NAMES[self.host_mode]}. Для входа выберите тот же режим.")
            self.match_mode.show()
        else:
            self.notice.setText(f"Режим хоста · {NETWORK_NAMES[self.host_mode]}. Можно подключаться.")
        return self.host_mode

    def use_host_mode(self):
        mode = self.host_mode
        if mode is not None:
            if mode != STUN:
                self.strict_route.setCurrentIndex(self.strict_route.findData(mode))
            self.strict.setChecked(mode != STUN)
            self.change_mode(mode != STUN)

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
        self.show_ice_notice("")
        if self.host:
            if self.mode == PROXY:
                address = self.onion.text().strip()
            if self.mode == PROXY and not address:
                self.notice.setText("Укажите адрес onion-сервиса или TCP-моста.")
                return
            self.set_busy(True)
            self.dispatch("invite", self.mode, address if self.mode != STUN else "", self.local_port.value())
        else:
            token, key = self.token.text().strip(), self.key.text().strip()
            if not token or not key:
                self.notice.setText("Введите токен и ключ приглашения.")
                return
            if self.check_invite_mode() != self.mode:
                return
            self.set_busy(True)
            self.dispatch("join", self.mode, token, key, self.proxy.text().strip(), address)

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

    def show_ice_notice(self, message):
        self.ice_notice.setText(message)
        self.ice_notice.setVisible(bool(message))

    def update_state(self):
        self.primary.setEnabled(not self.is_busy and not self.pending and not self.waiting)
        if self.accept_answer is not None:
            self.accept_answer.setEnabled(not self.is_busy and self.pending)
        if self.cancel is not None:
            self.cancel.setEnabled(self.is_busy or self.pending)
        editable = not (self.locked or self.is_busy or self.pending or self.waiting)
        self.strict.setEnabled(editable)
        self.strict_route.setEnabled(editable)
        for control in (self.endpoint, self.onion, self.proxy, self.local_port):
            control.setEnabled(not (self.is_busy or self.pending or self.waiting))


class SasDialog(AnimatedDialog):
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
