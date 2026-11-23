"""Регрессии потоковых эффектов и локального маршрута прослушивания."""

import unittest
from unittest.mock import patch

import numpy as np

from audio import Audio, EFFECTS, RATE, Resampler, SAMPLES, VoiceEffect
from audio_dsp import EchoControl
from voice_codec import BITRATES, MIX_RATE, MIX_SAMPLES, OpusDecoder, OpusEncoder


def sine(block=0, frequency=440):
    times = (np.arange(SAMPLES) + block * SAMPLES) / RATE
    return np.rint(9000 * np.sin(2 * np.pi * frequency * times)).astype(np.int16)


class EffectsTest(unittest.TestCase):
    def test_every_effect_keeps_pcm_duration(self):
        self.assertEqual(len(EFFECTS), 16)
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

    def test_opus_bitrate_and_mixer_at_48khz(self):
        for bitrate in BITRATES:
            encoder, decoder = OpusEncoder(bitrate), OpusDecoder()
            for index in range(4):
                packet = encoder.encode(sine(index).tobytes(), index)
                output = decoder.decode(packet)
                self.assertEqual(len(packet), 5 + bitrate * 20 // 8)
            self.assertEqual(output.shape, (MIX_SAMPLES,))
            self.assertGreater(Audio.level(output), 3000)
            self.assertIsNone(decoder.decode(packet))

    def test_callbacks_accept_partial_frames_and_200_percent_saturates(self):
        audio = Audio(lambda *_: None)
        audio.local, audio.muted, audio.noise = b"a" * 32, False, False
        with patch("audio.sd.RawInputStream") as stream:
            audio.make_input(None, MIX_RATE, MIX_SAMPLES, 2)
        input_callback = stream.call_args.kwargs["callback"]
        data = np.full(MIX_SAMPLES * 2, 2500, dtype=np.int16).tobytes()
        halves = len(data) // 2
        sent = []
        input_callback(data[:halves], MIX_SAMPLES // 2, None, None)
        audio.capture(sent.append)
        self.assertFalse(sent)
        input_callback(data[halves:], MIX_SAMPLES // 2, None, None)
        audio.capture(sent.append)
        self.assertEqual(len(sent), 1)
        np.testing.assert_array_equal(np.frombuffer(sent[0], dtype="<i2"), 2500)
        with patch("audio.sd.RawOutputStream") as stream:
            audio.output_stream = audio.make_output(None, MIX_RATE, MIX_SAMPLES, 2)
        output_callback = stream.call_args.kwargs["callback"]
        public = b"b" * 32
        audio.peer_volume(public, 200)
        frame = np.full(SAMPLES, 20000, dtype=np.int16).tobytes()
        for _ in range(audio.buffer):
            audio.incoming(public, frame)
        audio.mix(audio.next_mix)
        output = bytearray(halves)
        for _ in range(2):
            output_callback(output, MIX_SAMPLES // 2, None, None)
            np.testing.assert_array_equal(np.frombuffer(output, dtype=np.int16), 32767)
        audio.close()

    def test_echo_filter_uses_actual_reference_and_preserves_unrelated_voice(self):
        echo = EchoControl()
        rng = np.random.default_rng(30)
        for _ in range(8):
            echo.add_reference(rng.normal(0, 2900, SAMPLES))
        reflection = .56 * echo.reference[-4 * SAMPLES:-3 * SAMPLES].copy()
        self.assertLess(Audio.level(echo.process(reflection)), Audio.level(reflection) * .45)
        local = rng.normal(0, 1900, SAMPLES)
        np.testing.assert_array_equal(echo.process(local), local)
