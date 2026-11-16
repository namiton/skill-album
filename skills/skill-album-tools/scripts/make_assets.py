"""同梱素材（革、ラメ、効果音）を標準ライブラリだけで作る。素材のライセンスの心配がない。"""
import math
import os
import random
import struct
import sys
import wave
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "web", "assets")


def png(path, size, pixel):
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            rows.extend(pixel(x, y))

    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

    head = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", head) + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


def leather(rng):
    size = 192
    field = [[rng.random() for _ in range(size)] for _ in range(size)]

    def pixel(x, y):
        value = sum(field[(y + dy) % size][(x + dx) % size] for dx in (-1, 0, 1) for dy in (-1, 0, 1)) / 9
        alpha = int(abs(value - 0.5) * 300)
        shade = 255 if value > 0.5 else 0
        return (shade, shade, shade, min(alpha, 48))

    return size, pixel


def glitter(rng):
    size = 256
    points = {}
    for _ in range(900):
        points[(rng.randrange(size), rng.randrange(size))] = rng.randrange(120, 255)

    def pixel(x, y):
        level = points.get((x, y), 0)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            near = points.get(((x + dx) % size, (y + dy) % size), 0)
            level = max(level, near // 3)
        return (255, 255, 255, level)

    return size, pixel


def sound(path, seconds, sample):
    rate = 22050
    frames = bytearray()
    for index in range(int(seconds * rate)):
        value = max(-1.0, min(1.0, sample(index / rate, seconds)))
        frames.extend(struct.pack("<h", int(value * 32000)))
    with wave.open(path, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(bytes(frames))


def main():
    os.makedirs(OUT, exist_ok=True)
    rng = random.Random(20261104)
    size, pixel = leather(rng)
    png(os.path.join(OUT, "leather.png"), size, pixel)
    size, pixel = glitter(rng)
    png(os.path.join(OUT, "glitter.png"), size, pixel)

    def flip(t, total):
        return rng.uniform(-1, 1) * 0.35 * math.sin(math.pi * t / total) ** 2

    def slide(t, total):
        return rng.uniform(-1, 1) * 0.22 * (1 - t / total) * min(1, t * 40)

    def pop(t, total):
        return 0.4 * math.sin(2 * math.pi * (520 + 500 * t / total) * t) * (1 - t / total) ** 2

    sound(os.path.join(OUT, "flip.wav"), 0.22, flip)
    sound(os.path.join(OUT, "slide.wav"), 0.26, slide)
    sound(os.path.join(OUT, "pop.wav"), 0.14, pop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
