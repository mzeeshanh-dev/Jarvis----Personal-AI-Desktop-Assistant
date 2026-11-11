"""Build Jarvis brand assets from resources/brand/jarvis.jpg (dev-only, Pillow).

Outputs
- frontend/src/renderer/public/brand/logo.png    circular HUD logo (512 px)
- frontend/src/renderer/public/brand/reactor.png circular arc-reactor mark (256 px, small sizes)
- frontend/src/renderer/public/brand/hero.jpg    cleaned, vignetted background
- resources/icons/icon.png / icon.ico / tray.png / tray@2x.png
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "resources" / "brand" / "jarvis.jpg"
PUBLIC = ROOT / "frontend" / "src" / "renderer" / "public" / "brand"
ICONS = ROOT / "resources" / "icons"

HUD = (300, 641, 121)       # center x, center y, radius (source pixels)
REACTOR = (805, 652, 80)
WATERMARK = (1320, 850, 1600, 900)
RING = (79, 209, 245)        # cyan accent sampled from the image glow


def circle_crop(img: Image.Image, cx: int, cy: int, r: int, size: int, *, ring: bool = True,
                boost: float = 1.25) -> Image.Image:
    sq = img.crop((cx - r, cy - r, cx + r, cy + r)).resize((size, size), Image.LANCZOS)
    sq = ImageEnhance.Contrast(sq).enhance(boost)
    sq = ImageEnhance.Color(sq).enhance(1.15)
    # Source is a ~2x-upscaled JPEG: smooth block artifacts, then sharpen gently.
    sq = sq.filter(ImageFilter.GaussianBlur(0.8)).filter(ImageFilter.UnsharpMask(radius=2, percent=45, threshold=3))
    # Supersampled circular mask for a smooth edge.
    big = size * 4
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, big - 1, big - 1), fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(sq.convert("RGBA"), (0, 0), mask)
    if ring:
        ov = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        w = max(4, big // 70)
        ImageDraw.Draw(ov).ellipse((w // 2, w // 2, big - w // 2 - 1, big - w // 2 - 1), outline=RING + (230,), width=w)
        out.alpha_composite(ov.resize((size, size), Image.LANCZOS))
    return out


def hero(img: Image.Image) -> Image.Image:
    im = img.copy()
    # Remove the wallpaper-site watermark by filling with the surrounding black.
    ImageDraw.Draw(im).rectangle(WATERMARK, fill=(8, 10, 12))
    im = ImageEnhance.Brightness(im).enhance(0.9)
    w, h = im.size
    # Radial vignette so UI text stays readable over the artwork.
    v = Image.radial_gradient("L").resize((w, h), Image.LANCZOS)
    v = ImageChops.invert(v).point(lambda p: int(60 + p * 0.75))
    black = Image.new("RGB", (w, h), (4, 7, 10))
    return Image.composite(im, black, v)


def main() -> None:
    img = Image.open(SRC).convert("RGB")
    PUBLIC.mkdir(parents=True, exist_ok=True)
    ICONS.mkdir(parents=True, exist_ok=True)
    logo = circle_crop(img, *HUD, 512, boost=1.15)
    reactor = circle_crop(img, *REACTOR, 512, boost=1.2)
    logo.save(PUBLIC / "logo.png")
    reactor.resize((256, 256), Image.LANCZOS).save(PUBLIC / "reactor.png")
    hero(img).save(PUBLIC / "hero.jpg", quality=84, optimize=True, progressive=True)
    # The arc reactor is the app icon at every size (taskbar, tray, installer, notifications).
    reactor.save(ICONS / "icon.png")
    sizes = (256, 128, 64, 48, 32, 24, 16)
    frames = [reactor.resize((s, s), Image.LANCZOS) for s in sizes]
    frames[0].save(ICONS / "icon.ico", format="ICO", sizes=[(s, s) for s in sizes], append_images=frames[1:])
    reactor.resize((32, 32), Image.LANCZOS).save(ICONS / "tray.png")
    reactor.resize((64, 64), Image.LANCZOS).save(ICONS / "tray@2x.png")
    print("brand assets written")


if __name__ == "__main__":
    main()
