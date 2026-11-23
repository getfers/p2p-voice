"""Потоковая обработка голоса, локальное прослушивание и звук комнаты."""

import time
from collections import deque

import numpy as np
import sounddevice as sd
from scipy.signal import butter, sosfilt
from av.error import FFmpegError

from audio_dsp import AutoGain, EchoControl, ExtendedEffect, LEVEL_FLOOR, RATE, SAMPLES, THRESHOLD, dbfs
from crypto import wipe
from voice_codec import BITRATES, MIX_RATE, MIX_SAMPLES, PCM_BYTES, OpusDecoder, OpusEncoder, is_opus


FRAME_SECONDS = 0.020
DEVICES_ERRORS = (sd.PortAudioError, ValueError, OSError)
EFFECTS = (
    ("normal", "Обычный"), ("radio", "Рация"), ("robot", "Робот"),
    ("deep", "Грубый/Злой"), ("high", "Писклявый"), ("anonymous", "Аноним"),
    ("horn", "Мегафон / Рупор"), ("telephone", "Старый телефон"),
    ("cave", "Эхо / Пещера"), ("hall", "Стадион / Зал"), ("water", "Под водой"),
    ("chorus", "Хор / Двойник"), ("cyborg", "Пришелец / Киборг"), ("retro", "8-бит / Ретро-игра"),
    ("whisper", "Шёпот / Тень"), ("helium", "Гелий / Мультяшка"),
)
BUFFERS = ((2, "Низкая задержка · 40 мс"), (3, "Сбалансированный · 60 мс"), (5, "Стабильный звук · 100 мс"))
EFFECT_BLEND = np.linspace(0.0, 1.0, SAMPLES)


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
    """Переносит огибающую спектра с перекрытием соседних блоков."""

    def __init__(self, ratio=.77):
        self.previous = np.zeros(SAMPLES)
        self.tail = np.zeros(SAMPLES)
        length = 2 * SAMPLES
        self.window = np.sqrt(np.hanning(length + 1)[:-1])
        self.bins = np.arange(SAMPLES + 1)
        self.source_bins = self.bins / ratio

    def process(self, samples):
        frame = np.concatenate((self.previous, samples)) * self.window
        spectrum = np.fft.rfft(frame)
        cepstrum = np.fft.irfft(np.log(np.maximum(np.abs(spectrum), 1e-6)))
        cepstrum[18:-17] = 0
        envelope = np.fft.rfft(cepstrum).real
        shifted = np.interp(self.source_bins, self.bins, envelope)
        gain = np.exp(np.clip(shifted - envelope, -1.6, 1.2))
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
        self.extended = None
        if style == "radio":
            self.sos = butter(3, (300, 3000), btype="bandpass", fs=RATE, output="sos")
            self.filter_state = np.zeros((len(self.sos), 2))
            self.noise = np.random.default_rng()
        elif style in ("deep", "high", "anonymous", "helium"):
            self.pitch = PitchShift({"deep": .75, "high": 1.42, "anonymous": .61, "helium": 1.74}[style])
            if style == "deep":
                self.echo = np.zeros(round(.065 * RATE))
            elif style == "anonymous":
                self.formants = Formants()
            elif style == "helium":
                self.formants = Formants(1.46)
        elif style not in ("normal", "robot"):
            self.extended = ExtendedEffect(style)

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
        elif self.extended is not None:
            result = self.extended.process(samples)
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
        if self.extended is not None:
            self.extended.clear()
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
        self.input_tail = bytearray()
        self.input_bytes = PCM_BYTES
        self.input_channels = 1
        self.output_tail = bytearray()
        self.output_epoch = 0
        self.echo_queue = deque()
        self.echo_tail = bytearray()
        self.monitor_queue = deque()
        self.inbound = {}
        self.volumes = {}
        self.activity = {}
        self.playing = set()
        self.missed = set()
        self.decoders = {}
        self.to_mix_rate = Resampler(SAMPLES, MIX_SAMPLES)
        self.aec = False
        self.agc = False
        self.echo = EchoControl()
        self.auto_gain = AutoGain()
        self.bitrate = 64
        self.opus_encoder = None
        self.voice_sequence = 0
        self.buffer = 3
        self.threshold = THRESHOLD
        self.muted = True
        self.deafened = False
        self.noise = True
        self.ptt = False
        self.pressed = False
        self.probe = False
        self.loopback = False
        self.effect = VoiceEffect()
        self.previous_effect = None
        self.local = None
        self.capture_epoch = 0
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
        self.voice_sequence = 0
        self.opus_encoder = None
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
        try:
            info = sd.query_devices(device, "input" if capture else "output")
        except DEVICES_ERRORS:
            self.emit("device", (capture, False))
            return
        for rate in dict.fromkeys((MIX_RATE, round(info["default_samplerate"]))):
            block = round(rate * FRAME_SECONDS)
            if not 2 <= block <= 4000:
                continue
            maximum = max(1, min(info["max_input_channels" if capture else "max_output_channels"], 2))
            for channels in dict.fromkeys((1, maximum) if capture else (maximum, 1)):
                stream = None
                try:
                    stream = (self.make_input(device, rate, block, channels) if capture
                              else self.make_output(device, rate, block, channels))
                    stream.start()
                except DEVICES_ERRORS:
                    if stream is not None:
                        try:
                            stream.close()
                        except DEVICES_ERRORS:
                            pass
                    continue
                setattr(self, attribute, stream)
                self.emit("device", (capture, True))
                return
        self.emit("device", (capture, False))

    def make_input(self, device, rate, block, channels):
        def callback(indata, _frames, _timing, _status):
            epoch = self.capture_epoch
            if self.capture_enabled() and len(self.input_queue) < 12:
                self.input_queue.append((epoch, bytearray(indata)))

        self.input_resampler = Resampler(block, SAMPLES)
        self.input_channels = channels
        self.input_bytes = block * channels * 2
        return sd.RawInputStream(
            device=device, samplerate=rate, blocksize=block, channels=channels,
            dtype="int16", latency="low", callback=callback,
        )

    def make_output(self, device, rate, block, channels):
        epoch = self.output_epoch
        def callback(outdata, frames, _timing, _status):
            nonlocal epoch
            if epoch != self.output_epoch:
                epoch = self.output_epoch
                wipe(self.output_tail)
                self.output_tail.clear()
            data = self.output_bytes(frames * channels * 2)
            outdata[:] = data
            if self.aec and not self.loopback and len(self.echo_queue) < 12:
                self.echo_queue.append((epoch, data))
            else:
                wipe(data)

        self.output_resampler = Resampler(MIX_SAMPLES, block)
        self.reference_resampler = Resampler(block, SAMPLES)
        self.reference_bytes = block * channels * 2
        self.output_channels = channels
        return sd.RawOutputStream(
            device=device, samplerate=rate, blocksize=block, channels=channels,
            dtype="int16", latency="low", callback=callback,
        )

    def output_bytes(self, size):
        data = bytearray(size)
        offset = 0
        while offset < size:
            if not self.output_tail:
                try:
                    self.output_tail = self.output_queue.popleft()
                except IndexError:
                    break
            count = min(size - offset, len(self.output_tail))
            data[offset:offset + count] = self.output_tail[:count]
            del self.output_tail[:count]
            offset += count
        return data

    def reset_output(self):
        self.output_epoch += 1
        clear_queue(self.output_queue)
        clear_queue(self.echo_queue)
        wipe(self.echo_tail)
        self.echo_tail.clear()
        self.echo.clear()

    def read_reference(self):
        while self.echo_queue:
            epoch, data = self.echo_queue.popleft()
            if epoch == self.output_epoch:
                self.echo_tail.extend(data)
            if isinstance(data, bytearray):
                wipe(data)
        if self.aec and self.output_stream is not None:
            while len(self.echo_tail) >= self.reference_bytes:
                data = self.echo_tail[:self.reference_bytes]
                del self.echo_tail[:self.reference_bytes]
                samples = np.frombuffer(data, dtype=np.int16)[::self.output_channels]
                self.echo.add_reference(self.reference_resampler.convert(samples))
                wipe(data)

    def can_send(self):
        return self.local is not None and not self.muted and (not self.ptt or self.pressed)

    def discard_capture(self):
        self.capture_epoch += 1
        clear_queue(self.input_queue)
        wipe(self.input_tail)
        self.input_tail.clear()
        self.gate_hold = 0
        self.effect.clear()
        if self.previous_effect is not None:
            self.previous_effect.clear()
            self.previous_effect = None
        self.stop_activity(self.local)

    def settings(self, option, value):
        if option == "effect":
            if value != self.effect.style:
                if (self.can_send() or self.loopback) and self.previous_effect is None:
                    self.previous_effect = self.effect
                else:
                    self.effect.clear()
                self.effect = VoiceEffect(value)
            return
        if option == "bitrate" and value != self.bitrate:
            if value in BITRATES:
                self.bitrate = value
                self.opus_encoder = None
            return
        was_sending = self.can_send()
        setattr(self, option, value)
        if was_sending != self.can_send() or option == "loopback":
            self.discard_capture()
        if option == "loopback" and not value:
            clear_queue(self.monitor_queue)
            self.reset_output()
        if option == "aec" and not value:
            self.reset_output()
        if option == "agc":
            self.auto_gain.gain = 1.0
        if option == "deafened" and value:
            self.reset_playback()
        if option in ("muted", "probe", "loopback", "deafened"):
            self.sync_devices()
            if not self.capture_enabled():
                self.emit("meter", LEVEL_FLOOR)

    def gate(self, level):
        if not self.noise or self.ptt:
            return True
        if level >= self.threshold:
            self.gate_hold = 8
        opened = self.gate_hold > 0
        self.gate_hold = max(0, self.gate_hold - 1)
        return opened

    def capture(self, broadcast):
        self.read_reference()
        while self.input_queue:
            epoch, data = self.input_queue.popleft()
            if epoch == self.capture_epoch and self.capture_enabled():
                self.input_tail.extend(data)
            wipe(data)
        while len(self.input_tail) >= self.input_bytes:
            data = self.input_tail[:self.input_bytes]
            del self.input_tail[:self.input_bytes]
            raw = np.frombuffer(data, dtype=np.int16).reshape(-1, self.input_channels)
            samples = self.input_resampler.convert(raw[:, 0] if self.input_channels == 1 else raw.mean(axis=1))
            wipe(data)
            self.capture_frame(samples, broadcast)
            samples.fill(0)

    def capture_frame(self, samples, broadcast):
        if self.aec and not self.loopback:
            reduced = self.echo.process(samples.astype(np.float64))
            samples[:] = np.clip(reduced, -32768, 32767)
            reduced.fill(0)
        rms = self.level(samples)
        level = dbfs(rms)
        now = time.monotonic()
        if now >= self.next_meter:
            self.next_meter = now + .065
            self.emit("meter", level)
        send = self.can_send() and self.gate(level)
        if send or self.loopback:
            source = self.auto_gain.process(samples, rms) if self.agc else samples
            frame = self.effect.process(source)
            if self.previous_effect is not None:
                outgoing = self.previous_effect.process(source)
                frame[:] = np.rint((1 - EFFECT_BLEND) * outgoing + EFFECT_BLEND * frame).astype(np.int16)
                outgoing.fill(0)
                self.previous_effect.clear()
                self.previous_effect = None
            if self.agc:
                source.fill(0)
            if self.loopback:
                if len(self.monitor_queue) >= 3:
                    self.monitor_queue.popleft().fill(0)
                self.monitor_queue.append(frame.copy())
            if send:
                if level > -75:
                    self.mark_activity(self.local)
                broadcast(frame.astype("<i2", copy=False).tobytes())
            frame.fill(0)

    def encode(self, pcm):
        try:
            if self.opus_encoder is None:
                self.opus_encoder = OpusEncoder(self.bitrate)
            payload = self.opus_encoder.encode(pcm, self.voice_sequence)
            self.voice_sequence = (self.voice_sequence + 1) % (2 ** 32)
            return payload
        except FFmpegError:
            self.opus_encoder = None
            return None

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
        if len(data) == PCM_BYTES:
            samples = self.to_mix_rate.convert(np.frombuffer(data, dtype="<i2"))
        elif is_opus(data):
            try:
                if public not in self.decoders:
                    self.decoders[public] = OpusDecoder()
                samples = self.decoders[public].decode(data)
            except FFmpegError:
                return
            if samples is None:
                return
        else:
            return
        if dbfs(self.level(samples)) > -75:
            self.mark_activity(public)
        if self.deafened or self.output_stream is None:
            samples.fill(0)
            return
        queue = self.inbound.setdefault(public, deque())
        if len(queue) >= self.buffer + 3:
            queue.popleft().fill(0)
        queue.append(samples)

    def mix(self, now):
        if now < self.next_mix:
            return
        self.next_mix = max(self.next_mix + FRAME_SECONDS, now - FRAME_SECONDS)
        frames = []
        if self.monitor_queue:
            preview = self.monitor_queue.popleft()
            frames.append(self.to_mix_rate.convert(preview))
            preview.fill(0)
        mixed = np.zeros(MIX_SAMPLES, dtype=np.int32)
        for public, queue in self.inbound.items():
            if len(queue) >= self.buffer:
                self.playing.add(public)
            if not queue:
                if public in self.missed:
                    self.playing.discard(public)
                self.missed.add(public)
            elif public in self.playing:
                self.missed.discard(public)
                frame = queue.popleft()
                gain = self.volumes.get(public, 100)
                mixed += frame.astype(np.int32) * gain // 100
                frame.fill(0)
        for frame in frames:
            mixed += frame
            frame.fill(0)
        if (np.any(mixed) or frames) and self.output_stream is not None and len(self.output_queue) < 3:
            np.clip(mixed, -32768, 32767, out=mixed)
            output = self.output_resampler.convert(mixed)
            if self.output_channels == 2:
                output = np.repeat(output, 2)
            self.output_queue.append(bytearray(output.tobytes()))
            output.fill(0)
        mixed.fill(0)

    def peer_volume(self, public, value):
        if isinstance(public, bytes) and 0 <= value <= 200:
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
            self.reset_output()
        self.playing.discard(public)
        self.missed.discard(public)
        self.decoders.pop(public, None)
        self.stop_activity(public)

    def reset_playback(self):
        for public in list(self.inbound):
            self.remove(public)
        self.decoders.clear()
        clear_queue(self.monitor_queue)
        self.reset_output()
        self.next_mix = time.monotonic()

    def close_stream(self, attribute):
        stream = getattr(self, attribute)
        setattr(self, attribute, None)
        if stream is not None:
            try:
                stream.close()
            except DEVICES_ERRORS:
                pass
        if attribute == "output_stream":
            wipe(self.output_tail)
            self.output_tail.clear()

    def close(self):
        self.close_stream("input_stream")
        self.close_stream("output_stream")
        self.discard_capture()
        self.reset_playback()
        self.activity.clear()
