import AssessmentRoundedIcon from '@mui/icons-material/AssessmentRounded';
import CheckCircleRoundedIcon from '@mui/icons-material/CheckCircleRounded';
import PlayArrowRoundedIcon from '@mui/icons-material/PlayArrowRounded';
import ReplayRoundedIcon from '@mui/icons-material/ReplayRounded';
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material';
import axios from 'axios';
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { compareBacktestStrategies, fetchBacktestStrategies, runCopyBacktest } from '../api/dashboard';
import { PageContent } from '../components/PageContent';
import { PageHeader } from '../components/PageHeader';
import { SectionCard } from '../components/SectionCard';
import { BACKTEST_ACCOUNT_ID, setBacktestDashboard } from '../features/backtest/backtestSession';
import { copyBacktestToDashboard } from '../features/backtest/backtestAdapter';
import { useToast } from '../features/notify/ToastProvider';
import { CopyBacktestRequest, StrategyCatalogItem, StrategyCompareSummary } from '../types';
import { DASHBOARD_THEME, surfaceCardSx } from '../features/dashboard/utils';

const STYLE_LABEL: Record<string, string> = {
  legacy: '旧引擎',
  aggressive: '广撒网',
  concentrated: '集中进攻',
  balanced: '动态权重',
  momentum: '头狼',
};

const STYLE_COLOR: Record<string, 'default' | 'primary' | 'secondary' | 'warning' | 'success'> = {
  legacy: 'default',
  aggressive: 'primary',
  concentrated: 'warning',
  balanced: 'success',
  momentum: 'secondary',
};

const DEFAULT_SELECTED = [
  'route_g_conviction_trust',
  'route_f_partition_mimic',
  'route_b_merged_boost',
  'route_e_dual_pool_boost',
];

function fmtPct(v: number | null | undefined) {
  if (v == null || Number.isNaN(v)) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
}

function StrategyCard({
  item,
  selected,
  onToggle,
}: {
  item: StrategyCatalogItem;
  selected: boolean;
  onToggle: () => void;
}) {
  return (
    <Paper
      elevation={0}
      onClick={onToggle}
      sx={{
        ...surfaceCardSx,
        p: 1.5,
        cursor: 'pointer',
        border: '2px solid',
        borderColor: selected ? DASHBOARD_THEME.primary : 'transparent',
        bgcolor: selected ? `${DASHBOARD_THEME.primary}08` : undefined,
        transition: 'border-color 0.15s, background-color 0.15s',
        '&:hover': { borderColor: selected ? DASHBOARD_THEME.primary : DASHBOARD_THEME.borderSubtle },
      }}
    >
      <Stack direction="row" spacing={1} alignItems="flex-start">
        <CheckCircleRoundedIcon
          sx={{
            fontSize: 18,
            mt: 0.25,
            color: selected ? DASHBOARD_THEME.primary : DASHBOARD_THEME.textMuted,
            opacity: selected ? 1 : 0.35,
          }}
        />
        <Box sx={{ minWidth: 0, flex: 1 }}>
          <Stack direction="row" spacing={0.75} alignItems="center" flexWrap="wrap" useFlexGap>
            <Typography sx={{ fontSize: 13, fontWeight: 600, lineHeight: 1.3 }}>{item.label}</Typography>
            {item.style && (
              <Chip
                size="small"
                label={STYLE_LABEL[item.style] ?? item.style}
                color={STYLE_COLOR[item.style] ?? 'default'}
                sx={{ height: 18, fontSize: 10 }}
              />
            )}
          </Stack>
          <Typography sx={{ fontSize: 11, color: DASHBOARD_THEME.textMuted, mt: 0.5, lineHeight: 1.4 }}>
            {item.description}
          </Typography>
        </Box>
      </Stack>
    </Paper>
  );
}

function CompareTable({
  rows,
  entryDate,
  onViewDetail,
  detailLoadingId,
}: {
  rows: StrategyCompareSummary[];
  entryDate: string | null;
  onViewDetail: (id: string) => void;
  detailLoadingId: string | null;
}) {
  if (!rows.length) return null;
  const bestId = rows[0]?.strategy_id;
  const returnLabel = entryDate ? `自 ${entryDate} 收益` : '全历史收益';
  const subtitle = entryDate
    ? `自 ${entryDate} 空仓跟单 · 按收益排序`
    : '全历史空仓跟单 · 按收益排序';
  return (
    <SectionCard title="对比结果" subtitle={subtitle} noPadding>
      <Box sx={{ overflowX: 'auto' }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>策略</TableCell>
              <TableCell align="right">{returnLabel}</TableCell>
              <TableCell align="right">持仓数</TableCell>
              <TableCell align="right">最大回撤</TableCell>
              <TableCell align="right">现金%</TableCell>
              <TableCell align="center">操作</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((row) => (
              <TableRow
                key={row.strategy_id}
                hover
                sx={row.strategy_id === bestId ? { bgcolor: `${DASHBOARD_THEME.primary}06` } : undefined}
              >
                <TableCell>
                  <Stack spacing={0.25}>
                    <Stack direction="row" spacing={1} alignItems="center">
                      <Typography sx={{ fontSize: 13, fontWeight: 600 }}>{row.label}</Typography>
                      {row.strategy_id === bestId && (
                        <Chip size="small" label="最优" color="primary" sx={{ height: 20, fontSize: 10 }} />
                      )}
                    </Stack>
                    <Typography sx={{ fontSize: 11, color: DASHBOARD_THEME.textMuted }}>{row.description}</Typography>
                  </Stack>
                </TableCell>
                <TableCell align="right" sx={{ fontWeight: 600, color: DASHBOARD_THEME.primary }}>
                  {fmtPct(entryDate ? row.return_since_entry : row.return_pct)}
                </TableCell>
                <TableCell align="right">{row.position_count ?? '—'}</TableCell>
                <TableCell align="right">
                  {row.max_drawdown_pct != null ? `${row.max_drawdown_pct.toFixed(1)}%` : '—'}
                </TableCell>
                <TableCell align="right">{row.cash_pct != null ? `${row.cash_pct.toFixed(1)}%` : '—'}</TableCell>
                <TableCell align="center">
                  <Button
                    size="small"
                    variant="text"
                    disabled={detailLoadingId === row.strategy_id}
                    onClick={() => onViewDetail(row.strategy_id)}
                  >
                    {detailLoadingId === row.strategy_id ? <CircularProgress size={14} /> : '明细'}
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>
    </SectionCard>
  );
}

function RightPanel({
  phase,
  selectedCount,
  entryDate,
  compareRows,
  onViewDetail,
  detailLoadingId,
}: {
  phase: 'idle' | 'running' | 'done' | 'error';
  selectedCount: number;
  entryDate: string | null;
  compareRows: StrategyCompareSummary[];
  onViewDetail: (id: string) => void;
  detailLoadingId: string | null;
}) {
  if (phase === 'running') {
    return (
      <SectionCard>
        <Stack alignItems="center" justifyContent="center" py={6} spacing={2}>
          <CircularProgress size={36} sx={{ color: DASHBOARD_THEME.primary }} />
          <Typography sx={{ fontSize: 14, color: DASHBOARD_THEME.textSecondary }}>
            正在回放 {selectedCount} 个策略…
          </Typography>
        </Stack>
      </SectionCard>
    );
  }

  if (phase === 'done' && compareRows.length > 0) {
    return (
      <CompareTable
        rows={compareRows}
        entryDate={entryDate}
        onViewDetail={onViewDetail}
        detailLoadingId={detailLoadingId}
      />
    );
  }

  return (
    <SectionCard>
      <Stack spacing={1.5} py={2}>
        <Typography sx={{ fontSize: 14, fontWeight: 600 }}>对比结果将显示在这里</Typography>
        <Typography sx={{ fontSize: 13, color: DASHBOARD_THEME.textSecondary, lineHeight: 1.6 }}>
          左侧选择策略、可选入场日期，点击「运行对比」。明细页仍可在净值图上调整入场点。
        </Typography>
        {phase === 'error' && (
          <Typography sx={{ fontSize: 12, color: DASHBOARD_THEME.up }}>上次对比失败，请重试。</Typography>
        )}
      </Stack>
    </SectionCard>
  );
}

export function BacktestPage() {
  const navigate = useNavigate();
  const { showToast } = useToast();
  const [strategies, setStrategies] = useState<StrategyCatalogItem[]>([]);
  const [selected, setSelected] = useState<string[]>(DEFAULT_SELECTED);
  const [phase, setPhase] = useState<'idle' | 'running' | 'done' | 'error'>('idle');
  const [compareRows, setCompareRows] = useState<StrategyCompareSummary[]>([]);
  const [detailLoadingId, setDetailLoadingId] = useState<string | null>(null);
  const [entryDate, setEntryDate] = useState<string | null>(null);
  const [config, setConfig] = useState<CopyBacktestRequest>({
    initial_capital: 1_000_000,
    max_stock_pct: 20,
    min_new_position_pct: 1,
    max_positions: 10,
    strategy_id: 'route_f_partition_mimic',
  });

  useEffect(() => {
    fetchBacktestStrategies()
      .then(setStrategies)
      .catch(() => showToast('加载策略列表失败', 'error'));
  }, [showToast]);

  const toggle = (id: string) => {
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  const runCompare = useCallback(async () => {
    if (selected.length === 0) {
      showToast('请至少选择一个策略', 'warning');
      return;
    }
    setPhase('running');
    setCompareRows([]);
    try {
      const data = await compareBacktestStrategies(selected, {
        initialCapital: config.initial_capital,
        startDate: entryDate,
      });
      setCompareRows(data.results);
      setPhase('done');
      showToast(`已完成 ${data.results.length} 个策略对比`, 'success');
    } catch (err) {
      let text = '策略对比失败';
      if (axios.isAxiosError(err)) {
        const detail = err.response?.data?.detail;
        text = typeof detail === 'string' ? detail : err.message;
      } else if (err instanceof Error) {
        text = err.message;
      }
      showToast(text, 'error');
      setPhase('error');
    }
  }, [selected, config.initial_capital, entryDate, showToast]);

  const viewDetail = useCallback(
    async (strategyId: string) => {
      setDetailLoadingId(strategyId);
      try {
        const spec = strategies.find((s) => s.id === strategyId);
        const data = await runCopyBacktest({ ...config, strategy_id: strategyId, start_date: entryDate });
        const meta = {
          strategy_id: strategyId,
          strategy_label: spec?.label,
          initial_capital: config.initial_capital,
          entry_date: entryDate,
        };
        setBacktestDashboard(copyBacktestToDashboard(data, meta), meta);
        navigate(`/portfolio/${BACKTEST_ACCOUNT_ID}`);
      } catch (err) {
        let text = '加载明细失败';
        if (axios.isAxiosError(err)) {
          const detail = err.response?.data?.detail;
          text = typeof detail === 'string' ? detail : err.message;
        }
        showToast(text, 'error');
      } finally {
        setDetailLoadingId(null);
      }
    },
    [config, entryDate, navigate, showToast, strategies],
  );

  return (
    <Box sx={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <PageHeader
        title="抄作业回测"
        icon={<AssessmentRoundedIcon />}
        meta={
          <Typography component="span" sx={{ fontSize: 12, color: DASHBOARD_THEME.textSecondary }}>
            合并跟单变体对比 · 可选入场点 · 滑点1%
          </Typography>
        }
        actions={
          <>
            {phase === 'error' && (
              <Button size="small" variant="outlined" startIcon={<ReplayRoundedIcon />} onClick={runCompare}>
                重试
              </Button>
            )}
            <Button
              size="small"
              variant="contained"
              startIcon={phase === 'running' ? <CircularProgress size={14} color="inherit" /> : <PlayArrowRoundedIcon />}
              disabled={phase === 'running' || selected.length === 0}
              onClick={runCompare}
            >
              {phase === 'running' ? '对比中…' : '运行对比'}
            </Button>
          </>
        }
      />

      <PageContent>
        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: { xs: '1fr', md: '5fr 7fr' },
            gap: 2,
            alignItems: 'start',
          }}
        >
          <Stack spacing={2}>
            <SectionCard title="参数">
              <Stack spacing={1.5}>
                <TextField
                  size="small"
                  label="初始资金"
                  type="number"
                  fullWidth
                  value={config.initial_capital}
                  onChange={(e) => setConfig((c) => ({ ...c, initial_capital: Number(e.target.value) || 0 }))}
                />
                <Stack direction="row" spacing={1} alignItems="flex-start">
                  <TextField
                    size="small"
                    label="入场日期"
                    type="date"
                    fullWidth
                    value={entryDate ?? ''}
                    onChange={(e) => setEntryDate(e.target.value || null)}
                    InputLabelProps={{ shrink: true }}
                    helperText={
                      entryDate
                        ? `从 ${entryDate} 起空仓跟单，跳过此前调仓`
                        : '留空表示全历史回测'
                    }
                  />
                  {entryDate && (
                    <Button size="small" variant="outlined" sx={{ mt: 0.5, flexShrink: 0 }} onClick={() => setEntryDate(null)}>
                      全历史
                    </Button>
                  )}
                </Stack>
                <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
                  {(['2020-01-01', '2023-01-01', '2024-01-01'] as const).map((d) => (
                    <Chip
                      key={d}
                      size="small"
                      label={d.slice(0, 4)}
                      variant={entryDate === d ? 'filled' : 'outlined'}
                      color={entryDate === d ? 'primary' : 'default'}
                      onClick={() => setEntryDate(d)}
                      sx={{ cursor: 'pointer' }}
                    />
                  ))}
                </Stack>
              </Stack>
            </SectionCard>

            <SectionCard title="选择策略" subtitle={`已选 ${selected.length} 个`}>
              <Box
                sx={{
                  display: 'grid',
                  gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' },
                  gap: 1,
                }}
              >
                {strategies.map((s) => (
                  <StrategyCard key={s.id} item={s} selected={selected.includes(s.id)} onToggle={() => toggle(s.id)} />
                ))}
              </Box>
            </SectionCard>
          </Stack>

          <RightPanel
            phase={phase}
            selectedCount={selected.length}
            entryDate={entryDate}
            compareRows={compareRows}
            onViewDetail={viewDetail}
            detailLoadingId={detailLoadingId}
          />
        </Box>
      </PageContent>
    </Box>
  );
}
