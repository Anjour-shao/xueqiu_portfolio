import { Box } from '@mui/material';
import { EquityChart } from './EquityChart';
import { PnlCalendarView } from './PnlCalendarView';
import type { EquityPoint } from '../../types';
import type { ChartViewMode } from './ChartViewToolbar';
import type { CalendarGranularity } from './PnlCalendarView';

type Props = {
  chartView: ChartViewMode;
  points: EquityPoint[];
  calendarGranularity: CalendarGranularity;
  calendarYear: number;
  calendarMonth: number;
  onCalendarGranularityChange: (g: CalendarGranularity) => void;
  onYearMonthChange: (year: number, month: number) => void;
  showBrushControls?: boolean;
};

export function PortfolioChartPanel({
  chartView,
  points,
  calendarGranularity,
  calendarYear,
  calendarMonth,
  onCalendarGranularityChange,
  onYearMonthChange,
  showBrushControls = true,
}: Props) {
  return (
    <Box sx={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden', height: '100%' }}>
      {chartView === 'line' ? (
        <EquityChart points={points} height="100%" showBrushControls={showBrushControls} />
      ) : (
        <PnlCalendarView
          points={points}
          granularity={calendarGranularity}
          year={calendarYear}
          month={calendarMonth}
          onYearMonthChange={onYearMonthChange}
          onGranularityChange={onCalendarGranularityChange}
        />
      )}
    </Box>
  );
}
