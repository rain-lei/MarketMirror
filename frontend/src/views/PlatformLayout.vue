<script setup>
import {
  AlertOutlined,
  AuditOutlined,
  BulbOutlined,
  DashboardOutlined,
  FullscreenExitOutlined,
  FullscreenOutlined,
  LogoutOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  SettingOutlined,
  UserOutlined,
} from '@ant-design/icons-vue'
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import MultiTabBar from '../components/MultiTabBar.vue'
import { useTabStore } from '../composables/useTabStore'
import { useTheme } from '../composables/useTheme'

const props = defineProps({
  session: { type: Object, default: null },
  appName: { type: String, required: true },
})

const emit = defineEmits(['logout', 'navigate'])

const { theme, setTheme, themeOptions, isDark } = useTheme()
const { tabs, activeKey, openTab, closeTab, closeOthers, closeLeft, closeRight, closeAll, refreshTab } = useTabStore()

const collapsed = ref(false)
const isFullscreen = ref(false)

const modules = [
  { key: 'simulation', name: '场景推演', icon: DashboardOutlined },
  { key: 'risk', name: '风险预警', icon: AlertOutlined },
  { key: 'audit', name: '审计中心', icon: AuditOutlined },
  { key: 'settings', name: '平台配置', icon: SettingOutlined },
]

const currentModuleName = computed(() => {
  const mod = modules.find((m) => m.key === activeKey.value)
  return mod ? mod.name : '场景推演'
})

function onMenuClick({ key }) {
  openTab(key)
  emit('navigate', key)
}

function onTabClick(key) {
  emit('navigate', key)
}

function onTabClose(key) {
  closeTab(key)
  emit('navigate', activeKey.value)
}

function onTabCloseOthers(key) {
  closeOthers(key)
  emit('navigate', activeKey.value)
}

function onTabCloseLeft(key) {
  closeLeft(key)
  emit('navigate', activeKey.value)
}

function onTabCloseRight(key) {
  closeRight(key)
  emit('navigate', activeKey.value)
}

function onTabCloseAll() {
  closeAll()
  emit('navigate', activeKey.value)
}

function onTabRefresh(key) {
  refreshTab(key)
  emit('navigate', key)
}

function toggleCollapsed() {
  collapsed.value = !collapsed.value
}

function toggleFullscreen() {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen()
    isFullscreen.value = true
  } else {
    document.exitFullscreen()
    isFullscreen.value = false
  }
}

function onFullscreenChange() {
  isFullscreen.value = !!document.fullscreenElement
}

function handleLogout() {
  emit('logout')
}

onMounted(() => {
  document.addEventListener('fullscreenchange', onFullscreenChange)
})

onBeforeUnmount(() => {
  document.removeEventListener('fullscreenchange', onFullscreenChange)
})
</script>

<template>
  <a-layout class="platform-layout">
    <!-- Sidebar -->
    <a-layout-sider
      :collapsed="collapsed"
      :collapsed-width="64"
      :width="232"
      class="left-sider"
      collapsible
      :trigger="null"
    >
      <div class="sider-brand">
        <div class="logo-mark">MM</div>
        <div class="brand-text">
          <strong>{{ appName }}</strong>
          <span>Enterprise</span>
        </div>
      </div>

      <a-menu
        theme="dark"
        mode="inline"
        :selected-keys="[activeKey]"
        :inline-collapsed="collapsed"
        @click="onMenuClick"
      >
        <a-menu-item v-for="item in modules" :key="item.key">
          <template #icon>
            <component :is="item.icon" />
          </template>
          <span>{{ item.name }}</span>
        </a-menu-item>
      </a-menu>
    </a-layout-sider>

    <!-- Main Area -->
    <a-layout>
      <!-- Header -->
      <a-layout-header class="top-header">
        <div class="top-header-left">
          <span class="collapse-btn" @click="toggleCollapsed">
            <MenuUnfoldOutlined v-if="collapsed" />
            <MenuFoldOutlined v-else />
          </span>
          <a-breadcrumb class="top-header-breadcrumb">
            <a-breadcrumb-item>
              <DashboardOutlined />
            </a-breadcrumb-item>
            <a-breadcrumb-item>{{ appName }}</a-breadcrumb-item>
            <a-breadcrumb-item>{{ currentModuleName }}</a-breadcrumb-item>
          </a-breadcrumb>
        </div>

        <div class="top-header-right">
          <!-- Theme Toggle -->
          <a-dropdown trigger="click">
            <button class="header-action-btn" title="切换主题">
              <BulbOutlined />
            </button>
            <template #overlay>
              <a-menu @click="({ key }) => setTheme(key)">
                <a-menu-item v-for="opt in themeOptions" :key="opt.value">
                  <span style="margin-right: 8px">{{ opt.icon }}</span>
                  <span>{{ opt.label }}</span>
                  <span style="margin-left: 12px; font-size: 12px; color: var(--text-tertiary)">{{ opt.desc }}</span>
                </a-menu-item>
              </a-menu>
            </template>
          </a-dropdown>

          <!-- Fullscreen -->
          <button class="header-action-btn" title="全屏" @click="toggleFullscreen">
            <FullscreenOutlined v-if="!isFullscreen" />
            <FullscreenExitOutlined v-else />
          </button>

          <!-- User -->
          <a-dropdown trigger="click">
            <div class="user-avatar-dropdown">
              <a-avatar :size="28" style="background: linear-gradient(135deg, #3b82f6, #06b6d4); flex-shrink: 0;">
                <template #icon><UserOutlined /></template>
              </a-avatar>
              <div class="user-info">
                <div class="user-name">{{ session.displayName }}</div>
                <div class="user-role">{{ session.role }}</div>
              </div>
            </div>
            <template #overlay>
              <a-menu @click="handleLogout">
                <a-menu-item key="logout">
                  <LogoutOutlined />
                  <span style="margin-left: 8px">退出登录</span>
                </a-menu-item>
              </a-menu>
            </template>
          </a-dropdown>
        </div>
      </a-layout-header>

      <!-- Multi-Tab Bar -->
      <MultiTabBar
        :tabs="tabs"
        :active-key="activeKey"
        @tab-click="onTabClick"
        @tab-close="onTabClose"
        @tab-close-others="onTabCloseOthers"
        @tab-close-left="onTabCloseLeft"
        @tab-close-right="onTabCloseRight"
        @tab-close-all="onTabCloseAll"
        @tab-refresh="onTabRefresh"
      />

      <!-- Content -->
      <a-layout-content class="main-content">
        <Transition name="fade-slide" mode="out-in">
          <div :key="activeKey">
            <slot />
          </div>
        </Transition>
      </a-layout-content>
    </a-layout>
  </a-layout>
</template>
