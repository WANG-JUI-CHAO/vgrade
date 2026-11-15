"""產生 assets/vgrade.ico（純 Python，不需要額外套件）。

圖案：藍色圓角方塊上的白色勾號。
"""
import struct
import zlib
from pathlib import Path

BG = (42, 120, 214)       # #2a78d6
FG = (255, 255, 255)


def _dist_seg(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    cx, cy = ax + t * dx, ay + t * dy
    return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5


def render(size: int, ss: int = 4) -> bytes:
    """回傳 RGBA 像素（4x 超取樣抗鋸齒）。"""
    n = size * ss
    r = 0.22 * n                       # 圓角半徑
    stroke = 0.11 * n
    pts = [(0.25 * n, 0.52 * n), (0.43 * n, 0.70 * n), (0.76 * n, 0.32 * n)]
    acc = [[0, 0, 0, 0] for _ in range(size * size)]
    for y in range(n):
        for x in range(n):
            px, py = x + 0.5, y + 0.5
            qx = max(r - px, 0, px - (n - r))
            qy = max(r - py, 0, py - (n - r))
            if qx * qx + qy * qy > r * r:
                continue
            d = min(_dist_seg(px, py, *pts[0], *pts[1]), _dist_seg(px, py, *pts[1], *pts[2]))
            col = FG if d <= stroke / 2 else BG
            a = acc[(y // ss) * size + x // ss]
            a[0] += col[0]
            a[1] += col[1]
            a[2] += col[2]
            a[3] += 255
    k = ss * ss
    out = bytearray()
    for a in acc:
        if a[3]:
            cov = a[3] / 255
            out += bytes((round(a[0] / cov), round(a[1] / cov), round(a[2] / cov), round(a[3] / k)))
        else:
            out += b"\0\0\0\0"
    return bytes(out)


def png(size: int, rgba: bytes) -> bytes:
    raw = b"".join(b"\0" + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main() -> None:
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [png(s, render(s, 4 if s <= 64 else 2)) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for s, data in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    out = Path(__file__).resolve().parent.parent / "assets" / "vgrade.ico"
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(header + entries + b"".join(images))
    (out.parent / "vgrade.png").write_bytes(images[-1])
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
