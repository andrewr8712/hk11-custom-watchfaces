"""Offline HK11 PRO-A watch-face reader and digital-face builder."""
import argparse
import json
from pathlib import Path
from struct import Struct, pack, pack_into, unpack_from

from PIL import Image, ImageDraw, ImageFont

MAGIC = b"WF\x01\x00\x00\x80"
HEADER_SIZE = 52
RECORD = Struct("<IBBHHHII")
BITMAP = Struct("<2sHHHHH4s")
CANVAS = (320, 385)


def unpack_face(data):
    if len(data) < HEADER_SIZE or not data.startswith(MAGIC):
        raise ValueError("Unsupported WF header")
    count, size = unpack_from("<HI", data, 46)
    end = HEADER_SIZE + count * RECORD.size
    if not count or size != len(data) or end >= size:
        raise ValueError("Invalid WF size or record count")
    records = [RECORD.unpack_from(data, i) for i in range(HEADER_SIZE, end, RECORD.size)]
    offsets = sorted({record[0] for record in records})
    if offsets[0] != end or offsets[-1] >= size:
        raise ValueError("Invalid asset offsets")
    assets = {a: data[a:b] for a, b in zip(offsets, offsets[1:] + [size])}
    return data[:HEADER_SIZE], records, assets


def pack_face(header, records, assets):
    header = bytearray(header)
    cursor = HEADER_SIZE + len(records) * RECORD.size
    offsets = {}
    for key, asset in assets.items():
        offsets[key] = cursor
        cursor += len(asset)
    pack_into("<HI", header, 46, len(records), cursor)
    table = b"".join(RECORD.pack(offsets[key], *fields) for key, *fields in records)
    return bytes(header) + table + b"".join(assets.values())


def decode_line(data, width):
    pixels = bytearray()
    i = alpha = red = green = blue = 0

    def color():
        return bytes(((red << 3) | (red >> 2), (green << 2) | (green >> 4),
                      (blue << 3) | (blue >> 2), alpha))

    while i < len(data):
        op = data[i]
        i += 1
        if op < 128:
            red = (red + (op >> 5) - 2) & 31
            green = (green + ((op >> 2) & 7) - 4) & 63
            blue = (blue + (op & 3) - 2) & 31
            pixels.extend(color())
            continue
        count = op & 63
        if not count:
            raise ValueError("Invalid pixel run")
        repeat = op < 192
        if repeat:
            alpha = data[i]
            i += 1
        for _ in range(1 if repeat else count):
            value, = unpack_from("<H", data, i)
            i += 2
            red, green, blue = value >> 11, (value >> 5) & 63, value & 31
            pixels.extend(color() * (count if repeat else 1))
    if len(pixels) != width * 4:
        raise ValueError(f"Expected {width} pixels, got {len(pixels) // 4}")
    return bytes(pixels)


def decode_asset(data):
    magic, version, width, height, codec, step, _ = BITMAP.unpack_from(data)
    if magic != b"PA" or version != 256 or not (0 < width <= 2048 and 0 < height <= 4096):
        raise ValueError("Unsupported PA bitmap")
    if codec not in (0x0100, 0x011E, 0x0200, 0x021E):
        raise ValueError(f"Unsupported PA codec {codec:#06x}")
    tiled = (codec & 0xFF) == 0x1E
    tiles_x = (width + 29) // 30
    count = tiles_x * ((height + 29) // 30) if tiled else height
    entry = Struct("<b" if codec >> 8 == 1 else "<h")
    start = BITMAP.size + (count + 1) * entry.size
    offsets = [i * step + entry.unpack_from(data, BITMAP.size + i * entry.size)[0]
               for i in range(count + 1)]
    if offsets[0] or start + offsets[-1] != len(data) or sorted(offsets) != offsets:
        raise ValueError("Invalid PA row table")
    rows = [decode_line(data[start+a:start+b], 31 * 31 if tiled else width)
            for a, b in zip(offsets, offsets[1:])]
    if not tiled:
        return Image.frombytes("RGBA", (width, height), b"".join(rows))
    image = Image.new("RGBA", (width, height))
    for i, row in enumerate(rows):
        tile = Image.frombytes("RGBA", (31, 31), row)
        image.paste(tile, (30 * (i % tiles_x), 30 * (i // tiles_x)))
    return image


def encode_asset(image):
    image = image.convert("RGBA")
    width, height = image.size
    stream = bytearray()
    offsets = [0]
    for y in range(height):
        row = image.crop((0, y, width, y + 1)).tobytes()
        colors = [(a, (r >> 3) << 11 | (g >> 2) << 5 | (b >> 3)) for r, g, b, a in zip(row[::4], row[1::4], row[2::4], row[3::4])]
        i = 0
        while i < width:
            alpha, color = colors[i]
            end = i + 1
            while end < min(i + 63, width) and colors[end] == (alpha, color):
                end += 1
            stream.extend(pack("<BBH", 128 | (end - i), alpha, color))
            i = end
            while end < min(i + 63, width) and colors[end][0] == alpha:
                if end + 1 < width and colors[end] == colors[end + 1]:
                    break
                end += 1
            if end > i:
                stream.append(192 | (end - i))
                stream.extend(b"".join(pack("<H", value) for _, value in colors[i:end]))
                i = end
        offsets.append(len(stream))
    step = round(len(stream) / height)
    deltas = [offset - i * step for i, offset in enumerate(offsets)]
    if not all(-32768 <= n <= 32767 for n in deltas):
        raise ValueError("Image exceeds PA row-delta range")
    return (BITMAP.pack(b"PA", 256, width, height, 512, step, bytes(4))
            + b"".join(pack("<h", n) for n in deltas) + stream)


def extract(source, output):
    data = source.read_bytes()
    header, records, assets = unpack_face(data)
    if pack_face(header, records, assets) != data:
        raise ValueError("Byte-for-byte round trip failed")
    output.mkdir(parents=True, exist_ok=True)
    inventory = []
    for offset, asset in assets.items():
        image = decode_asset(asset)
        name = f"asset_{offset:06x}.png"
        image.save(output / name)
        codec, = unpack_from("<H", asset, 8)
        inventory.append(dict(image=name, offset=offset, dimensions=image.size, codec=hex(codec)))
    keys = ("asset_offset", "kind", "binding", "frames", "x", "y", "parameter_1", "parameter_2")
    layout = dict(records=[dict(zip(keys, r)) for r in records], assets=inventory)
    (output / "layout.json").write_text(json.dumps(layout, indent=2) + "\n")
    print(f"{source.name}: {len(records)} records, {len(assets)} decoded images, byte-identical round trip")


def build(background, digits, output):
    if background.size != CANVAS or digits.size != (41, 550):
        raise ValueError("Expected a 320×385 background and ten stacked 41×55 digits")
    header = bytearray(HEADER_SIZE)
    header[:len(MAGIC)] = MAGIC
    pack_into("<BHH", header, 41, 0x00, *CANVAS)
    records = [(0, 2, 16, 1, 160, 192, 0, 0),
               (1, 4, 5, 10, 103, 174, 10497, 5888),
               (1, 4, 6, 10, 217, 174, 10497, 5888)]
    assets = {0: encode_asset(background), 1: encode_asset(digits)}
    result = pack_face(header, records, assets)
    if pack_face(*unpack_face(result)) != result:
        raise ValueError("Generated file failed its round trip")
    for asset, original in zip(assets.values(), (background, digits)):
        pixels = decode_asset(asset).tobytes()
        source = original.convert("RGBA").tobytes()
        for index, (before, after) in enumerate(zip(source, pixels)):
            shift = (3, 2, 3, 0)[index % 4]
            if before >> shift != after >> shift:
                raise ValueError("Generated bitmap did not round-trip at RGB565 precision")
    output.write_bytes(result)


def demo(output):
    output.mkdir(parents=True, exist_ok=True)
    background = Image.new("RGBA", CANVAS, "#111b24")
    draw = ImageDraw.Draw(background)
    draw.rounded_rectangle((12, 12, 307, 372), 32, fill="#14222e", outline="#314c5b", width=2)
    draw.rounded_rectangle((34, 127, 285, 224), 19, fill="#0c171f", outline="#314c5b", width=2)
    draw.text((160, 70), "N O R T H L I N E", fill="#9de2c8", anchor="mm")
    draw.text((160, 292), "LOCAL TIME", fill="#9bb7c0", anchor="mm")
    draw.ellipse((156, 162, 164, 170), fill="#89edb7")
    draw.ellipse((156, 180, 164, 188), fill="#89edb7")
    for x in range(72, 250, 16):
        draw.line((x, 318, x, 327 if x % 48 == 24 else 322), fill="#497469", width=2)
    digits = Image.new("RGBA", (41, 550))
    font = ImageFont.load_default(size=51)
    draw = ImageDraw.Draw(digits)
    for i in range(10):
        draw.text((20, i * 55 + 28), str(i), font=font, fill="#dbfff0", anchor="mm")
    background.save(output / "northline_background.png")
    digits.save(output / "northline_digits.png")
    build(background, digits, output / "northline.bin")
    for x, digit in zip((62, 103, 176, 217), (1, 0, 0, 8)):
        glyph = digits.crop((0, digit * 55, 41, digit * 55 + 55))
        background.alpha_composite(glyph, (x, 147))
    background.save(output / "northline_preview.png")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command, names in (("extract", ("source", "output")),
                           ("build", ("background", "digits", "output")),
                           ("demo", ("output",))):
        subparser = commands.add_parser(command)
        for name in names:
            subparser.add_argument(name, type=Path)
    args = parser.parse_args()
    if args.command == "extract":
        extract(args.source, args.output)
    elif args.command == "build":
        with Image.open(args.background) as background, Image.open(args.digits) as digits:
            build(background, digits, args.output)
    else:
        demo(args.output)


if __name__ == "__main__":
    main()
