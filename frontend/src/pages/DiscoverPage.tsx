import CloudDownloadRoundedIcon from '@mui/icons-material/CloudDownloadRounded';
import OpenInNewRoundedIcon from '@mui/icons-material/OpenInNewRounded';
import PlayArrowRoundedIcon from '@mui/icons-material/PlayArrowRounded';
import StopRoundedIcon from '@mui/icons-material/StopRounded';
import TravelExploreRoundedIcon from '@mui/icons-material/TravelExploreRounded';
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Tab,
  Tabs,
  TextField,
  Typography,
} from '@mui/material';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  fetchDiscoveryCubes,
  fetchDiscoveryStats,
  patchDiscoveryCube,
  streamDiscoveryImport,
  streamDiscoveryMine,
} from '../api/discovery';
import { LogSection } from '../components/LogSection';
import { MetricGrid } from '../components/MetricGrid';
import { PageContent } from '../components/PageContent';
import { PageHeader } from '../components/PageHeader';
import { SectionCard } from '../components/SectionCard';
import { DataTable } from '../features/dashboard/DataTable';
import { isApiNotFoundError, STALE_BACKEND_HINT } from '../features/dashboard/apiError';
import { StatChip } from '../features/dashboard/StatChip';
import { DASHBOARD_THEME, monoSx, pctColor } from '../features/dashboard/utils';
import { useToast } from '../features/notify/ToastProvider';
import { portfolioUrl } from '../lib/xueqiuLinks';
import type { MinedCubeItem, SyncLogItem } from '../types';

const REASON_LABELS: Record<string, string> = {
  self_created: '自建',
  loss: '亏损',
  non_a: '非A/港美',
  non_cn: '非A股组合',
  inactive_6m: '6月无调仓',
  high_freq: '调仓过频',
  in_db: '已在库',
  show_error: '待补全',
  nav_error: '净值失败',
  holdings_error: '持仓失败',
  rebalance_error: '调仓失败',
};

type TabKey = 'pass' | 'pending' | 'selected' | 'rejected' | 'all';

function listParams(tab: TabKey): { auto_pass?: boolean; selected?: number } {
  switch (tab) {
    case 'pass':
      return { auto_pass: true };
    case 'pending':
      return { auto_pass: true, selected: undefined };
    case 'selected':
      return { selected: 1 };
    case 'rejected':
      return { selected: -1 };
    default:
      return {};
  }
}

function formatPct(v: number | null | undefined) {
  if (v == null || Number.isNaN(v)) return '—';
  const sign = v > 0 ? '+' : '';
  return `${sign}${v.toFixed(2)}%`;
}

export function DiscoverPage() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [tab, setTab] = useState<TabKey>('pass');
  const [maxDepth, setMaxDepth] = useState(1);
  const [search, setSearch] = useState('');
  const [mining, setMining] = useState(false);
  const [importing, setImporting] = useState(false);
  const [logs, setLogs] = useState<SyncLogItem[]>([]);
  const [importLogs, setImportLogs] = useState<SyncLogItem[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const importAbortRef = useRef<AbortController | null>(null);

  const statsQuery = useQuery({
    queryKey: ['discovery-stats'],
    queryFn: fetchDiscoveryStats,
    refetchInterval: mining ? 5000 : false,
  });

  const cubesQuery = useQuery({
    queryKey: ['discovery-cubes', tab, search],
    queryFn: () => {
      const base = listParams(tab);
      return fetchDiscoveryCubes({
        ...base,
        q: search.trim() || undefined,
      });
    },
  });

  const items = useMemo(() => {
    const raw = cubesQuery.data ?? [];
    if (tab !== 'pending') return raw;
    return raw.filter((r: MinedCubeItem) => r.selected == null && !r.imported_at);
  }, [cubesQuery.data, tab]);

  const pendingImportItems = useMemo(
    () => items.filter((r: MinedCubeItem) => r.selected === 1 && !r.imported_at),
    [items],
  );

  const patchMut = useMutation({
    mutationFn: ({ code, selected }: { code: string; selected: number }) =>
      patchDiscoveryCube(code, { selected }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['discovery-cubes'] });
      void queryClient.invalidateQueries({ queryKey: ['discovery-stats'] });
    },
  });

  const appendImportLog = useCallback((item: SyncLogItem) => {
    setImportLogs((prev) => [...prev, item]);
  }, []);

  const runImport = useCallback(
    async (codes: string[]) => {
      const normalized = codes.map((c) => c.trim().toUpperCase()).filter(Boolean);
      if (!normalized.length) return;

      importAbortRef.current?.abort();
      const controller = new AbortController();
      importAbortRef.current = controller;
      setImporting(true);
      setImportLogs([]);
      try {
        const result = await streamDiscoveryImport(normalized, appendImportLog, {
          signal: controller.signal,
        });
        await queryClient.invalidateQueries({ queryKey: ['discovery-cubes'] });
        await queryClient.invalidateQueries({ queryKey: ['discovery-stats'] });
        await queryClient.invalidateQueries({ queryKey: ['accounts'] });
        showToast(
          result.message || (result.ok ? '入库完成' : '入库未完成'),
          result.ok ? 'success' : 'error',
        );
      } catch (err) {
        if (controller.signal.aborted) {
          showToast('入库已停止', 'info');
          return;
        }
        const text = isApiNotFoundError(err)
          ? STALE_BACKEND_HINT
          : err instanceof Error
            ? err.message
            : '入库失败';
        showToast(text, 'error');
      } finally {
        setImporting(false);
      }
    },
    [appendImportLog, queryClient, showToast],
  );

  const appendLog = useCallback((item: SyncLogItem) => {
    setLogs((prev) => [...prev, item]);
  }, []);

  const startMine = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setMining(true);
    setLogs([]);
    try {
      const result = await streamDiscoveryMine(appendLog, {
        max_depth: maxDepth,
        signal: controller.signal,
      });
      await queryClient.invalidateQueries({ queryKey: ['discovery-stats'] });
      await queryClient.invalidateQueries({ queryKey: ['discovery-cubes'] });
      showToast(result.message || (result.ok ? '挖掘完成' : '挖掘未完成'), result.ok ? 'success' : 'error');
    } catch (err) {
      if (controller.signal.aborted) {
        showToast('挖掘已停止', 'info');
        return;
      }
      const text = isApiNotFoundError(err) ? STALE_BACKEND_HINT : err instanceof Error ? err.message : '挖掘失败';
      showToast(text, 'error');
    } finally {
      setMining(false);
    }
  }, [appendLog, maxDepth, queryClient, showToast]);

  const stopMine = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const stopImport = useCallback(() => {
    importAbortRef.current?.abort();
  }, []);

  useEffect(
    () => () => {
      abortRef.current?.abort();
      importAbortRef.current?.abort();
    },
    [],
  );

  const stats = statsQuery.data;

  const tabTotal = useMemo(() => {
    if (!stats) return null;
    switch (tab) {
      case 'pass':
        return stats.auto_pass_count;
      case 'pending':
        return stats.pending_count;
      case 'selected':
        return stats.selected_count;
      case 'rejected':
        return stats.rejected_count;
      default:
        return stats.total_count;
    }
  }, [stats, tab]);

  const columns = [
    { key: 'code', label: '组合', width: '22%' },
    { key: 'depth', label: '深度', width: '6%', align: 'center' as const },
    { key: 'return', label: '累计', width: '10%', align: 'right' as const },
    { key: 'source', label: '发现路径', width: '24%' },
    { key: 'tags', label: '标签', width: '18%' },
    { key: 'actions', label: '操作', width: '20%', align: 'right' as const },
  ];

  const rows = items.map((row: MinedCubeItem) => {
    const tags = row.reject_reasons.map((r) => REASON_LABELS[r] ?? r);
    const sourceLabel = row.source_account_code
      ? `${row.source_account_code} → @${row.source_user_uid ?? '—'}`
      : `@${row.source_user_uid ?? '—'}`;
    const actionCell = (
      <Stack direction="row" spacing={0.5} justifyContent="flex-end" flexWrap="wrap">
        <Button
          size="small"
          variant={row.selected === 1 ? 'contained' : 'outlined'}
          disabled={patchMut.isPending}
          onClick={(e) => {
            e.stopPropagation();
            patchMut.mutate({ code: row.account_code, selected: 1 });
          }}
        >
          选中
        </Button>
        <Button
          size="small"
          color="inherit"
          variant={row.selected === -1 ? 'contained' : 'outlined'}
          disabled={patchMut.isPending}
          onClick={(e) => {
            e.stopPropagation();
            patchMut.mutate({ code: row.account_code, selected: -1 });
          }}
        >
          拒绝
        </Button>
        {row.selected === 1 && !row.imported_at && (
          <Button
            size="small"
            color="primary"
            variant="contained"
            disabled={importing}
            startIcon={importing ? <CircularProgress size={12} /> : <CloudDownloadRoundedIcon />}
            onClick={(e) => {
              e.stopPropagation();
              void runImport([row.account_code]);
            }}
          >
            入库
          </Button>
        )}
        <Button
          size="small"
          href={portfolioUrl(row.account_code)}
          target="_blank"
          rel="noopener noreferrer"
          startIcon={<OpenInNewRoundedIcon sx={{ fontSize: 14 }} />}
          onClick={(e) => e.stopPropagation()}
        >
          雪球
        </Button>
      </Stack>
    );

    return [
      <Box key="n">
        <Typography sx={{ fontSize: 13, fontWeight: 600 }}>{row.account_name}</Typography>
        <Typography sx={{ ...monoSx, fontSize: 11, color: DASHBOARD_THEME.textMuted }}>{row.account_code}</Typography>
      </Box>,
      String(row.depth),
      <Typography key="r" sx={{ fontSize: 13, color: pctColor(row.cum_return_pct ?? 0), textAlign: 'right' }}>
        {formatPct(row.cum_return_pct)}
      </Typography>,
      <Box key="s">
        <Typography sx={{ fontSize: 12, color: DASHBOARD_THEME.textSecondary }}>
          {sourceLabel}
          {row.owner_name ? ` · 主理 ${row.owner_name}` : ''}
        </Typography>
        {row.latest_rebalance_time && (
          <Typography sx={{ fontSize: 11, color: DASHBOARD_THEME.textMuted }}>
            最近调仓 {row.latest_rebalance_time.slice(0, 10)}
            {row.rebalance_count_6m != null ? ` · 6月 ${row.rebalance_count_6m} 次` : ''}
          </Typography>
        )}
      </Box>,
      <Stack key="t" direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
        {row.auto_pass && <Chip size="small" label="初筛通过" color="success" variant="outlined" />}
        {tags.map((t) => (
          <Chip key={t} size="small" label={t} variant="outlined" />
        ))}
        {row.imported_at && <Chip size="small" label="已入库" color="primary" variant="outlined" />}
      </Stack>,
      actionCell,
    ];
  });

  return (
    <Box sx={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <PageHeader
        title="挖组合"
        icon={<TravelExploreRoundedIcon />}
        meta={
          <Typography component="span" sx={{ fontSize: 12, color: DASHBOARD_THEME.textSecondary }}>
            从已入库组合主理人自选向外 BFS · 近6月有调仓且月均≤3次 · 过滤非A股/港美股 · 改规则后请重新挖掘
          </Typography>
        }
        actions={
          <Stack direction="row" spacing={1} alignItems="center">
            <FormControl size="small" sx={{ minWidth: 120 }} title="深度1：种子主理人自选；深度2：初筛通过候选的主理人再扩一层（不是对初筛结果再跑规则）">
              <InputLabel>挖掘深度</InputLabel>
              <Select label="挖掘深度" value={maxDepth} onChange={(e) => setMaxDepth(Number(e.target.value))}>
                <MenuItem value={1}>1 层（仅种子）</MenuItem>
                <MenuItem value={2}>2 层</MenuItem>
                <MenuItem value={3}>3 层</MenuItem>
                <MenuItem value={4}>4 层</MenuItem>
                <MenuItem value={5}>5 层</MenuItem>
              </Select>
            </FormControl>
            {importing && (
              <Button variant="outlined" color="error" size="small" startIcon={<StopRoundedIcon />} onClick={stopImport}>
                停止入库
              </Button>
            )}
            {mining && (
              <Button variant="outlined" color="error" size="small" startIcon={<StopRoundedIcon />} onClick={stopMine}>
                停止
              </Button>
            )}
            <Button
              variant="contained"
              size="small"
              disabled={mining}
              startIcon={mining ? <CircularProgress size={14} color="inherit" /> : <PlayArrowRoundedIcon />}
              onClick={startMine}
            >
              {mining ? '挖掘中…' : '开始挖掘'}
            </Button>
          </Stack>
        }
      />

      <PageContent>
        <Stack spacing={2}>
          <MetricGrid minColWidth={100}>
            <StatChip compact label="已爬取" value={String(stats?.total_count ?? '—')} />
            <StatChip compact label="初筛通过" value={String(stats?.auto_pass_count ?? '—')} color={DASHBOARD_THEME.down} />
            <StatChip compact label="待决定" value={String(stats?.pending_count ?? '—')} />
            <StatChip compact label="已选中" value={String(stats?.selected_count ?? '—')} />
            <StatChip compact label="已拒绝" value={String(stats?.rejected_count ?? '—')} />
            <StatChip compact label="已入库" value={String(stats?.imported_count ?? '—')} />
          </MetricGrid>

          <SectionCard title="挖掘日志">
            <LogSection title="" logs={logs} running={mining} emptyHint="点击「开始挖掘」从 accounts 种子向外拉自选组合" />
          </SectionCard>

          {(importing || importLogs.length > 0) && (
            <SectionCard title="入库日志">
              <LogSection
                title=""
                logs={importLogs}
                running={importing}
                emptyHint="在「已选中」页点击入库或批量入库"
              />
            </SectionCard>
          )}

          <SectionCard
            title="候选组合"
            action={
              <Stack direction="row" spacing={1} alignItems="center">
                {tab === 'selected' && pendingImportItems.length > 0 && (
                  <Button
                    size="small"
                    variant="contained"
                    disabled={importing || mining}
                    startIcon={importing ? <CircularProgress size={14} color="inherit" /> : <CloudDownloadRoundedIcon />}
                    onClick={() => void runImport(pendingImportItems.map((r) => r.account_code))}
                  >
                    批量入库 ({pendingImportItems.length})
                  </Button>
                )}
                <TextField
                  size="small"
                  placeholder="搜索代码/名称"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  sx={{ width: 200 }}
                />
              </Stack>
            }
          >
            <Stack direction="row" alignItems="center" justifyContent="space-between" flexWrap="wrap" gap={1} sx={{ mb: 1 }}>
              <Tabs value={tab} onChange={(_, v) => setTab(v as TabKey)} sx={{ minHeight: 36 }}>
                <Tab value="pass" label="初筛通过" sx={{ minHeight: 36, py: 0.5, fontSize: 13 }} />
                <Tab value="pending" label="待决定" sx={{ minHeight: 36, py: 0.5, fontSize: 13 }} />
                <Tab value="selected" label="已选中" sx={{ minHeight: 36, py: 0.5, fontSize: 13 }} />
                <Tab value="rejected" label="已拒绝" sx={{ minHeight: 36, py: 0.5, fontSize: 13 }} />
                <Tab value="all" label="全部" sx={{ minHeight: 36, py: 0.5, fontSize: 13 }} />
              </Tabs>
              {!cubesQuery.isLoading && (
                <Typography sx={{ fontSize: 12, color: DASHBOARD_THEME.textMuted }}>
                  本页 {items.length} 条
                  {tabTotal != null ? ` / 共 ${tabTotal} 条` : ''}
                  {tab === 'pass' && tabTotal != null && items.length < tabTotal
                    ? '（有搜索条件时条数可能少于总数）'
                    : ''}
                </Typography>
              )}
            </Stack>
            {cubesQuery.isLoading ? (
              <Box sx={{ py: 4, display: 'flex', justifyContent: 'center' }}>
                <CircularProgress size={28} />
              </Box>
            ) : rows.length === 0 ? (
              <Typography sx={{ py: 3, textAlign: 'center', color: DASHBOARD_THEME.textMuted, fontSize: 13 }}>
                暂无数据，请先运行挖掘
              </Typography>
            ) : (
              <DataTable columns={columns} rows={rows} dense minWidth={900} />
            )}
          </SectionCard>
        </Stack>
      </PageContent>
    </Box>
  );
}
