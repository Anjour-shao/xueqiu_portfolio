import { MouseEvent, ReactNode } from 'react';
import { Box, Link } from '@mui/material';
import ArrowDownwardRoundedIcon from '@mui/icons-material/ArrowDownwardRounded';
import ArrowUpwardRoundedIcon from '@mui/icons-material/ArrowUpwardRounded';
import UnfoldMoreRoundedIcon from '@mui/icons-material/UnfoldMoreRounded';
import { DASHBOARD_THEME, isFinancialHeader, monoSx, pctColor, parsePctSign } from './utils';

export type TableColumn = {
  key: string;
  label: string;
  sortable?: boolean;
  width?: string;
  align?: 'left' | 'right' | 'center';
  clip?: boolean;
};

export type TableSort = {
  key: string;
  desc: boolean;
};

export function DataTable({
  columns,
  rows,
  dense = false,
  compact = false,
  minWidth,
  sort,
  onSort,
  getCellLink,
  onCellClick,
  onRowClick,
  rowActions,
  stickyHeader = true,
  showRowHoverActions = false,
}: {
  columns: TableColumn[];
  rows: Array<Array<string | number | ReactNode>>;
  dense?: boolean;
  compact?: boolean;
  minWidth?: number;
  sort?: TableSort | null;
  onSort?: (key: string) => void;
  getCellLink?: (rowIndex: number, columnKey: string) => string | null;
  onCellClick?: (rowIndex: number, columnKey: string, event: MouseEvent) => void;
  onRowClick?: (rowIndex: number) => void;
  rowActions?: (rowIndex: number) => ReactNode;
  stickyHeader?: boolean;
  showRowHoverActions?: boolean;
}) {
  const cellPy = compact && dense ? 0.75 : dense ? 1 : 1.35;
  const cellPx = compact && dense ? 0.75 : compact ? 1 : dense ? 1.25 : 1.5;
  const fontSize = dense ? 12 : 13;
  const hasActions = Boolean(rowActions);

  const handleHeaderClick = (col: TableColumn) => {
    if (!col.sortable || !onSort) return;
    onSort(col.key);
  };

  return (
    <Box sx={{ overflow: 'auto', width: '100%', maxWidth: '100%' }}>
      <Box
        component="table"
        sx={{
          width: '100%',
          borderCollapse: 'collapse',
          minWidth: minWidth ?? (compact ? 280 : 640),
          tableLayout: compact && !minWidth ? 'fixed' : compact ? 'auto' : 'auto',
        }}
      >
        <Box component="thead" sx={stickyHeader ? { position: 'sticky', top: 0, zIndex: 1 } : undefined}>
          <Box component="tr" sx={{ borderBottom: `1px solid ${DASHBOARD_THEME.borderSubtle}` }}>
            {columns.map((col) => {
              const active = sort?.key === col.key;
              const canSort = col.sortable && onSort;
              const headerClip = col.clip !== false;
              const headerUppercase = /^[A-Za-z0-9\s%]+$/.test(col.label);
              return (
                <Box
                  key={col.key}
                  component="th"
                  onClick={() => handleHeaderClick(col)}
                  sx={{
                    textAlign: col.align ?? 'center',
                    fontSize: dense ? 10 : 11,
                    fontWeight: 600,
                    color: active ? DASHBOARD_THEME.primary : DASHBOARD_THEME.textMuted,
                    letterSpacing: headerUppercase ? '0.04em' : undefined,
                    textTransform: headerUppercase ? 'uppercase' : 'none',
                    py: cellPy,
                    px: cellPx,
                    bgcolor: DASHBOARD_THEME.surface,
                    whiteSpace: 'nowrap',
                    cursor: canSort ? 'pointer' : 'default',
                    userSelect: 'none',
                    width: compact && col.width ? col.width : undefined,
                    maxWidth: compact && col.width && headerClip ? col.width : undefined,
                    overflow: headerClip ? 'hidden' : 'visible',
                    textOverflow: headerClip ? 'ellipsis' : 'clip',
                    '&:hover': canSort ? { color: DASHBOARD_THEME.primary } : undefined,
                  }}
                >
                  <Box
                    sx={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 0.35,
                      justifyContent:
                        col.align === 'right' ? 'flex-end' : col.align === 'left' ? 'flex-start' : 'center',
                    }}
                  >
                    {col.label}
                    {canSort && (
                      <Box component="span" sx={{ display: 'inline-flex', opacity: active ? 1 : 0.45, fontSize: 14 }}>
                        {!active && <UnfoldMoreRoundedIcon sx={{ fontSize: 14 }} />}
                        {active && sort.desc && <ArrowDownwardRoundedIcon sx={{ fontSize: 14 }} />}
                        {active && !sort.desc && <ArrowUpwardRoundedIcon sx={{ fontSize: 14 }} />}
                      </Box>
                    )}
                  </Box>
                </Box>
              );
            })}
            {hasActions && (
              <Box component="th" sx={{ width: 48, py: cellPy, px: cellPx, bgcolor: DASHBOARD_THEME.surface }} />
            )}
          </Box>
        </Box>
        <Box component="tbody">
          {rows.length === 0 ? (
            <Box component="tr">
              <Box
                component="td"
                colSpan={columns.length + (hasActions ? 1 : 0)}
                sx={{ py: 4, px: cellPx, fontSize: 13, color: DASHBOARD_THEME.textMuted, textAlign: 'center' }}
              >
                暂无数据
              </Box>
            </Box>
          ) : (
            rows.map((row, rowIndex) => (
              <Box
                key={rowIndex}
                component="tr"
                onClick={onRowClick ? () => onRowClick(rowIndex) : undefined}
                sx={{
                  transition: 'background-color 0.15s ease',
                  cursor: onRowClick ? 'pointer' : undefined,
                  borderBottom: `1px solid ${DASHBOARD_THEME.borderSubtle}`,
                  '&:last-child': { borderBottom: 'none' },
                  '&:hover': {
                    bgcolor: DASHBOARD_THEME.rowHover,
                    '& .row-action-cell': showRowHoverActions ? { opacity: 1 } : undefined,
                  },
                }}
              >
                {row.map((cell, cellIndex) => {
                  const col = columns[cellIndex];
                  const header = col?.label ?? '';
                  const useMono = typeof cell === 'string' || typeof cell === 'number' ? isFinancialHeader(header) : false;
                  const cellStr = String(cell);
                  const isPctCell =
                    typeof cell === 'string' &&
                    /收益|浮动|贡献|涨跌|仓位/.test(header) &&
                    parsePctSign(cellStr) !== 'neutral';
                  const cellColor =
                    typeof cell === 'string' && isPctCell
                      ? pctColor(
                          cellStr.startsWith('+')
                            ? 1
                            : cellStr.startsWith('-')
                              ? -1
                              : parseFloat(cellStr.replace('%', '')),
                        )
                      : DASHBOARD_THEME.textPrimary;
                  const tdSx = {
                    fontSize,
                    color: cellColor,
                    py: cellPy,
                    px: cellPx,
                    textAlign: col?.align ?? ('center' as const),
                    whiteSpace: 'nowrap' as const,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    width: compact && col?.width ? col.width : undefined,
                    maxWidth:
                      compact && col?.width && col?.clip !== false
                        ? col.width
                        : compact && col?.clip !== false
                          ? 140
                          : undefined,
                    ...(useMono ? monoSx : {}),
                  };
                  const colKey = col?.key ?? '';
                  const href = getCellLink?.(rowIndex, colKey);
                  const cellClickable = Boolean(onCellClick && colKey);
                  const cellContent =
                    href != null && (typeof cell === 'string' || typeof cell === 'number') ? (
                      <Link
                        href={href}
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        sx={{
                          color: 'inherit',
                          fontWeight: 600,
                          textDecoration: 'none',
                          '&:hover': { color: DASHBOARD_THEME.primary, textDecoration: 'underline' },
                        }}
                      >
                        {cell}
                      </Link>
                    ) : cellClickable && cellIndex === 0 ? (
                      <Box
                        component="span"
                        onClick={(e) => {
                          e.stopPropagation();
                          onCellClick?.(rowIndex, colKey, e);
                        }}
                        sx={{
                          cursor: 'pointer',
                          fontWeight: 600,
                          '&:hover': { color: DASHBOARD_THEME.primary, textDecoration: 'underline' },
                        }}
                      >
                        {cell}
                      </Box>
                    ) : (
                      cell
                    );
                  return (
                    <Box key={`${rowIndex}-${cellIndex}`} component="td" sx={tdSx}>
                      {cellContent}
                    </Box>
                  );
                })}
                {hasActions && (
                  <Box
                    component="td"
                    className="row-action-cell"
                    sx={{
                      py: cellPy,
                      px: 0.5,
                      textAlign: 'center',
                      opacity: showRowHoverActions ? 0 : 1,
                      transition: 'opacity 0.15s ease',
                    }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    {rowActions?.(rowIndex)}
                  </Box>
                )}
              </Box>
            ))
          )}
        </Box>
      </Box>
    </Box>
  );
}

export function toggleSort(current: TableSort | null, key: string, defaultDesc = true): TableSort {
  if (current?.key === key) {
    return { key, desc: !current.desc };
  }
  return { key, desc: defaultDesc };
}
