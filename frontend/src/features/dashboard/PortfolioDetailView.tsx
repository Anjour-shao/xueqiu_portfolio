import { useMemo } from 'react';
import type { DashboardPayload } from '../../types';
import { DetailDataPanel } from './DetailDataPanel';
import { PortfolioAnalyticsLayout } from './PortfolioAnalyticsLayout';
import { PortfolioChartPanel } from './PortfolioChartPanel';
import { usePortfolioChartState } from './usePortfolioChartState';

type Props = {
  dashboard: DashboardPayload;
  showBrushControls?: boolean;
};

export function PortfolioDetailView({ dashboard, showBrushControls = false }: Props) {
  const chart = usePortfolioChartState();

  const livePositions = useMemo(
    () => (dashboard.positions ?? []).filter((item) => item.current_weight > 0),
    [dashboard.positions],
  );
  const allTrades = useMemo(() => dashboard.recent_trades ?? [], [dashboard.recent_trades]);

  return (
    <PortfolioAnalyticsLayout
      overview={dashboard.overview}
      liveCount={livePositions.length}
      chartView={chart.chartView}
      onChartViewChange={chart.setChartView}
      calendarGranularity={chart.calendarGranularity}
      onCalendarGranularityChange={chart.setCalendarGranularity}
      calendarYear={chart.calendarYear}
      calendarMonth={chart.calendarMonth}
      onCalendarPrev={chart.handleCalendarPrev}
      onCalendarNext={chart.handleCalendarNext}
      chart={
        <PortfolioChartPanel
          chartView={chart.chartView}
          points={dashboard.equity_curve}
          calendarGranularity={chart.calendarGranularity}
          calendarYear={chart.calendarYear}
          calendarMonth={chart.calendarMonth}
          onCalendarGranularityChange={chart.setCalendarGranularity}
          onYearMonthChange={(y, m) => {
            chart.setCalendarYear(y);
            chart.setCalendarMonth(m);
          }}
          showBrushControls={showBrushControls}
        />
      }
      sidePanel={<DetailDataPanel dashboard={dashboard} livePositions={livePositions} allTrades={allTrades} />}
    />
  );
}
