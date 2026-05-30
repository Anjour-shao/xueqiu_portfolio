import DeleteOutlineRoundedIcon from '@mui/icons-material/DeleteOutlineRounded';
import { Box, Chip, CircularProgress, IconButton, Stack, Typography } from '@mui/material';
import { MouseEvent } from 'react';
import type { PortfolioOverviewItem } from '../../types';
import { DASHBOARD_THEME, fmtPct, monoSx, pctColor, surfaceCardSx } from '../dashboard/utils';

type Props = {
  item: PortfolioOverviewItem;
  watchReasons?: string[];
  deleting?: boolean;
  onOpen: () => void;
  onDelete: (e: MouseEvent) => void;
};

export function OverviewPortfolioCard({ item, watchReasons = [], deleting, onOpen, onDelete }: Props) {
  const cum = item.cum_return_pct;
  const excess = item.excess_return_pct;
  const navDate = item.latest_nav_date ?? '—';
  const tradeDate = item.latest_trade_time ? item.latest_trade_time.slice(0, 10) : '—';

  return (
    <Box
      onClick={onOpen}
      sx={{
        ...surfaceCardSx,
        position: 'relative',
        p: 1.75,
        cursor: 'pointer',
        transition: 'transform 0.15s ease, box-shadow 0.15s ease',
        '&:hover': {
          transform: 'translateY(-2px)',
          boxShadow: DASHBOARD_THEME.shadowMd,
          bgcolor: DASHBOARD_THEME.rowHover,
        },
      }}
    >
      <IconButton
        size="small"
        color="error"
        disabled={deleting}
        onClick={onDelete}
        aria-label="删除组合"
        sx={{
          position: 'absolute',
          top: 6,
          right: 6,
          opacity: 0.55,
          '&:hover': { opacity: 1 },
        }}
      >
        {deleting ? <CircularProgress size={16} color="inherit" /> : <DeleteOutlineRoundedIcon fontSize="small" />}
      </IconButton>

      <Typography
        noWrap
        title={item.account_name}
        sx={{ fontSize: 14, fontWeight: 600, color: DASHBOARD_THEME.textPrimary, pr: 3 }}
      >
        {item.account_name}
      </Typography>
      <Typography sx={{ fontSize: 11, color: DASHBOARD_THEME.textMuted, fontFamily: DASHBOARD_THEME.monoFont, mb: 1.25 }}>
        {item.account_code}
      </Typography>

      <Typography
        sx={{
          ...monoSx,
          fontSize: 22,
          fontWeight: 700,
          lineHeight: 1.2,
          color: cum != null ? pctColor(cum) : DASHBOARD_THEME.textMuted,
          mb: 0.75,
        }}
      >
        {cum != null ? fmtPct(cum) : '—'}
      </Typography>

      <Stack direction="row" spacing={1.5} sx={{ mb: 1 }}>
        <Box>
          <Typography sx={{ fontSize: 10, color: DASHBOARD_THEME.textMuted }}>超额</Typography>
          <Typography sx={{ ...monoSx, fontSize: 12, fontWeight: 600, color: excess != null ? pctColor(excess) : DASHBOARD_THEME.textMuted }}>
            {excess != null ? fmtPct(excess) : '—'}
          </Typography>
        </Box>
        <Box>
          <Typography sx={{ fontSize: 10, color: DASHBOARD_THEME.textMuted }}>持仓</Typography>
          <Typography sx={{ ...monoSx, fontSize: 12, fontWeight: 600 }}>{item.holding_count}</Typography>
        </Box>
      </Stack>

      <Typography sx={{ fontSize: 10, color: DASHBOARD_THEME.textMuted, lineHeight: 1.4 }}>
        净值 {navDate} · 调仓 {tradeDate}
      </Typography>

      {watchReasons.length > 0 && (
        <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap sx={{ mt: 1 }}>
          {watchReasons.map((r) => (
            <Chip
              key={r}
              label={r}
              size="small"
              sx={{
                height: 20,
                fontSize: 10,
                bgcolor: 'rgba(180, 83, 9, 0.08)',
                color: '#B45309',
                border: 'none',
              }}
            />
          ))}
        </Stack>
      )}
    </Box>
  );
}
