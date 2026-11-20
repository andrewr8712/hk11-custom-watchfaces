# HK11 PRO-A WF file and BLE install specification

This is a reverse-engineered profile for HK11 PRO-A firmware 3.07, project 7678. The included exporter and Android sender implement the digital subset tested on the watch. Multibyte integers inside the WF and PA files are **little-endian**. Multibyte integers in the BLE transfer protocol are **big-endian**.

## WF container

The file begins with a 52-byte header, then a table of 20-byte records, then the bitmap payloads. Offsets below are zero-based.

| Offset | Size | Field |
| --- | ---: | --- |
| 0 | 6 | Magic `57 46 01 00 00 80` |
| 6 | 36 | Zero in the verified exporter and five stock reference files |
| 42 | 2 | Canvas width, 320 |
| 44 | 2 | Canvas height, 385 |
| 46 | 2 | Record count |
| 48 | 4 | Total file byte length, including header, table and assets |

The record layout is `<IBBHHHII` in Python's struct notation. The fields are unsigned.

| Offset in record | Size | Field |
| --- | ---: | --- |
| 0 | 4 | Absolute byte offset of a PA asset from the start of the WF file |
| 4 | 1 | Component kind |
| 5 | 1 | Binding |
| 6 | 2 | Frame count |
| 8 | 2 | X coordinate |
| 10 | 2 | Y coordinate |
| 12 | 4 | First component parameter |
| 16 | 4 | Second component parameter |

The first asset begins at `52 + record_count * 20`. A record can share an asset with another record. In the observed files, each asset extends to the next distinct asset offset or the end of the file.

### Verified minimal digital layout

The following values reproduce the installed Ember and Northline layouts. Both clock records reference the same digit strip.

| Component | Asset | Kind | Binding | Frames | X | Y | Parameter 1 | Parameter 2 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Background | 320 × 385 bitmap | 2 | 16 | 1 | 160 | 192 | 0 | 0 |
| Hours | 41 × 550 digit strip | 4 | 5 | 10 | 103 | 174 | 10497 | 5888 |
| Minutes | Same digit strip | 4 | 6 | 10 | 217 | 174 | 10497 | 5888 |

The strip contains ten glyphs of 41 × 55 pixels in order 0–9 from top to bottom. Draw the colon into the background. The parameters above are verified as a set. Their individual subfields are still under investigation.

Stock layouts also contain analog hands, animation frames, battery gauges and weather icons. Their full parameter meanings and update behavior are outside this verified export profile. No dedicated chooser thumbnail was identified in the five stock files. A face can install and display correctly while the chooser shows generic artwork.

## PA bitmap assets

The 16-byte bitmap header is `<2sHHHHH4s`. It contains the two bytes `PA`, version `0x0100`, width, height, codec, offset step and four reserved bytes. The reference writer sets the reserved bytes to zero and uses codec `0x0200`.

For `0x0200`, the header is followed by `height + 1` signed 16-bit little-endian offset deltas. Entry `i` gives byte offset `i * step + delta[i]` relative to the end of the offset table. The first offset is zero and the last marks the end of the asset. Each scanline decodes independently with RGB and alpha state initially zero. Colors use RGB565 and a separate 8-bit alpha value.

| Opcode range | Meaning |
| --- | --- |
| `0x00–0x7f` | Emit one pixel with the current alpha. Add `(opcode >> 5) - 2` to red, `((opcode >> 2) & 7) - 4` to green and `(opcode & 3) - 2` to blue, wrapping within the RGB565 component widths. |
| `0x80–0xbf` | Read one alpha byte and one little-endian RGB565 color. Emit that pixel `opcode & 63` times and retain its state. |
| `0xc0–0xff` | Read and emit `opcode & 63` literal little-endian RGB565 colors using the current alpha. Retain the last color. |

Run length zero is invalid. The writer uses repeat and literal runs. The included reader also decodes the signed 8-bit offset variant `0x0100` and tiled variants `0x011e` and `0x021e`. For tiled assets, the offset table indexes 31 × 31 tiles in row-major order with a 30-pixel stride.

## BLE install

The UUID suffix below is shared by the listed services and characteristics. Append `-b5a3-f393-e0a9-e50e24dcca9e` to each first group.

| Purpose | UUID first group |
| --- | --- |
| Control service | `6e400001` |
| Control write characteristic | `6e400002` |
| File service | `6e400011` |
| File write characteristic | `6e400012` |
| File notification characteristic | `6e400013` |

Use writes without response for the control and file data characteristics. Subscribe to file notifications before sending. The longest file write is 241 value bytes. The reference sender requires an ATT MTU of at least 244.

### Install sequence

1. Write `ab 00 04 ff 87 80 01` to the control characteristic to request high-speed mode. The verified helper waits 150 ms before the next write.
2. On the control characteristic, send dial-start status. The 12-byte message contains `b0`, seven zero bytes and a big-endian 32-bit status field. Status 0 means start, 1 means success and 2 means failure.
3. Send the file header on the file write characteristic. Wait for the incoming `b0 00 00 00` on the file notification characteristic. The outbound status and inbound acknowledgement share the leading byte. Direction and characteristic distinguish them.
4. Send the data sequentially in blocks of at most 4,096 bytes, split into payload chunks of at most 232 bytes per packet. The verified helper schedules writes about 17 ms apart within each block. After a block, wait for `b1 <block:u16> 00` before continuing. Final success is `b1 ff ff 03`. It may arrive in place of a separate zero-status acknowledgement for the last block.
5. After the final acknowledgement, write dial-success status to the control characteristic. The verified helper waits about 35 ms after the acknowledgement before this write, then about 110 ms before restoring normal speed with `ab 00 04 ff 87 80 00`. On a failed transfer after start, send dial-failure status when the link is available and restore normal speed. The reference sender uses a 12-second wait for file acknowledgements.
6. Verify visible activation on the watch. The final file acknowledgement confirms receipt. The outbound status messages were required in the two tested installs to show the loading animation and activate the face.

Timings are settings of the verified Android implementation. They are starting values for other implementations, since the exact firmware tolerance is unknown.

### File header and data packets

All fields below follow the stated byte order with no implicit padding.

```text
Header   A0 | 0000:u16 | 0000:u16 | payload_length:u16 | payload_crc:u16 | payload
Payload  00 | filename_length:u8 | filename:ASCII | 09 | "watchface":ASCII
            | file_length:u32 | file_crc:u16

First chunk in block   A1 | block_index:u16 | 0000:u16 | block_length:u16 | block_crc:u16 | bytes
Other chunks           A1 | block_index:u16 | chunk_index:u16 | bytes
```

Blocks and chunks are indexed from zero. The filename length counts bytes. Use a short ASCII basename such as `ember.bin`. The header CRC covers only its payload, the file CRC covers the complete WF file, and the block CRC covers only that block's data. All three use CRC-16/MODBUS with initial value `0xffff`, reflected polynomial `0xa001`, and no final XOR. The CRC of ASCII `123456789` is `0x4b37`. The resulting CRC integer is serialized big-endian in this protocol.

The reference packetizer saves a local `transfer.dat` for the Android sender. That file is an application resource. Its length prefixes and packet counts are local framing and are removed before transmitting each BLE write.

[Back to the guide and reference build](README.md).
