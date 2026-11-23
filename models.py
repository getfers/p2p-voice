"""Проверяемые данные профиля и структуры группы, общие для UI и сети."""

import re
import secrets
from dataclasses import dataclass, replace

from crypto import b64, unb64


STATUS = {"online": "В сети", "busy": "Не беспокоить", "invisible": "Невидимка"}
MAX_AVATAR = 8000
MAX_PROFILE = 11200
MAX_CHANNELS = 12
ID_BYTES = 8
DEFAULT_BANNER = ("#264a52", "#735887")
COLOR_PATTERN = re.compile(r"#[0-9a-fA-F]{6}")


def identifier():
    return secrets.token_hex(ID_BYTES)


def read_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{16}", value):
        raise ValueError("Некорректный идентификатор.")
    return value


def title(value, limit, label):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit or not value.isprintable():
        raise ValueError(f"{label} должно содержать от 1 до {limit} печатных символов.")
    return value.strip()


def color(value):
    if not isinstance(value, str) or COLOR_PATTERN.fullmatch(value) is None:
        raise ValueError("Некорректный цвет.")
    return value.lower()


@dataclass(frozen=True)
class Profile:
    name: str
    status: str = "online"
    accent: str = "#5865f2"
    banner_first: str = DEFAULT_BANNER[0]
    banner_last: str = DEFAULT_BANNER[1]
    avatar: bytes = b""

    def document(self):
        return {
            "name": self.name, "status": self.status, "accent": self.accent,
            "banner_first": self.banner_first, "banner_last": self.banner_last,
            "avatar": b64(self.avatar),
        }

    @classmethod
    def read(cls, value):
        if not isinstance(value, dict):
            raise ValueError("Некорректный профиль.")
        status = value["status"]
        if not isinstance(status, str) or status not in STATUS:
            raise ValueError("Некорректный статус.")
        encoded = value["avatar"]
        if not isinstance(encoded, str) or len(encoded) > (MAX_AVATAR * 4 + 2) // 3:
            raise ValueError("Аватар превышает допустимый размер.")
        avatar = unb64(encoded)
        if len(avatar) > MAX_AVATAR or (avatar and not (
            avatar.startswith(b"\xff\xd8\xff") and avatar.endswith(b"\xff\xd9")
        )):
            raise ValueError("Аватар должен быть в формате JPEG.")
        return cls(
            title(value["name"], 30, "Имя"), status, color(value["accent"]),
            color(value["banner_first"]), color(value["banner_last"]), avatar,
        )


@dataclass(frozen=True)
class Channel:
    key: str
    name: str

    def document(self):
        return [self.key, self.name]

    @classmethod
    def read(cls, value):
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError("Некорректный канал.")
        return cls(read_id(value[0]), title(value[1], 28, "Название канала"))


@dataclass(frozen=True)
class Group:
    key: str
    name: str
    text: tuple[Channel, ...]
    voice: tuple[Channel, ...]

    @classmethod
    def create(cls, name):
        return cls(
            identifier(), title(name, 32, "Название группы"),
            tuple(Channel(identifier(), label) for label in ("общий", "ссылки", "оффтоп")),
            tuple(Channel(identifier(), label) for label in ("Гостиная", "Музыка")),
        )

    def document(self):
        return {
            "key": self.key, "name": self.name,
            "text": [item.document() for item in self.text],
            "voice": [item.document() for item in self.voice],
        }

    @classmethod
    def read(cls, value):
        if not isinstance(value, dict):
            raise ValueError("Некорректная группа.")
        channels = {}
        for kind in ("text", "voice"):
            items = value[kind]
            if not isinstance(items, list) or not 1 <= len(items) <= MAX_CHANNELS:
                raise ValueError("Некорректное число каналов.")
            channels[kind] = tuple(Channel.read(item) for item in items)
        keys = [item.key for item in channels["text"] + channels["voice"]]
        if len(set(keys)) != len(keys):
            raise ValueError("Повторяющиеся идентификаторы каналов.")
        return cls(read_id(value["key"]), title(value["name"], 32, "Название группы"), **channels)

    def find(self, kind, key):
        return next((item for item in getattr(self, kind) if item.key == key), None)

    def add(self, kind, name):
        if kind not in ("text", "voice"):
            raise ValueError("Неизвестный тип канала.")
        name = title(name, 28, "Название канала")
        items = getattr(self, kind)
        if len(items) >= MAX_CHANNELS:
            raise ValueError(f"В разделе может быть до {MAX_CHANNELS} каналов.")
        if any(item.name.casefold() == name.casefold() for item in items):
            raise ValueError("Канал с таким названием уже существует.")
        return replace(self, **{kind: items + (Channel(identifier(), name),)})
