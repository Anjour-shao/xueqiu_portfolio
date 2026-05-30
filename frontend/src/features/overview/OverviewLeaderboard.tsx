import { Box, Stack, Typography } from '@mui/material';
import type { PortfolioOverviewItem } from '../../types';
import { DASHBOARD_THEME, fmtPct, monoSx, pctColor, surfaceCardSx } from '../dashboard/utils';

function RankBadge({ rank }: { rank: number }) {
  return (
    <Box
      sx={{
        width: 22,
        height: 22,
        borderRadius: '50%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexShrink: 0,
        fontSize: 11,
        fontWeight: 700,
        bgcolor: DASHBOARD_THEME.insetBg,
        color: DASHBOARD_THEME.textSecondary,
        fontFamily: DASHBOARD_THEME.monoFont,
      }}
    >
      {rank}
    </Box>
  );
}

function LeaderboardRow({
  rank,
  item,
  onClick,
}: {
  rank: number;
  item: PortfolioOverviewItem;
  onClick: () => void;
}) {
  return (
    <Box
      onClick={onClick}
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 1,
        py: 0.75,
        px: 0.5,
        borderRadius: 1,
        cursor: 'pointer',
        '&:hover': { bgcolor: DASHBOARD_THEME.rowHover },
      }}
    >
      <RankBadge rank={rank} />
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography noWrap title={item.account_name} sx={{ fontSize: 13, fontWeight: 600 }}>
          {item.account_name}
        </Typography>
        <Typography sx={{ fontSize: 10, color: DASHBOARD_THEME.textMuted, fontFamily: DASHBOARD_THEME.monoFont }}>
          {item.account_code}
        </Typography>
      </Box>
      <Stack alignItems="flex-end" spacing={0.125} sx={{ flexShrink: 0 }}>
        <Typography
          sx={{
            ...monoSx,
            fontSize: 12,
            fontWeight: 600,
            color: item.cum_return_pct != null ? pctColor(item.cum_return_pct) : DASHBOARD_THEME.textMuted,
          }}
        >
          {item.cum_return_pct != null ? fmtPct(item.cum_return_pct) : '—'}
        </Typography>
        <Typography
          sx={{
            ...monoSx,
            fontSize: 10,
            color: item.excess_return_pct != null ? pctColor(item.excess_return_pct) : DASHBOARD_THEME.textMuted,
          }}
        >
          超额 {item.excess_return_pct != null ? fmtPct(item.excess_return_pct) : '—'}
        </Typography>
      </Stack>
    </Box>
  );
}

type Props = {
  topPerformers: PortfolioOverviewItem[];
  bottomPerformers: PortfolioOverviewItem[];
  onOpen: (code: string) => void;
};

export function OverviewLeaderboard({ topPerformers, bottomPerformers, onOpen }: Props) {
  const hasTop = topPerformers.length > 0;
  const hasBottom = bottomPerformers.length > 0;

  if (!hasTop && !hasBottom) {
    return (
      <Typography sx={{ fontSize: 13, color: DASHBOARD_THEME.textMuted, textAlign: 'center', py: 4 }}>
        暂无排行数据
      </Typography>
    );
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, height: '100%' }}>
      {hasTop && (
        <Box sx={{ ...surfaceCardSx, p: 1.5, flex: 1 }}>
          <Typography sx={{ fontSize: 12, fontWeight: 600, color: DASHBOARD_THEME.textMuted, mb: 1, letterSpacing: '0.04em' }}>
            表现靠前
          </Typography>
          <Stack spacing={0.25}>
            {topPerformers.map((item, i) => (
              <LeaderboardRow key={item.account_code} rank={i + 1} item={item} onClick={() => onOpen(item.account_code)} />
            ))}
          </Stack>
        </Box>
      )}
      {hasBottom && (
        <Box sx={{ ...surfaceCardSx, p: 1.5, flex: 1 }}>
          <Typography sx={{ fontSize: 12, fontWeight: 600, color: DASHBOARD_THEME.textMuted, mb: 1, letterSpacing: '0.04em' }}>
            表现靠后
          </Typography>
          <Stack spacing={0.25}>
            {bottomPerformers.map((item, i) => (
              <LeaderboardRow key={item.account_code} rank={i + 1} item={item} onClick={() => onOpen(item.account_code)} />
            ))}
          </Stack>
        </Box>
      )}
    </Box>
  );
}
