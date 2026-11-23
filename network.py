"""Ручной сигналинг, WebRTC, SOCKS5 и ретрансляция зашифрованных пакетов."""

import asyncio
import ipaddress
import json
import secrets
import struct
import time
from dataclasses import dataclass

from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc import sdp as rtc_sdp
from av.error import FFmpegError
from cryptography.exceptions import InvalidTag

from crypto import (
    MAX_TOKEN, Identity, b64, json_bytes, new_invite_key, open_document, seal_document,
    unb64, wipe,
)
from ice import gathered_description
from models import Group, ID_BYTES, MAX_PROFILE, Profile, read_id
from screen_media import CHUNK, STREAM_ID, VIDEO_HEADER, ScreenReceiver, video_chunks


STUN = "stun"
DIRECT = "direct"
PROXY = "proxy"
INVITE_SECONDS = 600
MAX_USERS = 8
MAX_IDENTITIES = 64
MAX_FRAME = MAX_TOKEN + 1
CONTROL, VOICE, VIDEO = 0, 1, 2
ROSTER, PAYLOAD = b"R", b"P"
COMMIT, REVEAL, CONFIRM, TEXT = b"B", b"N", b"V", b"M"
PROFILE, MOVE, ADD_CHANNEL = b"U", b"J", b"Q"
SCREEN_START, SCREEN_STOP = b"S", b"X"
VOICE_BYTES = 640
CHAT_BYTES = 4096
DATA_ERRORS = (ValueError, TypeError, KeyError, OverflowError, RecursionError, InvalidTag)


def read_invitation(token, key):
    offer = open_document(b"invite", token, key)
    mode = offer.get("mode")
    if mode not in (STUN, DIRECT, PROXY):
        raise ValueError("Приглашение содержит неизвестный режим сети.")
    if type(offer.get("expires")) is not int or offer["expires"] < time.time():
        raise ValueError("Срок действия приглашения истёк.")
    if "stun" in offer and (type(offer["stun"]) is not bool or offer["stun"] != (mode == STUN)):
        raise ValueError("В приглашении противоречивые настройки STUN.")
    return offer


def endpoint(value, numeric=False):
    host, separator, port = value.rpartition(":")
    if not separator or not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise ValueError("Укажите адрес с портом, например 192.168.1.8:17840.")
    host = host.removeprefix("[").removesuffix("]")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if numeric or not host or len(host) > 253 or not host.isascii() or not all(
            char.isalnum() or char in ".-" for char in host
        ):
            raise ValueError("Некорректный адрес.") from None
    else:
        if ip.is_unspecified or ip.is_multicast or "%" in host:
            raise ValueError("Укажите адрес конкретного компьютера.")
        host = str(ip)
    return host, int(port)


def usable_sdp(value, mode, advertised=""):
    if not isinstance(value, str) or len(value) > 24000:
        raise ValueError("Некорректное описание WebRTC.")
    description = rtc_sdp.SessionDescription.parse(value)
    if len(description.media) != 1 or description.media[0].kind != "application":
        raise ValueError("Ожидается один канал данных WebRTC.")
    media = description.media[0]
    candidates = []
    for candidate in media.ice_candidates:
        try:
            ip = ipaddress.ip_address(candidate.ip)
        except ValueError:
            continue
        if (ip.is_multicast or ip.is_unspecified or "%" in candidate.ip
                or candidate.protocol.lower() != "udp" or candidate.component != 1
                or not 1 <= candidate.port <= 65535):
            continue
        if candidate.type == "host" or (mode == STUN and candidate.type == "srflx"):
            candidates.append(candidate)
    if not candidates:
        raise ValueError("В SDP нет доступных UDP-адресов. Проверьте сетевые интерфейсы.")
    if advertised:
        host, port = endpoint(advertised, numeric=True)
        version = ipaddress.ip_address(host).version
        candidate = next((item for item in candidates if ipaddress.ip_address(item.ip).version == version), None)
        if candidate is None:
            raise ValueError("Для внешнего адреса нет подходящего локального интерфейса IPv4 или IPv6.")
        candidate.ip, candidate.port = host, port
        media.host, media.port = host, port
        candidates = [candidate]
    media.ice_candidates = candidates
    return str(description)


async def read_frame(reader, limit=MAX_FRAME):
    size = int.from_bytes(await reader.readexactly(4), "big")
    if not 1 <= size <= limit:
        raise ValueError("Недопустимый размер пакета.")
    return await reader.readexactly(size)


def write_frame(writer, packet):
    writer.write(struct.pack("!I", len(packet)) + packet)


async def connect_socks(proxy, remote):
    proxy_host, proxy_port = endpoint(proxy, numeric=True)
    if not ipaddress.ip_address(proxy_host).is_loopback:
        raise ValueError("Прокси SOCKS5 должен слушать на локальном loopback-адресе.")
    host, port = endpoint(remote)
    reader, writer = await asyncio.open_connection(proxy_host, proxy_port)
    try:
        writer.write(b"\x05\x01\x00")
        await writer.drain()
        if await reader.readexactly(2) != b"\x05\x00":
            raise ValueError("Прокси SOCKS5 не принял подключение без пароля.")
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            encoded = host.encode("ascii")
            address = b"\x03" + bytes([len(encoded)]) + encoded
        else:
            address = (b"\x01" if ip.version == 4 else b"\x04") + ip.packed
        writer.write(b"\x05\x01\x00" + address + struct.pack("!H", port))
        await writer.drain()
        version, status, reserved, family = await reader.readexactly(4)
        if (version, status, reserved) != (5, 0, 0):
            raise ValueError(f"Прокси SOCKS5 отклонил подключение (код {status}).")
        if family == 3:
            size = (await reader.readexactly(1))[0]
        elif family in (1, 4):
            size = 4 if family == 1 else 16
        else:
            raise ValueError("Некорректный ответ SOCKS5.")
        await reader.readexactly(size + 2)
        return reader, writer
    except BaseException:
        writer.close()
        raise


@dataclass
class Invitation:
    token: str
    key: bytearray
    expires: int
    voice: str
    link: object = None

    def clear(self):
        wipe(self.key)
        self.token = ""


class Link:
    def __init__(self, room, transport=None):
        self.room = room
        self.pc = transport
        self.writer = None
        self.reader_task = None
        self.channels = {}
        self.remote = None
        self.name = ""
        self.entry_voice = None
        self.secure = None
        self.active = False
        self.closed = False
        self.deadline = time.monotonic() + 35

    def attach(self, channel):
        index = {"control": CONTROL, "voice": VOICE, "screen": VIDEO}.get(channel.label)
        if index is None or index in self.channels:
            channel.close()
            return
        self.channels[index] = channel

        @channel.on("open")
        def opened():
            if index == VIDEO:
                self.room.show_screen_access()
            else:
                self.check_ready()

        @channel.on("message")
        def received(packet):
            self.receive(index, packet)

        @channel.on("close")
        def closed():
            if index == VIDEO:
                if not self.room.closed:
                    self.room.show_screen_access()
            else:
                self.room.spawn(self.room.drop(self))

        self.check_ready()

    def check_ready(self):
        if self.closed or self.active or self.secure is None:
            return
        if self.writer is not None or (
            all(channel in self.channels and self.channels[channel].readyState == "open"
                for channel in (CONTROL, VOICE))
        ):
            self.active = True
            self.room.link_ready(self)

    def enable_screen(self):
        self.attach(self.pc.createDataChannel("screen", ordered=False, maxRetransmits=0,
                                              negotiated=True, id=1000))

    def screen_ready(self):
        channel = self.channels.get(VIDEO)
        return self.active and channel is not None and channel.readyState == "open"

    def begin_stream(self, reader, writer):
        self.writer = writer
        self.check_ready()
        self.reader_task = self.room.spawn(self.read_stream(reader))

    async def read_stream(self, reader):
        try:
            while not self.closed:
                frame = await read_frame(reader)
                if frame[0] not in (CONTROL, VOICE):
                    raise ValueError("Неизвестный канал.")
                self.receive(frame[0], frame[1:])
        except (OSError, EOFError, ValueError):
            pass
        finally:
            await self.room.drop(self)

    def send(self, channel, payload):
        if not self.active or self.closed:
            return
        if self.writer is not None:
            pending = self.writer.transport.get_write_buffer_size()
        else:
            data_channel = self.channels.get(channel)
            if data_channel is None or data_channel.readyState != "open":
                return
            pending = data_channel.bufferedAmount
        if channel == VOICE and pending > 2048:
            return
        if channel == VIDEO and pending > 90_000:
            return
        if pending > 131072:
            self.room.spawn(self.room.drop(self))
            return
        try:
            packet = self.secure.encrypt(channel, payload)
            if self.writer is not None:
                write_frame(self.writer, bytes([channel]) + packet)
            else:
                data_channel.send(packet)
        except (OSError, RuntimeError):
            self.room.spawn(self.room.drop(self))

    def receive(self, channel, packet):
        if not self.active or self.closed or not isinstance(packet, bytes) or len(packet) > MAX_FRAME:
            return
        try:
            payload = self.secure.decrypt(channel, packet)
            self.room.receive(self, channel, payload)
        except DATA_ERRORS:
            return

    async def close(self):
        if self.closed:
            return
        self.closed = True
        self.active = False
        self.secure = None
        if self.reader_task is not None and self.reader_task is not asyncio.current_task():
            self.reader_task.cancel()
        if self.writer is not None:
            self.writer.close()
            self.writer = None
        if self.pc is not None:
            await self.pc.close()
            self.pc = None
        self.channels.clear()


class Room:
    def __init__(self, host, name, mode, emit, audio, identity=None, *, group=None,
                 voice=None, profile=None):
        self.host = host
        self.name = name
        self.mode = mode
        self.emit = emit
        self.audio = audio
        self.identity = identity if identity is not None else Identity()
        self.profile = profile or Profile(name)
        self.room_id = secrets.token_bytes(16) if host else None
        self.members = {self.identity.public: name}
        self.group = (group or Group.create("Моя группа")) if host else None
        self.voice = (voice or self.group.voice[0].key) if host else None
        if host and not self.group.find("voice", self.voice):
            raise ValueError("Голосовой канал больше недоступен.")
        self.locations = {self.identity.public: self.voice}
        self.profiles = {self.identity.public: self.profile}
        self.pairs = {}
        self.links = {}
        self.pending = None
        self.server = None
        self.accepting = set()
        self.tasks = set()
        self.closed = False
        self.relay = None
        self.screen_stream = 0
        self.screen_sequence = 0
        self.screen_sharing = False
        self.screen_targets = set()
        self.screen_receivers = {}

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def new_webrtc(self, host):
        link = Link(self, RTCPeerConnection(RTCConfiguration(iceServers=[])))
        if host:
            link.attach(link.pc.createDataChannel("control"))
            link.attach(link.pc.createDataChannel("voice", ordered=False, maxRetransmits=0))
        else:
            @link.pc.on("datachannel")
            def data_channel(channel):
                if channel.label in ("control", "voice"):
                    link.attach(channel)
                else:
                    channel.close()

        @link.pc.on("connectionstatechange")
        def changed():
            if link.pc is not None and link.pc.connectionState == "failed":
                self.spawn(self.drop(link))

        return link

    async def local_sdp(self, pc, description):
        raw = await gathered_description(pc, description, self.mode == STUN)
        details = usable_sdp(raw, self.mode)
        if self.mode == STUN and not any(
            candidate.type == "srflx"
            for candidate in rtc_sdp.SessionDescription.parse(details).media[0].ice_candidates
        ):
            self.emit("ice_notice", "STUN не вернул внешний UDP-адрес. Приглашение может работать только по локальному адресу или VPN.")
        return details

    async def invite(self, advertised, local_port):
        if not self.host or self.closed or self.pending is not None:
            raise ValueError("Сначала отмените или примите текущее приглашение.")
        if len(self.links) >= MAX_USERS - 1 or len(self.pairs) >= MAX_IDENTITIES:
            raise ValueError("Достигнут предел подключений для этой комнаты.")
        expires = int(time.time()) + INVITE_SECONDS
        pending = self.pending = Invitation("", new_invite_key(), expires, self.voice)
        description = {
            "mode": self.mode, "stun": self.mode == STUN, "expires": expires, "room": b64(self.room_id),
            "pub": b64(self.identity.public), "name": self.name,
            "group": self.group.document(), "voice": pending.voice,
        }
        try:
            if self.mode == PROXY:
                endpoint(advertised)
                if self.server is None or self.server.sockets[0].getsockname()[1] != local_port:
                    if self.server is not None:
                        if self.links or self.accepting:
                            raise ValueError("Для смены локального порта завершите текущую комнату.")
                        self.server.close()
                        await self.server.wait_closed()
                        self.server = None
                    self.server = await asyncio.start_server(self.accept_stream, "127.0.0.1", local_port)
                description["address"] = advertised
            else:
                description["screen"] = True
                link = pending.link = self.new_webrtc(True)
                async with asyncio.timeout(20):
                    details = await self.local_sdp(link.pc, await link.pc.createOffer())
                if advertised and self.mode == DIRECT:
                    description["sdp"] = usable_sdp(details, self.mode, advertised)
                else:
                    description["sdp"] = details
                if self.mode == DIRECT:
                    self.show_addresses(details)
            pending.token = seal_document(b"invite", description, pending.key)
            self.emit("invite", (pending.token, b64(pending.key)))
        except BaseException:
            await self.cancel_invite()
            raise

    async def cancel_invite(self):
        pending, self.pending = self.pending, None
        if pending is not None:
            pending.clear()
            self.emit("invite", ("", ""))
            if pending.link is not None:
                await pending.link.close()

    def show_addresses(self, details):
        media = rtc_sdp.SessionDescription.parse(details).media[0]
        addresses = [
            f"[{c.ip}]:{c.port}" if ":" in c.ip else f"{c.ip}:{c.port}"
            for c in media.ice_candidates
        ]
        self.emit("addresses", ", ".join(addresses))

    @staticmethod
    def read_identity(document):
        public = unb64(document["pub"], 32)
        name = document["name"]
        if not isinstance(name, str) or not 1 <= len(name) <= 30 or not name.isprintable():
            raise ValueError("Некорректный позывной.")
        return public, name

    async def join(self, token, key_text, proxy, advertised):
        key = bytearray(unb64(key_text, 32))
        writer = None
        try:
            offer = read_invitation(token, key)
            if offer["mode"] != self.mode:
                raise ValueError("Выберите режим соединения, указанный хостом.")
            self.room_id = unb64(offer["room"], 16)
            remote, name = self.read_identity(offer)
            self.group = Group.read(offer["group"])
            self.voice = read_id(offer["voice"])
            if not self.group.find("voice", self.voice):
                raise ValueError("Приглашение содержит неизвестный голосовой канал.")
            self.locations[self.identity.public] = self.voice
            answer = {"pub": b64(self.identity.public), "name": self.name}
            if self.mode == PROXY:
                async with asyncio.timeout(15):
                    reader, writer = await connect_socks(proxy, offer["address"])
                    challenge = await read_frame(reader, 32)
                    if len(challenge) != 32:
                        raise ValueError("Некорректный запрос подтверждения.")
                    answer["challenge"] = b64(challenge)
                    sealed = seal_document(b"answer", answer, key)
                    link = Link(self)
                    self.register(link, remote, name, key, token, sealed)
                    write_frame(writer, sealed.encode("ascii"))
                    await writer.drain()
                    link.begin_stream(reader, writer)
                    writer = None
            else:
                link = self.new_webrtc(False)
                self.relay = link
                async with asyncio.timeout(20):
                    await link.pc.setRemoteDescription(
                        RTCSessionDescription(usable_sdp(offer["sdp"], self.mode), "offer")
                    )
                    if offer.get("screen") is True:
                        link.enable_screen()
                        answer["screen"] = True
                    details = await self.local_sdp(link.pc, await link.pc.createAnswer())
                if self.mode == DIRECT and advertised:
                    self.show_addresses(details)
                    details = usable_sdp(details, self.mode, advertised)
                answer["sdp"] = details
                sealed = seal_document(b"answer", answer, key)
                self.register(link, remote, name, key, token, sealed)
                link.deadline = time.monotonic() + min(90, max(0, offer["expires"] - time.time()))
                self.emit("answer", sealed)
        finally:
            wipe(key)
            if writer is not None:
                writer.close()

    def require_invite(self):
        if self.pending is None or not self.pending.token or self.pending.expires < time.time():
            raise ValueError("Нужно действующее приглашение.")
        return self.pending

    def register(self, link, public, name, key, offer, answer):
        if public in self.links or len(self.links) >= MAX_USERS - 1 or (
            self.host and public in self.pairs
        ):
            raise ValueError("Этот участник уже использовал ключ комнаты.")
        self.pair(public)
        link.secure = self.identity.link(public, key, offer, answer, self.host)
        link.remote, link.name = public, name
        link.deadline = time.monotonic() + 35
        self.links[public] = link
        if not self.host:
            self.relay = link

    async def accept_answer(self, token):
        if not self.host or self.mode == PROXY:
            raise ValueError("Ручной ответ доступен для WebRTC.")
        pending = self.require_invite()
        answer = open_document(b"answer", token, pending.key)
        public, name = self.read_identity(answer)
        sdp = usable_sdp(answer["sdp"], self.mode)
        link = pending.link
        link.entry_voice = pending.voice
        self.register(link, public, name, pending.key, pending.token, token)
        self.consume_invite()
        try:
            if answer.get("screen") is True:
                link.enable_screen()
            await link.pc.setRemoteDescription(RTCSessionDescription(sdp, "answer"))
        except BaseException:
            await self.drop(link)
            raise

    def consume_invite(self):
        self.pending.clear()
        self.pending = None
        self.emit("invite", ("", ""))

    async def accept_stream(self, reader, writer):
        if self.closed or len(self.accepting) >= MAX_USERS:
            writer.close()
            return
        self.accepting.add(writer)
        try:
            pending = self.require_invite()
            challenge = secrets.token_bytes(32)
            async with asyncio.timeout(10):
                write_frame(writer, challenge)
                await writer.drain()
                token = (await read_frame(reader, MAX_TOKEN)).decode("ascii")
                if self.require_invite() is not pending:
                    return
                answer = open_document(b"answer", token, pending.key)
                if unb64(answer["challenge"], 32) != challenge:
                    return
                public, name = self.read_identity(answer)
                link = Link(self)
                link.entry_voice = pending.voice
                self.register(link, public, name, pending.key, pending.token, token)
                self.consume_invite()
                link.begin_stream(reader, writer)
                self.accepting.discard(writer)
        except (*DATA_ERRORS, OSError, EOFError):
            pass
        finally:
            if writer in self.accepting:
                self.accepting.discard(writer)
                writer.close()

    def pair(self, public):
        if public not in self.pairs:
            if len(self.pairs) >= MAX_IDENTITIES:
                raise ValueError("Слишком много ключей за время звонка.")
            self.pairs[public] = self.identity.peer(self.room_id, public)
        return self.pairs[public]

    def link_ready(self, link):
        if self.host:
            self.members[link.remote] = link.name
            self.locations[link.remote] = link.entry_voice
            self.publish_roster()
            self.emit("status", "Участник подключён.")
        elif self.mode != PROXY:
            self.emit("status", "Защищённый канал открыт. Ожидаем список участников.")

    def publish_roster(self):
        roster = [
            {"pub": b64(public), "name": name, "voice": self.locations[public]}
            for public, name in self.members.items()
        ]
        packet = ROSTER + json_bytes({"group": self.group.document(), "members": roster})
        for link in self.links.values():
            link.send(CONTROL, packet)
        self.begin_sas()
        self.show_roster()

    def show_roster(self):
        self.prune_screen()
        self.show_screen_access()
        self.emit("members", [
            (b64(public), name, public == self.identity.public,
             public in self.pairs and self.pairs[public].verified, self.locations.get(public))
            for public, name in self.members.items()
        ])

    def receive_roster(self, link, data):
        document = json.loads(data)
        if not isinstance(document, dict):
            return
        group = Group.read(document["group"])
        if (group.key != self.group.key or group.name != self.group.name
                or not set(self.group.text) <= set(group.text)
                or not set(self.group.voice) <= set(group.voice)):
            return
        roster = document["members"]
        if not isinstance(roster, list) or not 2 <= len(roster) <= MAX_USERS:
            return
        identities = [self.read_identity(member) for member in roster]
        members = dict(identities)
        if (len(members) != len(roster) or members.get(link.remote) != link.name
                or members.get(self.identity.public) != self.name):
            return
        locations = {public: read_id(member["voice"]) for (public, _), member in zip(identities, roster)}
        if any(not group.find("voice", channel) for channel in locations.values()):
            return
        for public in members.keys() - {self.identity.public}:
            self.pair(public)
        for public in self.members.keys() - members.keys():
            self.audio.remove(public)
            self.profiles.pop(public, None)
        for public in (members.keys() & self.members.keys()) - {self.identity.public}:
            if locations[public] != self.voice and self.locations.get(public) == self.voice:
                self.audio.remove(public)
        connected = link.remote in self.members
        if not connected or group != self.group:
            self.group = group
            self.emit("group", self.group)
        self.members = members
        self.locations = locations
        self.locations[self.identity.public] = self.voice
        self.begin_sas()
        self.show_roster()
        if not connected:
            self.emit("connected", None)
            self.emit("status", "Вы в комнате. Сверьте коды участников.")

    def receive(self, link, channel, packet):
        if channel == CONTROL:
            if packet[:1] == ROSTER and not self.host and link is self.relay:
                self.receive_roster(link, packet[1:])
                return
            if self.host and link.remote in self.members:
                if packet[:1] == MOVE:
                    self.set_location(link.remote, read_id(packet[1:].decode("ascii")))
                    return
                if packet[:1] == ADD_CHANNEL and len(packet) <= 256:
                    request = json.loads(packet[1:])
                    try:
                        self.add_channel(request["kind"], request["name"])
                    except ValueError as error:
                        link.send(CONTROL, b"E" + str(error).encode("utf-8"))
                    return
            elif packet[:1] == b"E" and link is self.relay and len(packet) <= 256:
                self.emit("error", packet[1:].decode("utf-8"))
                return
        limit = {VOICE: VOICE_BYTES + ID_BYTES, VIDEO: ID_BYTES + VIDEO_HEADER.size + CHUNK,
                 CONTROL: MAX_PROFILE + 1}[channel]
        if packet[:1] != PAYLOAD or not 89 < len(packet) <= 89 + limit:
            return
        sender, target = packet[1:33], packet[33:65]
        if sender not in self.members or target not in self.members or sender == target:
            return
        if self.host and sender != link.remote:
            return
        if channel in (VOICE, VIDEO) and self.locations[sender] != self.locations[target]:
            return
        if target == self.identity.public:
            peer = self.pairs[sender]
            data = peer.cipher.decrypt(channel, packet[65:], packet[:65])
            if channel == VOICE:
                if (peer.verified and len(data) == VOICE_BYTES + ID_BYTES
                        and data[:ID_BYTES].hex() == self.voice):
                    self.audio.incoming(sender, data[ID_BYTES:])
            elif channel == VIDEO:
                if peer.verified and data[:ID_BYTES].hex() == self.voice:
                    self.receive_screen(sender, data[ID_BYTES:])
            else:
                self.receive_control(sender, peer, data)
        elif self.host:
            self.links[target].send(channel, packet)

    def send_to(self, target, channel, payload):
        sender = self.identity.public
        link = self.links[target] if self.host else self.relay
        header = PAYLOAD + sender + target
        cipher = self.pairs[target].cipher
        link.send(channel, header + cipher.encrypt(channel, payload, header))

    def begin_sas(self):
        for target in self.members.keys() - {self.identity.public}:
            peer = self.pairs[target]
            if not peer.commit_sent:
                peer.commit_sent = True
                self.send_to(target, CONTROL, COMMIT + peer.commit)

    def receive_control(self, sender, peer, data):
        kind, body = data[:1], data[1:]
        if kind == COMMIT and len(body) == 32 and peer.remote_commit is None:
            peer.remote_commit = body
            peer.reveal_sent = True
            self.send_to(sender, CONTROL, REVEAL + peer.nonce)
        elif kind == REVEAL and len(body) == 32 and peer.sas is None:
            peer.reveal(body)
            if peer.sas is not None:
                self.emit("sas_code", (b64(sender), peer.sas))
        elif kind == CONFIRM and not body and peer.sas is not None:
            was_verified = peer.verified
            peer.remote_confirmed = True
            self.show_roster()
            if peer.verified and not was_verified:
                self.send_profile(sender)
        elif kind == TEXT and peer.verified and ID_BYTES < len(body) <= ID_BYTES + CHAT_BYTES:
            channel = body[:ID_BYTES].hex()
            if self.group.find("text", channel):
                self.emit_chat(sender, channel, body[ID_BYTES:].decode("utf-8"))
        elif kind == PROFILE and peer.verified and len(body) <= MAX_PROFILE:
            self.profiles[sender] = Profile.read(json.loads(body)).public()
            self.emit("profile", (b64(sender), self.profiles[sender]))
        elif kind in (SCREEN_START, SCREEN_STOP) and peer.verified and len(body) == STREAM_ID.size:
            stream = STREAM_ID.unpack(body)[0]
            receiver = self.screen_receivers.get(sender)
            if kind == SCREEN_START and self.locations[sender] == self.voice:
                if receiver is None or receiver.stream != stream:
                    if receiver is not None:
                        self.remove_screen(sender)
                    self.screen_receivers[sender] = ScreenReceiver(stream, time.monotonic())
            elif receiver is not None and receiver.stream == stream:
                self.remove_screen(sender)

    def send_profile(self, public):
        self.send_to(public, CONTROL, PROFILE + json_bytes(self.profile.public().document()))

    def update_profile(self, profile):
        self.profile = profile
        self.profiles[self.identity.public] = profile
        self.broadcast(CONTROL, PROFILE + json_bytes(profile.public().document()))

    def screen_recipients(self):
        local = self.identity.public
        return {
            public for public in self.members if public != local
            and self.locations.get(public) == self.voice and self.pairs[public].verified
            and (link := self.links.get(public) if self.host else self.relay) is not None
            and link.screen_ready()
        }

    def show_screen_access(self):
        self.emit("screen_access", bool(self.screen_recipients()))

    def start_screen(self):
        if not self.screen_recipients():
            raise ValueError("Для демонстрации нужен проверенный собеседник с поддержкой WebRTC в голосовом канале.")
        self.screen_stream += 1
        self.screen_sequence = 0
        self.screen_sharing = True

    def stop_screen(self):
        for public in self.screen_targets & self.members.keys():
            self.send_to(public, CONTROL, SCREEN_STOP + STREAM_ID.pack(self.screen_stream))
        self.screen_targets.clear()
        self.screen_sharing = False

    def send_screen(self, frame):
        if not self.screen_sharing or self.closed:
            return
        targets = self.screen_recipients()
        stream = STREAM_ID.pack(self.screen_stream)
        for public in (self.screen_targets - targets) & self.members.keys():
            self.send_to(public, CONTROL, SCREEN_STOP + stream)
        for public in targets - self.screen_targets:
            self.send_to(public, CONTROL, SCREEN_START + stream)
        self.screen_targets = targets
        channel = bytes.fromhex(self.voice)
        for chunk in video_chunks(frame, self.screen_stream, self.screen_sequence):
            for public in targets:
                self.send_to(public, VIDEO, channel + chunk)
        self.screen_sequence += 1

    def receive_screen(self, sender, data):
        receiver = self.screen_receivers.get(sender)
        if receiver is not None:
            frame = receiver.accept(data)
            if frame is not None:
                receiver.pending = frame
                if receiver.task is None:
                    receiver.task = self.spawn(self.decode_screen(sender, receiver))

    async def decode_screen(self, sender, receiver):
        try:
            while receiver.pending is not None:
                encoded, receiver.pending = receiver.pending, None
                try:
                    frame = await asyncio.to_thread(receiver.decode, encoded)
                except FFmpegError:
                    continue
                if frame is not None and self.screen_receivers.get(sender) is receiver:
                    receiver.last_frame = time.monotonic()
                    receiver.visible = True
                    self.emit("screen_frame", (b64(sender), *frame))
        finally:
            receiver.task = None

    def remove_screen(self, public):
        receiver = self.screen_receivers.pop(public)
        if receiver.task is not None:
            receiver.task.cancel()
        self.emit("screen_ended", b64(public))

    def prune_screen(self):
        for public in list(self.screen_receivers):
            if public not in self.members or self.locations.get(public) != self.voice:
                self.remove_screen(public)

    def add_channel(self, kind, name):
        candidate = self.group.add(kind, name)
        if self.host:
            self.group = candidate
            self.emit("group", self.group)
            self.publish_roster()
        elif self.relay and self.relay.active and self.relay.remote in self.members:
            self.relay.send(CONTROL, ADD_CHANNEL + json_bytes({"kind": kind, "name": name}))

    def set_location(self, public, channel):
        if not self.group.find("voice", channel) or self.locations[public] == channel:
            return
        self.locations[public] = channel
        if public == self.identity.public:
            if self.screen_sharing:
                self.stop_screen()
                self.emit("screen_stopped", None)
            self.voice = channel
            self.audio.discard_capture()
            self.audio.reset_playback()
        else:
            self.audio.remove(public)
        if self.host:
            self.publish_roster()
        else:
            self.relay.send(CONTROL, MOVE + channel.encode("ascii"))
            self.show_roster()

    def move(self, channel):
        if self.host or (self.relay and self.relay.remote in self.members):
            self.set_location(self.identity.public, channel)

    def broadcast(self, channel, payload):
        sender = self.identity.public
        for target in self.members.keys() - {sender}:
            if not self.pairs[target].verified:
                continue
            if channel == VOICE:
                if self.locations[target] != self.voice:
                    continue
                self.send_to(target, channel, bytes.fromhex(self.voice) + payload)
            else:
                self.send_to(target, channel, payload)

    def emit_chat(self, sender, channel, message):
        profile = self.profiles.get(sender)
        name = profile.name if profile else self.members[sender]
        self.emit("chat", (b64(sender), name, channel, message))

    def text(self, message, channel=None):
        payload = message.encode("utf-8")
        if not 1 <= len(payload) <= CHAT_BYTES:
            raise ValueError("Текст должен занимать до 4096 байт в UTF-8.")
        if not any(self.pairs[public].verified for public in self.members.keys() - {self.identity.public}):
            raise ValueError("Сначала подтвердите SAS с собеседником.")
        channel = channel or self.group.text[0].key
        if not self.group.find("text", channel):
            raise ValueError("Текстовый канал больше недоступен.")
        self.broadcast(CONTROL, TEXT + bytes.fromhex(channel) + payload)
        self.emit_chat(self.identity.public, channel, message)

    def show_sas(self, key):
        public = unb64(key, 32)
        if public in self.members and public in self.pairs:
            code = self.pairs[public].sas
            if code is None:
                self.emit("status", "Ожидаем код собеседника.")
            else:
                self.emit("sas", (key, self.members[public], code))

    def verify(self, key):
        public = unb64(key, 32)
        if public in self.members and public in self.pairs:
            peer = self.pairs[public]
            if peer.sas is not None and not peer.confirmed:
                peer.confirmed = True
                self.send_to(public, CONTROL, CONFIRM)
                self.show_roster()
                if peer.verified:
                    self.send_profile(public)
                else:
                    self.emit("status", "Ожидаем, пока собеседник подтвердит SAS.")

    async def drop(self, link):
        if self.closed or link.closed:
            return
        if self.links.get(link.remote) is link:
            del self.links[link.remote]
        was_member = self.members.pop(link.remote, None)
        self.locations.pop(link.remote, None)
        self.profiles.pop(link.remote, None)
        self.audio.remove(link.remote)
        await link.close()
        if self.host:
            if was_member is not None:
                self.publish_roster()
                self.emit("status", "Участник отключился.")
            elif link.remote is not None:
                self.emit("status", "Связь с участником не установлена.")
        elif link is self.relay:
            if was_member is None and self.mode == STUN:
                message = "Связь не установлена. Проверьте, что хост принял ответ. При блокировке UDP или CGNAT попробуйте общий VPN."
            else:
                message = "Связь с хостом завершена или не установлена."
            self.emit("ended", message)

    async def tick(self):
        now = time.monotonic()
        for public, receiver in list(self.screen_receivers.items()):
            if receiver.visible and now - receiver.last_frame > 4:
                receiver.visible = False
                self.emit("screen_ended", b64(public))
        if self.pending is not None and self.pending.expires < time.time():
            await self.cancel_invite()
            self.emit("status", "Срок действия приглашения истёк.")
        for link in list(self.links.values()):
            if time.monotonic() > link.deadline and (
                not link.active or (not self.host and link.remote not in self.members)
            ):
                await self.drop(link)

    async def close(self):
        if self.closed:
            return
        self.closed = True
        if self.server is not None:
            self.server.close()
        for writer in list(self.accepting):
            writer.close()
        await self.cancel_invite()
        links = set(self.links.values())
        if self.relay is not None:
            links.add(self.relay)
        await asyncio.gather(*(link.close() for link in links), return_exceptions=True)
        if self.server is not None:
            await self.server.wait_closed()
            self.server = None
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.identity.close()
        self.links.clear()
        for peer in self.pairs.values():
            peer.close()
        self.pairs.clear()
        self.members.clear()
        self.locations.clear()
        self.profiles.clear()
        self.screen_receivers.clear()
        self.screen_targets.clear()
        self.profile = None
