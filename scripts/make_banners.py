"""Render section banners from the welcome scene so every screen shares one visual style.

Run locally after changing the scene or the section list:
    python -m scripts.make_banners
Pillow is a dev-only dependency; the bot itself only sends the generated JPEGs.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "assets" / "welcome.jpg"
OUT = ROOT / "assets" / "banners"
SIZE = (1280, 720)
SCENE_OFFSET = 360
FEATHER = 240
BRAND = "DEXLO"

# key: (title, subtitle, hue shift in degrees from the blue scene, accent RGB)
SECTIONS: dict[str, tuple[str, str, int, tuple[int, int, int]]] = {
    "menu": ("DEXLO", "Игры и подарочные карты", 0, (90, 170, 255)),
    "steam": ("STEAM", "Игры и подарки Steam", 0, (102, 192, 244)),
    "cards": ("GIFT CARDS", "Подарочные карты сервисов", 60, (178, 120, 255)),
    "telegram": ("TELEGRAM", "Premium", -12, (60, 180, 240)),
    "topup": ("TOP-UP", "Пополнение игр", 105, (255, 90, 200)),
    "catalog": ("CATALOG", "Каталог предложений", 20, (120, 150, 255)),
    "search": ("SEARCH", "Поиск по каталогу", -30, (70, 220, 230)),
    "cart": ("CART", "Ваша корзина", -80, (90, 230, 150)),
    "checkout": ("CHECKOUT", "Оформление заказа", -100, (170, 235, 90)),
    "profile": ("PROFILE", "Личный кабинет", 150, (255, 170, 80)),
    "orders": ("ORDERS", "История заказов", 170, (255, 200, 90)),
    "support": ("SUPPORT", "Поддержка", -55, (60, 220, 190)),
    "docs": ("DOCS", "Документы и правила", 35, (160, 170, 210)),
}

FONT_DIR = Path("/System/Library/Fonts/Supplemental")
TITLE_FONT = FONT_DIR / "Arial Black.ttf"
TEXT_FONT = FONT_DIR / "Arial Bold.ttf"


def shift_hue(image: Image.Image, degrees: int) -> Image.Image:
    if not degrees:
        return image
    h, s, v = image.convert("HSV").split()
    offset = round(degrees / 360 * 255)
    h = h.point(lambda x: (x + offset) % 256)
    return Image.merge("HSV", (h, s, v)).convert("RGB")


def left_shade(size: tuple[int, int]) -> Image.Image:
    """Dark gradient on the left so the title stays readable over the scene."""
    width, height = size
    mask = Image.new("L", size)
    draw = ImageDraw.Draw(mask)
    for x in range(width):
        alpha = min(1.0, max(0.0, 1.25 - x / (width * 0.58))) ** 1.2
        draw.line([(x, 0), (x, height)], fill=round(240 * alpha))
    return mask


def fit_font(path: Path, text: str, max_width: int, start: int) -> ImageFont.FreeTypeFont:
    size = start
    while size > 40:
        font = ImageFont.truetype(str(path), size)
        if font.getlength(text) <= max_width:
            return font
        size -= 4
    return ImageFont.truetype(str(path), size)


def render(key: str, title: str, subtitle: str, hue: int, accent: tuple[int, int, int], number: int) -> Image.Image:
    source = shift_hue(Image.open(SOURCE).convert("RGB").resize(SIZE, Image.LANCZOS), hue)
    # Move the scene right so the left half is free for the title; the gap is
    # filled with a blurred copy so the edge never shows a hard seam.
    scene = source.resize((SIZE[0] * 2, SIZE[1] * 2)).filter(ImageFilter.GaussianBlur(40)).crop((0, SIZE[1] // 2, SIZE[0], SIZE[1] * 3 // 2))
    shifted = Image.new("RGB", SIZE)
    shifted.paste(source, (SCENE_OFFSET, 0))
    mask = Image.new("L", SIZE)
    draw = ImageDraw.Draw(mask)
    for x in range(SIZE[0]):
        weight = min(1.0, max(0.0, (x - SCENE_OFFSET) / FEATHER))
        draw.line([(x, 0), (x, SIZE[1])], fill=round(255 * weight))
    scene = Image.composite(shifted, scene, mask)
    dark = Image.new("RGB", SIZE, (6, 8, 16))
    image = Image.composite(dark, scene, left_shade(SIZE))

    draw = ImageDraw.Draw(image)
    margin = 72
    small = ImageFont.truetype(str(TEXT_FONT), 26)
    sub_font = ImageFont.truetype(str(TEXT_FONT), 40)
    title_font = fit_font(TITLE_FONT, title, 600, 120)

    draw.text((margin, 64), f"{BRAND} · DIGITAL STORE", font=small, fill=(200, 210, 230))
    draw.rectangle([margin, 112, margin + 64, 118], fill=accent)

    title_y = 300 - title_font.size // 2
    glow = Image.new("RGB", SIZE)
    ImageDraw.Draw(glow).text((margin, title_y), title, font=title_font, fill=accent)
    glow = glow.filter(ImageFilter.GaussianBlur(18))
    image = ImageChops.add(image, glow.point(lambda x: x // 2))
    draw = ImageDraw.Draw(image)
    draw.text((margin, title_y), title, font=title_font, fill=(245, 248, 255))

    draw.text((margin, title_y + title_font.size + 28), subtitle, font=sub_font, fill=accent)
    draw.text((margin, SIZE[1] - 64), f"№ {number:02d}", font=small, fill=(150, 160, 185))
    return image


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for number, (key, spec) in enumerate(SECTIONS.items()):
        path = OUT / f"{key}.jpg"
        render(key, *spec, number=number).save(path, "JPEG", quality=86, optimize=True, progressive=True)
        print(f"{path.relative_to(ROOT)}  {path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
