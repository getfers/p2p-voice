"""Криптографические примитивы и формат ручного обмена приглашениями."""

import base64
import hashlib
import hmac
import json
import secrets
import struct
import zlib

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


PROTOCOL = b"p2p-voice/v2"
TOKEN_VERSION = "2."
MAX_TOKEN = 32768
MAX_DOCUMENT = 49152
REPLAY_WINDOW = 128


def b64(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def unb64(value, size=None):
    if not isinstance(value, str) or not value.isascii():
        raise ValueError("Неверный формат кода.")
    data = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if b64(data) != value or (size is not None and len(data) != size):
        raise ValueError("Неверный формат кода.")
    return data


def json_bytes(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")


def derive(secret, salt, purpose, size=32):
    return HKDF(
        algorithm=hashes.SHA256(), length=size, salt=salt, info=PROTOCOL + b"/" + purpose
    ).derive(secret)


def new_invite_key():
    return bytearray(secrets.token_bytes(32))


def wipe(buffer):
    if buffer is not None:
        buffer[:] = b"\x00" * len(buffer)


def seal_document(kind, document, key):
    context = PROTOCOL + b"/" + kind
    nonce = secrets.token_bytes(12)
    cipher = ChaCha20Poly1305(derive(key, None, b"token/" + kind))
    return kind.decode("ascii") + TOKEN_VERSION + b64(
        nonce + cipher.encrypt(nonce, zlib.compress(json_bytes(document), 9), context)
    )


def open_document(kind, token, key):
    prefix = kind.decode("ascii") + TOKEN_VERSION
    if token.startswith(kind.decode("ascii") + "1."):
        raise ValueError("Для приглашений нового формата обновите «Эфир» на обоих компьютерах.")
    if len(token) > MAX_TOKEN or not token.startswith(prefix):
        raise ValueError("Неверный тип или размер токена.")
    raw = unb64(token[len(prefix):])
    if len(raw) < 28:
        raise ValueError("Неполный токен.")
    cipher = ChaCha20Poly1305(derive(key, None, b"token/" + kind))
    compressed = cipher.decrypt(raw[:12], raw[12:], PROTOCOL + b"/" + kind)
    decoder = zlib.decompressobj()
    document = decoder.decompress(compressed, MAX_DOCUMENT + 1)
    if len(document) > MAX_DOCUMENT or not decoder.eof or decoder.unused_data:
        raise ValueError("Неверный размер содержимого токена.")
    value = json.loads(document)
    if not isinstance(value, dict):
        raise ValueError("Неверный формат токена.")
    return value


def transcript(offer, answer):
    return hashlib.sha256(offer.encode("ascii") + b"\x00" + answer.encode("ascii")).digest()


class ReceiveWindow:
    def __init__(self):
        self.highest = -1
        self.seen = 0

    def accepts(self, number):
        delta = self.highest - number
        return delta < 0 or (delta < REPLAY_WINDOW and not self.seen & (1 << delta))

    def mark(self, number):
        if number > self.highest:
            distance = min(number - self.highest, REPLAY_WINDOW)
            self.seen = ((self.seen << distance) | 1) & ((1 << REPLAY_WINDOW) - 1)
            self.highest = number
        else:
            self.seen |= 1 << (self.highest - number)


class PacketCipher:
    """Раздельные ключи направлений и каналов, проверка повторов после AEAD."""

    def __init__(self, secret, salt, purpose, low_side):
        keys = derive(secret, salt, purpose, 128)
        outgoing, incoming = (0, 64) if low_side else (64, 0)
        self.send = [ChaCha20Poly1305(keys[outgoing + i:outgoing + i + 32]) for i in (0, 32)]
        self.receive = [ChaCha20Poly1305(keys[incoming + i:incoming + i + 32]) for i in (0, 32)]
        self.counters = [0, 0]
        self.windows = [ReceiveWindow(), ReceiveWindow()]

    def encrypt(self, channel, payload, header=b""):
        number = struct.pack("!Q", self.counters[channel])
        self.counters[channel] += 1
        nonce = b"\x00" * 4 + number
        aad = bytes([channel]) + header + number
        return number + self.send[channel].encrypt(nonce, payload, aad)

    def decrypt(self, channel, packet, header=b""):
        if len(packet) < 24:
            raise ValueError("Неполный пакет.")
        number = int.from_bytes(packet[:8], "big")
        window = self.windows[channel]
        if not window.accepts(number):
            raise ValueError("Повтор пакета.")
        aad = bytes([channel]) + header + packet[:8]
        value = self.receive[channel].decrypt(b"\x00" * 4 + packet[:8], packet[8:], aad)
        window.mark(number)
        return value


class Peer:
    """Сначала обе стороны фиксируют случайный вклад, затем раскрывают его."""

    def __init__(self, secret, salt, low_side):
        self.cipher = PacketCipher(secret, salt, b"payload", low_side)
        self.sas_key = bytearray(derive(secret, salt, b"sas"))
        self.low_side = low_side
        self.nonce = new_invite_key()
        self.commit = self.commitment(self.nonce, low_side)
        self.remote_commit = None
        self.commit_sent = False
        self.reveal_sent = False
        self.sas = None
        self.confirmed = False
        self.remote_confirmed = False

    def commitment(self, nonce, low_side):
        return hmac.digest(self.sas_key, b"commit/" + bytes([low_side]) + bytes(nonce), "sha256")

    def reveal(self, nonce):
        if not self.reveal_sent or self.sas is not None or self.remote_commit is None:
            return
        if not hmac.compare_digest(self.remote_commit, self.commitment(nonce, not self.low_side)):
            return
        first, second = (self.nonce, nonce) if self.low_side else (nonce, self.nonce)
        digest = hmac.digest(self.sas_key, b"code/" + bytes(first) + bytes(second), "sha256")
        self.sas = f"{int.from_bytes(digest[:8], 'big') % 1_000_000:06d}"
        self.close()

    @property
    def verified(self):
        return self.confirmed and self.remote_confirmed

    def close(self):
        wipe(self.nonce)
        wipe(self.sas_key)


class Identity:
    def __init__(self):
        self.private = X25519PrivateKey.generate()
        self.public = self.private.public_key().public_bytes_raw()

    def peer(self, room_id, public):
        if public == self.public:
            raise ValueError("Получен собственный открытый ключ.")
        secret = self.private.exchange(X25519PublicKey.from_public_bytes(public))
        first, second = sorted((self.public, public))
        salt = hashlib.sha256(room_id + first + second).digest()
        return Peer(secret, salt, self.public == first)

    def link(self, remote, key, offer, answer, host):
        secret = self.private.exchange(X25519PublicKey.from_public_bytes(remote))
        return PacketCipher(secret + bytes(key), transcript(offer, answer), b"link", host)

    def close(self):
        self.private = None
