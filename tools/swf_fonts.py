"""Preserve the console font names/icons while adding Chinese outline glyphs."""
from __future__ import annotations
from dataclasses import dataclass
import struct
import zlib


def rect_end(data, pos):
    bits = data[pos] >> 3
    return pos + (5 + 4*bits + 7)//8


def swf_parts(data):
    if len(data) < 16 or data[:3] not in (b'GFX', b'FWS', b'CFX', b'CWS'):
        raise ValueError('Unsupported font library')
    if data[:3] in (b'CFX', b'CWS'):
        data = data[:8] + zlib.decompress(data[8:])
    if struct.unpack_from('<I', data, 4)[0] != len(data):
        raise ValueError('Font library length differs')
    pos = rect_end(data, 8) + 4
    prefix, tags = data[:pos], []
    while pos < len(data):
        word = struct.unpack_from('<H', data, pos)[0]
        pos += 2
        kind, size = word >> 6, word & 63
        if size == 63:
            size = struct.unpack_from('<I', data, pos)[0]
            pos += 4
        if pos + size > len(data):
            raise ValueError('Truncated font tag')
        tags.append((kind, data[pos:pos+size]))
        pos += size
    return prefix, tags


@dataclass
class Font:
    ident: int
    flags: int
    language: int
    name: bytes
    metrics: bytes
    # codepoint -> (outline, advance, bounds)
    glyphs: dict
    kerning: list

    @classmethod
    def parse(cls, body):
        ident, flags, language, n = struct.unpack_from('<HBBB', body)
        name = body[5:5+n]
        pos = 5+n
        count = struct.unpack_from('<H', body, pos)[0]
        pos += 2
        width = 4 if flags & 8 else 2
        fmt = '<' + ('I' if width == 4 else 'H')
        offsets = [struct.unpack_from(fmt, body, pos+i*width)[0] for i in range(count+1)]
        if offsets != sorted(offsets) or offsets[0] != (count+1)*width:
            raise ValueError('Invalid outline offsets')
        shapes = [body[pos+a:pos+b] for a, b in zip(offsets, offsets[1:])]
        pos += offsets[-1]
        code_width = 2 if flags & 4 else 1
        fmt = '<' + ('H' if code_width == 2 else 'B')
        codes = [struct.unpack_from(fmt, body, pos+i*code_width)[0] for i in range(count)]
        pos += count*code_width
        if len(set(codes)) != count or not flags & 128:
            raise ValueError('Font needs unique glyph codes and layout metrics')
        metrics = body[pos:pos+6]
        pos += 6
        advances = [body[pos+i*2:pos+i*2+2] for i in range(count)]
        pos += count*2
        bounds = []
        for _ in range(count):
            end = rect_end(body, pos)
            bounds.append(body[pos:end])
            pos = end
        kcount = struct.unpack_from('<H', body, pos)[0]
        pos += 2
        kerning = []
        for _ in range(kcount):
            a, b = (struct.unpack_from(fmt, body, pos+i*code_width)[0] for i in range(2))
            adjust = body[pos+2*code_width:pos+2*code_width+2]
            kerning.append((a, b, adjust))
            pos += 2*code_width+2
        if pos != len(body):
            raise ValueError('Unexpected trailing font data')
        return cls(ident, flags, language, name, metrics,
                   dict(zip(codes, zip(shapes, advances, bounds))), kerning)

    def encode(self):
        codes = sorted(self.glyphs)
        offset = 4*(len(codes)+1)
        offsets, shapes = [], bytearray()
        for code in codes:
            offsets.append(offset+len(shapes))
            shapes.extend(self.glyphs[code][0])
        offsets.append(offset+len(shapes))
        body = bytearray(struct.pack('<HBBB', self.ident, self.flags | 12, self.language, len(self.name)))
        body.extend(self.name)
        body.extend(struct.pack('<H', len(codes)))
        body.extend(b''.join(struct.pack('<I', value) for value in offsets))
        body.extend(shapes)
        body.extend(b''.join(struct.pack('<H', value) for value in codes))
        body.extend(self.metrics)
        body.extend(b''.join(self.glyphs[code][1] for code in codes))
        body.extend(b''.join(self.glyphs[code][2] for code in codes))
        kerning = [row for row in self.kerning if row[0] in self.glyphs and row[1] in self.glyphs]
        body.extend(struct.pack('<H', len(kerning)))
        for a, b, adjust in kerning:
            body.extend(struct.pack('<HH', a, b)+adjust)
        return bytes(body)


def adapt_font_library(original, chinese, required):
    prefix, tags = swf_parts(original)
    _, additions = swf_parts(chinese)
    donors = {font.ident: font for kind, body in additions if kind == 75
              for font in [Font.parse(body)]}
    report, rebuilt = [], []
    for kind, body in tags:
        if kind == 75:
            font = Font.parse(body)
            if font.ident in (1, 3, 5):
                # The PC title font is multi-megabyte. Body outlines use the
                # same DefineFont3 coordinates and cover the menu's Chinese.
                preferred = donors[3]
                candidates = [preferred, donors[3], donors[1], donors[5]]
                needed = required[font.ident] if isinstance(required, dict) else required
                for code in needed:
                    if code in font.glyphs or code < 32:
                        continue
                    for donor in candidates:
                        if code in donor.glyphs:
                            font.glyphs[code] = donor.glyphs[code]
                            break
                    else:
                        raise ValueError(f'Chinese font lacks U+{code:04X}')
                body = font.encode()
                report.append({'name': font.name.rstrip(b'\0').decode(),
                               'glyphs': len(font.glyphs), 'bytes': len(body)})
        elif kind in (13, 62, 73):
            # Font3 contains its own codes/layout. Old per-glyph hint tables
            # cannot be reused after new outline glyphs have been appended.
            continue
        rebuilt.append(struct.pack('<HI', (kind << 6) | 63, len(body)) + body)
    result = bytearray(prefix + b''.join(rebuilt))
    struct.pack_into('<I', result, 4, len(result))
    # Independent parse verifies the emitted offsets, bounds and table sizes.
    for kind, body in swf_parts(result)[1]:
        if kind == 75:
            Font.parse(body)
    return bytes(result), report


def adapt_legacy_font_library(original, legacy, chinese, required):
    """Use the supplied compact Font2 outlines and supplement missing glyphs."""
    from swf_font_scale import scale_glyph, scale_metric
    prefix, tags = swf_parts(original)
    old = {font.ident: font for kind, body in swf_parts(legacy)[1] if kind == 48
           for font in [Font.parse(body)]}
    modern = {font.ident: font for kind, body in swf_parts(chinese)[1] if kind == 75
              for font in [Font.parse(body)]}
    rebuilt, report = [], []
    for kind, body in tags:
        if kind == 75:
            font = Font.parse(body)
            if font.ident in (1, 3, 5):
                needed = required[font.ident] if isinstance(required, dict) else required
                font.glyphs = {code: scale_glyph(glyph) for code, glyph in font.glyphs.items()}
                for code in needed:
                    if code in font.glyphs or code < 32:
                        continue
                    donors = [old[1 if font.ident == 1 else 3], old[3], old[1]]
                    glyph = next((donor.glyphs[code] for donor in donors if code in donor.glyphs), None)
                    if glyph is None:
                        glyph = next((scale_glyph(donor.glyphs[code]) for donor in
                                      (modern[3], modern[1], modern[5]) if code in donor.glyphs), None)
                    if glyph is None:
                        raise ValueError(f'Chinese font lacks U+{code:04X}')
                    font.glyphs[code] = glyph
                font.metrics = b''.join(scale_metric(font.metrics[i:i+2]) for i in range(0, 6, 2))
                font.kerning = [(a, b, scale_metric(adjust)) for a, b, adjust in font.kerning]
                body = font.encode()
                kind = 48
                report.append({'name': font.name.rstrip(b'\0').decode(),
                               'glyphs': len(font.glyphs), 'bytes': len(body), 'format': 'DefineFont2'})
        elif kind in (13, 62, 73):
            continue
        rebuilt.append(struct.pack('<HI', (kind << 6) | 63, len(body))+body)
    result = bytearray(prefix+b''.join(rebuilt))
    struct.pack_into('<I', result, 4, len(result))
    for kind, body in swf_parts(result)[1]:
        if kind in (48, 75):
            Font.parse(body)
    return bytes(result), report
