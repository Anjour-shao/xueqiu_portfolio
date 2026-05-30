import { Box, Stack, Typography } from '@mui/material';
import { useMemo } from 'react';
import type { EquityPoint } from '../../types';
import {
  buildMonthGrid,
  formatYearMonth,
  hasCalendarSeries,
  summarizeBenchmarkPeriod,
  summarizeBenchmarkYear,
  summarizeMonth,
  summarizeYear,
  summarizeYearMonths,
  summarizeYears,
  weekdayLabels,
} from './calendarUtils';
import { DASHBOARD_THEME, fmtPct, monoSx, pctColor } from './utils';

const INDEX_NAME = '上证指数';

export type CalendarGranularity = 'day' | 'month' | 'year';

type Props = {
  points: EquityPoint[];
  granularity: CalendarGranularity;
  year: number;
  month: number;
  onYearMonthChange: (year: number, month: number) => void;
  onGranularityChange: (g: CalendarGranularity) => void;
};

function cellBackground(pct: number | null, inMonth: boolean) {
  if (!inMonth || pct == null) return 'transparent';
  if (pct > 0) return DASHBOARD_THEME.upTint;
  if (pct < 0) return DASHBOARD_THEME.downTint;
  return 'rgba(148, 163, 184, 0.06)';
}

function formatDailyPct(pct: number) {
  const abs = Math.abs(pct);
  const text = abs >= 10 ? abs.toFixed(1) : abs.toFixed(2);
  return `${pct > 0 ? '+' : '-'}${text}%`;
}

export function PnlCalendarView({ points, granularity, year, month, onYearMonthChange, onGranularityChange }: Props) {
  const canShowCalendar = useMemo(() => hasCalendarSeries(points), [points]);
  const monthCells = useMemo(() => buildMonthGrid(year, month, points), [year, month, points]);
  const monthSummary = useMemo(() => summarizeMonth(points, year, month), [points, year, month]);
  const yearSummary = useMemo(() => summarizeYear(points, year), [points, year]);
  const yearMonths = useMemo(() => summarizeYearMonths(points, year), [points, year]);
  const allYears = useMemo(() => summarizeYears(points), [points]);

  const benchPeriodPct = useMemo(() => {
    if (granularity === 'day') return summarizeBenchmarkPeriod(points, year, month);
    if (granularity === 'month') return summarizeBenchmarkYear(points, year);
    return null;
  }, [points, year, month, granularity]);

  const periodSummary = granularity === 'day' ? monthSummary : granularity === 'month' ? yearSummary : null;

  if (!canShowCalendar) {
    return (
      <Box sx={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', px: 2 }}>
        <Typography sx={{ fontSize: 13, color: DASHBOARD_THEME.textMuted, textAlign: 'center' }}>
          暂无足够日频净值数据。关注组合请同步官方净值；回测需有连续模拟净值曲线。
        </Typography>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        flex: 1,
        minHeight: 0,
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'space-between',
        overflow: 'hidden',
      }}
    >
      {granularity === 'day' ? (
        <>
          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: 'repeat(7, 1fr)',
              gap: 0.5,
              mb: 0.5,
              flexShrink: 0,
            }}
          >
            {weekdayLabels().map((label) => (
              <Typography key={label} sx={{ fontSize: 11, color: DASHBOARD_THEME.textMuted, textAlign: 'center', fontWeight: 600 }}>
                {label}
              </Typography>
            ))}
          </Box>
          <Box
            sx={{
              flex: '0 1 auto',
              maxHeight: 'calc(100% - 56px)',
              display: 'grid',
              gridTemplateColumns: 'repeat(7, 1fr)',
              gridTemplateRows: 'repeat(6, minmax(40px, auto))',
              gap: 0.5,
              alignContent: 'start',
            }}
          >
            {monthCells.map((cell) => {
              const pct = cell.periodReturnPct;
              const showValue = cell.inMonth && pct != null && !cell.isWeekend;
              const bg = cellBackground(pct, cell.inMonth);
              const fg = pct != null ? pctColor(pct) : DASHBOARD_THEME.textMuted;
              return (
                <Box
                  key={cell.dateKey}
                  sx={{
                    borderRadius: 1,
                    bgcolor: bg,
                    border: cell.isToday ? `1px solid ${DASHBOARD_THEME.primary}` : '1px solid transparent',
                    p: 0.35,
                    minHeight: 44,
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    justifyContent: 'center',
                    opacity: cell.inMonth ? 1 : 0.35,
                  }}
                >
                  <Typography sx={{ fontSize: 10, color: DASHBOARD_THEME.textMuted, alignSelf: 'flex-end', lineHeight: 1 }}>
                    {cell.isToday ? '今' : cell.date.getDate()}
                  </Typography>
                  {showValue && (
                    <Typography sx={{ fontSize: 11, fontWeight: 700, color: fg, ...monoSx, lineHeight: 1.2, mt: 0.25 }}>
                      {formatDailyPct(pct!)}
                    </Typography>
                  )}
                </Box>
              );
            })}
          </Box>
        </>
      ) : granularity === 'month' ? (
        <Box
          sx={{
            flex: '0 1 auto',
            display: 'grid',
            gridTemplateColumns: 'repeat(4, 1fr)',
            gridTemplateRows: 'repeat(3, minmax(72px, auto))',
            gap: 1,
            alignContent: 'start',
          }}
        >
          {yearMonths.map(({ month: m, summary }) => {
            const pct = summary.periodReturnPct;
            const bg = pct == null ? 'transparent' : pct > 0 ? DASHBOARD_THEME.upTint : pct < 0 ? DASHBOARD_THEME.downTint : 'rgba(148,163,184,0.06)';
            return (
              <Box
                key={m}
                role="button"
                tabIndex={0}
                onClick={() => {
                  onGranularityChange('day');
                  onYearMonthChange(year, m);
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    onGranularityChange('day');
                    onYearMonthChange(year, m);
                  }
                }}
                sx={{
                  borderRadius: 1.5,
                  bgcolor: bg,
                  border: DASHBOARD_THEME.cardBorder,
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  cursor: 'pointer',
                  transition: 'transform 0.12s',
                  '&:hover': { transform: 'scale(1.02)', boxShadow: DASHBOARD_THEME.cardShadow },
                }}
              >
                <Typography sx={{ fontSize: 12, color: DASHBOARD_THEME.textSecondary, mb: 0.5 }}>{m + 1}月</Typography>
                <Typography sx={{ fontSize: 15, fontWeight: 700, color: pctColor(pct ?? 0), ...monoSx }}>
                  {pct != null ? fmtPct(pct) : '—'}
                </Typography>
                <Typography sx={{ fontSize: 10, color: DASHBOARD_THEME.textMuted, mt: 0.25 }}>
                  {summary.tradingDays > 0 ? `${summary.tradingDays} 交易日` : '无数据'}
                </Typography>
              </Box>
            );
          })}
        </Box>
      ) : (
        <Box
          sx={{
            flex: '0 1 auto',
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(100px, 1fr))',
            gap: 1,
            alignContent: 'start',
            overflowY: 'auto',
            maxHeight: 'calc(100% - 56px)',
          }}
        >
          {allYears.map(({ year: y, summary }) => {
            const pct = summary.periodReturnPct;
            const bg = pct == null ? 'transparent' : pct > 0 ? DASHBOARD_THEME.upTint : pct < 0 ? DASHBOARD_THEME.downTint : 'rgba(148,163,184,0.06)';
            return (
              <Box
                key={y}
                role="button"
                tabIndex={0}
                onClick={() => {
                  onGranularityChange('month');
                  onYearMonthChange(y, 0);
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    onGranularityChange('month');
                    onYearMonthChange(y, 0);
                  }
                }}
                sx={{
                  borderRadius: 1.5,
                  bgcolor: bg,
                  border: DASHBOARD_THEME.cardBorder,
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  minHeight: 72,
                  cursor: 'pointer',
                  transition: 'transform 0.12s',
                  '&:hover': { transform: 'scale(1.02)', boxShadow: DASHBOARD_THEME.cardShadow },
                }}
              >
                <Typography sx={{ fontSize: 12, color: DASHBOARD_THEME.textSecondary, mb: 0.5 }}>{y}年</Typography>
                <Typography sx={{ fontSize: 15, fontWeight: 700, color: pctColor(pct ?? 0), ...monoSx }}>
                  {pct != null ? fmtPct(pct) : '—'}
                </Typography>
                <Typography sx={{ fontSize: 10, color: DASHBOARD_THEME.textMuted, mt: 0.25 }}>
                  {summary.tradingDays > 0 ? `${summary.tradingDays} 交易日` : '无数据'}
                </Typography>
              </Box>
            );
          })}
        </Box>
      )}

      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mt: 1, pt: 1, borderTop: `1px solid rgba(148, 163, 184, 0.2)`, flexShrink: 0 }}
        flexWrap="wrap"
        gap={1.5}
      >
        {granularity !== 'year' && periodSummary && (
          <Typography sx={{ fontSize: 12, color: DASHBOARD_THEME.textSecondary, ...monoSx }}>
            {granularity === 'month' ? `${year}年区间收益` : `${formatYearMonth(year, month)}区间收益`}：
            <Box component="span" sx={{ fontWeight: 700, color: pctColor(periodSummary.periodReturnPct ?? 0), ml: 0.5 }}>
              {periodSummary.periodReturnPct != null ? fmtPct(periodSummary.periodReturnPct) : '—'}
            </Box>
          </Typography>
        )}
        {granularity === 'year' && (
          <Typography sx={{ fontSize: 12, color: DASHBOARD_THEME.textSecondary, ...monoSx }}>
            点击年份可下钻到月度视图
          </Typography>
        )}
        {benchPeriodPct != null && (
          <Typography sx={{ fontSize: 12, color: DASHBOARD_THEME.textMuted, ...monoSx }}>
            {INDEX_NAME}：
            <Box component="span" sx={{ color: pctColor(benchPeriodPct), fontWeight: 600, ml: 0.5 }}>
              {fmtPct(benchPeriodPct)}
            </Box>
          </Typography>
        )}
      </Stack>
    </Box>
  );
}
