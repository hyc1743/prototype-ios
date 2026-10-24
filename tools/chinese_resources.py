#!/usr/bin/env python3
"""Build console-compatible text-only edits from player-supplied PC Chinese files."""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import struct
import zlib

from game_localization import (MAGIC, BIBLE, TextBible, bibles, chunk, english_map, pack, path_key,
                               replace_english, u32, unpack)
from swf_fonts import adapt_font_library


class GameArchives:
    def __init__(self, root):
        self.root = Path(root)
        self.members = {}
        for archive in sorted(self.root.glob('*.rcf')):
            with archive.open('rb') as source:
                header = source.read(60)
                endian = '>' if header[34] else '<'
                eo, el, mo, ml, _, count = struct.unpack_from(endian+'6I', header, 36)
                if el != count*12 or count > 100000:
                    raise ValueError('Invalid archive index')
                source.seek(eo)
                entries = {key: (at, size) for key, at, size in struct.iter_unpack(endian+'III', source.read(el))}
                source.seek(mo)
                metadata = source.read(ml)
                pos = 8
                for _ in range(count):
                    size = u32(metadata, pos+12)
                    name = metadata[pos+16:pos+16+size].rstrip(b'\0').decode('ascii')
                    pos += 16+size+3
                    at, size = entries[path_key(name)]
                    self.members[name.lstrip('\\').replace('\\', '/').lower()] = {
                        'archive': archive.name, 'source_offset': at, 'source_size': size}

    def find(self, name):
        name = name.lower()
        for candidate in (name, name+'.rz', name.removesuffix('.rz')):
            if candidate in self.members:
                return candidate, self.members[candidate]
        return None, None

    def read(self, entry):
        with (self.root/entry['archive']).open('rb') as source:
            source.seek(entry['source_offset'])
            result = source.read(entry['source_size'])
        if len(result) != entry['source_size']:
            raise ValueError('Truncated source resource')
        return result


def dialogue(data):
    endian = '<' if u32(data, 0) == MAGIC else '>'
    _, header, total = chunk(data, 0, endian)
    if total != len(data):
        raise ValueError('Dialogue file length differs')
    pos = header
    while pos < total:
        _, _, size = chunk(data, pos, endian)
        marker = data.find(b'AudioDialogueSubtitle\0', pos+12, pos+size)
        if marker >= 0:
            name_at = marker + len(b'AudioDialogueSubtitle\0') + 4
            length = u32(data, name_at, endian)
            name = data[name_at+4:name_at+4+length].decode('utf8')
            language = b'\x01'+struct.pack(endian+'I', 7)+b'english\0'
            start = data.find(language, name_at+4+length+1, pos+size)
            if start < 0:
                raise ValueError('Dialogue has no English slot')
            length_at = start+len(language)
            length = u32(data, length_at, endian)
            end = length_at+4+length
            if end >= pos+size or data[end] != 0:
                raise ValueError('Dialogue text leaves its owning chunk')
            text = data[length_at+4:end].decode('utf8')
            return {'name': name, 'text': text, 'length_at': length_at, 'end': end,
                    'owner': pos, 'endian': endian}
        pos += size
    raise ValueError('Dialogue subtitle object not found')


def replace_dialogue(data, text):
    found = dialogue(data)
    raw = text.encode('utf8')
    start, end, endian = found['length_at'], found['end'], found['endian']
    out = bytearray(data[:start]+struct.pack(endian+'I', len(raw))+raw+data[end:])
    delta = len(out)-len(data)
    for at in (8, found['owner']+4, found['owner']+8):
        struct.pack_into(endian+'I', out, at, u32(data, at, endian)+delta)
    result = bytes(out)
    if dialogue(result)['text'] != text:
        raise ValueError('Dialogue replacement did not round-trip')
    return result


def button_map(original):
    symbols = {'00': 'DPR', '01': 'BJU', '02': 'BGR', '03': 'BAT', '04': 'BSP',
               '05': 'DPA', '06': 'ALE', '07': 'ARI', '08': 'BSE', '10': 'BL1',
               '11': 'BR1', '12': 'BL2', '13': 'BR2', '14': 'DPU', '15': 'DPD',
               '16': 'DPL', '17': 'BR3'}
    result = {number: original[key+'_SYMBOL'] for number, key in symbols.items()}
    result['09'] = '\u00b8'  # Console pause glyph, present in the original tutorials.
    return result


def normalize(text, buttons):
    text = re.sub(r'%0:BUTTON(\d{2})', lambda m: buttons.get(m[1], '[按键]'), text)
    # Keep unit suffixes outside the formatter type name.
    return re.sub(r'(%\d+:NUMBER)(MB|EP|X)', r'\1 \2', text)


def text_replacements(original, donor, buttons):
    donor = dict(donor)
    if '1_1_CAM_RESET_TUTORIAL_WIN32_KEYB' in donor:
        donor['1_1_CAM_RESET_TUTORIAL'] = donor['1_1_CAM_RESET_TUTORIAL_WIN32_KEYB']
    if 'AutoSaveWarningWin32' in donor:
        donor['AutoSaveWarningXenon'] = donor['AutoSaveWarningWin32']
    missing = original.keys()-donor.keys()
    if missing:
        raise ValueError('Chinese text lacks keys: '+', '.join(sorted(missing)[:8]))
    return {key: normalize(donor[key], buttons) for key in original}


def make_edit(name, entry, packed, before, after):
    # Only the root length and one subtitle/table chunk differ. In particular,
    # a table near the end of a scene must not duplicate its preceding meshes.
    endian = '<' if u32(before, 0) == MAGIC else '>'
    changes = [{'offset': 8, 'remove': 4, 'data': base64.b64encode(after[8:12]).decode()}]
    if name.startswith('audio/'):
        owner = dialogue(before)['owner']
        old_size, new_size = u32(before, owner+8, endian), u32(after, owner+8, endian)
        changes.append({'offset': owner, 'remove': old_size,
                        'data': base64.b64encode(after[owner:owner+new_size]).decode()})
    else:
        pos = 12
        while pos < len(before):
            kind, _, size = chunk(before, pos, endian)
            if kind == BIBLE and TextBible.parse(before[pos:pos+size]).language == 'english':
                new_size = u32(after, pos+8, endian)
                changes.append({'offset': pos, 'remove': size,
                                'data': base64.b64encode(after[pos:pos+new_size]).decode()})
                break
            pos += size
    rebuilt, cursor = bytearray(), 0
    for change in changes:
        rebuilt.extend(before[cursor:change['offset']])
        rebuilt.extend(base64.b64decode(change['data']))
        cursor = change['offset']+change['remove']
    rebuilt.extend(before[cursor:])
    assert rebuilt == after
    return {'path': name.removesuffix('.rz')+'.rz', **entry, 'source_sha256': hashlib.sha256(packed).hexdigest(),
            'unpacked_size': len(before), 'changes': changes, 'output_size': len(after),
            'output_sha256': hashlib.sha256(after).hexdigest()}


def build(game_root, chinese_root, output):
    game, chinese_root, output = GameArchives(game_root), Path(chinese_root), Path(output)
    if output.exists():
        raise ValueError('Chinese bundle output must be a new directory')
    _, menu_entry = game.find('art/hud/textbible_english.p3d.rz')
    original_menu = english_map(game.read(menu_entry))
    buttons = button_map(original_menu)
    donor_menu = english_map((chinese_root/'art/hud/fe_textbible.p3d.rz').read_bytes())
    donors = {p.relative_to(chinese_root).as_posix().lower(): p
              for p in chinese_root.rglob('*') if p.is_file() and p.name.lower().endswith(('.p3d', '.p3d.rz'))}
    donors['art/hud/textbible_english.p3d.rz'] = chinese_root/'art/hud/fe_textbible.p3d.rz'
    edits, skipped, errors = [], [], []
    hud_chars, subtitle_chars = set(), set()
    categories = {'hud': 0, 'dialogue': 0, 'movie': 0, 'scene': 0}
    for index, (relative, donor) in enumerate(sorted(donors.items())):
        name, entry = game.find(relative)
        if not entry:
            skipped.append(relative)
            continue
        packed = game.read(entry)
        before = unpack(packed)
        try:
            if relative.startswith('audio/'):
                source = dialogue(before)
                translated = dialogue(unpack(donor.read_bytes()))
                if source['name'] != translated['name']:
                    raise ValueError('Dialogue identity differs')
                text = normalize(translated['text'], buttons)
                after = replace_dialogue(before, text)
                subtitle_chars.update(map(ord, text))
                category = 'dialogue'
            else:
                original = english_map(before)
                replacement = text_replacements(original, english_map(donor.read_bytes()), buttons)
                after = replace_english(before, replacement)
                chars = hud_chars if relative.startswith('art/hud/') else subtitle_chars
                chars.update(ord(c) for text in replacement.values() for c in text)
                category = 'hud' if relative.startswith('art/hud/') else 'movie' if relative.startswith('movies/') else 'scene'
            categories[category] += 1
            if after != before:
                edits.append(make_edit(name, entry, packed, before, after))
        except (ValueError, UnicodeError, struct.error) as error:
            errors.append({'path': relative, 'error': str(error)})
        if index % 1000 == 0:
            print(f'Chinese resource compatibility: {index}/{len(donors)}, {len(edits)} edits, {len(errors)} mismatches', flush=True)
    _, font_entry = game.find('art/hud/fonts_latin.gfx')
    required = {1: subtitle_chars|hud_chars, 3: hud_chars, 5: hud_chars}
    font, font_report = adapt_font_library(game.read(font_entry),
        (chinese_root/'art/hud/fonts_latin.gfx').read_bytes(), required)
    output.mkdir(parents=True)
    (output/'fonts_latin.gfx').write_bytes(font)
    manifest = {'schema': 1, 'language': 'zh-Hans', 'resource_identity': 'ea15579ce7a45045ca313f06221090c575ad6c6affd26932a8d59393732ba3a4',
                'files': edits, 'font': {'path': 'art/hud/fonts_latin.gfx', 'asset': 'fonts_latin.gfx',
                'bytes': len(font), 'sha256': hashlib.sha256(font).hexdigest()}}
    (output/'localization.json').write_text(json.dumps(manifest, ensure_ascii=False, separators=(',', ':'))+'\n', encoding='utf8')
    report = {'language': 'zh-Hans', 'menu_entries': len(original_menu), 'categories': categories,
              'edited_files': len(edits), 'skipped_missing_files': skipped, 'mismatches': errors,
              'font': font_report, 'font_bytes': len(font),
              'preserves_native_audio_and_scene_tails': True}
    (output/'compatibility-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-root', type=Path, required=True)
    parser.add_argument('--chinese-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.game_root, args.chinese_root, args.output)
