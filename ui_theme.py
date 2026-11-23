"""Светлая и тёмная палитры используют одни и те же правила компоновки."""

from re import sub
from string import Template

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication


GREEN = "#10a37f"
ACCENTS = {
    "mono": ("Чёрный монохром Codex", None), "emerald": ("Изумрудный", "#08765c"),
    "indigo": ("Индиго", "#5246ba"), "graphite": ("Графит", "#485467"),
    "amber": ("Янтарный", "#935400"),
}
DENSITIES = {"compact": ("Компактный", .82), "normal": ("Обычный", 1.0), "large": ("Крупный", 1.22)}
PALETTES = {
    "light": {
        "surface": "#ffffff", "side": "#f7f7f8", "card": "#f0f0f2", "field": "#ffffff",
        "stroke": "#e5e5e7", "text": "#0d0d0d", "muted": "#6e6e80", "disabled": "#9999a4",
        "hover": "#e9e9ec", "selected": "#e2e2e7", "focus": "#9696a1", "scroll": "#bdbdc5",
        "primary": "#0d0d0d", "on_primary": "#ffffff", "meter": "#d4d4db",
        "secure": "#0b6d55", "secure_bg": "#def3eb", "green": GREEN,
    },
    "dark": {
        "surface": "#212121", "side": "#171717", "card": "#2f2f2f", "field": "#262626",
        "stroke": "#383838", "text": "#ececec", "muted": "#9b9b9b", "disabled": "#828282",
        "hover": "#373737", "selected": "#414141", "focus": "#737373", "scroll": "#535353",
        "primary": "#ececec", "on_primary": "#171717", "meter": "#484848",
        "secure": "#81cbb8", "secure_bg": "#233c35", "green": GREEN,
    },
}


RULES = Template("""
QWidget { color: $text; font-family: "Segoe UI"; font-size: 13px; }
QMainWindow, QDialog, QWidget#workspace, QWidget#settingsPage { background: $surface; }
QWidget#titlebar { background: $surface; border-bottom: 1px solid $stroke; }
QWidget#groupRail, QWidget#navigation, QWidget#inspector { background: $side; }
QWidget#groupRail { border-right: 1px solid $stroke; }
QFrame#card, QFrame#inset { background: $card; border: 1px solid $stroke; border-radius: 14px; }
QFrame#divider { min-height: 1px; max-height: 1px; background: $stroke; border: none; }
QFrame#toast { background: $field; border: 1px solid $focus; border-radius: 13px; }
QLabel#muted, QLabel#notice { color: $muted; }
QLabel#section { color: $muted; font-size: 11px; font-weight: 600; }
QLabel#subheading { font-size: 15px; font-weight: 600; }
QLabel#groupTitle { font-size: 21px; font-weight: 600; }
QLabel#channelTitle { font-size: 32px; font-weight: 600; }
QLabel#sas { font-family: "Consolas", monospace; font-size: 19px; font-weight: 700; }
QLabel#secure, QLabel#meterLevel[active="true"] { color: $secure; }
QLabel#secure[protected="false"], QLabel#meterLevel { color: $muted; }
QLabel#badge { background: $secure_bg; color: $secure; border-radius: 11px; padding: 4px 8px; }
QLabel#badge[encrypted="false"] { background: $selected; color: $muted; }
QPushButton, QLineEdit, QComboBox, QSpinBox {
  background: $field; border: 1px solid $stroke; border-radius: 9px;
  selection-background-color: $selected; selection-color: $text;
}
QPushButton { min-height: 27px; padding: 1px 8px; font-size: 12px; font-weight: 600; }
QPushButton:focus, QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: $focus; }
QPushButton:disabled, QLineEdit:disabled { color: $disabled; }
QPushButton:checked { background: $selected; border-color: $focus; }
QPushButton[role="primary"] { background: $primary; border-color: $primary; color: $on_primary; }
QPushButton[role="primary"]:focus { border-color: $focus; }
QPushButton[role="primary"]:disabled { background: $selected; border-color: $stroke; color: $disabled; }
QPushButton[role="flat"], QPushButton[role="row"], QPushButton[role="chrome"], QPushButton[role="closeWindow"] {
  background: transparent; border: 1px solid transparent; color: $muted;
}
QPushButton[role="chrome"], QPushButton[role="closeWindow"] { font-size: 16px; color: $text; padding: 0; }
QPushButton[role="row"] { text-align: left; padding-left: 8px; }
QPushButton[role="flat"]:focus, QPushButton[role="row"]:focus,
QPushButton[role="chrome"]:focus, QPushButton[role="closeWindow"]:focus { border-color: $focus; }
QPushButton[role="row"]:checked { background: $selected; color: $text; border-left: 2px solid $focus; }
QPushButton[role="flat"]:disabled { color: $disabled; }
QPushButton#microphone:checked, QPushButton#microphoneCheck:checked, QPushButton#microphoneMonitor:checked {
  background: $secure_bg; border: 1px solid $green; color: $secure;
}
QPushButton#screenShare[sharing="true"] { background: $primary; color: $on_primary; border-color: $primary; }
QPushButton[role="round"] {
  background: $card; border: 1px solid $stroke; border-radius: 13px;
  min-height: 0; padding: 0; font-size: 17px; font-weight: 600;
}
QPushButton[role="round"]:checked { background: $selected; border-color: $focus; }
QPushButton[role="member"] { min-height: 99px; padding: 0; background: $field; border-radius: 13px; }
QPushButton[role="member"][selected="true"] { border-color: $focus; }
QPushButton[role="member"][speaking="true"] { border: 1px solid $green; background: $secure_bg; }
QPushButton[role="presence"] { min-height: 26px; padding: 0; background: transparent; border: 1px solid transparent; }
QPushButton[role="presence"][speaking="true"] { border-color: $green; background: $secure_bg; }
QLineEdit, QComboBox, QSpinBox { min-height: 28px; padding: 2px 9px; }
QComboBox { padding-right: 26px; }
QComboBox::drop-down { border: none; width: 23px; subcontrol-origin: padding; subcontrol-position: top right; }
QComboBox QAbstractItemView, QMenu {
  background: $field; border: 1px solid $stroke; selection-background-color: $selected;
}
QMenu::item { padding: 6px 22px 6px 10px; }
QMenu::item:selected { background: $selected; }
QCheckBox { spacing: 7px; }
QScrollArea { border: none; background: transparent; }
QWidget#scrollBody, QWidget#scrollViewport { background: transparent; }
QScrollBar:vertical { background: transparent; width: 7px; margin: 2px; }
QScrollBar::handle:vertical { background: $scroll; border-radius: 3px; min-height: 19px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0px; width: 0px; }
QSplitter::handle { background: transparent; }
QSplitter::handle:horizontal { border-left: 1px solid $stroke; }
QSplitter::handle:vertical { border-top: 1px solid $stroke; }
QSplitter::handle:hover { background: $hover; }
QSplitter::handle:pressed { background: $selected; }
""")


def active_palette():
    app = QApplication.instance()
    name = app.property("sekretTheme") if app is not None else "light"
    colors = PALETTES.get(name, PALETTES["light"])
    accent = app.property("sekretAccent") if app is not None else None
    _, color = ACCENTS.get(accent, ACCENTS["mono"])
    if color:
        return {**colors, "primary": color, "on_primary": "#ffffff", "focus": color}
    return colors


def apply_palette(name, widget, scale=100, accent="mono", density="normal"):
    app = QApplication.instance()
    app.setProperty("sekretTheme", name)
    app.setProperty("sekretAccent", accent)
    colors = active_palette()
    palette = QPalette()
    for role, key in (
        (QPalette.ColorRole.Window, "surface"), (QPalette.ColorRole.WindowText, "text"),
        (QPalette.ColorRole.Base, "field"), (QPalette.ColorRole.AlternateBase, "side"),
        (QPalette.ColorRole.Text, "text"), (QPalette.ColorRole.Button, "field"),
        (QPalette.ColorRole.ButtonText, "text"), (QPalette.ColorRole.Highlight, "selected"),
        (QPalette.ColorRole.HighlightedText, "text"), (QPalette.ColorRole.PlaceholderText, "muted"),
        (QPalette.ColorRole.ToolTipBase, "card"), (QPalette.ColorRole.ToolTipText, "text"),
    ):
        palette.setColor(role, QColor(colors[key]))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors["disabled"]))
    app.setPalette(palette)
    rules = RULES.substitute(colors)
    if scale != 100:
        rules = sub(r"(font-size|min-height): (\d+)px", lambda m: (
            f"{m[1]}: {round(int(m[2]) * scale / 100)}px" if int(m[2]) >= 10 else m[0]
        ), rules)
    _, spacing = DENSITIES[density]
    rules = sub(r"(padding(?:-left|-right)?): ([\dpx ]+);", lambda m: (
        f"{m[1]}: " + sub(r"(\d+)px", lambda px: f"{round(int(px[1]) * spacing)}px", m[2]) + ";"
    ), rules)
    widget.setStyleSheet(rules)
