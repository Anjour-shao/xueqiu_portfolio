import DeleteOutlineRoundedIcon from '@mui/icons-material/DeleteOutlineRounded';
import { Box, Chip, CircularProgress, IconButton, Stack, Typography } from '@mui/material';
import axios from 'axios';
import { MouseEvent, useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { deleteAccount, fetchPortfoliosOverviewStats } from '../api/dashboard';
import { LoadingView } from '../components/LoadingView';
import { PageContent } from '../components/PageContent';
import { PageHeader } from '../components/PageHeader';
import { SectionCard } from '../components/SectionCard';
import { isApiNotFoundError, STALE_BACKEND_HINT } from '../features/dashboard/apiError';
import { DataTable, TableColumn, TableSort, toggleSort } from '../features/dashboard/DataTable';
import { OverviewFeaturedStats } from '../features/dashboard/OverviewFeaturedStats';
import { DASHBOARD_THEME, fmtPct } from '../features/dashboard/utils';
import { useToast } from '../features/notify/ToastProvider';
import { PortfolioOverviewItem } from '../types';

const OVERVIEW_COLS: TableColumn[] = [
  { key: 'account_name', label: '组合', sortable: true, width: '22%' },
  { key: 'account_code', label: '代码', sortable: true, width: '12%' },
  { key: 'cum_return_pct', label: '累计收益', sortable: true, width: '12%' },
  { key: 'excess_return_pct', label: '超额', sortable: true, width: '10%' },
  { key: 'holding_count', label: '持仓', sortable: true, width: '8%' },
  { key: 'latest_nav_date', label: '净值日', sortable: true, width: '14%' },
  { key: 'latest_trade_time', label: '最近调仓', sortable: true, width: '14%' },
];

function sortOverviewItems(items: PortfolioOverviewItem[], sort: TableSort) {
  const list = [...items];
  const desc = sort.desc ? -1 : 1;
  list.sort((a, b) => {
    switch (sort.key) {
      case 'account_name':
        return desc * a.account_name.localeCompare(b.account_name, 'zh-CN');
      case 'account_code':
        return desc * a.account_code.localeCompare(b.account_code);
      case 'cum_return_pct':
        return desc * ((a.cum_return_pct ?? -1e9) - (b.cum_return_pct ?? -1e9));
      case 'excess_return_pct':
        return desc * ((a.excess_return_pct ?? -1e9) - (b.excess_return_pct ?? -1e9));
      case 'holding_count':
        return desc * (a.holding_count - b.holding_count);
      case 'latest_nav_date':
        return desc * (a.latest_nav_date ?? '').localeCompare(b.latest_nav_date ?? '');
      case 'latest_trade_time':
        return desc * (a.latest_trade_time ?? '').localeCompare(b.latest_trade_time ?? '');
      default:
        return 0;
    }
  });
  return list;
}

function overviewRows(items: PortfolioOverviewItem[]) {
  return items.map((row) => [
    row.account_name,
    row.account_code,
    row.cum_return_pct != null ? fmtPct(row.cum_return_pct) : '—',
    row.excess_return_pct != null ? fmtPct(row.excess_return_pct) : '—',
    String(row.holding_count),
    row.latest_nav_date ?? '—',
    row.latest_trade_time ? row.latest_trade_time.slice(0, 10) : '—',
  ]);
}

export function OverviewPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [deletingCode, setDeletingCode] = useState<string | null>(null);
  const [sort, setSort] = useState<TableSort>({ key: 'cum_return_pct', desc: true });
  const { showToast } = useToast();
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  useEffect(() => {
    if (actionMessage?.text) {
      showToast(actionMessage.text, actionMessage.type);
      setActionMessage(null);
    }
  }, [actionMessage, showToast]);

  const handleDelete = async (code: string, name: string, e: MouseEvent) => {
    e.stopPropagation();
    if (deletingCode) return;
    if (!window.confirm(`确定删除组合「${name}」（${code}）？将同时删除库内全部调仓与净值数据。`)) return;
    setDeletingCode(code);
    setActionMessage(null);
    try {
      const result = await deleteAccount(code);
      setActionMessage({ type: 'success', text: result.message });
      await queryClient.invalidateQueries({ queryKey: ['portfolios-overview-stats'] });
      await queryClient.invalidateQueries({ queryKey: ['accounts'] });
      await queryClient.invalidateQueries({ queryKey: ['portfolios-overview'] });
      await queryClient.invalidateQueries({ queryKey: ['dashboard'] });
    } catch (err) {
      let text = '删除失败';
      if (axios.isAxiosError(err)) {
        const detail = err.response?.data?.detail;
        text = typeof detail === 'string' ? detail : err.message;
      } else if (err instanceof Error) {
        text = err.message;
      }
      setActionMessage({ type: 'error', text });
    } finally {
      setDeletingCode(null);
    }
  };

  const statsQuery = useQuery({
    queryKey: ['portfolios-overview-stats'],
    queryFn: fetchPortfoliosOverviewStats,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });

  const items = statsQuery.data?.items ?? [];
  const sortedItems = useMemo(() => sortOverviewItems(items, sort), [items, sort]);
  const tableRows = useMemo(() => overviewRows(sortedItems), [sortedItems]);

  if (statsQuery.isLoading) {
    return <LoadingView label="正在加载组合总览..." />;
  }

  if (statsQuery.isError) {
    return (
      <Box sx={{ p: 3 }}>
        <Typography sx={{ color: DASHBOARD_THEME.down, fontSize: 14 }}>
          {isApiNotFoundError(statsQuery.error) ? STALE_BACKEND_HINT : '加载失败'}
        </Typography>
      </Box>
    );
  }

  const data = statsQuery.data!;
  const summary = data.summary;
  const navMax = summary.freshness.cube_nav.latest_date_max;

  return (
    <Box sx={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <PageHeader
        title="组合总览"
        meta={
          <Typography component="span" sx={{ fontSize: 12, color: DASHBOARD_THEME.textSecondary, fontWeight: 400 }}>
            {summary.portfolio_count} 个组合 · 净值截至 {navMax ?? '—'}
          </Typography>
        }
      />

      <PageContent>
        <Stack spacing={3}>
          <SectionCard title="特色统计">
            <OverviewFeaturedStats summary={summary} items={items} watchlist={data.watchlist} />
          </SectionCard>

          <SectionCard
            title="待关注"
            action={
              (summary.freshness.cube_nav.stale_accounts ?? 0) > 0 ? (
                <Chip
                  size="small"
                  label="去同步"
                  clickable
                  onClick={() => navigate('/sync')}
                  sx={{ height: 24, fontSize: 11 }}
                />
              ) : undefined
            }
          >
            {!data.watchlist.length && (
              <Typography sx={{ fontSize: 13, color: DASHBOARD_THEME.textMuted, textAlign: 'center', py: 1 }}>
                暂无异常组合
              </Typography>
            )}
            <Stack spacing={1.25}>
              {data.watchlist.map((w) => (
                <Box
                  key={w.account_code}
                  sx={{
                    cursor: 'pointer',
                    py: 0.75,
                    px: 1,
                    borderRadius: 1,
                    textAlign: 'center',
                    '&:hover': { bgcolor: DASHBOARD_THEME.rowHover },
                  }}
                  onClick={() => navigate(`/portfolio/${encodeURIComponent(w.account_code)}`)}
                >
                  <Typography sx={{ fontSize: 13, fontWeight: 600 }}>{w.account_name}</Typography>
                  <Typography sx={{ fontSize: 11, color: DASHBOARD_THEME.textMuted, fontFamily: DASHBOARD_THEME.monoFont }}>
                    {w.account_code}
                  </Typography>
                  <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap justifyContent="center" sx={{ mt: 0.5 }}>
                    {w.reasons.map((r) => (
                      <Chip key={r} label={r} size="small" sx={{ height: 20, fontSize: 10 }} />
                    ))}
                  </Stack>
                </Box>
              ))}
            </Stack>
          </SectionCard>

          {items.length === 0 ? (
            <SectionCard>
              <Typography sx={{ fontSize: 14, color: DASHBOARD_THEME.textSecondary, textAlign: 'center', py: 4 }}>
                暂无 ZH 组合
              </Typography>
            </SectionCard>
          ) : (
            <SectionCard title="全部组合" noPadding>
              <DataTable
                compact
                columns={OVERVIEW_COLS}
                rows={tableRows}
                sort={sort}
                onSort={(key) => setSort((s) => toggleSort(s, key, key !== 'account_name' && key !== 'account_code'))}
                onRowClick={(rowIndex) =>
                  navigate(`/portfolio/${encodeURIComponent(sortedItems[rowIndex].account_code)}`)
                }
                showRowHoverActions
                rowActions={(rowIndex) => {
                  const row = sortedItems[rowIndex];
                  return (
                    <IconButton
                      size="small"
                      color="error"
                      disabled={deletingCode === row.account_code}
                      onClick={(e) => handleDelete(row.account_code, row.account_name, e)}
                      aria-label="删除组合"
                    >
                      {deletingCode === row.account_code ? (
                        <CircularProgress size={16} color="inherit" />
                      ) : (
                        <DeleteOutlineRoundedIcon fontSize="small" />
                      )}
                    </IconButton>
                  );
                }}
              />
            </SectionCard>
          )}

          {statsQuery.isFetching && !statsQuery.isLoading && (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 1 }}>
              <CircularProgress size={20} sx={{ color: DASHBOARD_THEME.primary }} />
            </Box>
          )}
        </Stack>
      </PageContent>
    </Box>
  );
}
