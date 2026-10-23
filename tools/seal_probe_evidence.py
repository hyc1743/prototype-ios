"""Encrypt private shader observations before uploading public CI evidence."""
import argparse
import io
import os
from pathlib import Path
import zipfile

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from ci_codegen import read_key

MAGIC = b'PROTOSPV1'


def seal(directory, destination):
    files = sorted(directory.glob('*.spirv.bin.*'))
    if not files:
        return
    if len(files) > 4096 or sum(p.stat().st_size for p in files) > 64 * 1024 * 1024:
        raise ValueError('Private shader evidence exceeds expected bounds')
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.writestr(path.name, path.read_bytes())
    nonce = os.urandom(12)
    encrypted = AESGCM(read_key(None)).encrypt(nonce, packed.getvalue(), MAGIC)
    destination.write_bytes(MAGIC + nonce + encrypted)
    print(f'Sealed {len(files)} private shader observations')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    seal(args.directory, args.destination)
