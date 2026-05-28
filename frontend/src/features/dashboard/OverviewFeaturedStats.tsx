import { Box, Typography } from '@mui/material';
import { useMemo } from 'react';
import { MetricGrid } from '../../components/MetricGrid';
import { StatChip } from './StatChip';
import type { OverviewWatchItem, PortfolioOverviewItem } from '../../types';
import { DASHBOARD_THEME, fmtPct, pctColor } from './utils';

type Summary = {
  portfolio_count: number;
  avg_cum_return_pct?: number | null;
  beat_benchmark_count: number;
  traded_today_count: number;
  freshness: {
    cube_nav: { stale_accounts?: number | null };
  };
};

function median(nums: number[]) {
  if (!nums.length) return null;
  const sorted = [...nums].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

function countWatchReason(watchlist: OverviewWatchItem[], reason: string) {
  return watchlist.filter((w) => w.reasons.some((r) => r.includes(reason))).length;
}

function bestBy<T>(items: T[], pick: (item: T) => number | null | undefined): T | null {
  let best: T | null = null;
  let bestVal = -Infinity;
  for (const item of items) {
    const v = pick(item);
    if (v == null || Number.isNaN(v)) continue;
    if (v > bestVal) {
      bestVal = v;
      best = item;
    }
  }
  return best;
}

function worstBy<T>(items: T[], pick: (item: T) => number | null | undefined): T | null {
  let worst: T | null = null;
  let worstVal = Infinity;
  for (const item of items) {
    const v = pick(item);
    if (v == null || Number.isNaN(v)) continue;
    if (v < worstVal) {
      worstVal = v;
      worst = item;
    }
  }
  return worst;
}

export function OverviewFeaturedStats({
  summary,
  items,
  watchlist,
}: {
  summary: Summary;
  items: PortfolioOverviewItem[];
  watchlist: OverviewWatchItem[];
}) {
  const derived = useMemo(() => {
    const cumValues = items.map((i) => i.cum_return_pct).filter((v): v is number => v != null);
    const medCum = median(cumValues);
    const excessChamp = bestBy(items, (i) => i.excess_return_pct);
    const cumLaggard = worstBy(items, (i) => i.cum_return_pct);
    const staleNav = summary.freshness.cube_nav.stale_accounts ?? 0;
    const navLagCount = countWatchReason(watchlist, '净值落后');
    const inactiveCount = countWatchReason(watchlist, '7日未调仓');

    return { medCum, excessChamp, cumLaggard, staleNav, navLagCount, inactiveCount };
  }, [items, watchlist, summary.freshness.cube_nav.stale_accounts]);

  const beatRatio =
    summary.portfolio_count > 0
      ? `${summary.beat_benchmark_count}/${summary.portfolio_count}`
      : '—';

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <Box>
        <Typography sx={{ fontSize: 12, fontWeight: 600, color: DASHBOARD_THEME.textMuted, mb: 1, letterSpacing: '0.04em' }}>
          表现摘要
        </Typography>
        <MetricGrid minColWidth={100}>
          <StatChip
            compact
            label="平均累计"
            value={summary.avg_cum_return_pct != null ? fmtPct(summary.avg_cum_return_pct) : '—'}
            color={pctColor(summary.avg_cum_return_pct ?? 0)}
          />
          <StatChip
            compact
            label="中位累计"
            value={derived.medCum != null ? fmtPct(derived.medCum) : '—'}
            color={pctColor(derived.medCum ?? 0)}
          />
          <StatChip
            compact
            label="跑赢基准"
            value={beatRatio}
            color={summary.beat_benchmark_count > 0 ? DASHBOARD_THEME.down : undefined}
          />
          <StatChip
            compact
            label="超额冠军"
            value={derived.excessChamp?.excess_return_pct != null ? fmtPct(derived.excessChamp.excess_return_pct) : '—'}
            color={pctColor(derived.excessChamp?.excess_return_pct ?? 0)}
            sub={derived.excessChamp ? `${derived.excessChamp.account_name}` : undefined}
          />
          <StatChip
            compact
            label="累计垫底"
            value={derived.cumLaggard?.cum_return_pct != null ? fmtPct(derived.cumLaggard.cum_return_pct) : '—'}
            color={pctColor(derived.cumLaggard?.cum_return_pct ?? 0)}
            sub={derived.cumLaggard ? `${derived.cumLaggard.account_name}` : undefined}
          />
        </MetricGrid>
      </Box>

      <Box>
        <Typography sx={{ fontSize: 12, fontWeight: 600, color: DASHBOARD_THEME.textMuted, mb: 1, letterSpacing: '0.04em' }}>
          异常 / 待办
        </Typography>
        <MetricGrid minColWidth={100}>
          <StatChip compact label="待关注" value={String(watchlist.length)} color={watchlist.length > 0 ? '#B45309' : undefined} />
          <StatChip compact label="净值落后" value={String(derived.navLagCount)} color={derived.navLagCount > 0 ? '#B45309' : undefined} />
          <StatChip compact label="7日未调仓" value={String(derived.inactiveCount)} color={derived.inactiveCount > 0 ? '#B45309' : undefined} />
          <StatChip compact label="今日调仓" value={String(summary.traded_today_count)} />
          <StatChip
            compact
            label="待同步净值"
            value={String(derived.staleNav)}
            color={derived.staleNav > 0 ? '#B45309' : DASHBOARD_THEME.down}
            sub="≥3 天未更新"
          />
        </MetricGrid>
      </Box>
    </Box>
  );
}
