import ast
import io
import json
import logging
from pathlib import Path
import re
import tempfile
import unittest

import bot
import messages

ROOT = Path(__file__).resolve().parent
WRITERS = {'debug', 'info', 'warning', 'error', 'exception', 'critical', 'log', 'translate'}
PRINTF = re.compile(r'%[-+ #0]*\d*(?:\.\d+)?[sdrfx%]')


def template(node: ast.AST) -> str | None:
    """A message as the catalog keys it: text as written, each value of an f-string as {}."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return ''.join(part.value if isinstance(part, ast.Constant) else '{}' for part in node.values)
    return None


def code_messages() -> dict:
    """Every message the code writes: the text given first to a log call, an error, print or translate."""
    found = {}
    for path in sorted(ROOT.glob('*.py')):
        if path.name.startswith('test_'):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            if isinstance(func, ast.Attribute):
                wanted = func.attr in WRITERS
            else:
                wanted = isinstance(func, ast.Name) and (func.id == 'print' or func.id[0].isupper())
            text = template(node.args[0]) if wanted else None
            if text and ' ' in text.strip():
                found.setdefault(text, f'{path.name}:{node.lineno}')
    return found


class MessagesTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(messages.use, 'id')
        self.english = json.loads(messages.CATALOG.read_text(encoding='utf-8'))

    def test_every_message_in_the_code_has_an_english_text_with_the_same_values(self):
        written = code_messages()
        self.assertEqual({text: written[text] for text in set(written) - set(self.english)}, {},
                         'add these messages to messages_en.json')
        self.assertEqual(sorted(set(self.english) - set(written)), [], 'these are no longer in the code')
        for indonesian, english in self.english.items():
            with self.subTest(message=indonesian):
                self.assertEqual(english.count('{}'), indonesian.count('{}'))
                if '{}' not in indonesian:
                    self.assertEqual(PRINTF.findall(english), PRINTF.findall(indonesian))

    def test_messages_stay_indonesian_unless_english_is_chosen(self):
        for language in ('id', 'fr', None):
            messages.use(language)
            self.assertEqual(messages.translate('Serangan selesai: %s (%s)'), 'Serangan selesai: %s (%s)')

    def test_an_english_message_keeps_its_values_and_the_reason_inside_it(self):
        messages.use('en')
        self.assertEqual(messages.translate('Serangan selesai: %s (%s)'), self.english['Serangan selesai: %s (%s)'])
        reason = 'Angka tidak terlihat; hentikan tanpa input'
        outer = 'Tingkatkan Lebih Banyak tidak terbaca ({}); tidak ada input lanjutan'
        self.assertEqual(messages.translate(outer.replace('{}', reason)),
                         self.english[outer].replace('{}', self.english[reason]))
        self.assertEqual(messages.translate('REMOTE: menghentikan bot'), self.english['REMOTE: menghentikan bot'])
        self.assertEqual(messages.translate('INFO: something unknown'), 'INFO: something unknown')
        self.assertEqual(messages.translate(42), 42)

    def test_the_log_and_the_errors_in_it_are_printed_in_the_chosen_language(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        root = logging.getLogger()
        saved = root.handlers[:]
        root.handlers = [handler]
        try:
            messages.install('en')
            messages.install('en')
            self.assertEqual(sum(isinstance(found, messages.Translate) for found in handler.filters), 1)
            reason = 'Angka tidak terlihat; hentikan tanpa input'
            logging.getLogger('bot').error('Berhenti: %s', bot.BotError(reason))
        finally:
            root.handlers = saved
        self.assertEqual(stream.getvalue(), 'ERROR: ' + self.english['Berhenti: %s'] % self.english[reason] + '\n')

    def test_the_config_picks_the_language_and_refuses_an_unknown_one(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            path.write_text(json.dumps({'language': 'en'}), encoding='utf-8')
            self.assertEqual(bot.configured_language(path), 'en')
            self.assertEqual(bot.configured_language(Path(folder) / 'missing.json'), 'id')
        config = bot.load_config(ROOT / 'config.json')
        bot.validate_config({**config, 'language': 'en'}, ROOT, 'both')
        with self.assertRaises(bot.BotError):
            bot.validate_config({**config, 'language': 'fr'}, ROOT, 'both')


if __name__ == '__main__':
    unittest.main()
