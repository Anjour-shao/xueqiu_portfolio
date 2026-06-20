import CloudDownloadRoundedIcon from '@mui/icons-material/CloudDownloadRounded';
import PlayArrowRoundedIcon from '@mui/icons-material/PlayArrowRounded';
import StopRoundedIcon from '@mui/icons-material/StopRounded';
import TravelExploreRoundedIcon from '@mui/icons-material/TravelExploreRounded';
import {
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  FormControl,
  FormControlLabel,
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
  fetchDiscoverySymbolPool,
  patchDiscoveryCube,
  saveDiscoverySymbolPool,
  streamDiscoveryImport,
  streamDiscoveryMine,
} from '../api/discovery';
import { DiscoverCubeTriageBar, openXueqiuPreviewPopup, readAutoNextEnabled } from '../components/DiscoverCubeTriageBar';
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
import type { DiscoveryStats, DiscoverySymbolPoolItem, MinedCubeItem, SyncLogItem } from '../types';

const REASON_LABELS: Record<string, string> = {
  self_created: '自建',
  loss: '亏损',
  non_a: '非A/港美',
  non_cn: '非A股组合',
  inactive_6m: '6月无调仓',
  old_low_return: '老组合低收益',
  low_return_1m: '近1月涨幅低',
  high_freq: '调仓过频',
  in_db: '已在库',
  show_error: '待补全',
  nav_error: '净值失败',
  holdings_error: '持仓失败',
  rebalance_error: '调仓失败',
};

const SOURCE_TYPE_LABELS: Record<string, string> = {
  watchlist: '自选链',
  following: '关注链',
  stock_hot: '个股',
};

function formatDiscoverySource(row: MinedCubeItem): string {
  const kind = SOURCE_TYPE_LABELS[row.source_type ?? 'watchlist'] ?? row.source_type ?? '自选链';
  if (row.source_type === 'stock_hot' && row.source_symbol) {
    return `${kind}·${row.source_symbol} · @${row.source_user_uid ?? '—'}`;
  }
  if (row.source_account_code) {
    return `${kind} · ${row.source_account_code} → @${row.source_user_uid ?? '—'}`;
  }
  return `${kind} · @${row.source_user_uid ?? '—'}`;
}

type TabKey = 'pass' | 'pending' | 'selected' | 'rejected' | 'all';

type CubeListParams = {
  auto_pass?: boolean;
  selected?: number;
  pending_only?: boolean;
};

function listParams(tab: TabKey): CubeListParams {
  switch (tab) {
    case 'pass':
      return { auto_pass: true };
    case 'pending':
      return { pending_only: true };
    case 'selected':
      return { selected: 1 };
    case 'rejected':
      return { selected: -1 };
    default:
      return {};
  }
}

function adjustDiscoveryStats(
  stats: DiscoveryStats,
  row: MinedCubeItem,
  nextSelected: number,
): DiscoveryStats {
  const prevSelected = row.selected;
  const next = nextSelected === 0 ? null : nextSelected;
  const wasPending = row.auto_pass && !row.imported_at && prevSelected == null;
  const isPending = row.auto_pass && !row.imported_at && next == null;

  let pending_count = stats.pending_count;
  let selected_count = stats.selected_count;
  let rejected_count = stats.rejected_count;

  if (wasPending && !isPending) pending_count -= 1;
  if (!wasPending && isPending) pending_count += 1;
  if (prevSelected === 1) selected_count -= 1;
  if (prevSelected === -1) rejected_count -= 1;
  if (next === 1) selected_count += 1;
  if (next === -1) rejected_count += 1;

  return { ...stats, pending_count, selected_count, rejected_count };
}

function applySelectionToList(
  items: MinedCubeItem[],
  code: string,
  selected: number,
  tab: TabKey,
): MinedCubeItem[] {
  const nextSelected = selected === 0 ? null : selected;
  const mapped = items.map((r) =>
    r.account_code === code ? { ...r, selected: nextSelected } : r,
  );
  if (tab === 'pending') {
    return mapped.filter((r) => r.selected == null && !r.imported_at);
  }
  if (tab === 'selected' && nextSelected !== 1) {
    return mapped.filter((r) => r.account_code !== code);
  }
  if (tab === 'rejected' && nextSelected !== -1) {
    return mapped.filter((r) => r.account_code !== code);
  }
  return mapped;
}

const TABLE_COLUMNS = [
  { key: 'code', label: '组合', width: '22%' },
  { key: 'depth', label: '深度', width: '6%', align: 'center' as const },
  { key: 'return', label: '累计', width: '10%', align: 'right' as const },
  { key: 'source', label: '发现路径', width: '24%' },
  { key: 'tags', label: '标签', width: '18%' },
  { key: 'actions', label: '操作', width: '20%', align: 'right' as const },
];

function formatPct(v: number | null | undefined) {
  if (v == null || Number.isNaN(v)) return '—';
  const sign = v > 0 ? '+' : '';
  return `${sign}${v.toFixed(2)}%`;
}

export function DiscoverPage() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [tab, setTab] = useState<TabKey>('pass');
  const [maxDepth, setMaxDepth] = useState(2);
  const [mineWatchlist, setMineWatchlist] = useState(true);
  const [mineFollowing, setMineFollowing] = useState(true);
  const [mineStockHot, setMineStockHot] = useState(true);
  const [newSymbol, setNewSymbol] = useState('');
  const [poolDraft, setPoolDraft] = useState<DiscoverySymbolPoolItem[] | null>(null);
  const [poolSaving, setPoolSaving] = useState(false);
  const [search, setSearch] = useState('');
  const [mining, setMining] = useState(false);
  const [importing, setImporting] = useState(false);
  const [logs, setLogs] = useState<SyncLogItem[]>([]);
  const [importLogs, setImportLogs] = useState<SyncLogItem[]>([]);
  const [previewCube, setPreviewCube] = useState<MinedCubeItem | null>(null);
  const previewPopupRef = useRef<Window | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const importAbortRef = useRef<AbortController | null>(null);

  const statsQuery = useQuery({
    queryKey: ['discovery-stats'],
    queryFn: fetchDiscoveryStats,
    refetchInterval: mining ? 5000 : false,
    staleTime: 60_000,
  });

  const poolQuery = useQuery({
    queryKey: ['discovery-symbol-pool'],
    queryFn: fetchDiscoverySymbolPool,
  });

  const poolItems = poolDraft ?? poolQuery.data?.items ?? [];
  const poolMeta = poolQuery.data?.meta;

  const cubesQuery = useQuery({
    queryKey: ['discovery-cubes', tab, search],
    queryFn: () => {
      const base = listParams(tab);
      return fetchDiscoveryCubes({
        ...base,
        q: search.trim() || undefined,
      });
    },
    staleTime: 30_000,
    placeholderData: (prev) => prev,
  });

  const items = cubesQuery.data ?? [];

  const pendingImportItems = useMemo(
    () => items.filter((r: MinedCubeItem) => r.selected === 1 && !r.imported_at),
    [items],
  );

  const patchMut = useMutation({
    mutationFn: ({ code, selected }: { code: string; selected: number }) =>
      patchDiscoveryCube(code, { selected }),
    onMutate: async ({ code, selected }) => {
      await queryClient.cancelQueries({ queryKey: ['discovery-cubes'] });

      const snapshots = queryClient.getQueriesData<MinedCubeItem[]>({ queryKey: ['discovery-cubes'] });
      const listKey = ['discovery-cubes', tab, search] as const;
      const prevItems = queryClient.getQueryData<MinedCubeItem[]>(listKey);
      const row = prevItems?.find((r) => r.account_code === code);

      for (const [key, data] of snapshots) {
        if (!data) continue;
        const keyTab = key[1] as TabKey;
        queryClient.setQueryData(
          key,
          applySelectionToList(data, code, selected, keyTab),
        );
      }

      const prevStats = queryClient.getQueryData<DiscoveryStats>(['discovery-stats']);
      if (prevStats && row) {
        queryClient.setQueryData(['discovery-stats'], adjustDiscoveryStats(prevStats, row, selected));
      }

      return { snapshots, prevStats };
    },
    onError: (_err, _vars, ctx) => {
      if (!ctx) return;
      for (const [key, data] of ctx.snapshots) {
        queryClient.setQueryData(key, data);
      }
      if (ctx.prevStats) {
        queryClient.setQueryData(['discovery-stats'], ctx.prevStats);
      }
    },
    onSuccess: (serverRow, { code }) => {
      queryClient.setQueriesData<MinedCubeItem[]>({ queryKey: ['discovery-cubes'] }, (old) => {
        if (!old) return old;
        return old.map((r) => (r.account_code === code ? { ...r, ...serverRow } : r));
      });
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
    const modes: string[] = [];
    if (mineWatchlist) modes.push('watchlist');
    if (mineFollowing) modes.push('following');
    if (mineStockHot) modes.push('stock_hot');
    if (!modes.length) {
      showToast('请至少选择一种挖掘模式', 'error');
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setMining(true);
    setLogs([]);
    try {
      const result = await streamDiscoveryMine(appendLog, {
        max_depth: maxDepth,
        modes,
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
  }, [appendLog, maxDepth, mineFollowing, mineStockHot, mineWatchlist, queryClient, showToast]);

  const savePool = useCallback(async () => {
    if (!poolItems.length) {
      showToast('股票池不能为空', 'error');
      return;
    }
    setPoolSaving(true);
    try {
      await saveDiscoverySymbolPool(poolItems);
      setPoolDraft(null);
      await queryClient.invalidateQueries({ queryKey: ['discovery-symbol-pool'] });
      showToast('股票池已保存', 'success');
    } catch (err) {
      const text = err instanceof Error ? err.message : '保存失败';
      showToast(text, 'error');
    } finally {
      setPoolSaving(false);
    }
  }, [poolItems, queryClient, showToast]);

  const addSymbolToPool = useCallback(() => {
    const sym = newSymbol.trim().toUpperCase();
    if (!sym) return;
    const base = poolDraft ?? poolQuery.data?.items ?? [];
    if (base.some((x) => x.symbol === sym)) {
      showToast('已在股票池中', 'info');
      return;
    }
    setPoolDraft([
      ...base,
      {
        symbol: sym,
        stock_name: null,
        note: null,
        enabled: true,
        sort_order: base.length,
        is_builtin: false,
        volume_rank_date: null,
      },
    ]);
    setNewSymbol('');
  }, [newSymbol, poolDraft, poolQuery.data?.items, showToast]);

  const stopMine = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const stopImport = useCallback(() => {
    importAbortRef.current?.abort();
  }, []);

  const closeCubePreview = useCallback(() => {
    previewPopupRef.current?.close();
    previewPopupRef.current = null;
    setPreviewCube(null);
  }, []);

  const openCubePreview = useCallback((row: MinedCubeItem) => {
    previewPopupRef.current = openXueqiuPreviewPopup(row.account_code, previewPopupRef.current);
    setPreviewCube(row);
  }, []);

  const handlePreviewDecide = useCallback(
    (selected: number) => {
      const code = previewCube?.account_code;
      if (!code) return;

      let nextRow: MinedCubeItem | null = null;
      if (readAutoNextEnabled()) {
        const idx = items.findIndex((r) => r.account_code === code);
        if (idx >= 0) {
          nextRow = items[idx + 1] ?? items[idx - 1] ?? null;
          if (nextRow?.account_code === code) nextRow = null;
        }
      }

      patchMut.mutate({ code, selected });

      if (nextRow) {
        previewPopupRef.current = openXueqiuPreviewPopup(nextRow.account_code, previewPopupRef.current);
        setPreviewCube(nextRow);
      } else {
        previewPopupRef.current?.close();
        previewPopupRef.current = null;
        setPreviewCube(null);
      }
    },
    [items, patchMut, previewCube],
  );

  useEffect(
    () => () => {
      previewPopupRef.current?.close();
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

  const columns = TABLE_COLUMNS;

  const rows = useMemo(() => {
    const patchingCode =
      patchMut.isPending && patchMut.variables ? patchMut.variables.code : null;

    return items.map((row: MinedCubeItem) => {
      const tags = row.reject_reasons.map((r) => REASON_LABELS[r] ?? r);
      const sourceLabel = formatDiscoverySource(row);
      const rowPatching = patchingCode === row.account_code;

      const actionCell = (
        <Stack direction="row" spacing={0.5} justifyContent="flex-end" flexWrap="wrap">
          <Button
            size="small"
            variant={row.selected === 1 ? 'contained' : 'outlined'}
            disabled={rowPatching}
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
            disabled={rowPatching}
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
            variant={previewCube?.account_code === row.account_code ? 'contained' : 'outlined'}
            onClick={(e) => {
              e.stopPropagation();
              openCubePreview(row);
            }}
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
  }, [items, importing, openCubePreview, patchMut, previewCube?.account_code, runImport]);

  return (
    <Box sx={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <PageHeader
        title="挖组合"
        icon={<TravelExploreRoundedIcon />}
        meta={
          <Typography component="span" sx={{ fontSize: 12, color: DASHBOARD_THEME.textSecondary }}>
            自选链 / 关注链 BFS / 股票池个股活跃用户 · 近6月有调仓且月均≤3次
          </Typography>
        }
        actions={
          <Stack direction="row" spacing={1} alignItems="center">
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
          </Stack>
        }
      />

      <PageContent>
        <Stack spacing={2} sx={{ pb: previewCube ? 10 : 0 }}>
          <MetricGrid minColWidth={100}>
            <StatChip compact label="已爬取" value={String(stats?.total_count ?? '—')} />
            <StatChip compact label="初筛通过" value={String(stats?.auto_pass_count ?? '—')} color={DASHBOARD_THEME.down} />
            <StatChip compact label="待决定" value={String(stats?.pending_count ?? '—')} />
            <StatChip compact label="已选中" value={String(stats?.selected_count ?? '—')} />
            <StatChip compact label="已拒绝" value={String(stats?.rejected_count ?? '—')} />
            <StatChip compact label="已入库" value={String(stats?.imported_count ?? '—')} />
          </MetricGrid>

          <SectionCard title="挖掘配置">
            <Stack direction="row" flexWrap="wrap" gap={2} alignItems="center">
              <FormControlLabel
                control={<Checkbox checked={mineWatchlist} onChange={(e) => setMineWatchlist(e.target.checked)} />}
                label="自选链"
              />
              <FormControlLabel
                control={<Checkbox checked={mineFollowing} onChange={(e) => setMineFollowing(e.target.checked)} />}
                label="关注链"
              />
              <FormControlLabel
                control={<Checkbox checked={mineStockHot} onChange={(e) => setMineStockHot(e.target.checked)} />}
                label="个股活跃用户（按股票池成交额序取前 12 只）"
              />
              <FormControl size="small" sx={{ minWidth: 120 }}>
                <InputLabel>挖掘深度</InputLabel>
                <Select label="挖掘深度" value={maxDepth} onChange={(e) => setMaxDepth(Number(e.target.value))}>
                  <MenuItem value={1}>1 层</MenuItem>
                  <MenuItem value={2}>2 层</MenuItem>
                  <MenuItem value={3}>3 层</MenuItem>
                  <MenuItem value={4}>4 层</MenuItem>
                  <MenuItem value={5}>5 层</MenuItem>
                </Select>
              </FormControl>
              <Button
                variant="contained"
                size="small"
                disabled={mining}
                startIcon={mining ? <CircularProgress size={14} color="inherit" /> : <PlayArrowRoundedIcon />}
                onClick={() => void startMine()}
              >
                {mining ? '挖掘中…' : '开始挖掘'}
              </Button>
            </Stack>
          </SectionCard>

          <SectionCard
            title="股票池"
            action={
              <Stack direction="row" spacing={1} alignItems="center">
                <TextField
                  size="small"
                  placeholder="SZ300308"
                  value={newSymbol}
                  onChange={(e) => setNewSymbol(e.target.value)}
                  sx={{ width: 120 }}
                />
                <Button size="small" variant="outlined" onClick={addSymbolToPool}>
                  添加
                </Button>
                <Button
                  size="small"
                  variant="contained"
                  disabled={poolSaving || !poolDraft}
                  onClick={() => void savePool()}
                >
                  {poolSaving ? '保存中…' : '保存'}
                </Button>
              </Stack>
            }
          >
            <Typography sx={{ fontSize: 12, color: DASHBOARD_THEME.textMuted, mb: 1 }}>
              {poolMeta
                ? `基准日 ${poolMeta.volume_rank_date ?? '—'} · 共 ${poolMeta.total_count} 只 · 启用 ${poolMeta.enabled_count} 只`
                : '加载中…'}
            </Typography>
            <Stack direction="row" flexWrap="wrap" gap={0.5} useFlexGap sx={{ maxHeight: 120, overflow: 'auto' }}>
              {poolItems.map((item) => (
                <Chip
                  key={item.symbol}
                  size="small"
                  label={
                    item.stock_name
                      ? `${item.stock_name} · ${item.symbol}`
                      : item.symbol
                  }
                  variant={item.enabled ? 'filled' : 'outlined'}
                  onDelete={() => {
                    const base = poolDraft ?? poolQuery.data?.items ?? [];
                    setPoolDraft(base.filter((x) => x.symbol !== item.symbol));
                  }}
                  onClick={() => {
                    const base = poolDraft ?? poolQuery.data?.items ?? [];
                    setPoolDraft(
                      base.map((x) => (x.symbol === item.symbol ? { ...x, enabled: !x.enabled } : x)),
                    );
                  }}
                />
              ))}
            </Stack>
            <Typography sx={{ fontSize: 11, color: DASHBOARD_THEME.textMuted, mt: 1 }}>
              点击切换启用/禁用 · 删除后需点保存
            </Typography>
          </SectionCard>

          <SectionCard title="挖掘日志">
            <LogSection title="" logs={logs} running={mining} emptyHint="配置模式后点击「开始挖掘」" />
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
            {cubesQuery.isLoading && !cubesQuery.data ? (
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

      <DiscoverCubeTriageBar
        cube={previewCube}
        patching={patchMut.isPending && patchMut.variables?.code === previewCube?.account_code}
        onDecide={handlePreviewDecide}
        onClose={closeCubePreview}
        onReopen={() => {
          if (previewCube) openCubePreview(previewCube);
        }}
      />
    </Box>
  );
}
