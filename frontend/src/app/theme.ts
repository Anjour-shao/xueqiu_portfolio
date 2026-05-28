import { createTheme } from '@mui/material/styles';
import { DASHBOARD_THEME } from '../features/dashboard/utils';

const theme = createTheme({
  palette: {
    mode: 'light',
    primary: { main: DASHBOARD_THEME.primary, dark: DASHBOARD_THEME.primaryHover, light: '#7A96B0' },
    background: { default: DASHBOARD_THEME.bgMain, paper: DASHBOARD_THEME.surface },
    text: { primary: DASHBOARD_THEME.textPrimary, secondary: DASHBOARD_THEME.textSecondary },
    divider: DASHBOARD_THEME.borderSubtle,
    success: { main: DASHBOARD_THEME.down },
    error: { main: DASHBOARD_THEME.up },
  },
  typography: {
    fontFamily: DASHBOARD_THEME.sansFont,
    h6: { fontSize: 17, fontWeight: 700, letterSpacing: '-0.02em', lineHeight: 1.3 },
    body2: { fontSize: 13, lineHeight: 1.5 },
    caption: { fontSize: 11, color: DASHBOARD_THEME.textMuted },
  },
  shape: { borderRadius: DASHBOARD_THEME.radiusMd },
  components: {
    MuiButton: {
      styleOverrides: {
        root: {
          textTransform: 'none',
          fontWeight: 600,
          borderRadius: 20,
          boxShadow: 'none',
          fontSize: 13,
        },
        contained: {
          boxShadow: 'none',
          '&:hover': { boxShadow: 'none' },
        },
        outlined: {
          borderColor: DASHBOARD_THEME.borderSubtle,
          '&:hover': { borderColor: DASHBOARD_THEME.textMuted, bgcolor: DASHBOARD_THEME.insetBg },
        },
        sizeSmall: { borderRadius: 18, fontSize: 12, px: 1.5 },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: {
          bgcolor: DASHBOARD_THEME.insetBg,
          border: `1px solid ${DASHBOARD_THEME.borderSubtle}`,
          fontFamily: DASHBOARD_THEME.monoFont,
          fontSize: 11,
        },
      },
    },
    MuiOutlinedInput: {
      styleOverrides: {
        root: {
          bgcolor: DASHBOARD_THEME.surface,
          borderRadius: `${DASHBOARD_THEME.radiusMd}px`,
          '& fieldset': { borderColor: DASHBOARD_THEME.borderSubtle },
          '&:hover fieldset': { borderColor: DASHBOARD_THEME.textMuted },
          '&.Mui-focused fieldset': { borderColor: DASHBOARD_THEME.primary },
        },
      },
    },
    MuiTab: {
      styleOverrides: {
        root: { textTransform: 'none', fontWeight: 600, fontSize: 13 },
      },
    },
    MuiTableCell: {
      styleOverrides: {
        root: { borderColor: DASHBOARD_THEME.borderSubtle },
        head: { fontWeight: 600, fontSize: 11, color: DASHBOARD_THEME.textMuted },
      },
    },
    MuiMenu: {
      styleOverrides: {
        paper: {
          bgcolor: DASHBOARD_THEME.surface,
          border: `1px solid ${DASHBOARD_THEME.borderSubtle}`,
          boxShadow: DASHBOARD_THEME.shadowMd,
          borderRadius: `${DASHBOARD_THEME.radiusMd}px`,
        },
      },
    },
    MuiDialog: {
      styleOverrides: {
        paper: {
          borderRadius: `${DASHBOARD_THEME.radiusLg}px`,
          boxShadow: DASHBOARD_THEME.shadowMd,
        },
      },
    },
    MuiAlert: {
      styleOverrides: {
        root: {
          border: `1px solid ${DASHBOARD_THEME.borderSubtle}`,
          boxShadow: DASHBOARD_THEME.shadowSm,
          borderRadius: `${DASHBOARD_THEME.radiusMd}px`,
        },
      },
    },
  },
});

export default theme;
