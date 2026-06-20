import CloseRoundedIcon from '@mui/icons-material/CloseRounded';
import OpenInNewRoundedIcon from '@mui/icons-material/OpenInNewRounded';
import {
  Box,
  Button,
  Checkbox,
  CircularProgress,
  FormControlLabel,
  IconButton,
  Paper,
  Stack,
  Typography,
} from '@mui/material';
import { useEffect, useState } from 'react';
import { DASHBOARD_THEME, monoSx, pctColor } from '../features/dashboard/utils';
import { portfolioUrl } from '../lib/xueqiuLinks';
import type { MinedCubeItem } from '../types';

const AUTO_NEXT_KEY = 'discover.xueqiuAutoNext';

type Props = {
  cube: MinedCubeItem | null;
  patching?: boolean;
  onDecide: (selected: number) => void;
  onClose: () => void;
  onReopen: () => void;
};

function formatPct(v: number | null | undefined) {
  if (v == null || Number.isNaN(v)) return '—';
  const sign = v > 0 ? '+' : '';
  return `${sign}${v.toFixed(2)}%`;
}

export function openXueqiuPreviewPopup(code: string, existing?: Window | null): Window | null {
  existing?.close();
  const width = Math.min(1200, window.screen.availWidth - 40);
  const height = Math.min(880, window.screen.availHeight - 80);
  const left = Math.round(window.screenX + Math.max(0, (window.outerWidth - width) / 2));
  const top = Math.round(window.screenY + 40);
  return window.open(
    portfolioUrl(code),
    'xueqiu_triage_preview',
    `popup=yes,width=${width},height=${height},left=${left},top=${top},scrollbars=yes,resizable=yes`,
  );
}

export function DiscoverCubeTriageBar({ cube, patching, onDecide, onClose, onReopen }: Props) {
  const [autoNext, setAutoNext] = useState(() => {
    try {
      return localStorage.getItem(AUTO_NEXT_KEY) !== '0';
    } catch {
      return true;
    }
  });

  useEffect(() => {
    if (!cube) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (patching) return;
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      if (e.key === '1') {
        e.preventDefault();
        onDecide(1);
      } else if (e.key === '2') {
        e.preventDefault();
        onDecide(0);
      } else if (e.key === '3') {
        e.preventDefault();
        onDecide(-1);
      } else if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [cube, onClose, onDecide, patching]);

  if (!cube) return null;

  const toggleAutoNext = (checked: boolean) => {
    setAutoNext(checked);
    try {
      localStorage.setItem(AUTO_NEXT_KEY, checked ? '1' : '0');
    } catch {
      /* ignore */
    }
  };

  return (
    <Paper
      elevation={8}
      sx={{
        position: 'fixed',
        left: 16,
        right: 16,
        bottom: 16,
        zIndex: (theme) => theme.zIndex.modal + 1,
        px: 2,
        py: 1.25,
        borderRadius: 2,
        border: DASHBOARD_THEME.cardBorder,
        bgcolor: 'rgba(255, 255, 255, 0.94)',
        backdropFilter: 'blur(14px)',
        boxShadow: '0 8px 32px rgba(0,0,0,0.12)',
      }}
    >
      <Stack direction="row" alignItems="center" justifyContent="space-between" gap={2} flexWrap="wrap">
        <Box sx={{ minWidth: 0, flex: 1 }}>
          <Typography sx={{ fontSize: 14, fontWeight: 700 }} noWrap>
            {cube.account_name}
          </Typography>
          <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap" useFlexGap>
            <Typography sx={{ ...monoSx, fontSize: 11, color: DASHBOARD_THEME.textMuted }}>{cube.account_code}</Typography>
            <Typography sx={{ fontSize: 12, color: pctColor(cube.cum_return_pct ?? 0) }}>
              累计 {formatPct(cube.cum_return_pct)}
            </Typography>
            <Typography sx={{ fontSize: 11, color: DASHBOARD_THEME.textSecondary }}>
              雪球原页已在旁侧窗口打开 · 快捷键 1选中 2待定 3拒绝 Esc关闭
            </Typography>
          </Stack>
        </Box>

        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
          <FormControlLabel
            control={
              <Checkbox
                size="small"
                checked={autoNext}
                onChange={(e) => toggleAutoNext(e.target.checked)}
              />
            }
            label={<Typography sx={{ fontSize: 12 }}>自动下一条</Typography>}
            sx={{ mr: 0 }}
          />
          <Button size="small" variant="outlined" startIcon={<OpenInNewRoundedIcon sx={{ fontSize: 14 }} />} onClick={onReopen}>
            重新打开
          </Button>
          <Button
            size="small"
            variant={cube.selected === 1 ? 'contained' : 'outlined'}
            disabled={patching}
            onClick={() => onDecide(1)}
          >
            选中 (1)
          </Button>
          <Button
            size="small"
            color="inherit"
            variant={cube.selected == null ? 'contained' : 'outlined'}
            disabled={patching}
            onClick={() => onDecide(0)}
          >
            待定 (2)
          </Button>
          <Button
            size="small"
            color="inherit"
            variant={cube.selected === -1 ? 'contained' : 'outlined'}
            disabled={patching}
            onClick={() => onDecide(-1)}
          >
            拒绝 (3)
          </Button>
          {patching && <CircularProgress size={18} />}
          <IconButton size="small" aria-label="关闭预览" onClick={onClose}>
            <CloseRoundedIcon fontSize="small" />
          </IconButton>
        </Stack>
      </Stack>
    </Paper>
  );
}

export function readAutoNextEnabled(): boolean {
  try {
    return localStorage.getItem(AUTO_NEXT_KEY) !== '0';
  } catch {
    return true;
  }
}
