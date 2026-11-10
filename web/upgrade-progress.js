import {UPGRADE_TIMES} from './upgrade-times.js?v=1';

/** Export time left is authoritative. Standard total durations only support an estimate. */
export function upgradeProgress(row, list, scannedAt, now = Date.now()) {
  if (row.section !== 'in_progress' || !Number.isFinite(row.seconds) || row.seconds < 0 || !Number.isFinite(scannedAt)) return null;
  const remaining = Math.max(0, row.seconds - Math.max(0, (now - scannedAt) / 1000));
  const target = Number.isInteger(row.next_level) ? row.next_level : Number.isInteger(row.current_level) ? row.current_level + 1 : null;
  const record = UPGRADE_TIMES[`${list}/${row.id}`];
  const recorded = Number.isFinite(row.duration_seconds) && row.duration_seconds > 0 ? row.duration_seconds : null;
  const standard = target === null ? null : record?.seconds[target];
  const total = recorded ?? (Number.isFinite(standard) && standard > 0 ? standard : null);
  // Outdated catalog durations must not produce negative progress or a false percentage.
  const percent = total !== null && row.seconds <= total ? Math.min(100, Math.max(0, 100 * (1 - remaining / total))) : null;
  return {row, list, remaining, finish: scannedAt + row.seconds * 1000, target, percent,
    estimated: recorded === null && percent !== null, names: record ? {en:record.en,id:record.id} : null,
    ready: remaining === 0};
}

export function ongoingUpgrades(lists, now = Date.now()) {
  return Object.entries(lists).flatMap(([list, data]) => (data.rows ?? []).map(row => upgradeProgress(row, list, data.scannedAt, now)).filter(Boolean))
    .sort((a,b) => a.finish - b.finish || String(a.row.name).localeCompare(String(b.row.name)));
}
