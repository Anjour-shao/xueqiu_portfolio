import axios from 'axios';

const api = axios.create({
  baseURL: '/',
  timeout: 15000,
});

export default api;
