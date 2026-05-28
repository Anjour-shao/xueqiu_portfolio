import type { SxProps, Theme } from '@mui/material';



export const DASHBOARD_THEME = {

  bgMain: '#F5F5F7',

  bgGradient: '#F5F5F7',

  surface: '#FFFFFF',

  cardBg: '#FFFFFF',

  cardBorder: '1px solid #E8E8ED',

  cardShadow: '0 1px 3px rgba(0, 0, 0, 0.06)',

  borderSubtle: '#E8E8ED',

  shadowSm: '0 1px 3px rgba(0, 0, 0, 0.06)',

  shadowMd: '0 4px 16px rgba(0, 0, 0, 0.08)',

  radiusLg: 16,

  radiusMd: 12,

  radiusPill: 980,

  pagePaddingX: { xs: 2, md: 3 },

  pagePaddingY: 2,

  sectionGap: 3,

  textPrimary: '#1D1D1F',

  textSecondary: '#86868B',

  textMuted: '#AEAEB2',

  primary: '#5B7B97',

  primaryHover: '#4A6A85',

  up: '#C97A7E',

  down: '#759E87',

  upTint: 'rgba(201, 122, 126, 0.08)',

  downTint: 'rgba(117, 158, 135, 0.08)',

  rowHover: 'rgba(0, 0, 0, 0.03)',

  navActive: 'rgba(0, 0, 0, 0.05)',

  insetBg: '#F5F5F7',

  monoFont: "'Roboto Mono', 'JetBrains Mono', ui-monospace, monospace",

  sansFont:

    '-apple-system, BlinkMacSystemFont, "SF Pro SC", "PingFang SC", "Noto Sans SC", "Microsoft YaHei", sans-serif',

} as const;



export const surfaceCardSx: SxProps<Theme> = {

  bgcolor: DASHBOARD_THEME.surface,

  border: DASHBOARD_THEME.cardBorder,

  borderRadius: `${DASHBOARD_THEME.radiusMd}px`,

  boxShadow: DASHBOARD_THEME.shadowSm,

};



/** @deprecated use surfaceCardSx */

export const glassCardSx: SxProps<Theme> = surfaceCardSx;



export const glassHeaderSx: SxProps<Theme> = {

  bgcolor: 'rgba(255, 255, 255, 0.82)',

  backdropFilter: 'blur(20px)',

  WebkitBackdropFilter: 'blur(20px)',

  borderBottom: `1px solid ${DASHBOARD_THEME.borderSubtle}`,

  boxShadow: 'none',

};



export const monoSx = { fontFamily: DASHBOARD_THEME.monoFont, fontVariantNumeric: 'tabular-nums' };



const FINANCIAL_HEADER_RE =

  /代码|仓位|收益|价格|成本|现价|贡献|胜率|涨跌|净值|浮动|成交|权重|批次|持仓|笔|只|平仓|时间|动作|%/i;



export function isFinancialHeader(header: string) {

  return FINANCIAL_HEADER_RE.test(header);

}



export function fmtPct(value: number | null | undefined) {

  if (value == null) return '-';

  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;

}



export function fmtWeightDelta(from: number, to: number) {

  return `${from.toFixed(0)}→${to.toFixed(0)}%`;

}



export function pctColor(value: number | null | undefined) {

  if (value == null) return DASHBOARD_THEME.textPrimary;

  if (value > 0) return DASHBOARD_THEME.up;

  if (value < 0) return DASHBOARD_THEME.down;

  return DASHBOARD_THEME.textPrimary;

}



export function parsePctSign(value: string): 'up' | 'down' | 'neutral' {

  const trimmed = value.trim();

  if (trimmed.startsWith('+')) return 'up';

  if (trimmed.startsWith('-') && trimmed !== '-') return 'down';

  const num = parseFloat(trimmed.replace('%', ''));

  if (!Number.isNaN(num)) {

    if (num > 0) return 'up';

    if (num < 0) return 'down';

  }

  return 'neutral';

}



/** 持仓时长：仅展示自然日天数 */

export function fmtHoldingDays(days: number | null | undefined, _isHolding?: boolean): string {

  if (days == null || days < 0) return '-';

  return `${days}天`;

}



export function fmtDateOnly(value: string | null | undefined): string {

  if (!value) return '-';

  const t = value.trim();

  if (t.length >= 10 && t[4] === '-') return t.slice(0, 10);

  if (t.length >= 10 && t.includes(' ')) return t.slice(0, 10);

  if (t.length >= 8 && /^\d{8}/.test(t)) {

    return `${t.slice(0, 4)}-${t.slice(4, 6)}-${t.slice(6, 8)}`;

  }

  return t.split(/\s/)[0] || t;

}



const ACTION_CN: Record<string, string> = {

  BUY: '买入',

  SELL: '卖出',

  INCREASE: '加仓',

  DECREASE: '减仓',

  HOLD: '持平',

};



/** 动作显示为中文（非 BUY/SELL 等英文） */

export function actionLabel(action: string | null | undefined): string {

  if (!action) return '-';

  const key = action.trim().toUpperCase();

  if (ACTION_CN[key]) return ACTION_CN[key];

  const raw = action.trim();

  if (raw === '买入' || raw === '卖出' || raw === '加仓' || raw === '减仓' || raw === '持平') return raw;

  if (raw.includes('买')) return '买入';

  if (raw.includes('卖')) return '卖出';

  return raw;

}



export function pctTintBg(value: string) {

  const sign = parsePctSign(value);

  if (sign === 'up') return DASHBOARD_THEME.upTint;

  if (sign === 'down') return DASHBOARD_THEME.downTint;

  return DASHBOARD_THEME.insetBg;

}


