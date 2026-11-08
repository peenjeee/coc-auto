import { UPGRADE_ICONS, UPGRADE_LEVEL_ICONS, UPGRADE_ALIASES } from './upgrade-icons.js?v=3';

// A scan reports the destination level, not necessarily the current level. Never infer one from
// the timer or a grouped count. Heroes, troops, pets and equipment have level-independent icons.
export function upgradePortraitSource(list, row, compact = false, rows = []) {
  const alias = String(row.name ?? '').toLowerCase().replace(/[^a-z0-9]/g, '');
  const key = UPGRADE_ICONS[list]?.[row.id] ? row.id : UPGRADE_ALIASES[list]?.[alias];
  const versions = UPGRADE_LEVEL_ICONS[list]?.[key];
  if (!versions) return UPGRADE_ICONS[list]?.[key] ?? null;
  const scanned = compact ? rows.find(candidate => row.id ? candidate.id === row.id
    : !candidate.id && String(candidate.name).toLowerCase() === String(row.name).toLowerCase()) : row;
  const level = compact && Number.isInteger(row.level) ? row.level : scanned?.current_level ?? scanned?.next_level;
  // Running upgrades may omit their level. Keep a representative portrait in that case;
  // a known target must still use its exact version, or initials when unsupported.
  return Number.isInteger(level) ? versions[level] ?? null : UPGRADE_ICONS[list]?.[key] ?? null;
}

// Category artwork makes missing assets visible without substituting a different building level.
export function upgradePortraitFallback(list, row = {}) {
  if (list === 'buildings' && (row.id === 'blacksmith' || row.data_id === 1000070)) {
    return 'icons/upgrades/equipment.webp';
  }
  return {
    buildings: 'icons/upgrades/buildings.webp', builder: 'icons/upgrades/builder.webp',
    lab: 'icons/upgrades/lab.webp', star: 'icons/upgrades/star.webp',
    pets: 'icons/upgrades/pets.png', gear: 'icons/upgrades/equipment.webp',
  }[list] ?? 'icons/upgrades/buildings.webp';
}
