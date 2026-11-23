"""Захват, воспроизведение, микширование и простой шумовой порог."""

import math
import struct
import time
from array import array
from collections import deque

import sounddevice as sd

from crypto import wipe


RATE = 16000
FRAME_SECONDS = 0.020
SAMPLES = 320
WIRE = struct.Struct("<320h")
DEVICES_ERRORS = (sd.PortAudioError, ValueError, OSError)


def devices():
    return [
        (index, item["name"], item["max_input_channels"], item["max_output_channels"])
        for index, item in enumerate(sd.query_devices())
    ]


def clear_queue(queue):
    while True:
        try:
            item = queue.popleft()
        except IndexError:
            return
        if isinstance(item, tuple):
            item = item[1]
        if isinstance(item, bytearray):
            wipe(item)
        else:
            item[:] = array("h", [0]) * len(item)


class Resampler:
    """Интерполирует блок длительностью 20 мс между частотами устройства и сети."""

    def __init__(self, source, target):
        self.steps = []
        for index in range(target):
            position = index * (source - 1) / (target - 1)
            left = int(position)
            self.steps.append((left, min(left + 1, source - 1), position - left))

    def convert(self, source):
        return array("h", (
            int(source[left] + (source[right] - source[left]) * weight)
            for left, right, weight in self.steps
        ))


class Audio:
    def __init__(self, emit):
        self.emit = emit
        self.input_stream = None
        self.output_stream = None
        self.input_device = None
        self.output_device = None
        self.input_queue = deque()
        self.output_queue = deque()
        self.inbound = {}
        self.activity = {}
        self.playing = set()
        self.muted = True
        self.deafened = False
        self.noise = True
        self.ptt = False
        self.pressed = False
        self.local = None
        self.capture_epoch = 0
        self.noise_floor = 80.0
        self.gate_hold = 0
        self.next_mix = time.monotonic()

    def start(self, local):
        self.local = local
        self.change_device(True, self.input_device)
        self.change_device(False, self.output_device)

    def change_device(self, capture, device):
        attribute = "input_stream" if capture else "output_stream"
        setattr(self, "input_device" if capture else "output_device", device)
        self.close_stream(attribute)
        clear_queue(self.input_queue if capture else self.output_queue)
        stream = None
        try:
            info = sd.query_devices(device, "input" if capture else "output")
            rate = round(info["default_samplerate"])
            block = round(rate * FRAME_SECONDS)
            if not 2 <= block <= 4000:
                raise ValueError("Неподдерживаемая частота устройства.")
            if capture:
                stream = self.make_input(device, rate, block)
            else:
                stream = self.make_output(device, rate, block)
            stream.start()
            setattr(self, attribute, stream)
        except DEVICES_ERRORS:
            if stream is not None:
                try:
                    stream.close()
                except DEVICES_ERRORS:
                    pass
            self.emit("audio_error", "Микрофон недоступен." if capture else "Устройство звука недоступно.")
        self.emit("device", (capture, getattr(self, attribute) is not None))

    def make_input(self, device, rate, block):
        def callback(indata, frames, _timing, _status):
            epoch = self.capture_epoch
            if frames != block or not self.can_send():
                return
            if len(self.input_queue) < 4:
                self.input_queue.append((epoch, bytearray(indata)))

        self.input_resampler = Resampler(block, SAMPLES)
        return sd.RawInputStream(
            device=device, samplerate=rate, blocksize=block, channels=1,
            dtype="int16", latency="low", callback=callback,
        )

    def make_output(self, device, rate, block):
        silence = bytes(block * 2)

        def callback(outdata, frames, _timing, _status):
            if self.deafened or frames != block or not self.output_queue:
                outdata[:] = silence if frames == block else bytes(frames * 2)
                return
            try:
                data = self.output_queue.popleft()
            except IndexError:
                outdata[:] = silence
                return
            outdata[:] = data
            wipe(data)

        self.output_resampler = Resampler(SAMPLES, block)
        return sd.RawOutputStream(
            device=device, samplerate=rate, blocksize=block, channels=1,
            dtype="int16", latency="low", callback=callback,
        )

    def can_send(self):
        return not self.muted and (not self.ptt or self.pressed)

    def settings(self, option, value):
        setattr(self, option, value)
        if not self.can_send():
            self.capture_epoch += 1
            clear_queue(self.input_queue)
            self.gate_hold = 0
            self.stop_activity(self.local)
        if self.deafened:
            for public in list(self.inbound):
                self.flush(public)
            clear_queue(self.output_queue)

    def capture(self, broadcast):
        while self.input_queue:
            epoch, data = self.input_queue.popleft()
            try:
                if epoch != self.capture_epoch or not self.can_send():
                    continue
                source = array("h")
                source.frombytes(data)
                samples = self.input_resampler.convert(source)
                source[:] = array("h", [0]) * len(source)
                level = self.level(samples)
                if self.noise:
                    threshold = max(190, min(950, self.noise_floor * 2.8))
                    if level < threshold:
                        self.noise_floor = 0.97 * self.noise_floor + 0.03 * level
                        self.gate_hold = max(0, self.gate_hold - 1)
                    else:
                        self.gate_hold = 6
                    if not self.gate_hold:
                        samples[:] = array("h", [0]) * SAMPLES
                        continue
                if level >= 235:
                    self.mark_activity(self.local)
                broadcast(WIRE.pack(*samples))
                samples[:] = array("h", [0]) * SAMPLES
            finally:
                wipe(data)

    @staticmethod
    def level(samples):
        return math.sqrt(sum(sample * sample for sample in samples) / SAMPLES)

    def mark_activity(self, public):
        if public not in self.activity:
            self.emit("speaking", (public, True))
        self.activity[public] = time.monotonic() + 0.25

    def stop_activity(self, public):
        if public in self.activity:
            del self.activity[public]
            self.emit("speaking", (public, False))

    def incoming(self, public, data):
        samples = array("h", WIRE.unpack(data))
        if self.level(samples) >= 235:
            self.mark_activity(public)
        if self.deafened or self.output_stream is None:
            samples[:] = array("h", [0]) * SAMPLES
            return
        queue = self.inbound.setdefault(public, deque())
        if len(queue) >= 6:
            old = queue.popleft()
            old[:] = array("h", [0]) * SAMPLES
        queue.append(samples)

    def mix(self, now):
        if now < self.next_mix:
            return
        self.next_mix = max(self.next_mix + FRAME_SECONDS, now - FRAME_SECONDS)
        frames = []
        for public, queue in self.inbound.items():
            if len(queue) >= 2:
                self.playing.add(public)
            if not queue:
                self.playing.discard(public)
            if public in self.playing:
                frames.append(queue.popleft())
        if frames and len(self.output_queue) < 4:
            mixed = [
                max(-32768, min(32767, sum(values)))
                for values in zip(*frames)
            ]
            outgoing = self.output_resampler.convert(mixed)
            self.output_queue.append(bytearray(outgoing.tobytes()))
            outgoing[:] = array("h", [0]) * len(outgoing)
        for frame in frames:
            frame[:] = array("h", [0]) * SAMPLES

    def tick(self):
        now = time.monotonic()
        self.mix(now)
        for public, deadline in list(self.activity.items()):
            if now >= deadline:
                self.stop_activity(public)

    def flush(self, public):
        clear_queue(self.inbound.pop(public, deque()))
        self.playing.discard(public)

    def remove(self, public):
        self.flush(public)
        self.stop_activity(public)

    def close_stream(self, attribute):
        stream = getattr(self, attribute)
        setattr(self, attribute, None)
        if stream is not None:
            try:
                stream.close()
            except DEVICES_ERRORS:
                pass

    def close(self):
        self.muted = True
        self.deafened = True
        self.close_stream("input_stream")
        self.close_stream("output_stream")
        clear_queue(self.input_queue)
        clear_queue(self.output_queue)
        for public in list(self.inbound):
            self.flush(public)
        self.activity.clear()
