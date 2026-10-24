"""Convert DefineFont3 outlines (20480 units/em) to DefineFont2 (1024)."""
import struct


class Bits:
    def __init__(self, data=b''):
        self.data, self.pos, self.out = data, 0, []

    def read(self, count, signed=False):
        value = 0
        for _ in range(count):
            value = (value << 1) | ((self.data[self.pos//8] >> (7-self.pos%8)) & 1)
            self.pos += 1
        if signed and count and value & (1 << (count-1)):
            value -= 1 << count
        return value

    def write(self, value, count):
        self.out.extend((value >> bit) & 1 for bit in range(count-1, -1, -1))

    def bytes(self):
        values = self.out + [0]*((-len(self.out)) % 8)
        return bytes(sum(values[i+j] << (7-j) for j in range(8)) for i in range(0, len(values), 8))


def width(values, minimum=1):
    return max([minimum]+[(value.bit_length()+1 if value >= 0 else (~value).bit_length()+1) for value in values])


def scale_rect(raw):
    source, target = Bits(raw), Bits()
    count = source.read(5)
    values = [round(source.read(count, True)/20) for _ in range(4)]
    count = width(values)
    target.write(count, 5)
    for value in values:
        target.write(value, count)
    return target.bytes()


def scale_shape(raw):
    if not raw:
        return raw
    source, target = Bits(raw), Bits()
    fill, line = source.read(4), source.read(4)
    target.write(fill, 4); target.write(line, 4)
    x = y = nx = ny = 0
    for _ in range(100000):
        edge = source.read(1)
        target.write(edge, 1)
        if not edge:
            flags = source.read(5)
            target.write(flags, 5)
            if flags == 0:
                return target.bytes()
            if flags & 16:
                raise ValueError('Font outline unexpectedly defines new styles')
            if flags & 1:
                count = source.read(5)
                x, y = source.read(count, True), source.read(count, True)
                nx, ny = round(x/20), round(y/20)
                count = width([nx, ny])
                target.write(count, 5); target.write(nx, count); target.write(ny, count)
            for flag, count in ((2, fill), (4, fill), (8, line)):
                if flags & flag:
                    target.write(source.read(count), count)
            continue
        straight = source.read(1)
        target.write(straight, 1)
        count = source.read(4)+2
        if straight:
            general = source.read(1)
            if general:
                dx, dy = source.read(count, True), source.read(count, True)
            elif source.read(1):
                dx, dy = 0, source.read(count, True)
            else:
                dx, dy = source.read(count, True), 0
            x += dx; y += dy
            xx, yy = round(x/20), round(y/20)
            dx, dy = xx-nx, yy-ny; nx, ny = xx, yy
            count = width([dx, dy], 2)
            target.write(count-2, 4)
            # General edges preserve both rounded components, including zeros.
            target.write(1, 1); target.write(dx, count); target.write(dy, count)
        else:
            cx, cy = x+source.read(count, True), y+source.read(count, True)
            x, y = cx+source.read(count, True), cy+source.read(count, True)
            ncx, ncy, xx, yy = round(cx/20), round(cy/20), round(x/20), round(y/20)
            values = [ncx-nx, ncy-ny, xx-ncx, yy-ncy]
            nx, ny = xx, yy
            count = width(values, 2)
            target.write(count-2, 4)
            for value in values:
                target.write(value, count)
    raise ValueError('Font outline has no end marker')


def scale_metric(raw):
    return struct.pack('<h', round(struct.unpack('<h', raw)[0]/20))


def scale_glyph(glyph):
    outline, advance, bounds = glyph
    return scale_shape(outline), scale_metric(advance), scale_rect(bounds)
