import HelpOutlineRoundedIcon from '@mui/icons-material/HelpOutlineRounded';
import { Box, Tooltip, Typography } from '@mui/material';
import { DASHBOARD_THEME, monoSx, pctTintBg } from './utils';

export function StatChip({
  label,
  value,
  color,
  compact = false,
  tooltip,
  sub,
}: {
  label: string;
  value: string;
  color?: string;
  compact?: boolean;
  tooltip?: string;
  sub?: string;
}) {
  const tint = pctTintBg(value);

  const chip = (
    <Box
      sx={{
        px: compact ? 0.75 : 1,
        py: compact ? 0.5 : 0.75,
        borderRadius: `${DASHBOARD_THEME.radiusMd}px`,
        bgcolor: tint,
        border: DASHBOARD_THEME.cardBorder,
        boxShadow: 'none',
        minHeight: compact ? 52 : sub ? 64 : 56,
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        textAlign: 'center',
      }}
    >
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 0.25,
          mb: 0.25,
          width: '100%',
        }}
      >
        <Typography
          sx={{
            fontSize: compact ? 9 : 10,
            fontWeight: 500,
            color: DASHBOARD_THEME.textMuted,
            lineHeight: 1.2,
            letterSpacing: '0.02em',
            whiteSpace: 'nowrap',
          }}
        >
          {label}
        </Typography>
        {tooltip && (
          <HelpOutlineRoundedIcon sx={{ fontSize: 11, color: DASHBOARD_THEME.textMuted, opacity: 0.85 }} />
        )}
      </Box>
      <Typography
        sx={{
          ...monoSx,
          fontSize: compact ? 14 : 18,
          fontWeight: 600,
          color: color ?? DASHBOARD_THEME.textPrimary,
          lineHeight: 1.15,
          whiteSpace: 'nowrap',
        }}
      >
        {value}
      </Typography>
      {sub && (
        <Typography sx={{ fontSize: 10, color: DASHBOARD_THEME.textSecondary, mt: 0.25, lineHeight: 1.2 }}>
          {sub}
        </Typography>
      )}
    </Box>
  );

  if (!tooltip) return chip;

  return (
    <Tooltip title={tooltip} arrow placement="top" enterDelay={300}>
      <Box sx={{ height: '100%', minWidth: 0 }}>{chip}</Box>
    </Tooltip>
  );
}
