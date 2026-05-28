const STORAGE_KEY = 'xueqiu-discover-checkpoint-v3';

export interface DiscoverCheckpoint {
  total_scanned: number;
  total_hits: number;
  updated_at?: string;
  note?: string;
}

export function defaultCheckpoint(): DiscoverCheckpoint {
  return {
    total_scanned: 0,
    total_hits: 0,
    updated_at: new Date().toISOString(),
  };
}

function migrateLegacyCheckpoint(data: Record<string, unknown>): DiscoverCheckpoint {
  return {
    total_scanned: Number(data.total_scanned) || 0,
    total_hits: Number(data.total_hits) || 0,
    updated_at: typeof data.updated_at === 'string' ? data.updated_at : undefined,
    note: typeof data.note === 'string' ? data.note : undefined,
  };
}

export function loadDiscoverCheckpoint(): DiscoverCheckpoint | null {
  try {
    for (const key of [STORAGE_KEY, 'xueqiu-discover-checkpoint-v2', 'xueqiu-discover-checkpoint-v1']) {
      const raw = localStorage.getItem(key);
      if (!raw) continue;
      const data = JSON.parse(raw) as Record<string, unknown>;
      const cp = migrateLegacyCheckpoint(data);
      if (key !== STORAGE_KEY) saveDiscoverCheckpoint(cp);
      return cp;
    }
    return null;
  } catch {
    return null;
  }
}

export function saveDiscoverCheckpoint(cp: DiscoverCheckpoint): void {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({ ...cp, updated_at: new Date().toISOString() }),
  );
}

export function applyBatchToCheckpoint(
  cp: DiscoverCheckpoint,
  batch: { scanned?: number; matched_count?: number },
): DiscoverCheckpoint {
  const scanned = batch.scanned ?? 0;
  const hits = batch.matched_count ?? 0;
  return {
    ...cp,
    total_scanned: cp.total_scanned + scanned,
    total_hits: cp.total_hits + hits,
    updated_at: new Date().toISOString(),
  };
}

export function importCheckpointJson(raw: string): DiscoverCheckpoint {
  const data = JSON.parse(raw) as Record<string, unknown>;
  const existing = loadDiscoverCheckpoint();
  return {
    ...migrateLegacyCheckpoint(data),
    total_scanned: Number(data.total_scanned) || existing?.total_scanned || 0,
    total_hits: Number(data.total_hits) || existing?.total_hits || 0,
    note: typeof data.note === 'string' ? data.note : existing?.note,
    updated_at: new Date().toISOString(),
  };
}

export function exportCheckpointJson(cp: DiscoverCheckpoint): string {
  return JSON.stringify(cp, null, 2);
}
