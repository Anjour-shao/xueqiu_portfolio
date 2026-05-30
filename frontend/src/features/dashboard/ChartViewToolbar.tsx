import ChevronLeftRoundedIcon from '@mui/icons-material/ChevronLeftRounded';
import ChevronRightRoundedIcon from '@mui/icons-material/ChevronRightRounded';
import { Box, IconButton, Stack, Typography } from '@mui/material';
import { DASHBOARD_THEME } from './utils';
import { formatYearMonth } from './calendarUtils';
import type { CalendarGranularity } from './PnlCalendarView';

export type ChartViewMode = 'line' | 'calendar';

function SegmentedControl<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <Box
      sx={{
        display: 'inline-flex',
        p: 0.25,
        borderRadius: `${DASHBOARD_THEME.radiusPill}px`,
        bgcolor: DASHBOARD_THEME.insetBg,
        border: `1px solid ${DASHBOARD_THEME.borderSubtle}`,
      }}
    >
      {options.map((opt) => {
        const active = value === opt.value;
        return (
          <Box
            key={opt.value}
            component="button"
            type="button"
            onClick={() => onChange(opt.value)}
            sx={{
              border: 'none',
              cursor: 'pointer',
              px: 1.5,
              py: 0.625,
              fontSize: 13,
              fontWeight: active ? 600 : 500,
              fontFamily: 'inherit',
              color: active ? DASHBOARD_THEME.textPrimary : DASHBOARD_THEME.textSecondary,
              bgcolor: active ? DASHBOARD_THEME.surface : 'transparent',
              borderRadius: `${DASHBOARD_THEME.radiusPill}px`,
              boxShadow: active ? DASHBOARD_THEME.shadowSm : 'none',
              transition: 'all 0.15s ease',
              whiteSpace: 'nowrap',
            }}
          >
            {opt.label}
          </Box>
        );
      })}
    </Box>
  );
}

type Props = {
  chartView: ChartViewMode;
  onChartViewChange: (v: ChartViewMode) => void;
  showCalendarToggle?: boolean;
  calendarGranularity?: CalendarGranularity;
  onCalendarGranularityChange?: (g: CalendarGranularity) => void;
  calendarYear?: number;
  calendarMonth?: number;
  onCalendarPrev?: () => void;
  onCalendarNext?: () => void;
};

export function ChartViewToolbar({
  chartView,
  onChartViewChange,
  showCalendarToggle = true,
  calendarGranularity = 'day',
  onCalendarGranularityChange,
  calendarYear,
  calendarMonth,
  onCalendarPrev,
  onCalendarNext,
}: Props) {
  const chartOptions: { value: ChartViewMode; label: string }[] = [{ value: 'line', label: '净值走势' }];
  if (showCalendarToggle) chartOptions.push({ value: 'calendar', label: '盈亏日历' });

  const periodLabel =
    calendarYear != null && calendarMonth != null
      ? calendarGranularity === 'year'
        ? '历年收益'
        : calendarGranularity === 'month'
          ? `${calendarYear}年`
          : formatYearMonth(calendarYear, calendarMonth)
      : '';

  const showNav = calendarGranularity !== 'year';

  return (
    <Stack direction="row" alignItems="center" justifyContent="space-between" flexWrap="wrap" gap={1.5} sx={{ mb: 1.5, flexShrink: 0 }}>
      <SegmentedControl value={chartView} options={chartOptions} onChange={onChartViewChange} />

      {chartView === 'calendar' && showCalendarToggle && calendarYear != null && calendarMonth != null && (
        <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap">
          {showNav && (
            <Stack direction="row" alignItems="center" spacing={0.25}>
              <IconButton size="small" onClick={onCalendarPrev} aria-label="上一期">
                <ChevronLeftRoundedIcon fontSize="small" />
              </IconButton>
              <Typography sx={{ fontSize: 13, fontWeight: 600, color: DASHBOARD_THEME.textPrimary, minWidth: 88, textAlign: 'center' }}>
                {periodLabel}
              </Typography>
              <IconButton size="small" onClick={onCalendarNext} aria-label="下一期">
                <ChevronRightRoundedIcon fontSize="small" />
              </IconButton>
            </Stack>
          )}
          {!showNav && (
            <Typography sx={{ fontSize: 13, fontWeight: 600, color: DASHBOARD_THEME.textPrimary, minWidth: 88, textAlign: 'center' }}>
              {periodLabel}
            </Typography>
          )}
          <SegmentedControl
            value={calendarGranularity}
            options={[
              { value: 'day', label: '日' },
              { value: 'month', label: '月' },
              { value: 'year', label: '年' },
            ]}
            onChange={(g) => onCalendarGranularityChange?.(g)}
          />
        </Stack>
      )}
    </Stack>
  );
}
