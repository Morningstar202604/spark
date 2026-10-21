"""Regenerate raster brand assets (logo.ico / png) from the vector mark.

The mark is a four-point spark star identical to ``brand/mark.svg``. It is
sampled here from the same cubic-bezier segments so the icon and the SVG never
drift apart — editing one without the other is how brand assets rot.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]

# brand/mark.svg path (64x64 viewBox): a symmetric four-point spark star.
# Each arm is one cubic: tip -> control -> control -> centre. Scaling this by
# 0.74 matches the on-screen BrandMark inset.
SEGMENTS = [
    ((32.0, 4.0), (33.9, 21.2), (42.8, 30.1), (60.0, 32.0)),
    ((60.0, 32.0), (42.8, 33.9), (33.9, 42.8), (32.0, 60.0)),
    ((32.0, 60.0), (30.1, 42.8), (21.2, 33.9), (4.0, 32.0)),
    ((4.0, 32.0), (21.2, 30.1), (30.1, 21.2), (32.0, 4.0)),
]


def _cubic(p0, p1, p2, p3, steps: int = 48) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for i in range(steps):
        t = i / steps
        mt = 1 - t
        x = mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0]
        y = mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1]
        out.append((x, y))
    return out


def star_points(scale: float = 1.0, dx: float = 0.0, dy: float = 0.0) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for seg in SEGMENTS:
        pts.extend(_cubic(*seg))
    return [(x * scale + dx, y * scale + dy) for x, y in pts]


def render(size: int) -> Image.Image:
    ss = 4  # supersample for smooth edges
    big = size * ss
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Rounded-square gradient background (top-left light → bottom-right deep).
    grad = Image.new("RGB", (big, big))
    gd = ImageDraw.Draw(grad)
    top = (255, 157, 79)
    bottom = (220, 77, 10)
    for y in range(big):
        t = y / max(1, big - 1)
        gd.line([(0, y), (big, y)], fill=tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, big - 1, big - 1], radius=int(big * 0.25), fill=255)
    img.paste(grad, (0, 0), mask)

    # Spark star, slightly inset so it reads at 16px.
    scale = big / 64.0 * 0.86
    off = (big - 64.0 * scale) / 2.0
    pts = star_points(scale, dx=off, dy=off)
    pts = [(x, y) for x, y in pts]
    d.polygon(pts, fill=(255, 255, 255, 255))
    return img.resize((size, size), Image.LANCZOS)


def main() -> int:
    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [render(s) for s in sizes]
    ico = assets / "logo.ico"
    frames[-1].save(ico, format="ICO", sizes=[(s, s) for s in sizes])
    frames[-1].save(assets / "logo.png")
    frames[5].save(ROOT / "brand" / "mark-128.png")
    print(f"wrote {ico} ({ico.stat().st_size} bytes), assets/logo.png, brand/mark-128.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
