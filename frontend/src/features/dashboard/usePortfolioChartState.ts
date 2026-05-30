import { useState } from 'react';
import { shiftMonth } from './calendarUtils';
import type { CalendarGranularity } from './PnlCalendarView';
import type { ChartViewMode } from './ChartViewToolbar';

export function usePortfolioChartState() {
  const now = new Date();
  const [chartView, setChartView] = useState<ChartViewMode>('line');
  const [calendarGranularity, setCalendarGranularity] = useState<CalendarGranularity>('day');
  const [calendarYear, setCalendarYear] = useState(now.getFullYear());
  const [calendarMonth, setCalendarMonth] = useState(now.getMonth());

  const handleCalendarPrev = () => {
    if (calendarGranularity === 'year') return;
    if (calendarGranularity === 'month') {
      setCalendarYear((y) => y - 1);
    } else {
      const next = shiftMonth(calendarYear, calendarMonth, -1);
      setCalendarYear(next.year);
      setCalendarMonth(next.month);
    }
  };

  const handleCalendarNext = () => {
    if (calendarGranularity === 'year') return;
    if (calendarGranularity === 'month') {
      setCalendarYear((y) => y + 1);
    } else {
      const next = shiftMonth(calendarYear, calendarMonth, 1);
      setCalendarYear(next.year);
      setCalendarMonth(next.month);
    }
  };

  return {
    chartView,
    setChartView,
    calendarGranularity,
    setCalendarGranularity,
    calendarYear,
    calendarMonth,
    setCalendarYear,
    setCalendarMonth,
    handleCalendarPrev,
    handleCalendarNext,
  };
}
