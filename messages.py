"""Terminal messages in the language picked in config ("language": "id", the default, or "en"). The code writes its
messages in Indonesian; in English each log line and each error text is looked up in messages_en.json, by its exact
template or, for a message built with values in it, by its template with those values matched back out."""
import json
import logging
import re
from functools import cache
from pathlib import Path

LANGUAGES = ('id', 'en')
CATALOG = Path(__file__).with_name('messages_en.json')
# A value in a template: {} where the code writes an f-string, a printf field where it hands the values to the log.
VALUE = re.compile(r'\{\}|%[-+ #0]*\d*(?:\.\d+)?[sdrfx]')
LEVEL = re.compile(r'(DEBUG|INFO|WARNING|ERROR|CRITICAL): (.*)', re.S)
_language = 'id'


def use(language: object) -> None:
    """Every later message in this language; anything unknown keeps Indonesian."""
    global _language
    _language = language if language in LANGUAGES else 'id'


def pieces(template: str) -> list:
    """The text around a template's values, as it reads once they are filled in."""
    return [piece.replace('%%', '%') for piece in VALUE.split(template)]


@cache
def catalog() -> tuple[dict, list]:
    english = json.loads(CATALOG.read_text(encoding='utf-8'))
    # Templates with values, also the printf ones a bot running in Indonesian prints filled in.
    found = [(pieces(key), pieces(english[key])) for key in english]
    found = [(around, english) for around, english in found if len(around) > 1 and ''.join(around).strip()]
    # The most specific template first, so "Tembok {} …" is tried before a looser "{} …".
    found.sort(key=lambda template: -len(''.join(template[0])))
    patterns = [(around, re.compile('(.*?)'.join(map(re.escape, around)), re.S), english) for around, english in found]
    return english, patterns


def translate(text: object) -> object:
    """text in the chosen language; text the catalog does not know, and anything that is not text, as it is."""
    if _language == 'id' or not isinstance(text, str):
        return text
    exact, patterns = catalog()
    if text in exact:
        return exact[text]
    level = LEVEL.fullmatch(text)
    if level:
        # A log line relayed from a bot that still runs in Indonesian.
        return f'{level[1]}: {translate(level[2])}'
    for around, pattern, english in patterns:
        # Its fixed text comes first: a cheap check that leaves the regex to the few templates that can match.
        if not (text.startswith(around[0]) and text.endswith(around[-1]) and all(p in text for p in around[1:-1])):
            continue
        found = pattern.fullmatch(text)
        if found:
            # A value can itself be a message, such as the reason in "… ({exc}); …".
            values = [translate(value) for value in found.groups()] + ['']
            return ''.join(piece + value for piece, value in zip(english, values))
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
