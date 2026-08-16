import axios, { type AxiosRequestConfig } from "axios";

const configuredApiBaseUrl =
  typeof import.meta.env.VITE_API_BASE_URL === "string"
    ? import.meta.env.VITE_API_BASE_URL.trim()
    : "";

export const API_BASE_URL =
  configuredApiBaseUrl ||
  (import.meta.env.PROD ? "" : "http://127.0.0.1:8000");
const TOKEN_KEY = "tupai:access-token";
const SCHEDULE_SET_KEY = "tupai:schedule-set-id";

/**
 * The selected schedule set is shared by the shell and every API request.
 * Keeping it in localStorage means a refresh does not silently switch the
 * operator back to another timetable.
 */
export const scheduleSetStore = {
  get: () => window.localStorage.getItem(SCHEDULE_SET_KEY),
  set: (id: string) => {
    window.localStorage.setItem(SCHEDULE_SET_KEY, id);
    window.dispatchEvent(new CustomEvent("tupai:schedule-set-changed", { detail: id }));
  },
  clear: () => {
    window.localStorage.removeItem(SCHEDULE_SET_KEY);
    window.dispatchEvent(new Event("tupai:schedule-set-changed"));
  },
};

export const http = axios.create({ baseURL: API_BASE_URL, timeout: 180_000 });

http.interceptors.request.use((config) => {
  const token = window.localStorage.getItem(TOKEN_KEY);
  if (token) config.headers.Authorization = `Bearer ${token}`;
  const scheduleSetId = scheduleSetStore.get();
  if (scheduleSetId) config.headers["X-Schedule-Set-Id"] = scheduleSetId;
  return config;
});

http.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401 && !error.config?.url?.includes("/auth/token")) {
      window.localStorage.removeItem(TOKEN_KEY);
      window.dispatchEvent(new Event("tupai:unauthorized"));
    }
    return Promise.reject(error);
  },
);

export const customInstance = async <T>(config: AxiosRequestConfig): Promise<T> => {
  const response = await http.request<T>(config);
  return response.data;
};

export const authStore = {
  get: () => window.localStorage.getItem(TOKEN_KEY),
  set: (token: string) => window.localStorage.setItem(TOKEN_KEY, token),
  clear: () => {
    window.localStorage.removeItem(TOKEN_KEY);
    scheduleSetStore.clear();
  },
};
