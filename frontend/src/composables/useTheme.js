import { ref, watchEffect } from 'vue'

const THEME_KEY = 'marketmirror.theme'
const theme = ref(localStorage.getItem(THEME_KEY) || 'light')

function applyTheme(value) {
  document.documentElement.setAttribute('data-theme', value)
}

watchEffect(() => {
  applyTheme(theme.value)
})

export function useTheme() {
  function toggle() {
    theme.value = theme.value === 'dark' ? 'light' : 'dark'
    localStorage.setItem(THEME_KEY, theme.value)
  }

  return { theme, toggle }
}
