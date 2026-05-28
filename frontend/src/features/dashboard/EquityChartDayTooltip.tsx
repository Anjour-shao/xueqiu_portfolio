import { Box, Typography } from '@mui/material';
import type { ReactNode } from 'react';
import { EquityHoldingItem, EquityTradeTodayItem } from '../../types';
import {
  actionLabel,
  DASHBOARD_THEME,
  fmtPct,
  fmtWeightDelta,
  monoSx,
  pctColor,
} from './utils';

export const EQUITY_DAY_TOOLTIP_WIDTH = 272;
export const EQUITY_DAY_TOOLTIP_MAX_HEIGHT = 480;

const TRADE_GRID = 'minmax(0, 1fr) 40px 72px';
const HOLDING_GRID = 'minmax(0, 1fr) 56px';

type Props = {
  date: string;
  dayPct: number;
  close: number;
  cumReturn: number | null;
  benchDailyPct: number | null;
  benchCumPct: number | null;
  trades: EquityTradeTodayItem[];
  holdings: EquityHoldingItem[];
  left: number;
  top: number;
  onPointerEnter: () => void;
  onPointerLeave: () => void;
};

function actionPillSx(action: string) {
  const label = actionLabel(action);
  if (label === '买入' || label === '加仓') {
    return { bgcolor: DASHBOARD_THEME.upTint, color: DASHBOARD_THEME.up };
  }
  if (label === '卖出' || label === '减仓') {
    return { bgcolor: DASHBOARD_THEME.downTint, color: DASHBOARD_THEME.down };
  }
  return { bgcolor: DASHBOARD_THEME.insetBg, color: DASHBOARD_THEME.textSecondary };
}

function InlineMetric({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <Box component="span" sx={{ display: 'inline-flex', alignItems: 'baseline', gap: 0.375, whiteSpace: 'nowrap' }}>
      <Typography component="span" sx={{ fontSize: 10, color: DASHBOARD_THEME.textMuted }}>
        {label}
      </Typography>
      <Typography
        component="span"
        sx={{
          ...monoSx,
          fontSize: 11,
          fontWeight: 600,
          color: color ?? DASHBOARD_THEME.textPrimary,
        }}
      >
        {value}
      </Typography>
    </Box>
  );
}

function SectionLabel({ children, first }: { children: ReactNode; first?: boolean }) {
  return (
    <Typography
      sx={{
        fontSize: 10,
        fontWeight: 600,
        color: DASHBOARD_THEME.textMuted,
        letterSpacing: '0.04em',
        pt: first ? 0.5 : 1,
        pb: 0.5,
        flexShrink: 0,
      }}
    >
      {children}
    </Typography>
  );
}

function TradeRow({ trade }: { trade: EquityTradeTodayItem }) {
  const pill = actionPillSx(trade.action);
  return (
    <Box
      sx={{
        display: 'grid',
        gridTemplateColumns: TRADE_GRID,
        gap: 0.5,
        alignItems: 'center',
        py: 0.35,
        minWidth: 0,
      }}
    >
      <Typography
        noWrap
        title={trade.stock_name}
        sx={{ fontSize: 11, color: DASHBOARD_THEME.textPrimary, minWidth: 0 }}
      >
        {trade.stock_name}
      </Typography>
      <Typography
        sx={{
          fontSize: 10,
          fontWeight: 600,
          textAlign: 'center',
          borderRadius: 1,
          px: 0.25,
          py: 0.125,
          lineHeight: 1.2,
          ...pill,
        }}
      >
        {actionLabel(trade.action)}
      </Typography>
      <Typography sx={{ ...monoSx, fontSize: 11, fontWeight: 600, textAlign: 'right' }}>
        {fmtWeightDelta(trade.from_weight, trade.to_weight)}
      </Typography>
    </Box>
  );
}

function HoldingRow({ holding }: { holding: EquityHoldingItem }) {
  return (
    <Box
      sx={{
        display: 'grid',
        gridTemplateColumns: HOLDING_GRID,
        gap: 0.5,
        alignItems: 'center',
        py: 0.35,
        minWidth: 0,
      }}
    >
      <Typography
        noWrap
        title={holding.stock_name}
        sx={{ fontSize: 11, color: DASHBOARD_THEME.textPrimary, minWidth: 0 }}
      >
        {holding.stock_name}
      </Typography>
      <Typography sx={{ ...monoSx, fontSize: 11, fontWeight: 600, textAlign: 'right' }}>
        {holding.weight.toFixed(1)}%
      </Typography>
    </Box>
  );
}

export function EquityChartDayTooltip({
  date,
  dayPct,
  close,
  cumReturn,
  benchDailyPct,
  benchCumPct,
  trades,
  holdings,
  left,
  top,
  onPointerEnter,
  onPointerLeave,
}: Props) {
  return (
    <Box
      onPointerEnter={onPointerEnter}
      onPointerLeave={onPointerLeave}
      sx={{
        position: 'absolute',
        left,
        top,
        width: EQUITY_DAY_TOOLTIP_WIDTH,
        maxHeight: `min(72vh, ${EQUITY_DAY_TOOLTIP_MAX_HEIGHT}px)`,
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        pointerEvents: 'auto',
        zIndex: 10,
        bgcolor: 'rgba(255, 255, 255, 0.98)',
        border: DASHBOARD_THEME.cardBorder,
        borderRadius: `${DASHBOARD_THEME.radiusMd}px`,
        boxShadow: DASHBOARD_THEME.shadowMd,
      }}
    >
      <Box
        sx={{
          flexShrink: 0,
          px: 1.25,
          pt: 0.75,
          pb: 0.5,
          borderBottom: `1px solid ${DASHBOARD_THEME.borderSubtle}`,
        }}
      >
        <Box
          sx={{
            display: 'flex',
            alignItems: 'baseline',
            justifyContent: 'space-between',
            gap: 1,
            mb: 0.5,
          }}
        >
          <Typography sx={{ fontSize: 13, fontWeight: 600, color: DASHBOARD_THEME.textPrimary }}>{date}</Typography>
          <Typography sx={{ ...monoSx, fontSize: 12, fontWeight: 600, color: pctColor(dayPct), flexShrink: 0 }}>
            日 {fmtPct(dayPct)}
          </Typography>
        </Box>
        <Box
          sx={{
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            columnGap: 1,
            rowGap: 0.25,
          }}
        >
          <InlineMetric label="收盘" value={close.toFixed(2)} />
          <InlineMetric
            label="累计"
            value={cumReturn != null ? fmtPct(cumReturn) : '-'}
            color={cumReturn != null ? pctColor(cumReturn) : undefined}
          />
          {benchDailyPct != null && (
            <InlineMetric label="上证指数" value={fmtPct(benchDailyPct)} color={pctColor(benchDailyPct)} />
          )}
          {benchCumPct != null && (
            <InlineMetric label="指数累计" value={fmtPct(benchCumPct)} color={pctColor(benchCumPct)} />
          )}
        </Box>
      </Box>

      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          maxHeight: 'min(48vh, 320px)',
          overflowY: 'auto',
          overflowX: 'hidden',
          px: 1.25,
          pb: 1,
          WebkitOverflowScrolling: 'touch',
        }}
      >
        <SectionLabel first>调仓 {trades.length}</SectionLabel>
        {!trades.length ? (
          <Typography sx={{ fontSize: 11, color: DASHBOARD_THEME.textMuted, pb: 0.5 }}>无</Typography>
        ) : (
          trades.map((t, i) => <TradeRow key={`${t.stock_name}-${t.action}-${i}`} trade={t} />)
        )}

        <SectionLabel>持仓 {holdings.length}</SectionLabel>
        {!holdings.length ? (
          <Typography sx={{ fontSize: 11, color: DASHBOARD_THEME.textMuted }}>无</Typography>
        ) : (
          holdings.map((h) => <HoldingRow key={h.stock_name} holding={h} />)
        )}
      </Box>
    </Box>
  );
}
