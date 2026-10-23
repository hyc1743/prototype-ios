import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from stage_ios import PROFILE, binding_header, embedded_sections, frontend, patch_sources, replace_once


FRONTEND = '''    for (NSString *d in @[@"save", @"temp"]) {}
    for (NSString *d in @[@"romfs", @"temp"]) {}
    // Keep game files and scratch data out of device backups
static int boot_env(void) {
    setvbuf(stderr, NULL, _IOLBF, 0);
    NSString *romfs = romfs_locate();
    setenv("DM_ROMFS", romfs.fileSystemRepresentation, 0);
    setenv("DM_SAVE", [doc stringByAppendingPathComponent:@"save"].fileSystemRepresentation, 0);
}
    self.window.rootViewController = front_ios_root();
int main(int argc, char **argv) {
    @autoreleasepool { g_boot_ok = boot_env(); }
}
'''


class StageTests(unittest.TestCase):
    def test_binding_uses_verified_table_layout_and_expected_build(self):
        hooks = {"table_offset": 0x2500000, "table_size": 0x4000,
                 "hooks": [{"symbol": "vkCreateInstance", "slot": 2}, {"symbol": "PrototypeOpen", "slot": 7}]}
        header = binding_header(hooks, "ab" * 32)
        self.assertIn("#define PROTOTYPE_TABLE_OFFSET 0x2500000", header)
        self.assertIn("#define PROTOTYPE_HOOK_COUNT 2", header)
        self.assertIn("PrototypeHookBinding", header)
        self.assertEqual(header.count("0xab"), 32)
        self.assertNotIn("GAME_PROBE", header)
        self.assertEqual(PROFILE, "0150524F54310000")

    def test_binding_names_slots_and_build_id_are_validated(self):
        base = {"table_offset": 0x2500000, "table_size": 16}
        for records in ([{"symbol": 'bad";abort();//', "slot": 0}],
                        [{"symbol": "9name", "slot": 0}],
                        [{"symbol": "valid", "slot": -1}],
                        [{"symbol": "valid", "slot": True}],
                        [{"symbol": "valid", "slot": 2}],
                        [{"symbol": "valid", "slot": 0}, {"symbol": "other", "slot": 0}],
                        [{"symbol": "valid", "slot": 0}, {"symbol": "valid", "slot": 1}]):
            with self.subTest(records=records), self.assertRaises(ValueError):
                binding_header(dict(base, hooks=records), "00" * 32)
        with self.assertRaises(ValueError):
            binding_header(dict(base, hooks=[]), "not a build id")

    def test_frontend_gates_guest_boot_on_import_and_maps_game_and_saves(self):
        result = frontend(FRONTEND)
        self.assertIn("prototype_set_error_callback(prototype_show_error)", result)
        self.assertIn("boot_env(0)", result)
        self.assertIn("if (!resources_ready) return 0;", result)
        self.assertIn("[[PrototypeImportViewController alloc] initWithReady:", result)
        self.assertIn("g_boot_ok = boot_env(1)", result)
        self.assertIn('setenv("PROTOTYPE_GAME_ROOT", romfs.fileSystemRepresentation, 1)', result)
        self.assertIn('setenv("PROTOTYPE_DOCUMENTS_ROOT", doc.fileSystemRepresentation, 1)', result)
        self.assertIn('setenv("PROTOTYPE_SAVE_ROOT", [doc stringByAppendingPathComponent:@"saves"]', result)
        self.assertIn('if (![fm fileExistsAtPath:config])', result)
        self.assertIn('@"dm/prototype.toml"', result)
        self.assertNotIn("LOCALIZATION", result)
        self.assertNotIn("CAPTURE", result)

    def test_platform_overlay_preserves_switch_gamepad_and_binds_metal_layer(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            host, ui = source / "dm/host", source / "dm/ios/Sources"
            host.mkdir(parents=True)
            ui.mkdir(parents=True)
            runtime = source / "runtime"
            runtime.mkdir()
            (runtime / "PrototypeVulkan.c").write_text("void adapter(void) {}")
            (host / "main.c").write_text("original")
            (host / "Makefile").write_text("SRC=main.c loader.c\nGFX=../gfx/vk/*.m\n")
            (host / "hle_hid.c").write_text("/* native input */\n")
            (host / "hle_svc.c").write_text("    mem_init();\n    if (svc_thread(num, regs)) return regs[0];\n")
            (ui / "main.m").write_text(FRONTEND)
            original_pad = b"/* Switch layout: right A, bottom B, top X, left Y */"
            (ui / "DmGamepad.m").write_bytes(original_pad)
            (ui / "front_ios.m").write_text('''    g_layer = (CAMetalLayer *)self.game.layer;
static void front_ios_audio_apply(void) { dm_audio_suspend(g_audio_bg || g_audio_intr); }
case 1: UIApplication.sharedApplication.idleTimerDisabled = NO;
case 2: dm_gfx_suspend(0);
''')
            profile = source / "profile"
            profile.mkdir()
            with patch("stage_ios.patch_main", side_effect=lambda data: data):
                patch_sources(source, profile, runtime)
            self.assertEqual((ui / "DmGamepad.m").read_bytes(), original_pad)
            front = (ui / "front_ios.m").read_text()
            self.assertIn("prototype_vk_set_metal_layer((__bridge void *)g_layer)", front)
            self.assertIn("prototype_vk_suspend(1)", front)
            self.assertIn("prototype_vk_suspend(0)", front)
            self.assertIn("prototype_audio_suspend(g_audio_bg || g_audio_intr)", front)
            self.assertIn("prototype_read_native_pad", (host / "hle_hid.c").read_text())
            self.assertIn("prototype_platform_svc(num, regs)", (host / "hle_svc.c").read_text())

    def test_embedded_section_hashes_cover_guest_and_signing_padding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = b"A" * 0x4000 + b"B" * 0x4000 + b"C" * 11
            (root / "main.patched.bin").write_bytes(raw)
            for patch_offset, extra in (("8000", ""), ("-4000", "# FILE_IMAGE_LEN 8000\n")):
                (root / "main.fixups.tsv").write_text("# TEXT_END 3000\n# PATCH_OFF " + patch_offset + "\n" + extra)
                result = embedded_sections(root)
                self.assertEqual(result["__TEXT,__dmmainx0"]["sha256"], hashlib.sha256(raw[:0x4000]).hexdigest())
                self.assertEqual(result["__DMmainW,__img"]["sha256"], hashlib.sha256(raw[0x4000:0x8000]).hexdigest())
                self.assertEqual(result["__TEXT,__dmmainx1"], {"size": 0x4000,
                    "sha256": hashlib.sha256(b"C" * 11 + bytes(0x4000 - 11)).hexdigest()})

    def test_versioned_overlay_requires_one_exact_anchor(self):
        for text in ("unknown", "anchor anchor"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                replace_once(text, "anchor", "replacement")


if __name__ == "__main__":
    unittest.main()
