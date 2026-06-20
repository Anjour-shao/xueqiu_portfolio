import api from './client';
import {
  CopyRebalancePlanResponse,
  PersonalAccountResponse,
  StrategyCatalogItem,
} from '../types';

export async function fetchPersonalAccount() {
  const res = await api.get<PersonalAccountResponse>('/api/personal-account');
  return res.data;
}

export async function updatePersonalCash(cash: number) {
  const res = await api.put<PersonalAccountResponse>('/api/personal-account/cash', { cash });
  return res.data;
}

export async function updatePersonalStrategy(strategy_id: string) {
  const res = await api.put<PersonalAccountResponse>('/api/personal-account/strategy', { strategy_id });
  return res.data;
}

export async function executePersonalTrade(payload: {
  action: string;
  ts_code: string;
  shares: number;
  price?: number;
  stock_name?: string;
}) {
  const res = await api.post<PersonalAccountResponse>('/api/personal-account/trade', payload);
  return res.data;
}

export async function fetchCopyRebalancePlan(strategy_id?: string) {
  const res = await api.get<CopyRebalancePlanResponse>('/api/personal-account/rebalance-plan', {
    params: strategy_id ? { strategy_id } : undefined,
  });
  return res.data;
}

export async function fetchBacktestStrategies() {
  const res = await api.get<StrategyCatalogItem[]>('/api/backtest-strategies');
  return res.data;
}
