"""Stateful DSP for the 20 ms voice processing pipeline."""

import math

import numpy as np
from scipy.signal import butter, correlate, iirpeak, lfilter, sosfilt


RATE = 16000
SAMPLES = 320
LEVEL_FLOOR = -90.0
THRESHOLD = -52


def dbfs(level):
    return max(LEVEL_FLOOR, 20 * math.log10(max(1, level) / 32768))


class Filter:
    def __init__(self, band, kind):
        self.sos = butter(3, band, btype=kind, fs=RATE, output="sos")
        self.state = np.zeros((len(self.sos), 2))

    def process(self, samples):
        result, self.state = sosfilt(self.sos, samples, zi=self.state)
        return result

    def clear(self):
        self.state.fill(0)


class Delay:
    def __init__(self, seconds):
        self.history = np.zeros(round(RATE * seconds) + SAMPLES + 2)
        self.offsets = np.arange(SAMPLES)
        self.cursor = 0

    def write(self, samples):
        self.indices = (self.cursor + self.offsets) % self.history.size
        self.history[self.indices] = samples
        self.cursor = (self.cursor + SAMPLES) % self.history.size

    def tap(self, seconds):
        index = self.indices - RATE * seconds
        lower = np.floor(index).astype(np.intp)
        weight = index - lower
        return ((1 - weight) * self.history[lower % self.history.size]
                + weight * self.history[(lower + 1) % self.history.size])

    def clear(self):
        self.history.fill(0)
        self.cursor = 0


class ExtendedEffect:
    def __init__(self, style):
        self.style = style
        self.phase = 0
        self.time = np.arange(SAMPLES) / RATE
        bands = {
            "horn": ((680, 2150), "bandpass"), "telephone": ((400, 2800), "bandpass"),
            "hall": (3400, "lowpass"), "water": (830, "lowpass"),
            "cyborg": ((330, 5300), "bandpass"), "whisper": ((1050, 6600), "bandpass"),
        }
        self.filter = Filter(*bands[style]) if style in bands else None
        delays = {"cave": 1.02, "hall": .53, "water": .023, "chorus": .043, "cyborg": .011}
        self.delay = Delay(delays[style]) if style in delays else None
        self.rng = np.random.default_rng()
        if style == "horn":
            self.peak = iirpeak(1260, 3.5, fs=RATE)
        if style == "whisper":
            self.breath = Filter((1200, 6200), "bandpass")
        self.state = np.zeros(2)
        self.envelope = np.zeros(1)

    def process(self, samples):
        method = getattr(self, self.style)
        time = self.time + self.phase
        result = method(samples, time)
        self.phase = (self.phase + SAMPLES / RATE) % 100
        return result

    def horn(self, samples, _):
        band = self.filter.process(samples)
        resonance, self.state = lfilter(*self.peak, band, zi=self.state)
        return .74 * np.tanh(5.2 * (band + .9 * resonance))

    def telephone(self, samples, _):
        level = min(.012, np.sqrt(np.mean(samples * samples)) * .16)
        crackle = (self.rng.random(SAMPLES) < .026) * self.rng.standard_normal(SAMPLES) * level
        return .87 * np.tanh(1.85 * self.filter.process(samples + crackle))

    def cave(self, samples, _):
        self.delay.write(samples)
        wet = sum(gain * self.delay.tap(offset) for offset, gain in (
            (.17, .43), (.34, .27), (.51, .16), (.68, .085), (.85, .05), (1.02, .026),
        ))
        return .79 * samples + wet

    def hall(self, samples, _):
        self.delay.write(samples)
        wet = sum(gain * self.delay.tap(offset) for offset, gain in (
            (.041, .28), (.057, -.16), (.071, .18), (.096, .15), (.117, -.09), (.149, .13),
            (.184, .11), (.223, -.08), (.279, .07), (.334, .05), (.41, -.04), (.53, .025),
        ))
        return .80 * samples + self.filter.process(wet)

    def water(self, samples, time):
        self.delay.write(self.filter.process(samples))
        return (1.16 + .19 * np.sin(2 * np.pi * 1.1 * time)) * self.delay.tap(
            .012 + .0023 * np.sin(2 * np.pi * 1.7 * time)
        )

    def chorus(self, samples, time):
        self.delay.write(samples)
        first = self.delay.tap(.025 + .004 * np.sin(2 * np.pi * .7 * time))
        second = self.delay.tap(.030 - .004 * np.sin(2 * np.pi * 1.1 * time))
        return .74 * samples + .27 * first + .23 * second

    def cyborg(self, samples, time):
        band = self.filter.process(samples)
        self.delay.write(band)
        delayed = self.delay.tap(.0052 + .0037 * np.sin(2 * np.pi * .8 * time))
        return np.tanh(1.2 * (.85 * band - .62 * delayed + .36 * band * np.cos(2 * np.pi * 83 * time)))

    @staticmethod
    def retro(samples, _):
        held = np.repeat(samples[::4], 4)
        return np.rint(np.clip(held * 1.55, -1, 127 / 128) * 128) / 128

    def whisper(self, samples, _):
        band = self.filter.process(samples)
        envelope, self.envelope = lfilter([.024], [1, -.976], np.abs(samples), zi=self.envelope)
        return .60 * band + 1.15 * envelope * self.breath.process(self.rng.standard_normal(SAMPLES))

    def clear(self):
        for processor in (self.filter, self.delay, getattr(self, "breath", None)):
            if processor is not None:
                processor.clear()
        self.state.fill(0)
        self.envelope.fill(0)
        self.phase = 0


class EchoControl:
    """Estimate delay and attenuation of the dominant loudspeaker reflection."""

    LOOKBACK = SAMPLES * 12

    def __init__(self):
        self.reference = np.zeros(self.LOOKBACK)
        self.delay = None
        self.gain = 0.0

    def add_reference(self, samples):
        self.reference[:-SAMPLES] = self.reference[SAMPLES:]
        self.reference[-SAMPLES:] = samples

    def process(self, samples):
        power = float(np.dot(samples, samples))
        if power <= 320 * 35 ** 2:
            return samples
        cumulative = np.concatenate(([0.0], np.cumsum(np.square(self.reference))))
        energy = cumulative[SAMPLES:] - cumulative[:-SAMPLES]
        if energy.max() < 320 * 65 ** 2:
            return samples
        correlation = correlate(self.reference, samples, mode="valid", method="fft")
        score = np.abs(correlation) / np.sqrt(power * np.maximum(energy, 320 * 65 ** 2))
        delay = int(np.argmax(score))
        if score[delay] >= .73:
            measured = float(np.clip(correlation[delay] / max(1, energy[delay]), -1.5, 1.5))
            if self.delay is None or abs(delay - self.delay) > 24:
                self.delay, self.gain = delay, measured * .65
            else:
                self.delay = delay
                self.gain = .7 * self.gain + .3 * measured
        if self.delay is not None and score[self.delay] > .33:
            return samples - self.gain * self.reference[self.delay:self.delay + SAMPLES]
        return samples

    def clear(self):
        self.reference.fill(0)
        self.delay = None
        self.gain = 0.0


class AutoGain:
    def __init__(self):
        self.gain = 1.0

    def process(self, samples, level):
        target = float(np.clip(3000 / max(level, 100), .4, 6.0))
        mix = .22 if target < self.gain else .055
        self.gain += mix * (target - self.gain)
        return np.clip(samples * self.gain, -32768, 32767).astype(np.int16)
