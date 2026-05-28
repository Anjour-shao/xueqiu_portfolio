import AddRoundedIcon from '@mui/icons-material/AddRounded';
import {
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  LinearProgress,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { streamSyncXueqiu } from '../api/dashboard';
import { DASHBOARD_THEME, surfaceCardSx } from '../features/dashboard/utils';
import { useToast } from '../features/notify/ToastProvider';
import { SyncLogItem, SyncXueqiuResponse } from '../types';

type Props = {
  open: boolean;
  onClose: () => void;
  onImported: (result: SyncXueqiuResponse, accountId: string) => void;
};

const LOG_COLOR: Record<string, string> = {
  info: DASHBOARD_THEME.textSecondary,
  success: DASHBOARD_THEME.down,
  warn: '#B45309',
  error: DASHBOARD_THEME.up,
};

function LogPanel({ logs, running }: { logs: SyncLogItem[]; running: boolean }) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs.length, running]);

  return (
    <Box
      sx={{
        ...surfaceCardSx,
        p: 1.25,
        maxHeight: 280,
        minHeight: 120,
        overflow: 'auto',
        bgcolor: DASHBOARD_THEME.insetBg,
      }}
    >
      {!logs.length && !running && (
        <Typography sx={{ fontSize: 12, color: DASHBOARD_THEME.textMuted }}>导入日志将显示在这里</Typography>
      )}
      <Stack spacing={0.35}>
        {logs.map((line, idx) => (
          <Typography
            key={`${idx}-${line.message.slice(0, 48)}`}
            sx={{
              fontSize: 12,
              lineHeight: 1.5,
              fontFamily:
                line.message.startsWith('▶') || line.message.startsWith('──')
                  ? undefined
                  : DASHBOARD_THEME.monoFont,
              color: LOG_COLOR[line.level] ?? DASHBOARD_THEME.textPrimary,
              pl: line.level === 'error' ? 1 : 0,
              borderLeft: line.level === 'error' ? `2px solid ${DASHBOARD_THEME.up}` : undefined,
            }}
          >
            {line.message}
          </Typography>
        ))}
        <div ref={bottomRef} />
      </Stack>
    </Box>
  );
}

export function ImportLogsDialog({ open, onClose, onImported }: Props) {
  const { showToast } = useToast();
  const abortRef = useRef<AbortController | null>(null);
  const [portfolioId, setPortfolioId] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [logs, setLogs] = useState<SyncLogItem[]>([]);

  const normalizedId = useMemo(() => portfolioId.trim().toUpperCase(), [portfolioId]);
  const isValidPortfolioId = /^ZH\d+$/i.test(normalizedId);
  const canSubmit = isValidPortfolioId && !submitting;

  const resetForm = useCallback(() => {
    setPortfolioId('');
    setLogs([]);
  }, []);

  const handleClose = () => {
    if (submitting) {
      abortRef.current?.abort();
      return;
    }
    resetForm();
    onClose();
  };

  const handleImport = async () => {
    if (!canSubmit) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setSubmitting(true);
    setLogs([{ level: 'info', message: `开始导入 ${normalizedId}…` }]);

    try {
      const outcome = await streamSyncXueqiu(
        normalizedId,
        (item) => setLogs((prev) => [...prev, item]),
        controller.signal,
      );

      if (outcome.ok && outcome.result) {
        showToast(outcome.result.message || `组合 ${normalizedId} 添加完成`, 'success');
        onImported(outcome.result, normalizedId);
        resetForm();
        onClose();
        return;
      }

      const failText =
        outcome.message ||
        '导入未完成。若为 HTTP 400，多为雪球限流：调仓历史多的组合会连续翻页请求，请等待 1～2 分钟后重试。';
      setLogs((prev) => [...prev, { level: 'error', message: failText }]);
      showToast(failText, 'error');
    } catch (err) {
      if (controller.signal.aborted) {
        setLogs((prev) => [...prev, { level: 'warn', message: '■ 已取消导入' }]);
        showToast('已取消导入', 'info');
        return;
      }
      let text = '添加失败，请稍后重试。';
      if (err instanceof Error) {
        text = err.message;
      }
      setLogs((prev) => [...prev, { level: 'error', message: text }]);
      showToast(text, 'error');
    } finally {
      setSubmitting(false);
      abortRef.current = null;
    }
  };

  useEffect(() => {
    if (!open) {
      abortRef.current?.abort();
      setSubmitting(false);
    }
  }, [open]);

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth="sm"
      fullWidth
      PaperProps={{
        sx: {
          bgcolor: 'rgba(255, 255, 255, 0.92)',
          backdropFilter: 'blur(16px)',
          border: DASHBOARD_THEME.cardBorder,
          boxShadow: '0 8px 40px rgba(0, 0, 0, 0.08)',
          borderRadius: 3,
        },
      }}
    >
      <DialogTitle sx={{ fontWeight: 700, color: DASHBOARD_THEME.textPrimary, pb: 0.5 }}>添加雪球组合</DialogTitle>
      <DialogContent sx={{ bgcolor: 'transparent' }}>
        <Stack spacing={1.5} sx={{ pt: 0.5 }}>
          <Typography sx={{ fontSize: 12, color: DASHBOARD_THEME.textSecondary, lineHeight: 1.55 }}>
            输入 ZH 组合号后，将分页拉取全部历史调仓并写入数据库。调仓批次多的组合请求更久；若出现 HTTP 400，多为雪球
            限流而非组合不存在，稍后重试即可。
          </Typography>

          <TextField
            label="组合号"
            value={portfolioId}
            onChange={(e) => setPortfolioId(e.target.value)}
            placeholder="例如: ZH3207026"
            fullWidth
            autoFocus
            disabled={submitting}
            helperText={
              portfolioId.trim() && !isValidPortfolioId
                ? '请输入正确的雪球组合号，格式如 ZH3207026'
                : '支持 ZH 开头的雪球组合'
            }
            error={Boolean(portfolioId.trim()) && !isValidPortfolioId}
          />

          {submitting && (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <CircularProgress size={18} sx={{ color: DASHBOARD_THEME.primary }} />
              <Typography sx={{ fontSize: 12, color: DASHBOARD_THEME.textSecondary }}>导入进行中…</Typography>
              <Box sx={{ flex: 1 }}>
                <LinearProgress sx={{ borderRadius: 1, height: 4 }} />
              </Box>
            </Box>
          )}

          <LogPanel logs={logs} running={submitting} />
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button onClick={handleClose} disabled={false}>
          {submitting ? '停止' : '关闭'}
        </Button>
        <Button variant="contained" startIcon={<AddRoundedIcon />} onClick={handleImport} disabled={!canSubmit}>
          {submitting ? '导入中…' : '添加组合'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
