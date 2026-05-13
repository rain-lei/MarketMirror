export const APP_NAME = 'MarketMirror'
export const APP_VERSION = '2.0.0'
export const APP_DESC = '多类型投资者市场冲击仿真平台'

export const DEMO_ACCOUNTS = [
  { username: 'admin', password: 'Admin@2026', role: '监管管理员', displayName: '系统管理员' },
  { username: 'analyst', password: 'Analyst@2026', role: '风险分析师', displayName: '策略分析员' },
]

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000'

// Theme presets
export const THEME_PRESETS = {
  light: { label: '浅色模式', icon: '☀️', desc: '明亮清晰的视觉体验' },
  dark: { label: '深色模式', icon: '🌙', desc: '护眼的暗色界面' },
  auto: { label: '跟随系统', icon: '💻', desc: '自动匹配系统主题' },
}

// Layout defaults
export const LAYOUT_DEFAULTS = {
  sidebarWidth: 232,
  sidebarCollapsedWidth: 64,
  headerHeight: 48,
  tabHeight: 38,
}
