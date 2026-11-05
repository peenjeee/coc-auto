// Canonical device messages are localized only for display. Never use this on game recognition data.
const response = await fetch(new URL('./messages_en.json', import.meta.url));
if (!response.ok) throw new Error('Could not load the message catalog');
const english = await response.json();
const tokenPattern = /\{\}|%[-+ #0]*\d*(?:\.\d+)?[sdrfx]|%%/g;
const prefixPattern = /^(DEBUG|INFO|WARNING|ERROR|CRITICAL|REMOTE): /;
const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

function pieces(text) {
  const literals = [];
  let start = 0;
  for (const token of text.matchAll(tokenPattern)) {
    if (token[0] === '%%') continue;
    literals.push(text.slice(start, token.index).replaceAll('%%', '%'));
    start = token.index + token[0].length;
  }
  literals.push(text.slice(start).replaceAll('%%', '%'));
  return literals;
}

const patterns = Object.entries(english).map(([source, target]) => {
  const prefix = source.match(prefixPattern);
  if (prefix && target.startsWith(prefix[0])) {
    source = source.slice(prefix[0].length);
    target = target.slice(prefix[0].length);
  }
  const literals = pieces(source);
  return { literals, target: pieces(target),
    specificity: literals.reduce((sum, part) => sum + part.length, 0),
    pattern: new RegExp('^' + literals.map(escape).join('([\\s\\S]*?)') + '$(?![\\s\\S])') };
}).filter(({ literals, specificity }) => literals.length > 1 && specificity > 0)
  .sort((a, b) => b.specificity - a.specificity);

/** Translate known templates, keeping severity, paths, flags and unknown external details intact. */
export function translate(text, language = document.documentElement.lang) {
  if (language !== 'en' || typeof text !== 'string') return text;
  if (Object.hasOwn(english, text)) return english[text];
  const prefix = text.match(prefixPattern);
  if (prefix) return prefix[0] + translate(text.slice(prefix[0].length), language);
  for (const wrapper of ['Perintah ditolak: {}', 'Gagal: {}']) {
    if (text.startsWith(wrapper.slice(0, -2))) {
      return english[wrapper].slice(0, -2) + translate(text.slice(wrapper.length - 2), language);
    }
  }
  for (const { pattern, target } of patterns) {
    const found = text.match(pattern);
    if (!found) continue;
    const values = found.slice(1).map((value) => translate(value, language));
    return target.map((part, index) => part + (values[index] ?? '')).join('');
  }
  for (const separator of [', ', '; ']) {
    if (text.includes(separator)) return text.split(separator).map((part) => translate(part, language)).join(separator);
  }
  return text;
}
