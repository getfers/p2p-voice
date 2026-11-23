"""Захват кадров для VideoStreamTrack, кодек VP8 и сборка кадров без повторной передачи."""

import asyncio
import struct
from fractions import Fraction
from threading import Lock

import av
from aiortc import VideoStreamTrack
from aiortc.mediastreams import MediaStreamError


QUALITY = (540, 720, 1080)
FPS = (15, 24, 30)
MAX_WIDTH, MAX_HEIGHT = 1920, 1080
CHUNK = 10_240
MAX_CHUNKS = 8
VIDEO_HEADER = struct.Struct("!IIHH")
STREAM_ID = struct.Struct("!I")


class LatestImage:
    """Один снимок между потоками Qt и кодека. Старый снимок можно пропустить."""

    def __init__(self):
        self.lock = Lock()
        self.image = None

    def put(self, image):
        with self.lock:
            self.image = image

    def take(self):
        with self.lock:
            image, self.image = self.image, None
            return image


class ScreenTrack(VideoStreamTrack):
    def __init__(self, images, fps):
        super().__init__()
        self.images = images
        self.fps = fps
        self.sequence = 0

    async def recv(self):
        while self.readyState == "live":
            image = self.images.take()
            if image is not None:
                frame = av.VideoFrame(image.width(), image.height(), "bgra")
                plane = frame.planes[0]
                pixels = image.constBits().asstring(image.sizeInBytes())
                stride = image.bytesPerLine()
                if plane.line_size != stride:
                    padded = bytearray(plane.buffer_size)
                    for row in range(image.height()):
                        offset = row * plane.line_size
                        padded[offset:offset + stride] = pixels[row * stride:(row + 1) * stride]
                    pixels = padded
                plane.update(pixels)
                frame.pts = self.sequence
                frame.time_base = Fraction(1, self.fps)
                self.sequence += 1
                return frame
            await asyncio.sleep(.008)
        raise MediaStreamError


class ScreenEncoder:
    def __init__(self, fps):
        self.fps = fps
        self.codec = None

    def encode(self, frame):
        if self.codec is None or (self.codec.width, self.codec.height) != (frame.width, frame.height):
            codec = av.CodecContext.create("libvpx", "w")
            codec.width, codec.height = frame.width, frame.height
            codec.pix_fmt = "yuv420p"
            codec.time_base = frame.time_base
            codec.framerate = Fraction(self.fps)
            codec.bit_rate = min(2_500_000, max(450_000, frame.width * frame.height * 2))
            codec.gop_size = 1
            codec.thread_count = 2
            codec.options = {"deadline": "realtime", "cpu-used": "8", "lag-in-frames": "0"}
            self.codec = codec
        packets = self.codec.encode(frame.reformat(format="yuv420p"))
        if packets and packets[0].is_keyframe:
            data = bytes(packets[0])
            if len(data) <= CHUNK * MAX_CHUNKS:
                return data
        return None


def video_chunks(data, stream, sequence):
    count = (len(data) + CHUNK - 1) // CHUNK
    for index in range(count):
        offset = index * CHUNK
        yield VIDEO_HEADER.pack(stream, sequence, index, count) + data[offset:offset + CHUNK]


class ScreenReceiver:
    def __init__(self, stream, now):
        self.stream = stream
        self.last_frame = now
        self.visible = False
        self.sequence = -1
        self.count = 0
        self.parts = {}
        self.pending = None
        self.task = None
        self.decoder = av.CodecContext.create("vp8", "r")
        self.decoder.thread_count = 1

    def accept(self, data):
        if not VIDEO_HEADER.size < len(data) <= VIDEO_HEADER.size + CHUNK:
            return None
        stream, sequence, index, count = VIDEO_HEADER.unpack_from(data)
        if stream != self.stream or sequence < self.sequence or not 0 <= index < count <= MAX_CHUNKS:
            return None
        if sequence > self.sequence:
            self.sequence, self.count = sequence, count
            self.parts.clear()
        if self.count != count or index in self.parts:
            return None
        self.parts[index] = data[VIDEO_HEADER.size:]
        if len(self.parts) != count:
            return None
        frame = b"".join(self.parts[i] for i in range(count))
        self.parts.clear()
        self.count = 0
        if len(frame) < 10 or frame[0] & 1 or frame[3:6] != b"\x9d\x01\x2a":
            return None
        width, height = struct.unpack_from("<HH", frame, 6)
        if not 1 <= (width & 0x3fff) <= MAX_WIDTH or not 1 <= (height & 0x3fff) <= MAX_HEIGHT:
            return None
        return frame

    def decode(self, frame):
        frames = self.decoder.decode(av.Packet(frame))
        if frames:
            image = frames[0].to_ndarray(format="rgb24")
            return image.shape[1], image.shape[0], image.tobytes()
        return None
