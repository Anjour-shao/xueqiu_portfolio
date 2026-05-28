import AssessmentRoundedIcon from '@mui/icons-material/AssessmentRounded';
import OpenInNewRoundedIcon from '@mui/icons-material/OpenInNewRounded';
import PlayArrowRoundedIcon from '@mui/icons-material/PlayArrowRounded';
import ReplayRoundedIcon from '@mui/icons-material/ReplayRounded';
import {
  Box,
  Button,
  CircularProgress,
  FormControlLabel,
  Stack,
  Switch,
  TextField,
  Typography,
} from '@mui/material';
import axios from 'axios';
import { ReactNode, useCallback, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { runCopyBacktest } from '../api/dashboard';
import { PageContent } from '../components/PageContent';
import { PageHeader } from '../components/PageHeader';
import { SectionCard } from '../components/SectionCard';
import { BACKTEST_ACCOUNT_ID, setBacktestDashboard } from '../features/backtest/backtestSession';
import { copyBacktestToDashboard } from '../features/backtest/backtestAdapter';
import { useToast } from '../features/notify/ToastProvider';
import { CopyBacktestRequest } from '../types';
import { DASHBOARD_THEME } from '../features/dashboard/utils';

const DEFAULT_BACKTEST_CONFIG: CopyBacktestRequest = {
  initial_capital: 100_000,
  max_stock_pct: 20,
  star_unlock_profit: 500_000,
  lot_size: 100,
  min_new_position_pct: 1,
  allow_star_market: false,
};

function HeroState({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
}) {
  return (
    <SectionCard>
      <Box sx={{ py: 5, textAlign: 'center' }}>
        <Typography sx={{ fontSize: 20, fontWeight: 600, color: DASHBOARD_THEME.textPrimary, letterSpacing: '-0.02em', mb: 1 }}>
          {title}
        </Typography>
        {subtitle && (
          <Typography sx={{ fontSize: 14, color: DASHBOARD_THEME.textSecondary, mb: 3, maxWidth: 420, mx: 'auto' }}>
            {subtitle}
          </Typography>
        )}
        {action}
      </Box>
    </SectionCard>
  );
}

export function BacktestPage() {
  const navigate = useNavigate();
  const { showToast } = useToast();
  const [phase, setPhase] = useState<'idle' | 'running' | 'done' | 'error'>('idle');
  const [hasResult, setHasResult] = useState(false);
  const [config, setConfig] = useState<CopyBacktestRequest>(DEFAULT_BACKTEST_CONFIG);

  const run = useCallback(async () => {
    setPhase('running');
    setHasResult(false);
    try {
      const data = await runCopyBacktest(config);
      const dashboard = copyBacktestToDashboard(data);
      setBacktestDashboard(dashboard);
      setHasResult(true);
      setPhase('done');
      showToast('回测完成，可查看结果', 'success');
    } catch (err) {
      let text = '抄作业回测失败，请稍后重试。';
      if (axios.isAxiosError(err)) {
        const detail = err.response?.data?.detail;
        text = typeof detail === 'string' ? detail : err.message;
      } else if (err instanceof Error) {
        text = err.message;
      }
      showToast(text, 'error');
      setPhase('error');
    }
  }, [config, showToast]);

  const viewResult = () => {
    navigate(`/portfolio/${BACKTEST_ACCOUNT_ID}`);
  };

  return (
    <Box sx={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <PageHeader
        title="抄作业回测"
        icon={<AssessmentRoundedIcon />}
        meta={
          <Typography component="span" sx={{ fontSize: 12, color: DASHBOARD_THEME.textSecondary }}>
            合并全部 ZH 调仓信号 · 成交价未复权 · 累计收益按后复权盯市
          </Typography>
        }
        actions={
          <>
            {phase === 'error' && (
              <Button size="small" variant="outlined" startIcon={<ReplayRoundedIcon />} onClick={run}>
                重试
              </Button>
            )}
            {hasResult && phase === 'done' && (
              <Button size="small" variant="outlined" startIcon={<OpenInNewRoundedIcon />} onClick={viewResult}>
                查看回测结果
              </Button>
            )}
            <Button
              size="small"
              variant="contained"
              startIcon={phase === 'running' ? <CircularProgress size={14} color="inherit" /> : <PlayArrowRoundedIcon />}
              disabled={phase === 'running'}
              onClick={run}
            >
              {phase === 'running' ? '计算中…' : phase === 'done' ? '重新回测' : '开始回测'}
            </Button>
          </>
        }
      />

      <PageContent>
        <Stack spacing={3}>
          <SectionCard title="回测参数">
            <Stack direction={{ xs: 'column', md: 'row' }} spacing={1.5} flexWrap="wrap" useFlexGap>
              <TextField
                size="small"
                label="初始资金"
                type="number"
                value={config.initial_capital}
                onChange={(e) => setConfig((c) => ({ ...c, initial_capital: Number(e.target.value) || 0 }))}
                sx={{ width: { xs: '100%', sm: 140 } }}
              />
              <TextField
                size="small"
                label="单票上限 %"
                type="number"
                value={config.max_stock_pct}
                onChange={(e) => setConfig((c) => ({ ...c, max_stock_pct: Number(e.target.value) || 0 }))}
                sx={{ width: { xs: '100%', sm: 120 } }}
              />
              <TextField
                size="small"
                label="整手股数"
                type="number"
                value={config.lot_size}
                onChange={(e) => setConfig((c) => ({ ...c, lot_size: Number(e.target.value) || 100 }))}
                sx={{ width: { xs: '100%', sm: 110 } }}
              />
              <TextField
                size="small"
                label="最小建仓 %"
                type="number"
                value={config.min_new_position_pct}
                onChange={(e) => setConfig((c) => ({ ...c, min_new_position_pct: Number(e.target.value) || 0 }))}
                sx={{ width: { xs: '100%', sm: 120 } }}
              />
              <TextField
                size="small"
                label="科创解锁盈利"
                type="number"
                value={config.star_unlock_profit}
                onChange={(e) => setConfig((c) => ({ ...c, star_unlock_profit: Number(e.target.value) || 0 }))}
                sx={{ width: { xs: '100%', sm: 150 } }}
              />
              <FormControlLabel
                control={
                  <Switch
                    checked={config.allow_star_market}
                    onChange={(e) => setConfig((c) => ({ ...c, allow_star_market: e.target.checked }))}
                    size="small"
                  />
                }
                label={<Typography sx={{ fontSize: 13 }}>允许科创板(688)</Typography>}
              />
            </Stack>
          </SectionCard>

          {phase === 'idle' && (
            <HeroState title="准备就绪" subtitle="点击「开始回测」运行全部 ZH 组合的抄作业模拟" />
          )}

          {phase === 'running' && (
            <HeroState
              title="正在回放调仓信号"
              subtitle="合并全部组合的历史调仓，按规则模拟成交与盯市…"
              action={<CircularProgress size={32} sx={{ color: DASHBOARD_THEME.primary }} />}
            />
          )}

          {phase === 'done' && hasResult && (
            <HeroState
              title="回测已完成"
              subtitle="可在组合详情页查看净值、盈亏日历与持仓交易明细。"
              action={
                <Button variant="contained" startIcon={<OpenInNewRoundedIcon />} onClick={viewResult}>
                  查看回测结果
                </Button>
              }
            />
          )}

          {phase === 'error' && (
            <HeroState title="回测失败" subtitle="请检查后端服务与数据同步状态后重试。" />
          )}
        </Stack>
      </PageContent>
    </Box>
  );
}
