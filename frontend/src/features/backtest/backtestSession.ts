import type { DashboardPayload } from '../../types';

export const BACKTEST_ACCOUNT_ID = '_backtest';

const STORAGE_KEY = 'xueqiu:backtest-dashboard';
const META_KEY = 'xueqiu:backtest-meta';

export interface BacktestSessionMeta {
  strategy_id: string;
  strategy_label?: string;
  initial_capital: number;
  /** 当前回测使用的入场日；null 表示全历史 */
  entry_date?: string | null;
}

export function setBacktestDashboard(payload: DashboardPayload, meta?: BacktestSessionMeta): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    if (meta) {
      sessionStorage.setItem(META_KEY, JSON.stringify(meta));
    }
  } catch {
    // quota exceeded — ignore
  }
}

export function getBacktestDashboard(): DashboardPayload | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as DashboardPayload;
  } catch {
    return null;
  }
}

export function getBacktestMeta(): BacktestSessionMeta | null {
  try {
    const raw = sessionStorage.getItem(META_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as BacktestSessionMeta;
  } catch {
    return null;
  }
}

export function clearBacktestDashboard(): void {
  sessionStorage.removeItem(STORAGE_KEY);
  sessionStorage.removeItem(META_KEY);
}

export function isBacktestAccount(accountCode: string): boolean {
  return accountCode.trim() === BACKTEST_ACCOUNT_ID;
}
