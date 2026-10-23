#!/usr/bin/env python3
"""Stage the exact reviewed nxnative touch/GameController frontend for SDL."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = "677345b0933f5f8754aeec3125d14df32683ca97"
SOURCE_SHA256 = "676766a42a2476310b86ee34dfc90aad7e0cada0f04a8a527958abe3b6aef342"
INCLUDES = '#include "dm.h"\n#include "dm_input.h"\n#include "front.h"\n#include "game_profile.h"'
DIAMOND = 'switch (b) { case B_A: return CGPointMake(c.x + g, c.y); case B_B: return CGPointMake(c.x, c.y + g); case B_X: return CGPointMake(c.x, c.y - g); default: return CGPointMake(c.x - g, c.y); }'
XBOX_DIAMOND = 'switch (b) { case B_A: return CGPointMake(c.x, c.y + g); case B_B: return CGPointMake(c.x + g, c.y); case B_X: return CGPointMake(c.x - g, c.y); default: return CGPointMake(c.x, c.y - g); }'
PHYSICAL = 'K(g.buttonB, B_A); K(g.buttonA, B_B); K(g.buttonY, B_X); K(g.buttonX, B_Y);   // by position: right A, bottom B, top X, left Y'
XBOX_PHYSICAL = 'K(g.buttonA, B_A); K(g.buttonB, B_B); K(g.buttonX, B_X); K(g.buttonY, B_Y);   // by position: bottom A, right B, left X, top Y'


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("Pinned gamepad source anchor changed: " + old[:100])
    return text.replace(old, new, 1)


def adapt_controls(raw):
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError("Gamepad source differs from the reviewed nxnative SHA-256")
    text = raw.decode("utf8")
    text = replace_once(text, INCLUDES, '#include "PrototypeNativePad.h"')
    text = replace_once(text, DIAMOND, XBOX_DIAMOND)
    text = replace_once(text, PHYSICAL, XBOX_PHYSICAL)
    text = text.replace("Nintendo button positions", "Xbox button positions")
    text = text.replace("A (right) B (bottom) X (top) Y (left)", "A (bottom) B (right) X (left) Y (top)")
    text = text.replace("bottom = B, right = A, left = Y, top = X (same positions as on a Switch controller)",
                        "bottom = A, right = B, left = X, top = Y (Xbox layout)")
    return text.encode("utf8")


def stage_controls(raw_pinned_dm_gamepad, output):
    raw = raw_pinned_dm_gamepad if isinstance(raw_pinned_dm_gamepad, bytes) else Path(raw_pinned_dm_gamepad).read_bytes()
    adapted = adapt_controls(raw)
    files = {"DmGamepad.m": adapted}
    for name in ("PrototypeNativePad.h", "PrototypeNativePad.m"):
        files[name] = (ROOT / "ios/source-port" / name).read_bytes()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    for name, content in files.items():
        (output / name).write_bytes(content)
    return {"nxnative_commit": SOURCE_COMMIT, "source_sha256": SOURCE_SHA256,
            "layout": "Xbox", "bridge": "SDL virtual gamepad using merged touch and GameController input",
            "runtime_verified": False,
            "files": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(stage_controls(args.raw, args.out), indent=2))


if __name__ == "__main__":
    main()
