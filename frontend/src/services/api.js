import { API_BASE_URL } from "../config";

const REQUEST_TIMEOUT = 18000;

async function request(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT);

  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {})
      }
    });

    if (!response.ok) {
      let message = `请求失败 (HTTP ${response.status})`;
      try {
        const body = await response.json();
        message = body.detail || message;
      } catch (_err) {
        // Keep fallback message.
      }
      throw new Error(message);
    }

    return response.json();
  } finally {
    clearTimeout(timer);
  }
}

export function runSimulation(payload) {
  return request("/api/simulate", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function healthCheck() {
  return request("/api/health", { method: "GET" });
}
