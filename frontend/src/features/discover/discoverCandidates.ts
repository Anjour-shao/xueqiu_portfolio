import type { DiscoveredPortfolioItem } from '../../types';

const STORAGE_KEY = 'xueqiu-discover-candidates-v1';

export type DiscoverCandidate = DiscoveredPortfolioItem & {
  added_at: string;
  reviewed?: boolean;
};

export function loadDiscoverCandidates(): DiscoverCandidate[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const list = JSON.parse(raw) as DiscoverCandidate[];
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

export function saveDiscoverCandidates(list: DiscoverCandidate[]): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
}

export function mergeCandidates(
  existing: DiscoverCandidate[],
  incoming: DiscoveredPortfolioItem[],
): DiscoverCandidate[] {
  const byCode = new Map(existing.map((c) => [c.account_code, c]));
  const now = new Date().toISOString();
  for (const item of incoming) {
    const prev = byCode.get(item.account_code);
    byCode.set(item.account_code, {
      ...item,
      added_at: prev?.added_at ?? now,
      reviewed: prev?.reviewed ?? false,
    });
  }
  return [...byCode.values()].sort((a, b) => b.added_at.localeCompare(a.added_at));
}

export function markCandidateReviewed(accountCode: string, reviewed = true): DiscoverCandidate[] {
  const next = loadDiscoverCandidates().map((c) =>
    c.account_code === accountCode ? { ...c, reviewed } : c,
  );
  saveDiscoverCandidates(next);
  return next;
}

export function removeCandidates(accountCodes: string[]): DiscoverCandidate[] {
  const drop = new Set(accountCodes);
  const next = loadDiscoverCandidates().filter((c) => !drop.has(c.account_code));
  saveDiscoverCandidates(next);
  return next;
}

export function clearAllCandidates(): void {
  localStorage.removeItem(STORAGE_KEY);
}
