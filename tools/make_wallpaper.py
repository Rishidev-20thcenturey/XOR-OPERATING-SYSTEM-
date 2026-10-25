#!/usr/bin/env python3
"""Generate the XOR desktop wallpaper: 1920x1080 PNG, stdlib only.

Design: deep-slate vertical gradient with a subtle teal diagonal XOR
glyph band (two crossing strokes) - restrained, no text rendering.
Re-run to regenerate desktop/theme/wallpaper.png deterministically.
"""
import os
import struct
import zlib

WIDTH, HEIGHT = 1920, 1080
TOP = (16, 24, 38)       # deep slate
BOTTOM = (10, 14, 22)    # darker slate
ACCENT = (32, 178, 170)  # light sea-green teal


def pixel(x, y):
    t = y / HEIGHT
    r = int(TOP[0] + (BOTTOM[0] - TOP[0]) * t)
    g = int(TOP[1] + (BOTTOM[1] - TOP[1]) * t)
    b = int(TOP[2] + (BOTTOM[2] - TOP[2]) * t)
    # Subtle X glyph centred right-of-centre: distance to two diagonals.
    cx, cy, arm = 1320, HEIGHT // 2, 260
    dx, dy = x - cx, y - cy
    if abs(dx) <= arm and abs(dy) <= arm:
        d1 = abs(dy - dx) / 1.4142
        d2 = abs(dy + dx) / 1.4142
        d = min(d1, d2)
        if d < 26:  # stroke with soft edge
            strength = max(0.0, 1.0 - d / 26.0) * 0.35
            r = int(r + (ACCENT[0] - r) * strength)
            g = int(g + (ACCENT[1] - g) * strength)
            b = int(b + (ACCENT[2] - b) * strength)
    return r, g, b


def write_png(path):
    raw = bytearray()
    for y in range(HEIGHT):
        raw.append(0)  # filter: none
        for x in range(WIDTH):
            raw.extend(pixel(x, y))
    def chunk(tag, data):
        out = struct.pack(">I", len(data)) + tag + data
        return out + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(png)
    print(f"wrote {path} ({len(png)} bytes)")


if __name__ == "__main__":
    target = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "desktop", "theme", "wallpaper.png")
    write_png(target)
