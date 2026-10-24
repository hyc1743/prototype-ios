import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CollisionBitmapFixTests(unittest.TestCase):
    def test_wrapper_targets_prototype_one_collision_bitmap_function(self):
        source = (ROOT / 'ios/source-port/PrototypeCollisionFix.cpp').read_text(encoding='utf8')
        self.assertIn('sub_827A28A8', source)
        self.assertIn('uint64_t(start) + 8192', source)
        self.assertIn('std::memset(static_cast<void*>(REX_RAW_ADDR(start)), 0, 8192)', source)
        self.assertIn('proto_collision_bitmap_fix', source)

    def test_ios_build_links_collision_fix_source(self):
        source = (ROOT / 'tools/build_source_ios.py').read_text(encoding='utf8')
        self.assertIn('ios/source-port/PrototypeCollisionFix.cpp', source)


if __name__ == '__main__':
    unittest.main()
