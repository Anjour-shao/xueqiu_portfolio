import ExpandMoreRoundedIcon from '@mui/icons-material/ExpandMoreRounded';
import { Box, Collapse, IconButton, Stack, Typography } from '@mui/material';
import { useEffect, useState } from 'react';
import { DASHBOARD_THEME } from '../features/dashboard/utils';

type LogLine = { level: string; message: string };

const LOG_COLOR: Record<string, string> = {
  info: DASHBOARD_THEME.textSecondary,
  success: DASHBOARD_THEME.down,
  warn: '#B45309',
  error: DASHBOARD_THEME.up,
};

export function LogSection({
  title,
  logs,
  running,
  currentStep,
  emptyHint,
  defaultExpanded,
}: {
  title: string;
  logs: LogLine[];
  running?: boolean;
  currentStep?: string | null;
  emptyHint: string;
  defaultExpanded?: boolean;
}) {
  const hasActivity = running || logs.length > 0;
  const [expanded, setExpanded] = useState(defaultExpanded ?? hasActivity);

  useEffect(() => {
    if (running) setExpanded(true);
  }, [running]);

  return (
    <Box sx={{ borderTop: `1px solid ${DASHBOARD_THEME.borderSubtle}`, pt: 1.5, mt: 1.5 }}>
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        onClick={() => setExpanded((v) => !v)}
        sx={{ cursor: 'pointer', userSelect: 'none', mb: expanded ? 1 : 0 }}
      >
        <Stack direction="row" alignItems="center" spacing={0.5}>
          <Typography sx={{ fontSize: 13, fontWeight: 600, color: DASHBOARD_THEME.textPrimary }}>{title}</Typography>
          {!running && logs.length > 0 && (
            <Typography sx={{ fontSize: 11, color: DASHBOARD_THEME.textMuted }}>({logs.length})</Typography>
          )}
        </Stack>
        <Stack direction="row" alignItems="center" spacing={0.75}>
          {running && currentStep && (
            <Typography sx={{ fontSize: 11, color: DASHBOARD_THEME.textSecondary }}>{currentStep}</Typography>
          )}
          <IconButton
            size="small"
            aria-label={expanded ? '收起' : '展开'}
            sx={{
              transform: expanded ? 'rotate(180deg)' : 'none',
              transition: 'transform 0.2s',
            }}
          >
            <ExpandMoreRoundedIcon fontSize="small" />
          </IconButton>
        </Stack>
      </Stack>

      <Collapse in={expanded}>
        <Box
          sx={{
            maxHeight: 320,
            overflow: 'auto',
            bgcolor: DASHBOARD_THEME.insetBg,
            borderRadius: `${DASHBOARD_THEME.radiusMd}px`,
            p: 1.25,
          }}
        >
          {!logs.length && !running && (
            <Typography sx={{ fontSize: 13, color: DASHBOARD_THEME.textMuted, textAlign: 'center' }}>{emptyHint}</Typography>
          )}
          <Stack spacing={0.35}>
            {logs.map((line, idx) => (
              <Typography
                key={`${idx}-${line.message.slice(0, 48)}`}
                sx={{
                  fontSize: 12,
                  lineHeight: 1.5,
                  fontFamily: line.message.startsWith('▶') || line.message.startsWith('──') ? undefined : DASHBOARD_THEME.monoFont,
                  color: LOG_COLOR[line.level] ?? DASHBOARD_THEME.textPrimary,
                  pl: line.message.startsWith('  ') ? 1.5 : 0,
                  borderLeft: line.level === 'error' ? `2px solid ${DASHBOARD_THEME.up}` : undefined,
                }}
              >
                {line.message}
              </Typography>
            ))}
          </Stack>
        </Box>
      </Collapse>
    </Box>
  );
}
