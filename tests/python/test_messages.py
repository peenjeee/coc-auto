from coc_auto.paths import RESOURCE_ROOT, REPO_ROOT
from pathlib import Path as _TestPath
FIXTURES = _TestPath(__file__).resolve().parents[1] / "fixtures"
import ast
import io
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from coc_auto import bot
from coc_auto import messages

ROOT = RESOURCE_ROOT
WRITERS = {'debug', 'info', 'warning', 'error', 'exception', 'critical', 'log', 'translate'}
PRINTF = re.compile(r'%[-+#0]*\d*(?:\.\d+)?[sdrfx%]')


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
    for path in sorted((REPO_ROOT / 'src/coc_auto').glob('*.py')):
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
        # Assigned fragments, return messages and setup problems are also catalog entries.
        # They need not appear as the first argument of a logging call.
        self.assertEqual((REPO_ROOT / 'web/messages_en.json').read_bytes(), messages.CATALOG.read_bytes(),
                         'run python messages.py to update the dashboard catalog')
        for indonesian, english in self.english.items():
            with self.subTest(message=indonesian):
                self.assertEqual(english.count('{}'), indonesian.count('{}'))
                if '{}' not in indonesian:
                    self.assertEqual(PRINTF.findall(english), PRINTF.findall(indonesian))

    def test_assembled_messages_and_severity_prefixes_are_fully_english(self):
        messages.use('en')
        examples = {
            'INFO: Menyerah: kerusakan mencapai 50%': 'INFO: Surrender: damage reached 50%',
            'INFO: Menyerah: 2 bintang': 'INFO: Surrender: 2 stars',
            'INFO: Menyerah: setelah 60 detik battle': 'INFO: Surrender: after 60 battle seconds',
            'INFO: Trofi desa malam 1000 < 2000: serang sampai selesai':
                'INFO: Builder Base trophies 1000 < 2000: fight to the end',
            'INFO: Tombol upgrade tembok: gold putih, elixir merah':
                'INFO: Wall upgrade button: gold white, elixir red',
            'Selesai: batas waktu tercapai; bot berhenti di desa':
                'Done: time limit reached; bot stops in the village',
            'Selesai: 3 tembok sudah di-upgrade; bot berhenti di desa':
                'Done: 3 walls upgraded; bot stops in the village',
            'REMOTE: Perintah ditolak: Pilihan cycles harus angka 1..100':
                'REMOTE: Command rejected: Option cycles must be a number 1..100',
            'REMOTE: menjalankan python bot.py --mode home': 'REMOTE: running python bot.py --mode home',
            'INFO: Isi Gerobak Eliksir: 42%': 'INFO: Elixir Cart fill: 42%',
            'Deteksi LDPlayer: Resolusi layar LDPlayer tidak terbaca; koneksi putus':
                'LDPlayer detection: LDPlayer screen resolution could not be read; connection lost',
        }
        for source, english in examples.items():
            with self.subTest(source=source):
                self.assertEqual(messages.translate(source), english)
        for source in ('INFO: unknown device detail', r'C:\LDPlayer\LDPlayer9\adb.exe',
                       'emulator-5554', 'coc_0123456789abcdefghijklmnop', '--surrender-at 50'):
            self.assertEqual(messages.translate(source), source)

    def test_indonesian_fragments_outside_logging_calls_are_registered(self):
        # Catch the assigned/returned fragments, problem lists, CLI help and window labels
        # that the logging-call inventory cannot see. Docstrings and protocol checks are excluded.
        words = re.compile(r'\b(tidak|belum|gagal|pilih|simpan|penuh|desa|ditolak|selesai|batal|harus|'
                           r'pasukan|koneksi|tingkatkan|berhenti|perlu|kerusakan|bintang|setelah|putih|merah|'
                           r'bahasa|dijalankan|dibuka|ditutup|dimuat|dimatikan|dinyalakan|pengaturan|deteksi|'
                           r'kotak|tanpa|hanya|saja|angka|tembok|sudah)\b', re.I)
        missing = {}
        for path in (REPO_ROOT / 'src/coc_auto').glob('*.py'):
            if path.name.startswith('test_'):
                continue
            tree = ast.parse(path.read_text(encoding='utf-8'))
            parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
            docs = {node.body[0].value for node in ast.walk(tree)
                    if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    and node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)}
            patterns = {node.args[0] for node in ast.walk(tree)
                        if isinstance(node, ast.Call) and node.args and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name) and node.func.value.id == 're'
                        and node.func.attr in ('compile', 'fullmatch', 'match', 'search', 'sub')}
            for node in ast.walk(tree):
                if node in docs or node in patterns or isinstance(parents.get(node), ast.JoinedStr):
                    continue
                text = template(node)
                if text and words.search(text) and text not in self.english and text != 'Perintah ditolak':
                    missing[text] = f'{path.name}:{node.lineno}'
        self.assertEqual(missing, {})

    def test_english_wrappers_translate_catalog_names_and_nested_reasons(self):
        messages.use('en')
        reason = 'Builders or Laboratory busy; Goblin offer closed without paying gems'
        source_reason = 'Tukang atau Lab sibuk; tawaran Goblin ditutup tanpa membayar gem'
        items = json.loads((ROOT / 'village-export-catalog.json').read_text(encoding='utf-8'))['items']
        cases = [('Ratu Pemanah', 'Archer Queen'), ('Raja Barbar', 'Barbarian King')]
        # A plain-text name cannot distinguish entities sharing the same Indonesian name.
        cases += [(item['name_id'], self.english.get(item['name_id'], item['name'])) for item in items.values()]
        for source, expected in cases:
            for nested in (reason, source_reason):
                line = f'WARNING: Upgrade of {source} skipped: {nested}'
                with self.subTest(name=source, reason=nested):
                    result = messages.translate(line)
                    self.assertEqual(result, f'WARNING: Upgrade of {expected} skipped: {reason}')
                    self.assertEqual(messages.translate(result), result)
        line = 'WARNING: Upgrade of Custom name skipped: external detail'
        self.assertEqual(messages.translate(line), line)
        messages.use('id')
        line = f'WARNING: Upgrade of Ratu Pemanah skipped: {reason}'
        self.assertEqual(messages.translate(line), line)

    def test_worker_output_stays_canonical_under_either_preference(self):
        with patch.dict(os.environ, {'COC_CANONICAL_LOG': '1'}):
            for language in messages.LANGUAGES:
                messages.use(language)
                self.assertEqual(messages.translate('Menyerah: %s'), 'Menyerah: %s')

    def test_help_uses_the_config_before_argparse_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'custom.json'
            path.write_text(json.dumps({'language': 'id'}))
            path.with_name('custom.local.json').write_text(json.dumps({'language': 'en'}))
            for script, expected in (('bot.py', 'dry run without taps by default'),
                                     ('calibrate.py', 'Manual CoC calibration; no emulator input')):
                for flag in (['--config', str(path)], ['--config=' + str(path)]):
                    result = subprocess.run([sys.executable, str(REPO_ROOT / script), *flag, '--help'],
                                            capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(expected, result.stdout)
                    self.assertNotIn('tanpa', result.stdout)
                    self.assertNotIn('Tingkatkan', result.stdout)

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

    def test_a_line_from_a_bot_still_running_in_indonesian_is_shown_in_english(self):
        # The dashboard switched to English while the bot runs: its lines come filled in and with their level.
        line = 'WARNING: CoC tidak terbuka; membuka game (percobaan 2)'
        messages.use('id')
        self.assertEqual(messages.translate(line), line)
        messages.use('en')
        self.assertEqual(messages.translate(line),
                         'WARNING: ' + self.english['CoC tidak terbuka; membuka game (percobaan %s)'] % 2)
        surrender = self.english['Menyerah: %s'] % self.english['kerusakan mencapai {}%'].replace('{}', '50')
        self.assertEqual(messages.translate('INFO: Menyerah: kerusakan mencapai 50%'), 'INFO: ' + surrender)

    def test_a_percent_sign_before_a_word_is_text_not_a_value(self):
        # "50% saat" / "50% during": "% s" and "% d" are not printf values; read as one, the line came out "50% suring".
        messages.use('en')
        self.assertEqual(messages.translate('INFO: Kerusakan mencapai 50% saat deploy; sisa pasukan tidak dikerahkan'),
                         'INFO: Damage reached 50% during deploy; remaining troops not deployed')

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
