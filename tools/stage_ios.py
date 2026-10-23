#!/usr/bin/env python3
"""Stage the pinned real guest, host adapters and signing-time embedded image."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from nro import Nro
from patch_libnx_host import patch_main
from prepare_nxnative import main as prepare
from rewrite_nro import main as rewrite, COMMIT, reviewed_sources
from patch_guest_hooks import main as patch_hooks

ROOT = Path(__file__).resolve().parents[1]
PROFILE = "0150524F54310000"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("Reviewed host source anchor changed: " + old[:80])
    return text.replace(old, new, 1)


def binding_header(hooks, build_id):
    if not isinstance(build_id, str) or not re.fullmatch(r"[0-9a-f]{64}", build_id):
        raise ValueError("Invalid expected guest build identity")
    rows = []
    slots = set()
    symbols = set()
    for hook in hooks["hooks"]:
        symbol, slot = hook["symbol"], hook["slot"]
        if (not isinstance(symbol, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", symbol)
                or type(slot) is not int or not 0 <= slot < hooks["table_size"] // 8
                or slot in slots or symbol in symbols):
            raise ValueError("Invalid or duplicate host binding")
        slots.add(slot)
        symbols.add(symbol)
        rows.append("    {" + json.dumps(symbol) + ", " + str(slot) + "},")
    build = ",".join("0x" + build_id[i:i+2] for i in range(0, 64, 2))
    return "\n".join([
        "/* Generated from verified offline hooks; do not edit. */", "#pragma once",
        "typedef struct { const char *symbol; unsigned slot; } PrototypeHookBinding;",
        f"#define PROTOTYPE_TABLE_OFFSET {hooks['table_offset']:#x}",
        f"#define PROTOTYPE_TABLE_SIZE {hooks['table_size']:#x}",
        f"#define PROTOTYPE_HOOK_COUNT {len(rows)}",
        f"static const unsigned char prototype_expected_build[32] = {{{build}}};",
        "static const PrototypeHookBinding prototype_hook_bindings[] = {", *rows, "};", ""])


def frontend(text):
    text = '#include "PrototypeLibnx.h"\n#import "PrototypeImportViewController.h"\n' + text
    text = text.replace('@"romfs"', '@"game"').replace('@"save"', '@"saves"')
    text = text.replace("Documents/romfs", "Documents/game").replace("<App>/romfs", "<App>/game")
    text = replace_once(text, 'for (NSString *d in @[@"saves", @"temp"])',
                        'for (NSString *d in @[@"saves", @"temp", @"game", @"ResourcePacks"])')
    text = replace_once(text, 'static int boot_env(void) {', 'static int boot_env(int resources_ready) {')
    text = replace_once(text, 'for (NSString *d in @[@"game", @"temp"])',
                        'for (NSString *d in @[@"game", @"temp", @"ResourcePacks"])')
    text = replace_once(text, '    for (NSString *d in @[@"saves",',
                        '    static int initialized;\n    if (!initialized) {\n    initialized = 1;\n    for (NSString *d in @[@"saves",')
    text = replace_once(text, '    // Keep game files and scratch data out of device backups', '''    NSString *config = [doc stringByAppendingPathComponent:@"prototype.toml"];
    if (![fm fileExistsAtPath:config]) {
        NSError *error = nil;
        NSString *bundledConfig = [res stringByAppendingPathComponent:@"dm/prototype.toml"];
        if (![fm copyItemAtPath:bundledConfig toPath:config error:&error])
            fprintf(stderr, "boot: default configuration copy failed: %s\\n", error.localizedDescription.UTF8String);
    }
    // Keep game files and scratch data out of device backups''')
    text = replace_once(text, '    NSString *romfs = romfs_locate();',
                        '    }\n    if (!resources_ready) return 0;\n    NSString *romfs = romfs_locate();')
    text = replace_once(text, 'setenv("DM_ROMFS", romfs.fileSystemRepresentation, 0);',
                        'setenv("DM_ROMFS", romfs.fileSystemRepresentation, 0);\n'
                        '    setenv("PROTOTYPE_GAME_ROOT", romfs.fileSystemRepresentation, 1);\n'
                        '    setenv("PROTOTYPE_DOCUMENTS_ROOT", doc.fileSystemRepresentation, 1);\n'
                        '    setenv("PROTOTYPE_SAVE_ROOT", [doc stringByAppendingPathComponent:@"saves"].fileSystemRepresentation, 1);')
    text = replace_once(text, 'setvbuf(stderr, NULL, _IOLBF, 0);',
                        'setvbuf(stderr, NULL, _IOLBF, 0);\n    dup2(fileno(stderr), STDOUT_FILENO);')
    error_ui = '''static void prototype_show_error(const char *message) {
    @autoreleasepool {
        NSString *text = message ? [NSString stringWithUTF8String:message] : @"Game startup failed.";
        dispatch_async(dispatch_get_main_queue(), ^{
            UIViewController *controller = nil;
            for (UIScene *scene in UIApplication.sharedApplication.connectedScenes) {
                if (![scene isKindOfClass:UIWindowScene.class]) continue;
                for (UIWindow *window in ((UIWindowScene *)scene).windows)
                    if (window.isKeyWindow) controller = window.rootViewController;
            }
            while (controller.presentedViewController) controller = controller.presentedViewController;
            if (!controller) return;
            UIAlertController *alert = [UIAlertController alertControllerWithTitle:@"Prototype could not start"
                message:[text stringByAppendingString:@"\\n\\nCheck Documents/game and Documents/log.txt."]
                preferredStyle:UIAlertControllerStyleAlert];
            [alert addAction:[UIAlertAction actionWithTitle:@"OK" style:UIAlertActionStyleDefault handler:nil]];
            [controller presentViewController:alert animated:YES completion:nil];
        });
    }
}
'''
    text = replace_once(text, 'int main(int argc, char **argv) {', error_ui + '\nint main(int argc, char **argv) {')
    text = replace_once(text, '@autoreleasepool { g_boot_ok = boot_env(); }',
                        '@autoreleasepool { prototype_set_error_callback(prototype_show_error); boot_env(0); }')
    text = replace_once(text, '    self.window.rootViewController = front_ios_root();', '''    __weak UIWindow *window = self.window;
    self.window.rootViewController = [[PrototypeImportViewController alloc] initWithReady:^{
        g_boot_ok = boot_env(1);
        if (!g_boot_ok || !window) return NO;
        window.rootViewController = front_ios_root();
        front_ios_focus_event(3);
        return YES;
    }];''')
    return text


def embedded_sections(hooked):
    """Bind the actual three signing-time guest sections to their exact bytes."""
    raw = (hooked / "main.patched.bin").read_bytes()
    fields = dict(line[2:].split() for line in (hooked / "main.fixups.tsv").read_text().splitlines())
    page = 0x4000
    text_end = (int(fields["TEXT_END"], 16) + page - 1) & ~(page - 1)
    patch = int(fields["PATCH_OFF"], 16)
    split = int(fields["FILE_IMAGE_LEN"], 16) if patch < 0 else patch
    end = (len(raw) + page - 1) & ~(page - 1)
    if not 0 < text_end <= split < end:
        raise ValueError("Invalid embedded guest section layout")
    result = {}
    for name, lo, hi in (("__TEXT,__dmmainx0", 0, text_end),
                         ("__DMmainW,__img", text_end, split),
                         ("__TEXT,__dmmainx1", split, end)):
        content = raw[lo:min(hi, len(raw))] + bytes(max(0, hi - len(raw)))
        result[name] = {"size": hi - lo, "sha256": hashlib.sha256(content).hexdigest()}
    return result


def patch_sources(source, profile, runtime):
    host = source / "dm/host"
    (host / "main.c").write_bytes(patch_main((host / "main.c").read_bytes()))
    for path in sorted(runtime.iterdir()):
        if path.suffix in {".c", ".m", ".h", ".inc"}:
            shutil.copyfile(path, host / path.name)
    make = (host / "Makefile").read_text(encoding="utf8")
    names = " ".join(path.name for path in sorted(runtime.iterdir()) if path.suffix in {".c", ".m"})
    make = replace_once(make, "SRC=main.c loader.c", f"SRC={names} main.c loader.c")
    make = replace_once(make, "../gfx/vk/*.m", "")
    (host / "Makefile").write_text(make, encoding="utf8")
    svc = host / "hle_svc.c"
    svc.write_text(replace_once(svc.read_text(encoding="utf8"),
        "    mem_init();\n    if (svc_thread(num, regs)) return regs[0];",
        "    mem_init();\n    extern int prototype_platform_svc(int, uint64_t *);\n"
        "    if (prototype_platform_svc(num, regs)) return regs[0];\n"
        "    if (svc_thread(num, regs)) return regs[0];"), encoding="utf8")
    hid = (host / "hle_hid.c").read_text(encoding="utf8")
    hid += '''
/* Prototype adapter: sample the real virtual/physical controller merge and latches. */
void prototype_read_native_pad(uint64_t *buttons, int32_t axes[4]) {
    NpadState state;
    uint32_t id = connected_id();
    fill(&state, id, style_of(id));
    *buttons = state.buttons;
    axes[0] = state.lx; axes[1] = state.ly;
    axes[2] = state.rx; axes[3] = state.ry;
}
'''
    (host / "hle_hid.c").write_text(hid, encoding="utf8")
    # The upstream front end manages the touch/controller UI and iOS lifecycle.
    ui = source / "dm/ios/Sources"
    (ui / "main.m").write_text(frontend((ui / "main.m").read_text(encoding="utf8")), encoding="utf8")
    front = (ui / "front_ios.m").read_text(encoding="utf8")
    front = '#include "PrototypeVulkan.h"\n#include "PrototypeAudio.h"\n' + front
    front = front.replace('Put the RomFS folder into this App\'s Documents as \\"romfs\\"',
                          'Put the original game folder, containing default.xex, into Documents as \\"game\\"')
    front = replace_once(front, "    g_layer = (CAMetalLayer *)self.game.layer;",
        "    g_layer = (CAMetalLayer *)self.game.layer;\n    prototype_vk_set_metal_layer((__bridge void *)g_layer);")
    front = replace_once(front, "static void front_ios_audio_apply(void) { dm_audio_suspend(g_audio_bg || g_audio_intr); }",
                         "static void front_ios_audio_apply(void) { dm_audio_suspend(g_audio_bg || g_audio_intr); prototype_audio_suspend(g_audio_bg || g_audio_intr); }")
    front = replace_once(front, "case 1: UIApplication.sharedApplication.idleTimerDisabled = NO;",
                         "case 1: prototype_vk_suspend(1); UIApplication.sharedApplication.idleTimerDisabled = NO;")
    front = replace_once(front, "case 2: dm_gfx_suspend(0);", "case 2: prototype_vk_suspend(0); dm_gfx_suspend(0);")
    (ui / "front_ios.m").write_text(front, encoding="utf8")
    for path in (ROOT / "ios/profile").iterdir():
        shutil.copyfile(path, profile / path.name)


def stage(args):
    original, out = args.nro.resolve(), args.out.resolve()
    upstream = args.nxnative_root.resolve()
    if out.is_relative_to(upstream) or upstream.is_relative_to(ROOT / "ios/runtime"):
        raise ValueError("Staging output must be separate from pinned upstream")
    reviewed_sources(upstream)
    source = out / "work/nxnative-staged"
    if source.exists() or (out / "ios").exists():
        raise ValueError("Staging directories already exist")
    out.mkdir(parents=True, exist_ok=True)
    shutil.copytree(upstream, source, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    image = Nro(original.read_bytes())
    prepared, rewritten, hooked = out / "prepared", out / "rewritten", out / "rewritten-bindings"
    if prepare([str(original), "--out", str(prepared)]) != 0:
        raise ValueError("NRO preparation failed")
    if rewrite([str(original), "--prepared", str(prepared), "--nxnative-root", str(upstream), "--out", str(rewritten)]) != 0:
        raise ValueError("Guest rewriting failed")
    spec = json.loads((ROOT / "ios/runtime/guest-hooks.json").read_text(encoding="utf8"))
    if spec["source_nro_sha256"] != image.sha256:
        raise ValueError("Hook profile is bound to a different guest")
    combined = out / "guest-hooks.json"
    combined.write_text(json.dumps(spec, indent=2), encoding="utf8")
    patch_hooks(["--nro", str(original), "--rewritten", str(rewritten / "rewritten/main.patched.bin"),
                 "--provenance", str(rewritten / "provenance.json"), "--boundaries", str(prepared / "main.function-boundaries.json"),
                 "--hooks", str(combined), "--out", str(hooked)])
    hooks = json.loads((hooked / "hooks.json").read_text(encoding="utf8"))
    profile = source / "dm/games" / PROFILE
    profile.mkdir(parents=True)
    patch_sources(source, profile, ROOT / "ios/runtime")
    (profile / "PrototypeHookTable.h").write_text(binding_header(hooks, image.build_id), encoding="utf8")
    ios = out / "ios"
    shutil.copytree(source / "dm/ios/Sources", ios / "Sources")
    for path in (ROOT / "ios/import").iterdir():
        if path.suffix in {".h", ".m"}:
            shutil.copyfile(path, ios / "Sources" / path.name)
    shutil.copyfile(ROOT / "ios/project.yml", ios / "project.yml")
    gen = ios / "Gen"
    dm = gen / "dm"
    dm.mkdir(parents=True)
    shutil.copyfile(ROOT / "ios/resources/PrototypeData.manifest.json", dm / "PrototypeData.manifest.json")
    shutil.copyfile(ROOT / "ios/config/prototype.toml", dm / "prototype.toml")
    for path in hooked.glob("*.tsv"):
        shutil.copyfile(path, dm / path.name)
    for name in ("shader_map.tsv", "pso_manifest.tsv"):
        shutil.copyfile(profile / name, dm / name)
    notices = dm / "notices"
    notices.mkdir()
    shutil.copyfile(upstream / "LICENSE", notices / "nxnative-MIT.txt")
    environment = dict(os.environ, GAME=PROFILE, DM_GAME=PROFILE)
    environment.pop("DM_GAMES_FIRST", None)
    environment.pop("DM_GAMES_DIR", None)
    environment.pop("PROTOTYPE_GUEST_KEY", None)
    embed = subprocess.run([sys.executable, str(upstream / "dm/ios/tools/gen_embed.py"), str(hooked), str(gen / "embed.S"), "main"],
                           capture_output=True, text=True, check=True, env=environment)
    if not args.prepare_only:
        mvk = Path(os.environ["MOLTENVK_ROOT"]).resolve()
        library = mvk / "static/MoltenVK.xcframework/ios-arm64/libMoltenVK.a"
        if not library.is_file():
            raise ValueError("MOLTENVK_ROOT does not contain the iOS static library")
        config = subprocess.check_output([sys.executable, str(upstream / "dm/ios/tools/gen_build.py"),
                                          str(source / "dm/host"), str(gen)], text=True, env=environment)
        config = replace_once(config, 'HEADER_SEARCH_PATHS = $(inherited) ',
                              'HEADER_SEARCH_PATHS = $(inherited) "' + str(mvk / "include") + '" ')
        config += 'OTHER_LDFLAGS = $(inherited) $(DM_FRAMEWORKS) ' + embed.stdout.strip()
        config += ' "' + str(library) + '" -lc++ -framework Metal -framework QuartzCore -framework IOSurface -framework Foundation -framework AVFAudio -framework UIKit -framework CoreMotion -framework CoreText\n'
        (gen / "dm.xcconfig").write_text(config, encoding="utf8")
    report = {"guest_sha256": image.sha256, "guest_embedded": True, "source_commit": COMMIT,
              "embedded_sections": embedded_sections(hooked),
              "project": "ios/project.yml", "host_root": "work/nxnative-staged/dm/host",
              "patched_dir": "rewritten-bindings", "hook_count": len(hooks["hooks"]),
              "patched_sha256": sha(hooked / "main.patched.bin"),
              "assembly_sha256": sha(gen / "embed.S"), "project_sources_generated": not args.prepare_only,
              "compiled": False, "runtime_verified": False}
    (out / "stage-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
    print(json.dumps(report, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nxnative-root", type=Path, required=True)
    parser.add_argument("--nro", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true", help="Generate guest/source inputs without macOS make or Xcode")
    stage(parser.parse_args(argv))


if __name__ == "__main__":
    main()
