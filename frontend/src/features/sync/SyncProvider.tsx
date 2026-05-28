import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { streamSyncAll } from '../../api/dashboard';
import { isApiNotFoundError, STALE_BACKEND_HINT } from '../dashboard/apiError';
import { SyncLogItem } from '../../types';

type SyncSummary = { type: 'success' | 'error' | 'info'; text: string } | null;

type SyncContextValue = {
  running: boolean;
  logs: SyncLogItem[];
  currentStep: string | null;
  /** 当前进行中的步骤索引 0–2，未开始时为 null */
  syncStepIndex: number | null;
  /** 最近一次全量同步是否成功完成（三步全绿） */
  syncAllDone: boolean;
  summary: SyncSummary;
  startSync: () => Promise<void>;
  stopSync: () => void;
  clearLogs: () => void;
};

const SyncContext = createContext<SyncContextValue | null>(null);

function stepFromMessage(message: string): string | null {
  const match = message.match(/──\s*(\d\/3[^─]+)──/);
  return match ? match[1].trim() : null;
}

function parseSyncStepIndex(currentStep: string | null): number | null {
  if (!currentStep) return null;
  const m = currentStep.match(/^(\d)\/3/);
  if (!m) return null;
  return Math.min(2, Math.max(0, parseInt(m[1], 10) - 1));
}

export function SyncProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const abortRef = useRef<AbortController | null>(null);
  const [running, setRunning] = useState(false);
  const [logs, setLogs] = useState<SyncLogItem[]>([]);
  const [currentStep, setCurrentStep] = useState<string | null>(null);
  const [summary, setSummary] = useState<SyncSummary>(null);

  const appendLog = useCallback((item: SyncLogItem) => {
    setLogs((prev) => [...prev, item]);
    const step = stepFromMessage(item.message);
    if (step) setCurrentStep(step);
  }, []);

  const invalidateCaches = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: ['accounts'] });
    await queryClient.invalidateQueries({ queryKey: ['dashboard'] });
    await queryClient.invalidateQueries({ queryKey: ['portfolios-overview'] });
    await queryClient.invalidateQueries({ queryKey: ['data-freshness'] });
    await queryClient.invalidateQueries({ queryKey: ['portfolios-overview-stats'] });
  }, [queryClient]);

  const stopSync = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const clearLogs = useCallback(() => {
    setLogs([]);
    setSummary(null);
    setCurrentStep(null);
  }, []);

  const startSync = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setRunning(true);
    setLogs([]);
    setSummary(null);
    setCurrentStep(null);

    try {
      const result = await streamSyncAll(appendLog, controller.signal);
      if (result.ok) {
        await invalidateCaches();
        setSummary({ type: 'success', text: '全量同步完成' });
      } else {
        setSummary({
          type: 'error',
          text: result.message ? `同步未完成：${result.message}` : '同步未完成，请查看日志中的错误项',
        });
      }
    } catch (err) {
      if (controller.signal.aborted) {
        appendLog({ level: 'warn', message: '■ 已请求停止，后端将在当前步骤完成后中断' });
        setSummary({ type: 'info', text: '同步已停止' });
        return;
      }
      let text = '同步失败';
      if (isApiNotFoundError(err)) {
        text = STALE_BACKEND_HINT;
      } else if (err instanceof Error) {
        text = err.message;
      }
      appendLog({ level: 'error', message: `✗ 同步中断：${text}` });
      setSummary({ type: 'error', text });
    } finally {
      setCurrentStep(null);
      setRunning(false);
    }
  }, [appendLog, invalidateCaches]);

  const syncStepIndex = useMemo(() => parseSyncStepIndex(currentStep), [currentStep]);
  const syncAllDone = useMemo(
    () => !running && summary?.type === 'success',
    [running, summary?.type],
  );

  const value = useMemo(
    () => ({
      running,
      logs,
      currentStep,
      syncStepIndex,
      syncAllDone,
      summary,
      startSync,
      stopSync,
      clearLogs,
    }),
    [running, logs, currentStep, syncStepIndex, syncAllDone, summary, startSync, stopSync, clearLogs],
  );

  return <SyncContext.Provider value={value}>{children}</SyncContext.Provider>;
}

export function useSync() {
  const ctx = useContext(SyncContext);
  if (!ctx) {
    throw new Error('useSync must be used within SyncProvider');
  }
  return ctx;
}
