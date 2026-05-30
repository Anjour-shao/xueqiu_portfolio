import { Box, Tab, Tabs, Typography } from '@mui/material';
import { MouseEvent, useMemo, useState } from 'react';
import { DashboardPayload, GroupedStatItem, PositionItem, TradeItem } from '../../types';
import { DataTable, TableColumn, TableSort, toggleSort } from './DataTable';
import { stockUrl } from '../../lib/xueqiuLinks';
import { TradeHistoryPopover } from './TradeHistoryPopover';
import { actionLabel, DASHBOARD_THEME, fmtDateOnly, fmtHoldingDays, fmtPct, surfaceCardSx } from './utils';

const DETAIL_TABLE_PROPS = { compact: true, dense: true, minWidth: 420 } as const;

const POSITION_COLS: TableColumn[] = [
  { key: 'stock_name', label: '股票', sortable: true, width: '20%' },
  { key: 'holding_days', label: '持仓时长', sortable: true, width: '14%' },
  { key: 'current_weight', label: '仓位', sortable: true, width: '12%' },
  { key: 'avg_cost', label: '成本', sortable: true, width: '14%', clip: false },
  { key: 'mark_price', label: '现价', sortable: true, width: '14%', clip: false },
  { key: 'return_pct', label: '浮动', sortable: true, width: '14%', clip: false },
];

const TRADE_COLS: TableColumn[] = [
  { key: 'trade_time', label: '日期', sortable: true, width: '20%', clip: false },
  { key: 'stock_name', label: '股票', sortable: true, width: '18%' },
  { key: 'action', label: '动作', width: '10%' },
  { key: 'weight', label: '仓位', width: '14%' },
  { key: 'price', label: '成交价', sortable: true, width: '16%', clip: false },
  { key: 'return_pct', label: '收益', sortable: true, width: '22%', clip: false },
];

const STOCK_COLS: TableColumn[] = [
  { key: 'stock_name', label: '股票', sortable: true, width: '20%' },
  { key: 'holding_days', label: '持仓时长', sortable: true, width: '16%' },
  { key: 'realized_count', label: '平仓', sortable: true, width: '10%' },
  { key: 'win_rate', label: '胜率', sortable: true, width: '12%', clip: false },
  { key: 'cum_return_pct', label: '累计收益', sortable: true, width: '18%', clip: false },
  { key: 'last_trade_time', label: '最近调仓', sortable: true, width: '24%', clip: false },
];

function cmpNum(a: number | null | undefined, b: number | null | undefined) {
  const av = a ?? -Infinity;
  const bv = b ?? -Infinity;
  return av - bv;
}

function sortPositions(items: PositionItem[], sort: TableSort) {
  const list = [...items];
  const desc = sort.desc ? -1 : 1;
  list.sort((a, b) => {
    switch (sort.key) {
      case 'stock_name':
        return desc * a.stock_name.localeCompare(b.stock_name, 'zh-CN');
      case 'current_weight':
        return desc * (a.current_weight - b.current_weight);
      case 'avg_cost':
        return desc * cmpNum(a.avg_cost ?? a.avg_cost_hfq, b.avg_cost ?? b.avg_cost_hfq);
      case 'mark_price':
        return desc * cmpNum(a.mark_price ?? a.mark_price_hfq, b.mark_price ?? b.mark_price_hfq);
      case 'return_pct':
        return desc * cmpNum(a.return_pct, b.return_pct);
      case 'holding_days':
        return desc * cmpNum(a.holding_days, b.holding_days);
      default:
        return 0;
    }
  });
  return list;
}

function sortTrades(items: TradeItem[], sort: TableSort) {
  const list = [...items];
  const desc = sort.desc ? -1 : 1;
  list.sort((a, b) => {
    switch (sort.key) {
      case 'trade_time':
        return desc * a.trade_time.localeCompare(b.trade_time);
      case 'stock_name':
        return desc * a.stock_name.localeCompare(b.stock_name, 'zh-CN');
      case 'price':
        return desc * cmpNum(a.price, b.price);
      case 'return_pct':
        return desc * cmpNum(a.return_pct, b.return_pct);
      default:
        return 0;
    }
  });
  return list;
}

function sortGrouped(items: GroupedStatItem[], sort: TableSort) {
  const list = [...items];
  const desc = sort.desc ? -1 : 1;
  list.sort((a, b) => {
    switch (sort.key) {
      case 'last_trade_time':
        return desc * (a.last_trade_time ?? '').localeCompare(b.last_trade_time ?? '');
      case 'cum_return_pct':
        return desc * (stockCumReturn(a) - stockCumReturn(b));
      case 'win_rate':
        return desc * (a.win_rate - b.win_rate);
      case 'realized_count':
        return desc * (a.realized_count - b.realized_count);
      case 'holding_days':
        return desc * cmpNum(a.holding_days, b.holding_days);
      case 'stock_name':
        return desc * a.stock_name.localeCompare(b.stock_name, 'zh-CN');
      default:
        return 0;
    }
  });
  return list;
}

function positionsRows(positions: PositionItem[]) {
  return positions.map((item) => [
    item.stock_name,
    fmtHoldingDays(item.holding_days, item.is_holding ?? true),
    `${item.current_weight.toFixed(1)}%`,
    (item.avg_cost ?? item.avg_cost_hfq)?.toFixed(2) ?? '-',
    (item.mark_price ?? item.mark_price_hfq)?.toFixed(2) ?? '-',
    fmtPct(item.return_pct),
  ]);
}

function stockCumReturn(item: GroupedStatItem) {
  const legacy = item as GroupedStatItem & { avg_return_pct?: number };
  return item.cum_return_pct ?? legacy.avg_return_pct;
}

function groupedRows(stats: GroupedStatItem[]) {
  return stats.map((item) => [
    item.stock_name,
    fmtHoldingDays(item.holding_days, item.is_holding),
    item.realized_count,
    `${item.win_rate.toFixed(0)}%`,
    fmtPct(stockCumReturn(item)),
    fmtDateOnly(item.last_trade_time),
  ]);
}

function buildTradesByCode(trades: TradeItem[]): Map<string, TradeItem[]> {
  const map = new Map<string, TradeItem[]>();
  for (const t of trades) {
    const list = map.get(t.ts_code) ?? [];
    list.push(t);
    map.set(t.ts_code, list);
  }
  for (const list of map.values()) {
    list.sort((a, b) => a.trade_time.localeCompare(b.trade_time));
  }
  return map;
}

function tradeRows(trades: TradeItem[]) {
  return trades.map((item) => [
    fmtDateOnly(item.trade_time),
    item.stock_name,
    actionLabel(item.action),
    `${item.from_weight.toFixed(0)}→${item.to_weight.toFixed(0)}%`,
    item.price?.toFixed(2) ?? '-',
    fmtPct(item.return_pct),
  ]);
}

const tabSx = {
  minHeight: 36,
  flexShrink: 0,
  maxWidth: '100%',
  '& .MuiTabs-scroller': { overflow: 'auto !important' },
  '& .MuiTabs-indicator': {
    height: 2,
    borderRadius: '2px 2px 0 0',
    backgroundColor: DASHBOARD_THEME.primary,
  },
  '& .MuiTab-root': {
    textTransform: 'none',
    fontWeight: 600,
    fontSize: 13,
    minHeight: 36,
    minWidth: 'auto',
    px: 1.5,
    py: 0,
    color: DASHBOARD_THEME.textSecondary,
  },
  '& .Mui-selected': { color: DASHBOARD_THEME.textPrimary },
};

export function DetailDataPanel({
  dashboard,
  livePositions,
  allTrades,
}: {
  dashboard: DashboardPayload;
  livePositions: PositionItem[];
  allTrades: TradeItem[];
}) {
  const [tab, setTab] = useState(0);
  const [positionSort, setPositionSort] = useState<TableSort>({ key: 'current_weight', desc: true });
  const [tradeSort, setTradeSort] = useState<TableSort>({ key: 'trade_time', desc: true });
  const [stockSort, setStockSort] = useState<TableSort>({ key: 'cum_return_pct', desc: true });
  const [tradePopover, setTradePopover] = useState<{
    anchor: HTMLElement;
    stat: GroupedStatItem;
  } | null>(null);

  const sortedPositions = useMemo(
    () => sortPositions(livePositions, positionSort),
    [livePositions, positionSort],
  );
  const sortedTrades = useMemo(() => sortTrades(allTrades, tradeSort), [allTrades, tradeSort]);
  const sortedGrouped = useMemo(
    () => sortGrouped(dashboard.grouped_stats, stockSort),
    [dashboard.grouped_stats, stockSort],
  );
  const tradesByCode = useMemo(() => buildTradesByCode(allTrades), [allTrades]);

  const popoverTrades = tradePopover ? (tradesByCode.get(tradePopover.stat.ts_code) ?? []) : [];

  const stockCellLink = (rowIndex: number, columnKey: string) => {
    if (columnKey !== 'stock_name') return null;
    if (tab === 0) {
      const item = sortedPositions[rowIndex];
      return item?.ts_code ? stockUrl(item.ts_code) : null;
    }
    if (tab === 1) {
      const item = sortedTrades[rowIndex];
      return item?.ts_code ? stockUrl(item.ts_code) : null;
    }
    return null;
  };

  const handleStockCellClick = (rowIndex: number, columnKey: string, event: MouseEvent) => {
    if (tab !== 2 || columnKey !== 'stock_name') return;
    const stat = sortedGrouped[rowIndex];
    if (!stat) return;
    setTradePopover({ anchor: event.currentTarget as HTMLElement, stat });
  };

  return (
    <Box
      component="section"
      aria-label="持仓与交易明细"
      sx={{
        ...surfaceCardSx,
        p: { xs: 1.5, md: 2 },
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0,
        minWidth: 0,
        width: '100%',
        maxWidth: '100%',
        height: '100%',
        overflow: 'hidden',
        boxSizing: 'border-box',
      }}
    >
      <Tabs
        value={tab}
        onChange={(_, value) => setTab(value)}
        variant="scrollable"
        scrollButtons="auto"
        allowScrollButtonsMobile
        sx={tabSx}
      >
        <Tab label={`持仓 ${livePositions.length}`} />
        <Tab label={`交易 ${allTrades.length}`} />
        <Tab label={`股票 ${dashboard.grouped_stats.length}`} />
      </Tabs>

      <Box sx={{ flex: 1, minHeight: 0, minWidth: 0, overflow: 'auto', mt: 1 }}>
        {tab === 0 && (
          <DataTable
            {...DETAIL_TABLE_PROPS}
            columns={POSITION_COLS}
            rows={positionsRows(sortedPositions)}
            sort={positionSort}
            onSort={(key) => setPositionSort((s) => toggleSort(s, key, true))}
            getCellLink={stockCellLink}
          />
        )}
        {tab === 1 && (
          <DataTable
            {...DETAIL_TABLE_PROPS}
            columns={TRADE_COLS}
            rows={tradeRows(sortedTrades)}
            sort={tradeSort}
            onSort={(key) => setTradeSort((s) => toggleSort(s, key, key === 'trade_time'))}
            getCellLink={stockCellLink}
          />
        )}
        {tab === 2 && (
          <>
            <Typography sx={{ fontSize: 11, color: DASHBOARD_THEME.textMuted, mb: 1, px: 0.25 }}>
              点击股票名查看该股全部调仓记录
            </Typography>
            <DataTable
              {...DETAIL_TABLE_PROPS}
              columns={STOCK_COLS}
              rows={groupedRows(sortedGrouped)}
              sort={stockSort}
              onSort={(key) => setStockSort((s) => toggleSort(s, key, key === 'last_trade_time' || key === 'cum_return_pct'))}
              onCellClick={handleStockCellClick}
            />
          </>
        )}
      </Box>

      {tradePopover && (
        <TradeHistoryPopover
          anchorEl={tradePopover.anchor}
          open
          onClose={() => setTradePopover(null)}
          stockName={tradePopover.stat.stock_name}
          stat={tradePopover.stat}
          trades={popoverTrades}
        />
      )}
    </Box>
  );
}
