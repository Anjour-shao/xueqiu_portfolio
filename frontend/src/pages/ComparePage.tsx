import {
  Box,
  Checkbox,
  Chip,
  CircularProgress,
  FormControlLabel,
  Stack,
  Typography,
} from '@mui/material';
import { useQueries, useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { fetchAccounts, fetchDashboard } from '../api/dashboard';
import { LoadingView } from '../components/LoadingView';
import { PageContent } from '../components/PageContent';
import { PageHeader } from '../components/PageHeader';
import { SectionCard } from '../components/SectionCard';
import { CompareChart } from '../features/compare/CompareChart';
import type { CompareSeries } from '../features/compare/compareSeries';
import { DASHBOARD_THEME, surfaceCardSx } from '../features/dashboard/utils';

const MAX_SELECTED = 8;
const STORAGE_KEY = 'xueqiu-compare-selected';

function loadStoredSelection(): string[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as string[];
    return Array.isArray(parsed) ? parsed.slice(0, MAX_SELECTED) : [];
  } catch {
    return [];
  }
}

export function ComparePage() {
  const accountsQuery = useQuery({
    queryKey: ['accounts'],
    queryFn: fetchAccounts,
    staleTime: 60_000,
  });

  const accounts = accountsQuery.data ?? [];
  const accountCodes = useMemo(() => accounts.map((a) => a.id), [accounts]);

  const [selected, setSelected] = useState<string[]>(() => loadStoredSelection());

  useEffect(() => {
    if (!accounts.length || selected.length > 0) return;
    setSelected(accounts.slice(0, Math.min(3, MAX_SELECTED)).map((a) => a.id));
  }, [accounts, selected.length]);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(selected));
    } catch {
      /* ignore */
    }
  }, [selected]);

  const validSelected = useMemo(
    () => selected.filter((code) => accountCodes.includes(code)).slice(0, MAX_SELECTED),
    [selected, accountCodes],
  );

  const dashboardQueries = useQueries({
    queries: validSelected.map((code) => ({
      queryKey: ['dashboard', code, 'compare'],
      queryFn: () => fetchDashboard(code),
      staleTime: 60_000,
      enabled: validSelected.length >= 2,
    })),
  });

  const loadingDash = dashboardQueries.some((q) => q.isLoading);
  const seriesList = useMemo((): CompareSeries[] => {
    return dashboardQueries
      .map((q, i) => {
        const code = validSelected[i];
        const account = accounts.find((a) => a.id === code);
        if (!q.data?.equity_curve?.length) return null;
        return {
          accountCode: code,
          accountName: account?.name || code,
          points: q.data.equity_curve,
        };
      })
      .filter((s): s is CompareSeries => s != null);
  }, [dashboardQueries, validSelected, accounts]);

  const toggleCode = (code: string) => {
    setSelected((prev) => {
      if (prev.includes(code)) {
        return prev.filter((c) => c !== code);
      }
      if (prev.length >= MAX_SELECTED) return prev;
      return [...prev, code];
    });
  };

  if (accountsQuery.isLoading) {
    return <LoadingView label="加载组合列表…" />;
  }

  return (
    <>
      <PageHeader
        title="组合对比"
        meta={
          <Typography component="span" sx={{ fontSize: 12, color: DASHBOARD_THEME.textSecondary }}>
            支持共同起点与绝对累计两种对比方式
          </Typography>
        }
      />
      <PageContent>
        <Box
          sx={{
            display: 'flex',
            flexDirection: 'column',
            gap: 2,
            height: '100%',
            minHeight: 0,
          }}
        >
          <SectionCard
            title={`选择组合（最多 ${MAX_SELECTED} 个 · 已选 ${validSelected.length}）`}
            sx={{ flexShrink: 0, maxHeight: 200, overflow: 'auto' }}
          >
            <Stack direction="row" flexWrap="wrap" gap={0.75}>
              {accounts.map((acc) => {
                const checked = validSelected.includes(acc.id);
                const disabled = !checked && validSelected.length >= MAX_SELECTED;
                return (
                  <FormControlLabel
                    key={acc.id}
                    control={
                      <Checkbox
                        size="small"
                        checked={checked}
                        disabled={disabled}
                        onChange={() => toggleCode(acc.id)}
                      />
                    }
                    label={
                      <Stack direction="row" spacing={0.5} alignItems="center">
                        <Typography sx={{ fontSize: 13, fontWeight: checked ? 600 : 400 }}>{acc.name}</Typography>
                        <Chip label={acc.id} size="small" sx={{ height: 20, fontSize: 10 }} />
                      </Stack>
                    }
                    sx={{ mr: 1, ml: 0 }}
                  />
                );
              })}
              {!accounts.length && (
                <Typography sx={{ fontSize: 13, color: DASHBOARD_THEME.textMuted }}>暂无关注组合</Typography>
              )}
            </Stack>
          </SectionCard>

          <Box
            sx={{
              ...surfaceCardSx,
              flex: 1,
              minHeight: 360,
              p: 2,
              display: 'flex',
              flexDirection: 'column',
              overflow: 'hidden',
            }}
          >
            {validSelected.length < 2 ? (
              <Box sx={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Typography sx={{ fontSize: 13, color: DASHBOARD_THEME.textMuted }}>
                  请至少选择 2 个组合进行对比
                </Typography>
              </Box>
            ) : loadingDash ? (
              <Box sx={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <CircularProgress size={28} sx={{ color: DASHBOARD_THEME.primary }} />
              </Box>
            ) : (
              <CompareChart seriesList={seriesList} />
            )}
          </Box>
        </Box>
      </PageContent>
    </>
  );
}
