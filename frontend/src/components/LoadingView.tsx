import { Box, CircularProgress, Typography } from '@mui/material';
import { DASHBOARD_THEME, surfaceCardSx } from '../features/dashboard/utils';

export function LoadingView({ label = '加载中...' }: { label?: string }) {
  return (
    <Box
      sx={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        bgcolor: DASHBOARD_THEME.bgMain,
      }}
    >
      <Box sx={{ ...surfaceCardSx, textAlign: 'center', px: 4, py: 3 }}>
        <CircularProgress sx={{ color: DASHBOARD_THEME.primary }} size={32} />
        <Typography sx={{ mt: 2, color: DASHBOARD_THEME.textSecondary, fontSize: 13 }}>{label}</Typography>
      </Box>
    </Box>
  );
}
