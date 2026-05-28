import CloseRoundedIcon from '@mui/icons-material/CloseRounded';
import { Alert, IconButton, Snackbar } from '@mui/material';
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react';
import { DASHBOARD_THEME } from '../dashboard/utils';

export type ToastSeverity = 'success' | 'error' | 'info' | 'warning';

type ToastItem = {
  id: number;
  message: string;
  severity: ToastSeverity;
};

type ToastContextValue = {
  showToast: (message: string, severity?: ToastSeverity) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);

const AUTO_HIDE = 4500;

export function ToastProvider({ children }: { children: ReactNode }) {
  const queueRef = useRef<ToastItem[]>([]);
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState<ToastItem | null>(null);

  const pump = useCallback(() => {
    if (queueRef.current.length === 0) {
      setCurrent(null);
      setOpen(false);
      return;
    }
    const [next, ...rest] = queueRef.current;
    queueRef.current = rest;
    setCurrent(next);
    setOpen(true);
  }, []);

  const showToast = useCallback(
    (message: string, severity: ToastSeverity = 'info') => {
      queueRef.current.push({ id: Date.now() + Math.random(), message, severity });
      setCurrent((cur) => {
        if (!cur) {
          setOpen(true);
          return queueRef.current.shift() ?? null;
        }
        return cur;
      });
    },
    [],
  );

  const handleClose = useCallback(() => {
    setOpen(false);
  }, []);

  const handleExited = useCallback(() => {
    pump();
  }, [pump]);

  const value = useMemo(() => ({ showToast }), [showToast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <Snackbar
        open={open && Boolean(current)}
        autoHideDuration={AUTO_HIDE}
        onClose={handleClose}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
        TransitionProps={{ onExited: handleExited }}
      >
        {current ? (
          <Alert
            severity={current.severity}
            variant="outlined"
            sx={{
              width: '100%',
              maxWidth: 420,
              bgcolor: DASHBOARD_THEME.surface,
              border: DASHBOARD_THEME.cardBorder,
              boxShadow: DASHBOARD_THEME.shadowMd,
              borderRadius: `${DASHBOARD_THEME.radiusMd}px`,
              '& .MuiAlert-message': { fontSize: 13 },
            }}
            action={
              <IconButton size="small" onClick={handleClose} aria-label="关闭">
                <CloseRoundedIcon fontSize="small" />
              </IconButton>
            }
          >
            {current.message}
          </Alert>
        ) : undefined}
      </Snackbar>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used within ToastProvider');
  return ctx;
}
