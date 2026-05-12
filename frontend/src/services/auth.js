import { DEMO_ACCOUNTS } from "../config";

const SESSION_KEY = "marketmirror.session.v2";

function createAuthError(code, message) {
  const error = new Error(message);
  error.code = code;
  return error;
}

export function login(username, password) {
  const normalizedUsername = String(username || "").trim().toLowerCase();
  const normalizedPassword = String(password || "");

  if (!normalizedUsername) {
    throw createAuthError("USERNAME_REQUIRED", "请输入账号");
  }

  if (!normalizedPassword) {
    throw createAuthError("PASSWORD_REQUIRED", "请输入密码");
  }

  const user = DEMO_ACCOUNTS.find((item) => item.username === normalizedUsername);

  if (!user) {
    throw createAuthError("USER_NOT_FOUND", "账号不存在，请检查后重试");
  }

  if (user.password !== normalizedPassword) {
    throw createAuthError("PASSWORD_INCORRECT", "密码错误，请重新输入");
  }

  const session = {
    username: user.username,
    role: user.role,
    displayName: user.displayName,
    loginAt: Date.now()
  };

  localStorage.setItem(SESSION_KEY, JSON.stringify(session));
  return session;
}

export function restoreSession() {
  const raw = localStorage.getItem(SESSION_KEY);
  if (!raw) return null;

  try {
    const session = JSON.parse(raw);
    if (!session || !session.username || !session.role || !session.displayName) {
      throw createAuthError("SESSION_INVALID", "会话数据不完整，已清理，请重新登录");
    }
    return session;
  } catch (_err) {
    localStorage.removeItem(SESSION_KEY);
    throw createAuthError("SESSION_INVALID", "会话异常，请重新登录");
  }
}

export function logout() {
  localStorage.removeItem(SESSION_KEY);
}
