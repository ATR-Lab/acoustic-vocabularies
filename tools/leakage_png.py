"""Bounded stdlib decoder for actual noninterlaced Unity RGB/RGBA PNG captures.

No resizing, image editing, lossy decoding, color correction or noise removal.
PNG bytes remain unchanged. Differences compare all decoded channels, including
alpha, so invisible RGB differences are conservatively retained.
"""
import hashlib
import struct
import zlib

MAX_PIXELS = 16_777_216
MAX_BYTES = 80 * 1024 * 1024


def need(ok, code):
    if not ok:
        raise ValueError(code)


def decode_png(raw):
    need(isinstance(raw, bytes) and 0 < len(raw) <= MAX_BYTES, "PNG_SIZE")
    need(raw[:8] == b"\x89PNG\r\n\x1a\n", "PNG_SIGNATURE")
    offset, width, height, channels = 8, None, None, None
    packed = bytearray()
    ended = seen_data = data_ended = False
    color_metadata = []
    while offset < len(raw):
        need(offset + 12 <= len(raw), "PNG_TRUNCATED_CHUNK")
        count = struct.unpack_from(">I", raw, offset)[0]
        kind = raw[offset + 4:offset + 8]
        need(len(kind) == 4 and all(65 <= c <= 90 or 97 <= c <= 122 for c in kind), "PNG_CHUNK_NAME")
        end = offset + 12 + count
        need(end <= len(raw), "PNG_TRUNCATED_CHUNK")
        data = raw[offset + 8:end - 4]
        crc = struct.unpack_from(">I", raw, end - 4)[0]
        need(zlib.crc32(kind + data) & 0xffffffff == crc, "PNG_CRC")
        need(not ended, "PNG_TRAILING_BYTES")
        if width is None:
            need(kind == b"IHDR" and count == 13, "PNG_HEADER")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", data)
            need(0 < width <= 8192 and 0 < height <= 8192 and width * height <= MAX_PIXELS, "PNG_DIMENSIONS")
            need(depth == 8 and color in (2, 6) and compression == filtering == interlace == 0, "PNG_ENCODING")
            channels = 3 if color == 2 else 4
        elif kind == b"IDAT":
            need(not data_ended, "PNG_SPLIT_DATA")
            packed.extend(data)
            seen_data = True
        elif kind == b"IEND":
            need(count == 0 and seen_data and end == len(raw), "PNG_END")
            ended = True
        else:
            need(kind != b"IHDR" and len(kind) == 4 and kind[0] & 32, "PNG_UNSUPPORTED_CRITICAL")
            need(kind not in (b"acTL", b"fcTL", b"fdAT", b"tRNS"), "PNG_ANIMATION_OR_TRANSPARENCY")
            # Different color interpretation is not dismissed as identical bytes.
            if kind in (b"gAMA", b"sRGB", b"iCCP", b"cHRM", b"cICP"):
                color_metadata.append((kind.decode("ascii"), hashlib.sha256(data).hexdigest()))
            if seen_data:
                data_ended = True
        offset = end
    need(ended, "PNG_MISSING_END")
    stride = width * channels
    expected = (stride + 1) * height
    decoder = zlib.decompressobj()
    try:
        filtered = decoder.decompress(bytes(packed), expected + 1)
    except zlib.error as error:
        raise ValueError("PNG_DEFLATE") from error
    need(len(filtered) == expected and decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail, "PNG_DECODE_SIZE")
    pixels = bytearray(stride * height)
    previous = bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        method = filtered[start]
        need(method in range(5), "PNG_FILTER")
        row = bytearray(filtered[start + 1:start + 1 + stride])
        if method:
            for x in range(stride):
                left = row[x - channels] if x >= channels else 0
                above = previous[x]
                corner = previous[x - channels] if x >= channels else 0
                if method == 1:
                    prediction = left
                elif method == 2:
                    prediction = above
                elif method == 3:
                    prediction = (left + above) // 2
                else:
                    p = left + above - corner
                    a, b, c = abs(p - left), abs(p - above), abs(p - corner)
                    prediction = left if a <= b and a <= c else above if b <= c else corner
                row[x] = (row[x] + prediction) & 255
        pixels[y * stride:(y + 1) * stride] = row
        previous = row
    pixels = bytes(pixels)
    return {"width": width, "height": height, "channels": channels, "pixels": pixels,
            "pixel_sha256": hashlib.sha256(pixels).hexdigest(), "color_metadata": color_metadata}


def compare_pixels(left, right, tolerance):
    need(type(tolerance) is int and 0 <= tolerance <= 255, "PIXEL_TOLERANCE")
    keys = ("width", "height", "channels", "color_metadata")
    if any(left[k] != right[k] for k in keys):
        return {"within_tolerance": False, "reason": "IMAGE_INTERPRETATION_DIFFERS"}
    if left["pixels"] == right["pixels"]:
        return {"within_tolerance": True, "comparison": "all_decoded_channels_identical"}
    # First exceedance is sufficient to refute a max-channel bound. Do not call
    # this the maximum or changed-pixel count: those were deliberately not measured.
    for index, (a, b) in enumerate(zip(left["pixels"], right["pixels"], strict=True)):
        if abs(a - b) > tolerance:
            pixel, channel = divmod(index, left["channels"])
            y, x = divmod(pixel, left["width"])
            return {"within_tolerance": False, "reason": "PIXEL_DELTA", "first_exceedance":
                    {"x": x, "y": y, "channel": channel, "absolute_delta": abs(a - b)}}
    return {"within_tolerance": True, "comparison": "all_decoded_channels_within_bound"}
