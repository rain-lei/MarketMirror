export const APP_NAME = "MarketMirror";

export const DEMO_ACCOUNTS = [
  { username: "admin", password: "Admin@2026", role: "监管管理员", displayName: "系统管理员" },
  { username: "analyst", password: "Analyst@2026", role: "风险分析师", displayName: "策略分析员" }
];

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";
