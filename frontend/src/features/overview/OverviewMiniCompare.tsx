import { Box, CircularProgress, Link, Typography } from '@mui/material';
import { useQueries } from '@tanstack/react-query';
import { useMemo } from 'react';
import { Link as RouterLink } from 'react-router-dom';
import { fetchDashboard } from '../../api/dashboard';
import type { PortfolioOverviewItem } from '../../types';
import { CompareChart } from '../compare/CompareChart';
import type { CompareSeries } from '../compare/compareSeries';
import { DASHBOARD_THEME, surfaceCardSx } from '../dashboard/utils';

type Props = {
  performers: PortfolioOverviewItem[];
};

export function OverviewMiniCompare({ performers }: Props) {
  const codes = useMemo(() => performers.slice(0, 3).map((p) => p.account_code), [performers]);

  const dashboardQueries = useQueries({
    queries: codes.map((code) => ({
      queryKey: ['dashboard', code, 'overview-mini-compare'],
      queryFn: () => fetchDashboard(code),
      staleTime: 60_000,
      enabled: codes.length >= 2,
    })),
  });

  const loading = dashboardQueries.some((q) => q.isLoading);

  const seriesList = useMemo((): CompareSeries[] => {
    return dashboardQueries
      .map((q, i) => {
        const perf = performers[i];
        if (!perf || !q.data?.equity_curve?.length) return null;
        return {
          accountCode: perf.account_code,
          accountName: perf.account_name,
          points: q.data.equity_curve,
        };
      })
      .filter((s): s is CompareSeries => s != null);
  }, [dashboardQueries, performers]);

  if (codes.length < 2) {
    return (
      <Box sx={{ ...surfaceCardSx, p: 2, height: 320, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Typography sx={{ fontSize: 13, color: DASHBOARD_THEME.textMuted }}>组合不足 2 个，暂无法对比</Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ ...surfaceCardSx, p: 1.5, height: 360, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <Box sx={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', mb: 0.5, flexShrink: 0 }}>
        <Typography sx={{ fontSize: 13, fontWeight: 600, color: DASHBOARD_THEME.textPrimary }}>Top 3 共同起点对比</Typography>
        <Link component={RouterLink} to="/compare" underline="hover" sx={{ fontSize: 12, color: DASHBOARD_THEME.primary }}>
          查看完整对比
        </Link>
      </Box>
      <Box sx={{ flex: 1, minHeight: 0 }}>
        {loading ? (
          <Box sx={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <CircularProgress size={24} sx={{ color: DASHBOARD_THEME.primary }} />
          </Box>
        ) : (
          <CompareChart seriesList={seriesList} />
        )}
      </Box>
    </Box>
  );
}
