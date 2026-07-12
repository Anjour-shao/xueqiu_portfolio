import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AppShell } from './AppShell';
import { BacktestPage } from '../pages/BacktestPage';
import { ComparePage } from '../pages/ComparePage';
import { HoldingsPage } from '../pages/HoldingsPage';
import { OverviewPage } from '../pages/OverviewPage';
import { DiscoverPage } from '../pages/DiscoverPage';
import { MyPortfolioPage } from '../pages/MyPortfolioPage';
import { StockSummaryPage } from '../pages/StockSummaryPage';
import { SyncDataPage } from '../pages/SyncDataPage';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/overview" replace />} />
          <Route path="overview" element={<OverviewPage />} />
          <Route path="my-portfolio" element={<MyPortfolioPage />} />
          <Route path="portfolio/:accountCode" element={<HoldingsPage />} />
          <Route path="discover" element={<DiscoverPage />} />
          <Route path="stock-summary" element={<StockSummaryPage />} />
          <Route path="sync" element={<SyncDataPage />} />
          <Route path="compare" element={<ComparePage />} />
          <Route path="backtest" element={<BacktestPage />} />
          <Route path="*" element={<Navigate to="/overview" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
