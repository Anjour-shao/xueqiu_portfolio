import { ParsedTradeItem } from '../types';

const TIME_SUFFIX_RE = /(\d{1,2}:\d{2}:\d{2})$/;
const CODE_RE = /^(SH|SZ|BJ)\d{6}$/i;
const WEIGHT_RE = /^(-?\d+(?:\.\d+)?)%(-?\d+(?:\.\d+)?)%$/;
const PRICE_RE = /参考成交价\s*([-+]?\d+(?:\.\d+)?)/;
const SPECIAL_WORDS = ['分红送配', '已取消'] as const;

export interface SkippedTradeItem {
  trade_time: string | null;
  stock_name: string | null;
  ts_code: string | null;
  reason: string;
  raw_block: string;
}

export interface ParseResult {
  trades: ParsedTradeItem[];
  skipped: SkippedTradeItem[];
}

function compactLines(text: string) {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function parseTime(line: string): string | null {
  const match = line.match(TIME_SUFFIX_RE);
  if (!match) return null;

  const clock = match[1];
  const datePart = line.slice(0, match.index).trim();
  const parts = datePart.split('.');
  if (parts.length !== 3) return null;

  const [year, month, day] = parts.map((item) => Number(item));
  if ([year, month, day].some((item) => Number.isNaN(item))) return null;

  const yyyy = String(year).padStart(4, '0');
  const mm = String(month).padStart(2, '0');
  const dd = String(day).padStart(2, '0');
  return `${yyyy}-${mm}-${dd} ${clock}`;
}

function splitBlocks(lines: string[]) {
  const blocks: Array<{ tradeTime: string; lines: string[] }> = [];
  let currentTime: string | null = null;
  let current: string[] = [];

  for (const line of lines) {
    const ts = parseTime(line);
    if (ts) {
      if (currentTime) {
        blocks.push({ tradeTime: currentTime, lines: current });
      }
      currentTime = ts;
      current = [];
    } else if (currentTime) {
      current.push(line);
    }
  }

  if (currentTime) {
    blocks.push({ tradeTime: currentTime, lines: current });
  }

  return blocks;
}

function inferAction(fromWeight: number, toWeight: number): ParsedTradeItem['action'] {
  if (fromWeight === 0 && toWeight > 0) return 'BUY';
  if (fromWeight > 0 && toWeight === 0) return 'SELL';
  if (toWeight > fromWeight) return 'INCREASE';
  if (toWeight < fromWeight) return 'DECREASE';
  return 'HOLD';
}

export function parseRebalanceLogs(text: string): ParseResult {
  const lines = compactLines(text);
  const trades: ParsedTradeItem[] = [];
  const skipped: SkippedTradeItem[] = [];

  for (const { tradeTime, lines: block } of splitBlocks(lines)) {
    let index = 0;

    while (index < block.length) {
      const rawPart = block.slice(index, Math.min(index + 4, block.length)).join('\n');
      if (index + 2 >= block.length) {
        skipped.push({ trade_time: tradeTime, stock_name: null, ts_code: null, reason: 'incomplete_block', raw_block: rawPart });
        break;
      }

      const stockName = block[index];
      const tsCode = block[index + 1]?.toUpperCase() ?? null;
      const statusOrWeight = block[index + 2] ?? '';
      const priceLine = block[index + 3] ?? '';

      if (!tsCode || !CODE_RE.test(tsCode)) {
        skipped.push({ trade_time: tradeTime, stock_name: stockName, ts_code: tsCode, reason: 'invalid_code', raw_block: rawPart });
        index += 1;
        continue;
      }

      if (SPECIAL_WORDS.some((word) => rawPart.includes(word))) {
        skipped.push({ trade_time: tradeTime, stock_name: stockName, ts_code: tsCode, reason: 'special_ignored', raw_block: rawPart });
        index += priceLine ? 4 : 3;
        continue;
      }

      const weightMatch = statusOrWeight.match(WEIGHT_RE);
      if (!weightMatch) {
        skipped.push({ trade_time: tradeTime, stock_name: stockName, ts_code: tsCode, reason: 'invalid_weight', raw_block: rawPart });
        index += priceLine ? 4 : 3;
        continue;
      }

      const fromWeight = Number(weightMatch[1]);
      const toWeight = Number(weightMatch[2]);
      const priceMatch = priceLine.match(PRICE_RE);
      const price = priceMatch ? Number(priceMatch[1]) : null;

      trades.push({
        trade_time: tradeTime,
        stock_name: stockName,
        ts_code: tsCode,
        from_weight: fromWeight,
        to_weight: toWeight,
        weight_delta: Number((toWeight - fromWeight).toFixed(4)),
        price,
        action: inferAction(fromWeight, toWeight),
        raw_block: rawPart,
      });

      index += 4;
    }
  }

  return { trades, skipped };
}
