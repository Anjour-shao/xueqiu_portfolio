import CheckRoundedIcon from '@mui/icons-material/CheckRounded';
import { Box, Typography, keyframes } from '@mui/material';
import { DASHBOARD_THEME } from '../dashboard/utils';

export type SyncStepState = 'pending' | 'active' | 'done';

const STEPS = [
  { id: 0, short: '1/3', title: '雪球调仓' },
  { id: 1, short: '2/3', title: '新浪行情' },
  { id: 2, short: '3/3', title: '官方净值' },
] as const;

const pulse = keyframes`
  0%, 100% { box-shadow: 0 0 0 0 rgba(91, 123, 151, 0.25); }
  50% { box-shadow: 0 0 0 5px rgba(91, 123, 151, 0); }
`;

export function parseSyncStepIndex(currentStep: string | null): number | null {
  if (!currentStep) return null;
  const m = currentStep.match(/^(\d)\/3/);
  if (!m) return null;
  return Math.min(2, Math.max(0, parseInt(m[1], 10) - 1));
}

export function buildSyncStepStates(
  running: boolean,
  currentStep: string | null,
  syncSuccess: boolean,
): SyncStepState[] {
  const states: SyncStepState[] = ['pending', 'pending', 'pending'];
  if (!running && syncSuccess) {
    return ['done', 'done', 'done'];
  }
  const idx = parseSyncStepIndex(currentStep);
  if (idx != null) {
    for (let i = 0; i < idx; i += 1) states[i] = 'done';
    states[idx] = running ? 'active' : 'done';
    for (let i = idx + 1; i < 3; i += 1) states[i] = 'pending';
  } else if (running) {
    states[0] = 'active';
  }
  return states;
}

function StepDot({ state, index }: { state: SyncStepState; index: number }) {
  const isDone = state === 'done';
  const isActive = state === 'active';
  return (
    <Box
      sx={{
        width: 32,
        height: 32,
        borderRadius: '50%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexShrink: 0,
        bgcolor: isDone ? DASHBOARD_THEME.downTint : isActive ? 'rgba(91, 123, 151, 0.1)' : DASHBOARD_THEME.insetBg,
        border: `1.5px solid ${isDone ? DASHBOARD_THEME.down : isActive ? DASHBOARD_THEME.primary : DASHBOARD_THEME.borderSubtle}`,
        color: isDone ? DASHBOARD_THEME.down : isActive ? DASHBOARD_THEME.primary : DASHBOARD_THEME.textMuted,
        animation: isActive ? `${pulse} 1.5s ease-in-out infinite` : undefined,
      }}
    >
      {isDone ? (
        <CheckRoundedIcon sx={{ fontSize: 18 }} />
      ) : (
        <Typography sx={{ fontSize: 13, fontWeight: 600, lineHeight: 1 }}>{index + 1}</Typography>
      )}
    </Box>
  );
}

function Connector({ done }: { done: boolean }) {
  return (
    <Box
      sx={{
        flex: 1,
        height: 1,
        minWidth: 48,
        alignSelf: 'center',
        mx: 1,
        bgcolor: done ? DASHBOARD_THEME.down : DASHBOARD_THEME.borderSubtle,
        transition: 'background-color 0.25s',
      }}
    />
  );
}

type Props = {
  stepStates: SyncStepState[];
};

export function SyncStepIndicator({ stepStates }: Props) {
  return (
    <Box
      sx={{
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'center',
        width: '100%',
        maxWidth: 720,
        mx: 'auto',
        py: 2,
      }}
    >
      {STEPS.map((step, i) => (
        <Box
          key={step.id}
          sx={{
            display: 'flex',
            alignItems: 'center',
            flex: i < STEPS.length - 1 ? 1 : '0 0 auto',
            minWidth: 0,
          }}
        >
          <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 0.75, minWidth: 88, flexShrink: 0 }}>
            <StepDot state={stepStates[i] ?? 'pending'} index={i} />
            <Typography sx={{ fontSize: 10, color: DASHBOARD_THEME.textMuted, fontWeight: 500, textAlign: 'center' }}>
              {step.short}
            </Typography>
            <Typography
              sx={{
                fontSize: 13,
                fontWeight: stepStates[i] === 'active' ? 600 : 500,
                color:
                  stepStates[i] === 'done'
                    ? DASHBOARD_THEME.down
                    : stepStates[i] === 'active'
                      ? DASHBOARD_THEME.primary
                      : DASHBOARD_THEME.textSecondary,
                textAlign: 'center',
                whiteSpace: 'nowrap',
              }}
            >
              {step.title}
            </Typography>
          </Box>
          {i < STEPS.length - 1 && <Connector done={stepStates[i] === 'done'} />}
        </Box>
      ))}
    </Box>
  );
}
