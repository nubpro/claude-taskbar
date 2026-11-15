"""Draw the Claude Taskbar icon: a dark tile with three session rows (working, done, idle)
sitting on a taskbar strip. Writes docs/icon.png (512 px) and claude_taskbar.ico.

    python tools/make_icon.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
BG_TOP = (44, 42, 39)
BG_BOTTOM = (28, 27, 26)
BAR = (58, 56, 52)
ORANGE = (217, 119, 87)
GREEN = (111, 191, 115)
GREY = (122, 119, 111)
TEXT = (235, 233, 228)
TEXT_DIM = (150, 147, 140)


def draw(size: int, simple: bool = False) -> Image.Image:
    """Drawn 8x larger and scaled down for smooth edges. `simple` drops the text lines for tiny sizes."""
    k = 8
    n = size * k
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))

    # rounded tile with a soft vertical gradient
    grad = Image.new("RGBA", (n, n))
    gd = ImageDraw.Draw(grad)
    for y in range(n):
        t = y / (n - 1)
        gd.line([(0, y), (n, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM)) + (255,))
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, n - 1, n - 1), radius=n * 0.22, fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)

    u = n / 100  # draw in a 100-unit space
    # taskbar strip along the bottom, clipped by the tile's rounded corners
    strip = Image.new("L", (n, n), 0)
    ImageDraw.Draw(strip).rectangle((0, 80 * u, n, n), fill=255)
    strip = Image.composite(strip, Image.new("L", (n, n), 0), mask)
    img.paste(Image.new("RGBA", (n, n), BAR + (255,)), (0, 0), strip)
    d.line([(0, 80 * u), (n, 80 * u)], fill=(72, 70, 65, 255), width=max(1, round(u * 0.8)))

    rows = [(ORANGE, TEXT, 46), (GREEN, TEXT, 38), (GREY, TEXT_DIM, 30)]
    if simple:
        # tiny sizes: three big dots only, so they stay readable at 16 px
        for i, (dot, _, _) in enumerate(rows):
            cy = (22 + i * 22) * u
            r = 9 * u
            d.ellipse((50 * u - r, cy - r, 50 * u + r, cy + r), fill=dot)
    else:
        # the "current session" highlight behind the first row, as in the widget
        d.rounded_rectangle((10 * u, 13 * u, 90 * u, 31 * u), radius=5 * u, fill=(64, 60, 55, 255))
        for i, (dot, line, length) in enumerate(rows):
            cy = (22 + i * 20) * u
            r = 6 * u
            d.ellipse((20 * u - r, cy - r, 20 * u + r, cy + r), fill=dot)
            h = 3.6 * u
            d.rounded_rectangle((33 * u, cy - h, (33 + length) * u, cy + h), radius=h, fill=line)
    # faint edge so the tile doesn't vanish on dark backgrounds
    edge = max(1, round(n * 0.008))
    d.rounded_rectangle((edge // 2, edge // 2, n - 1 - edge // 2, n - 1 - edge // 2), radius=n * 0.22,
                        outline=(255, 255, 255, 22), width=edge)
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    (ROOT / "docs").mkdir(exist_ok=True)
    draw(512).save(ROOT / "docs" / "icon.png")
    sizes = [256, 128, 64, 48, 32, 24, 16]
    images = [draw(s, simple=s <= 24) for s in sizes]
    images[0].save(ROOT / "claude_taskbar.ico", sizes=[(s, s) for s in sizes], append_images=images[1:])
    print("docs/icon.png and claude_taskbar.ico written")
