import api from './client';
import type { DiscoveryStats, MinedCubeItem, SyncLogItem } from '../types';

export type SyncStreamDoneEvent = { type: 'done'; ok: boolean; message?: string };

export async function fetchDiscoveryStats() {
  const res = await api.get<DiscoveryStats>('/api/discovery/stats');
  return res.data;
}

export async function fetchDiscoveryCubes(params?: {
  auto_pass?: boolean;
  selected?: number;
  depth?: number;
  q?: string;
}) {
  const res = await api.get<{ items: MinedCubeItem[] }>('/api/discovery/cubes', { params });
  return res.data.items;
}

export async function patchDiscoveryCube(
  accountCode: string,
  body: { selected?: number; note?: string },
) {
  const res = await api.patch<MinedCubeItem>(`/api/discovery/cubes/${encodeURIComponent(accountCode)}`, body);
  return res.data;
}

export async function importDiscoveryCube(accountCode: string) {
  const res = await api.post<{ ok: boolean; message: string; account_code: string }>(
    `/api/discovery/cubes/${encodeURIComponent(accountCode)}/import`,
  );
  return res.data;
}

export async function streamDiscoveryMine(
  onLog: (item: SyncLogItem) => void,
  options?: { max_depth?: number; signal?: AbortSignal },
): Promise<{ ok: boolean; message?: string }> {
  const res = await fetch('/api/discovery/mine-stream', {
    method: 'POST',
    headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' },
    body: JSON.stringify({ max_depth: options?.max_depth ?? 1 }),
    signal: options?.signal,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(text || `挖掘请求失败 (${res.status})`);
  }
  const reader = res.body?.getReader();
  if (!reader) {
    throw new Error('无法读取挖掘日志流');
  }

  const decoder = new TextDecoder();
  let buffer = '';
  let result: { ok: boolean; message?: string } = { ok: false };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split('\n\n');
    buffer = chunks.pop() ?? '';
    for (const chunk of chunks) {
      for (const line of chunk.split('\n')) {
        if (!line.startsWith('data: ')) continue;
        const payload = JSON.parse(line.slice(6)) as SyncLogItem | SyncStreamDoneEvent;
        if ('type' in payload && payload.type === 'done') {
          result = { ok: payload.ok, message: payload.message };
        } else {
          onLog(payload as SyncLogItem);
        }
      }
    }
  }
  return result;
}
