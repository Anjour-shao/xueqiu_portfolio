import AddRoundedIcon from '@mui/icons-material/AddRounded';
import ArrowBackRoundedIcon from '@mui/icons-material/ArrowBackRounded';
import DeleteOutlineRoundedIcon from '@mui/icons-material/DeleteOutlineRounded';
import TrendingUpRoundedIcon from '@mui/icons-material/TrendingUpRounded';
import { Box, Button, CircularProgress, Typography } from '@mui/material';
import axios from 'axios';
import { useMemo, useState } from 'react';
import { Navigate, useNavigate, useParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { deleteAccount, fetchAccounts, fetchDashboard, syncLatestHfq } from '../api/dashboard';
import { ImportLogsDialog } from '../components/ImportLogsDialog';
import { LoadingView } from '../components/LoadingView';
import {
  BACKTEST_ACCOUNT_ID,
  getBacktestDashboard,
  isBacktestAccount,
} from '../features/backtest/backtestSession';
import { PortfolioDetailView } from '../features/dashboard/PortfolioDetailView';
import { PortfolioPageHeader } from '../components/PageHeader';
import { useToast } from '../features/notify/ToastProvider';
import { DASHBOARD_THEME } from '../features/dashboard/utils';

export function HoldingsPage() {
  const { accountCode } = useParams<{ accountCode: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [importOpen, setImportOpen] = useState(false);
  const [refreshingQuotes, setRefreshingQuotes] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const activeAccount = accountCode?.trim() ?? '';
  const isBacktest = isBacktestAccount(activeAccount);

  const accountsQuery = useQuery({
    queryKey: ['accounts'],
    queryFn: fetchAccounts,
    enabled: !isBacktest,
  });

  const dashboardQuery = useQuery({
    queryKey: ['dashboard', activeAccount],
    queryFn: () => fetchDashboard(activeAccount),
    enabled: Boolean(activeAccount) && !isBacktest,
  });

  const backtestDashboard = useMemo(
    () => (isBacktest ? getBacktestDashboard() : null),
    [isBacktest, activeAccount],
  );

  const dashboard = isBacktest ? backtestDashboard : dashboardQuery.data;

  const livePositions = useMemo(
    () => (dashboard?.positions ?? []).filter((item) => item.current_weight > 0),
    [dashboard?.positions],
  );

  const handleImported = async (_result: { inserted_count: number }, importedAccountId: string) => {
    setImportOpen(false);
    showToast(`组合 ${importedAccountId} 已添加并同步完成。`, 'success');
    await queryClient.invalidateQueries({ queryKey: ['accounts'] });
    await queryClient.invalidateQueries({ queryKey: ['dashboard'] });
    await queryClient.invalidateQueries({ queryKey: ['portfolios-overview'] });
  };

  const handleRefreshQuotes = async () => {
    if (!activeAccount || refreshingQuotes) return;
    setRefreshingQuotes(true);
    try {
      const result = await syncLatestHfq(activeAccount);
      showToast(result.message, result.synced_count > 0 ? 'success' : 'info');
      await queryClient.invalidateQueries({ queryKey: ['dashboard', activeAccount] });
    } catch (err) {
      let text = '刷新最新价失败，请稍后重试。';
      if (axios.isAxiosError(err)) {
        const detail = err.response?.data?.detail;
        text = typeof detail === 'string' ? detail : err.message;
      } else if (err instanceof Error) {
        text = err.message;
      }
      showToast(text, 'error');
    } finally {
      setRefreshingQuotes(false);
    }
  };

  const handleDeleteAccount = async () => {
    if (!activeAccount || deleting) return;
    const label = dashboard?.account ?? activeAccount;
    if (!window.confirm(`确定删除组合「${label}」？将同时删除库内全部调仓与净值数据，且不可恢复。`)) return;
    setDeleting(true);
    try {
      const result = await deleteAccount(activeAccount);
      showToast(result.message, 'success');
      await queryClient.invalidateQueries({ queryKey: ['accounts'] });
      await queryClient.invalidateQueries({ queryKey: ['portfolios-overview'] });
      await queryClient.invalidateQueries({ queryKey: ['portfolios-overview-stats'] });
      await queryClient.invalidateQueries({ queryKey: ['dashboard'] });
      navigate('/overview', { replace: true });
    } catch (err) {
      let text = '删除失败，请稍后重试。';
      if (axios.isAxiosError(err)) {
        const detail = err.response?.data?.detail;
        text = typeof detail === 'string' ? detail : err.message;
      } else if (err instanceof Error) {
        text = err.message;
      }
      showToast(text, 'error');
    } finally {
      setDeleting(false);
    }
  };

  if (!activeAccount) {
    return <Navigate to="/overview" replace />;
  }

  if (isBacktest && !backtestDashboard) {
    return <Navigate to="/backtest" replace />;
  }

  if (!isBacktest && accountsQuery.isLoading) {
    return <LoadingView label="正在加载账户..." />;
  }

  const headerTitle = isBacktest ? (dashboard?.account ?? '抄作业模拟') : (dashboard?.account ?? activeAccount);
  const headerCode = isBacktest ? undefined : activeAccount;

  return (
    <>
      <ImportLogsDialog open={importOpen} onClose={() => setImportOpen(false)} onImported={handleImported} />

      <Box sx={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <PortfolioPageHeader
          title={headerTitle}
          code={headerCode}
          badge={isBacktest ? '回测' : undefined}
          actions={
            isBacktest ? (
              <Button
                variant="outlined"
                size="small"
                startIcon={<ArrowBackRoundedIcon />}
                onClick={() => navigate('/backtest')}
              >
                返回回测
              </Button>
            ) : (
              <>
                <Button
                  variant="outlined"
                  size="small"
                  color="error"
                  disabled={!activeAccount || deleting}
                  startIcon={deleting ? <CircularProgress size={14} color="inherit" /> : <DeleteOutlineRoundedIcon />}
                  onClick={handleDeleteAccount}
                >
                  {deleting ? '删除中…' : '删除组合'}
                </Button>
                <Button
                  variant="outlined"
                  size="small"
                  disabled={!activeAccount || refreshingQuotes || livePositions.length === 0}
                  startIcon={
                    refreshingQuotes ? <CircularProgress size={14} color="inherit" /> : <TrendingUpRoundedIcon />
                  }
                  onClick={handleRefreshQuotes}
                >
                  {refreshingQuotes ? '刷新中…' : '刷新最新价'}
                </Button>
                <Button variant="contained" size="small" startIcon={<AddRoundedIcon />} onClick={() => setImportOpen(true)}>
                  添加组合
                </Button>
              </>
            )
          }
        />

        <Box
          component="main"
          sx={{
            flex: 1,
            minHeight: 0,
            display: 'flex',
            flexDirection: 'column',
            gap: 1.5,
            px: { xs: 1.5, md: 2.5 },
            py: 1.5,
            overflow: 'hidden',
          }}
        >
          {!isBacktest && dashboardQuery.isLoading && (
            <Box sx={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <CircularProgress sx={{ color: DASHBOARD_THEME.primary }} />
            </Box>
          )}

          {!isBacktest && dashboardQuery.isError && (
            <Box sx={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Typography sx={{ color: DASHBOARD_THEME.down, fontSize: 14 }}>加载失败或账户不存在</Typography>
            </Box>
          )}

          {dashboard && (
            <Box sx={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
              <PortfolioDetailView dashboard={dashboard} showBrushControls={isBacktest} />
            </Box>
          )}
        </Box>
      </Box>
    </>
  );
}
