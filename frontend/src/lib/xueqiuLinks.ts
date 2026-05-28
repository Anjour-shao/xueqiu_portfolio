export function portfolioUrl(accountCode: string) {
  return `https://xueqiu.com/P/${encodeURIComponent(accountCode.trim())}`;
}

export function stockUrl(tsCode: string) {
  return `https://xueqiu.com/S/${encodeURIComponent(tsCode.trim().toUpperCase())}`;
}
