import { Box, Chip, Stack, Typography } from '@mui/material';
import { MouseEvent, useMemo, useState } from 'react';
import type { OverviewWatchItem, PortfolioOverviewItem } from '../../types';
import { DASHBOARD_THEME } from '../dashboard/utils';
import { OverviewPortfolioCard } from './OverviewPortfolioCard';
import { sortOverviewItems, type OverviewSort, type OverviewSortKey } from './sortOverviewItems';

const SORT_OPTIONS: { key: OverviewSortKey; label: string }[] = [
  { key: 'cum_return_pct', label: '累计收益' },
  { key: 'excess_return_pct', label: '超额' },
  { key: 'latest_trade_time', label: '最近调仓' },
];

type Props = {
  items: PortfolioOverviewItem[];
  watchlist: OverviewWatchItem[];
  deletingCode: string | null;
  onOpen: (code: string) => void;
  onDelete: (code: string, name: string, e: MouseEvent) => void;
};

export function OverviewPortfolioGrid({ items, watchlist, deletingCode, onOpen, onDelete }: Props) {
  const [sort, setSort] = useState<OverviewSort>({ key: 'cum_return_pct', desc: true });

  const watchByCode = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const w of watchlist) {
      map.set(w.account_code, w.reasons);
    }
    return map;
  }, [watchlist]);

  const sortedItems = useMemo(() => sortOverviewItems(items, sort), [items, sort]);

  return (
    <Box>
      <Stack direction="row" alignItems="center" justifyContent="space-between" flexWrap="wrap" gap={1} sx={{ mb: 1.5 }}>
        <Typography sx={{ fontSize: 15, fontWeight: 600, color: DASHBOARD_THEME.textPrimary }}>全部组合</Typography>
        <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
          {SORT_OPTIONS.map((opt) => {
            const active = sort.key === opt.key;
            return (
              <Chip
                key={opt.key}
                label={`${opt.label}${active && sort.desc ? ' ↓' : ''}`}
                size="small"
                clickable
                onClick={() =>
                  setSort((s) =>
                    s.key === opt.key ? { key: opt.key, desc: !s.desc } : { key: opt.key, desc: true },
                  )
                }
                sx={{
                  height: 26,
                  fontSize: 11,
                  fontWeight: active ? 600 : 400,
                  bgcolor: active ? DASHBOARD_THEME.navActive : 'transparent',
                  border: active ? `1px solid ${DASHBOARD_THEME.primary}` : DASHBOARD_THEME.cardBorder,
                  color: active ? DASHBOARD_THEME.primary : DASHBOARD_THEME.textSecondary,
                }}
              />
            );
          })}
        </Stack>
      </Stack>

      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
          gap: 1.5,
        }}
      >
        {sortedItems.map((item) => (
          <OverviewPortfolioCard
            key={item.account_code}
            item={item}
            watchReasons={watchByCode.get(item.account_code)}
            deleting={deletingCode === item.account_code}
            onOpen={() => onOpen(item.account_code)}
            onDelete={(e) => onDelete(item.account_code, item.account_name, e)}
          />
        ))}
      </Box>
    </Box>
  );
}
