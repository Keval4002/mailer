import axios from "axios";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const API_TOKEN   = process.env.NEXT_PUBLIC_API_TOKEN || "";

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: API_TOKEN
    ? { Authorization: `Bearer ${API_TOKEN}` }
    : {},
});

export default api;