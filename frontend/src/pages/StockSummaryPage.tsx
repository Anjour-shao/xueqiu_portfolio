import SearchRoundedIcon from '@mui/icons-material/SearchRounded';
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  FormControl,
  InputLabel,
  LinearProgress,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import { useRef, useState } from 'react';
import { streamStockSummary, StockSummaryEvent } from '../api/dashboard';
import { PageContent } from '../components/PageContent';
import { PageHeader } from '../components/PageHeader';
import { SectionCard } from '../components/SectionCard';
import { DASHBOARD_THEME } from '../features/dashboard/utils';
import { useToast } from '../features/notify/ToastProvider';

const PAGE_OPTIONS = [
  { value: 5, label: '5 页（约 100 条）' },
  { value: 10, label: '10 页（约 200 条）' },
  { value: 20, label: '20 页（约 400 条）' },
];

const STEPS = [
  { key: 'resolve', label: '解析股票', icon: '🔍' },
  { key: 'fetch', label: '爬取讨论区', icon: '📥' },
  { key: 'clean', label: '清洗数据', icon: '🧹' },
  { key: 'ai', label: 'AI 深度分析', icon: '🤖' },
] as const;

// ---- 文本渲染：处理轻度 markdown（** 加粗、- 列表） ----

function renderLine(text: string, idx: number) {
  // 处理 **加粗**
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  const children = parts.map((p, i) => {
    if (p.startsWith('**') && p.endsWith('**')) {
      return (
        <strong key={i} style={{ fontWeight: 600 }}>
          {p.slice(2, -2)}
        </strong>
      );
    }
    return p;
  });

  return (
    <Typography
      key={idx}
      sx={{
        fontSize: 15,
        lineHeight: 1.85,
        color: '#374151',
        pl: text.startsWith('-') || text.startsWith('•') ? 2 : 0,
        mb: 0.5,
      }}
    >
      {children}
    </Typography>
  );
}

function renderArticle(summary: string) {
  // 把 AI 输出切分成段落块
  const blocks: { type: 'h2' | 'text'; content: string }[] = [];
  const lines = summary.split('\n');
  let currentText: string[] = [];

  for (const line of lines) {
    const trimmed = line.trim();
    // 检测标题行：以 【xxx】 开头
    const h2Match = trimmed.match(/^【(.+?)】/);
    if (h2Match && !trimmed.startsWith('【📌】')) {
      if (currentText.length > 0) {
        blocks.push({ type: 'text', content: currentText.join('\n') });
        currentText = [];
      }
      blocks.push({ type: 'h2', content: h2Match[1] });
      // 标题后面可能还有文字
      const after = trimmed.slice(h2Match[0].length).trim();
      if (after) currentText.push(after);
    } else {
      currentText.push(line);
    }
  }
  if (currentText.length > 0) {
    blocks.push({ type: 'text', content: currentText.join('\n') });
  }

  if (blocks.length === 0) {
    // 无标题 → 整段渲染
    return (
      <Box sx={{ fontSize: 15, lineHeight: 1.85, color: '#374151' }}>
        {summary.split('\n').map((l, i) => renderLine(l.trim(), i))}
      </Box>
    );
  }

  return blocks.map((block, bi) => {
    if (block.type === 'h2') {
      return (
        <Typography
          key={`h2-${bi}`}
          sx={{
            fontSize: 17,
            fontWeight: 700,
            color: '#111827',
            mt: bi > 0 ? 3.5 : 2,
            mb: 1.5,
            letterSpacing: '-0.01em',
          }}
        >
          {block.content}
        </Typography>
      );
    }
    // text block
    const textLines = block.content.split('\n').map((l) => l.trim()).filter((l) => l.length > 0);
    return (
      <Box key={`text-${bi}`} sx={{ mb: 2 }}>
        {textLines.map((l, i) => renderLine(l, i))}
      </Box>
    );
  });
}

// ---- State ----

type RunState = {
  loading: boolean;
  currentStep: string;
  messages: { step: string; text: string }[];
  result: StockSummaryEvent['result'] | null;
  error: string | null;
};

export function StockSummaryPage() {
  const { showToast } = useToast();
  const [keyword, setKeyword] = useState('');
  const [pages, setPages] = useState(10);
  const [run, setRun] = useState<RunState>({
    loading: false,
    currentStep: '',
    messages: [],
    result: null,
    error: null,
  });
  const abortRef = useRef<AbortController | null>(null);

  const handleSearch = async () => {
    const kw = keyword.trim();
    if (!kw) return;

    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setRun({ loading: true, currentStep: '', messages: [], result: null, error: null });

    try {
      await streamStockSummary(
        kw,
        pages,
        (event: StockSummaryEvent) => {
          setRun((prev) => {
            const newMessages = [...prev.messages, { step: event.step, text: event.message }];
            if (event.step === 'done') {
              return { ...prev, loading: false, currentStep: 'done', messages: newMessages, result: event.result ?? null };
            }
            if (event.step === 'error') {
              return { ...prev, loading: false, currentStep: 'error', messages: newMessages, error: event.message };
            }
            return { ...prev, currentStep: event.step, messages: newMessages };
          });
        },
        controller.signal,
      );
    } catch (err: any) {
      if (err?.name === 'AbortError') return;
      setRun((prev) => ({ ...prev, loading: false, error: err?.message || '未知错误' }));
      showToast(err?.message || '请求失败', 'error');
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleSearch();
  };

  const activeStepIdx = STEPS.findIndex((s) => s.key === run.currentStep);
  const showResult = run.result && !run.loading;
  const info = run.result?.company_info;

  return (
    <Box sx={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <PageHeader
        title="个股汇总"
        icon={<SearchRoundedIcon />}
        meta={
          <Typography component="span" sx={{ fontSize: 12, color: DASHBOARD_THEME.textSecondary }}>
            输入股票名称或代码，AI 深度分析雪球讨论区
          </Typography>
        }
      />

      <PageContent>
        <Stack spacing={3}>
          {/* ---- 搜索栏 ---- */}
          <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'center', flexWrap: 'wrap' }}>
            <TextField
              size="small"
              placeholder="输入股票名称或代码"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={run.loading}
              sx={{ flex: 1, minWidth: 200 }}
              inputProps={{ sx: { fontSize: 14 } }}
            />
            <FormControl size="small" sx={{ minWidth: 150 }}>
              <InputLabel>页数</InputLabel>
              <Select value={pages} label="页数" disabled={run.loading} onChange={(e) => setPages(Number(e.target.value))}>
                {PAGE_OPTIONS.map((opt) => (
                  <MenuItem key={opt.value} value={opt.value}>{opt.label}</MenuItem>
                ))}
              </Select>
            </FormControl>
            <Button
              variant="contained"
              disabled={run.loading || !keyword.trim()}
              onClick={handleSearch}
              startIcon={run.loading ? <CircularProgress size={16} color="inherit" /> : <SearchRoundedIcon />}
              sx={{ whiteSpace: 'nowrap', minWidth: 100 }}
            >
              {run.loading ? '分析中…' : '开始分析'}
            </Button>
          </Box>

          {run.loading && <LinearProgress />}

          {/* ---- 初始引导 ---- */}
          {!run.loading && !run.result && !run.error && run.messages.length === 0 && (
            <Box
              sx={{
                bgcolor: DASHBOARD_THEME.surface,
                borderRadius: `${DASHBOARD_THEME.radiusMd}px`,
                p: 4,
                textAlign: 'center',
              }}
            >
              <Typography sx={{ fontSize: 18, fontWeight: 600, color: '#374151', mb: 1 }}>
                个股汇总分析
              </Typography>
              <Typography sx={{ fontSize: 14, color: DASHBOARD_THEME.textSecondary, lineHeight: 1.8 }}>
                输入 A 股股票名称或 6 位代码
                <br />
                系统自动爬取雪球讨论区 · AI 深度分析与提炼
              </Typography>
            </Box>
          )}

          {/* ---- 进度步骤 ---- */}
          {(run.loading || run.result || run.error) && (
            <SectionCard title="分析进度">
              <Stack spacing={1.5}>
                <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                  {STEPS.map((step, idx) => {
                    const isDone = run.currentStep === 'done' || activeStepIdx > idx;
                    const isActive = activeStepIdx === idx;
                    return (
                      <Chip
                        key={step.key}
                        icon={isActive ? <CircularProgress size={12} /> : undefined}
                        label={`${step.icon} ${step.label}`}
                        size="small"
                        variant={isActive ? 'filled' : 'outlined'}
                        sx={{
                          fontWeight: isActive ? 600 : 400,
                          bgcolor: isActive ? '#f3f4f6' : 'transparent',
                          color: isActive ? '#111827' : isDone ? '#6b7280' : '#9ca3af',
                          borderColor: isDone ? '#d1d5db' : '#e5e7eb',
                        }}
                      />
                    );
                  })}
                </Stack>
                <Box
                  sx={{
                    maxHeight: 160,
                    overflow: 'auto',
                    bgcolor: '#f9fafb',
                    borderRadius: 1,
                    p: 1.5,
                    fontFamily: 'monospace',
                    fontSize: 11,
                    lineHeight: 1.7,
                  }}
                >
                  {run.messages.map((m, i) => (
                    <Box key={i} sx={{ color: m.step === 'error' ? '#dc2626' : '#6b7280' }}>
                      {m.step === 'error' ? '✕ ' : '· '}{m.text}
                    </Box>
                  ))}
                </Box>
              </Stack>
            </SectionCard>
          )}

          {/* ---- 错误 ---- */}
          {run.error && !run.loading && (
            <Box sx={{ p: 2, bgcolor: '#fef2f2', borderRadius: 1 }}>
              <Typography color="error" variant="body2">{run.error}</Typography>
            </Box>
          )}

          {/* ---- 结果文章 ---- */}
          {showResult && run.result!.summary && (
            <Box
              sx={{
                bgcolor: DASHBOARD_THEME.surface,
                borderRadius: `${DASHBOARD_THEME.radiusMd}px`,
                p: { xs: 3, sm: 4 },
                boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
              }}
            >
              {/* 抬头 */}
              <Typography
                sx={{
                  fontSize: 20,
                  fontWeight: 700,
                  color: '#111827',
                  mb: 0.5,
                  letterSpacing: '-0.02em',
                }}
              >
                {run.result!.stock_name}
                <Typography
                  component="span"
                  sx={{ fontSize: 14, fontWeight: 400, color: '#9ca3af', ml: 1.5 }}
                >
                  {run.result!.stock_code}
                </Typography>
              </Typography>

              {/* 元信息 */}
              <Stack direction="row" spacing={1.5} sx={{ mb: 3 }} flexWrap="wrap" useFlexGap>
                {info?.industry && (
                  <Typography sx={{ fontSize: 13, color: '#6b7280' }}>{info.industry}</Typography>
                )}
                {info?.area && (
                  <Typography sx={{ fontSize: 13, color: '#6b7280' }}>{info.area}</Typography>
                )}
                {info?.list_date && (
                  <Typography sx={{ fontSize: 13, color: '#9ca3af' }}>上市 {info.list_date}</Typography>
                )}
                <Typography sx={{ fontSize: 13, color: '#9ca3af' }}>
                  基于 {run.result!.post_count} 条讨论
                </Typography>
              </Stack>

              <Divider sx={{ mb: 3 }} />

              {/* 正文 */}
              {renderArticle(run.result!.summary)}
            </Box>
          )}
        </Stack>
      </PageContent>
    </Box>
  );
}
