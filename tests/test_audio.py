"""Регрессии потоковых эффектов и локального маршрута прослушивания."""

import unittest

import numpy as np

from audio import Audio, EFFECTS, RATE, Resampler, SAMPLES, VoiceEffect


def sine(block=0, frequency=440):
    times = (np.arange(SAMPLES) + block * SAMPLES) / RATE
    return np.rint(9000 * np.sin(2 * np.pi * frequency * times)).astype(np.int16)


class EffectsTest(unittest.TestCase):
    def test_every_effect_keeps_pcm_duration(self):
        expected = sine()
        for name, _ in EFFECTS:
            with self.subTest(name=name):
                processor = VoiceEffect(name)
                result = processor.process(expected)
                self.assertEqual(result.shape, (SAMPLES,))
                self.assertEqual(result.dtype, np.int16)
                self.assertEqual(np.array_equal(result, expected), name == "normal")
                processor.clear()
                self.assertFalse(np.any(processor.process(np.zeros_like(expected))))

    def test_pitch_moves_in_both_directions(self):
        def spectral_peak(name):
            effect = VoiceEffect(name)
            data = np.concatenate([effect.process(sine(index)) for index in range(34)][4:])
            return float(np.argmax(np.abs(np.fft.rfft(data * np.hanning(data.size))))) * RATE / data.size

        self.assertLess(spectral_peak("deep"), 390)
        self.assertGreater(spectral_peak("high"), 500)

    def test_preview_uses_same_processed_pcm_without_sending_when_muted(self):
        audio = Audio(lambda *_: None)
        audio.input_resampler = Resampler(SAMPLES, SAMPLES)
        audio.local = b"x" * 32
        audio.loopback = True
        audio.noise = False
        audio.effect = VoiceEffect("robot")
        frames = []
        audio.input_queue.append((audio.capture_epoch, bytearray(sine().tobytes())))
        audio.capture(frames.append)
        self.assertFalse(frames)
        local_only = audio.monitor_queue[0].copy()
        self.assertFalse(np.array_equal(local_only, sine()))
        audio.muted = False
        audio.input_queue.append((audio.capture_epoch, bytearray(sine(1).tobytes())))
        audio.capture(frames.append)
        self.assertEqual(len(frames), 1)
        np.testing.assert_array_equal(audio.monitor_queue[-1], np.frombuffer(frames[0], dtype="<i2"))
        audio.close()
