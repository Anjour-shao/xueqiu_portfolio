import PlayArrowRoundedIcon from '@mui/icons-material/PlayArrowRounded';
import StopRoundedIcon from '@mui/icons-material/StopRounded';
import SyncRoundedIcon from '@mui/icons-material/SyncRounded';
import { Box, Button, CircularProgress, Stack, Typography } from '@mui/material';
import { useQuery } from '@tanstack/react-query';
import { useEffect } from 'react';
import { fetchDataFreshness } from '../api/dashboard';
import { LogSection } from '../components/LogSection';
import { MetricGrid } from '../components/MetricGrid';
import { PageContent } from '../components/PageContent';
import { PageHeader } from '../components/PageHeader';
import { SectionCard } from '../components/SectionCard';
import { StatChip } from '../features/dashboard/StatChip';
import { DASHBOARD_THEME } from '../features/dashboard/utils';
import { useToast } from '../features/notify/ToastProvider';
import { buildSyncStepStates, SyncStepIndicator } from '../features/sync/SyncStepIndicator';
import { useSync } from '../features/sync/SyncProvider';

function freshnessAccent(status: string | undefined) {
  if (status === 'ok') return DASHBOARD_THEME.down;
  if (status === 'stale') return '#B45309';
  return DASHBOARD_THEME.textMuted;
}

export function SyncDataPage() {
  const { showToast } = useToast();
  const { running, logs, currentStep, summary, syncAllDone, startSync, stopSync } = useSync();

  const syncStepStates = buildSyncStepStates(running, currentStep, syncAllDone);

  const freshnessQuery = useQuery({
    queryKey: ['data-freshness'],
    queryFn: fetchDataFreshness,
    refetchInterval: running ? 8000 : false,
  });

  const fresh = freshnessQuery.data;

  useEffect(() => {
    if (summary?.text && !running) showToast(summary.text, summary.type);
  }, [summary?.text, summary?.type, running, showToast]);

  return (
    <Box sx={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <PageHeader
        title="数据同步"
        icon={<SyncRoundedIcon />}
        meta={
          <Typography component="span" sx={{ fontSize: 12, color: DASHBOARD_THEME.textSecondary }}>
            全量同步已入库组合的调仓、行情与官方净值
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
        </Stack>
      </PageContent>
    </Box>
  );
}
