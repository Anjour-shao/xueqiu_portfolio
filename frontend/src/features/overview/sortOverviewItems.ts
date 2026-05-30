import type { PortfolioOverviewItem } from '../../types';

export type OverviewSortKey = 'cum_return_pct' | 'excess_return_pct' | 'latest_trade_time';

export type OverviewSort = {
  key: OverviewSortKey;
  desc: boolean;
};

export function sortOverviewItems(items: PortfolioOverviewItem[], sort: OverviewSort) {
  const list = [...items];
  const desc = sort.desc ? -1 : 1;
  list.sort((a, b) => {
    switch (sort.key) {
      case 'cum_return_pct':
        return desc * ((a.cum_return_pct ?? -1e9) - (b.cum_return_pct ?? -1e9));
      case 'excess_return_pct':
        return desc * ((a.excess_return_pct ?? -1e9) - (b.excess_return_pct ?? -1e9));
      case 'latest_trade_time':
        return desc * (a.latest_trade_time ?? '').localeCompare(b.latest_trade_time ?? '');
      default:
        return 0;
    }
  });
  return list;
}
