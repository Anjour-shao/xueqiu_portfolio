import { Box, Typography } from '@mui/material';
import { ReactNode } from 'react';
import { DASHBOARD_THEME, surfaceCardSx } from '../features/dashboard/utils';

type Props = {
  title?: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
  noPadding?: boolean;
  sx?: Record<string, unknown>;
};

export function SectionCard({ title, subtitle, action, children, noPadding, sx }: Props) {
  return (
    <Box
      sx={{
        ...surfaceCardSx,
        p: noPadding ? 0 : 2,
        overflow: 'hidden',
        ...sx,
      }}
    >
      {(title || action) && (
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 1,
            mb: subtitle || children ? 1.5 : 0,
            px: noPadding ? 2 : 0,
            pt: noPadding ? 2 : 0,
          }}
        >
          <Box>
            {title && (
              <Typography sx={{ fontSize: 15, fontWeight: 600, color: DASHBOARD_THEME.textPrimary, letterSpacing: '-0.01em' }}>
                {title}
              </Typography>
            )}
            {subtitle && (
              <Typography sx={{ fontSize: 12, color: DASHBOARD_THEME.textSecondary, mt: 0.25 }}>{subtitle}</Typography>
            )}
          </Box>
          {action}
        </Box>
      )}
      {children}
    </Box>
  );
}
