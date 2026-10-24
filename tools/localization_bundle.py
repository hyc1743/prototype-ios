#!/usr/bin/env python3
"""Authenticate a private localization edit bundle for CI and runtime probes."""
from __future__ import annotations
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import zipfile

from ci_codegen import deterministic_zip, read_key, safe_path
from game_localization import pack, unpack

MAGIC = b'PROTOLOC1'
HEADER = struct.Struct('<9sQ32s12s')
MAX_BYTES = 64*1024*1024


def validate(data):
    if len(data) > MAX_BYTES:
        raise ValueError('Localization bundle too large')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if len(archive.namelist()) != 4 or set(archive.namelist()) != {'localization.json', 'fonts_latin.gfx', 'compatibility-report.json', 'CREDITS.txt'}:
            raise ValueError('Unexpected localization bundle members')
        if sum(info.file_size for info in archive.infolist()) > MAX_BYTES:
            raise ValueError('Expanded localization bundle too large')
        files = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(files['localization.json'])
    if manifest.get('schema') != 1 or manifest.get('language') != 'zh-Hans':
        raise ValueError('Unsupported localization manifest')
    seen = set()
    for item in manifest['files']:
        name = str(safe_path(item['path']))
        if name in seen or not name.endswith(('.p3d', '.p3d.rz')):
            raise ValueError('Unsafe or repeated localization destination')
        seen.add(name)
        if not re.fullmatch(r'\d\d(?:art|audio|movies|woi)\.rcf', item['archive']):
            raise ValueError('Invalid localization source archive')
        if not 0 < item['source_size'] <= MAX_BYTES or item['source_offset'] < 0:
            raise ValueError('Invalid source byte range')
        cursor, size = 0, item['unpacked_size']
        for change in item['changes']:
            payload = base64.b64decode(change['data'], validate=True)
            if not cursor <= change['offset'] <= change['offset']+change['remove'] <= item['unpacked_size']:
                raise ValueError('Overlapping or invalid localization edits')
            cursor = change['offset']+change['remove']
            size += len(payload)-change['remove']
        if (not 0 <= item['unpacked_size'] <= MAX_BYTES or size != item['output_size']
                or not 0 < size <= MAX_BYTES):
            raise ValueError('Invalid localization edit bounds')
        for key in ('source_sha256', 'output_sha256'):
            if not re.fullmatch('[0-9a-f]{64}', item[key]):
                raise ValueError('Invalid localization digest')
    font = files['fonts_latin.gfx']
    if (len(font) != manifest['font']['bytes'] or hashlib.sha256(font).hexdigest() != manifest['font']['sha256']
            or manifest['font']['path'] != 'art/hud/fonts_latin.gfx'):
        raise ValueError('Font identity differs')
    report = json.loads(files['compatibility-report.json'])
    if report.get('mismatches') or report.get('edited_files') != len(manifest['files']):
        raise ValueError('Localization compatibility report is incomplete')
    return manifest, files


def inspect_container(data):
    if len(data) < HEADER.size+16:
        raise ValueError('Localization container truncated')
    magic, size, digest, nonce = HEADER.unpack_from(data)
    if magic != MAGIC or size > MAX_BYTES or len(data) != HEADER.size+size+16:
        raise ValueError('Invalid localization container')
    return {'schema': 1, 'plaintext_bytes': size, 'plaintext_sha256': digest.hex(),
            'container_sha256': hashlib.sha256(data).hexdigest()}


def seal(folder, destination, key):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    data = deterministic_zip({name: (Path(folder)/name).read_bytes() for name in
                              ('localization.json', 'fonts_latin.gfx', 'compatibility-report.json', 'CREDITS.txt')})
    validate(data)
    nonce = os.urandom(12)
    header = HEADER.pack(MAGIC, len(data), hashlib.sha256(data).digest(), nonce)
    container = header+AESGCM(key).encrypt(nonce, data, header)
    Path(destination).write_bytes(container)
    report = inspect_container(container)
    Path(destination).with_suffix('.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf8')
    return report


def open_bundle(source, destination, key):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    raw = Path(source).read_bytes()
    info = inspect_container(raw)
    data = AESGCM(key).decrypt(HEADER.unpack_from(raw)[3], raw[HEADER.size:], raw[:HEADER.size])
    if hashlib.sha256(data).hexdigest() != info['plaintext_sha256']:
        raise ValueError('Localization plaintext digest differs')
    manifest, files = validate(data)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    for name, content in files.items():
        (destination/name).write_bytes(content)
    return {'language': manifest['language'], 'edits': len(manifest['files']), **info}


def apply(folder, game_root):
    folder, game_root = Path(folder), Path(game_root)
    manifest = json.loads((folder/'localization.json').read_bytes())
    sources = {}
    total_bytes = 0
    try:
        for index, edit in enumerate(manifest['files']):
            source = sources.setdefault(edit['archive'], None)
            if source is None:
                source = sources[edit['archive']] = (game_root/edit['archive']).open('rb')
            source.seek(edit['source_offset'])
            raw = source.read(edit['source_size'])
            if hashlib.sha256(raw).hexdigest() != edit['source_sha256']:
                raise ValueError('Game resource differs: '+edit['path'])
            original = unpack(raw)
            result, cursor = bytearray(), 0
            for change in edit['changes']:
                result.extend(original[cursor:change['offset']])
                result.extend(base64.b64decode(change['data']))
                cursor = change['offset']+change['remove']
            result.extend(original[cursor:])
            out = bytes(result)
            if hashlib.sha256(out).hexdigest() != edit['output_sha256']:
                raise ValueError('Localization output differs: '+edit['path'])
            target = game_root/safe_path(edit['path'])
            target.parent.mkdir(parents=True, exist_ok=True)
            data = pack(out)
            target.write_bytes(data)
            total_bytes += len(data)
            if index % 2000 == 0:
                print(f'Applied Chinese resources {index}/{len(manifest["files"])}', flush=True)
    finally:
        for source in sources.values():
            if source is not None:
                source.close()
    font = game_root/'art/hud/fonts_latin.gfx'
    font.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(folder/'fonts_latin.gfx', font)
    return {'files': len(manifest['files']), 'written_bytes': total_bytes+font.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('seal', 'open', 'apply'))
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--key-file', type=Path)
    args = parser.parse_args()
    if args.command == 'seal': result = seal(args.source, args.destination, read_key(args.key_file))
    elif args.command == 'open': result = open_bundle(args.source, args.destination, read_key(args.key_file))
    else: result = apply(args.source, args.destination)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
