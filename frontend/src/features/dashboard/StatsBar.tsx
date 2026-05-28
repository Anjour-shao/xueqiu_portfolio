import { Box } from '@mui/material';
import { OverviewMetrics } from '../../types';
import { MetricGrid } from '../../components/MetricGrid';
import { METRIC_TOOLTIPS } from './metricTooltips';
import { StatChip } from './StatChip';
import { DASHBOARD_THEME, fmtPct, pctColor } from './utils';

const INDEX_NAME = '上证指数';

/** 双行网格 KPI，框内居中，无横向滚动 */
export function StatsBar({ overview, liveCount }: { overview: OverviewMetrics; liveCount: number }) {
  const hasRisk =
    overview.max_drawdown_pct != null ||
    overview.volatility_pct != null ||
    overview.sharpe_ratio != null;

  const primaryChips = [
    <StatChip key="cum" compact label="累计收益" value={fmtPct(overview.cum_return_pct)} color={pctColor(overview.cum_return_pct)} />,
    overview.benchmark_return_pct != null ? (
      <StatChip
        key="bench"
        compact
        label={INDEX_NAME}
        value={fmtPct(overview.benchmark_return_pct)}
        color={pctColor(overview.benchmark_return_pct)}
      />
    ) : null,
    overview.excess_return_pct != null ? (
      <StatChip key="excess" compact label="超额" value={fmtPct(overview.excess_return_pct)} color={pctColor(overview.excess_return_pct)} />
    ) : null,
    <StatChip key="realized" compact label="已实现" value={fmtPct(overview.realized_return_pct)} color={pctColor(overview.realized_return_pct)} />,
    <StatChip key="float" compact label="浮动" value={fmtPct(overview.unrealized_return_pct)} color={pctColor(overview.unrealized_return_pct)} />,
    <StatChip key="win" compact label="平仓胜率" value={`${overview.win_rate.toFixed(1)}%`} />,
    <StatChip key="reb" compact label="调仓批次" value={`${overview.rebalance_event_count ?? 0}`} />,
    <StatChip key="bs" compact label="买入/卖出" value={`${overview.buy_count ?? 0}/${overview.sell_count ?? 0}`} />,
    <StatChip key="hold" compact label="持仓" value={`${liveCount} 只`} />,
  ].filter(Boolean);

  const riskChips = hasRisk
    ? [
        overview.max_drawdown_pct != null ? (
          <StatChip
            key="mdd"
            compact
            label="最大回撤"
            value={fmtPct(overview.max_drawdown_pct)}
            color={pctColor(overview.max_drawdown_pct)}
            tooltip={METRIC_TOOLTIPS['最大回撤']}
          />
        ) : null,
        overview.volatility_pct != null ? (
          <StatChip
            key="vol"
            compact
            label="年化波动"
            value={`${overview.volatility_pct.toFixed(1)}%`}
            tooltip={METRIC_TOOLTIPS['年化波动']}
          />
        ) : null,
        overview.sharpe_ratio != null ? (
          <StatChip
            key="sharpe"
            compact
            label="夏普比率"
            value={overview.sharpe_ratio.toFixed(2)}
            tooltip={METRIC_TOOLTIPS['夏普比率']}
          />
        ) : null,
        overview.calmar_ratio != null ? (
          <StatChip
            key="calmar"
            compact
            label="卡玛比率"
            value={overview.calmar_ratio.toFixed(2)}
            tooltip={METRIC_TOOLTIPS['卡玛比率']}
          />
        ) : null,
        overview.positive_day_ratio != null ? (
          <StatChip
            key="up"
            compact
            label="上涨日占比"
            value={`${overview.positive_day_ratio.toFixed(1)}%`}
            tooltip={METRIC_TOOLTIPS['上涨日占比']}
          />
        ) : null,
      ].filter(Boolean)
    : [];

  return (
    <Box component="section" aria-label="核心指标" sx={{ flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 1.5 }}>
      <MetricGrid minColWidth={88}>{primaryChips}</MetricGrid>
      {riskChips.length > 0 && (
        <Box sx={{ pt: 1.5, borderTop: `1px solid ${DASHBOARD_THEME.borderSubtle}` }}>
          <MetricGrid minColWidth={88}>{riskChips}</MetricGrid>
        </Box>
      )}
    </Box>
  );
}
