"""Локальные настройки клиента без профилей, ключей и истории комнат."""

from PyQt6.QtCore import QSettings

from audio_dsp import THRESHOLD
from branding import APP_NAME


DEFAULTS = {
    "theme": "light", "scale": 100, "density": "normal", "accent": "mono",
    "tray_start": False, "tray_close": False, "hardware": False,
    "ptt_key": "F8", "mic_key": "Ctrl+Shift+M", "sound_key": "Ctrl+Shift+D", "ptt": False,
    "noise": True, "threshold": THRESHOLD, "bitrate": 64, "buffer": 3, "aec": False, "agc": False,
    "ui_volume": 34, "notification_sound": True, "toast": True,
}


class Preferences:
    def __init__(self, ephemeral=False):
        self.file = None if ephemeral else QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                                                    APP_NAME, "preferences")
        self.values = DEFAULTS.copy()
        if self.file is not None:
            for key, default in DEFAULTS.items():
                try:
                    self.values[key] = self.file.value(key, default, type=type(default))
                except (TypeError, ValueError):
                    pass

    def __getitem__(self, key):
        return self.values[key]

    def set(self, key, value):
        self.values[key] = value
        if self.file is not None:
            self.file.setValue(key, value)
