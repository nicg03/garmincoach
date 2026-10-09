"""Generate reviewable, dependency-free source artwork for Capacitor assets."""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

ACCENT = (33, 84, 255, 255)
DARK = (17, 17, 17, 255)
WHITE = (255, 255, 255, 255)


def _chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return (struct.pack(">I", len(payload)) + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))


def write_png(path: Path, width: int, height: int, pixel) -> None:
    compressor = zlib.compressobj(9)
    parts = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            row.extend(pixel(x, y))
        parts.append(compressor.compress(bytes(row)))
    parts.append(compressor.flush())
    header = struct.pack(">2I5B", width, height, 8, 6, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", b"".join(parts)) + _chunk(b"IEND", b""))


def mark(size: int, background, radius: float):
    centre = (size - 1) / 2
    outer = size * radius
    ring_outer = size * 0.27
    ring_inner = size * 0.18

    def pixel(x: int, y: int):
        distance = ((x - centre) ** 2 + (y - centre) ** 2) ** 0.5
        if distance <= outer:
            return WHITE if ring_inner <= distance <= ring_outer else ACCENT
        return background
    return pixel


if __name__ == "__main__":
    target = Path(__file__).resolve().parent.parent / "resources"
    target.mkdir(parents=True, exist_ok=True)
    write_png(target / "icon-only.png", 1024, 1024, mark(1024, ACCENT, 0.49))

    splash_size = 2732
    centre = (splash_size - 1) / 2
    icon = mark(512, DARK, 0.48)

    def splash(x: int, y: int):
        local_x = int(x - centre + 256)
        local_y = int(y - centre + 256)
        if 0 <= local_x < 512 and 0 <= local_y < 512:
            return icon(local_x, local_y)
        return DARK

    write_png(target / "splash.png", splash_size, splash_size, splash)
    print(f"Wrote mobile source assets to {target}")
