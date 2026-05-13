import { ref, computed } from 'vue'

// Shared reactive state — singleton across all components
const tabs = ref([])
const activeKey = ref('simulation')

// Default tab that is always open
const HOME_TAB = { key: 'simulation', title: '场景推演', icon: 'DashboardOutlined', closable: false }

const MODULE_META = {
  simulation: { title: '场景推演', icon: 'DashboardOutlined' },
  risk: { title: '风险预警', icon: 'AlertOutlined' },
  audit: { title: '审计中心', icon: 'AuditOutlined' },
  settings: { title: '平台配置', icon: 'SettingOutlined' },
}

export function useTabStore() {
  // Ensure home tab always present
  function ensureHomeTab() {
    if (!tabs.value.find((t) => t.key === 'simulation')) {
      tabs.value.unshift({ ...HOME_TAB })
    }
    if (!activeKey.value) {
      activeKey.value = 'simulation'
    }
  }

  function openTab(key) {
    const meta = MODULE_META[key]
    if (!meta) return

    ensureHomeTab()

    const existing = tabs.value.find((t) => t.key === key)
    if (existing) {
      activeKey.value = key
      return
    }

    tabs.value.push({
      key,
      title: meta.title,
      icon: meta.icon,
      closable: key !== 'simulation',
    })
    activeKey.value = key
  }

  function closeTab(key) {
    const tab = tabs.value.find((t) => t.key === key)
    if (!tab || !tab.closable) return

    const idx = tabs.value.indexOf(tab)
    tabs.value.splice(idx, 1)

    // Activate adjacent tab
    if (activeKey.value === key) {
      if (tabs.value.length === 0) {
        ensureHomeTab()
      } else {
        const newIdx = Math.min(idx, tabs.value.length - 1)
        activeKey.value = tabs.value[newIdx].key
      }
    }
  }

  function closeOthers(key) {
    const tab = tabs.value.find((t) => t.key === key)
    if (!tab) return
    tabs.value = tabs.value.filter((t) => !t.closable || t.key === key)
    activeKey.value = key
  }

  function closeLeft(key) {
    const idx = tabs.value.findIndex((t) => t.key === key)
    if (idx < 0) return
    tabs.value = tabs.value.filter((t, i) => !t.closable || i >= idx)
    activeKey.value = key
  }

  function closeRight(key) {
    const idx = tabs.value.findIndex((t) => t.key === key)
    if (idx < 0) return
    tabs.value = tabs.value.filter((t, i) => !t.closable || i <= idx)
    activeKey.value = key
  }

  function closeAll() {
    tabs.value = tabs.value.filter((t) => !t.closable)
    activeKey.value = tabs.value[0]?.key || 'simulation'
  }

  function closeAllButKey(key) {
    closeOthers(key)
  }

  function refreshTab(key) {
    // Close and immediately reopen to force re-render
    const tab = tabs.value.find((t) => t.key === key)
    if (!tab) return
    // We use a refresh counter to force remount
    if (tab._refreshKey === undefined) {
      tab._refreshKey = 0
    }
    tab._refreshKey++
  }

  function setActiveKey(key) {
    if (tabs.value.find((t) => t.key === key)) {
      activeKey.value = key
    }
  }

  const tabList = computed(() => tabs.value)

  return {
    tabs: tabList,
    activeKey,
    openTab,
    closeTab,
    closeOthers,
    closeLeft,
    closeRight,
    closeAll,
    closeAllButKey,
    refreshTab,
    setActiveKey,
    ensureHomeTab,
    MODULE_META,
  }
}
