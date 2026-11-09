import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

from scripts.build_upgrade_sprites import build


class SpriteBuildTests(unittest.TestCase):
    def test_build_preserves_source_counts_and_is_reproducible_with_a_module(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output, module = root/'source', root/'sprites', root/'sprites.js'
            source.mkdir()
            assets = {}
            for number, color in enumerate([(0, 0, 255, 255), (0, 255, 0, 127)]):
                name = f'heroes__test{number}__icon.webp'
                image = np.full((12, 8, 4), color, dtype=np.uint8)
                self.assertTrue(cv2.imwrite(str(source/name), image, [cv2.IMWRITE_WEBP_QUALITY, 101]))
                assets[name] = f'https://example.invalid/{name}'
            (source/'sources.json').write_text(json.dumps({'revision':'test','assets':assets}))
            groups = {'buildings': ['icons/entities/' + name for name in assets]}
            summary = build(source, output, size=16, quality=101, module_path=module, groups=groups)
            self.assertEqual(summary['original_files'], 2)
            self.assertEqual(summary['sprite_files'], 1)
            self.assertEqual(summary['original_bytes'], sum((source/name).stat().st_size for name in assets))
            files = [*output.glob('*.webp'), module]
            before = {p.name: hashlib.sha256(p.read_bytes()).digest() for p in files}
            build(source, output, size=16, quality=101, module_path=module, groups=groups)
            self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).digest() for p in files})
            sheet = cv2.imread(str(next(output.glob('*.webp'))), cv2.IMREAD_UNCHANGED)
            self.assertEqual(tuple(sheet[10, 10]), (0, 0, 255, 255))
            self.assertEqual(tuple(sheet[10, 30]), (0, 255, 0, 127))
