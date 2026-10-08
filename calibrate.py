"""Manual screenshot calibration; never sends game input."""

import argparse
import json
import logging
import os
from pathlib import Path
import tempfile
import uuid

import cv2
import numpy as np

from bot import (ADB, ACTIONS, BotError, DEPLOY_SCREENS, RESOURCES, SCREEN_NAMES,
                 asset_path, crop, executable, glyphs, integer, load_config, rectangle)

LOG = logging.getLogger(__name__)


def save_config(path: Path, config: dict) -> None:
    payload = json.dumps(config, indent=2, ensure_ascii=False) + '\n'
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.calibration-', suffix='.tmp', delete=False) as stream:
            name = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if name is not None and name.exists():
            name.unlink()


def choose_roi(image: np.ndarray, label: str) -> list:
    LOG.info('%s: drag kotak, Enter/Space simpan; C batal', label)
    window = f'ROI: {label}'
    value = list(cv2.selectROI(window, image, showCrosshair=True, fromCenter=False))
    cv2.destroyWindow(window)
    return rectangle(value, list(image.shape[1::-1]))


def choose_point(image: np.ndarray, label: str) -> list:
    window = f'Klik: {label} (Esc batal)'
    selected = []

    def click(event: int, x: int, y: int, flags: int, param: object) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            selected[:] = [x, y]

    cv2.namedWindow(window, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(window, click)
    cv2.imshow(window, image)
    try:
        while not selected:
            if cv2.waitKey(50) & 0xFF == 27 or cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                raise BotError('Kalibrasi dibatalkan')
        return selected.copy()
    finally:
        cv2.destroyWindow(window)


def write_png(path: Path, image: np.ndarray) -> None:
    ok, encoded = cv2.imencode('.png', image)
    if not ok:
        raise BotError('Gagal encode PNG')
    with path.open('xb') as stream:
        stream.write(encoded.tobytes())


def calibrate_screen(image: np.ndarray, name: str, root: Path, counts: list[int]) -> dict:
    anchors = []
    for index in range(2):
        roi = choose_roi(image, f'{name}: anchor {index+1}, UI statis unik; bukan angka/timer/base')
        template = crop(image, roi)
        if float(template.std()) < 5:
            raise BotError('Anchor terlalu polos; pilih ikon/teks UI')
        filename = f'calibration/{name}-{uuid.uuid4().hex}.png'
        path = asset_path(root, filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_png(path, template)
        anchors.append({'roi': roi, 'file': filename})
    actions = {key: choose_point(image, f'{name}: {key}') for key in ACTIONS.get(name, ())}
    screen = {'anchors': anchors, 'actions': actions}
    if name == 'home_scout':
        screen['loot'] = {key: choose_roi(image, f'Angka {key} saja; tanpa ikon/label') for key in RESOURCES}
    if name in DEPLOY_SCREENS:
        if not counts:
            raise BotError('Layar deploy memerlukan --counts, contoh --counts 40 40')
        screen['deploy'] = choose_point(image, 'Satu titik deploy di luar garis merah')
        screen['troops'] = [{'slot': choose_point(image, f'Slot pasukan: {count} unit'), 'count': count}
                            for count in counts]
    return screen


def learn_digits(samples: list, loot: dict, folder: Path) -> None:
    """Average labelled loot glyphs into one template per digit; never overwrites."""
    collected = {}
    for image, *labels in samples:
        for key, label in zip(('gold', 'elixir'), labels):
            expected = label.replace(' ', '')
            found = glyphs(crop(image, loot[key]))
            if not expected.isdigit() or len(found) != len(expected):
                raise BotError(f'{key}: {len(found)} angka terlihat, label {label!r}')
            for digit, glyph in zip(expected, found):
                collected.setdefault(digit, []).append(glyph)
    missing = sorted(set('0123456789') - set(collected))
    if missing:
        raise BotError(f'Contoh angka belum lengkap: {"".join(missing)}')
    folder.mkdir(parents=True, exist_ok=False)
    for digit, items in collected.items():
        write_png(folder / f'{digit}.png', (np.mean(items, axis=0) * 255).round().astype(np.uint8))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Kalibrasi manual CoC; tidak ada input emulator')
    parser.add_argument('--config', type=Path, default=Path('config.json'))
    parser.add_argument('--init', action='store_true', help='Buat config baru; tidak overwrite')
    parser.add_argument('--adb', default=r'C:\LDPlayer\LDPlayer14\adb.exe')
    parser.add_argument('--serial', help='Serial dari adb devices; jangan menebak')
    parser.add_argument('--tesseract', default=r'C:\Program Files\Tesseract-OCR\tesseract.exe')
    parser.add_argument('--gold', type=int, default=0)
    parser.add_argument('--elixir', type=int, default=0)
    parser.add_argument('--dark', type=int, default=0)
    parser.add_argument('--capture', type=Path, help='Simpan PNG baru, tanpa overwrite')
    parser.add_argument('--screen', choices=SCREEN_NAMES + ('loading',))
    parser.add_argument('--image', type=Path, help='Kalibrasi dari screenshot lokal, tanpa ADB')
    parser.add_argument('--counts', type=int, nargs='+', default=[])
    parser.add_argument('--replace', action='store_true', help='Izinkan mengganti profil layar yang dipilih')
    parser.add_argument('--learn-digits', nargs='+', metavar='PNG:EMAS:ELIXIR',
                        help='Bangun template angka loot dari screenshot scout berlabel')
    parser.add_argument('--digits-dir', default='calibration/digits', help='Folder template angka baru')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    try:
        if args.init:
            if args.config.exists() or not args.serial:
                raise BotError('--init perlu --serial dan config tujuan yang belum ada')
            config = {'adb': args.adb, 'serial': args.serial, 'tesseract': args.tesseract,
                      'resolution': [1280, 720],
                      'minimum': {key: integer(getattr(args, key), 0, 999_999_999, key) for key in RESOURCES},
                      'screens': {}}
            with args.config.open('x', encoding='utf-8') as stream:
                stream.write(json.dumps(config, indent=2) + '\n')
            LOG.info('Config dibuat: %s; resolusi harus cocok dengan LDPlayer', args.config)
            return 0
        config = load_config(args.config)
        if args.learn_digits:
            samples = []
            for spec in args.learn_digits:
                path, gold, elixir = spec.rsplit(':', 2)
                image = cv2.imread(path)
                if image is None or list(image.shape[1::-1]) != config['resolution']:
                    raise BotError(f'Screenshot tidak terbaca/resolusi salah: {path}')
                samples.append((image, gold, elixir))
            folder = asset_path(args.config.parent, f'{args.digits_dir}/0.png').parent
            learn_digits(samples, config['screens']['home_scout']['loot'], folder)
            save_config(args.config, {**config, 'digits': args.digits_dir})
            LOG.info('Template angka disimpan: %s', args.digits_dir)
            return 0
        if not args.screen and not args.capture:
            raise BotError('Pilih --screen atau --capture')
        if args.screen in config.get('screens', {}) and not args.replace:
            raise BotError('Profil sudah ada; gunakan --replace untuk menggantinya')
        counts = [integer(c, 1, 300, 'count') for c in args.counts]
        if args.screen in DEPLOY_SCREENS and not counts:
            raise BotError('--counts diperlukan untuk layar deploy')
        if args.image:
            image = cv2.imread(str(args.image))
            if image is None or list(image.shape[1::-1]) != config['resolution']:
                raise BotError('Screenshot tidak terbaca/resolusi salah')
        else:
            device = ADB(executable(config['adb'], 'ADB'), config['serial'], config['resolution'])
            image = device.capture()
        if args.capture:
            write_png(args.capture, image)
            LOG.info('Screenshot tersimpan lokal: %s', args.capture)
        if args.screen:
            screen = calibrate_screen(image, args.screen, args.config.parent, counts)
            updated = {**config, 'screens': {**config.get('screens', {}), args.screen: screen}}
            save_config(args.config, updated)
            LOG.info('Kalibrasi disimpan: %s', args.screen)
        return 0
    except KeyboardInterrupt:
        LOG.warning('Kalibrasi dihentikan')
        return 130
    except (BotError, OSError, KeyError, TypeError, cv2.error) as exc:
        LOG.error('Berhenti: %s', exc)
        return 1
    finally:
        cv2.destroyAllWindows()


if __name__ == '__main__':
    raise SystemExit(main())
