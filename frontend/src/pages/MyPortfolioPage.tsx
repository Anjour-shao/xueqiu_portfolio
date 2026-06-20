import AccountBalanceWalletRoundedIcon from '@mui/icons-material/AccountBalanceWalletRounded';
import AddRoundedIcon from '@mui/icons-material/AddRounded';
import RefreshRoundedIcon from '@mui/icons-material/RefreshRounded';
import RemoveRoundedIcon from '@mui/icons-material/RemoveRounded';
import {
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tabs,
  TextField,
  Typography,
} from '@mui/material';
import axios from 'axios';
import { useCallback, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { LoadingView } from '../components/LoadingView';
import { MetricGrid } from '../components/MetricGrid';
import { PageContent } from '../components/PageContent';
import { PageHeader } from '../components/PageHeader';
import { SectionCard } from '../components/SectionCard';
import {
  executePersonalTrade,
  fetchBacktestStrategies,
  fetchCopyRebalancePlan,
  fetchPersonalAccount,
  updatePersonalCash,
  updatePersonalStrategy,
} from '../api/personal';
import { StatChip } from '../features/dashboard/StatChip';
import { DASHBOARD_THEME, fmtPct } from '../features/dashboard/utils';
import { useToast } from '../features/notify/ToastProvider';
import type { PersonalHoldingItem } from '../types';

function fmtSignedMoney(v: number | null | undefined) {
  if (v == null) return '—';
  const sign = v > 0 ? '+' : '';
  return `${sign}${v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function pnlColor(v: number | null | undefined) {
  if (v == null || v === 0) return DASHBOARD_THEME.textSecondary;
  return v > 0 ? DASHBOARD_THEME.up : DASHBOARD_THEME.down;
}

export function MyPortfolioPage() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [tab, setTab] = useState(0);
  const [cashOpen, setCashOpen] = useState(false);
  const [cashInput, setCashInput] = useState('');
  const [tradeOpen, setTradeOpen] = useState(false);
  const [tradeAction, setTradeAction] = useState<'买入' | '卖出'>('买入');
  const [tradeCode, setTradeCode] = useState('');
  const [tradeName, setTradeName] = useState('');
  const [tradeShares, setTradeShares] = useState('');
  const [tradePrice, setTradePrice] = useState('');
  const [sellHolding, setSellHolding] = useState<PersonalHoldingItem | null>(null);

  const accountQuery = useQuery({
    queryKey: ['personal-account'],
    queryFn: fetchPersonalAccount,
  });

  const strategiesQuery = useQuery({
    queryKey: ['backtest-strategies'],
    queryFn: fetchBacktestStrategies,
  });

  const planQuery = useQuery({
    queryKey: ['copy-rebalance-plan', accountQuery.data?.strategy_id],
    queryFn: () => fetchCopyRebalancePlan(accountQuery.data?.strategy_id),
    enabled: Boolean(accountQuery.data?.strategy_id),
  });

  const invalidate = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: ['personal-account'] });
    await queryClient.invalidateQueries({ queryKey: ['copy-rebalance-plan'] });
  }, [queryClient]);

  const cashMutation = useMutation({
    mutationFn: updatePersonalCash,
    onSuccess: async () => {
      showToast('现金已更新', 'success');
      setCashOpen(false);
      await invalidate();
    },
    onError: (err) => {
      let text = '更新失败';
      if (axios.isAxiosError(err)) {
        const detail = err.response?.data?.detail;
        text = typeof detail === 'string' ? detail : err.message;
      }
      showToast(text, 'error');
    },
  });

  const strategyMutation = useMutation({
    mutationFn: updatePersonalStrategy,
    onSuccess: async () => {
      showToast('策略已更新', 'success');
      await invalidate();
    },
    onError: (err) => {
      let text = '策略更新失败';
      if (axios.isAxiosError(err)) {
        const detail = err.response?.data?.detail;
        text = typeof detail === 'string' ? detail : err.message;
      }
      showToast(text, 'error');
    },
  });

  const tradeMutation = useMutation({
    mutationFn: executePersonalTrade,
    onSuccess: async () => {
      showToast('交易已记录', 'success');
      setTradeOpen(false);
      setSellHolding(null);
      await invalidate();
    },
    onError: (err) => {
      let text = '交易失败';
      if (axios.isAxiosError(err)) {
        const detail = err.response?.data?.detail;
        text = typeof detail === 'string' ? detail : err.message;
      }
      showToast(text, 'error');
    },
  });

  const account = accountQuery.data;
  const strategyLabel = useMemo(() => {
    const sid = account?.strategy_id;
    if (!sid) return '—';
    const found = strategiesQuery.data?.find((s) => s.id === sid);
    return found?.label ?? sid;
  }, [account?.strategy_id, strategiesQuery.data]);

  const openBuy = () => {
    setTradeAction('买入');
    setTradeCode('');
    setTradeName('');
    setTradeShares('');
    setTradePrice('');
    setSellHolding(null);
    setTradeOpen(true);
  };

  const openSell = (h: PersonalHoldingItem) => {
    setTradeAction('卖出');
    setTradeCode(h.ts_code);
    setTradeName(h.stock_name);
    setTradeShares('');
    setTradePrice(h.price != null ? String(h.price) : '');
    setSellHolding(h);
    setTradeOpen(true);
  };

  const submitTrade = () => {
    const shares = Number(tradeShares);
    const price = tradePrice.trim() ? Number(tradePrice) : undefined;
    if (!tradeCode.trim() || !Number.isFinite(shares) || shares <= 0) {
      showToast('请填写有效代码与股数', 'error');
      return;
    }
    tradeMutation.mutate({
      action: tradeAction,
      ts_code: tradeCode.trim(),
      shares,
      price,
      stock_name: tradeName.trim() || undefined,
    });
  };

  if (accountQuery.isLoading) {
    return <LoadingView label="加载实盘账户…" />;
  }

  if (accountQuery.isError) {
    return (
      <Box sx={{ p: 3 }}>
        <Typography color="error">加载失败，请确认后端已启动并已配置数据库。</Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <PageHeader
        title="我的持仓"
        icon={<AccountBalanceWalletRoundedIcon />}
        meta={
          <Typography component="span" sx={{ fontSize: 12, color: DASHBOARD_THEME.textSecondary }}>
            维护实盘持仓与现金，钉钉调仓方案将据此生成
          </Typography>
        }
        actions={
          <Button
            size="small"
            variant="outlined"
            startIcon={planQuery.isFetching ? <CircularProgress size={14} /> : <RefreshRoundedIcon />}
            onClick={() => {
              invalidate();
              planQuery.refetch();
            }}
          >
            刷新
          </Button>
        }
      />

      <PageContent>
        <Stack spacing={3}>
          <MetricGrid minColWidth={120}>
            <StatChip compact label="总资产" value={account?.total_assets?.toLocaleString('zh-CN', { minimumFractionDigits: 2 }) ?? '—'} />
            <Box
              onClick={() => {
                setCashInput(String(account?.cash ?? 0));
                setCashOpen(true);
              }}
              sx={{ cursor: 'pointer' }}
            >
              <StatChip
                compact
                label="现金"
                value={account?.cash?.toLocaleString('zh-CN', { minimumFractionDigits: 2 }) ?? '—'}
                sub="点击修改"
              />
            </Box>
            <StatChip compact label="市值" value={account?.market_value?.toLocaleString('zh-CN', { minimumFractionDigits: 2 }) ?? '—'} />
            <StatChip
              compact
              label="持有盈亏"
              value={fmtSignedMoney(account?.holding_pnl)}
              color={pnlColor(account?.holding_pnl)}
              sub={account?.holding_pnl_pct != null ? fmtPct(account.holding_pnl_pct) : undefined}
            />
            <StatChip compact label="持仓" value={`${account?.holdings.length ?? 0} 只`} />
            <StatChip compact label="抄作业策略" value={strategyLabel} sub={account?.strategy_id} />
          </MetricGrid>

          <SectionCard
            title="抄作业策略"
            subtitle="仅当关注组合有新调仓推送时，才会在钉钉简报里给出本次信号的跟单参考；不要求追历史、也不强制改动现有持仓"
            action={
              <FormControl size="small" sx={{ minWidth: 220 }}>
                <InputLabel id="strategy-select-label">策略</InputLabel>
                <Select
                  labelId="strategy-select-label"
                  label="策略"
                  value={account?.strategy_id ?? ''}
                  onChange={(e) => strategyMutation.mutate(e.target.value)}
                  disabled={strategyMutation.isPending}
                >
                  {(strategiesQuery.data ?? []).map((s) => (
                    <MenuItem key={s.id} value={s.id}>
                      {s.label}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            }
          >
            <Typography sx={{ fontSize: 12, color: DASHBOARD_THEME.textSecondary }}>
              当前：{strategyLabel}。下次组合调仓推送时，系统将仅针对该批新信号给出参考方案。
            </Typography>
          </SectionCard>

          <SectionCard noPadding>
            <Box sx={{ borderBottom: `1px solid ${DASHBOARD_THEME.borderSubtle}`, px: 2 }}>
              <Tabs value={tab} onChange={(_, v) => setTab(v)}>
                <Tab label={`持仓 ${account?.holdings.length ?? 0}`} />
                <Tab label="调仓方案" />
              </Tabs>
            </Box>

            {tab === 0 && (
              <Box sx={{ px: 2, pb: 2 }}>
                <Stack direction="row" spacing={1} sx={{ py: 1.5 }}>
                  <Button size="small" variant="contained" startIcon={<AddRoundedIcon />} onClick={openBuy}>
                    买入
                  </Button>
                </Stack>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>名称</TableCell>
                      <TableCell align="right">现价</TableCell>
                      <TableCell align="right">成本</TableCell>
                      <TableCell align="right">股数</TableCell>
                      <TableCell align="right">仓位</TableCell>
                      <TableCell align="right">盈亏</TableCell>
                      <TableCell align="right">操作</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {account?.holdings.map((h) => (
                      <TableRow key={h.ts_code} hover>
                        <TableCell>
                          <Typography sx={{ fontSize: 13, fontWeight: 600 }}>{h.stock_name}</Typography>
                          <Typography sx={{ fontSize: 11, color: DASHBOARD_THEME.textMuted }}>{h.ts_code}</Typography>
                        </TableCell>
                        <TableCell align="right">{h.price?.toFixed(2) ?? '—'}</TableCell>
                        <TableCell align="right">{h.cost_price.toFixed(2)}</TableCell>
                        <TableCell align="right">{h.shares}</TableCell>
                        <TableCell align="right">{h.weight_pct != null ? `${h.weight_pct.toFixed(1)}%` : '—'}</TableCell>
                        <TableCell align="right" sx={{ color: pnlColor(h.unrealized_pnl_pct) }}>
                          {h.unrealized_pnl_pct != null ? fmtPct(h.unrealized_pnl_pct) : '—'}
                          {h.unrealized_pnl_amount != null && (
                            <Typography component="span" sx={{ fontSize: 10, display: 'block' }}>
                              {fmtSignedMoney(h.unrealized_pnl_amount)}
                            </Typography>
                          )}
                        </TableCell>
                        <TableCell align="right">
                          <Button size="small" color="success" startIcon={<RemoveRoundedIcon />} onClick={() => openSell(h)}>
                            卖出
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                    {!account?.holdings.length && (
                      <TableRow>
                        <TableCell colSpan={7}>
                          <Typography sx={{ fontSize: 13, color: DASHBOARD_THEME.textMuted, py: 2 }}>
                            暂无持仓，点击「买入」添加。
                          </Typography>
                        </TableCell>
                      </TableRow>
                    )}
                  </TableBody>
                </Table>
              </Box>
            )}

            {tab === 1 && (
              <Box sx={{ px: 2, pb: 2, pt: 1.5 }}>
                <Typography sx={{ fontSize: 13, color: DASHBOARD_THEME.textSecondary, lineHeight: 1.7, mb: 2 }}>
                  {planQuery.data?.note ||
                    '调仓方案仅在关注组合有新调仓并已推送时生成，针对本次新信号给出参考。不要求追历史仓位，也不会因此建议你改动现有持仓。请等待下次组合更新通知。'}
                </Typography>
                {planQuery.data?.actions.length ? (
                  <Table size="small">
                    <TableHead>
                      <TableRow>
                        <TableCell>动作</TableCell>
                        <TableCell>标的</TableCell>
                        <TableCell align="right">股数</TableCell>
                        <TableCell align="right">仓位变化</TableCell>
                        <TableCell align="right">现价</TableCell>
                        <TableCell align="right">约金额</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {planQuery.data.actions.map((a) => (
                        <TableRow key={`${a.action}-${a.ts_code}`} hover>
                          <TableCell sx={{ color: a.action === '买入' ? DASHBOARD_THEME.up : DASHBOARD_THEME.down, fontWeight: 600 }}>
                            {a.action}
                          </TableCell>
                          <TableCell>
                            <Typography sx={{ fontSize: 13, fontWeight: 600 }}>{a.stock_name}</Typography>
                            <Typography sx={{ fontSize: 11, color: DASHBOARD_THEME.textMuted }}>{a.ts_code}</Typography>
                          </TableCell>
                          <TableCell align="right">{a.shares_delta}</TableCell>
                          <TableCell align="right">
                            {a.current_weight_pct.toFixed(1)}% → {a.target_weight_pct.toFixed(1)}%
                            <Typography sx={{ fontSize: 10, color: DASHBOARD_THEME.textMuted }}>
                              {a.current_shares} → {a.target_shares} 股
                            </Typography>
                          </TableCell>
                          <TableCell align="right">{a.price?.toFixed(2) ?? '—'}</TableCell>
                          <TableCell align="right">{a.amount != null ? a.amount.toLocaleString('zh-CN', { minimumFractionDigits: 2 }) : '—'}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                ) : null}
              </Box>
            )}
          </SectionCard>
        </Stack>
      </PageContent>

      <Dialog open={cashOpen} onClose={() => setCashOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>修改现金</DialogTitle>
        <DialogContent>
          <TextField
            fullWidth
            margin="dense"
            label="可用现金（元）"
            type="number"
            value={cashInput}
            onChange={(e) => setCashInput(e.target.value)}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCashOpen(false)}>取消</Button>
          <Button
            variant="contained"
            disabled={cashMutation.isPending}
            onClick={() => {
              const v = Number(cashInput);
              if (!Number.isFinite(v) || v < 0) {
                showToast('请输入有效金额', 'error');
                return;
              }
              cashMutation.mutate(v);
            }}
          >
            保存
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={tradeOpen} onClose={() => setTradeOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>{tradeAction} {sellHolding?.stock_name ?? ''}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            {tradeAction === '买入' && (
              <>
                <TextField label="股票代码" value={tradeCode} onChange={(e) => setTradeCode(e.target.value)} placeholder="如 600519 或 SH600519" />
                <TextField label="名称（可选）" value={tradeName} onChange={(e) => setTradeName(e.target.value)} />
              </>
            )}
            <TextField
              label="股数（整手）"
              type="number"
              value={tradeShares}
              onChange={(e) => setTradeShares(e.target.value)}
              helperText={sellHolding ? `可卖 ${sellHolding.shares} 股` : '主板 100 股/手，科创板 200 股/手'}
            />
            <TextField
              label="成交价（可选，默认现价）"
              type="number"
              value={tradePrice}
              onChange={(e) => setTradePrice(e.target.value)}
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setTradeOpen(false)}>取消</Button>
          <Button variant="contained" disabled={tradeMutation.isPending} onClick={submitTrade}>
            确认{tradeAction}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
