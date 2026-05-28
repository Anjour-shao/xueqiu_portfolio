import axios from 'axios';

export function isApiNotFoundError(err: unknown): boolean {
  return axios.isAxiosError(err) && err.response?.status === 404;
}

export const STALE_BACKEND_HINT =
  '后端 API 版本过旧或未启动。请在 backend 目录运行 python main.py（默认端口 8011），并重启前端 npm run dev。';
