"""
Generate the extension icons.

Chrome wants PNGs, and checking binaries into a repo makes them impossible to
review or tweak. So the icons are described here and written out instead:
run `python extension/icons/make_icons.py` after changing anything.

Pure standard library on purpose -- zlib and struct are all a PNG needs, and
the alternative would be adding Pillow to the project for three small files.

The mark is a filled disc in the site's accent blue with a ring cut out of it:
a watch face, near enough, and legible at 16 pixels where anything more
detailed turns to mud. Sampling is 4x4 per pixel so the curves don't alias.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

SIZES = (16, 48, 128)

ACCENT = (47, 109, 246)
WHITE = (255, 255, 255)

# As fractions of the icon's width, so every size is the same drawing.
DISC = 0.48
RING_OUTER = 0.34
RING_INNER = 0.24

SAMPLES = 4


def _coverage(cx: float, cy: float, size: int) -> tuple[float, float]:
    """How much of one pixel is inside the disc, and inside the ring.

    Supersampling rather than real anti-aliasing: at these sizes the cost is
    nothing and the result is indistinguishable.
    """
    disc_hits = ring_hits = 0
    step = 1.0 / SAMPLES
    for sy in range(SAMPLES):
        for sx in range(SAMPLES):
            x = (cx + (sx + 0.5) * step) / size - 0.5
            y = (cy + (sy + 0.5) * step) / size - 0.5
            distance = (x * x + y * y) ** 0.5
            if distance <= DISC:
                disc_hits += 1
            if RING_INNER <= distance <= RING_OUTER:
                ring_hits += 1
    total = SAMPLES * SAMPLES
    return disc_hits / total, ring_hits / total


def _pixels(size: int) -> bytearray:
    """Raw RGBA scanlines, each prefixed with PNG's filter-type byte."""
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            disc, ring = _coverage(x, y, size)
            if disc <= 0:
                raw += bytes((0, 0, 0, 0))
                continue
            # The ring is white over blue, so blend towards white by how much
            # of this pixel the ring covers.
            blend = min(ring, disc)
            colour = tuple(
                round(ACCENT[i] * (1 - blend) + WHITE[i] * blend) for i in range(3)
            )
            raw += bytes((*colour, round(255 * disc)))
    return raw


def _chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(
        ">I", zlib.crc32(body) & 0xFFFFFFFF)


def write_png(path: Path, size: int) -> None:
    header = struct.pack(">2I5B", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    data = zlib.compress(bytes(_pixels(size)), 9)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", data)
        + _chunk(b"IEND", b"")
    )


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    for size in SIZES:
        target = here / f"icon{size}.png"
        write_png(target, size)
        print(f"wrote {target.name} ({target.stat().st_size} bytes)")
