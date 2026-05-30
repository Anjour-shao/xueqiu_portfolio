import { Box, Chip, Stack } from '@mui/material';
import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import type { OverviewWatchItem, PortfolioOverviewItem } from '../../types';
import { DASHBOARD_THEME } from '../dashboard/utils';

type Props = {
  staleAccounts: number;
  tradedTodayCount: number;
  watchlist: OverviewWatchItem[];
  items: PortfolioOverviewItem[];
};

function todayTradedItems(items: PortfolioOverviewItem[]): PortfolioOverviewItem[] {
  const today = new Date().toISOString().slice(0, 10);
  return items.filter((i) => i.latest_trade_time?.slice(0, 10) === today);
}

export function OverviewAlertStrip({ staleAccounts, tradedTodayCount, watchlist, items }: Props) {
  const navigate = useNavigate();

  const tradedToday = useMemo(() => todayTradedItems(items), [items]);

  const alerts: Array<{ key: string; label: string; onClick: () => void; warn?: boolean }> = [];

  if (staleAccounts > 0) {
    alerts.push({
      key: 'stale',
      label: `待同步净值 ${staleAccounts} 个`,
      onClick: () => navigate('/sync'),
      warn: true,
    });
  }

  if (tradedTodayCount > 0 && tradedToday.length > 0) {
    const first = tradedToday[0];
    alerts.push({
      key: 'today',
      label: `今日调仓 ${tradedTodayCount} 个`,
      onClick: () => navigate(`/portfolio/${encodeURIComponent(first.account_code)}`),
      warn: false,
    });
  }

  for (const w of watchlist.slice(0, 4)) {
    alerts.push({
      key: w.account_code,
      label: `${w.account_name} · ${w.reasons.join('、')}`,
      onClick: () => navigate(`/portfolio/${encodeURIComponent(w.account_code)}`),
      warn: true,
    });
  }

  if (!alerts.length) return null;

  return (
    <Box
      sx={{
        px: 1.25,
        py: 1,
        borderRadius: `${DASHBOARD_THEME.radiusMd}px`,
        bgcolor: 'rgba(180, 83, 9, 0.06)',
        border: '1px solid rgba(180, 83, 9, 0.15)',
      }}
    >
      <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
        {alerts.map((a) => (
          <Chip
            key={a.key}
            label={a.label}
            size="small"
            clickable
            onClick={a.onClick}
            sx={{
              height: 28,
              fontSize: 11,
              fontWeight: 500,
              bgcolor: a.warn ? 'rgba(180, 83, 9, 0.1)' : DASHBOARD_THEME.surface,
              color: a.warn ? '#B45309' : DASHBOARD_THEME.textPrimary,
              border: a.warn ? 'none' : DASHBOARD_THEME.cardBorder,
              maxWidth: 280,
              '& .MuiChip-label': { overflow: 'hidden', textOverflow: 'ellipsis' },
            }}
          />
        ))}
      </Stack>
    </Box>
  );
}
