"""Потоковая обработка голоса, локальное прослушивание и звук комнаты."""

import math
import time
from collections import deque

import numpy as np
import sounddevice as sd
from scipy.signal import butter, sosfilt

from crypto import wipe


RATE = 16000
FRAME_SECONDS = 0.020
SAMPLES = 320
DEVICES_ERRORS = (sd.PortAudioError, ValueError, OSError)
EFFECTS = (
    ("normal", "Обычный"), ("radio", "Рация"), ("robot", "Робот"),
    ("deep", "Грубый/Злой"), ("high", "Писклявый"), ("anonymous", "Аноним"),
)


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
            item.fill(0)


class Resampler:
    def __init__(self, source, target):
        self.indices = np.arange(source)
        self.positions = np.linspace(0, source - 1, target)

    def convert(self, source):
        return np.interp(self.positions, self.indices, source).astype(np.int16)


class PitchShift:
    """Две модулируемые линии задержки с перекрытием и плавной огибающей."""

    def __init__(self, ratio):
        self.width = 560
        self.phase = 0.0
        self.step = (1.0 - ratio) / self.width
        self.history = np.zeros(self.width + 18)
        self.offsets = np.arange(SAMPLES)
        self.source_indices = np.arange(self.history.size + SAMPLES)

    def process(self, samples):
        source = np.concatenate((self.history, samples))
        phase = (self.phase + self.step * self.offsets) % 1.0
        other = (phase + 0.5) % 1.0
        base = self.history.size + self.offsets - 12
        weight = np.sin(np.pi * phase) ** 2
        first = np.interp(base - self.width * phase, self.source_indices, source)
        second = np.interp(base - self.width * other, self.source_indices, source)
        self.history[:] = source[-self.history.size:]
        self.phase = (self.phase + self.step * SAMPLES) % 1.0
        source.fill(0)
        return weight * first + (1.0 - weight) * second

    def clear(self):
        self.history.fill(0)


class Formants:
    """Переносит огибающую спектра вниз с перекрытием соседних блоков."""

    def __init__(self):
        self.previous = np.zeros(SAMPLES)
        self.tail = np.zeros(SAMPLES)
        length = 2 * SAMPLES
        self.window = np.sqrt(np.hanning(length + 1)[:-1])
        self.bins = np.arange(SAMPLES + 1)

    def process(self, samples):
        frame = np.concatenate((self.previous, samples)) * self.window
        spectrum = np.fft.rfft(frame)
        cepstrum = np.fft.irfft(np.log(np.maximum(np.abs(spectrum), 1e-6)))
        cepstrum[18:-17] = 0
        envelope = np.fft.rfft(cepstrum).real
        lower = np.interp(self.bins / .77, self.bins, envelope)
        gain = np.exp(np.clip(lower - envelope, -1.6, 1.2))
        adjusted = np.fft.irfft(spectrum * gain) * self.window
        output = adjusted[:SAMPLES] + self.tail
        self.previous[:] = samples
        self.tail[:] = adjusted[SAMPLES:]
        return output

    def clear(self):
        self.previous.fill(0)
        self.tail.fill(0)


class VoiceEffect:
    def __init__(self, style="normal"):
        self.style = style
        self.phase = 0.0
        self.step = 2 * np.pi * 60 / RATE
        self.robot_phase = self.step * np.arange(SAMPLES)
        self.pitch = None
        self.formants = None
        self.echo = None
        self.sos = None
        if style == "radio":
            self.sos = butter(3, (300, 3000), btype="bandpass", fs=RATE, output="sos")
            self.filter_state = np.zeros((len(self.sos), 2))
            self.noise = np.random.default_rng()
        elif style in ("deep", "high", "anonymous"):
            self.pitch = PitchShift({"deep": .75, "high": 1.42, "anonymous": .61}[style])
            if style == "deep":
                self.echo = np.zeros(round(.065 * RATE))
            elif style == "anonymous":
                self.formants = Formants()

    def process(self, pcm):
        if self.style == "normal":
            return pcm.copy()
        samples = pcm.astype(np.float64) / 32768.0
        result = samples
        if self.style == "radio":
            filtered, self.filter_state = sosfilt(self.sos, samples, zi=self.filter_state)
            hiss = min(.008, float(np.sqrt(np.mean(samples ** 2))) * .14)
            result = .79 * np.tanh(filtered * 3.2) + hiss * self.noise.standard_normal(SAMPLES)
        elif self.style == "robot":
            result = 1.28 * samples * np.sin(self.phase + self.robot_phase)
            self.phase = (self.phase + self.step * SAMPLES) % (2 * np.pi)
        elif self.pitch is not None:
            result = self.pitch.process(samples)
            if self.echo is not None:
                delayed = self.echo[:SAMPLES].copy()
                self.echo[:-SAMPLES] = self.echo[SAMPLES:]
                self.echo[-SAMPLES:] = result
                result += .20 * delayed
                delayed.fill(0)
            elif self.formants is not None:
                result = self.formants.process(result)
        frame = np.rint(np.clip(result * 32768.0, -32768, 32767)).astype(np.int16)
        samples.fill(0)
        result.fill(0)
        return frame

    def clear(self):
        if self.pitch is not None:
            self.pitch.clear()
        if self.formants is not None:
            self.formants.clear()
        if self.echo is not None:
            self.echo.fill(0)
        if self.sos is not None:
            self.filter_state.fill(0)
        self.phase = 0.0
        if self.pitch is not None:
            self.pitch.phase = 0.0


class Audio:
    def __init__(self, emit):
        self.emit = emit
        self.input_stream = None
        self.output_stream = None
        self.input_device = None
        self.output_device = None
        self.input_queue = deque()
        self.output_queue = deque()
        self.monitor_queue = deque()
        self.inbound = {}
        self.volumes = {}
        self.activity = {}
        self.playing = set()
        self.muted = True
        self.deafened = False
        self.noise = True
        self.ptt = False
        self.pressed = False
        self.probe = False
        self.loopback = False
        self.effect = VoiceEffect()
        self.local = None
        self.capture_epoch = 0
        self.noise_floor = 80.0
        self.gate_hold = 0
        self.next_mix = time.monotonic()
        self.next_meter = 0

    def session(self, local):
        self.discard_capture()
        self.reset_playback()
        self.volumes.clear()
        self.local = local
        self.muted = True
        self.pressed = False
        self.sync_devices()

    def capture_enabled(self):
        return self.probe or self.loopback or (self.local is not None and not self.muted)

    def playback_enabled(self):
        return self.loopback or (self.local is not None and not self.deafened)

    def sync_devices(self):
        for capture, wanted in ((True, self.capture_enabled()), (False, self.playback_enabled())):
            attribute = "input_stream" if capture else "output_stream"
            if not wanted:
                self.close_stream(attribute)
            elif getattr(self, attribute) is None:
                self.open_device(capture)

    def change_device(self, capture, device):
        setattr(self, "input_device" if capture else "output_device", device)
        if capture:
            self.discard_capture()
        else:
            self.reset_playback()
        self.close_stream("input_stream" if capture else "output_stream")
        self.sync_devices()

    def open_device(self, capture):
        attribute = "input_stream" if capture else "output_stream"
        device = self.input_device if capture else self.output_device
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
                stream = self.make_output(device, rate, block, info["max_output_channels"])
            stream.start()
            setattr(self, attribute, stream)
            self.emit("device", (capture, True))
        except DEVICES_ERRORS:
            if stream is not None:
                try:
                    stream.close()
                except DEVICES_ERRORS:
                    pass
            self.emit("device", (capture, False))

    def make_input(self, device, rate, block):
        def callback(indata, frames, _timing, _status):
            epoch = self.capture_epoch
            if frames == block and self.capture_enabled() and len(self.input_queue) < 4:
                self.input_queue.append((epoch, bytearray(indata)))

        self.input_resampler = Resampler(block, SAMPLES)
        return sd.RawInputStream(
            device=device, samplerate=rate, blocksize=block, channels=1,
            dtype="int16", latency="low", callback=callback,
        )

    def make_output(self, device, rate, block, maximum):
        channels = 1
        try:
            sd.check_output_settings(device, channels=1, dtype="int16", samplerate=rate)
        except DEVICES_ERRORS:
            if maximum < 2:
                raise
            channels = 2
        silence = bytes(block * channels * 2)

        def callback(outdata, frames, _timing, _status):
            if frames != block:
                outdata[:] = bytes(frames * channels * 2)
                return
            try:
                data = self.output_queue.popleft()
            except IndexError:
                outdata[:] = silence
                return
            outdata[:] = data
            wipe(data)

        self.output_resampler = Resampler(SAMPLES, block)
        self.output_channels = channels
        return sd.RawOutputStream(
            device=device, samplerate=rate, blocksize=block, channels=channels,
            dtype="int16", latency="low", callback=callback,
        )

    def can_send(self):
        return self.local is not None and not self.muted and (not self.ptt or self.pressed)

    def discard_capture(self):
        self.capture_epoch += 1
        clear_queue(self.input_queue)
        self.gate_hold = 0
        self.effect.clear()
        self.stop_activity(self.local)

    def settings(self, option, value):
        if option == "effect":
            if value != self.effect.style:
                self.discard_capture()
                self.effect = VoiceEffect(value)
                clear_queue(self.monitor_queue)
                clear_queue(self.output_queue)
            return
        was_sending = self.can_send()
        setattr(self, option, value)
        if was_sending != self.can_send() or option == "loopback":
            self.discard_capture()
        if option == "loopback" and not value:
            clear_queue(self.monitor_queue)
            clear_queue(self.output_queue)
        if option == "deafened" and value:
            self.reset_playback()
        if option in ("muted", "probe", "loopback", "deafened"):
            self.sync_devices()
            if not self.capture_enabled():
                self.emit("meter", -90.0)

    def gate(self, level):
        if not self.noise:
            return True
        threshold = max(190, min(950, self.noise_floor * 2.8))
        if level < threshold:
            self.noise_floor = .97 * self.noise_floor + .03 * level
            self.gate_hold = max(0, self.gate_hold - 1)
        else:
            self.gate_hold = 6
        return bool(self.gate_hold)

    def capture(self, broadcast):
        while self.input_queue:
            epoch, data = self.input_queue.popleft()
            try:
                if epoch != self.capture_epoch or not self.capture_enabled():
                    continue
                samples = self.input_resampler.convert(np.frombuffer(data, dtype=np.int16))
                level = self.level(samples)
                now = time.monotonic()
                if now >= self.next_meter:
                    self.next_meter = now + .065
                    self.emit("meter", 20.0 * math.log10(max(level, 1) / 32768))
                send = self.can_send()
                if send or self.loopback:
                    frame = self.effect.process(samples)
                    if self.loopback:
                        if len(self.monitor_queue) >= 3:
                            self.monitor_queue.popleft().fill(0)
                        self.monitor_queue.append(frame.copy())
                    if send and self.gate(level):
                        if level >= 235:
                            self.mark_activity(self.local)
                        broadcast(frame.astype("<i2", copy=False).tobytes())
                    frame.fill(0)
                samples.fill(0)
            finally:
                wipe(data)

    @staticmethod
    def level(samples):
        return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))

    def mark_activity(self, public):
        if public not in self.activity:
            self.emit("speaking", (public, True))
        self.activity[public] = time.monotonic() + .25

    def stop_activity(self, public):
        if public in self.activity:
            del self.activity[public]
            self.emit("speaking", (public, False))

    def incoming(self, public, data):
        samples = np.frombuffer(data, dtype="<i2").copy()
        if self.level(samples) >= 235:
            self.mark_activity(public)
        if self.deafened or self.output_stream is None:
            samples.fill(0)
            return
        queue = self.inbound.setdefault(public, deque())
        if len(queue) >= 6:
            queue.popleft().fill(0)
        queue.append(samples)

    def mix(self, now):
        if now < self.next_mix:
            return
        self.next_mix = max(self.next_mix + FRAME_SECONDS, now - FRAME_SECONDS)
        frames = []
        if self.monitor_queue:
            frames.append(self.monitor_queue.popleft())
        for public, queue in self.inbound.items():
            if len(queue) >= 2:
                self.playing.add(public)
            if not queue:
                self.playing.discard(public)
            if public in self.playing:
                frame = queue.popleft()
                gain = self.volumes.get(public, 100)
                if gain != 100:
                    np.copyto(frame, frame.astype(np.int32) * gain // 100, casting="unsafe")
                frames.append(frame)
        if frames and self.output_stream is not None and len(self.output_queue) < 3:
            mixed = np.clip(np.sum(frames, axis=0, dtype=np.int32), -32768, 32767)
            output = self.output_resampler.convert(mixed)
            if self.output_channels == 2:
                output = np.repeat(output, 2)
            self.output_queue.append(bytearray(output.tobytes()))
            mixed.fill(0)
            output.fill(0)
        for frame in frames:
            frame.fill(0)

    def peer_volume(self, public, value):
        if isinstance(public, bytes) and 0 <= value <= 100:
            self.volumes[public] = value

    def tick(self):
        now = time.monotonic()
        self.mix(now)
        for public, deadline in list(self.activity.items()):
            if now >= deadline:
                self.stop_activity(public)

    def remove(self, public):
        queue = self.inbound.pop(public, None)
        if queue is not None:
            clear_queue(queue)
            clear_queue(self.output_queue)
        self.playing.discard(public)
        self.stop_activity(public)

    def reset_playback(self):
        for public in list(self.inbound):
            self.remove(public)
        clear_queue(self.monitor_queue)
        clear_queue(self.output_queue)
        self.next_mix = time.monotonic()

    def close_stream(self, attribute):
        stream = getattr(self, attribute)
        setattr(self, attribute, None)
        if stream is not None:
            try:
                stream.close()
            except DEVICES_ERRORS:
                pass

    def close(self):
        self.close_stream("input_stream")
        self.close_stream("output_stream")
        self.discard_capture()
        self.reset_playback()
        self.activity.clear()
