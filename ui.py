"""Главный экран, навигация по группе и управление звонком."""

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime

from PyQt6.QtCore import QEvent, QSize, QSignalBlocker, QTimer, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QInputDialog, QMainWindow, QMenu, QSizePolicy, QWidget,
)

from audio import EFFECTS
from models import Group, Profile
from network import DIRECT, PROXY, STUN
from ui_dialogs import ExchangeDialog, NETWORK_NAMES, ProfileDialog, ProfileStore, SasDialog, field
from ui_widgets import (
    ACCENT, GREEN, STYLE, Avatar, Meter, ProfileHeader, button, column, divider, icon,
    label, panel, refresh, scroll_area,
)


MAX_MESSAGES = 200
EFFECT_ICONS = ("wave", "radio", "robot", "bolt", "spark", "mask")
MODE_HINT = {
    STUN: "STUN · поиск внешнего адреса",
    DIRECT: "Инкогнито · без STUN · прямой IP",
    PROXY: "Инкогнито · локальный SOCKS5",
}


def horizontal(margins=(0, 0, 0, 0), spacing=7):
    layout = QHBoxLayout()
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return layout


def clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().hide()
            item.widget().deleteLater()
        elif item.layout() is not None:
            child = item.layout()
            clear(child)
            child.deleteLater()


def picture(symbol, fill=ACCENT, side=16):
    widget = label()
    widget.setFixedSize(side, side)
    widget.setPixmap(icon(symbol, fill).pixmap(side, side))
    return widget


@dataclass(frozen=True)
class Member:
    name: str
    own: bool
    verified: bool
    voice: str


@dataclass(frozen=True)
class Message:
    public: str
    name: str
    stamp: str
    text: str


class MemberButton(QWidget):
    def __init__(self, profile, own, selected, speaking, callback, *, small=False):
        super().__init__()
        layout = column(self, spacing=0)
        self.control = button(callback=callback, role="presence" if small else "member")
        self.control.setAccessibleName(f"Профиль: {profile.name}")
        self.control.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.control)
        if small:
            content = QHBoxLayout(self.control)
            content.setContentsMargins(5, 1, 4, 1)
            content.setSpacing(7)
            self.avatar = Avatar(24, profile, ring=speaking)
            content.addWidget(self.avatar)
            name = label(profile.name)
            name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            content.addWidget(name, 1)
        else:
            content = column(self.control, (4, 8, 4, 7), 2)
            self.avatar = Avatar(52, profile, ring=speaking)
            content.addWidget(self.avatar, alignment=Qt.AlignmentFlag.AlignHCenter)
            name = label(profile.name)
            name.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            content.addWidget(name)
        self.caption = None
        self.own = own
        if not small:
            self.caption = label("", "muted")
            self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            content.addWidget(self.caption)
        for item in (self.avatar, name, self.caption):
            if item is not None:
                item.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        refresh(self.control, "selected", selected)
        self.activity(speaking)

    def activity(self, speaking):
        refresh(self.control, "speaking", speaking)
        self.avatar.ring = speaking
        self.avatar.update()
        if self.caption is not None:
            self.caption.setText("Говорит" if speaking else "Вы" if self.own else "В канале")
            self.caption.setStyleSheet(f"color: {GREEN};" if speaking else "")


class Window(QMainWindow):
    def __init__(self, name, worker_type, *, ephemeral=False):
        super().__init__()
        self.profile_store = ProfileStore(ephemeral)
        self.profile, self.amnesia = self.profile_store.load(Profile(name))
        group = Group.create("Моя группа")
        self.groups = {group.key: group}
        self.selected_group = group.key
        self.voice_choice = {group.key: group.voice[0].key}
        self.text_choice = {group.key: group.text[0].key}
        self.overview = True
        self.session_group = None
        self.hosting = False
        self.connecting = False
        self.leaving = False
        self.mode = DIRECT
        self.members = {}
        self.profiles = {}
        self.sas_codes = {}
        self.speaking = set()
        self.inspect_peer = None
        self.inspector_open = True
        self.presence_rows = defaultdict(list)
        self.call_rows = defaultdict(list)
        self.messages = defaultdict(lambda: deque(maxlen=MAX_MESSAGES))
        self.rendered_channel = None
        self.devices = []
        self.audio_devices = {True: None, False: None}
        self.ptt = self.keyboard = self.mouse = False
        self.noise = True
        self.closing = False
        self.worker_done = False
        self.signal_dialog = None
        self.invitation = ("", "")
        self.reply = ""
        self.signal_busy = False
        self.clipboard_values = set()
        self.setWindowTitle("Эфир")
        self.resize(1280, 800)
        self.setMinimumSize(1070, 640)
        self.setFont(QFont("Segoe UI", 10))
        self.setStyleSheet(STYLE)
        self.build()
        self.worker = worker_type(self)
        self.worker.session_event.connect(self.dispatch)
        self.worker.finished.connect(self.worker_finished)
        QApplication.instance().installEventFilter(self)
        QApplication.instance().applicationStateChanged.connect(self.focus_changed)
        self.update_view()
        self.render_self()
        self.render_inspector()
        self.worker.start()

    def build(self):
        root = QWidget()
        frame = column(root, spacing=0)
        titlebar = QWidget()
        titlebar.setObjectName("titlebar")
        titlebar.setFixedHeight(30)
        title = QHBoxLayout(titlebar)
        title.setContentsMargins(12, 0, 12, 0)
        title.addWidget(picture("wave", "#a69cff", 16))
        title.addWidget(label("Эфир"))
        title.addStretch()
        frame.addWidget(titlebar)
        body = horizontal(spacing=0)
        self.rail = QWidget()
        self.rail.setObjectName("groupRail")
        self.rail.setFixedWidth(72)
        self.rail_layout = column(self.rail, (0, 15, 0, 10), 10)
        body.addWidget(self.rail)
        self.navigation = QWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.setFixedWidth(237)
        self.build_navigation()
        body.addWidget(self.navigation)
        self.workspace = QWidget()
        self.workspace.setObjectName("workspace")
        self.build_workspace()
        body.addWidget(self.workspace, 1)
        self.inspector = QWidget()
        self.inspector.setObjectName("inspector")
        self.inspector.setFixedWidth(325)
        self.build_inspector()
        body.addWidget(self.inspector)
        frame.addLayout(body, 1)
        self.setCentralWidget(root)

    def build_navigation(self):
        layout = column(self.navigation, (14, 15, 14, 14), 10)
        self.group_title = label("", "groupTitle")
        self.group_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.group_title)
        self.group_members = label("", "muted")
        layout.addWidget(self.group_members)
        layout.addWidget(divider())
        nav, self.channel_list = scroll_area()
        self.channel_list.setSpacing(4)
        layout.addWidget(nav, 1)
        layout.addWidget(label("МОЙ ПРОФИЛЬ", "section"))
        self.self_card, self.self_layout = panel(margins=(9, 8, 9, 9), spacing=7)
        layout.addWidget(self.self_card)

    def build_workspace(self):
        layout = column(self.workspace, (16, 18, 16, 13), 12)
        header = horizontal(spacing=7)
        title_box = QWidget()
        headings = column(title_box, spacing=1)
        self.breadcrumb = label("", "muted")
        self.breadcrumb.setMinimumHeight(18)
        self.breadcrumb.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.channel_title = label("", "channelTitle")
        self.channel_title.setMinimumHeight(45)
        self.channel_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        headings.addWidget(self.breadcrumb)
        headings.addWidget(self.channel_title)
        header.addWidget(title_box, 1)
        self.create = button("Создать комнату", self.host_room, role="primary", icon_name="plus")
        self.join_control = button("Войти по токену", self.open_join, icon_name="key")
        for control in (self.create, self.join_control):
            control.setFixedHeight(37)
            header.addWidget(control, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)
        call, call_layout = panel(margins=(15, 12, 15, 13), spacing=11)
        voice_heading = horizontal()
        voice_heading.addWidget(picture("voice", GREEN))
        voice_heading.addWidget(label("В голосовом канале", "subheading"), 1)
        self.badge = label("E2EE", "badge")
        voice_heading.addWidget(self.badge)
        call_layout.addLayout(voice_heading)
        self.stage = QWidget()
        self.stage.setMinimumHeight(118)
        self.stage_grid = QGridLayout(self.stage)
        self.stage_grid.setContentsMargins(0, 0, 0, 0)
        self.stage_grid.setSpacing(8)
        call_layout.addWidget(self.stage)
        controls = horizontal(spacing=5)
        self.mic = button("Микрофон", self.set_mic, icon_name="mic", toggle=True)
        self.sound = button("Звук", self.set_sound, icon_name="headphones", toggle=True)
        self.ptt_control = button("Говорить · F8", icon_name="wave")
        self.ptt_control.pressed.connect(lambda: self.set_pressed("mouse", True))
        self.ptt_control.released.connect(lambda: self.set_pressed("mouse", False))
        self.invite = button("Пригласить", self.open_invite)
        self.leave = button("Выйти", self.leave_room, icon_name="close")
        for control in (self.mic, self.sound, self.ptt_control, self.invite, self.leave):
            control.setFixedHeight(35)
            control.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            controls.addWidget(control)
        call_layout.addLayout(controls)
        layout.addWidget(call)
        chat_frame, chat = panel("inset", margins=(14, 11, 14, 12), spacing=8)
        chat_header = horizontal()
        chat_header.addWidget(picture("hash", "#a69cff"))
        self.chat_title = label("", "subheading")
        chat_header.addWidget(self.chat_title, 1)
        chat_header.addWidget(label("Текстовый канал", "muted"))
        chat.addLayout(chat_header)
        chat.addWidget(divider())
        self.chat_scroll, self.chat_rows = scroll_area()
        self.chat_rows.setSpacing(9)
        self.chat_rows.setAlignment(Qt.AlignmentFlag.AlignTop)
        chat.addWidget(self.chat_scroll, 1)
        composer = horizontal()
        self.message = field(limit=1024)
        self.message.returnPressed.connect(self.submit_chat)
        composer.addWidget(self.message, 1)
        self.send_chat = button(callback=self.submit_chat, role="primary", icon_name="send", width=34)
        self.send_chat.setToolTip("Отправить сообщение")
        self.send_chat.setAccessibleName("Отправить сообщение")
        composer.addWidget(self.send_chat)
        chat.addLayout(composer)
        layout.addWidget(chat_frame, 1)
        self.notice = label("", "notice", True)
        self.notice.hide()
        layout.addWidget(self.notice)

    def build_inspector(self):
        outer = column(self.inspector, (13, 13, 13, 12), 0)
        scroller, layout = scroll_area()
        layout.setSpacing(12)
        outer.addWidget(scroller)
        profile_title = horizontal()
        profile_title.addWidget(label("Карточка участника", "subheading"), 1)
        close_profile = button(callback=self.toggle_inspector, role="flat", icon_name="close", width=26)
        close_profile.setAccessibleName("Свернуть карточку участника")
        close_profile.setToolTip("Свернуть карточку участника")
        self.close_profile = close_profile
        profile_title.addWidget(close_profile)
        layout.addLayout(profile_title)
        self.profile_card, self.inspector_profile = panel(margins=(10, 10, 10, 10), spacing=7)
        self.profile_card.setMinimumHeight(217)
        layout.addWidget(self.profile_card)
        studio, studio_layout = panel(margins=(12, 10, 12, 10), spacing=6)
        studio_title = horizontal()
        studio_title.addWidget(picture("sliders", "#a69cff"))
        studio_title.addWidget(label("Студия голоса", "subheading"), 1)
        setup = button(callback=self.audio_settings, role="flat", icon_name="sliders", width=29)
        setup.setAccessibleName("Настройки аудиоустройств")
        setup.setToolTip("Аудиоустройства, шумовой порог и Push-to-Talk")
        studio_title.addWidget(setup)
        studio_layout.addLayout(studio_title)
        meter_row = horizontal()
        self.probe = button("Проверка микрофона", self.set_probe, role="row", toggle=True)
        self.probe.setToolTip("Включить локальное измерение уровня микрофона")
        meter_row.addWidget(self.probe, 1)
        self.db = label("−∞ дБ", "secure")
        meter_row.addWidget(self.db)
        studio_layout.addLayout(meter_row)
        self.meter = Meter()
        studio_layout.addWidget(self.meter)
        self.loopback = button("Прослушать себя", self.set_loopback, icon_name="play", toggle=True)
        self.loopback.setToolTip("Локальное прослушивание с выбранным эффектом. Используйте наушники.")
        studio_layout.addWidget(self.loopback)
        studio_layout.addWidget(label("ГОЛОСОВЫЕ ЭФФЕКТЫ", "section"))
        effects = QGridLayout()
        effects.setSpacing(5)
        self.effects = QButtonGroup(self)
        for i, ((effect, title), glyph) in enumerate(zip(EFFECTS, EFFECT_ICONS)):
            control = button(title, lambda _checked, option=effect: self.send("audio", "effect", option),
                             icon_name=glyph, toggle=True)
            control.setChecked(i == 0)
            self.effects.addButton(control)
            effects.addWidget(control, i // 2, i % 2)
        studio_layout.addLayout(effects)
        layout.addWidget(studio)
        network, network_layout = panel(margins=(12, 10, 12, 10), spacing=7)
        network_title = horizontal()
        network_title.addWidget(picture("shield", GREEN))
        network_title.addWidget(label("Сеть и защита", "subheading"), 1)
        self.net_status = label("E2EE", "secure")
        network_title.addWidget(self.net_status)
        network_layout.addLayout(network_title)
        modes = horizontal(spacing=4)
        self.mode_controls = {}
        self.mode_group = QButtonGroup(self)
        for mode in (STUN, DIRECT, PROXY):
            control = button(NETWORK_NAMES[mode], lambda _checked, mode=mode: self.choose_mode(mode), toggle=True)
            control.setChecked(mode == DIRECT)
            self.mode_controls[mode] = control
            self.mode_group.addButton(control)
            modes.addWidget(control, 1)
        network_layout.addLayout(modes)
        self.mode_hint = label("", "muted", True)
        network_layout.addWidget(self.mode_hint)
        network_layout.addWidget(divider())
        verify = horizontal()
        verify.addWidget(label("SAS", "section"))
        self.sas = label("··· ···", "sas")
        verify.addWidget(self.sas, 1)
        self.verify_button = button("Сверить", self.verify_peer)
        verify.addWidget(self.verify_button)
        network_layout.addLayout(verify)
        layout.addWidget(network)
        layout.addStretch()

    def send(self, command, *args):
        self.worker.send(command, *args)

    def group(self):
        if self.session_group is not None and self.session_group.key == self.selected_group:
            return self.session_group
        return self.groups[self.selected_group]

    def group_in_view(self):
        return self.session_group is not None and self.selected_group == self.session_group.key

    def choose_group(self, key, overview=False):
        self.selected_group = key
        self.overview = overview
        self.update_view()

    def show_home(self):
        self.choose_group(self.selected_group, True)

    def group_entry(self, action, name, glyph=None, checked=False, accent=None):
        row = QWidget()
        layout = horizontal(spacing=0)
        row.setLayout(layout)
        marker = QFrame()
        marker.setFixedSize(4, 24)
        marker.setStyleSheet("background: #7180ff; border-radius: 2px;" if checked else "background: transparent;")
        layout.addWidget(marker, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addStretch()
        control = button("" if glyph else name[:1].upper(), action, icon_name=glyph, role="round", toggle=True)
        control.setIconSize(QSize(22, 22))
        control.setFixedSize(44, 44)
        control.setChecked(checked)
        control.setToolTip(name)
        control.setAccessibleName(name)
        if accent and not checked:
            control.setStyleSheet(f"color: {accent};")
        layout.addWidget(control)
        layout.addStretch()
        row.setMinimumHeight(44)
        return row, control

    def render_rail(self):
        clear(self.rail_layout)
        home, _ = self.group_entry(self.show_home, "Главная / Обзор", "home", self.overview)
        self.rail_layout.addWidget(home)
        self.rail_layout.addWidget(divider())
        view, content = scroll_area()
        content.setSpacing(9)
        groups = dict(self.groups)
        if self.session_group is not None:
            groups[self.session_group.key] = self.session_group
        tones = ("#dacfff", "#c5e3fe", "#efc998", "#b4e3ce")
        for i, (key, group) in enumerate(groups.items()):
            selected = not self.overview and key == self.selected_group
            row, _ = self.group_entry(lambda _=False, value=key: self.choose_group(value),
                                      group.name, "group" if i == 0 else None,
                                      selected, tones[i % len(tones)])
            content.addWidget(row)
        entry, self.add_group_control = self.group_entry(self.group_menu, "Добавить группу", "plus")
        content.addWidget(entry)
        content.addStretch()
        self.rail_layout.addWidget(view, 1)

    def group_menu(self):
        source = self.add_group_control
        menu = QMenu(self)
        menu.addAction("Создать группу…", self.create_group)
        menu.addAction("Войти по токену…", self.open_join)
        menu.exec(source.mapToGlobal(source.rect().bottomLeft()))
        menu.deleteLater()
        if not self.closing and source is self.add_group_control:
            source.setChecked(False)

    def create_group(self):
        name, accepted = QInputDialog.getText(self, "Новая группа", "Название группы")
        if accepted:
            try:
                group = Group.create(name)
                self.groups[group.key] = group
                self.choose_group(group.key)
            except ValueError as error:
                self.set_notice(str(error))

    def current_channel(self, kind):
        group = self.group()
        keys = self.text_choice if kind == "text" else self.voice_choice
        channel = group.find(kind, keys.get(group.key))
        if channel is None:
            channel = getattr(group, kind)[0]
            keys[group.key] = channel.key
        return channel

    def choose_channel(self, kind, channel):
        self.overview = False
        group = self.group()
        keys = self.text_choice if kind == "text" else self.voice_choice
        keys[group.key] = channel
        if kind == "voice" and self.group_in_view() and not self.connecting:
            self.send("move", channel)
        self.update_view()

    def create_channel(self, kind):
        name, accepted = QInputDialog.getText(
            self, "Новый текстовый канал" if kind == "text" else "Новый голосовой канал", "Название канала"
        )
        if not accepted:
            return
        try:
            group = self.group()
            new = group.add(kind, name)
            if self.group_in_view():
                self.send("channel", kind, name.strip())
            else:
                self.groups[group.key] = new
                self.choose_channel(kind, getattr(new, kind)[-1].key)
        except ValueError as error:
            self.set_notice(str(error))

    def render_navigation(self):
        group = self.group()
        self.group_title.setText(group.name)
        self.group_title.setToolTip(group.name)
        count = len(self.members) if self.group_in_view() else 1
        word = "участник" if count == 1 else "участника" if 2 <= count <= 4 else "участников"
        self.group_members.setText(f"Группа · {count} {word}")
        clear(self.channel_list)
        self.presence_rows.clear()
        for kind, heading, glyph in (("text", "ТЕКСТОВЫЕ КАНАЛЫ", "hash"),
                                     ("voice", "ГОЛОСОВЫЕ КАНАЛЫ", "voice")):
            section = horizontal(spacing=2)
            section.addWidget(label(heading, "section"), 1)
            create = button(callback=lambda _=False, k=kind: self.create_channel(k),
                            role="flat", icon_name="plus", width=25)
            create.setAccessibleName(f"Создать {'текстовый' if kind == 'text' else 'голосовой'} канал")
            create.setToolTip(create.accessibleName())
            create.setEnabled(not (self.group_in_view() and self.connecting))
            section.addWidget(create)
            self.channel_list.addLayout(section)
            for channel in getattr(group, kind):
                people = [(key, member) for key, member in self.members.items()
                          if kind == "voice" and self.group_in_view() and member.voice == channel.key]
                row = horizontal(spacing=1)
                control = button(channel.name, lambda _=False, k=kind, value=channel.key: self.choose_channel(k, value),
                                 role="row", icon_name=glyph, toggle=True)
                control.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
                control.setToolTip(channel.name)
                control.setChecked(self.current_channel(kind).key == channel.key)
                row.addWidget(control, 1)
                if kind == "voice":
                    total = label(str(len(people)), "secure" if people else "muted")
                    total.setFixedWidth(15)
                    row.addWidget(total)
                self.channel_list.addLayout(row)
                for key, member in people:
                    row = horizontal((23, 0, 1, 0))
                    widget = self.member_button(key, member, small=True)
                    row.addWidget(widget, 1)
                    self.channel_list.addLayout(row)
            self.channel_list.addSpacing(12)
        self.channel_list.addStretch()

    def render_self(self):
        clear(self.self_layout)
        self.self_layout.addWidget(ProfileHeader(self.profile, own=True, edit=self.edit_profile,
                                                 on_select=lambda: self.select_peer(None)))
        row = horizontal(spacing=6)
        row.addWidget(button("Баннер", lambda: self.edit_profile("banner"), icon_name="banner"), 1)
        row.addWidget(button("Цвет", lambda: self.edit_profile("color"), icon_name="color"), 1)
        self.self_layout.addLayout(row)

    def display_profile(self, public, member):
        if member.own:
            return self.profile
        return self.profiles.get(public) or Profile(member.name)

    def member_button(self, public, member, *, small=False):
        widgets = self.presence_rows if small else self.call_rows
        key = None if member.own else public
        widget = MemberButton(self.display_profile(public, member), member.own, key == self.inspect_peer,
                              public in self.speaking, lambda: self.select_peer(key), small=small)
        widgets[public].append(widget)
        return widget

    def render_stage(self):
        clear(self.stage_grid)
        self.call_rows.clear()
        voice = self.current_channel("voice")
        people = [(key, member) for key, member in self.members.items()
                  if self.group_in_view() and member.voice == voice.key]
        count = min(len(people), 4)
        for i in range(4):
            self.stage_grid.setColumnStretch(i, int(i < count))
        if not people:
            card, layout = panel("inset", margins=(12, 17, 12, 15))
            layout.addStretch()
            title = label(voice.name if self.overview else "Канал свободен", "subheading")
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(title)
            prompt = "Создайте комнату или подключитесь по приглашению." if not self.connecting else "Устанавливаем соединение…"
            hint = label(prompt, "muted", True)
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(hint)
            layout.addStretch()
            self.stage_grid.addWidget(card, 0, 0, 1, 4)
            return
        for i, (key, member) in enumerate(people):
            self.stage_grid.addWidget(self.member_button(key, member), i // 4, i % 4)

    def select_peer(self, public):
        self.inspect_peer = public
        self.inspector_open = True
        self.render_inspector()
        for key, widgets in self.call_rows.items():
            selected = (None if self.members[key].own else key) == public
            for widget in widgets:
                refresh(widget.control, "selected", selected)
        if public is not None and public not in self.sas_codes:
            self.send("sas", public)

    def toggle_inspector(self):
        self.inspector_open = not self.inspector_open
        self.render_inspector()

    def render_inspector(self):
        member = self.members.get(self.inspect_peer)
        public = self.inspect_peer if member else None
        profile = self.display_profile(public, member) if member else self.profile
        self.profile_card.setVisible(self.inspector_open)
        self.close_profile.setIcon(icon("close" if self.inspector_open else "plus"))
        self.close_profile.setToolTip("Свернуть карточку" if self.inspector_open else "Открыть карточку")
        clear(self.inspector_profile)
        self.inspector_profile.addWidget(ProfileHeader(profile, edit=self.edit_profile if member is None else None))
        self.inspector_profile.addWidget(divider())
        row = horizontal()
        row.addWidget(picture("shield", GREEN))
        text = "Ваш профиль" if not member else "Собеседник проверен" if member.verified else "Требуется сверка SAS"
        row.addWidget(label(text, "muted"), 1)
        if member:
            row.addWidget(label("SAS", "secure" if member.verified else "muted"))
        self.inspector_profile.addLayout(row)
        self.update_sas()

    def update_sas(self):
        code = self.sas_codes.get(self.inspect_peer)
        self.sas.setText(f"{code[:3]} {code[3:]}" if code else "··· ···")
        self.verify_button.setEnabled(bool(code and self.inspect_peer in self.members))

    def update_view(self):
        group = self.group()
        voice, text = self.current_channel("voice"), self.current_channel("text")
        self.breadcrumb.setText(f"Обзор / {group.name} / {voice.name}" if not self.overview else f"Обзор / {group.name}")
        self.channel_title.setText("Обзор" if self.overview else voice.name)
        self.channel_title.setToolTip(voice.name)
        self.chat_title.setText(text.name)
        self.render_rail()
        self.render_navigation()
        self.render_stage()
        self.render_chat(text.key)
        self.update_controls()

    def update_controls(self):
        in_call = self.session_group is not None and not self.connecting and not self.leaving
        busy = in_call or self.connecting or self.leaving
        self.create.setEnabled(not busy)
        self.join_control.setEnabled(not busy or self.connecting)
        self.mic.setEnabled(in_call)
        self.sound.setEnabled(in_call)
        self.ptt_control.setEnabled(in_call and self.mic.isChecked())
        self.ptt_control.setToolTip("Удерживайте кнопку или F8. Нажатие включает Push-to-Talk.")
        self.invite.setEnabled(in_call and self.hosting)
        self.leave.setEnabled(busy and not self.leaving)
        ready = any(member.verified for member in self.members.values())
        enabled = in_call and self.group_in_view() and ready
        self.message.setEnabled(enabled)
        self.send_chat.setEnabled(enabled)
        self.message.setPlaceholderText(
            f"Написать в #{self.current_channel('text').name}" if enabled
            else "Сначала подтвердите SAS с собеседником" if in_call and self.group_in_view()
            else "Чат доступен в подключённой комнате"
        )
        self.mode_hint.setText(MODE_HINT[self.mode])
        for mode, control in self.mode_controls.items():
            control.setEnabled(not busy)
            control.setToolTip(NETWORK_NAMES[mode] if not busy else "Режим сети задаётся до входа в комнату")
        refresh(self.badge, "encrypted", bool(in_call and ready))
        self.net_status.setText("E2EE" if ready else "E2EE · ожидание")

    def add_message_row(self, message):
        row = QFrame()
        layout = horizontal((1, 2, 5, 2), 9)
        row.setLayout(layout)
        member = self.members.get(message.public)
        profile = self.display_profile(message.public, member) if member else Profile(message.name)
        layout.addWidget(Avatar(33, profile), alignment=Qt.AlignmentFlag.AlignTop)
        text_area = QWidget()
        content = column(text_area, spacing=2)
        heading = horizontal(spacing=10)
        name = label(message.name)
        name.setStyleSheet("font-weight: 650;")
        heading.addWidget(name)
        heading.addWidget(label(message.stamp, "muted"))
        heading.addStretch()
        content.addLayout(heading)
        content.addWidget(label(message.text, wrap=True))
        layout.addWidget(text_area, 1)
        self.chat_rows.addWidget(row)

    def render_chat(self, channel, *, force=False):
        if self.rendered_channel == channel and not force:
            return
        self.rendered_channel = channel
        clear(self.chat_rows)
        history = self.messages.get(channel, ())
        if history:
            for entry in history:
                self.add_message_row(entry)
        else:
            hint = label("Сообщения появятся здесь и будут храниться до выхода из комнаты.", "muted", True)
            hint.setContentsMargins(2, 10, 3, 3)
            self.chat_rows.addWidget(hint)
        self.scroll_chat()

    def scroll_chat(self):
        bar = self.chat_scroll.verticalScrollBar()
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()) if not self.closing else None)

    def receive_chat(self, public, name, channel, message):
        history = self.messages[channel]
        first = not history
        entry = Message(public, name, datetime.now().strftime("%H:%M"), message)
        history.append(entry)
        if channel == self.rendered_channel:
            if first:
                clear(self.chat_rows)
            if self.chat_rows.count() >= MAX_MESSAGES:
                self.chat_rows.takeAt(0).widget().deleteLater()
            self.add_message_row(entry)
            self.scroll_chat()

    def submit_chat(self):
        message = self.message.text().strip()
        if self.send_chat.isEnabled() and message:
            self.send("text", message, self.current_channel("text").key)
            self.message.clear()

    def edit_profile(self, focus=None):
        dialog = ProfileDialog(self, self.profile, self.amnesia, self.save_profile, focus)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.open()

    def save_profile(self, profile, amnesia):
        self.profile_store.save(profile, amnesia)
        self.profile, self.amnesia = profile, amnesia
        if self.session_group is not None or self.connecting:
            self.send("profile", profile)
        self.render_self()
        self.render_navigation()
        self.render_stage()
        self.render_inspector()

    def choose_mode(self, mode):
        if self.session_group is None and not self.connecting:
            self.mode = mode
            if self.signal_dialog:
                self.signal_dialog.reject()
            self.update_controls()

    def set_notice(self, message):
        self.notice.setText(message)
        self.notice.setVisible(bool(message))

    def host_room(self, _checked=False, *, invite=True):
        if self.session_group is not None or self.connecting or self.leaving:
            return
        group = self.group()
        self.session_group, self.hosting = group, True
        self.overview = False
        self.send("host", self.profile, self.mode, group, self.current_channel("voice").key)
        self.update_view()
        self.set_notice("Комната открыта. Создайте приглашение для собеседника.")
        if invite:
            self.open_invite()

    def open_invite(self):
        if self.hosting and self.session_group is not None:
            self.open_exchange(True)

    def open_join(self):
        if self.session_group is None and not self.hosting and not self.leaving:
            self.open_exchange(False)

    def open_exchange(self, host):
        if self.signal_dialog is not None:
            self.signal_dialog.raise_()
            self.signal_dialog.activateWindow()
            return
        dialog = ExchangeDialog(self, host, self.mode, self.exchange, self.copy_value)
        self.signal_dialog = dialog
        dialog.finished.connect(lambda _: self.close_exchange(dialog))
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        if host:
            dialog.set_invite(*self.invitation)
        else:
            dialog.set_reply(self.reply)
        dialog.set_busy(self.signal_busy)
        dialog.open()

    def close_exchange(self, dialog):
        if self.signal_dialog is dialog:
            self.signal_dialog = None

    def exchange(self, operation, *args):
        if operation == "join":
            if self.connecting or self.session_group is not None:
                return
            self.connecting = True
            self.send("join", self.profile, self.mode, *args)
            self.set_notice("Подключение к комнате…")
            self.update_view()
        else:
            self.send(operation, *args)

    def copy_value(self, value):
        if value:
            QApplication.clipboard().setText(value)
            self.clipboard_values.add(value)
            if self.signal_dialog is not None:
                self.signal_dialog.notice.setText("Скопировано.")

    def clear_clipboard(self):
        if self.clipboard_values:
            clipboard = QApplication.clipboard()
            if clipboard.text() in self.clipboard_values:
                clipboard.clear()
            self.clipboard_values.clear()

    def set_audio(self, option, value):
        self.send("audio", option, value)

    def set_mic(self, enabled):
        self.set_audio("muted", not enabled)
        self.mic.setToolTip("Выключить микрофон" if enabled else "Включить микрофон")
        self.update_controls()

    def set_sound(self, disabled):
        self.set_audio("deafened", disabled)
        self.sound.setText("Звук выкл." if disabled else "Звук")
        self.sound.setToolTip("Включить звук комнаты" if disabled else "Отключить звук комнаты")

    def set_probe(self, enabled):
        self.set_audio("probe", enabled)

    def set_loopback(self, enabled):
        self.set_audio("loopback", enabled)
        self.loopback.setText("Остановить прослушивание" if enabled else "Прослушать себя")
        self.loopback.setToolTip("Прослушивание работает локально. Используйте наушники.")

    def set_ptt(self, enabled):
        self.ptt = enabled
        self.set_audio("ptt", enabled)
        if not enabled:
            self.release_ptt()

    def set_pressed(self, source, value):
        if value and not self.ptt_control.isEnabled():
            return
        if value and not self.ptt:
            self.set_ptt(True)
        setattr(self, source, value)
        self.set_audio("pressed", self.keyboard or self.mouse)
        if source == "keyboard":
            self.ptt_control.setDown(value)

    def release_ptt(self):
        if self.keyboard or self.mouse:
            self.keyboard = self.mouse = False
            self.ptt_control.setDown(False)
            self.set_audio("pressed", False)

    def audio_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Настройки звука")
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setMinimumWidth(360)
        layout = column(dialog, (20, 18, 20, 18), 10)
        form = QFormLayout()
        for capture, name in ((True, "Микрофон"), (False, "Наушники / выход")):
            combo = QComboBox()
            combo.addItem("По умолчанию", None)
            for index, device_name, inputs, outputs in self.devices:
                if (inputs if capture else outputs) > 0:
                    combo.addItem(device_name, index)
            combo.setCurrentIndex(max(0, combo.findData(self.audio_devices[capture])))
            combo.currentIndexChanged.connect(
                lambda _index, cap=capture, widget=combo: self.change_audio_device(cap, widget.currentData())
            )
            form.addRow(name, combo)
        layout.addLayout(form)
        noise = QCheckBox("Шумовой порог")
        noise.setChecked(self.noise)
        noise.toggled.connect(self.set_noise)
        layout.addWidget(noise)
        ptt = QCheckBox("Push-to-Talk · клавиша F8")
        ptt.setChecked(self.ptt)
        ptt.toggled.connect(self.set_ptt)
        layout.addWidget(ptt)
        layout.addWidget(button("Готово", dialog.accept, role="primary"))
        dialog.open()

    def set_noise(self, enabled):
        self.noise = enabled
        self.set_audio("noise", enabled)

    def change_audio_device(self, capture, device):
        self.audio_devices[capture] = device
        self.send("device", capture, device)

    def verify_peer(self):
        public = self.inspect_peer
        code = self.sas_codes.get(public)
        if code and public in self.members:
            profile = self.display_profile(public, self.members[public])
            dialog = SasDialog(self, profile.name, code, lambda: self.send("verify", public))
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            dialog.open()

    def leave_room(self):
        if self.leaving or (self.session_group is None and not self.connecting):
            return
        self.release_ptt()
        self.leaving = True
        if self.signal_dialog is not None:
            self.signal_dialog.reject()
        self.update_controls()
        self.set_notice("Закрываем комнату…")
        self.send("leave")

    def room_closed(self, status):
        self.session_group = None
        self.hosting = self.connecting = self.leaving = False
        self.invitation, self.reply = ("", ""), ""
        self.signal_busy = False
        self.members.clear()
        self.profiles.clear()
        self.sas_codes.clear()
        self.speaking.clear()
        self.inspect_peer = None
        self.messages.clear()
        self.rendered_channel = None
        self.message.clear()
        self.clear_clipboard()
        with QSignalBlocker(self.mic):
            self.mic.setChecked(False)
        if self.selected_group not in self.groups:
            self.voice_choice.pop(self.selected_group, None)
            self.text_choice.pop(self.selected_group, None)
            self.selected_group = next(iter(self.groups))
            self.overview = True
        if self.signal_dialog is not None:
            if self.signal_dialog.host or status:
                self.signal_dialog.reject()
            else:
                self.signal_dialog.set_reply("")
                self.signal_dialog.set_busy(False)
        self.update_view()
        self.render_inspector()
        if status or self.notice.text() in ("Закрываем комнату…", "Подключение к комнате…"):
            self.set_notice(status)

    def dispatch(self, kind, data):
        if self.closing:
            return
        if kind == "group":
            first = self.session_group is None
            self.session_group = data
            if self.hosting:
                self.groups[data.key] = data
            if first:
                self.selected_group = data.key
                self.overview = False
            self.update_view()
        elif kind == "members":
            self.members = {public: Member(name, own, verified, voice) for public, name, own, verified, voice in data}
            if self.session_group is not None:
                local = next((member for member in self.members.values() if member.own), None)
                if local and local.voice:
                    self.voice_choice[self.session_group.key] = local.voice
            self.speaking.intersection_update(self.members)
            for cache in (self.profiles, self.sas_codes):
                for key in cache.keys() - self.members.keys():
                    del cache[key]
            if self.inspect_peer is not None and self.inspect_peer not in self.members:
                self.inspect_peer = None
            if any(member.verified for member in self.members.values()) and self.notice.text().startswith(
                "Ожидаем, пока собеседник"
            ):
                self.set_notice("")
            self.update_view()
            self.render_inspector()
        elif kind == "profile":
            public, profile = data
            self.profiles[public] = profile
            self.render_navigation()
            self.render_stage()
            if public == self.inspect_peer:
                self.render_inspector()
        elif kind == "speaking":
            public, speaking = data
            if speaking:
                self.speaking.add(public)
            else:
                self.speaking.discard(public)
            for row in (*self.presence_rows.get(public, ()), *self.call_rows.get(public, ())):
                row.activity(speaking)
        elif kind == "chat":
            self.receive_chat(*data)
        elif kind == "meter":
            self.meter.set_level(data)
            self.db.setText("−∞ дБ" if data < -65 else f"{data:.0f} дБ")
        elif kind == "devices":
            self.devices = data
        elif kind == "device" and not data[1]:
            self.set_notice("Микрофон недоступен. Проверьте устройство." if data[0] else "Устройство вывода недоступно.")
        elif kind in ("status", "error", "audio_error", "fatal"):
            self.set_notice(data)
            if self.signal_dialog is not None and kind == "error":
                self.signal_dialog.notice.setText(data)
        elif kind == "addresses" and self.signal_dialog is not None:
            self.signal_dialog.notice.setText(f"Локальные адреса UDP: {data}")
        elif kind == "sas_code":
            public, code = data
            self.sas_codes[public] = code
            self.update_sas()
        elif kind == "sas":
            public, _, code = data
            self.sas_codes[public] = code
            self.update_sas()
        elif kind == "busy":
            self.signal_busy = data
            if self.signal_dialog is not None:
                self.signal_dialog.set_busy(data)
        elif kind == "invite":
            self.invitation = data
            if self.signal_dialog is not None and self.signal_dialog.host:
                self.signal_dialog.set_invite(*data)
        elif kind == "answer":
            self.reply = data
            if self.signal_dialog is not None and not self.signal_dialog.host:
                self.signal_dialog.set_reply(data)
                self.signal_dialog.notice.setText("Скопируйте ответ и передайте его хосту.")
        elif kind == "connected":
            self.connecting = False
            self.reply = ""
            if self.signal_dialog is not None:
                self.signal_dialog.reject()
            self.update_controls()
        elif kind == "ended":
            self.room_closed(data)

    def focus_changed(self, state):
        if state != Qt.ApplicationState.ApplicationActive:
            self.release_ptt()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Show and isinstance(watched, QDialog) and watched.parent() is self:
            self.release_ptt()
        if event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease) and event.key() == Qt.Key.Key_F8:
            if self.keyboard or (QApplication.activeModalWidget() is None and self.ptt_control.isEnabled()):
                if not event.isAutoRepeat():
                    self.set_pressed("keyboard", event.type() == QEvent.Type.KeyPress)
                return True
        return super().eventFilter(watched, event)

    def worker_finished(self):
        self.worker_done = True
        if self.closing:
            self.close()

    def closeEvent(self, event):
        if self.worker_done:
            self.clear_clipboard()
            super().closeEvent(event)
        else:
            self.closing = True
            self.release_ptt()
            self.worker.stop()
            event.ignore()
