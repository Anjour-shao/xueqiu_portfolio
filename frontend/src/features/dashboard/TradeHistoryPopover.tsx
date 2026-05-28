import { Box, Popover, Typography } from '@mui/material';
import { GroupedStatItem, TradeItem } from '../../types';
import { actionLabel, DASHBOARD_THEME, fmtDateOnly, fmtHoldingDays, fmtPct } from '../dashboard/utils';

const POPOVER_WIDTH = 320;
const POPOVER_MAX_HEIGHT = 360;

type Props = {
  anchorEl: HTMLElement | null;
  open: boolean;
  onClose: () => void;
  stockName: string;
  stat: GroupedStatItem;
  trades: TradeItem[];
};

export function TradeHistoryPopover({ anchorEl, open, onClose, stockName, stat, trades }: Props) {
  return (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={onClose}
      anchorOrigin={{ vertical: 'center', horizontal: 'right' }}
      transformOrigin={{ vertical: 'center', horizontal: 'left' }}
      slotProps={{
        paper: {
          sx: {
            width: POPOVER_WIDTH,
            maxHeight: POPOVER_MAX_HEIGHT,
            overflow: 'hidden',
            display: 'flex',
            flexDirection: 'column',
            border: DASHBOARD_THEME.cardBorder,
            boxShadow: DASHBOARD_THEME.shadowMd,
            borderRadius: `${DASHBOARD_THEME.radiusMd}px`,
          },
        },
      }}
    >
      <Box sx={{ px: 1.5, py: 1.25, borderBottom: `1px solid ${DASHBOARD_THEME.borderSubtle}`, flexShrink: 0 }}>
        <Typography sx={{ fontSize: 13, fontWeight: 600, color: DASHBOARD_THEME.textPrimary }}>{stockName}</Typography>
        {stat.holding_days != null && (
          <Typography sx={{ fontSize: 11, color: DASHBOARD_THEME.textSecondary, mt: 0.25 }}>
            持仓时长 {fmtHoldingDays(stat.holding_days, stat.is_holding)}
            {stat.holding_opened_at ? ` · 本轮 ${fmtDateOnly(stat.holding_opened_at)} 起` : ''}
          </Typography>
        )}
        <Typography sx={{ fontSize: 10, fontWeight: 600, color: DASHBOARD_THEME.textMuted, mt: 0.5 }}>
          调仓记录（{trades.length} 笔）
        </Typography>
      </Box>
      <Box sx={{ flex: 1, overflow: 'auto', px: 1.5, py: 1 }}>
        {!trades.length ? (
          <Typography sx={{ fontSize: 12, color: DASHBOARD_THEME.textMuted }}>暂无交易记录</Typography>
        ) : (
          trades.map((t) => (
            <Box
              key={t.id}
              sx={{
                display: 'grid',
                gridTemplateColumns: '72px 44px 1fr auto',
                gap: 0.5,
                alignItems: 'center',
                fontFamily: DASHBOARD_THEME.monoFont,
                fontSize: 11,
                py: 0.35,
                borderBottom: `1px solid ${DASHBOARD_THEME.borderSubtle}`,
                '&:last-child': { borderBottom: 'none' },
              }}
            >
              <span>{fmtDateOnly(t.trade_time)}</span>
              <span>{actionLabel(t.action)}</span>
              <span>
                {t.from_weight.toFixed(0)}→{t.to_weight.toFixed(0)}%
              </span>
              <span style={{ color: DASHBOARD_THEME.textSecondary }}>
                {t.price?.toFixed(2) ?? '-'}
                {t.return_pct != null ? ` · ${fmtPct(t.return_pct)}` : ''}
              </span>
            </Box>
          ))
        )}
      </Box>
    </Popover>
  );
}
