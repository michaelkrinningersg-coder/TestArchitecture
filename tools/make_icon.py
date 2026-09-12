"""Erzeugt das Anwendungssymbol als PNG und als Windows-ICO.

    python tools/make_icon.py

Ein Kolben auf dunklem Grund, der Inhalt in der Ampelfarbe „frei". Bewusst
flächig gezeichnet, damit die 16-Pixel-Fassung in der Taskleiste noch lesbar
bleibt.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
PNG = ROOT / "labcontrol" / "resources" / "labcontrol.png"
ICO = ROOT / "packaging" / "labcontrol.ico"

SIZE = 512
GROUND = "#14425f"
GLASS = "#f4f8fb"
LIQUID = "#2fa864"



def draw(size: int = SIZE) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    unit = size / 512

    pen.rounded_rectangle((0, 0, size - 1, size - 1), radius=96 * unit, fill=GROUND)

    # Erlenmeyerkolben: Hals, Schulter, Boden
    flask = [
        (196 * unit, 96 * unit), (316 * unit, 96 * unit),
        (316 * unit, 196 * unit), (420 * unit, 392 * unit),
        (404 * unit, 420 * unit), (108 * unit, 420 * unit),
        (92 * unit, 392 * unit), (196 * unit, 196 * unit),
    ]
    pen.polygon(flask, fill=GLASS)

    # Füllung bis knapp unter die Schulter
    level = 300 * unit
    liquid = [
        (140 * unit, level), (372 * unit, level),
        (420 * unit, 392 * unit), (404 * unit, 420 * unit),
        (108 * unit, 420 * unit), (92 * unit, 392 * unit),
    ]
    pen.polygon(liquid, fill=LIQUID)

    # Stopfen
    pen.rounded_rectangle(
        (184 * unit, 60 * unit, 328 * unit, 108 * unit),
        radius=16 * unit, fill=GLASS)
    return image


def main() -> int:
    PNG.parent.mkdir(parents=True, exist_ok=True)
    ICO.parent.mkdir(parents=True, exist_ok=True)
    master = draw()
    master.resize((256, 256), Image.LANCZOS).save(PNG)
    master.save(ICO, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                            (64, 64), (128, 128), (256, 256)])
    print(f"{PNG.relative_to(ROOT)} · {ICO.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
