"""Регрессии ручного сигналинга, SAS и ретрансляции между тремя узлами."""

import asyncio
import secrets
import unittest
from dataclasses import replace

from cryptography.exceptions import InvalidTag

from crypto import Identity, b64, new_invite_key, open_document, seal_document
from network import DIRECT, PROXY, VOICE, Room


class CryptoTests(unittest.TestCase):
    def test_tamper_replay_and_reordering(self):
        first, second, stranger = Identity(), Identity(), Identity()
        room_id = secrets.token_bytes(16)
        sender = first.peer(room_id, second.public).cipher
        receiver = second.peer(room_id, first.public).cipher
        relay = stranger.peer(room_id, first.public).cipher
        packet1 = sender.encrypt(VOICE, b"audio 1", b"header")
        packet2 = sender.encrypt(VOICE, b"audio 2", b"header")
        with self.assertRaises(InvalidTag):
            relay.decrypt(VOICE, packet1, b"header")
        with self.assertRaises(InvalidTag):
            receiver.decrypt(VOICE, packet2, b"bad header")
        self.assertEqual(receiver.decrypt(VOICE, packet2, b"header"), b"audio 2")
        self.assertEqual(receiver.decrypt(VOICE, packet1, b"header"), b"audio 1")
        with self.assertRaises(ValueError):
            receiver.decrypt(VOICE, packet1, b"header")

    def test_token_requires_separate_key_and_type(self):
        key = new_invite_key()
        token = seal_document(b"invite", {"sdp": "description"}, key)
        self.assertEqual(open_document(b"invite", token, key), {"sdp": "description"})
        with self.assertRaises(InvalidTag):
            open_document(b"invite", token, new_invite_key())
        with self.assertRaises(ValueError):
            open_document(b"answer", token, key)


class AudioSink:
    def __init__(self):
        self.frames = []

    def incoming(self, sender, payload):
        self.frames.append((sender, payload))

    def remove(self, _public):
        pass

    def discard_capture(self):
        pass

    def reset_playback(self):
        self.frames.clear()


class RoomTests(unittest.IsolatedAsyncioTestCase):
    def room(self, host, name, mode):
        events = {}
        room = Room(host, name, mode, events.__setitem__, AudioSink())
        self.addAsyncCleanup(room.close)
        return room, events

    async def wait_for(self, condition):
        async with asyncio.timeout(12):
            while not condition():
                await asyncio.sleep(0.025)

    async def pair(self, one, two):
        public_one, public_two = one.identity.public, two.identity.public
        await self.wait_for(lambda: one.pairs.get(public_two) and one.pairs[public_two].sas
                            and two.pairs.get(public_one) and two.pairs[public_one].sas)
        self.assertEqual(one.pairs[public_two].sas, two.pairs[public_one].sas)
        one.verify(b64(public_two))
        await self.wait_for(lambda: two.pairs[public_one].remote_confirmed)
        self.assertFalse(one.pairs[public_two].verified)
        two.verify(b64(public_one))
        await self.wait_for(lambda: one.pairs[public_two].verified)
        self.assertTrue(two.pairs[public_one].verified)

    async def webrtc_join(self, host, host_events, guest, guest_events):
        await host.invite("", 17840)
        token, key = host_events["invite"]
        await guest.join(token, key, "", "")
        answer = guest_events["answer"]
        await host.accept_answer(answer)
        with self.assertRaises(ValueError):
            await host.accept_answer(answer)
        await self.wait_for(lambda: "connected" in guest_events)

    async def test_webrtc_three_participants(self):
        host, host_events = self.room(True, "Host", DIRECT)
        first, first_events = self.room(False, "Alice", DIRECT)
        second, second_events = self.room(False, "Bob", DIRECT)
        await self.webrtc_join(host, host_events, first, first_events)
        await self.webrtc_join(host, host_events, second, second_events)
        with self.assertRaises(ValueError):
            first.text("До проверки")
        await self.pair(first, second)
        first.text("Привет, Bob!")
        await self.wait_for(lambda: "chat" in second_events)
        self.assertEqual(second_events["chat"], (
            b64(first.identity.public), "Alice", host.group.text[0].key, "Привет, Bob!",
        ))
        self.assertNotIn("chat", host_events)
        frame = b"\x99" * 640
        second.broadcast(VOICE, frame)
        await self.wait_for(lambda: bool(first.audio.frames))
        self.assertEqual(first.audio.frames, [(second.identity.public, frame)])
        self.assertFalse(host.audio.frames)
        await self.pair(host, first)
        host.text("С хостом тоже работает")
        await self.wait_for(lambda: "chat" in first_events and first_events["chat"][1] == "Host")

    async def test_channel_and_profile_sync_with_separate_voice_channels(self):
        host, host_events = self.room(True, "Host", DIRECT)
        guest, guest_events = self.room(False, "Guest", DIRECT)
        await self.webrtc_join(host, host_events, guest, guest_events)
        self.assertEqual(guest.group, host.group)
        await self.pair(host, guest)
        guest.add_channel("voice", "Студия")
        await self.wait_for(lambda: guest.group.voice[-1].name == "Студия")
        self.assertEqual(guest.group, host.group)
        host.add_channel("text", "проверка")
        await self.wait_for(lambda: guest.group.text[-1].name == "проверка")
        custom = replace(guest.profile, name="Луна", status="busy", banner_first="#663355",
                         decoration="radar", effect="aura")
        guest.update_profile(custom)
        key = guest.identity.public
        await self.wait_for(lambda: host.profiles.get(key) == custom)
        self.assertEqual(host_events["profile"], (b64(key), custom))
        studio = guest.group.voice[-1].key
        guest.move(studio)
        await self.wait_for(lambda: host.locations[key] == studio)
        frame = b"\x11" * 640
        guest.broadcast(VOICE, frame)
        host.broadcast(VOICE, frame)
        await asyncio.sleep(.10)
        self.assertFalse(host.audio.frames)
        self.assertFalse(guest.audio.frames)
        chat = guest.group.text[-1].key
        guest.text("Сообщение в другой канал", chat)
        await self.wait_for(lambda: "chat" in host_events)
        self.assertEqual(host_events["chat"], (b64(key), "Луна", chat, "Сообщение в другой канал"))
        host.move(studio)
        await self.wait_for(lambda: guest.locations[host.identity.public] == studio)
        guest.broadcast(VOICE, frame)
        await self.wait_for(lambda: host.audio.frames)
        self.assertEqual(host.audio.frames, [(key, frame)])

    async def test_socks_resolves_name_inside_proxy(self):
        host, host_events = self.room(True, "Host", PROXY)
        guest, guest_events = self.room(False, "Guest", PROXY)
        domain = "a" * 56 + ".onion"
        await host.invite(domain + ":17840", 0)
        host_port = host.server.sockets[0].getsockname()[1]
        proxy_requests = []
        proxy_tasks = set()

        async def pump(reader, writer):
            try:
                while data := await reader.read(4096):
                    writer.write(data)
                    await writer.drain()
            finally:
                writer.close()

        async def serve_proxy(reader, writer):
            task = asyncio.current_task()
            proxy_tasks.add(task)
            upstream = None
            try:
                self.assertEqual(await reader.readexactly(3), b"\x05\x01\x00")
                writer.write(b"\x05\x00")
                self.assertEqual(await reader.readexactly(4), b"\x05\x01\x00\x03")
                size = (await reader.readexactly(1))[0]
                proxy_requests.append(await reader.readexactly(size))
                self.assertEqual(int.from_bytes(await reader.readexactly(2), "big"), 17840)
                tunnel, upstream = await asyncio.open_connection("127.0.0.1", host_port)
                writer.write(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00")
                await writer.drain()
                await asyncio.gather(pump(reader, upstream), pump(tunnel, writer))
            finally:
                writer.close()
                if upstream is not None:
                    upstream.close()
                proxy_tasks.discard(task)

        proxy = await asyncio.start_server(serve_proxy, "127.0.0.1", 0)
        try:
            port = proxy.sockets[0].getsockname()[1]
            token, key = host_events["invite"]
            await guest.join(token, key, f"127.0.0.1:{port}", "")
            await self.wait_for(lambda: "connected" in guest_events)
            await self.pair(host, guest)
            guest.text("Через SOCKS5")
            await self.wait_for(lambda: "chat" in host_events)
            self.assertEqual(host_events["chat"], (
                b64(guest.identity.public), "Guest", host.group.text[0].key, "Через SOCKS5",
            ))
            self.assertEqual(proxy_requests, [domain.encode("ascii")])
            self.assertIsNone(host.pending)
        finally:
            await guest.close()
            await host.close()
            proxy.close()
            await proxy.wait_closed()
            await asyncio.gather(*proxy_tasks, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
