import copy
import json
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import analyze_vulkan as vk


def adrp(register, pc, target):
    value = (((target & ~4095) - (pc & ~4095)) >> 12) & 0x1FFFFF
    return 0x90000000 | ((value & 3) << 29) | (((value >> 2) & 0x7FFFF) << 5) | register


def add(register, base, immediate):
    return 0x91000000 | (immediate << 10) | (base << 5) | register


def ldr(register, base, immediate=0):
    return 0xF9400000 | ((immediate // 8) << 10) | (base << 5) | register


def branch(pc, target, link=False):
    return (0x94000000 if link else 0x14000000) | (((target - pc) // 4) & 0x3FFFFFF)


def fixture():
    source = bytearray(0x6000)
    for offset in range(0x1000, 0x1050, 4):
        struct.pack_into("<I", source, offset, 0xD503201F)
    words = {
        0x1000: adrp(1, 0x1000, 0x4000), 0x1004: add(1, 1, 0),
        0x1008: branch(0x1008, 0x3000, True),
        0x100C: 0x34000000 | (((0x1040 - 0x100C) // 4) << 5),
        0x1010: adrp(1, 0x1010, 0x4000), 0x1014: add(1, 1, 0x40),
        0x1018: branch(0x1018, 0x3000, True), 0x101C: 0x7100001F,
        0x1020: adrp(0, 0x1020, 0x5000), 0x1024: ldr(0, 0, 8),
        0x1028: 0x9A9F0000, 0x102C: branch(0x102C, 0x1048),
        0x1040: adrp(0, 0x1040, 0x5000), 0x1044: ldr(0, 0),
        0x1048: 0xD65F03C0,
    }
    for offset, word in words.items():
        struct.pack_into("<I", source, offset, word)
    for offset, name in [(0x4000, b"vkDestroyInstance\0"), (0x4040, b"vkCreateInstance\0")]:
        source[offset:offset + len(name)] = name
    struct.pack_into("<QQ", source, 0x5000, 0x800, 0x820)
    return source


def recover(source):
    return vk.recover_lookup(source, start=0x1000, size=0x50, strcmp=0x3000, minimum_exports=2)


class VulkanRecoveryTests(unittest.TestCase):
    def test_cbz_and_final_csel_lookup_results_including_zero_got_offset(self):
        found = recover(fixture())
        self.assertEqual([(x["name"], x["offset"]) for x in found], [("vkDestroyInstance", 0x800), ("vkCreateInstance", 0x820)])
        self.assertEqual(found[0]["pointer_offset"], 0x5000)
        self.assertEqual(found[1]["result_kind"], "strcmp-cmp-csel-got")

    def test_malformed_return_block_and_incomplete_inventory_fail(self):
        data = fixture()
        struct.pack_into("<I", data, 0x1044, 0xD503201F)
        with self.assertRaisesRegex(ValueError, "GOT result"):
            recover(data)
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            vk.recover_lookup(fixture(), start=0x1000, size=0x50, strcmp=0x3000, minimum_exports=3)

    def test_address_taken_function_requires_hook_even_without_direct_branch(self):
        data = fixture()
        struct.pack_into("<II", data, 0x100, adrp(2, 0x100, 0x5000), ldr(2, 2))
        refs = vk.scan_references(data, recover(data), prefix_start=0x80, prefix_end=0x200, wrapper_ranges=[(0x800, 0x1000)])
        self.assertEqual(refs, {0x800: [{"site": 0x100, "kind": "adrp-ldr-address", "pointer_offset": 0x5000}]})

    def test_stale_adrp_register_does_not_fabricate_reference(self):
        data = fixture()
        struct.pack_into("<III", data, 0x100, adrp(1, 0x100, 0x5000), 0xAA1F03E1, ldr(0, 1))
        self.assertEqual(vk.scan_references(data, recover(data), prefix_start=0x80, prefix_end=0x200, wrapper_ranges=[(0x800, 0x1000)]), {})

    def test_direct_b_bl_and_unmapped_wrapper_target_fail_closed(self):
        data = fixture()
        struct.pack_into("<II", data, 0x80, branch(0x80, 0x800), branch(0x84, 0x820, True))
        refs = vk.scan_references(data, recover(data), prefix_start=0x80, prefix_end=0x100, wrapper_ranges=[(0x800, 0x1000)])
        self.assertEqual(refs[0x800][0]["kind"], "b")
        self.assertEqual(refs[0x820][0]["kind"], "bl")
        struct.pack_into("<I", data, 0x80, branch(0x80, 0x808))
        with self.assertRaisesRegex(ValueError, "Unmapped"):
            vk.scan_references(data, recover(data), prefix_start=0x80, prefix_end=0x100, wrapper_ranges=[(0x800, 0x1000)])

    def test_profile_retains_runtime_records_and_special_surface_not_linked(self):
        runtime = [{"slot": 1024, "offset": 0x40, "symbol": "boot", "expected_original": "abc"}]
        profile = {"source_nro_sha256": "same", "hooks": [{"slot": 0, "offset": 0x10}] + runtime}
        before = copy.deepcopy(runtime)
        report = {"source_nro_sha256": "same", "hooks": [{"slot": 0, "offset": 0x20, "symbol": "vkDestroyInstance"}]}
        updated = vk.update_profile(profile, report)
        self.assertEqual(updated["hooks"][1:], before)
        linked = vk.exports_text("PROTOTYPE_VK_EXPORT(vkCreateDevice)\n", report["hooks"] + [{"symbol": "vkCreateViSurfaceNN"}])
        self.assertIn("PROTOTYPE_VK_EXPORT(vkDestroyInstance)", linked)
        self.assertIn("PROTOTYPE_VK_EXPORT(vkCreateDevice)", linked)
        self.assertNotIn("vkCreateViSurfaceNN", linked)

    def test_real_guest_lookup_and_destroy_instance_address_reference(self):
        root = Path(__file__).resolve().parents[1]
        nro, boundaries = root / "switch/prototype-nx/prototype.nro", root / "work/analysis/boundaries.json"
        if not nro.is_file() or not boundaries.is_file():
            self.skipTest("Original guest and local FDE evidence are private inputs")
        report = vk.analyze(nro.read_bytes(), json.loads(boundaries.read_text()))
        self.assertEqual(report["export_count"], 768)
        self.assertEqual([h["symbol"] for h in report["hooks"]], ["vkGetInstanceProcAddr", "vkGetDeviceProcAddr", "vkDestroyInstance"])
        destroy = report["hooks"][2]
        self.assertEqual(destroy["offset"], 0x290EFB4)
        self.assertEqual(destroy["call_sites"], [{"site": 0x1677E8, "kind": "adrp-ldr-address", "pointer_offset": 0x3CA0B68}])


if __name__ == "__main__":
    unittest.main()
