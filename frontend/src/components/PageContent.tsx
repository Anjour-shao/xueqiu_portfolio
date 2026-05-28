import { Box } from '@mui/material';
import { ReactNode } from 'react';
import { DASHBOARD_THEME } from '../features/dashboard/utils';

export function PageContent({ children }: { children: ReactNode }) {
  return (
    <Box
      sx={{
        flex: 1,
        minHeight: 0,
        overflow: 'auto',
        px: DASHBOARD_THEME.pagePaddingX,
        py: DASHBOARD_THEME.pagePaddingY,
      }}
    >
      {children}
    </Box>
  );
}
