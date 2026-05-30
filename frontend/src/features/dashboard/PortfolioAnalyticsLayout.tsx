import { Box, Stack } from '@mui/material';
import { ReactNode } from 'react';
import { OverviewMetrics } from '../../types';
import { CalendarGranularity } from './PnlCalendarView';
import { ChartViewToolbar, ChartViewMode } from './ChartViewToolbar';
import { StatsBar } from './StatsBar';
import { surfaceCardSx } from './utils';

type Props = {
  overview: OverviewMetrics;
  liveCount: number;
  statsExtra?: ReactNode;
  chartView: ChartViewMode;
  onChartViewChange: (v: ChartViewMode) => void;
  showCalendarToggle?: boolean;
  calendarGranularity?: CalendarGranularity;
  onCalendarGranularityChange?: (g: CalendarGranularity) => void;
  calendarYear?: number;
  calendarMonth?: number;
  onCalendarPrev?: () => void;
  onCalendarNext?: () => void;
  chart: ReactNode;
  sidePanel: ReactNode;
};

export function PortfolioAnalyticsLayout({
  overview,
  liveCount,
  statsExtra,
  chartView,
  onChartViewChange,
  showCalendarToggle = true,
  calendarGranularity,
  onCalendarGranularityChange,
  calendarYear,
  calendarMonth,
  onCalendarPrev,
  onCalendarNext,
  chart,
  sidePanel,
}: Props) {
  return (
    <Stack sx={{ flex: 1, minHeight: 0, overflow: 'hidden', gap: 2 }}>
      <Box sx={{ flexShrink: 0 }}>
        <StatsBar overview={overview} liveCount={liveCount} />
        {statsExtra ? <Box sx={{ mt: 1.5 }}>{statsExtra}</Box> : null}
      </Box>

      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', lg: 'minmax(0, 11fr) minmax(0, 9fr)' },
          gap: 2,
          overflow: 'hidden',
        }}
      >
        <Box
          component="section"
          sx={{
            ...surfaceCardSx,
            p: 2,
            minHeight: 0,
            height: { lg: '100%' },
            display: 'flex',
            flexDirection: 'column',
            minWidth: 0,
            overflow: 'hidden',
          }}
        >
          <Box sx={{ flexShrink: 0 }}>
            <ChartViewToolbar
              chartView={chartView}
              onChartViewChange={onChartViewChange}
              showCalendarToggle={showCalendarToggle}
              calendarGranularity={calendarGranularity}
              onCalendarGranularityChange={onCalendarGranularityChange}
              calendarYear={calendarYear}
              calendarMonth={calendarMonth}
              onCalendarPrev={onCalendarPrev}
              onCalendarNext={onCalendarNext}
            />
          </Box>
          <Box sx={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'visible' }}>{chart}</Box>
        </Box>

        <Box sx={{ minHeight: { xs: 320, lg: 0 }, height: { lg: '100%' }, minWidth: 0, display: 'flex', overflow: 'hidden' }}>
          {sidePanel}
        </Box>
      </Box>
    </Stack>
  );
}
