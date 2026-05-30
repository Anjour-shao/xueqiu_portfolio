import api from './client';
import {
  AccountItem,
  CopyBacktestRequest,
  CopyBacktestResponse,
  StrategyCatalogItem,
  StrategyCompareResponse,
  DashboardPayload,
  DeleteAccountResponse,
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

/** 抄作业回测：合并全部 ZH 组合信号，单一账户模拟 */
export async function runCopyBacktest(params?: CopyBacktestRequest) {
  const res = await api.post<CopyBacktestResponse>('/api/backtest-copy', params ?? {}, { timeout: 300000 });
  return res.data;
}

export async function fetchBacktestStrategies() {
  const res = await api.get<StrategyCatalogItem[]>('/api/backtest-strategies');
  return res.data;
}

export async function compareBacktestStrategies(
  strategyIds: string[],
  options: {
    initialCapital: number;
    startDate?: string | null;
    endDate?: string | null;
    entrySweepDates?: string[];
  },
) {
  const res = await api.post<StrategyCompareResponse>(
    '/api/backtest-compare',
    {
      strategy_ids: strategyIds,
      initial_capital: options.initialCapital,
      start_date: options.startDate || null,
      end_date: options.endDate || null,
      entry_sweep_dates: options.entrySweepDates?.length ? options.entrySweepDates : null,
    },
    { timeout: 600000 },
  );
  return res.data;
}

export async function deleteAccount(accountKey: string) {
  const res = await api.delete<DeleteAccountResponse>(`/api/accounts/${encodeURIComponent(accountKey)}`);
  return res.data;
}
