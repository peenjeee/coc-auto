// Run with node --test test_web.mjs. No browser, relay account, or npm dependencies required.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import test from 'node:test';

const root = new URL('./', import.meta.url);
globalThis.document = { documentElement: { lang: 'en' } };
globalThis.fetch = async (url) => ({ ok: true, json: async () => JSON.parse(await fs.readFile(url, 'utf8')) });
const { translate } = await import('./web/messages.js');
const catalog = JSON.parse(await fs.readFile(new URL('messages_en.json', root), 'utf8'));

test('browser and terminal ship the same catalog', async () => {
  assert.deepEqual(JSON.parse(await fs.readFile(new URL('web/messages_en.json', root), 'utf8')), catalog);
});

test('every exact and rendered message retains its values in English', () => {
  const render = (text) => {
    let index = 0;
    return text.replace(/\{\}|%[-+#0]*\d*(?:\.\d+)?[sdrfx]|%%/g,
      (token) => token === '%%' ? '%' : `VALUE_${index++}`);
  };
  for (const [source, english] of Object.entries(catalog)) {
    assert.equal(translate(source), english, source);
    assert.equal(translate(render(source)), render(english), source);
    assert.equal(translate(render(source), 'id'), render(source), source);
  }
});

test('nested reasons, printf logs and diagnostic lists translate fully', () => {
  const examples = {
    'INFO: Menyerah: kerusakan mencapai 50%': 'INFO: Surrender: damage reached 50%',
    'INFO: Kerusakan mencapai 50% saat deploy; sisa pasukan tidak dikerahkan':
      'INFO: Damage reached 50% during deploy; remaining troops not deployed',
    'INFO: Trofi desa malam 1000 < 2000: serang sampai selesai':
      'INFO: Builder Base trophies 1000 < 2000: fight to the end',
    'INFO: Tombol upgrade tembok: gold putih, elixir merah':
      'INFO: Wall upgrade button: gold white, elixir red',
    'REMOTE: Perintah ditolak: Pilihan cycles harus angka 1..100':
      'REMOTE: Command rejected: Option cycles must be a number 1..100',
    'Gagal: config LDPlayer tidak ditemukan: example.config':
      'Failed: LDPlayer config not found: example.config',
    'REMOTE: menjalankan python bot.py --mode home': 'REMOTE: running python bot.py --mode home',
    'Deteksi LDPlayer: Resolusi layar LDPlayer tidak terbaca; koneksi putus':
      'LDPlayer detection: LDPlayer screen resolution could not be read; connection lost',
    'WARNING: Layar macet (Layar tidak dikenal; tidak ada input dikirim); rapikan layar lalu lanjut, pemulihan 1/20':
      'WARNING: Screen stuck (Unknown screen; no input sent); tidying the screen then continuing, recovery 1/20',
  };
  for (const [source, english] of Object.entries(examples)) assert.equal(translate(source), english);
  for (const value of ['emulator-5554', 'coc_0123456789abcdefghijklmnop', 'INFO: unknown external detail',
    'C:\\LDPlayer\\LDPlayer9\\adb.exe', '--surrender-at 50']) assert.equal(translate(value), value);
});

test('the same retained history renders in either selected language', () => {
  const history = ['INFO: Serangan selesai: home (1)', 'REMOTE: Bot dihentikan'];
  document.documentElement.lang = 'en';
  assert.deepEqual(history.map((line) => translate(line)), ['INFO: Attack finished: home (1)', 'REMOTE: Bot stopped']);
  document.documentElement.lang = 'id';
  assert.deepEqual(history.map((line) => translate(line)), history);
  document.documentElement.lang = 'en';
});

test('dashboard keys, HTML references and interpolation parameters match', async () => {
  const source = await fs.readFile(new URL('web/i18n.js', root), 'utf8');
  // The whole module, so texts added with Object.assign further down are checked too.
  const text = Function(source.replaceAll('export function', 'function') + '\nreturn TEXT;')();
  assert.deepEqual(Object.keys(text.id).sort(), Object.keys(text.en).sort());
  for (const key of Object.keys(text.id)) {
    assert.deepEqual((text.id[key].match(/\{\w+\}/g) || []).sort(),
      (text.en[key].match(/\{\w+\}/g) || []).sort(), key);
  }
  const html = await fs.readFile(new URL('web/index.html', root), 'utf8');
  for (const [, key] of html.matchAll(/data-i18n(?:-confirm)?="([^"]+)"/g)) assert.ok(key in text.en, key);
});
