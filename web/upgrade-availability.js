import { MAX_LEVELS } from './upgrade-max-levels.js';

/** Hide only confirmed completed maximum-level export groups. Unknown rows stay visible. */
export function isFullyUpgraded(row) {
  if (row.source !== 'game_export' || row.section === 'in_progress' || !Number.isInteger(row.current_level)) return false;
  const maximum = MAX_LEVELS[String(row.data_id)];
  // A level beyond the pinned catalog signals newer game data, not proof of a maximum.
  return Number.isInteger(maximum) && row.current_level === maximum;
}
