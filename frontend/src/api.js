import axios from 'axios';

// In development, the backend is on port 8000. In production (if hosted), this should point to the actual backend URL.
const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const api = axios.create({
  baseURL: API_BASE_URL,
});

export default api;
