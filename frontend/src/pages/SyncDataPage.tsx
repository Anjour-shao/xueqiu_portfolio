import CloudDownloadRoundedIcon from '@mui/icons-material/CloudDownloadRounded';
import PlayArrowRoundedIcon from '@mui/icons-material/PlayArrowRounded';
import StopRoundedIcon from '@mui/icons-material/StopRounded';
import SyncRoundedIcon from '@mui/icons-material/SyncRounded';
import { Box, Button, CircularProgress, Stack, Typography } from '@mui/material';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchCubeCatalogStats, fetchDataFreshness, streamSyncCubeCatalog } from '../api/dashboard';
import { LogSection } from '../components/LogSection';
import { MetricGrid } from '../components/MetricGrid';
import { PageContent } from '../components/PageContent';
import { PageHeader } from '../components/PageHeader';
import { SectionCard } from '../components/SectionCard';
import { isApiNotFoundError, STALE_BACKEND_HINT } from '../features/dashboard/apiError';
import { StatChip } from '../features/dashboard/StatChip';
import { DASHBOARD_THEME } from '../features/dashboard/utils';
import { useToast } from '../features/notify/ToastProvider';
import { buildSyncStepStates, SyncStepIndicator } from '../features/sync/SyncStepIndicator';
import { useSync } from '../features/sync/SyncProvider';
import { SyncLogItem } from '../types';

function freshnessAccent(status: string | undefined) {
  if (status === 'ok') return DASHBOARD_THEME.down;
  if (status === 'stale') return '#B45309';
  return DASHBOARD_THEME.textMuted;
}

export function SyncDataPage() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { running, logs, currentStep, summary, syncAllDone, startSync, stopSync } = useSync();

  const syncStepStates = buildSyncStepStates(running, currentStep, syncAllDone);

  const catalogAbortRef = useRef<AbortController | null>(null);
  const [catalogRunning, setCatalogRunning] = useState(false);
  const [catalogLogs, setCatalogLogs] = useState<SyncLogItem[]>([]);
  const [catalogSummary, setCatalogSummary] = useState<{ type: 'success' | 'error' | 'info'; text: string } | null>(
    null,
  );

  const freshnessQuery = useQuery({
    queryKey: ['data-freshness'],
    queryFn: fetchDataFreshness,
    refetchInterval: running || catalogRunning ? 8000 : false,
  });
  const catalogStatsQuery = useQuery({
    queryKey: ['cube-catalog-stats'],
    queryFn: fetchCubeCatalogStats,
    refetchInterval: catalogRunning ? 5000 : false,
  });

  const fresh = freshnessQuery.data;
  const catalogStats = catalogStatsQuery.data;

  useEffect(() => {
    if (summary?.text && !running) showToast(summary.text, summary.type);
  }, [summary?.text, summary?.type, running, showToast]);

  useEffect(() => {
    if (catalogSummary?.text) {
      showToast(catalogSummary.text, catalogSummary.type);
      setCatalogSummary(null);
    }
  }, [catalogSummary, showToast]);

  const appendCatalogLog = useCallback((item: SyncLogItem) => {
    setCatalogLogs((prev) => [...prev, item]);
  }, []);

  const startCatalogSync = useCallback(async () => {
    catalogAbortRef.current?.abort();
    const controller = new AbortController();
    catalogAbortRef.current = controller;

    setCatalogRunning(true);
    setCatalogLogs([]);
    setCatalogSummary(null);

    try {
      const result = await streamSyncCubeCatalog(appendCatalogLog, controller.signal);
      await queryClient.invalidateQueries({ queryKey: ['cube-catalog-stats'] });
      if (result.ok) {
        setCatalogSummary({ type: 'success', text: result.message || '榜单目录同步完成' });
      } else {
        setCatalogSummary({
          type: 'error',
          text: result.message ? `同步未完成：${result.message}` : '榜单目录同步未完成',
        });
      }
    } catch (err) {
      if (controller.signal.aborted) {
        appendCatalogLog({ level: 'warn', message: '■ 已请求停止榜单同步' });
        setCatalogSummary({ type: 'info', text: '榜单同步已停止' });
        return;
      }
      let text = '榜单同步失败';
      if (isApiNotFoundError(err)) {
        text = STALE_BACKEND_HINT;
      } else if (err instanceof Error) {
        text = err.message;
      }
      appendCatalogLog({ level: 'error', message: `✗ ${text}` });
      setCatalogSummary({ type: 'error', text });
    } finally {
      setCatalogRunning(false);
    }
  }, [appendCatalogLog, queryClient]);

  const stopCatalogSync = useCallback(() => {
    catalogAbortRef.current?.abort();
  }, []);

  const catalogUpdatedLabel = catalogStats?.last_updated_at
    ? catalogStats.last_updated_at.replace('T', ' ').slice(0, 19)
    : '—';

  return (
    <Box sx={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <PageHeader
        title="数据同步"
        icon={<SyncRoundedIcon />}
        meta={
          <Typography component="span" sx={{ fontSize: 12, color: DASHBOARD_THEME.textSecondary }}>
            全量同步与榜单目录互不影响，可分别停止
          </Typography>
        }
        actions={
          <>
            {running && (
              <Button variant="outlined" color="error" size="small" startIcon={<StopRoundedIcon />} onClick={stopSync}>
                停止全量
              </Button>
            )}
            <Button
              variant="contained"
              size="small"
              disabled={running}
              startIcon={running ? <CircularProgress size={14} color="inherit" /> : <PlayArrowRoundedIcon />}
              onClick={startSync}
            >
              {running ? '全量同步中…' : '一键全量同步'}
            </Button>
          </>
        }
      />

      <PageContent>
        <Stack spacing={3}>
          <MetricGrid minColWidth={110}>
            <StatChip
              compact
              label="调仓"
              value={fresh?.rebalance.latest_trade_time?.slice(0, 10) ?? '—'}
              color={freshnessAccent(fresh?.rebalance.status)}
              sub={fresh ? `${fresh.rebalance.trade_count ?? 0} 条` : undefined}
            />
            <StatChip
              compact
              label="后复权"
              value={fresh?.quotes.latest_date ?? '—'}
              color={freshnessAccent(fresh?.quotes.status)}
              sub={fresh ? `${fresh.quotes.symbol_count ?? 0} 只` : undefined}
            />
            <StatChip
              compact
              label="基准指数"
              value={fresh?.benchmark.latest_date ?? '—'}
              color={freshnessAccent(fresh?.benchmark.status)}
              sub={fresh?.benchmark.ts_code ?? undefined}
            />
            <StatChip
              compact
              label="官方净值"
              value={fresh?.cube_nav.latest_date_max ?? '—'}
              color={freshnessAccent(fresh?.cube_nav.status)}
              sub={fresh ? `${fresh.cube_nav.account_count ?? 0} 组合` : undefined}
            />
            <StatChip
              compact
              label="净值落后"
              value={fresh ? String(fresh.cube_nav.stale_accounts ?? 0) : '—'}
              color={(fresh?.cube_nav.stale_accounts ?? 0) > 0 ? '#B45309' : DASHBOARD_THEME.down}
              sub="≥3 天未更新"
            />
            <StatChip
              compact
              label="数据截至"
              value={fresh?.as_of?.replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3') ?? '—'}
            />
          </MetricGrid>

          <SectionCard title="全量数据同步">
            <SyncStepIndicator stepStates={syncStepStates} />
            <LogSection
              title="同步日志"
              logs={logs}
              running={running}
              currentStep={currentStep}
              emptyHint="点击「一键全量同步」开始"
            />
          </SectionCard>

          <SectionCard
            title="榜单组合目录"
            subtitle="从雪球发现页热门/年/月/日榜拉取 ZH 与名称（约 130 条），增量入库"
            action={
              <Stack direction="row" spacing={1}>
                {catalogRunning && (
                  <Button variant="outlined" color="error" size="small" startIcon={<StopRoundedIcon />} onClick={stopCatalogSync}>
                    停止
                  </Button>
                )}
                <Button
                  variant="outlined"
                  size="small"
                  disabled={catalogRunning}
                  startIcon={catalogRunning ? <CircularProgress size={14} /> : <CloudDownloadRoundedIcon fontSize="small" />}
                  onClick={startCatalogSync}
                >
                  {catalogRunning ? '同步中…' : '同步榜单'}
                </Button>
              </Stack>
            }
          >
            <Typography sx={{ fontSize: 12, color: DASHBOARD_THEME.textSecondary, textAlign: 'center', mb: 1 }}>
              库内 {catalogStats?.total_count ?? '—'} 个 · 最近更新 {catalogUpdatedLabel}
            </Typography>
            <LogSection
              title="同步日志"
              logs={catalogLogs}
              running={catalogRunning}
              emptyHint="点击「同步榜单」开始"
            />
          </SectionCard>
        </Stack>
      </PageContent>
    </Box>
  );
}
