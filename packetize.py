"""Generate Android helper packets from an HK11 WF file."""
import argparse
from pathlib import Path
import struct


BLOCK_SIZE = 4096
CHUNK_SIZE = 232


def crc16(data):
    checksum = 0xFFFF
    for value in data:
        checksum ^= value
        for _ in range(8):
            checksum = (checksum >> 1) ^ (0xA001 if checksum & 1 else 0)
    return checksum


def file_header(filename, data):
    name = filename.encode("ascii")
    payload = (bytes((0, len(name))) + name + b"\x09watchface"
               + struct.pack(">IH", len(data), crc16(data)))
    return struct.pack(">BHHHH", 0xA0, 0, 0, len(payload), crc16(payload)) + payload


def block_packets(index, block):
    for i, start in enumerate(range(0, len(block), CHUNK_SIZE)):
        head = struct.pack(">BHH", 0xA1, index, i)
        if i == 0:
            head += struct.pack(">HH", len(block), crc16(block))
        yield head + block[start:start + CHUNK_SIZE]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dial", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    data = args.dial.read_bytes()
    if (not 52 <= len(data) < 1_000_000
            or not data.startswith(b"WF\x01\x00\x00\x80")
            or struct.unpack_from("<I", data, 48)[0] != len(data)):
        parser.error("expected an HK11 WF dial with a valid file length")
    blocks = [list(block_packets(index, data[start:start + BLOCK_SIZE]))
              for index, start in enumerate(range(0, len(data), BLOCK_SIZE))]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("wb") as output:
        def write_short(value):
            output.write(struct.pack(">H", value))

        def write_packet(packet):
            write_short(len(packet))
            output.write(packet)

        write_packet(file_header(args.dial.name, data))
        write_short(len(blocks))
        for block in blocks:
            write_short(len(block))
            for packet in block:
                write_packet(packet)


if __name__ == "__main__":
    main()
