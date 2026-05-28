import ContentCopyRoundedIcon from '@mui/icons-material/ContentCopyRounded';
import ExpandMoreRoundedIcon from '@mui/icons-material/ExpandMoreRounded';
import OpenInNewRoundedIcon from '@mui/icons-material/OpenInNewRounded';
import PlayArrowRoundedIcon from '@mui/icons-material/PlayArrowRounded';
import RestartAltRoundedIcon from '@mui/icons-material/RestartAltRounded';
import StopRoundedIcon from '@mui/icons-material/StopRounded';
import TravelExploreRoundedIcon from '@mui/icons-material/TravelExploreRounded';
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Button,
  Checkbox,
  CircularProgress,
  FormControlLabel,
  FormGroup,
  Grid,
  LinearProgress,
  Link,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useState } from 'react';
import { fetchCubeCatalogStats, followPortfolios, resetCubeCatalogDiscovered } from '../api/dashboard';
import { PageContent } from '../components/PageContent';
import { PageHeader } from '../components/PageHeader';
import { SectionCard } from '../components/SectionCard';
import { portfolioUrl } from '../lib/xueqiuLinks';
import { DataTable, TableColumn } from '../features/dashboard/DataTable';
import {
  defaultCheckpoint,
  exportCheckpointJson,
  importCheckpointJson,
} from '../features/discover/discoverCheckpoint';
import {
  clearAllCandidates,
  markCandidateReviewed,
  removeCandidates,
  type DiscoverCandidate,
} from '../features/discover/discoverCandidates';
import { useDiscover } from '../features/discover/DiscoverProvider';
import { useToast } from '../features/notify/ToastProvider';
import { DASHBOARD_THEME } from '../features/dashboard/utils';
import type { DiscoverLogItem } from '../types';

const LOG_COLOR: Record<string, string> = {
  info: DASHBOARD_THEME.textSecondary,
  success: DASHBOARD_THEME.down,
  warn: '#B45309',
  error: DASHBOARD_THEME.up,
};

const PROFILE_OPTIONS = [
  { id: 'mature_scaled', label: '成熟型（≥5年，净值按年限门槛）' },
  { id: 'young_high_return', label: '新锐型（<5年，累计≥300%）' },
] as const;

function profileLabel(id: string) {
  return PROFILE_OPTIONS.find((p) => p.id === id)?.label.split('（')[0] ?? id;
}

function LiveLogPanel({
  logs,
  running,
  progress,
}: {
  logs: DiscoverLogItem[];
  running: boolean;
  progress: { current: number; total: number; code: string } | null;
}) {
  return (
    <SectionCard
      title="挖取日志"
      action={
        running && progress ? (
          <Stack direction="row" spacing={0.75} alignItems="center">
            <CircularProgress size={14} sx={{ color: DASHBOARD_THEME.primary }} />
            <Typography sx={{ fontSize: 11, color: DASHBOARD_THEME.textSecondary }}>
              {progress.current}/{progress.total} {progress.code}
            </Typography>
          </Stack>
        ) : undefined
      }
      sx={{ minHeight: 360, display: 'flex', flexDirection: 'column', height: '100%' }}
    >
      <Box sx={{ flex: 1, overflow: 'auto', minHeight: 280, bgcolor: DASHBOARD_THEME.insetBg, borderRadius: `${DASHBOARD_THEME.radiusMd}px`, p: 1.25 }}>
        {!logs.length && !running && (
          <Typography sx={{ fontSize: 13, color: DASHBOARD_THEME.textMuted }}>
            按 account_code 顺序遍历 cube_catalog 中未挖过的组合
          </Typography>
        )}
        <Stack spacing={0.35}>
          {logs.map((line, idx) => (
            <Typography
              key={`${idx}-${line.message.slice(0, 48)}`}
              sx={{
                fontSize: 11,
                lineHeight: 1.5,
                fontFamily: line.message.startsWith('──') ? undefined : DASHBOARD_THEME.monoFont,
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
    </SectionCard>
  );
}

export function DiscoverPage() {
  const queryClient = useQueryClient();
  const {
    running,
    logs,
    progress,
    summary,
    batchHits,
    candidates,
    checkpoint,
    setCheckpoint,
    startScan,
    stopScan,
    clearLogs,
    setCandidates,
  } = useDiscover();

  const { data: catalogStats } = useQuery({
    queryKey: ['cube-catalog-stats'],
    queryFn: fetchCubeCatalogStats,
  });

  const [batchSize, setBatchSize] = useState('');
  const [inactiveDays, setInactiveDays] = useState('90');
  const [maxRebalanceMonth, setMaxRebalanceMonth] = useState('4');
  const [youngMinCum, setYoungMinCum] = useState('300');
  const [profiles, setProfiles] = useState<string[]>(['mature_scaled', 'young_high_return']);
  const [excludeFollowed, setExcludeFollowed] = useState(true);
  const [autoAdvanceCheckpoint, setAutoAdvanceCheckpoint] = useState(true);
  const [importJson, setImportJson] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [following, setFollowing] = useState(false);
  const [pageSummary, setPageSummary] = useState<{ type: 'success' | 'error' | 'info'; text: string } | null>(null);
  const { showToast } = useToast();

  useEffect(() => {
    if (pageSummary?.text) {
      showToast(pageSummary.text, pageSummary.type);
      setPageSummary(null);
    }
  }, [pageSummary, showToast]);

  const continuousMode = batchSize.trim() === '';
  const catalogCount = catalogStats?.total_count ?? 0;
  const catalogDiscovered = catalogStats?.discovered_count ?? 0;
  const catalogRemaining = catalogStats?.remaining_count ?? Math.max(0, catalogCount - catalogDiscovered);
  const catalogPct = catalogCount > 0 ? (catalogDiscovered / catalogCount) * 100 : 0;
  const hitRate =
    checkpoint.total_scanned > 0 ? ((checkpoint.total_hits / checkpoint.total_scanned) * 100).toFixed(2) : '—';

  const buildParams = useCallback(() => {
    if (!profiles.length) throw new Error('请至少勾选一个画像');
    if (catalogCount <= 0) throw new Error('cube_catalog 为空，请先在「同步数据」页执行「同步榜单组合」');
    if (catalogRemaining <= 0) throw new Error('catalog 已全部挖过，请点「重置已挖过」后再扫');
    const batchRaw = batchSize.trim();
    const continuous = batchRaw === '';
    return {
      scan_mode: 'catalog' as const,
      continuous,
      batch_size: continuous ? undefined : Number(batchRaw) || 30,
      profiles: [...profiles],
      max_rebalance_per_month: Number(maxRebalanceMonth) || 4,
      young_min_cum_pct: Number(youngMinCum) || 300,
      max_inactive_days: Number(inactiveDays) || 90,
      exclude_followed: excludeFollowed,
    };
  }, [batchSize, profiles, maxRebalanceMonth, youngMinCum, inactiveDays, excludeFollowed, catalogCount, catalogRemaining]);

  const handleStart = () => {
    setPageSummary(null);
    try {
      void startScan(buildParams(), { autoAdvanceCheckpoint });
    } catch (e) {
      setPageSummary({ type: 'error', text: (e as Error).message });
    }
  };

  const handleResetDiscovered = async () => {
    if (!window.confirm('重置 catalog 全部「已挖过」标记？将从头顺序重挖。')) return;
    try {
      const res = await resetCubeCatalogDiscovered();
      await queryClient.invalidateQueries({ queryKey: ['cube-catalog-stats'] });
      setPageSummary({ type: 'success', text: res.message });
    } catch (e) {
      setPageSummary({ type: 'error', text: (e as Error).message });
    }
  };

  const resetCheckpointStats = () => {
    setCheckpoint(defaultCheckpoint());
    setPageSummary({ type: 'info', text: '已重置累计统计' });
  };

  const handleImportJson = () => {
    try {
      const cp = importCheckpointJson(importJson);
      setCheckpoint(cp);
      setImportJson('');
      setPageSummary({ type: 'success', text: '已导入 checkpoint 统计' });
    } catch (e) {
      setPageSummary({ type: 'error', text: (e as Error).message });
    }
  };

  const copyCheckpoint = async () => {
    await navigator.clipboard.writeText(exportCheckpointJson(checkpoint));
    setPageSummary({ type: 'info', text: 'checkpoint JSON 已复制' });
  };

  const toggleProfile = (id: string) => {
    setProfiles((prev) => (prev.includes(id) ? prev.filter((p) => p !== id) : [...prev, id]));
  };

  const toggleAll = (checked: boolean, list: DiscoverCandidate[]) => {
    setSelected(checked ? new Set(list.map((h) => h.account_code)) : new Set());
  };

  const toggleOne = (code: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  };

  const handleFollow = async () => {
    const codes = [...selected];
    if (!codes.length) return;
    setFollowing(true);
    setPageSummary(null);
    try {
      const res = await followPortfolios(codes);
      setPageSummary({
        type: res.ok ? 'success' : 'error',
        text: res.errors.length ? `${res.message}：${res.errors.join('；')}` : res.message,
      });
      await queryClient.invalidateQueries({ queryKey: ['accounts'] });
      await queryClient.invalidateQueries({ queryKey: ['portfolios-overview'] });
      await queryClient.invalidateQueries({ queryKey: ['portfolios-overview-stats'] });
      const next = removeCandidates(codes);
      setCandidates(next);
      setSelected(new Set());
    } catch (e) {
      setPageSummary({ type: 'error', text: (e as Error).message });
    } finally {
      setFollowing(false);
    }
  };

  const buildCandidateRows = (rows: DiscoverCandidate[]) =>
    rows.map((row) => [
      row.account_code,
      row.account_name,
      (row.matched_profiles ?? []).map((p) => profileLabel(p)).join(' · ') || '—',
      row.latest_nav.toFixed(4),
      `${row.cum_return_pct.toFixed(2)}%`,
      row.latest_trade_time ?? '—',
    ]);

  const CANDIDATE_TABLE_COLS: TableColumn[] = [
    { key: 'code', label: '代码', width: '14%' },
    { key: 'name', label: '名称', width: '18%' },
    { key: 'profile', label: '画像', width: '16%' },
    { key: 'nav', label: '净值', align: 'right', width: '12%' },
    { key: 'cum', label: '累计', align: 'right', width: '10%' },
    { key: 'trade', label: '最近调仓', width: '16%' },
  ];

  const renderCandidateTable = (rows: DiscoverCandidate[], title: string, emptyHint: string) => (
    <SectionCard
      title={title}
      action={
        rows.length > 0 ? (
          <FormControlLabel
            control={
              <Checkbox
                size="small"
                checked={rows.every((r) => selected.has(r.account_code))}
                indeterminate={
                  rows.some((r) => selected.has(r.account_code)) && !rows.every((r) => selected.has(r.account_code))
                }
                onChange={(_, v) => toggleAll(v, rows)}
              />
            }
            label={<Typography sx={{ fontSize: 12 }}>全选</Typography>}
          />
        ) : undefined
      }
      noPadding
    >
      {rows.length === 0 ? (
        <Typography sx={{ fontSize: 13, color: DASHBOARD_THEME.textMuted, px: 2, pb: 2 }}>{emptyHint}</Typography>
      ) : (
        <Box>
          {rows.map((row, rowIndex) => (
            <Box
              key={row.account_code}
              sx={{
                display: 'flex',
                alignItems: 'center',
                gap: 0.5,
                px: 1,
                opacity: row.reviewed ? 0.65 : 1,
                borderBottom: `1px solid ${DASHBOARD_THEME.borderSubtle}`,
                '&:last-child': { borderBottom: 'none' },
              }}
            >
              <Checkbox size="small" checked={selected.has(row.account_code)} onChange={() => toggleOne(row.account_code)} />
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <DataTable compact columns={CANDIDATE_TABLE_COLS} rows={[buildCandidateRows(rows)[rowIndex]]} stickyHeader={false} />
              </Box>
              <Stack direction="row" spacing={0.25} flexShrink={0}>
                <Link href={portfolioUrl(row.account_code)} target="_blank" rel="noreferrer" sx={{ display: 'inline-flex', p: 0.5 }}>
                  <OpenInNewRoundedIcon sx={{ fontSize: 16, color: DASHBOARD_THEME.primary }} />
                </Link>
                <Button
                  size="small"
                  sx={{ minWidth: 0, fontSize: 10, px: 0.5 }}
                  onClick={() => setCandidates(markCandidateReviewed(row.account_code, !row.reviewed))}
                >
                  {row.reviewed ? '未复核' : '已复核'}
                </Button>
              </Stack>
            </Box>
          ))}
        </Box>
      )}
    </SectionCard>
  );

  return (
    <Box sx={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <PageHeader
        title="挖组合"
        icon={<TravelExploreRoundedIcon />}
        meta={
          <Typography component="span" sx={{ fontSize: 12, color: DASHBOARD_THEME.textSecondary }}>
            目录 {catalogCount} · 已挖 {catalogDiscovered} · 未挖 {catalogRemaining}
            {running && summary?.text ? ` · ${summary.text}` : ''}
          </Typography>
        }
        actions={
          <>
            {running && (
              <Button variant="outlined" color="error" size="small" startIcon={<StopRoundedIcon />} onClick={stopScan}>
                停止
              </Button>
            )}
            <Button
              variant="contained"
              size="small"
              disabled={running || catalogCount <= 0 || catalogRemaining <= 0}
              startIcon={running ? <CircularProgress size={14} color="inherit" /> : <PlayArrowRoundedIcon />}
              onClick={handleStart}
            >
              {running ? '扫描中…' : continuousMode ? '连续顺序挖取' : '本批扫描'}
            </Button>
          </>
        }
      />

      <PageContent>
        <Stack spacing={3}>
          <SectionCard title="扫描参数">
            <Grid container spacing={2}>
              <Grid item xs={12} sm={4}>
                <TextField
                  label="本批数量"
                  size="small"
                  fullWidth
                  value={batchSize}
                  onChange={(e) => setBatchSize(e.target.value)}
                  placeholder="留空=连续"
                  helperText={
                    continuousMode
                      ? `连续模式：顺序挖完 ${catalogRemaining} 个未挖过的组合`
                      : `本批顺序取 ${Math.min(catalogRemaining || 500, 500)} 个以内`
                  }
                />
              </Grid>
              <Grid item xs={4} sm={2}>
                <TextField label="调仓≤天" size="small" fullWidth value={inactiveDays} onChange={(e) => setInactiveDays(e.target.value)} />
              </Grid>
              <Grid item xs={4} sm={2}>
                <TextField label="月调仓≤次" size="small" fullWidth value={maxRebalanceMonth} onChange={(e) => setMaxRebalanceMonth(e.target.value)} />
              </Grid>
              <Grid item xs={4} sm={2}>
                <TextField label="新锐累计≥%" size="small" fullWidth value={youngMinCum} onChange={(e) => setYoungMinCum(e.target.value)} />
              </Grid>
              <Grid item xs={12} sm={6}>
                <FormGroup row>
                  {PROFILE_OPTIONS.map((opt) => (
                    <FormControlLabel
                      key={opt.id}
                      control={
                        <Checkbox size="small" checked={profiles.includes(opt.id)} onChange={() => toggleProfile(opt.id)} />
                      }
                      label={<Typography sx={{ fontSize: 12 }}>{opt.label}</Typography>}
                    />
                  ))}
                </FormGroup>
              </Grid>
            </Grid>

            {catalogCount > 0 && (
              <Box sx={{ mt: 2 }}>
                <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.5 }}>
                  <Typography sx={{ fontSize: 12, color: DASHBOARD_THEME.textSecondary }}>catalog 顺序进度</Typography>
                  <Typography sx={{ fontSize: 12, fontWeight: 600, color: DASHBOARD_THEME.primary }}>
                    {catalogPct.toFixed(1)}%
                  </Typography>
                </Stack>
                <LinearProgress variant="determinate" value={catalogPct} sx={{ height: 6, borderRadius: 3 }} />
              </Box>
            )}

            <Stack direction="row" spacing={2} flexWrap="wrap" alignItems="center" sx={{ mt: 2 }}>
              <FormControlLabel
                control={<Checkbox size="small" checked={excludeFollowed} onChange={(_, v) => setExcludeFollowed(v)} />}
                label={<Typography sx={{ fontSize: 12 }}>跳过已关注</Typography>}
              />
              <FormControlLabel
                control={
                  <Checkbox size="small" checked={autoAdvanceCheckpoint} onChange={(_, v) => setAutoAdvanceCheckpoint(v)} />
                }
                label={<Typography sx={{ fontSize: 12 }}>每批结束累计尝试/命中数</Typography>}
              />
              <Typography sx={{ fontSize: 12, color: DASHBOARD_THEME.textMuted }}>
                累计尝试 {checkpoint.total_scanned} · 命中 {checkpoint.total_hits} · 命中率 {hitRate}%
              </Typography>
            </Stack>

            <Stack direction="row" spacing={1} flexWrap="wrap" sx={{ mt: 1.5 }}>
              <Button size="small" variant="outlined" onClick={handleResetDiscovered} disabled={running}>
                重置已挖过
              </Button>
              <Button size="small" variant="outlined" startIcon={<ContentCopyRoundedIcon />} onClick={copyCheckpoint}>
                复制统计
              </Button>
              <Button size="small" variant="text" startIcon={<RestartAltRoundedIcon />} onClick={resetCheckpointStats}>
                重置统计
              </Button>
            </Stack>
          </SectionCard>

          <Accordion
            disableGutters
            elevation={0}
            sx={{
              border: `1px solid ${DASHBOARD_THEME.borderSubtle}`,
              borderRadius: `${DASHBOARD_THEME.radiusMd}px !important`,
              '&:before': { display: 'none' },
              overflow: 'hidden',
            }}
          >
            <AccordionSummary expandIcon={<ExpandMoreRoundedIcon />}>
              <Typography sx={{ fontSize: 14, fontWeight: 600 }}>高级选项</Typography>
            </AccordionSummary>
            <AccordionDetails sx={{ pt: 0 }}>
              <TextField
                label="导入 checkpoint JSON"
                size="small"
                fullWidth
                multiline
                minRows={2}
                value={importJson}
                onChange={(e) => setImportJson(e.target.value)}
              />
              <Button size="small" sx={{ mt: 1 }} onClick={handleImportJson} disabled={!importJson.trim()}>
                导入 checkpoint
              </Button>
            </AccordionDetails>
          </Accordion>

          <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '7fr 5fr' }, gap: 2 }}>
            <Stack spacing={2}>
              {renderCandidateTable(batchHits, `本批候选 (${batchHits.length})`, '本批扫描通过后显示在此')}
              {renderCandidateTable(candidates, `全部候选 (${candidates.length})`, '跨批次累积；点击「已复核」标记人工看过')}
              <Stack direction="row" spacing={1} flexWrap="wrap" alignItems="center">
                <Typography sx={{ fontSize: 12, color: DASHBOARD_THEME.textSecondary }}>
                  加入关注将自动全量同步调仓与官方净值
                </Typography>
                <Button size="small" variant="contained" disabled={!selected.size || following} onClick={handleFollow}>
                  {following ? '同步中…' : `加入关注并同步 (${selected.size})`}
                </Button>
                <Button
                  size="small"
                  color="warning"
                  onClick={() => {
                    if (window.confirm('清空全部候选列表？')) {
                      clearAllCandidates();
                      setCandidates([]);
                    }
                  }}
                >
                  清空候选
                </Button>
                <Button size="small" onClick={clearLogs} disabled={running}>
                  清空日志
                </Button>
              </Stack>
            </Stack>
            <LiveLogPanel logs={logs} running={running} progress={progress} />
          </Box>
        </Stack>
      </PageContent>
    </Box>
  );
}
