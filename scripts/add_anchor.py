#!/usr/bin/env python3
"""Add one screen anchor to config.json from a screenshot, without the interactive GUI.

The detector requires every anchor of a screen to match, and the template has to
be exactly the size of its ROI. This helper crops the template from --rect and
uses the same rectangle as the ROI; --margin lets the detector search that many
pixels around it.

    python scripts/add_anchor.py home_menu captures/menu.png multiplayer \
        --rect 95,40,190,40 --margin 4

The template is saved as calibration/<screen>-<name>.png. An existing anchor with
the same file is replaced, so the command is idempotent. Use --replace when the
screen's other anchors are from an older game UI and no longer match.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import cv2


def rect(value: str) -> tuple[int, int, int, int]:
    parts = [int(part) for part in value.split(',')]
    if len(parts) != 4 or min(parts[2:]) <= 0:
        raise argparse.ArgumentTypeError('expected x,y,w,h with a positive size')
    return tuple(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('screen')
    parser.add_argument('image', type=Path)
    parser.add_argument('name', help='template file suffix, e.g. multiplayer')
    parser.add_argument('--rect', type=rect, required=True, help='template and ROI x,y,w,h')
    parser.add_argument('--margin', type=int, default=0, help='search margin in pixels (0..40)')
    parser.add_argument('--replace', action='store_true',
                        help='drop the screen\'s other anchors; the detector requires all of them to match')
    parser.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[1] / 'src/coc_auto/resources/config.json')
    args = parser.parse_args()
    if not 0 <= args.margin <= 40:
        raise SystemExit('margin must be 0..40')

    image = cv2.imread(str(args.image))
    if image is None:
        raise SystemExit(f'cannot read {args.image}')
    height, width = image.shape[:2]
    x, y, w, h = args.rect
    if x < 0 or y < 0 or x + w > width or y + h > height:
        raise SystemExit(f'rect {args.rect} is outside {width}x{height}')
    template = image[y:y + h, x:x + w]
    if float(template.std()) < 5:
        raise SystemExit(f'template is too plain (std {template.std():.2f}); pick a label or icon')

    config = json.loads(args.config.read_text(encoding='utf-8'))
    screen = config.get('screens', {}).get(args.screen)
    if screen is None:
        raise SystemExit(f'unknown screen {args.screen!r}')

    relative = Path('calibration') / f'{args.screen}-{args.name}.png'
    relative.parent.mkdir(exist_ok=True)
    if not cv2.imwrite(str(relative), template):
        raise SystemExit(f'cannot write {relative}')
    anchor = {'roi': [x, y, w, h], 'file': str(relative)}
    if args.margin:
        anchor['margin'] = args.margin
    anchors = [] if args.replace else [item for item in screen.get('anchors', [])
                                       if item.get('file') != str(relative)]
    screen['anchors'] = [*anchors, anchor]

    shutil.copy2(args.config, args.config.with_suffix(args.config.suffix + '.bak'))
    args.config.write_text(json.dumps(config, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    score = float(cv2.matchTemplate(template, template, cv2.TM_SQDIFF_NORMED).min())
    print(f'{args.screen}: added {relative} roi={x},{y},{w},{h} margin={args.margin} score={score:.4f}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
