import { Box } from '@mui/material';
import { ReactNode } from 'react';

type Props = {
  children: ReactNode;
  minColWidth?: number;
  gap?: number;
};

export function MetricGrid({ children, minColWidth = 100, gap = 1 }: Props) {
  return (
    <Box
      sx={{
        display: 'grid',
        gridTemplateColumns: `repeat(auto-fit, minmax(${minColWidth}px, 1fr))`,
        gap,
      }}
    >
      {children}
    </Box>
  );
}
