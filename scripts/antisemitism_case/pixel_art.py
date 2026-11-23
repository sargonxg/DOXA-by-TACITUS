"""Draw original low-resolution illustrations for the DOXA evidence desk."""

from io import BytesIO

from PIL import Image, ImageDraw

INK = "#102b23"
FOREST = "#1b4234"
GREEN = "#32604b"
LEAF = "#557e5e"
MINT = "#a6c79c"
PAPER = "#f9eac1"
SHADE = "#dbbd7f"
GOLD = "#ecb94f"
AMBER = "#a26d35"


def scene(width, height):
    canvas = Image.new("RGB", (width, height), INK)
    pen = ImageDraw.Draw(canvas)
    pen.polygon([(2, 7), (7, 2), (width - 8, 2), (width - 3, 7), (width - 3, height - 8),
                 (width - 8, height - 3), (7, height - 3), (2, height - 8)], fill=GOLD)
    pen.polygon([(4, 8), (8, 4), (width - 9, 4), (width - 5, 8), (width - 5, height - 9),
                 (width - 9, height - 5), (8, height - 5), (4, height - 9)], fill=FOREST)
    for y in range(8, height - 7, 4):
        for x in range(8 + (y % 8) // 2, width - 7, 4):
            pen.point((x, y), fill=GREEN)
    for x, y in [(9, 9), (width - 10, 9), (9, height - 10), (width - 10, height - 10)]:
        pen.line([(x - 2, y), (x + 2, y)], fill=LEAF)
        pen.line([(x, y - 2), (x, y + 2)], fill=LEAF)
    return canvas, pen


def folio(pen, x, y):
    pen.rectangle((x + 2, y + 4, x + 33, y + 35), fill=INK)
    pen.polygon([(x, y + 2), (x + 23, y + 2), (x + 26, y + 5),
                 (x + 32, y + 2), (x + 35, y + 2), (x + 35, y + 32),
                 (x + 29, y + 32), (x + 26, y + 34), (x + 22, y + 32),
                 (x, y + 32)], fill=AMBER)
    pen.polygon([(x + 2, y), (x + 22, y), (x + 26, y + 3), (x + 30, y),
                 (x + 34, y), (x + 34, y + 29), (x + 29, y + 29), (x + 26, y + 31),
                 (x + 22, y + 29), (x + 2, y + 29)], fill=PAPER)
    pen.line([(x + 26, y + 4), (x + 26, y + 29)], fill=SHADE)
    pen.rectangle((x + 4, y + 12, x + 23, y + 17), fill=GOLD)
    for offset, length in [(6, 15), (9, 18), (14, 16), (21, 18), (24, 11)]:
        pen.line([(x + 5, y + offset), (x + length, y + offset)], fill=AMBER)
    for offset in [6, 10, 13, 20, 23]:
        pen.line([(x + 29, y + offset), (x + 32, y + offset)], fill=SHADE)


def bubble(pen, x, y, color):
    pen.polygon([(x, y + 3), (x + 3, y), (x + 25, y), (x + 28, y + 3),
                 (x + 28, y + 15), (x + 25, y + 18), (x + 13, y + 18),
                 (x + 7, y + 23), (x + 7, y + 18), (x + 3, y + 18), (x, y + 15)], fill=color)
    for col in [7, 15]:
        pen.polygon([(x + col, y + 6), (x + col + 4, y + 6), (x + col + 4, y + 10),
                     (x + col + 2, y + 13), (x + col, y + 13), (x + col + 2, y + 10),
                     (x + col, y + 10)], fill=FOREST)


def cards(pen, x, y):
    for index, accent in enumerate((GOLD, MINT, SHADE)):
        left = x + 17 * index
        pen.rectangle((left + 2, y + 3, left + 16, y + 25), fill=INK)
        pen.rectangle((left, y, left + 14, y + 22), fill=accent)
        pen.rectangle((left + 2, y + 4, left + 12, y + 19), fill=FOREST)
        pen.rectangle((left + 5, y + 1, left + 9, y + 3), fill=AMBER)
        for offset, width in [(8, 7), (11, 6), (14, 4)]:
            pen.line(((left + 4, y + offset), (left + width + 1, y + offset)), fill=accent)


def render():
    hero, pen = scene(120, 64)
    pen.polygon([(12, 49), (40, 58), (105, 49), (105, 42), (76, 32), (12, 40)], fill=INK)
    pen.polygon([(12, 40), (42, 49), (103, 40), (75, 30)], fill=GREEN)
    pen.line([(15, 49), (40, 57), (102, 48)], fill=LEAF)
    folio(pen, 16, 17)
    for x, y in [(52, 29), (56, 29), (60, 29), (64, 29), (67, 27), (67, 23), (71, 20)]:
        pen.rectangle((x, y, x + 1, y + 1), fill=GOLD)
    bubble(pen, 73, 10, MINT)
    cards(pen, 55, 35)

    source, pen = scene(74, 52)
    folio(pen, 14, 10)
    pen.line([(46, 29), (58, 41)], fill=INK, width=6)
    pen.line([(46, 29), (57, 40)], fill=AMBER, width=3)
    pen.polygon([(35, 15), (45, 15), (51, 21), (51, 30), (45, 36), (35, 36),
                 (29, 30), (29, 21)], fill=GOLD)
    pen.polygon([(36, 18), (44, 18), (48, 22), (48, 29), (44, 33), (36, 33),
                 (32, 29), (32, 22)], fill=GREEN)
    pen.line([(35, 24), (44, 24)], fill=PAPER)
    pen.line([(35, 27), (42, 27)], fill=MINT)

    voices, pen = scene(74, 52)
    bubble(pen, 12, 12, GOLD)
    bubble(pen, 33, 19, MINT)
    pen.line([(16, 40), (30, 40), (30, 34)], fill=GOLD)
    pen.line([(54, 41), (60, 41)], fill=MINT)

    comparison, pen = scene(74, 52)
    cards(pen, 13, 14)
    for x in [20, 37, 53]:
        pen.line([(x, 41), (x, 43)], fill=GOLD)
    pen.line([(20, 43), (53, 43)], fill=GOLD)

    graphics = {}
    for name, canvas in [("evidence-desk", hero), ("source", source), ("voice", voices), ("criterion", comparison)]:
        buffer = BytesIO()
        canvas.resize((canvas.width * 5, canvas.height * 5), Image.Resampling.NEAREST).save(buffer, format="PNG")
        graphics[name + ".png"] = buffer.getvalue()
    return graphics
