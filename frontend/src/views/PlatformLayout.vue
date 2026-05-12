<script setup>
import {
  AlertOutlined,
  AuditOutlined,
  BulbOutlined,
  DashboardOutlined,
  LogoutOutlined,
  SettingOutlined
} from '@ant-design/icons-vue'
import { useTheme } from '../composables/useTheme'

const props = defineProps({
  session: { type: Object, default: null },
  activeModule: { type: String, default: 'simulation' },
  appName: { type: String, required: true }
})

const emit = defineEmits(['logout', 'navigate'])

const { theme, toggle: toggleTheme } = useTheme()

const modules = [
  { key: 'simulation', name: '场景推演', icon: DashboardOutlined },
  { key: 'risk', name: '风险预警', icon: AlertOutlined },
  { key: 'audit', name: '审计中心', icon: AuditOutlined },
  { key: 'settings', name: '平台配置', icon: SettingOutlined }
]
</script>

<template>
  <a-layout class="platform-layout">
    <a-layout-sider width="240" class="left-sider">
      <div class="sider-brand">
        <div class="logo-mark">MM</div>
        <div>
          <strong>{{ appName }}</strong>
          <p>Enterprise Console</p>
        </div>
      </div>

      <a-menu
        theme="dark"
        mode="inline"
        :selected-keys="[activeModule]"
        @click="({ key }) => emit('navigate', key)"
      >
        <a-menu-item v-for="item in modules" :key="item.key">
          <component :is="item.icon" />
          <span>{{ item.name }}</span>
        </a-menu-item>
      </a-menu>
    </a-layout-sider>

    <a-layout>
      <a-layout-header class="top-header">
        <div>
          <h2>监管智能驾驶舱</h2>
          <p>{{ session.displayName }} · {{ session.role }}</p>
        </div>
        <div class="header-actions">
          <a-button
            size="small"
            type="text"
            @click="toggleTheme"
            :title="theme === 'dark' ? '切换明亮模式' : '切换暗夜模式'"
          >
            <template #icon><BulbOutlined /></template>
            {{ theme === 'dark' ? '亮色' : '暗色' }}
          </a-button>
          <a-button type="default" size="small" @click="emit('logout')">
            <template #icon><LogoutOutlined /></template>
            退出
          </a-button>
        </div>
      </a-layout-header>

      <a-layout-content class="main-content">
        <Transition name="fade-slide" mode="out-in">
          <slot />
        </Transition>
      </a-layout-content>
    </a-layout>
  </a-layout>
</template>
