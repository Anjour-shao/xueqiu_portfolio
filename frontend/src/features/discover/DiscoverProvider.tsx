import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { streamDiscoverPortfolios } from '../../api/dashboard';
import { useQueryClient } from '@tanstack/react-query';
import { isApiNotFoundError, STALE_BACKEND_HINT } from '../dashboard/apiError';
import {
  applyBatchToCheckpoint,
  defaultCheckpoint,
  loadDiscoverCheckpoint,
  saveDiscoverCheckpoint,
  type DiscoverCheckpoint,
} from './discoverCheckpoint';
import {
  loadDiscoverCandidates,
  mergeCandidates,
  saveDiscoverCandidates,
  type DiscoverCandidate,
} from './discoverCandidates';
import type { DiscoverLogItem, DiscoverPortfoliosParams, DiscoverStreamEvent } from '../../types';

type DiscoverSummary = { type: 'success' | 'error' | 'info'; text: string } | null;

export type DiscoverScanParams = DiscoverPortfoliosParams;

const CONTINUOUS_CHUNK_SIZE = 25;

type DiscoverContextValue = {
  running: boolean;
  logs: DiscoverLogItem[];
  progress: { current: number; total: number; code: string } | null;
  summary: DiscoverSummary;
  batchHits: DiscoverCandidate[];
  candidates: DiscoverCandidate[];
  checkpoint: DiscoverCheckpoint;
  setCheckpoint: (cp: DiscoverCheckpoint) => void;
  startScan: (params: DiscoverScanParams, options?: { autoAdvanceCheckpoint?: boolean }) => Promise<void>;
  stopScan: () => void;
  clearLogs: () => void;
  refreshCandidates: () => void;
  setCandidates: (list: DiscoverCandidate[]) => void;
};

const DiscoverContext = createContext<DiscoverContextValue | null>(null);

const MAX_LOGS = 500;

function streamToLog(ev: DiscoverStreamEvent): DiscoverLogItem | null {
  if (ev.type === 'log') {
    return { level: ev.level ?? 'info', message: ev.message };
  }
  if (ev.type === 'progress') {
    return { level: 'info', message: `[${ev.current}/${ev.total}] ${ev.code}` };
  }
  if (ev.type === 'skip') {
    return { level: 'info', message: `跳过 ${ev.code}：${ev.reason}` };
  }
  if (ev.type === 'hit') {
    const profiles = ev.item.matched_profiles?.length ? ` [${ev.item.matched_profiles.join(',')}]` : '';
    return {
      level: 'success',
      message: `候选 ${ev.item.account_code} ${ev.item.account_name} nav=${ev.item.latest_nav}${profiles}`,
    };
  }
  return null;
}

export function DiscoverProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const abortRef = useRef<AbortController | null>(null);
  const batchHitsRef = useRef(0);
  const progressRef = useRef<DiscoverContextValue['progress']>(null);
  const autoAdvanceRef = useRef(true);
  const checkpointRef = useRef<DiscoverCheckpoint>(loadDiscoverCheckpoint() ?? defaultCheckpoint());

  const [running, setRunning] = useState(false);
  const [logs, setLogs] = useState<DiscoverLogItem[]>([]);
  const [progress, setProgress] = useState<DiscoverContextValue['progress']>(null);
  const [summary, setSummary] = useState<DiscoverSummary>(null);
  const [batchHits, setBatchHits] = useState<DiscoverCandidate[]>([]);
  const [candidates, setCandidatesState] = useState<DiscoverCandidate[]>(() => loadDiscoverCandidates());
  const [checkpoint, setCheckpointState] = useState<DiscoverCheckpoint>(() => checkpointRef.current);

  const appendLog = useCallback((item: DiscoverLogItem) => {
    setLogs((prev) => [...prev.slice(-(MAX_LOGS - 1)), item]);
  }, []);

  const setCheckpoint = useCallback((cp: DiscoverCheckpoint) => {
    checkpointRef.current = cp;
    saveDiscoverCheckpoint(cp);
    setCheckpointState(cp);
  }, []);

  const refreshCandidates = useCallback(() => {
    setCandidatesState(loadDiscoverCandidates());
  }, []);

  const setCandidates = useCallback((list: DiscoverCandidate[]) => {
    saveDiscoverCandidates(list);
    setCandidatesState(list);
  }, []);

  const stopScan = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const clearLogs = useCallback(() => {
    setLogs([]);
    setSummary(null);
    setProgress(null);
    setBatchHits([]);
  }, []);

  const mergeBatchCandidates = useCallback((localBatch: DiscoverCandidate[]) => {
    if (!localBatch.length) return;
    const merged = mergeCandidates(loadDiscoverCandidates(), localBatch);
    saveDiscoverCandidates(merged);
    setCandidatesState(merged);
  }, []);

  const runOneChunk = useCallback(
    async (
      params: DiscoverScanParams,
      controller: AbortController,
      localBatch: DiscoverCandidate[],
      onDone: (ev: Extract<DiscoverStreamEvent, { type: 'done' }>) => void,
    ) => {
      return streamDiscoverPortfolios(
        params,
        (ev) => {
          if (ev.type === 'progress') {
            const p = { current: ev.current, total: ev.total, code: ev.code };
            progressRef.current = p;
            setProgress(p);
          }
          const logItem = streamToLog(ev);
          if (logItem) appendLog(logItem);
          if (ev.type === 'hit') {
            batchHitsRef.current += 1;
            const row: DiscoverCandidate = {
              ...ev.item,
              added_at: new Date().toISOString(),
              reviewed: false,
            };
            localBatch.push(row);
            setBatchHits((prev) => [...prev, row]);
          }
          if (ev.type === 'done') {
            onDone(ev);
          }
        },
        controller.signal,
      );
    },
    [appendLog],
  );

  const startScan = useCallback(
    async (params: DiscoverScanParams, options?: { autoAdvanceCheckpoint?: boolean }) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      autoAdvanceRef.current = options?.autoAdvanceCheckpoint ?? true;

      const continuous = Boolean(params.continuous);
      const chunkSize = continuous ? CONTINUOUS_CHUNK_SIZE : (params.batch_size ?? 30);

      setRunning(true);
      setLogs([]);
      setSummary(null);
      setProgress(null);
      setBatchHits([]);
      batchHitsRef.current = 0;

      const localBatch: DiscoverCandidate[] = [];

      appendLog({
        level: 'info',
        message: continuous
          ? `── 连续顺序挖取 cube_catalog（每轮 ${chunkSize} 个，直到全部挖过或停止）──`
          : `── 本批顺序挖取 catalog 中 ${chunkSize} 个未挖过的组合 ──`,
      });

      const applyDone = (ev: Extract<DiscoverStreamEvent, { type: 'done' }>) => {
        if (!ev.ok) {
          setSummary({ type: 'error', text: ev.message ?? '扫描失败' });
          return false;
        }
        void queryClient.invalidateQueries({ queryKey: ['cube-catalog-stats'] });
        if (autoAdvanceRef.current) {
          setCheckpoint(
            applyBatchToCheckpoint(checkpointRef.current, {
              scanned: ev.scanned,
              matched_count: ev.matched_count,
            }),
          );
        }
        return true;
      };

      try {
        let round = 0;
        while (!controller.signal.aborted) {
          round += 1;
          if (continuous && round > 1) {
            appendLog({ level: 'info', message: `── 第 ${round} 轮：继续顺序挖未标记的组合 ──` });
          }

          let doneEv: Extract<DiscoverStreamEvent, { type: 'done' }> | null = null;
          const outcome = await runOneChunk(
            {
              ...params,
              scan_mode: 'catalog',
              batch_size: chunkSize,
            },
            controller,
            localBatch,
            (ev) => {
              doneEv = ev;
            },
          );

          let chunkOk = false;
          const finished = doneEv as Extract<DiscoverStreamEvent, { type: 'done' }> | null;
          if (finished) {
            chunkOk = applyDone(finished);
            if (!chunkOk) break;
            const remaining = finished.catalog_remaining_count;
            if (continuous && (remaining === 0 || (finished.scanned === 0 && (remaining ?? 1) === 0))) {
              appendLog({ level: 'info', message: '■ catalog 已全部挖过' });
              setSummary({ type: 'success', text: 'catalog 已全部顺序挖完' });
              break;
            }
          } else if (!outcome.ok && outcome.message) {
            setSummary({ type: 'error', text: outcome.message });
            break;
          }

          mergeBatchCandidates([...localBatch]);
          localBatch.length = 0;

          if (!continuous) {
            if (chunkOk) {
              setSummary({ type: 'success', text: '本批扫描完成' });
            }
            break;
          }
        }

        if (localBatch.length) {
          mergeBatchCandidates(localBatch);
        }
      } catch (err) {
        if (controller.signal.aborted) {
          appendLog({ level: 'warn', message: '■ 已停止扫描' });
          setSummary({ type: 'info', text: '已停止；本批未完成部分不计入累计尝试数' });
          mergeBatchCandidates(localBatch);
          return;
        }
        let text = '扫描失败';
        if (isApiNotFoundError(err)) {
          text = STALE_BACKEND_HINT;
        } else if (err instanceof Error) {
          text = err.message;
        }
        appendLog({ level: 'error', message: `✗ ${text}` });
        setSummary({ type: 'error', text });
      } finally {
        setProgress(null);
        setRunning(false);
        abortRef.current = null;
      }
    },
    [appendLog, mergeBatchCandidates, queryClient, runOneChunk, setCheckpoint],
  );

  const value = useMemo(
    () => ({
      running,
      logs,
      progress,
      summary,
      batchHits,
      candidates,
      checkpoint,
      setCheckpoint,
      startScan,
      stopScan,
      clearLogs,
      refreshCandidates,
      setCandidates,
    }),
    [
      running,
      logs,
      progress,
      summary,
      batchHits,
      candidates,
      checkpoint,
      setCheckpoint,
      startScan,
      stopScan,
      clearLogs,
      refreshCandidates,
      setCandidates,
    ],
  );

  return <DiscoverContext.Provider value={value}>{children}</DiscoverContext.Provider>;
}

export function useDiscover() {
  const ctx = useContext(DiscoverContext);
  if (!ctx) {
    throw new Error('useDiscover must be used within DiscoverProvider');
  }
  return ctx;
}
