from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from game_localization import (BIBLE, DATA, MAGIC, TextBible, actual_language,
                               bibles, english_map, pack, replace_english, restore, unpack)


def pstring(value):
    raw = value.encode() + b'\0'
    return bytes([len(raw)]) + raw


def bible(language, values):
    keys = tuple(values)
    prefix = pstring(language) + struct.pack('<II', 0, len(keys))
    prefix += b''.join(pstring(key) for key in keys)
    return TextBible(language, keys, tuple(values.values()), prefix,
                     pstring('Text Bible Data') + struct.pack('<I', 0)).encode(values.values())


def file(*chunks):
    body = b''.join(chunks)
    return struct.pack('<III', MAGIC, 12, 12+len(body)) + body


class LocalizationTests(unittest.TestCase):
    def test_language_is_detected_from_text_instead_of_the_english_slot_name(self):
        data = file(bible('english', {'ENGLISH': 'INGLÊS', 'ACHIEVEMENTS': 'CONQUISTAS',
                                     'CONTINUE': 'CONTINUAR'}))
        self.assertEqual(actual_language(english_map(data)), 'pt-BR')
        with tempfile.TemporaryDirectory() as directory:
            donor = Path(directory)/'english.p3d'
            donor.write_bytes(data)
            output = Path(directory)/'output'
            with self.assertRaisesRegex(ValueError, 'original English'):
                restore(Path(directory), donor, output)
            self.assertFalse(output.exists())

    def test_rebuild_preserves_other_languages_identifiers_and_format_tokens(self):
        portuguese = {'ENGLISH': 'INGLÊS', 'MESSAGE': 'Pressione %0:BUTTON01\n%1:NUMBER'}
        french = bible('french', {'ENGLISH': 'ANGLAIS', 'MESSAGE': 'Appuyez %0:BUTTON01'})
        original = file(bible('english', portuguese), french)
        self.assertEqual(replace_english(original, portuguese), original)
        changed = replace_english(pack(original), {'ENGLISH': 'ENGLISH',
                                                  'MESSAGE': 'Press %0:BUTTON01\n%1:NUMBER'})
        parsed = bibles(changed)
        self.assertEqual(parsed[0].keys, tuple(portuguese))
        self.assertEqual(parsed[0].values[1], 'Press %0:BUTTON01\n%1:NUMBER')
        self.assertTrue(changed.endswith(french))
        self.assertEqual(unpack(pack(changed)), changed)

    def test_incompatible_missing_keys_and_invalid_offsets_are_rejected(self):
        original = file(bible('english', {'A': 'first', 'B': 'second'}))
        with self.assertRaisesRegex(ValueError, 'lacks target keys: B'):
            replace_english(original, {'A': 'replacement'})
        buttons = file(bible('english', {'HINT': 'Pressione %0:BUTTON01'}))
        with self.assertRaisesRegex(ValueError, 'tokens differ'):
            replace_english(buttons, {'HINT': 'Press %0:BUTTON03'})
        bad = bytearray(original)
        bible_header = struct.unpack_from('<I', bad, 16)[0]
        # First of the two start offsets, immediately before the second table.
        struct.pack_into('<I', bad, 12+bible_header-16, 99999)
        with self.assertRaises(ValueError):
            bibles(bad)
        with self.assertRaises(ValueError):
            bibles(original[:-1])

    def test_compression_size_lies_and_trailing_payloads_are_rejected(self):
        compressed = pack(file(bible('english', {'A': 'value'})))
        bad = bytearray(compressed)
        struct.pack_into('<I', bad, 8, 1)
        for data in (bad, compressed[:-1], compressed+b'extra'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                unpack(data)


if __name__ == '__main__':
    unittest.main()
