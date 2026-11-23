"""Negotiated 20 ms mono Opus and compatibility with legacy PCM peers."""

import struct
from fractions import Fraction

import av
import numpy as np

from audio_dsp import RATE, SAMPLES


MIX_RATE = 48000
MIX_SAMPLES = 960
PCM_BYTES = SAMPLES * 2
OPUS_LIMIT = 512
BITRATES = (32, 64, 96, 128)
OPUS_HEADER = struct.Struct("!BI")


def is_opus(packet):
    return OPUS_HEADER.size < len(packet) <= OPUS_LIMIT and packet[0] == 1


class OpusEncoder:
    def __init__(self, bitrate):
        self.codec = av.CodecContext.create("libopus", "w")
        self.codec.layout = "mono"
        self.codec.format = "s16"
        self.codec.sample_rate = RATE
        self.codec.bit_rate = bitrate * 1000
        self.codec.time_base = Fraction(1, RATE)
        self.codec.options = {"application": "voip", "frame_duration": "20", "vbr": "off"}
        self.codec.open()

    def encode(self, pcm, sequence):
        frame = av.AudioFrame(format="s16", layout="mono", samples=SAMPLES)
        frame.sample_rate = RATE
        frame.planes[0].update(pcm)
        packets = self.codec.encode(frame)
        if len(packets) != 1:
            return None
        packet = OPUS_HEADER.pack(1, sequence) + bytes(packets[0])
        return packet if is_opus(packet) else None


class OpusDecoder:
    def __init__(self):
        self.codec = av.CodecContext.create("opus", "r")
        # Raw RTP-style packets have no OpusHead, so tell FFmpeg the channels via extradata.
        self.codec.extradata = b"OpusHead\x01\x01\x00\x00\x80\xbb\x00\x00\x00\x00\x00"
        self.sequence = None

    def decode(self, packet):
        sequence = OPUS_HEADER.unpack_from(packet)[1]
        if self.sequence is not None and not 0 < (sequence - self.sequence) % (2 ** 32) < 2 ** 31:
            return None
        frames = self.codec.decode(av.Packet(packet[OPUS_HEADER.size:]))
        if len(frames) != 1:
            return None
        frame = frames[0]
        if frame.samples != MIX_SAMPLES or frame.sample_rate != MIX_RATE or len(frame.layout.channels) != 1:
            return None
        samples = frame.to_ndarray().reshape(-1)
        self.sequence = sequence
        return np.clip(np.rint(samples * 32768), -32768, 32767).astype(np.int16)
