#!/usr/bin/env python3
"""Inspect game text and restore English from a player's original resource copy.

No text is translated or downloaded. Outputs loose files for game/art/hud/;
the original RCF archives and saves are never modified.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import struct
import zlib

MAX_TEXT_BYTES = 64 * 1024 * 1024
MAGIC = 0xff443350
BIBLE = 0x18202
DATA = 0x18201


def u32(data, offset, endian='<'):
    if offset < 0 or offset + 4 > len(data):
        raise ValueError('Truncated resource integer')
    return struct.unpack_from(endian + 'I', data, offset)[0]


def path_key(name):
    result = 0
    for value in name.encode('ascii'):
        if result == 0 and value == 92:
            continue
        # Radical's filename key folds ASCII punctuation as well as capitals.
        result = (31 * result + (value + 32 if value < 97 else value)) & 0xffffffff
    return result


def read_archive_file(archive, name):
    archive = Path(archive)
    with archive.open('rb') as source:
        header = source.read(60)
        if len(header) != 60 or not header.startswith(b'ATG CORE CEMENT LIBRARY\0'):
            raise ValueError('Not a Radical RCF archive')
        endian = '>' if header[34] == 1 else '<'
        offset, size = u32(header, 36, endian), u32(header, 40, endian)
        count = u32(header, 56, endian)
        if size != count * 12 or count > 100000 or offset + size > archive.stat().st_size:
            raise ValueError('Invalid RCF entry table')
        source.seek(offset)
        rows = source.read(size)
        matches = [(at, length) for key, at, length in struct.iter_unpack(endian + 'III', rows)
                   if key == path_key(name)]
        if len(matches) != 1:
            raise ValueError('Missing or ambiguous archive member: ' + name)
        at, length = matches[0]
        if not 0 < length <= MAX_TEXT_BYTES or at + length > archive.stat().st_size:
            raise ValueError('Invalid text resource bounds')
        source.seek(at)
        return source.read(length)


def unpack(data):
    if len(data) > MAX_TEXT_BYTES:
        raise ValueError('Text resource exceeds size limit')
    if not data.startswith(b'RZ'):
        return data
    expected = u32(data, 8)
    if expected > MAX_TEXT_BYTES:
        raise ValueError('Inflated text exceeds size limit')
    decoder = zlib.decompressobj()
    result = decoder.decompress(data[16:], MAX_TEXT_BYTES + 1)
    if len(result) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError('Invalid compressed text resource')
    return result


def pack(data):
    return b'RZ' + bytes(6) + struct.pack('<I', len(data)) + bytes(4) + zlib.compress(data, 9)


def chunk(data, offset, endian='<'):
    kind, header, total = (u32(data, offset+field, endian) for field in (0, 4, 8))
    if not 12 <= header <= total or offset + total > len(data):
        raise ValueError('Invalid Pure3D chunk bounds')
    return kind, header, total


def pstring(data, pos, end):
    if pos >= end or pos+1+data[pos] > end:
        raise ValueError('Truncated Pure3D string')
    stop = pos+1+data[pos]
    return data[pos+1:stop].rstrip(b'\0').decode('utf8'), stop


@dataclass
class TextBible:
    language: str
    keys: tuple[str, ...]
    values: tuple[str, ...]
    prefix: bytes
    data_prefix: bytes
    endian: str = '<'
    data_header_delta: int = 0
    original_data: bytes = b''

    @classmethod
    def parse(cls, data):
        endian = '<' if u32(data, 0) == BIBLE else '>'
        kind, header, total = chunk(data, 0, endian)
        if kind != BIBLE or total != len(data):
            raise ValueError('Not a complete text bible chunk')
        language, pos = pstring(data, 12, header)
        count = u32(data, pos+4, endian)
        pos += 8
        if count > 50000:
            raise ValueError('Too many text entries')
        keys = []
        for _ in range(count):
            key, pos = pstring(data, pos, header)
            keys.append(key)
        prefix = data[12:pos]
        if pos + count*8 != header:
            raise ValueError('Invalid text index')
        offsets = [u32(data, pos+i*4, endian) for i in range(count)]
        kind, data_header, data_total = (u32(data, header+at, endian) for at in (0, 4, 8))
        # Existing PT-BR scene tables have stale DATA header lengths, while
        # the actual blob size and enclosing total remain authoritative.
        if kind != DATA or data_total < 12 or header + data_total != total:
            raise ValueError('Unsupported text data chunk')
        _, pos = pstring(data, header+12, total)
        length = u32(data, pos+4, endian)
        start = pos+8
        blob = data[start:]
        if length != len(blob) or (offsets and offsets[0] != 0):
            raise ValueError('Invalid text blob size or first offset')
        ends = offsets[1:] + [length]
        if any(not 0 <= a <= b <= length for a, b in zip(offsets, ends)):
            raise ValueError('Text offsets leave the blob')
        values = tuple(blob[a:b].decode('utf8') for a, b in zip(offsets, ends))
        return cls(language, tuple(keys), values, prefix, data[header+12:start-4], endian,
                   data_header-data_total, data)

    def encode(self, values):
        values = tuple(values)
        if len(values) != len(self.keys):
            raise ValueError('Replacement text count differs')
        if self.original_data and values == self.values:
            return self.original_data
        blob = bytearray()
        offsets = []
        for value in values:
            offsets.append(len(blob))
            blob.extend(value.encode('utf8'))
        ends = [value+1 for value in offsets[1:]] + ([len(blob)+1] if offsets else [])
        index = self.prefix + b''.join(struct.pack(self.endian+'I', value) for value in offsets+ends)
        child = self.data_prefix + struct.pack(self.endian+'I', len(blob)) + blob
        child = struct.pack(self.endian+'III', DATA, len(child)+12, len(child)+12) + child
        return struct.pack(self.endian+'III', BIBLE, len(index)+12, len(index)+12+len(child)) + index + child


def bibles(data):
    data = unpack(data)
    endian = '<' if u32(data, 0) == MAGIC else '>'
    kind, header, total = chunk(data, 0, endian)
    if kind != MAGIC or header != 12 or total != len(data):
        raise ValueError('Unsupported Pure3D file')
    result = []
    pos = header
    while pos < total:
        kind, _, size = chunk(data, pos, endian)
        if kind == BIBLE:
            result.append(TextBible.parse(data[pos:pos+size]))
        pos += size
    return result


def english_map(data):
    matches = [bible for bible in bibles(data) if bible.language == 'english']
    if len(matches) != 1:
        raise ValueError('Expected one English language slot')
    return dict(zip(matches[0].keys, matches[0].values))


def actual_language(text):
    markers = {key: text.get(key, '').strip().upper()
               for key in ('ENGLISH', 'ACHIEVEMENTS', 'MAIN_MENU', 'CONTINUE', 'OPTIONS')}
    if markers['ACHIEVEMENTS'] == 'CONQUISTAS' and markers['CONTINUE'] == 'CONTINUAR':
        return 'pt-BR'
    if (markers['ENGLISH'] == 'ENGLISH' and markers['MAIN_MENU'] == 'MAIN MENU'
            and markers['CONTINUE'] == 'CONTINUE' and markers['OPTIONS'] == 'OPTIONS'):
        return 'en'
    return 'unknown'


def replace_english(data, replacements):
    data = unpack(data)
    # Validate every chunk and offsets before building any output.
    bibles(data)
    endian = '<' if u32(data, 0) == MAGIC else '>'
    output = bytearray()
    pos = 12
    for _ in range(10000):
        if pos == len(data):
            break
        kind, _, size = chunk(data, pos, endian)
        raw = data[pos:pos+size]
        if kind == BIBLE:
            bible = TextBible.parse(raw)
            if bible.language == 'english':
                missing = set(bible.keys) - replacements.keys()
                if missing:
                    raise ValueError('Donor lacks target keys: ' + ', '.join(sorted(missing)[:15]))
                tokens = re.compile(r'%\d+:[A-Z0-9_]+')
                for key, original in zip(bible.keys, bible.values):
                    if sorted(tokens.findall(original)) != sorted(tokens.findall(replacements[key])):
                        raise ValueError('Donor format/controller tokens differ for target key: ' + key)
                raw = bible.encode([replacements[key] for key in bible.keys])
        output.extend(raw)
        pos += size
    else:
        raise ValueError('Too many chunks')
    return struct.pack(endian+'III', MAGIC, 12, 12+len(output)) + output


def restore(target_root, donor, output):
    target_root, donor, output = Path(target_root), Path(donor), Path(output)
    if output.exists():
        raise ValueError('Use a new output directory')
    if donor.is_dir():
        donor = donor / '00art.rcf'
    if donor.suffix.lower() == '.rcf':
        donor_data = read_archive_file(donor, r'\art\hud\textbible_english.p3d.rz')
    else:
        donor_data = donor.read_bytes()
    text = english_map(donor_data)
    if actual_language(text) != 'en':
        raise ValueError('Donor English slot does not contain recognisable original English text')
    files, report = {}, {'donor_sha256': hashlib.sha256(donor_data).hexdigest(), 'files': []}
    for name in ('fe_textbible.p3d.rz', 'textbible_english.p3d.rz'):
        source = read_archive_file(target_root / '00art.rcf', '\\art\\hud\\' + name)
        changed = replace_english(source, text)
        if actual_language(english_map(changed)) != 'en':
            raise ValueError('Restored English did not pass content check')
        files[name] = pack(changed)
        report['files'].append({'name': name, 'entries': len(english_map(changed)),
                                'sha256': hashlib.sha256(files[name]).hexdigest()})
    # Build all files first: an incompatible donor cannot leave a partial patch.
    folder = output / 'game/art/hud'
    folder.mkdir(parents=True)
    for name, data in files.items():
        (folder/name).write_bytes(data)
    (output/'english-restoration.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-root', type=Path, required=True)
    parser.add_argument('--donor', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.donor:
        if not args.output:
            parser.error('--donor requires --output')
        result = restore(args.game_root, args.donor, args.output)
    else:
        data = read_archive_file(args.game_root/'00art.rcf', r'\art\hud\textbible_english.p3d.rz')
        text = english_map(data)
        result = {'language_slot': 'english', 'actual_text_language': actual_language(text),
                  'entries': len(text), 'sha256': hashlib.sha256(unpack(data)).hexdigest()}
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
