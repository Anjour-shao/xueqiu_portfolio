import CompareArrowsRoundedIcon from '@mui/icons-material/CompareArrowsRounded';
import AssessmentRoundedIcon from '@mui/icons-material/AssessmentRounded';
import DashboardRoundedIcon from '@mui/icons-material/DashboardRounded';
import SyncRoundedIcon from '@mui/icons-material/SyncRounded';
import TravelExploreRoundedIcon from '@mui/icons-material/TravelExploreRounded';
import type { SvgIconComponent } from '@mui/icons-material';

export type NavItem = {
  path: string;
  label: string;
  shortLabel: string;
  icon: SvgIconComponent;
};

export const NAV_ITEMS: NavItem[] = [
  { path: '/overview', label: '总览', shortLabel: '总览', icon: DashboardRoundedIcon },
  { path: '/discover', label: '挖组合', shortLabel: '挖组合', icon: TravelExploreRoundedIcon },
  { path: '/sync', label: '数据同步', shortLabel: '同步', icon: SyncRoundedIcon },
  { path: '/compare', label: '组合对比', shortLabel: '对比', icon: CompareArrowsRoundedIcon },
  { path: '/backtest', label: '抄作业回测', shortLabel: '回测', icon: AssessmentRoundedIcon },
];
