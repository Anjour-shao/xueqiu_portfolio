import CropFreeRoundedIcon from '@mui/icons-material/CropFreeRounded';
import ClearRoundedIcon from '@mui/icons-material/ClearRounded';
import FlagRoundedIcon from '@mui/icons-material/FlagRounded';
import ReplayRoundedIcon from '@mui/icons-material/ReplayRounded';
import { Box, Button, Chip, IconButton, Stack, Tooltip, Typography } from '@mui/material';
import type { ECharts, EChartsOption } from 'echarts';
import ReactECharts from 'echarts-for-react';
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { EquityHoldingItem, EquityPoint, EquityTradeTodayItem } from '../../types';
import {
  EQUITY_DAY_TOOLTIP_MAX_HEIGHT,
  EQUITY_DAY_TOOLTIP_WIDTH,
  EquityChartDayTooltip,
} from './EquityChartDayTooltip';
import { DASHBOARD_THEME, fmtPct, pctColor } from './utils';

const INDEX_NAME = '上证';
const TOOLTIP_GAP = 12;

function hexToRgba(hex: string, alpha: number) {
  const normalized = hex.replace('#', '');
  const r = parseInt(normalized.slice(0, 2), 16);
  const g = parseInt(normalized.slice(2, 4), 16);
  const b = parseInt(normalized.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

type RangeStats = {
  startDate: string;
  endDate: string;
  tradingDays: number;
  navReturnPct: number;
  indexReturnPct: number | null;
  excessReturnPct: number | null;
};

type ChartSeries = {
  dates: string[];
  candlesticks: [number, number, number, number][];
  navs: number[];
  cumReturns: (number | null)[];
  benchNavs: (number | null)[];
  benchDailyPcts: (number | null)[];
  holdingsByDate: Map<string, EquityHoldingItem[]>;
  tradesByDate: Map<string, EquityTradeTodayItem[]>;
  navBase: number;
  isOfficial: boolean;
};

function prepareSeries(points: EquityPoint[]): ChartSeries | null {
  if (!points.length) return null;
  const isOfficial = points.some((p) => p.nav_source === 'official');
  const dates: string[] = [];
  const candlesticks: [number, number, number, number][] = [];
  const cumReturns: (number | null)[] = [];
  const benchNavs: (number | null)[] = [];
  const benchDailyPcts: (number | null)[] = [];
  const navs: number[] = [];
  const holdingsByDate = new Map<string, EquityHoldingItem[]>();
  const tradesByDate = new Map<string, EquityTradeTodayItem[]>();

  let prevNav = 1;
  for (const p of points) {
    dates.push(p.trade_date);
    const close = typeof p.nav === 'number' ? p.nav : prevNav;
    const open = prevNav;
    candlesticks.push([open, close, Math.max(open, close), Math.min(open, close)]);
    cumReturns.push(p.cum_return_pct ?? null);
    navs.push(close);
    holdingsByDate.set(p.trade_date, p.holdings ?? []);
    tradesByDate.set(p.trade_date, p.trades_today ?? []);
    prevNav = close;
  }

  const navBase = navs[0] ?? 1;
  for (const p of points) {
    const benchPct = p.benchmark_return_pct;
    benchNavs.push(benchPct != null ? navBase * (1 + benchPct / 100) : null);
    benchDailyPcts.push(p.benchmark_daily_pct ?? null);
  }

  return {
    dates,
    candlesticks,
    navs,
    cumReturns,
    benchNavs,
    benchDailyPcts,
    holdingsByDate,
    tradesByDate,
    navBase,
    isOfficial,
  };
}

function calcRangeStats(series: ChartSeries, startIdx: number, endIdx: number): RangeStats | null {
  const from = Math.min(startIdx, endIdx);
  const to = Math.max(startIdx, endIdx);
  if (from < 0 || to >= series.dates.length || from === to) return null;

  const startNav = series.navs[from];
  const endNav = series.navs[to];
  const navReturnPct = startNav > 0 ? (endNav / startNav - 1) * 100 : 0;

  let indexReturnPct: number | null = null;
  let excessReturnPct: number | null = null;
  const b0 = series.benchNavs[from];
  const b1 = series.benchNavs[to];
  if (b0 != null && b1 != null && b0 > 0) {
    indexReturnPct = (b1 / b0 - 1) * 100;
    excessReturnPct = navReturnPct - indexReturnPct;
  }

  return {
    startDate: series.dates[from],
    endDate: series.dates[to],
    tradingDays: to - from + 1,
    navReturnPct,
    indexReturnPct,
    excessReturnPct,
  };
}

function resolveAxisIndex(
  series: ChartSeries,
  value: number | string | undefined,
): number | null {
  if (value == null) return null;
  if (typeof value === 'number' && Number.isFinite(value)) {
    const idx = Math.round(value);
    if (idx >= 0 && idx < series.dates.length) return idx;
  }
  const date = String(value);
  const idx = series.dates.indexOf(date);
  return idx >= 0 ? idx : null;
}

function flipTooltipPos(
  pixel: [number, number],
  containerW: number,
  containerH: number,
): { left: number; top: number } {
  const [px, py] = pixel;
  const tw = EQUITY_DAY_TOOLTIP_WIDTH;
  const th = EQUITY_DAY_TOOLTIP_MAX_HEIGHT;
  let left = px + TOOLTIP_GAP;
  let top = py + TOOLTIP_GAP;
  if (left + tw > containerW - 4) left = px - tw - TOOLTIP_GAP;
  if (top + th > containerH - 4) top = py - th - TOOLTIP_GAP;
  left = Math.max(4, Math.min(left, containerW - tw - 4));
  top = Math.max(4, Math.min(top, containerH - 4));
  return { left, top };
}

function buildKlineOption(
  series: ChartSeries,
  hasIndex: boolean,
  zoomStart: number,
  entryDate?: string | null,
  pendingEntryDate?: string | null,
): EChartsOption {
  const upColor = DASHBOARD_THEME.up;
  const downColor = DASHBOARD_THEME.down;
  const primary = DASHBOARD_THEME.primary;

  const markLines: {
    xAxis: string;
    lineStyle: { color: string; type: 'solid' | 'dashed'; width: number };
    label: { formatter: string; fontSize: number };
  }[] = [];
  if (entryDate && series.dates.includes(entryDate)) {
    markLines.push({
      xAxis: entryDate,
      lineStyle: { color: DASHBOARD_THEME.primary, type: 'solid', width: 2 },
      label: { formatter: '入场', fontSize: 10 },
    });
  }
  if (pendingEntryDate && pendingEntryDate !== entryDate && series.dates.includes(pendingEntryDate)) {
    markLines.push({
      xAxis: pendingEntryDate,
      lineStyle: { color: '#F59E0B', type: 'dashed', width: 2 },
      label: { formatter: '选中', fontSize: 10 },
    });
  }

  return {
    backgroundColor: 'transparent',
    animation: false,
    toolbox: { show: false },
    tooltip: {
      trigger: 'axis',
      showContent: false,
      axisPointer: { type: 'cross', crossStyle: { color: 'rgba(91, 123, 151, 0.35)' } },
      transitionDuration: 0,
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
    grid: { left: 4, right: 20, top: 12, bottom: 44, containLabel: true },
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
      data: series.dates,
      boundaryGap: true,
      axisLine: { lineStyle: { color: 'rgba(0,0,0,0.06)' } },
      axisTick: { show: false },
      axisLabel: {
        color: DASHBOARD_THEME.textMuted,
        fontSize: 10,
        hideOverlap: true,
        margin: 8,
        showMinLabel: true,
        showMaxLabel: true,
        overflow: 'none',
      },
    },
    yAxis: {
      type: 'value',
      scale: true,
      splitLine: { lineStyle: { color: 'rgba(0,0,0,0.05)', type: 'dashed' } },
      axisLabel: { color: DASHBOARD_THEME.textMuted, fontSize: 10, fontFamily: DASHBOARD_THEME.monoFont },
    },
    series: [
      ...(hasIndex
        ? [
            {
              name: INDEX_NAME,
              type: 'line' as const,
              data: series.benchNavs,
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
      {
        name: '组合',
        type: 'candlestick',
        data: series.candlesticks,
        z: 2,
        barMaxWidth: 14,
        itemStyle: {
          color: upColor,
          color0: downColor,
          borderColor: upColor,
          borderColor0: downColor,
        },
        markLine: markLines.length
          ? {
              symbol: 'none',
              silent: true,
              data: markLines,
            }
          : undefined,
      },
    ],
  };
}

export type EntryPickConfig = {
  activeDate: string | null;
  pendingDate: string | null;
  pickMode: boolean;
  rerunning: boolean;
  onTogglePickMode: () => void;
  onPickDate: (date: string) => void;
  onRerun: () => void;
  onResetFull: () => void;
  onClearPending: () => void;
};

export function EquityChart({
  points,
  height = 440,
  showBrushControls = true,
  entryPick,
}: {
  points: EquityPoint[];
  height?: number | string;
  showBrushControls?: boolean;
  entryPick?: EntryPickConfig;
}) {
  const chartRef = useRef<ReactECharts>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const chartWrapRef = useRef<HTMLDivElement>(null);
  const [rangeStats, setRangeStats] = useState<RangeStats | null>(null);
  const [isBrushActive, setIsBrushActive] = useState(false);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const [tooltipPos, setTooltipPos] = useState<{ left: number; top: number } | null>(null);
  const fillParent = height === '100%';

  useEffect(() => {
    if (!fillParent || !containerRef.current) return;
    const el = containerRef.current;
    const ro = new ResizeObserver(() => {
      chartRef.current?.getEchartsInstance()?.resize();
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [fillParent]);

  const series = useMemo(() => prepareSeries(points), [points]);
  const hasIndex = useMemo(() => points.some((p) => p.benchmark_return_pct != null), [points]);

  const zoomStart = useMemo(() => {
    if (!series) return 0;
    const windowSize = series.isOfficial ? 120 : 30;
    const len = series.dates.length;
    return len > windowSize ? Math.max(0, ((len - windowSize) / len) * 100) : 0;
  }, [series]);

  const option = useMemo(() => {
    if (!series) return {};
    const base = buildKlineOption(
      series,
      hasIndex,
      zoomStart,
      entryPick?.activeDate,
      entryPick?.pendingDate,
    );
    return {
      ...base,
      tooltip: {
        ...(base.tooltip as object),
        axisPointer: {
          type: 'cross',
          show: !isBrushActive,
          crossStyle: { color: 'rgba(91, 123, 151, 0.35)' },
        },
      },
    };
  }, [series, hasIndex, zoomStart, isBrushActive, entryPick?.activeDate, entryPick?.pendingDate]);

  const onChartClick = useCallback(
    (params: { componentType?: string; dataIndex?: number; name?: string }) => {
      if (!entryPick?.pickMode || !series) return;
      let idx = params.dataIndex;
      if (idx == null && params.name) {
        idx = series.dates.indexOf(params.name);
      }
      if (idx == null || idx < 0) return;
      entryPick.onPickDate(series.dates[idx]);
    },
    [entryPick, series],
  );

  const getChart = useCallback((): ECharts | undefined => chartRef.current?.getEchartsInstance(), []);

  const startBrush = useCallback(() => {
    const chart = getChart();
    if (!chart) return;
    setIsBrushActive(true);
    setHoverIndex(null);
    setTooltipPos(null);
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
      if (!series) return;
      const area = params.areas?.[0];
      const range = area?.coordRange ?? area?.range;
      if (!range || range.length < 2) {
        setRangeStats(null);
        return;
      }
      const startIdx = Math.max(0, Math.floor(Math.min(range[0], range[1])));
      const endIdx = Math.min(series.dates.length - 1, Math.ceil(Math.max(range[0], range[1])));
      setRangeStats(calcRangeStats(series, startIdx, endIdx));
      setIsBrushActive(false);
      getChart()?.dispatchAction({ type: 'takeGlobalCursor', key: 'brush', brushOption: { brushType: false } });
    },
    [series, getChart],
  );

  const onUpdateAxisPointer = useCallback(
    (event: { axesInfo?: Array<{ axisDim?: string; value?: number | string }> }) => {
      if (!series || isBrushActive) return;
      const xInfo = event.axesInfo?.find((a) => a.axisDim === 'x') ?? event.axesInfo?.[0];
      const idx = resolveAxisIndex(series, xInfo?.value);
      setHoverIndex(idx);
    },
    [series, isBrushActive],
  );

  const onGlobalOut = useCallback(() => {
    setHoverIndex(null);
    setTooltipPos(null);
  }, []);

  useLayoutEffect(() => {
    if (hoverIndex == null || !series || !chartWrapRef.current) {
      setTooltipPos(null);
      return;
    }
    const chart = getChart();
    if (!chart) return;
    const yVal = series.navs[hoverIndex] ?? 0;
    const raw = chart.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [hoverIndex, yVal]);
    if (!raw || !Array.isArray(raw) || raw.some((n) => !Number.isFinite(n))) {
      setTooltipPos(null);
      return;
    }
    const pixel = raw as [number, number];
    const { clientWidth: cw, clientHeight: ch } = chartWrapRef.current;
    setTooltipPos(flipTooltipPos(pixel, cw, ch));
  }, [hoverIndex, series, getChart, option]);

  const onEvents = useMemo(
    () => ({
      brushEnd: onBrushEnd,
      updateAxisPointer: onUpdateAxisPointer,
      globalout: onGlobalOut,
      click: onChartClick,
    }),
    [onBrushEnd, onUpdateAxisPointer, onGlobalOut, onChartClick],
  );

  const showToolbar = showBrushControls || entryPick;
  const toolbarHeight = showToolbar ? (entryPick ? 68 : 34) : 0;

  const hoverTooltip =
    !isBrushActive && hoverIndex != null && tooltipPos != null && series ? (() => {
      const date = series.dates[hoverIndex];
      const [open, closeVal] = series.candlesticks[hoverIndex];
      const dayPct = open > 0 ? ((closeVal / open - 1) * 100) : 0;
      const benchNav = series.benchNavs[hoverIndex];
      const benchCumPct =
        benchNav != null && series.navBase > 0 ? (benchNav / series.navBase - 1) * 100 : null;
      return (
        <EquityChartDayTooltip
          date={date}
          dayPct={dayPct}
          close={closeVal}
          cumReturn={series.cumReturns[hoverIndex]}
          benchDailyPct={series.benchDailyPcts[hoverIndex]}
          benchCumPct={benchCumPct}
          trades={series.tradesByDate.get(date) ?? []}
          holdings={series.holdingsByDate.get(date) ?? []}
          left={tooltipPos.left}
          top={tooltipPos.top}
        />
      );
    })() : null;

  if (!series) {
    return (
      <Box
        ref={containerRef}
        sx={{
          height: fillParent ? '100%' : height,
          minHeight: fillParent ? 200 : undefined,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <Typography sx={{ fontSize: 13, color: DASHBOARD_THEME.textMuted }}>暂无净值数据</Typography>
      </Box>
    );
  }

  return (
    <Box
      ref={containerRef}
      sx={{
        display: 'flex',
        flexDirection: 'column',
        height: fillParent ? '100%' : height,
        minHeight: fillParent ? 0 : typeof height === 'number' ? height : 400,
      }}
    >
      {showToolbar && (
        <Stack spacing={0.5} sx={{ mb: 0.5, minHeight: toolbarHeight, flexShrink: 0 }}>
          {entryPick && (
            <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
              <Chip
                size="small"
                icon={<FlagRoundedIcon />}
                label={entryPick.pickMode ? '点击图表选入场日' : '选入场点'}
                color={entryPick.pickMode ? 'primary' : 'default'}
                variant={entryPick.pickMode ? 'filled' : 'outlined'}
                onClick={entryPick.onTogglePickMode}
              />
              {entryPick.pendingDate && (
                <Typography sx={{ fontSize: 11, color: DASHBOARD_THEME.textSecondary }}>
                  已选 {entryPick.pendingDate}
                </Typography>
              )}
              {entryPick.activeDate && (
                <Chip size="small" label={`当前自 ${entryPick.activeDate}`} variant="outlined" color="primary" />
              )}
              {entryPick.pendingDate && (
                <Button
                  size="small"
                  variant="contained"
                  disabled={entryPick.rerunning}
                  startIcon={<ReplayRoundedIcon />}
                  onClick={entryPick.onRerun}
                >
                  {entryPick.rerunning ? '回测中…' : '从此日重新回测'}
                </Button>
              )}
              {entryPick.activeDate && (
                <Button size="small" variant="text" disabled={entryPick.rerunning} onClick={entryPick.onResetFull}>
                  恢复全历史
                </Button>
              )}
              {entryPick.pendingDate && (
                <Button size="small" variant="text" onClick={entryPick.onClearPending}>
                  清除选择
                </Button>
              )}
            </Stack>
          )}
          {showBrushControls && (
            <Stack
              direction="row"
              spacing={1}
              alignItems="center"
              justifyContent="space-between"
              flexWrap="wrap"
              sx={{ gap: 1 }}
            >
              <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" sx={{ flex: 1, minWidth: 0 }}>
                {rangeStats ? (
                  <>
                    <Typography sx={{ fontSize: 11, color: DASHBOARD_THEME.textSecondary, fontWeight: 600 }}>
                      {rangeStats.startDate}→{rangeStats.endDate}
                    </Typography>
                    <Typography sx={{ fontSize: 11, color: DASHBOARD_THEME.textMuted }}>{rangeStats.tradingDays}日</Typography>
                    <Typography
                      sx={{ fontSize: 12, fontWeight: 600, color: pctColor(rangeStats.navReturnPct), fontFamily: DASHBOARD_THEME.monoFont }}
                    >
                      组合 {fmtPct(rangeStats.navReturnPct)}
                    </Typography>
                    {rangeStats.indexReturnPct != null && (
                      <Typography sx={{ fontSize: 11, color: DASHBOARD_THEME.textSecondary, fontFamily: DASHBOARD_THEME.monoFont }}>
                        上证 {fmtPct(rangeStats.indexReturnPct)}
                      </Typography>
                    )}
                    {rangeStats.excessReturnPct != null && (
                      <Typography
                        sx={{ fontSize: 11, color: pctColor(rangeStats.excessReturnPct), fontFamily: DASHBOARD_THEME.monoFont }}
                      >
                        超额 {fmtPct(rangeStats.excessReturnPct)}
                      </Typography>
                    )}
                  </>
                ) : (
                  <Typography sx={{ fontSize: 11, color: DASHBOARD_THEME.textMuted }}>框选区间可查看涨跌幅统计</Typography>
                )}
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
          )}
        </Stack>
      )}

      <Box
        ref={chartWrapRef}
        sx={{
          position: 'relative',
          flex: 1,
          minHeight: fillParent ? 0 : typeof height === 'number' ? height - toolbarHeight : 380,
        }}
      >
        <ReactECharts
          ref={chartRef}
          option={option}
          style={{
            height: '100%',
            width: '100%',
            minHeight: fillParent ? 0 : typeof height === 'number' ? height - toolbarHeight : 380,
          }}
          notMerge
          lazyUpdate
          onEvents={onEvents}
        />
        {hoverTooltip}
      </Box>
    </Box>
  );
}
