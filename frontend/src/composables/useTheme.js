import { ref, watchEffect } from 'vue'

const THEME_KEY = 'marketmirror.theme.v2'
const SETTINGS_KEY = 'marketmirror.settings.v2'

// 'light' | 'dark' | 'auto'
const theme = ref(loadTheme())

function loadTheme() {
  const saved = localStorage.getItem(SETTINGS_KEY)
  if (saved) {
    try {
      const settings = JSON.parse(saved)
      if (settings.themeMode) return settings.themeMode
    } catch (_) { /* ignore */ }
  }
  return localStorage.getItem(THEME_KEY) || 'light'
}

function saveTheme(value) {
  localStorage.setItem(THEME_KEY, value)
}

function applyTheme(value) {
  document.documentElement.setAttribute('data-theme', value)
  saveTheme(value)
}

watchEffect(() => {
  const mode = theme.value
  if (mode === 'auto') {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    document.documentElement.setAttribute('data-theme', 'auto')
    // Also set the actual applied class for systems without @media support
    document.documentElement.style.colorScheme = prefersDark ? 'dark' : 'light'
  } else {
    applyTheme(mode)
    document.documentElement.style.colorScheme = mode
  }
})

// Listen for system preference changes when in auto mode
if (window.matchMedia) {
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (theme.value === 'auto') {
      // Trigger reactivity
      document.documentElement.style.colorScheme =
        window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
    }
  })
}

export function useTheme() {
  const themeOptions = [
    { value: 'light', label: '浅色模式', icon: '☀️', desc: '明亮清晰的视觉体验' },
    { value: 'dark', label: '深色模式', icon: '🌙', desc: '护眼的暗色界面' },
    { value: 'auto', label: '跟随系统', icon: '💻', desc: '自动匹配系统主题' },
  ]

  function setTheme(mode) {
    if (!['light', 'dark', 'auto'].includes(mode)) return
    theme.value = mode
    saveSettings()
  }

  function saveSettings() {
    try {
      const existing = JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}')
      existing.themeMode = theme.value
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(existing))
    } catch (_) { /* ignore */ }
  }

  const isDark = () => {
    if (theme.value === 'auto') {
      return window.matchMedia('(prefers-color-scheme: dark)').matches
    }
    return theme.value === 'dark'
  }

  return {
    theme,
    themeOptions,
    setTheme,
    isDark,
  }
}
