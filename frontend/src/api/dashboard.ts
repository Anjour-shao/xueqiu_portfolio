import api from './client';
import {
  AccountItem,
  CopyBacktestRequest,
  CopyBacktestResponse,
  CubeCatalogStats,
  DashboardPayload,
  DiscoverPortfoliosParams,
  DiscoverPortfoliosResponse,
  DiscoverStreamEvent,
  DeleteAccountResponse,
  FollowPortfoliosResponse,
  ImportLogsPayload,
  ImportLogsResponse,
  DataFreshnessResponse,
  PortfoliosOverviewResponse,
  PortfoliosOverviewStatsResponse,
  SyncCubeNavAllResponse,
  SyncLatestHfqResponse,
  SyncLogItem,
  SyncQuotesResponse,
  SyncXueqiuAllResponse,
  SyncXueqiuResponse,
} from '../types';

export async function fetchAccounts() {
  const res = await api.get<AccountItem[]>('/api/accounts');
  return res.data;
}

export async function fetchDashboard(accountKey: string) {
  const res = await api.get<DashboardPayload>(`/api/dashboard/${encodeURIComponent(accountKey)}`);
  return res.data;
}

export async function importLogs(payload: ImportLogsPayload) {
  const res = await api.post<ImportLogsResponse>('/api/import-logs', payload);
  return res.data;
}

export async function syncLatestHfq(accountKey: string) {
  const res = await api.post<SyncLatestHfqResponse>(
    `/api/sync-latest-hfq/${encodeURIComponent(accountKey)}`,
    {},
    { timeout: 120000 },
  );
  return res.data;
}

export async function syncFromXueqiu(accountKey: string) {
  const res = await api.post<SyncXueqiuResponse>(
    `/api/sync-xueqiu/${encodeURIComponent(accountKey)}`,
    {},
    { timeout: 180000 },
  );
  return res.data;
}

export type SyncXueqiuStreamDoneEvent = {
  type: 'done';
  ok: boolean;
  message?: string;
  result?: SyncXueqiuResponse;
};

/** 单组合导入（SSE 实时日志） */
export async function streamSyncXueqiu(
  accountKey: string,
  onLog: (item: SyncLogItem) => void,
  signal?: AbortSignal,
): Promise<{ ok: boolean; message?: string; result?: SyncXueqiuResponse }> {
  const res = await fetch(`/api/sync-xueqiu-stream/${encodeURIComponent(accountKey)}`, {
    method: 'POST',
    headers: { Accept: 'text/event-stream' },
    signal,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(text || `导入请求失败 (${res.status})`);
  }
  const reader = res.body?.getReader();
  if (!reader) {
    throw new Error('无法读取导入日志流');
  }

  const decoder = new TextDecoder();
  let buffer = '';
  let outcome: { ok: boolean; message?: string; result?: SyncXueqiuResponse } = { ok: false };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split('\n\n');
    buffer = chunks.pop() ?? '';
    for (const chunk of chunks) {
      for (const line of chunk.split('\n')) {
        if (!line.startsWith('data: ')) continue;
        const payload = JSON.parse(line.slice(6)) as SyncLogItem | SyncXueqiuStreamDoneEvent;
        if ('type' in payload && payload.type === 'done') {
          outcome = {
            ok: payload.ok,
            message: payload.message,
            result: payload.result,
          };
        } else {
          onLog(payload as SyncLogItem);
        }
      }
    }
  }
  return outcome;
}

/** 全量同步：数据库中所有 ZH 开头的雪球组合 */
export async function syncAllFromXueqiu() {
  const res = await api.post<SyncXueqiuAllResponse>('/api/sync-xueqiu-all', {}, { timeout: 600000 });
  return res.data;
}

export async function fetchHealth() {
  const res = await api.get<{ status: string; api_version?: string; engine_version: string }>('/health');
  return res.data;
}

export async function fetchPortfoliosOverview() {
  const res = await api.get<PortfoliosOverviewResponse>('/api/portfolios/overview');
  return res.data;
}

export async function fetchPortfoliosOverviewStats() {
  const res = await api.get<PortfoliosOverviewStatsResponse>('/api/portfolios/overview-stats');
  return res.data;
}

export async function fetchDataFreshness() {
  const res = await api.get<DataFreshnessResponse>('/api/data-freshness');
  return res.data;
}

export async function fetchCubeCatalogStats() {
  const res = await api.get<CubeCatalogStats>('/api/cube-catalog/stats');
  return res.data;
}

export async function resetCubeCatalogDiscovered() {
  const res = await api.post<{ ok: boolean; message: string; reset_count: number }>(
    '/api/cube-catalog/reset-discovered',
  );
  return res.data;
}

export async function syncQuotes() {
  const res = await api.post<SyncQuotesResponse>('/api/sync-quotes', {}, { timeout: 600000 });
  return res.data;
}

export async function syncCubeNavAll() {
  const res = await api.post<SyncCubeNavAllResponse>('/api/sync-cube-nav-all', {}, { timeout: 600000 });
  return res.data;
}

export type SyncStreamDoneEvent = { type: 'done'; ok: boolean; message?: string };

/** 一键全量同步（SSE 实时日志） */
export async function streamSyncAll(
  onLog: (item: SyncLogItem) => void,
  signal?: AbortSignal,
): Promise<{ ok: boolean; message?: string }> {
  const res = await fetch('/api/sync-all-stream', {
    method: 'POST',
    headers: { Accept: 'text/event-stream' },
    signal,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(text || `同步请求失败 (${res.status})`);
  }
  const reader = res.body?.getReader();
  if (!reader) {
    throw new Error('无法读取同步日志流');
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

/** 榜单组合目录同步（SSE，独立于全量同步） */
export async function streamSyncCubeCatalog(
  onLog: (item: SyncLogItem) => void,
  signal?: AbortSignal,
): Promise<{ ok: boolean; message?: string }> {
  const res = await fetch('/api/sync-cube-catalog-stream', {
    method: 'POST',
    headers: { Accept: 'text/event-stream' },
    signal,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(text || `榜单同步请求失败 (${res.status})`);
  }
  const reader = res.body?.getReader();
  if (!reader) {
    throw new Error('无法读取榜单同步日志流');
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

/** 抄作业回测：合并全部 ZH 组合信号，单一账户模拟 */
export async function runCopyBacktest(params?: CopyBacktestRequest) {
  const res = await api.post<CopyBacktestResponse>('/api/backtest-copy', params ?? {}, { timeout: 300000 });
  return res.data;
}

/** 挖组合（同步，适合小范围调试） */
export async function discoverPortfolios(params: DiscoverPortfoliosParams) {
  const res = await api.post<DiscoverPortfoliosResponse>('/api/discover-portfolios', params, { timeout: 600000 });
  return res.data;
}

/** 挖组合（SSE 进度 + 命中实时推送） */
export async function streamDiscoverPortfolios(
  params: DiscoverPortfoliosParams,
  onEvent: (event: DiscoverStreamEvent) => void,
  signal?: AbortSignal,
): Promise<{ ok: boolean; message?: string; result?: DiscoverPortfoliosResponse }> {
  const res = await fetch('/api/discover-portfolios-stream', {
    method: 'POST',
    headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
    signal,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(text || `挖组合请求失败 (${res.status})`);
  }
  const reader = res.body?.getReader();
  if (!reader) {
    throw new Error('无法读取挖组合日志流');
  }

  const decoder = new TextDecoder();
  let buffer = '';
  let outcome: { ok: boolean; message?: string; result?: DiscoverPortfoliosResponse } = { ok: false };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split('\n\n');
    buffer = chunks.pop() ?? '';
    for (const chunk of chunks) {
      for (const line of chunk.split('\n')) {
        if (!line.startsWith('data: ')) continue;
        const payload = JSON.parse(line.slice(6)) as DiscoverStreamEvent;
        onEvent(payload);
        if (payload.type === 'done') {
          const { type: _t, ok, message, ...rest } = payload;
          outcome = {
            ok,
            message,
            result: ok
              ? {
                  scanned: rest.scanned ?? 0,
                  matched_count: rest.matched_count ?? 0,
                  not_found: rest.not_found ?? 0,
                  filtered_out: rest.filtered_out ?? 0,
                  items: rest.items ?? [],
                  batch_start: rest.batch_start ?? null,
                  batch_end: rest.batch_end ?? null,
                  last_scanned_num: rest.last_scanned_num ?? null,
                  next_checkpoint: rest.next_checkpoint ?? null,
                }
              : undefined,
          };
        }
      }
    }
  }
  return outcome;
}

export async function followPortfolios(accountCodes: string[], syncAfterFollow = true) {
  const res = await api.post<FollowPortfoliosResponse>(
    '/api/follow-portfolios',
    {
      account_codes: accountCodes,
      sync_after_follow: syncAfterFollow,
    },
    { timeout: 600000 },
  );
  return res.data;
}

export async function deleteAccount(accountKey: string) {
  const res = await api.delete<DeleteAccountResponse>(`/api/accounts/${encodeURIComponent(accountKey)}`);
  return res.data;
}
