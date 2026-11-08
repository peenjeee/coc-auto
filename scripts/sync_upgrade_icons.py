"""Refresh the dashboard's small, pinned subset of ClashKing's Supercell game artwork.
Run from the repository root. Existing sources.json pins the asset revision.
"""
import concurrent.futures
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / 'web/icons/entities'
REVISION = 'fdac291ae7fbc2901221ff945834be534d0344ae'
BASE = f'https://raw.githubusercontent.com/ClashKingInc/ClashKingAssets/{REVISION}/'

def clean(value):
    return re.sub(r'[^a-z0-9]', '', value.lower())

def main():
    tree = json.load(urllib.request.urlopen(f'https://api.github.com/repos/ClashKingInc/ClashKingAssets/git/trees/{REVISION}?recursive=1'))
    paths = [item['path'] for item in tree['tree'] if item['path'].endswith('.webp')]
    export_catalog = ROOT / 'village-export-catalog.json'
    identities = (json.loads(export_catalog.read_text(encoding='utf-8')).get('items', {}).values()
                  if export_catalog.exists() else [])
    game_names = {}
    for item in identities:
        if item.get('id'):
            game_names.setdefault((item['list'], item['id']), []).extend(
                [item['name'], item.get('name_id', item['name'])])
    sources, icons, levels, aliases, missing = {}, {}, {}, {}, []
    files = {'buildings':'buildings.json', 'lab':'research.json', 'pets':'research.json', 'gear':'gear.json', 'builder':'builder.json', 'star':'star.json'}
    for listing, filename in files.items():
        icons[listing], levels[listing], aliases[listing] = {}, {}, {}
        for category, entries in json.loads((ROOT / filename).read_text(encoding='utf-8')).items():
            if listing == 'pets' and category != 'pet' or listing == 'lab' and category == 'pet':
                continue
            for key, names in entries.items():
                # Export IDs are stable; include their official names as artwork aliases.
                # For example the legacy key raging_barbarian exports as Raged Barbarian.
                names = list(dict.fromkeys([*names, *game_names.get((listing, key), [])]))
                village = 'builder-base' if listing in ('builder', 'star') else 'home-village'
                if category == 'hero':
                    prefix, flat = 'assets/heroes/', False
                elif category == 'pet':
                    prefix, flat = 'assets/pets/', False
                elif category == 'equipment':
                    prefix, flat = 'assets/equipment/', True
                elif category in ('spell', 'dark_spell'):
                    prefix, flat = 'assets/spells/', True
                elif listing in ('lab', 'star'):
                    prefix, flat = 'assets/troops/', False
                elif category == 'trap':
                    prefix, flat = f'assets/traps/{village}/', False
                else:
                    prefix, flat = f'assets/buildings/{village}/', False
                wanted = {clean(key), *(clean(name) for name in names)}
                candidates = []
                for path in paths:
                    if not path.startswith(prefix):
                        continue
                    tail = path[len(prefix):]
                    slug = tail[:-5] if flat else tail.split('/')[0]
                    if clean(slug) not in wanted:
                        continue
                    if flat or tail.endswith('/icon.webp') or re.search(r'/level_\d+\.webp$', tail):
                        candidates.append(path)
                candidates.sort(key=lambda path: (0 if path.endswith('/icon.webp') else 1, int(re.search(r'level_(\d+)', path).group(1)) if 'level_' in path else 0, path))
                if not candidates:
                    missing.append(f'{listing}/{key}')
                    continue
                path = candidates[0]
                local = 'icons/entities/' + path.removeprefix('assets/').replace('/', '__')
                icons[listing][key] = local
                sources[local.removeprefix('icons/entities/')] = BASE + path
                for version in candidates:
                    match = re.search(r'/level_(\d+)\.webp$', version)
                    if not match:
                        continue
                    version_local = 'icons/entities/' + version.removeprefix('assets/').replace('/', '__')
                    levels[listing].setdefault(key, {})[int(match.group(1))] = version_local
                    sources[version_local.removeprefix('icons/entities/')] = BASE + version
                for name in [key, *names]:
                    aliases[listing][clean(name)] = key
    DEST.mkdir(parents=True, exist_ok=True)
    def download(entry):
        filename, url = entry
        destination = DEST / filename
        if not destination.exists():
            destination.write_bytes(urllib.request.urlopen(url, timeout=30).read())
        return destination.stat().st_size
    print(f'Preparing {len(sources)} local portraits, including building levels.', flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        size = sum(pool.map(download, sources.items()))
    (DEST / 'sources.json').write_text(json.dumps({'artwork':'Clash of Clans game art by Supercell', 'source':'https://github.com/ClashKingInc/ClashKingAssets', 'revision':REVISION, 'assets':sources, 'unavailable':missing}, indent=2) + '\n')
    (ROOT / 'web/upgrade-icons.js').write_text('// Generated by scripts/sync_upgrade_icons.py. Game art by Supercell, supplied by ClashKing Assets.\nexport const UPGRADE_ICONS = ' + json.dumps(icons, separators=(',', ':')) + ';\nexport const UPGRADE_LEVEL_ICONS = ' + json.dumps(levels, separators=(',', ':')) + ';\nexport const UPGRADE_ALIASES = ' + json.dumps(aliases, ensure_ascii=False, separators=(',', ':')) + ';\n')
    print(f'{len(sources)} local icons, {size:,} bytes. Unavailable: {", ".join(missing) or "none"}')

if __name__ == '__main__':
    main()
