from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from swf_font_scale import Bits, scale_rect, scale_shape, width


def contour():
    output = Bits()
    output.write(1, 4); output.write(0, 4)
    output.write(0, 1); output.write(3, 5)
    output.write(1, 5); output.write(0, 1); output.write(0, 1); output.write(1, 1)
    for dx, dy in ((20480, 0), (0, 20480), (-20480, 0), (0, -20480)):
        count = width([dx, dy], 2)
        output.write(1, 1); output.write(1, 1); output.write(count-2, 4)
        output.write(1, 1); output.write(dx, count); output.write(dy, count)
    # A quadratic curve with control (10240, 5120), endpoint (20480, 0).
    output.write(1, 1); output.write(0, 1)
    count = width([10240, 5120, 10240, -5120], 2)
    output.write(count-2, 4)
    for value in (10240, 5120, 10240, -5120): output.write(value, count)
    output.write(0, 1); output.write(0, 5)
    return output.bytes()


class FontScaleTests(unittest.TestCase):
    def test_em_conversion_keeps_contour_endpoints_and_quadratic_control(self):
        raw = contour()
        changed = scale_shape(raw)
        reader = Bits(changed)
        self.assertEqual((reader.read(4), reader.read(4)), (1, 0))
        self.assertEqual((reader.read(1), reader.read(5)), (0, 3))
        n = reader.read(5)
        self.assertEqual((reader.read(n, True), reader.read(n, True), reader.read(1)), (0, 0, 1))
        points, x, y = [], 0, 0
        for _ in range(4):
            self.assertEqual((reader.read(1), reader.read(1)), (1, 1))
            n = reader.read(4)+2
            self.assertEqual(reader.read(1), 1)
            x += reader.read(n, True); y += reader.read(n, True)
            points.append((x, y))
        self.assertEqual(points, [(1024, 0), (1024, 1024), (0, 1024), (0, 0)])
        self.assertEqual((reader.read(1), reader.read(1)), (1, 0))
        n = reader.read(4)+2
        self.assertEqual([reader.read(n, True) for _ in range(4)], [512, 256, 512, -256])
        self.assertEqual((reader.read(1), reader.read(5)), (0, 0))
        self.assertLess(len(changed), len(raw))

    def test_rectangle_bounds_scale_with_signed_coordinates(self):
        output = Bits()
        values = [-20480, 20480, -10240, 10240]
        n = width(values)
        output.write(n, 5)
        for value in values: output.write(value, n)
        reader = Bits(scale_rect(output.bytes()))
        n = reader.read(5)
        self.assertEqual([reader.read(n, True) for _ in range(4)], [-1024, 1024, -512, 512])


if __name__ == '__main__': unittest.main()
