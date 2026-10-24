import base64
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from chinese_resources import GameArchives, dialogue, make_edit, replace_dialogue
from game_localization import MAGIC, pack, path_key, unpack
from localization_bundle import apply, open_bundle, seal
from swf_fonts import Font, adapt_font_library, swf_parts


def audio(endian):
    name = b'synthetic_dialogue'
    text = 'original subtitle'.encode()
    body = (struct.pack(endian+'II', 10, 21)+b'AudioDialogueSubtitle\0'+
            struct.pack(endian+'II', 1, len(name))+name+b'\0'+
            b'\x01'+struct.pack(endian+'I', 7)+b'english\0'+struct.pack(endian+'I', len(text))+text+b'\0')
    subtitle = struct.pack(endian+'III', 0xfe000000, len(body)+12, len(body)+12)+body
    voice = b'\x00NATIVE_XMA_BYTES\xff\x00\x93'
    voice = struct.pack(endian+'III', 0xfe000000, len(voice)+12, len(voice)+12)+voice
    return struct.pack(endian+'III', MAGIC, 12, 12+len(subtitle)+len(voice))+subtitle+voice


def library(donor=False):
    tags = []
    for ident, name in ((1, b'PrototypeSubtitle'), (3, b'PrototypeBody360'), (5, b'PrototypeTitle360'), (7, b'PrototypeDigital')):
        glyphs = {0xb1: (b'\x00\x00', b'\x0a\x00', b'\x00')}
        if donor:
            glyphs[0xb1] = (b'\x00\x01', b'\x0a\x00', b'\x00')
            glyphs[ord('中')] = (b'\x00\x02', b'\x14\x00', b'\x00')
            name = name.replace(b'360', b'PC')
        font = Font(ident, 132, 0, name, bytes(6), glyphs, [])
        body = font.encode()
        tags.append(struct.pack('<HI', (75 << 6) | 63, len(body))+body)
    result = bytearray(b'GFX\x08'+bytes(4)+bytes(5)+b''.join(tags))
    struct.pack_into('<I', result, 4, len(result))
    return bytes(result)


class ChineseResourceTests(unittest.TestCase):
    def test_archive_view_hides_localized_members_and_font_without_changing_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = [r'\art\hud\fe_textbible.p3d.rz', r'\art\hud\fonts_latin.gfx', r'\art\other.gfx']
            rows = sorted((path_key(name), 4096+i*2048, 1) for i, name in enumerate(names))
            table = b''.join(struct.pack('>III', *row) for row in rows)
            metadata = bytes(8)
            for name in names:
                raw = name.encode()+b'\0'
                metadata += bytes(12)+struct.pack('<I', len(raw))+raw+bytes(3)
            header = b'ATG CORE CEMENT LIBRARY\0'.ljust(32, b'\0')+bytes([2, 1, 1, 1])
            header += struct.pack('>6I', 60, len(table), 60+len(table), len(metadata), 0, 3)
            archive = root/'00art.rcf'; archive.write_bytes(header+table+metadata)
            before = archive.read_bytes()
            manifest = GameArchives(root).view_metadata([{'path': 'art/hud/fe_textbible.p3d.rz'}])
            self.assertEqual(archive.read_bytes(), before)
            changed = list(struct.iter_unpack('>III', base64.b64decode(manifest['archive_tables'][0]['data'])))
            self.assertEqual(changed, sorted(changed))
            self.assertEqual(len(changed), len(rows))
            hashes = {row[0] for row in changed}
            self.assertNotIn(path_key(names[0]), hashes)
            self.assertNotIn(path_key(names[1]), hashes)
            self.assertIn(next(row for row in rows if row[0] == path_key(names[2])), changed)

    def test_big_and_little_endian_subtitle_edits_preserve_native_audio_bytes(self):
        for endian in ('<', '>'):
            before = audio(endian)
            after = replace_dialogue(before, '中文字幕')
            old_voice = 12+struct.unpack_from(endian+'I', before, 20)[0]
            new_voice = 12+struct.unpack_from(endian+'I', after, 20)[0]
            self.assertEqual(after[new_voice:], before[old_voice:])
            self.assertEqual(dialogue(after)['text'], '中文字幕')
            self.assertEqual(struct.unpack_from(endian+'I', after, 8)[0], len(after))
            edit = make_edit('audio/english/audiofile/a/a/aabbccdd.p3d.rz',
                {'archive': '00audio.rcf', 'source_offset': 0, 'source_size': len(before)}, before, before, after)
            literal = b''.join(base64.b64decode(change['data']) for change in edit['changes'])
            self.assertNotIn(b'NATIVE_XMA_BYTES', literal)

    def test_font_adaptation_keeps_console_font_names_and_original_button_outlines(self):
        original, donor = library(), library(True)
        changed, report = adapt_font_library(original, donor, {ord('中')})
        old = {Font.parse(body).ident: Font.parse(body) for kind, body in swf_parts(original)[1] if kind == 75}
        new = {Font.parse(body).ident: Font.parse(body) for kind, body in swf_parts(changed)[1] if kind == 75}
        for ident in (1, 3, 5):
            self.assertEqual(new[ident].name, old[ident].name)
            self.assertEqual(new[ident].glyphs[0xb1], old[ident].glyphs[0xb1])
            self.assertIn(ord('中'), new[ident].glyphs)
        self.assertEqual(new[7].glyphs, old[7].glyphs)
        with self.assertRaisesRegex(ValueError, 'lacks'):
            adapt_font_library(original, donor, {ord('缺')})

    def test_authenticated_bundle_applies_only_to_matching_original_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root/'bundle'; folder.mkdir()
            before = audio('>'); after = replace_dialogue(before, '中文字幕')
            edit = make_edit('audio/english/audiofile/a/a/aabbccdd.p3d.rz',
                {'archive': '00audio.rcf', 'source_offset': 0, 'source_size': len(before)}, before, before, after)
            font = library(True)
            manifest = {'schema': 1, 'language': 'zh-Hans', 'files': [edit], 'font': {
                'path': 'art/hud/fonts_latin.gfx', 'bytes': len(font), 'sha256': hashlib.sha256(font).hexdigest()}}
            (folder/'localization.json').write_text(json.dumps(manifest))
            (folder/'fonts_latin.gfx').write_bytes(font)
            (folder/'compatibility-report.json').write_text(json.dumps({'edited_files': 1, 'mismatches': []}))
            (folder/'CREDITS.txt').write_text('Synthetic fixture only')
            key = bytes(range(32)); container = root/'locale.enc'
            seal(folder, container, key)
            opened = root/'opened'
            open_bundle(container, opened, key)
            game = root/'game'; game.mkdir(); (game/'00audio.rcf').write_bytes(before)
            apply(opened, game)
            self.assertEqual(unpack((game/edit['path']).read_bytes()), after)
            self.assertEqual((game/'00audio.rcf').read_bytes(), before)
            (game/'00audio.rcf').write_bytes(before[:-1]+b'!')
            with self.assertRaisesRegex(ValueError, 'resource differs'):
                apply(opened, game)
            corrupted = bytearray(container.read_bytes()); corrupted[-1] ^= 1
            container.write_bytes(corrupted)
            with self.assertRaises(Exception):
                open_bundle(container, root/'wrong', key)


if __name__ == '__main__':
    unittest.main()
