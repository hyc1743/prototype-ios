#!/usr/bin/env python3
"""Seal the pinned generated Prototype C++ input and authenticate CI extraction."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import sys
import tempfile
import zipfile

MAGIC = b"PROTOGEN1"
HEADER = struct.Struct("<9sQ32s12s")
PINNED_ZIP_SHA256 = "ccd3b9cf9a1c7c6b7cbef291252e9f65c18acf061c6eec676df8f40ea665a2e0"
XEX_SHA256 = "fda520ae3721eaddede1fd773af3ad2f6099a5ae2981849fb07ed4c7a8b53afe"
TOOL_ZIP_SHA256 = "ad4485da853fbe932e9f5dbb643f9b5fe85dc73775ebbad4df6879fe6df0c11b"
EXPECTED_SOURCE_COUNT = 157
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
MANIFEST_NAME = "codegen-manifest.json"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or "\0" in value:
        raise ValueError("Unsafe codegen archive path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value != path.as_posix() or len(value) > 240:
        raise ValueError("Unsafe codegen archive path")
    if any(p in {"", "."} or p.endswith((".", " ")) for p in path.parts):
        raise ValueError("Unsafe codegen archive path")
    if any(p.split(".", 1)[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]} for p in path.parts):
        raise ValueError("Unsafe codegen archive path")
    return path


def declared_sources(data, expected_count=EXPECTED_SOURCE_COUNT):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("sources.cmake must be UTF-8") from None
    text = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    match = re.fullmatch(r"\s*set\(GENERATED_SOURCES\s+(.*?)\s*\)\s*", text, re.DOTALL)
    if not match:
        raise ValueError("Unexpected sources.cmake syntax")
    names = []
    for entry in match.group(1).split():
        prefix = "${CMAKE_CURRENT_LIST_DIR}/"
        if not entry.startswith(prefix):
            raise ValueError("Codegen sources must use CMAKE_CURRENT_LIST_DIR")
        name = entry[len(prefix):]
        if safe_path(name).suffix != ".cpp":
            raise ValueError("Codegen source entry is not C++")
        names.append(name)
    if len(names) != expected_count or len({x.casefold() for x in names}) != len(names):
        raise ValueError("Unexpected or repeated codegen source count")
    return names


def deterministic_zip(entries):
    """Create a reproducible archive from a mapping; production input is pinned later."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(entries.items()):
            safe_path(name)
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return output.getvalue()


def build_archive(directory, *, expected_count=EXPECTED_SOURCE_COUNT):
    root = Path(directory).resolve()
    entries = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Codegen input cannot contain symlinks")
        if not path.is_file():
            continue
        name = path.relative_to(root).as_posix()
        safe_path(name)
        if path.suffix not in {".cpp", ".h"} and name != "sources.cmake":
            if path.name in {"codegen.build.stamp", "codegen.d", "codegen.partition.json", "codegen.stamp"}:
                continue
            raise ValueError("Unreviewed generated file: " + name)
        entries[name] = path.read_bytes()
    names = declared_sources(entries.get("sources.cmake", b""), expected_count)
    if set(names) != {n for n in entries if n.endswith(".cpp")}:
        raise ValueError("CMake source declarations differ from codegen files")
    manifest = {"schema": 1, "project": "prototype", "original_xex_sha256": XEX_SHA256,
                "official_tool": {"repository": "rexglue/rexglue-sdk", "tag": "v0.10.0", "archive_sha256": TOOL_ZIP_SHA256},
                "source_count": len(names), "uncompressed_bytes": sum(len(d) for d in entries.values()),
                "files": [{"path": n, "bytes": len(d), "sha256": sha256(d)} for n, d in sorted(entries.items())]}
    entries[MANIFEST_NAME] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    packed = deterministic_zip(entries)
    validate_archive(packed, expected_count=expected_count)
    return packed, manifest


def validate_archive(data, *, expected_count=EXPECTED_SOURCE_COUNT):
    """Validate complete contents without writing; fixtures can select a small count."""
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("Codegen ZIP is oversized")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > 1024 or sum(i.file_size for i in infos) > MAX_EXPANDED_BYTES:
                raise ValueError("Codegen ZIP has invalid expanded size")
            seen, files = set(), {}
            for info in infos:
                if info.orig_filename != info.filename:
                    raise ValueError("Unsafe normalized codegen archive path")
                safe_path(info.filename)
                if info.filename.casefold() in seen:
                    raise ValueError("Repeated codegen ZIP path")
                seen.add(info.filename.casefold())
                mode = info.external_attr >> 16
                if info.is_dir() or stat.S_IFMT(mode) != stat.S_IFREG or info.flag_bits & 1:
                    raise ValueError("Codegen ZIP entries must be regular unencrypted files")
                files[info.filename] = archive.read(info)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError):
        raise ValueError("Invalid codegen ZIP") from None
    try:
        manifest = json.loads(files.pop(MANIFEST_NAME))
    except (KeyError, ValueError, UnicodeDecodeError):
        raise ValueError("Missing or invalid codegen manifest") from None
    if (not isinstance(manifest, dict) or manifest.get("schema") != 1 or manifest.get("project") != "prototype" or
            manifest.get("original_xex_sha256") != XEX_SHA256 or
            manifest.get("official_tool") != {"repository": "rexglue/rexglue-sdk", "tag": "v0.10.0", "archive_sha256": TOOL_ZIP_SHA256}):
        raise ValueError("Codegen provenance differs from reviewed inputs")
    inventory = manifest.get("files")
    if not isinstance(inventory, list):
        raise ValueError("Codegen manifest requires exact file inventory")
    inventory_names = []
    for item in inventory:
        if not isinstance(item, dict):
            raise ValueError("Invalid codegen inventory entry")
        name = item.get("path")
        safe_path(name)
        inventory_names.append(name)
        if name not in files or item.get("bytes") != len(files[name]) or item.get("sha256") != sha256(files[name]):
            raise ValueError("Codegen file does not match inventory: " + name)
        if PurePosixPath(name).suffix not in {".cpp", ".h"} and name != "sources.cmake":
            raise ValueError("Unreviewed codegen inventory file")
    if len(set(inventory_names)) != len(inventory_names) or set(inventory_names) != set(files):
        raise ValueError("Codegen inventory does not cover exact ZIP contents")
    sources = declared_sources(files.get("sources.cmake", b""), expected_count)
    if (set(sources) != {n for n in files if n.endswith(".cpp")} or
            manifest.get("source_count") != len(sources) or
            manifest.get("uncompressed_bytes") != sum(len(d) for d in files.values())):
        raise ValueError("Codegen source list or expanded inventory size differs")
    return manifest, files


def inspect_container(data):
    if len(data) < HEADER.size + 16:
        raise ValueError("Codegen container is truncated")
    magic, size, digest, _ = HEADER.unpack_from(data)
    if magic != MAGIC or size > MAX_ARCHIVE_BYTES or len(data) != HEADER.size + size + 16:
        raise ValueError("Unsupported codegen container")
    if digest.hex() != PINNED_ZIP_SHA256:
        raise ValueError("Codegen container does not identify the pinned ZIP")
    return {"version": 1, "plaintext_zip_bytes": size, "plaintext_zip_sha256": digest.hex(),
            "container_bytes": len(data), "container_sha256": sha256(data)}


def seal(data, key):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if len(key) != 32 or sha256(data) != PINNED_ZIP_SHA256:
        raise ValueError("Sealing requires the pinned codegen ZIP and a 32-byte key")
    validate_archive(data)
    nonce = os.urandom(12)
    header = HEADER.pack(MAGIC, len(data), bytes.fromhex(PINNED_ZIP_SHA256), nonce)
    return header + AESGCM(key).encrypt(nonce, data, header)


def open_container(data, key):
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    inspect_container(data)
    if len(key) != 32:
        raise ValueError("Codegen key must be 32 bytes")
    try:
        plain = AESGCM(key).decrypt(HEADER.unpack_from(data)[3], data[HEADER.size:], data[:HEADER.size])
    except InvalidTag:
        raise ValueError("Codegen container authentication failed") from None
    if sha256(plain) != PINNED_ZIP_SHA256:
        raise ValueError("Decrypted codegen ZIP digest mismatch")
    validate_archive(plain)
    return plain


def extract_archive(data, destination, *, expected_count=EXPECTED_SOURCE_COUNT):
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError("Codegen destination already exists")
    manifest, files = validate_archive(data, expected_count=expected_count)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if any(p.is_symlink() for p in [destination.parent, *destination.parent.parents]):
        raise ValueError("Codegen destination parents cannot be symlinks")
    with tempfile.TemporaryDirectory(prefix=destination.name + ".unlock-", dir=destination.parent) as temporary:
        stage = Path(temporary)
        for name, contents in files.items():
            path = stage.joinpath(*safe_path(name).parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
        (stage / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if destination.exists():
            raise ValueError("Codegen destination appeared during extraction")
        stage.replace(destination)
    return manifest


def read_key(path=None):
    value = Path(path).read_text(encoding="ascii") if path is not None else os.environ.get("PROTOTYPE_CODEGEN_KEY", "")
    try:
        key = bytes.fromhex(value.strip())
    except ValueError:
        raise ValueError("Codegen key requires 64 hexadecimal characters") from None
    if len(key) != 32:
        raise ValueError("Codegen key requires 64 hexadecimal characters")
    return key


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    pack = commands.add_parser("pack")
    pack.add_argument("source", type=Path)
    pack.add_argument("destination", type=Path)
    create = commands.add_parser("seal")
    create.add_argument("source", type=Path)
    create.add_argument("destination", type=Path)
    create.add_argument("--key-file", type=Path, default=Path(".tools/private/prototype-codegen.key"))
    create.add_argument("--manifest", type=Path)
    unlock = commands.add_parser("open")
    unlock.add_argument("source", type=Path)
    unlock.add_argument("destination", type=Path)
    unlock.add_argument("--key-file", type=Path)
    args = parser.parse_args(argv)
    if args.destination.exists():
        raise ValueError("Destination already exists")
    if args.command == "pack":
        data, manifest = build_archive(args.source)
        if sha256(data) != PINNED_ZIP_SHA256:
            raise ValueError("Generated ZIP differs from pinned output")
        args.destination.parent.mkdir(parents=True, exist_ok=True)
        args.destination.write_bytes(data)
        report = {"plaintext_zip_bytes": len(data), "plaintext_zip_sha256": sha256(data), "source_count": manifest["source_count"]}
    elif args.command == "seal":
        data = args.source.read_bytes()
        if sha256(data) != PINNED_ZIP_SHA256:
            raise ValueError("Only the pinned codegen ZIP may be sealed")
        manifest, _ = validate_archive(data)
        if args.key_file.exists():
            key = read_key(args.key_file)
        else:
            key = os.urandom(32)
            args.key_file.parent.mkdir(parents=True, exist_ok=True)
            with args.key_file.open("x", encoding="ascii") as out:
                out.write(key.hex() + "\n")
            args.key_file.chmod(0o600)
        packed = seal(data, key)
        args.destination.parent.mkdir(parents=True, exist_ok=True)
        args.destination.write_bytes(packed)
        report = inspect_container(packed)
        if args.manifest:
            args.manifest.parent.mkdir(parents=True, exist_ok=True)
            args.manifest.write_text(json.dumps({**report, **manifest}, indent=2) + "\n", encoding="utf-8")
    else:
        plain = open_container(args.source.read_bytes(), read_key(args.key_file))
        manifest = extract_archive(plain, args.destination)
        report = {"plaintext_zip_bytes": len(plain), "plaintext_zip_sha256": sha256(plain), "source_count": manifest["source_count"], "files": len(manifest["files"])}
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print(str(error) if isinstance(error, ValueError) else type(error).__name__, file=sys.stderr)
        sys.exit(1)
