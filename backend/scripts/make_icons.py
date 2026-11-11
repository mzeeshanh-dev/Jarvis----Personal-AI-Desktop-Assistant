"""Render the Jarvis emblem to resources/icons (icon.png, icon.ico, tray.png).

Same geometry as frontend/src/renderer/components/Emblem.tsx. Dev-only (Pillow).
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parents[2] / "resources" / "icons"
S = 1024
ACCENT = (114, 207, 152)
BG = (11, 13, 12)


def px(v: float) -> float:
    return v * S / 100


def render(with_bg: bool = True) -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = S / 2
    if with_bg:
        d.ellipse([c - px(49), c - px(49), c + px(49), c + px(49)], fill=BG + (255,))
    r = px(46)
    d.ellipse([c - r, c - r, c + r, c + r], outline=ACCENT + (110,), width=int(px(1.8)))
    # segmented coil
    r, w = px(34), px(7)
    for i in range(10):
        a0 = 36 * i - 90 + 4
        d.arc([c - r - w / 2, c - r - w / 2, c + r + w / 2, c + r + w / 2], a0, a0 + 28, fill=ACCENT + (225,), width=int(w))
    r = px(24)
    d.ellipse([c - r, c - r, c + r, c + r], fill=BG + (255,), outline=ACCENT + (160,), width=int(px(1.8)))
    # glowing core
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    for k in range(40, 0, -1):
        rr = px(20) * k / 40
        t = 1 - k / 40
        col = tuple(int(ACCENT[j] + (242 - ACCENT[j]) * t) for j in range(3))
        g.ellipse([c - rr, c - rr, c + rr, c + rr], fill=col + (int(90 + 165 * t),))
    glow = glow.filter(ImageFilter.GaussianBlur(px(1.2)))
    img.alpha_composite(glow)
    rr = px(5.5)
    d = ImageDraw.Draw(img)
    d.ellipse([c - rr, c - rr, c + rr, c + rr], fill=(242, 255, 246, 255))
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    big = render()
    big.resize((512, 512), Image.LANCZOS).save(OUT / "icon.png")
    big.resize((256, 256), Image.LANCZOS).save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    big.resize((32, 32), Image.LANCZOS).save(OUT / "tray.png")
    big.resize((64, 64), Image.LANCZOS).save(OUT / "tray@2x.png")
    print("icons written to", OUT)


if __name__ == "__main__":
    main()
