"""Build the iOS asset catalog from the pinned upstream Prototype game icon."""
import hashlib
import json
from pathlib import Path
import plistlib
import subprocess
import urllib.request

ICON_URL = ('https://raw.githubusercontent.com/fungalspores/prototype-switch/'
            '292de4b382dbe335011d8b4f56ec4212df5ef74c/art/icon_prototype.jpg')
ICON_SHA256 = 'b7542f380f4113498b49df51af7e17a5918a6337e225e3915028884cea61ed95'


def install_icon(app, output):
    app, output = Path(app), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(ICON_URL, timeout=60) as response:
        raw = response.read(1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != ICON_SHA256:
        raise ValueError('Pinned Prototype game icon differs')
    original = output / 'prototype.jpg'
    original.write_bytes(raw)
    catalog = output / 'Images.xcassets'
    icons = catalog / 'AppIcon.appiconset'
    icons.mkdir(parents=True)
    images = []
    for idiom, sizes, scales in [('iphone', [20, 29, 40, 60], [2, 3]),
                                 ('ipad', [20, 29, 40, 76], [1, 2]),
                                 ('ipad', [83.5], [2]), ('ios-marketing', [1024], [1])]:
        for size in sizes:
            for scale in scales:
                pixels = int(size * scale)
                filename = f'icon-{pixels}.png'
                if not (icons / filename).exists():
                    subprocess.run(['sips', '-s', 'format', 'png', '-z', str(pixels), str(pixels),
                                    str(original), '--out', str(icons / filename)],
                                   check=True, capture_output=True)
                images.append({'idiom': idiom, 'size': f'{size}x{size}',
                               'scale': f'{scale}x', 'filename': filename})
    (icons / 'Contents.json').write_text(json.dumps({'images': images,
        'info': {'version': 1, 'author': 'xcode'}}, indent=2))
    (catalog / 'Contents.json').write_text(json.dumps({'info': {'version': 1, 'author': 'xcode'}}))
    partial = output / 'icon-info.plist'
    subprocess.run(['xcrun', 'actool', str(catalog), '--compile', str(app),
                    '--platform', 'iphoneos', '--minimum-deployment-target', '26.0',
                    '--target-device', 'iphone', '--target-device', 'ipad', '--app-icon', 'AppIcon',
                    '--output-partial-info-plist', str(partial)], check=True, capture_output=True)
    info_path = app / 'Info.plist'
    info = plistlib.loads(info_path.read_bytes())
    info.update(plistlib.loads(partial.read_bytes()))
    if not info.get('CFBundleIcons') or not (app / 'Assets.car').is_file():
        raise ValueError('Compiled app icon catalog is missing')
    info_path.write_bytes(plistlib.dumps(info))
    return {'source': ICON_URL, 'sha256': ICON_SHA256, 'asset_catalog': True}
