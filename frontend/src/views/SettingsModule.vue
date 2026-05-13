<script setup>
import { onMounted, ref } from 'vue'
import {
  ApiOutlined,
  BulbOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  DesktopOutlined,
  InfoCircleOutlined,
  SettingOutlined,
} from '@ant-design/icons-vue'
import { APP_NAME, APP_VERSION } from '../config'
import { healthCheck } from '../services/api'
import { useTheme } from '../composables/useTheme'

const { theme, setTheme, themeOptions } = useTheme()
const apiStatus = ref('checking') // 'checking' | 'connected' | 'disconnected'
const apiUrl = ref(import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000')

async function checkApiHealth() {
  apiStatus.value = 'checking'
  try {
    const res = await healthCheck()
    if (res.status === 'ok') {
      apiStatus.value = 'connected'
    } else {
      apiStatus.value = 'disconnected'
    }
  } catch (_) {
    apiStatus.value = 'disconnected'
  }
}

const sysInfo = {
  应用名称: APP_NAME,
  版本号: `v${APP_VERSION}`,
  'API 地址': apiUrl.value,
  '前端框架': 'Vue 3.5 + Vite 5',
  'UI 组件库': 'Ant Design Vue 4',
  '图表引擎': 'Apache ECharts 5',
  '构建工具': 'Vite 5',
  运行环境: import.meta.env.MODE || 'development',
}

onMounted(() => {
  checkApiHealth()
})
</script>

<template>
  <section class="single-module">
    <!-- Theme Settings -->
    <div class="settings-section">
      <a-card :bordered="false" class="glass-card">
        <template #title>
          <div class="card-title"><BulbOutlined /> 主题设置</div>
        </template>
        <div class="theme-option-cards">
          <div
            v-for="opt in themeOptions"
            :key="opt.value"
            class="theme-option-card"
            :class="{ selected: theme === opt.value }"
            @click="setTheme(opt.value)"
          >
            <div class="theme-icon">{{ opt.icon }}</div>
            <div class="theme-label">{{ opt.label }}</div>
            <div class="theme-desc">{{ opt.desc }}</div>
          </div>
        </div>
        <div style="margin-top: 14px; font-size: 13px; color: var(--text-secondary)">
          当前模式：<strong>{{ themeOptions.find(o => o.value === theme)?.label }}</strong>
        </div>
      </a-card>
    </div>

    <!-- API Status + System Info -->
    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px">
      <!-- API Connection -->
      <a-card :bordered="false" class="glass-card">
        <template #title>
          <div class="card-title"><ApiOutlined /> API 连接状态</div>
        </template>

        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 16px">
          <span
            class="api-status-dot"
            :class="apiStatus"
          ></span>
          <span style="font-weight: 500; font-size: 16px">
            <template v-if="apiStatus === 'connected'">
              <CheckCircleOutlined style="color: var(--color-success); margin-right: 4px" />
              已连接
            </template>
            <template v-else-if="apiStatus === 'disconnected'">
              <CloseCircleOutlined style="color: var(--color-danger); margin-right: 4px" />
              未连接
            </template>
            <template v-else>
              <InfoCircleOutlined style="color: var(--color-warning); margin-right: 4px" />
              检测中...
            </template>
          </span>
        </div>

        <div class="system-info-grid">
          <div class="system-info-item">
            <span class="info-label">后端地址</span>
            <span class="info-value">{{ apiUrl }}</span>
          </div>
        </div>

        <a-button
          type="primary"
          size="small"
          :loading="apiStatus === 'checking'"
          @click="checkApiHealth"
          style="margin-top: 12px"
        >
          重新检测
        </a-button>
      </a-card>

      <!-- System Info -->
      <a-card :bordered="false" class="glass-card">
        <template #title>
          <div class="card-title"><DesktopOutlined /> 系统信息</div>
        </template>
        <div class="system-info-grid">
          <div
            v-for="(value, key) in sysInfo"
            :key="key"
            class="system-info-item"
          >
            <span class="info-label">{{ key }}</span>
            <span class="info-value">{{ value }}</span>
          </div>
        </div>
      </a-card>
    </div>
  </section>
</template>
