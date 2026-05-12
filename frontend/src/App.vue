<script setup>
import { message } from 'ant-design-vue'
import { computed, onMounted, ref } from 'vue'
import { usePlatformStore } from './composables/usePlatformStore'
import { login, logout, restoreSession } from './services/auth'
import AuditCenterModule from './views/AuditCenterModule.vue'
import LoginView from './views/LoginView.vue'
import PlatformLayout from './views/PlatformLayout.vue'
import RiskWarningModule from './views/RiskWarningModule.vue'
import SettingsModule from './views/SettingsModule.vue'
import SimulationModule from './views/SimulationModule.vue'

const { APP_NAME, addLog } = usePlatformStore()

const session = ref(null)
const activeModule = ref('simulation')

const isLoggedIn = computed(() => Boolean(session.value))

function handleLogin(sess) {
  session.value = sess
  addLog('系统', `用户 ${sess.username} 登录成功`)
}

function handleLogout() {
  if (session.value) {
    addLog('系统', `用户 ${session.value.username} 退出系统`)
    message.info('已退出')
  }
  logout()
  session.value = null
  activeModule.value = 'simulation'
}

function navigateTo(key) {
  activeModule.value = key
}

onMounted(() => {
  try {
    const sess = restoreSession()
    if (sess) {
      session.value = sess
      addLog('系统', '会话已恢复')
    } else {
      addLog('系统', '平台初始化完成')
    }
  } catch (err) {
    addLog('鉴权', err.message || '会话异常')
  }
})
</script>

<template>
  <div class="app-shell">
    <LoginView v-if="!isLoggedIn" @login="handleLogin" />

    <PlatformLayout
      v-else
      :session="session"
      :active-module="activeModule"
      :app-name="APP_NAME"
      @logout="handleLogout"
      @navigate="navigateTo"
    >
      <SimulationModule v-if="activeModule === 'simulation'" key="simulation" />
      <RiskWarningModule v-else-if="activeModule === 'risk'" key="risk" />
      <AuditCenterModule v-else-if="activeModule === 'audit'" key="audit" />
      <SettingsModule v-else key="settings" />
    </PlatformLayout>
  </div>
</template>
