import CropFreeRoundedIcon from '@mui/icons-material/CropFreeRounded';
import ClearRoundedIcon from '@mui/icons-material/ClearRounded';
import { Box, Chip, IconButton, Stack, Tooltip, Typography } from '@mui/material';
import type { EChartsOption } from 'echarts';
import ReactECharts from 'echarts-for-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { EquityPoint } from '../../types';
import {
  buildCompareChartData,
  type CompareMode,
  type CompareSeries,
} from './compareSeries';
import { DASHBOARD_THEME, fmtPct, pctColor } from '../dashboard/utils';

export type { CompareSeries } from './compareSeries';

const INDEX_NAME = '上证';
const SERIES_COLORS = ['#E74C3C', '#3498DB', '#2ECC71', '#9B59B6', '#F39C12', '#1ABC9C', '#E67E22', '#34495E'];

type RangeStat = {
  startDate: string;
  endDate: string;
  tradingDays: number;
  returns: Array<{ name: string; returnPct: number | null; benchPct: number | null }>;
};

function buildDateIndex(points: EquityPoint[]): Map<string, number> {
  const map = new Map<string, number>();
  points.forEach((p, i) => {
    map.set(p.trade_date, i);
  });
  return map;
}

function calcSeriesRangeReturn(
  points: EquityPoint[],
  dateIndex: Map<string, number>,
  startDate: string,
  endDate: string,
): { navReturnPct: number | null; benchReturnPct: number | null } {
  const startIdx = dateIndex.get(startDate);
  const endIdx = dateIndex.get(endDate);
  if (startIdx == null || endIdx == null) {
    return { navReturnPct: null, benchReturnPct: null };
  }
  const from = Math.min(startIdx, endIdx);
  const to = Math.max(startIdx, endIdx);
  const startCum = points[from].cum_return_pct;
  const endCum = points[to].cum_return_pct;
  if (startCum == null || endCum == null) {
    return { navReturnPct: null, benchReturnPct: null };
  }
  const navReturnPct = ((1 + endCum / 100) / (1 + startCum / 100) - 1) * 100;

  const b0 = points[from].benchmark_return_pct;
  const b1 = points[to].benchmark_return_pct;
  let benchReturnPct: number | null = null;
  if (b0 != null && b1 != null) {
    benchReturnPct = ((1 + b1 / 100) / (1 + b0 / 100) - 1) * 100;
  }
  return { navReturnPct, benchReturnPct };
}

function hexToRgba(hex: string, alpha: number) {
  const normalized = hex.replace('#', '');
  const r = parseInt(normalized.slice(0, 2), 16);
  const g = parseInt(normalized.slice(2, 4), 16);
  const b = parseInt(normalized.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

const MODE_OPTIONS: { key: CompareMode; label: string }[] = [
  { key: 'rebased', label: '共同起点' },
  { key: 'absolute', label: '绝对累计' },
];

export function CompareChart({ seriesList }: { seriesList: CompareSeries[] }) {
  const chartRef = useRef<ReactECharts>(null);
  const [mode, setMode] = useState<CompareMode>('rebased');
  const [isBrushActive, setIsBrushActive] = useState(false);
  const [rangeStats, setRangeStats] = useState<RangeStat | null>(null);

  const chartData = useMemo(() => buildCompareChartData(seriesList, mode), [seriesList, mode]);
  const { dates, overlapStart, portfolioSeries, benchData } = chartData;

  const dateIndexes = useMemo(
    () => seriesList.map((s) => buildDateIndex(s.points)),
    [seriesList],
  );

  const hasBench = benchData != null;

  const zoomStart = useMemo(() => {
    const windowSize = 120;
    return dates.length > windowSize ? Math.max(0, ((dates.length - windowSize) / dates.length) * 100) : 0;
  }, [dates]);

  useEffect(() => {
    setRangeStats(null);
    const chart = chartRef.current?.getEchartsInstance();
    if (chart) {
      chart.dispatchAction({ type: 'brush', command: 'clear', areas: [] });
    }
  }, [mode, seriesList]);

  const option = useMemo((): EChartsOption => {
    const primary = DASHBOARD_THEME.primary;
    const lineSeries = portfolioSeries.map((s, i) => ({
      name: s.name,
      type: 'line' as const,
      data: s.data,
      smooth: true,
      symbol: 'none',
      showSymbol: false,
      connectNulls: false,
      z: 2 + i,
      lineStyle: { width: 2, color: SERIES_COLORS[i % SERIES_COLORS.length] },
      itemStyle: { color: SERIES_COLORS[i % SERIES_COLORS.length] },
    }));

    return {
      backgroundColor: 'transparent',
      animation: false,
      legend: {
        type: 'scroll',
        top: 0,
        left: 0,
        right: 0,
        textStyle: { fontSize: 11, color: DASHBOARD_THEME.textSecondary },
      },
      grid: { left: 4, right: 20, top: 36, bottom: 44, containLabel: true },
      tooltip: {
        trigger: 'axis',
        axisPointer: {
          type: 'cross',
          show: !isBrushActive,
          crossStyle: { color: 'rgba(91, 123, 151, 0.35)' },
        },
        valueFormatter: (v) => (v == null || v === '' ? '—' : fmtPct(Number(v))),
      },
      brush: {
        toolbox: [],
        xAxisIndex: 0,
        brushType: 'lineX',
        brushMode: 'single',
        transformable: true,
        brushStyle: {
          color: hexToRgba(primary, 0.15),
          borderColor: primary,
          borderWidth: 2,
        },
        outOfBrush: { colorAlpha: 0.35 },
        throttleType: 'debounce',
        throttleDelay: 200,
      },
      dataZoom: [
        {
          type: 'inside',
          xAxisIndex: 0,
          filterMode: 'filter',
          zoomOnMouseWheel: true,
          moveOnMouseMove: true,
          moveOnMouseWheel: true,
        },
        {
          type: 'slider',
          xAxisIndex: 0,
          bottom: 4,
          height: 16,
          start: zoomStart,
          end: 100,
          filterMode: 'filter',
          brushSelect: true,
          showDetail: false,
          borderColor: 'transparent',
          backgroundColor: 'rgba(255, 255, 255, 0.45)',
          fillerColor: hexToRgba(primary, 0.12),
          handleStyle: { color: primary, borderColor: primary },
          textStyle: { color: DASHBOARD_THEME.textMuted, fontSize: 10 },
        },
      ],
      xAxis: {
        type: 'category',
        data: dates,
        boundaryGap: false,
        axisLine: { lineStyle: { color: 'rgba(0,0,0,0.06)' } },
        axisTick: { show: false },
        axisLabel: {
          color: DASHBOARD_THEME.textMuted,
          fontSize: 10,
          hideOverlap: true,
        },
      },
      yAxis: {
        type: 'value',
        scale: true,
        axisLabel: {
          color: DASHBOARD_THEME.textMuted,
          fontSize: 10,
          fontFamily: DASHBOARD_THEME.monoFont,
          formatter: (v: number) => `${v}%`,
        },
        splitLine: { lineStyle: { color: 'rgba(0,0,0,0.05)', type: 'dashed' } },
      },
      series: [
        ...(hasBench && benchData
          ? [
              {
                name: INDEX_NAME,
                type: 'line' as const,
                data: benchData,
                smooth: true,
                symbol: 'none',
                showSymbol: false,
                connectNulls: false,
                z: 1,
                lineStyle: { width: 1.5, type: 'dashed' as const, color: '#94A3B8' },
                itemStyle: { color: '#94A3B8' },
              },
            ]
          : []),
        ...lineSeries,
      ],
    };
  }, [portfolioSeries, benchData, dates, hasBench, zoomStart, isBrushActive]);

  const getChart = useCallback(() => chartRef.current?.getEchartsInstance(), []);

  const startBrush = useCallback(() => {
    const chart = getChart();
    if (!chart) return;
    setIsBrushActive(true);
    chart.dispatchAction({
      type: 'takeGlobalCursor',
      key: 'brush',
      brushOption: { brushType: 'lineX', brushMode: 'single' },
    });
  }, [getChart]);

  const clearBrush = useCallback(() => {
    const chart = getChart();
    if (chart) {
      chart.dispatchAction({ type: 'brush', command: 'clear', areas: [] });
      chart.dispatchAction({ type: 'takeGlobalCursor', key: 'brush', brushOption: { brushType: false } });
    }
    setIsBrushActive(false);
    setRangeStats(null);
  }, [getChart]);

  const onBrushEnd = useCallback(
    (params: { areas?: Array<{ coordRange?: number[]; range?: number[] }> }) => {
      const area = params.areas?.[0];
      const range = area?.coordRange ?? area?.range;
      if (!range || range.length < 2 || !dates.length) {
        setRangeStats(null);
        setIsBrushActive(false);
        return;
      }
      const startIdx = Math.max(0, Math.floor(Math.min(range[0], range[1])));
      const endIdx = Math.min(dates.length - 1, Math.ceil(Math.max(range[0], range[1])));
      if (startIdx === endIdx) {
        setRangeStats(null);
        setIsBrushActive(false);
        return;
      }
      const startDate = dates[startIdx];
      const endDate = dates[endIdx];
      const returns = seriesList.map((s, i) => {
        const { navReturnPct, benchReturnPct } = calcSeriesRangeReturn(
          s.points,
          dateIndexes[i],
          startDate,
          endDate,
        );
        return {
          name: s.accountName,
          returnPct: navReturnPct,
          benchPct: benchReturnPct,
        };
      });
      setRangeStats({
        startDate,
        endDate,
        tradingDays: endIdx - startIdx + 1,
        returns,
      });
      setIsBrushActive(false);
      getChart()?.dispatchAction({ type: 'takeGlobalCursor', key: 'brush', brushOption: { brushType: false } });
    },
    [dates, seriesList, dateIndexes, getChart],
  );

  const onEvents = useMemo(() => ({ brushEnd: onBrushEnd }), [onBrushEnd]);

  const hintText =
    mode === 'rebased'
      ? overlapStart
        ? `共同起点 ${overlapStart} · 从对齐日开始的累计收益 · 框选区间可查看各组合区间收益`
        : '从共同起点对齐后的累计收益 · 框选区间可查看各组合区间收益'
      : '各组合自成立以来的绝对累计收益 · 框选区间可查看各组合区间收益';

  if (!seriesList.length || !dates.length) {
    return (
      <Box sx={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Typography sx={{ fontSize: 13, color: DASHBOARD_THEME.textMuted }}>请选择至少 2 个有净值数据的组合</Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <Stack
        direction="row"
        spacing={1}
        alignItems="center"
        justifyContent="space-between"
        flexWrap="wrap"
        sx={{ mb: 0.5, minHeight: 34, gap: 1 }}
      >
        <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap sx={{ flexShrink: 0 }}>
          {MODE_OPTIONS.map((opt) => (
            <Chip
              key={opt.key}
              label={opt.label}
              size="small"
              clickable
              onClick={() => setMode(opt.key)}
              sx={{
                height: 26,
                fontSize: 11,
                fontWeight: mode === opt.key ? 600 : 400,
                bgcolor: mode === opt.key ? DASHBOARD_THEME.navActive : 'transparent',
                border: mode === opt.key ? `1px solid ${DASHBOARD_THEME.primary}` : DASHBOARD_THEME.cardBorder,
                color: mode === opt.key ? DASHBOARD_THEME.primary : DASHBOARD_THEME.textSecondary,
              }}
            />
          ))}
        </Stack>
        <Stack direction="row" spacing={0.25} flexShrink={0}>
          <Tooltip title="框选区间">
            <IconButton size="small" onClick={startBrush} aria-label="框选区间" sx={{ color: DASHBOARD_THEME.primary }}>
              <CropFreeRoundedIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title="清除框选">
            <IconButton size="small" onClick={clearBrush} aria-label="清除框选" sx={{ color: DASHBOARD_THEME.textSecondary }}>
              <ClearRoundedIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Stack>
      </Stack>

      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" sx={{ mb: 0.5, minHeight: 20, gap: 0.5 }}>
        {rangeStats ? (
          <>
            <Typography sx={{ fontSize: 11, color: DASHBOARD_THEME.textSecondary, fontWeight: 600 }}>
              {rangeStats.startDate}→{rangeStats.endDate}
            </Typography>
            <Typography sx={{ fontSize: 11, color: DASHBOARD_THEME.textMuted }}>{rangeStats.tradingDays}日</Typography>
            {rangeStats.returns.map((r) => (
              <Typography
                key={r.name}
                sx={{
                  fontSize: 11,
                  fontWeight: 600,
                  color: r.returnPct != null ? pctColor(r.returnPct) : DASHBOARD_THEME.textMuted,
                  fontFamily: DASHBOARD_THEME.monoFont,
                }}
              >
                {r.name} {r.returnPct != null ? fmtPct(r.returnPct) : '—'}
              </Typography>
            ))}
          </>
        ) : (
          <Typography sx={{ fontSize: 11, color: DASHBOARD_THEME.textMuted }}>{hintText}</Typography>
        )}
      </Stack>

      <Box sx={{ flex: 1, minHeight: 0 }}>
        <ReactECharts
          ref={chartRef}
          option={option}
          style={{ height: '100%', width: '100%', minHeight: 360 }}
          notMerge
          lazyUpdate
          onEvents={onEvents}
        />
      </Box>
    </Box>
  );
}
