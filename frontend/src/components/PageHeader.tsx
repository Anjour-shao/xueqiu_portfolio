import { Box, Chip, Stack, Typography } from '@mui/material';
import { ReactNode } from 'react';
import { DASHBOARD_THEME, glassHeaderSx } from '../features/dashboard/utils';

type Props = {
  title: string;
  code?: string;
  badge?: string;
  icon?: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
};

export function PageHeader({ title, code, badge, icon, meta, actions }: Props) {
  return (
    <Box
      component="header"
      sx={{
        ...glassHeaderSx,
        flexShrink: 0,
        px: DASHBOARD_THEME.pagePaddingX,
        py: 1.5,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 2,
        flexWrap: { xs: 'wrap', md: 'nowrap' },
      }}
    >
      <Stack direction="row" spacing={1.25} alignItems="center" sx={{ minWidth: 0, flex: 1 }}>
        {icon ? <Box sx={{ color: DASHBOARD_THEME.primary, display: 'flex', flexShrink: 0 }}>{icon}</Box> : null}
        <Stack
          direction="row"
          spacing={1}
          alignItems="baseline"
          sx={{ minWidth: 0, flex: 1 }}
          useFlexGap
          flexWrap="wrap"
        >
          <Typography
            sx={{
              fontSize: 17,
              fontWeight: 700,
              color: DASHBOARD_THEME.textPrimary,
              letterSpacing: '-0.02em',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              minWidth: 0,
            }}
          >
            {title}
          </Typography>
          {code && (
            <Typography
              component="span"
              sx={{
                fontSize: 12,
                fontWeight: 500,
                color: DASHBOARD_THEME.textSecondary,
                fontFamily: DASHBOARD_THEME.monoFont,
                flexShrink: 0,
              }}
            >
              {code}
            </Typography>
          )}
          {badge && (
            <Chip
              label={badge}
              size="small"
              sx={{
                height: 20,
                fontSize: 11,
                fontWeight: 600,
                bgcolor: 'rgba(91, 123, 151, 0.1)',
                color: DASHBOARD_THEME.primary,
                flexShrink: 0,
              }}
            />
          )}
          {meta}
        </Stack>
      </Stack>
      {actions ? (
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" sx={{ justifyContent: { md: 'flex-end' } }}>
          {actions}
        </Stack>
      ) : null}
    </Box>
  );
}

/** @deprecated use PageHeader */
export const PortfolioPageHeader = PageHeader;
