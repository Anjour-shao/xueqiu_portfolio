import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AppShell } from './AppShell';
import { BacktestPage } from '../pages/BacktestPage';
import { DiscoverPage } from '../pages/DiscoverPage';
import { HoldingsPage } from '../pages/HoldingsPage';
import { OverviewPage } from '../pages/OverviewPage';
import { SyncDataPage } from '../pages/SyncDataPage';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/overview" replace />} />
          <Route path="overview" element={<OverviewPage />} />
          <Route path="portfolio/:accountCode" element={<HoldingsPage />} />
          <Route path="sync" element={<SyncDataPage />} />
          <Route path="discover" element={<DiscoverPage />} />
          <Route path="backtest" element={<BacktestPage />} />
          <Route path="*" element={<Navigate to="/overview" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
