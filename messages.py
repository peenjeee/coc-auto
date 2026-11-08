"""Presentation-only localization; game recognition data never passes through this module."""
from __future__ import annotations
import json
import logging
import os
import re
import sys
from functools import lru_cache
from pathlib import Path

LANGUAGES = ('id', 'en')
CATALOG = Path(__file__).with_name('messages_en.json')
_language = 'id'
# No space flag: "50% saat" / "50% during" are text, and no message uses "% d" as a value.
TOKENS = re.compile(r'\{\}|%[-+#0]*\d*(?:\.\d+)?[sdrfx]|%%')
PREFIX = re.compile(r'^(DEBUG|INFO|WARNING|ERROR|CRITICAL|REMOTE): ')


def configured_language(path: Path) -> str:
    """Read the display preference without importing OpenCV or a calibration."""
    language = 'id'
    for source in (path, path.with_name(path.stem + '.local.json')):
        try:
            language = json.loads(source.read_text(encoding='utf-8')).get('language', language)
        except (OSError, ValueError, AttributeError):
            pass
    return language


def initialize_cli(default: Path) -> None:
    """Find --config before argparse handles --help or reports invalid arguments."""
    path = default
    for index, arg in enumerate(sys.argv[1:], 1):
        if arg.startswith('--config='):
            path = Path(arg.split('=', 1)[1])
        elif arg == '--config' and index + 1 < len(sys.argv):
            path = Path(sys.argv[index + 1])
    use(configured_language(path))


def use(language: object) -> None:
    """Every later message in this language; anything unknown keeps Indonesian."""
    global _language
    _language = language if language in LANGUAGES and not os.environ.get('COC_CANONICAL_LOG') else 'id'


def pieces(text: str) -> tuple[list, list]:
    literals, values, start = [], [], 0
    for token in TOKENS.finditer(text):
        if token.group() == '%%':
            continue
        literals.append(text[start:token.start()].replace('%%', '%'))
        values.append(token.group())
        start = token.end()
    literals.append(text[start:].replace('%%', '%'))
    return literals, values


@lru_cache(maxsize=1)
def catalog() -> tuple[dict, list]:
    english = json.loads(CATALOG.read_text(encoding='utf-8'))
    patterns = []
    for source, target in english.items():
        prefix = PREFIX.match(source)
        if prefix and target.startswith(prefix.group()):
            source, target = source[prefix.end():], target[prefix.end():]
        literals, values = pieces(source)
        if values and any(literals):
            patterns.append((sum(map(len, literals)), re.compile('(.*?)'.join(map(re.escape, literals)), re.S),
                             pieces(target)[0]))
    patterns.sort(key=lambda entry: -entry[0])
    return english, patterns


def duration(seconds: int) -> str:
    remaining = max(0,int(seconds))
    parts = []
    for unit,size in (('d',86400),('h',3600),('m',60),('s',1)):
        amount,remaining = divmod(remaining,size)
        if amount:
            parts.append(f'{amount}{unit}')
    return ' '.join(parts) or '0s'


def readable(text: str) -> str:
    # Keep retained logs readable too; do not invent levels for older None entries.
    text = re.sub(r'\b(Menunggu|Waiting) (\d+) (?:detik|seconds):',
                  lambda match: match[1]+' '+duration(int(match[2]))+':',text)
    text = re.sub(r'(CoC ditutup selama menunggu; dibuka lagi setelah) (\d+) detik',
                  lambda match: 'CoC ditutup selama menunggu; dibuka kembali dalam '+duration(int(match[2])),text)
    text = re.sub(r'(CoC closed during the wait; opened again after) (\d+) seconds',
                  lambda match: 'CoC closed during the wait; reopening in '+duration(int(match[2])),text)
    text = re.sub(r'((?:Bangunan|Riset|Hewan|Peralatan) di-upgrade: .+?) ke level None$',r'\1',text)
    return re.sub(r'((?:Building|Research|Pet|Equipment) upgraded: .+?) to level None$',r'\1',text)


def translate(text: object) -> object:
    """text in the chosen language; text the catalog does not know, and anything that is not text, as it is."""
    if not isinstance(text, str):
        return text
    text = readable(text)
    if _language == 'id':
        return text
    exact, patterns = catalog()
    if text in exact:
        return exact[text]
    prefix = PREFIX.match(text)
    if prefix:
        return prefix.group() + translate(text[prefix.end():])
    # Error wrappers must be recognized before an inner, more specific error template.
    for wrapper in ('Perintah ditolak: {}', 'Gagal: {}'):
        if text.startswith(wrapper[:-2]):
            return exact[wrapper][:-2] + translate(text[len(wrapper) - 2:])
    for _, pattern, literals in patterns:
        found = pattern.fullmatch(text)
        if found:
            # A value can itself be a message, such as the reason in "… ({exc}); …".
            values = [translate(value) for value in found.groups()] + ['']
            return ''.join(piece + value for piece, value in zip(literals, values))
    for separator in (', ', '; '):
        if separator in text:
            return separator.join(translate(value) for value in text.split(separator))
    return text


class Translate(logging.Filter):
    """Translates each record, and the errors and texts logged with it, before a handler formats it."""

    def filter(self, record: logging.LogRecord) -> bool:
        if _language != 'id':
            record.msg = translate(record.msg)
            if isinstance(record.args, tuple):
                record.args = tuple(translate(str(arg)) if isinstance(arg, (str, BaseException)) else arg
                                    for arg in record.args)
        return True


def install(language: object) -> None:
    """Use language and translate what the root logger's handlers print."""
    use(language)
    for handler in logging.getLogger().handlers:
        if not any(isinstance(existing, Translate) for existing in handler.filters):
            handler.addFilter(Translate())


if __name__ == '__main__':
    # Run python messages.py after editing the catalog; the test checks the browser copy stays in sync.
    Path(__file__).with_name('web').joinpath('messages_en.json').write_bytes(CATALOG.read_bytes())
