import type { DashboardPayload } from '../../types';

export const BACKTEST_ACCOUNT_ID = '_backtest';

const STORAGE_KEY = 'xueqiu:backtest-dashboard';

export function setBacktestDashboard(payload: DashboardPayload): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
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

export function clearBacktestDashboard(): void {
  sessionStorage.removeItem(STORAGE_KEY);
}

export function isBacktestAccount(accountCode: string): boolean {
  return accountCode.trim() === BACKTEST_ACCOUNT_ID;
}
