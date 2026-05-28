import SyncRoundedIcon from '@mui/icons-material/SyncRounded';
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  Typography,
} from '@mui/material';
import ExpandMoreRoundedIcon from '@mui/icons-material/ExpandMoreRounded';
import { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import { syncAllFromXueqiu } from '../api/dashboard';
import { actionLabel, DASHBOARD_THEME, fmtDateOnly, surfaceCardSx, monoSx } from '../features/dashboard/utils';
import { SyncXueqiuAllResponse, SyncXueqiuResponse } from '../types';

type Props = {
  open: boolean;
  onClose: () => void;
  onDone: (result: SyncXueqiuAllResponse) => void;
};

const LOG_COLOR: Record<string, string> = {
  info: DASHBOARD_THEME.textSecondary,
  success: DASHBOARD_THEME.down,
  warn: '#B45309',
  error: DASHBOARD_THEME.up,
};

const STATUS_CHIP: Record<string, { label: string; color: 'success' | 'default' | 'warning' }> = {
  inserted: { label: '新增', color: 'success' },
  skipped: { label: '已存在', color: 'default' },
  failed: { label: '失败', color: 'warning' },
};

function LogLines({ logs }: { logs: SyncXueqiuResponse['logs'] }) {
  return (
    <Stack spacing={0.6}>
      {logs.map((line, idx) => (
        <Typography
          key={`${idx}-${line.message}`}
          sx={{
            fontSize: 12,
            lineHeight: 1.5,
            color: LOG_COLOR[line.level] ?? DASHBOARD_THEME.textPrimary,
            pl: 1,
            borderLeft: `2px solid ${LOG_COLOR[line.level] ?? DASHBOARD_THEME.textMuted}`,
          }}
        >
          {line.message}
        </Typography>
      ))}
    </Stack>
  );
}

function TradeTable({ rows }: { rows: SyncXueqiuResponse['trade_results'] }) {
  if (!rows.length) {
    return (
      <Typography sx={{ fontSize: 12, color: DASHBOARD_THEME.textMuted, py: 1 }}>本批无明细</Typography>
    );
  }
  return (
    <Box>
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: '1fr 100px 72px 72px',
          gap: 1,
          py: 0.75,
          fontSize: 10,
          fontWeight: 700,
          color: DASHBOARD_THEME.textMuted,
        }}
      >
        <Box>股票</Box>
        <Box>仓位</Box>
        <Box>动作</Box>
        <Box>状态</Box>
      </Box>
      {rows.map((row) => {
        const chip = STATUS_CHIP[row.status] ?? { label: row.status, color: 'default' as const };
        return (
          <Box
            key={`${row.ts_code}-${row.from_weight}-${row.to_weight}-${row.status}`}
            sx={{
              display: 'grid',
              gridTemplateColumns: '1fr 100px 72px 72px',
              gap: 1,
              py: 0.85,
              fontSize: 12,
              borderTop: '1px solid rgba(0,0,0,0.04)',
            }}
          >
            <Box>{row.stock_name}</Box>
            <Box sx={monoSx}>
              {row.from_weight.toFixed(0)}→{row.to_weight.toFixed(0)}%
            </Box>
            <Box>{actionLabel(row.action)}</Box>
            <Box>
              <Chip label={chip.label} size="small" color={chip.color} variant="outlined" />
            </Box>
          </Box>
        );
      })}
    </Box>
  );
}

export function SyncXueqiuDialog({ open, onClose, onDone }: Props) {
  const [phase, setPhase] = useState<'idle' | 'running' | 'done' | 'error'>('idle');
  const [result, setResult] = useState<SyncXueqiuAllResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const runSync = useCallback(async () => {
    setPhase('running');
    setResult(null);
    setError(null);
    try {
      const data = await syncAllFromXueqiu();
      setResult(data);
      setPhase('done');
      onDone(data);
    } catch (err) {
      let text = '雪球全量同步失败，请稍后重试。';
      if (axios.isAxiosError(err)) {
        const detail = err.response?.data?.detail;
        text = typeof detail === 'string' ? detail : err.message;
      } else if (err instanceof Error) {
        text = err.message;
      }
      setError(text);
      setPhase('error');
    }
  }, [onDone]);

  useEffect(() => {
    if (!open) {
      setPhase('idle');
      setResult(null);
      setError(null);
      return;
    }
    runSync();
  }, [open, runSync]);

  const handleClose = () => {
    if (phase === 'running') return;
    onClose();
  };

  const summarySeverity =
    phase === 'error' ? 'error' : result && result.total_inserted > 0 ? 'success' : 'info';

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth="md"
      fullWidth
      PaperProps={{
        sx: {
          bgcolor: 'rgba(255, 255, 255, 0.88)',
          backdropFilter: 'blur(16px)',
          border: DASHBOARD_THEME.cardBorder,
          boxShadow: '0 8px 40px rgba(0, 0, 0, 0.08)',
          borderRadius: 3,
        },
      }}
    >
      <DialogTitle
        sx={{
          fontWeight: 700,
          color: DASHBOARD_THEME.textPrimary,
          display: 'flex',
          alignItems: 'center',
          gap: 1,
        }}
      >
        <SyncRoundedIcon sx={{ color: DASHBOARD_THEME.primary, fontSize: 22 }} />
        雪球全量更新
      </DialogTitle>
      <DialogContent sx={{ bgcolor: 'transparent' }}>
        <Stack spacing={2} sx={{ pt: 0.5 }}>
          <Alert severity="info" sx={{ borderRadius: 2 }}>
            将更新数据库中<strong>全部 ZH 组合</strong>（非仅当前选中账户）。每组合抓取雪球页「最新调仓」一批，含
            <strong>买入/卖出/加减仓</strong>，与库内全量去重后增量写入。
          </Alert>

          {phase === 'running' && (
            <Box
              sx={{
                ...surfaceCardSx,
                py: 4,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                gap: 1.5,
              }}
            >
              <CircularProgress size={32} sx={{ color: DASHBOARD_THEME.primary }} />
              <Typography sx={{ fontSize: 13, color: DASHBOARD_THEME.textSecondary }}>
                正在依次抓取各组合（请勿关闭窗口）…
              </Typography>
            </Box>
          )}

          {phase === 'error' && error && (
            <Alert severity="error" sx={{ borderRadius: 2 }}>
              {error}
            </Alert>
          )}

          {phase === 'done' && result && (
            <>
              <Alert severity={summarySeverity} sx={{ borderRadius: 2 }}>
                {result.message}
              </Alert>

              <Box sx={{ ...surfaceCardSx, px: 2, py: 1.5, maxHeight: 200, overflow: 'auto' }}>
                <Typography sx={{ fontSize: 13, fontWeight: 600, color: DASHBOARD_THEME.textPrimary, mb: 1 }}>
                  总日志
                </Typography>
                <LogLines logs={result.logs} />
              </Box>

              <Stack spacing={1}>
                {result.accounts.map((acc) => (
                  <Accordion
                    key={acc.account_id}
                    disableGutters
                    elevation={0}
                    sx={{
                      ...surfaceCardSx,
                      '&:before': { display: 'none' },
                      borderRadius: '12px !important',
                      overflow: 'hidden',
                    }}
                  >
                    <AccordionSummary expandIcon={<ExpandMoreRoundedIcon />}>
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', pr: 1 }}>
                        <Typography sx={{ fontSize: 13, fontWeight: 600 }}>
                          {acc.account_name}（{acc.account_id}）
                        </Typography>
                        {acc.ok === false && (
                          <Chip label="失败" size="small" color="warning" variant="outlined" />
                        )}
                        {acc.inserted_count > 0 && (
                          <Chip label={`+${acc.inserted_count}`} size="small" color="success" variant="outlined" />
                        )}
                        {acc.rebalance_time && (
                          <Typography sx={{ fontSize: 11, color: DASHBOARD_THEME.textMuted }}>
                            {fmtDateOnly(acc.rebalance_time)}
                          </Typography>
                        )}
                      </Box>
                    </AccordionSummary>
                    <AccordionDetails sx={{ pt: 0, px: 2, pb: 2 }}>
                      <Typography sx={{ fontSize: 12, color: DASHBOARD_THEME.textSecondary, mb: 1 }}>
                        {acc.message}
                      </Typography>
                      <LogLines logs={acc.logs} />
                      {acc.trade_results.length > 0 && (
                        <Box sx={{ mt: 1.5 }}>
                          <TradeTable rows={acc.trade_results} />
                        </Box>
                      )}
                    </AccordionDetails>
                  </Accordion>
                ))}
              </Stack>
            </>
          )}
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        {phase === 'error' && (
          <Button startIcon={<SyncRoundedIcon />} onClick={runSync} sx={{ color: DASHBOARD_THEME.primary }}>
            重试
          </Button>
        )}
        <Button onClick={handleClose} disabled={phase === 'running'} variant="contained">
          {phase === 'running' ? '同步中…' : '关闭'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
