"""Полноразмерные настройки с навигацией внутри главного окна."""

from PyQt6.QtCore import QSignalBlocker, Qt
from PyQt6.QtWidgets import QButtonGroup, QComboBox, QFormLayout, QHBoxLayout, QSpinBox, QStackedWidget, QWidget

from audio import EFFECTS
from network import DIRECT, PROXY, STUN
from screen_ui import video_choices
from ui_dialogs import ProfileEditor, STRICT_HELP, STRICT_TITLE, STUN_HELP, field
from ui_widgets import Meter, button, column, divider, label, panel, scroll_area, switch_row


CATEGORIES = (
    ("profile", "Мой профиль", "edit"),
    ("voice", "Голос и видео", "mic"),
    ("network", "Сеть и инкогнито", "shield"),
    ("appearance", "Внешний вид и уведомления", "sliders"),
)


class SettingsPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.setObjectName("settingsPage")
        self.categories = QButtonGroup(self)
        self.stack = QStackedWidget()
        self.buttons = {}
        self.pages = {}
        rail = self.rail = QWidget()
        rail.setObjectName("navigation")
        self.set_scale(window.ui_scale)
        menu = column(rail, (17, 29, 14, 20), 8)
        menu.addWidget(label("Настройки", "groupTitle"))
        menu.addSpacing(13)
        for key, caption, glyph in CATEGORIES:
            control = button(caption, lambda _checked, page=key: self.show_section(page),
                             role="row", icon_name=glyph, toggle=True)
            self.buttons[key] = control
            self.categories.addButton(control)
            menu.addWidget(control)
        menu.addStretch()
        menu.addWidget(button("Назад · Esc", window.close_settings, icon_name="close"))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(rail)
        layout.addWidget(self.stack, 1)

    def set_scale(self, scale):
        self.rail.setFixedWidth(round(264 * scale / 100))

    def start(self, key, focus=None):
        for page in self.pages.values():
            self.stack.removeWidget(page)
            page.deleteLater()
        self.device_lists = {}
        self.pages.clear()
        self.show_section(key)
        if key == "profile" and focus is not None:
            self.editor.focus_field(focus)

    def show_section(self, key):
        if key not in self.pages:
            caption = next(name for name_key, name, _ in CATEGORIES if name_key == key)
            scroll, body = scroll_area()
            body.setContentsMargins(30, 26, 30, 30)
            body.setSpacing(17)
            contents = QWidget()
            contents.setMaximumWidth(735)
            content = column(contents, spacing=14)
            content.addWidget(label(caption, "channelTitle", True))
            body.addWidget(contents)
            body.addStretch()
            getattr(self, f"build_{key}")(content)
            self.pages[key] = scroll
            self.stack.addWidget(scroll)
        self.buttons[key].setChecked(True)
        self.stack.setCurrentWidget(self.pages[key])
        self.update_network_state()

    def build_profile(self, layout):
        window = self.window
        self.editor = ProfileEditor(window.profile, window.amnesia, window.local_gif, window.gif_path,
                                    window.save_profile)
        layout.addWidget(self.editor)

    def build_voice(self, layout):
        window = self.window
        _, audio = self.add_card(layout, "Аудиоустройства")
        form = QFormLayout()
        for capture, name in ((True, "Микрофон"), (False, "Наушники / выход")):
            combo = QComboBox()
            self.device_lists[capture] = combo
            combo.currentIndexChanged.connect(
                lambda _, cap=capture, widget=combo: window.change_audio_device(cap, widget.currentData())
            )
            form.addRow(name, combo)
        self.update_devices()
        audio.addLayout(form)
        self.noise = switch_row(audio, "Шумоподавление", window.noise, window.set_noise,
                                detail="Мягкий шумовой порог микрофона.")
        self.ptt = switch_row(audio, "Push-to-Talk · F8", window.ptt, window.set_ptt)
        _, test = self.add_card(layout, "Проверка микрофона")
        self.probe = switch_row(test, "Измерять уровень", window.probe.isChecked(), window.set_probe)
        self.level = label("−∞ дБ", "meterLevel")
        self.meter = Meter()
        test.addWidget(self.meter)
        test.addWidget(self.level)
        self.loopback = switch_row(test, "Прослушать себя", window.loopback.isChecked(), window.set_loopback,
                                   detail="Звук с выбранным эффектом. Используйте наушники.")
        effects = QComboBox()
        for key, name in EFFECTS:
            effects.addItem(name, key)
        self.effects = effects
        effects.setCurrentIndex(effects.findData(window.voice_effect))
        effects.currentIndexChanged.connect(lambda _: window.set_effect(effects.currentData()))
        test.addWidget(effects)
        _, video = self.add_card(layout, "Качество демонстрации экрана")
        quality, fps = video_choices(window.screen_height, window.screen_fps)
        form = QFormLayout()
        form.addRow("Разрешение", quality)
        form.addRow("Частота кадров", fps)
        quality.currentIndexChanged.connect(lambda _: setattr(window, "screen_height", quality.currentData()))
        fps.currentIndexChanged.connect(lambda _: setattr(window, "screen_fps", fps.currentData()))
        video.addLayout(form)

    def build_network(self, layout):
        window = self.window
        frame, card = self.add_card(layout, "Подключение")
        self.network_card = frame
        self.strict = switch_row(card, STRICT_TITLE, window.mode != STUN, self.change_network)
        self.mode_hint = label("", "muted", True)
        card.addWidget(self.mode_hint)
        self.route = QComboBox()
        self.route.addItem("Прямой IP / VPN", DIRECT)
        self.route.addItem("Tor / SOCKS5", PROXY)
        self.route.setCurrentIndex(int(window.mode == PROXY))
        self.route.currentIndexChanged.connect(lambda _: self.change_network(self.strict.isChecked()))
        card.addWidget(self.route)
        self.locked = label("Завершите комнату, чтобы изменить режим сети.", "muted", True)
        layout.addWidget(self.locked)
        _, ports = self.add_card(layout, "Адреса и порты приглашений")
        options = window.network_options
        form = QFormLayout()
        for attr, name, placeholder, limit in (
            ("direct_address", "Внешний UDP", "IP:порт при ручном пробросе", 255),
            ("onion", "Адрес onion / TCP", "адрес.onion:17840", 255),
            ("proxy", "Локальный SOCKS5", "127.0.0.1:9050", 90),
        ):
            entry = field(placeholder, limit=limit)
            entry.setText(getattr(options, attr))
            entry.textChanged.connect(lambda value, key=attr: setattr(options, key, value.strip()))
            form.addRow(name, entry)
        port = QSpinBox()
        port.setRange(1, 65535)
        port.setValue(options.port)
        port.valueChanged.connect(lambda value: setattr(options, "port", value))
        form.addRow("Локальный порт моста Tor (TCP)", port)
        ports.addLayout(form)
        ports.addWidget(label("Для WebRTC локальный UDP-порт выбирается при сборе ICE и показывается в приглашении прямого режима.", "muted", True))

    def build_appearance(self, layout):
        window = self.window
        _, visuals = self.add_card(layout, "Оформление")
        form = QFormLayout()
        theme = QComboBox()
        theme.addItem("Светлая (Codex Light)", "light")
        theme.addItem("Тёмная (Codex Dark)", "dark")
        theme.setCurrentIndex(theme.findData(window.theme))
        theme.currentIndexChanged.connect(lambda _: window.set_theme(theme.currentData()))
        form.addRow("Тема", theme)
        scale = QComboBox()
        for value in (90, 100, 110, 125):
            scale.addItem(f"{value}%", value)
        scale.setCurrentIndex(scale.findData(window.ui_scale))
        scale.currentIndexChanged.connect(lambda _: window.set_scale(scale.currentData()))
        form.addRow("Масштаб интерфейса", scale)
        visuals.addLayout(form)
        _, notices = self.add_card(layout, "Уведомления")
        switch_row(notices, "Звуки уведомлений", window.notifications.sound_enabled, window.notifications.set_sound)
        switch_row(notices, "Всплывающие уведомления", window.notifications.toast_enabled,
                   window.notifications.set_toasts, detail="Показывать сообщения и подключения внутри sekret.")

    def change_network(self, strict):
        self.window.choose_mode(self.route.currentData() if strict else STUN)
        self.update_network_state()

    def update_network_state(self):
        if "network" not in self.pages:
            return
        window = self.window
        available = window.can_choose_mode()
        strict = window.mode != STUN
        self.network_card.setEnabled(available)
        if self.strict.isChecked() != strict:
            with QSignalBlocker(self.strict):
                self.strict.setChecked(strict)
            self.strict.slide(strict)
        if strict:
            with QSignalBlocker(self.route):
                self.route.setCurrentIndex(self.route.findData(window.mode))
        self.route.setVisible(strict)
        self.mode_hint.setText(STRICT_HELP if strict else STUN_HELP)
        self.locked.setVisible(not available)

    def update_devices(self):
        for capture, combo in getattr(self, "device_lists", {}).items():
            with QSignalBlocker(combo):
                combo.clear()
                combo.addItem("Системное устройство", None)
                for index, name, inputs, outputs in self.window.devices:
                    if (inputs if capture else outputs) > 0:
                        combo.addItem(name, index)
                combo.setCurrentIndex(max(0, combo.findData(self.window.audio_devices[capture])))

    def update_meter(self, value):
        if "voice" in self.pages and self.stack.currentWidget() is self.pages["voice"]:
            self.meter.set_level(value)
            self.level.setText("−∞ дБ" if value < -65 else f"{value:.0f} дБ")

    @staticmethod
    def add_card(layout, title):
        frame, card = panel(spacing=10)
        card.addWidget(label(title, "subheading"))
        card.addWidget(divider())
        layout.addWidget(frame)
        return frame, card
