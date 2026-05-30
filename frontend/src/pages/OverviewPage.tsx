import CompareArrowsRoundedIcon from '@mui/icons-material/CompareArrowsRounded';
import SyncRoundedIcon from '@mui/icons-material/SyncRounded';
import { Box, Button, CircularProgress, Stack, Typography } from '@mui/material';
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
import { DASHBOARD_THEME } from '../features/dashboard/utils';
import { OverviewAlertStrip } from '../features/overview/OverviewAlertStrip';
import { OverviewLeaderboard } from '../features/overview/OverviewLeaderboard';
import { OverviewMiniCompare } from '../features/overview/OverviewMiniCompare';
import { OverviewPortfolioGrid } from '../features/overview/OverviewPortfolioGrid';
import { useToast } from '../features/notify/ToastProvider';

export function OverviewPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [deletingCode, setDeletingCode] = useState<string | null>(null);
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

  const topForCompare = useMemo(() => {
    const data = statsQuery.data;
    if (!data) return [];
    if (data.top_performers.length >= 2) return data.top_performers.slice(0, 3);
    return [...items]
      .sort((a, b) => (b.cum_return_pct ?? -1e9) - (a.cum_return_pct ?? -1e9))
      .slice(0, 3);
  }, [statsQuery.data, items]);

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
  const staleAccounts = summary.freshness.cube_nav.stale_accounts ?? 0;

  const openPortfolio = (code: string) => navigate(`/portfolio/${encodeURIComponent(code)}`);

  return (
    <Box sx={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <PageHeader
        title="组合总览"
        meta={
          <Typography component="span" sx={{ fontSize: 12, color: DASHBOARD_THEME.textSecondary, fontWeight: 400 }}>
            {summary.portfolio_count} 个组合 · 净值截至 {navMax ?? '—'}
          </Typography>
        }
        actions={
          <Stack direction="row" spacing={1} flexShrink={0}>
            <Button
              size="small"
              variant="outlined"
              startIcon={<CompareArrowsRoundedIcon />}
              onClick={() => navigate('/compare')}
              sx={{ textTransform: 'none', fontSize: 12 }}
            >
              组合对比
            </Button>
            <Button
              size="small"
              variant={staleAccounts > 0 ? 'contained' : 'outlined'}
              startIcon={<SyncRoundedIcon />}
              onClick={() => navigate('/sync')}
              sx={{
                textTransform: 'none',
                fontSize: 12,
                ...(staleAccounts > 0
                  ? { bgcolor: '#B45309', '&:hover': { bgcolor: '#92400E' } }
                  : {}),
              }}
            >
              数据同步{staleAccounts > 0 ? ` (${staleAccounts})` : ''}
            </Button>
          </Stack>
        }
      />

      <PageContent>
        <Stack spacing={2.5}>
          <OverviewAlertStrip
            staleAccounts={staleAccounts}
            tradedTodayCount={summary.traded_today_count}
            watchlist={data.watchlist}
            items={items}
          />

          {items.length >= 2 && (
            <Box
              sx={{
                display: 'grid',
                gridTemplateColumns: { xs: '1fr', lg: '1.4fr 1fr' },
                gap: 2,
                alignItems: 'stretch',
              }}
            >
              <OverviewMiniCompare performers={topForCompare} />
              <OverviewLeaderboard
                topPerformers={data.top_performers}
                bottomPerformers={data.bottom_performers}
                onOpen={openPortfolio}
              />
            </Box>
          )}

          {items.length === 0 ? (
            <SectionCard>
              <Stack spacing={2} alignItems="center" sx={{ py: 4 }}>
                <Typography sx={{ fontSize: 14, color: DASHBOARD_THEME.textSecondary }}>暂无 ZH 组合</Typography>
                <Button variant="contained" onClick={() => navigate('/sync')} sx={{ textTransform: 'none' }}>
                  去数据同步
                </Button>
              </Stack>
            </SectionCard>
          ) : (
            <OverviewPortfolioGrid
              items={items}
              watchlist={data.watchlist}
              deletingCode={deletingCode}
              onOpen={openPortfolio}
              onDelete={handleDelete}
            />
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
