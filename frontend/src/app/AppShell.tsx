import ChevronLeftRoundedIcon from '@mui/icons-material/ChevronLeftRounded';
import ChevronRightRoundedIcon from '@mui/icons-material/ChevronRightRounded';
import { Box, Drawer, IconButton, List, ListItemButton, ListItemIcon, ListItemText, Typography } from '@mui/material';
import { useEffect, useState } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { DiscoverProvider, useDiscover } from '../features/discover/DiscoverProvider';
import { SyncProvider, useSync } from '../features/sync/SyncProvider';
import { DASHBOARD_THEME } from '../features/dashboard/utils';
import { NAV_ITEMS } from './navItems';

const SIDEBAR_EXPANDED = 200;
const SIDEBAR_COLLAPSED = 56;
const STORAGE_KEY = 'xueqiu.sidebarCollapsed';

function NavList({ collapsed }: { collapsed: boolean }) {
  const { running: syncRunning } = useSync();
  const { running: discoverRunning } = useDiscover();

  return (
    <List sx={{ px: 1, py: 1.5 }}>
      {NAV_ITEMS.map((item) => {
        const showDot =
          (item.path === '/sync' && syncRunning) || (item.path === '/discover' && discoverRunning);
        return (
          <ListItemButton
            key={item.path}
            component={NavLink}
            to={item.path}
            end={item.path === '/overview'}
            title={item.label}
            sx={{
              borderRadius: `${DASHBOARD_THEME.radiusMd}px`,
              mb: 0.5,
              minHeight: 40,
              justifyContent: collapsed ? 'center' : 'flex-start',
              px: collapsed ? 1 : 1.25,
              color: DASHBOARD_THEME.textSecondary,
              '&:hover': { bgcolor: DASHBOARD_THEME.navActive },
              '&.active': {
                bgcolor: DASHBOARD_THEME.navActive,
                '& .MuiListItemIcon-root': { color: DASHBOARD_THEME.textPrimary },
                '& .MuiListItemText-primary': { color: DASHBOARD_THEME.textPrimary, fontWeight: 600 },
              },
            }}
          >
            <ListItemIcon
              sx={{
                minWidth: collapsed ? 0 : 32,
                justifyContent: 'center',
                color: 'inherit',
              }}
            >
              <item.icon fontSize="small" />
            </ListItemIcon>
            {!collapsed && (
              <ListItemText
                primary={
                  <Box component="span" sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                    {item.label}
                    {showDot && (
                      <Box
                        component="span"
                        sx={{
                          width: 6,
                          height: 6,
                          borderRadius: '50%',
                          bgcolor: DASHBOARD_THEME.primary,
                          animation: 'pulse 1.2s ease-in-out infinite',
                          '@keyframes pulse': { '0%,100%': { opacity: 1 }, '50%': { opacity: 0.35 } },
                        }}
                      />
                    )}
                  </Box>
                }
                primaryTypographyProps={{ fontSize: 13, fontWeight: 500 }}
              />
            )}
          </ListItemButton>
        );
      })}
    </List>
  );
}

export function AppShell() {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) === '1';
    } catch {
      return false;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, collapsed ? '1' : '0');
    } catch {
      /* ignore */
    }
  }, [collapsed]);

  const width = collapsed ? SIDEBAR_COLLAPSED : SIDEBAR_EXPANDED;

  return (
    <SyncProvider>
      <DiscoverProvider>
        <AppShellLayout collapsed={collapsed} onToggleCollapsed={() => setCollapsed((v) => !v)} width={width} />
      </DiscoverProvider>
    </SyncProvider>
  );
}

function AppShellLayout({
  collapsed,
  onToggleCollapsed,
  width,
}: {
  collapsed: boolean;
  onToggleCollapsed: () => void;
  width: number;
}) {
  return (
    <Box sx={{ display: 'flex', height: '100vh', minHeight: 0, overflow: 'hidden', bgcolor: DASHBOARD_THEME.bgMain }}>
      <Drawer
        variant="permanent"
        sx={{
          width,
          flexShrink: 0,
          '& .MuiDrawer-paper': {
            width,
            boxSizing: 'border-box',
            borderRight: `1px solid ${DASHBOARD_THEME.borderSubtle}`,
            bgcolor: DASHBOARD_THEME.surface,
            overflowX: 'hidden',
            transition: 'width 0.2s ease',
            boxShadow: 'none',
          },
        }}
      >
        <Box
          sx={{
            height: 52,
            px: 1,
            display: 'flex',
            alignItems: 'center',
            justifyContent: collapsed ? 'center' : 'space-between',
            borderBottom: `1px solid ${DASHBOARD_THEME.borderSubtle}`,
          }}
        >
          {!collapsed && (
            <Typography
              sx={{
                fontSize: 15,
                fontWeight: 600,
                color: DASHBOARD_THEME.textPrimary,
                pl: 1,
                letterSpacing: '-0.02em',
              }}
            >
              雪球分析
            </Typography>
          )}
          <IconButton size="small" onClick={onToggleCollapsed} aria-label={collapsed ? '展开侧边栏' : '折叠侧边栏'}>
            {collapsed ? <ChevronRightRoundedIcon fontSize="small" /> : <ChevronLeftRoundedIcon fontSize="small" />}
          </IconButton>
        </Box>

        <NavList collapsed={collapsed} />
      </Drawer>

      <Box
        component="main"
        sx={{
          flex: 1,
          minWidth: 0,
          minHeight: 0,
          display: 'flex',
          flexDirection: 'column',
          bgcolor: DASHBOARD_THEME.bgMain,
          overflow: 'hidden',
        }}
      >
        <Outlet />
      </Box>
    </Box>
  );
}
