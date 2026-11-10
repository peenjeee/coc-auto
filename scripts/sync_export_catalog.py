"""Pin game IDs and names for village exports; no network requests during a scan."""
import argparse
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESOURCES = ROOT / 'src/coc_auto/resources'
REVISION = 'fdac291ae7fbc2901221ff945834be534d0344ae'
BASE = f'https://raw.githubusercontent.com/ClashKingInc/ClashKingAssets/{REVISION}/assets/'

def normalized(text):
    return re.sub('[^a-z0-9]', '', text.lower())


def sync_display_names(items):
    path = RESOURCES/'messages_en.json'
    catalog = json.loads(path.read_text(encoding='utf-8'))
    for item in items.values():
        catalog[item['name_id']] = item['name']
    encoded = json.dumps(catalog,ensure_ascii=False,indent=1)+'\n'
    path.write_text(encoded,encoding='utf-8')
    (ROOT/'web/messages_en.json').write_text(encoded,encoding='utf-8')

def write_upgrade_times(data, identities):
    """Standard game durations are estimates; exports remain authoritative for time left."""
    times = {}
    for group in ('buildings', 'traps', 'heroes', 'troops', 'spells', 'pets', 'equipment'):
        for item in data[group]:
            identity = identities.get(str(item['_id']))
            if not identity or not identity['id']:
                continue
            seconds = {}
            for level in item.get('levels', []):
                duration = level.get('build_time', level.get('upgrade_time'))
                if type(level.get('level')) is int and type(duration) in (int, float) and duration > 0:
                    seconds[level['level']] = duration
            times[identity['list'] + '/' + identity['id']] = {
                'en': identity['name'], 'id': identity.get('name_id', identity['name']), 'seconds': seconds}
    (ROOT/'web/upgrade-times.js').write_text(
        '// Standard durations from pinned ClashKing game data ' + REVISION + '.\n'
        + '// Discounts and helpers can change actual total time; exported remaining timers take precedence.\n'
        + 'export const UPGRADE_TIMES = ' + json.dumps(times, ensure_ascii=False, separators=(',', ':')) + ';\n',
        encoding='utf-8')


def generate(data, translations, extend=False):
    files = {'buildings':'buildings.json', 'lab':'research.json', 'pets':'research.json',
             'gear':'gear.json', 'builder':'builder.json', 'star':'star.json'}
    catalogs = {name:json.loads((ROOT/name).read_text(encoding='utf-8')) for name in set(files.values())}
    output = {}
    for group in ('buildings', 'traps', 'heroes', 'troops', 'spells', 'pets', 'equipment'):
        for item in data[group]:
            village = item.get('village', 'home')
            village = 'builder' if village == 'builderBase' else village
            if village not in ('home', 'builder'):
                continue
            listing = ('builder' if village == 'builder' else 'buildings') if group in ('buildings','traps','heroes') else (
                'star' if village == 'builder' else 'pets' if group == 'pets' else 'gear' if group == 'equipment' else 'lab')
            catalog = catalogs[files[listing]]
            name = item['name']
            indonesian = translations.get(item.get('TID', {}).get('name'), name)
            aliases = {normalized(name), normalized(indonesian)}
            found = [(key, category) for category, entries in catalog.items() for key, names in entries.items()
                     if aliases & {normalized(v) for v in [key, *names]}]
            if len(set(found)) > 1:
                raise ValueError(f'Ambiguous catalog identity: {name}: {found}')
            if found:
                key, category = found[0]
            else:
                key = re.sub('[^a-z0-9]+', '_', name.lower()).strip('_')
                category = {'traps':'trap', 'heroes':'hero', 'pets':'pet', 'equipment':'equipment'}.get(group)
                if category is None and group == 'buildings':
                    category = {'Defense':'defense','Wall':'defense','Army':'army','Resource':'resource'}.get(item.get('type'),'other')
                if category is None:
                    production = item.get('production_building', '')
                    category = {'Workshop':'siege','Dark Barracks':'dark_troop','Spell Factory':'spell',
                                'Dark Spell Factory':'dark_spell'}.get(production, 'troop')
                # Seasonal and Super troops have no research upgrades. Never add those to upgrade choices.
                levels = item.get('levels', [])
                upgradable = any(any(isinstance(v, (int,float)) and v > 0 for k,v in level.items()
                                     if k in ('build_cost','upgrade_cost','upgrade_cost_shiny','upgrade_cost_glowy','upgrade_cost_starry'))
                                 for level in levels)
                if extend and (upgradable or group == 'equipment'):
                    catalog.setdefault(category,{})[key] = list(dict.fromkeys([name, indonesian]))
                else:
                    key = None
            levels = [level.get('level') for level in item.get('levels', [])]
            levels = [level for level in levels if type(level) is int and level > 0]
            output[str(item['_id'])] = {'id':key, 'list':listing, 'category':category,
                                       'name':name, 'name_id':indonesian,
                                       'max_level':max(levels) if levels else None}
    if extend:
        for name, catalog in catalogs.items():
            groups = []
            for category, entries in catalog.items():
                lines = [f'    {json.dumps(key)}: {json.dumps(names,ensure_ascii=False)}' for key,names in entries.items()]
                groups.append('  '+json.dumps(category)+': {\n'+',\n'.join(lines)+'\n  }')
            (ROOT/name).write_text('{\n'+',\n'.join(groups)+'\n}\n',encoding='utf-8')
    result = {'source':'https://github.com/ClashKingInc/ClashKingAssets', 'revision':REVISION,
              'notice':'Clash of Clans game data and names by Supercell. IDs, names and maximum levels from the pinned catalog; current levels/timers from the game export.',
              'items':output}
    (RESOURCES/'village-export-catalog.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    maximums = {key:item['max_level'] for key,item in output.items() if item['max_level'] is not None}
    (ROOT/'web/upgrade-max-levels.js').write_text(
        '// Generated by scripts/sync_export_catalog.py; pinned game-data revision '+REVISION+'\n'
        +'export const MAX_LEVELS = '+json.dumps(maximums,sort_keys=True)+';\n',encoding='utf-8')
    write_upgrade_times(data, output)
    sync_display_names(output)
    print(f'Wrote {len(output)} game identities')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--extend',action='store_true',help='Add missing permanent upgrade names to the OCR action catalogs')
    args = parser.parse_args()
    data = json.load(urllib.request.urlopen(BASE+'static_data.json',timeout=30))
    translations = json.load(urllib.request.urlopen(BASE+'translations/ID.json',timeout=30))
    generate(data,translations,args.extend)
