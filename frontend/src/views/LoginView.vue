<script setup>
import { reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { KeyOutlined, LoginOutlined, UserOutlined } from '@ant-design/icons-vue'
import { APP_NAME, APP_DESC, DEMO_ACCOUNTS } from '../config'
import { login } from '../services/auth'

const emit = defineEmits(['login'])

const loginForm = reactive({
  username: 'admin',
  password: 'Admin@2026',
})
const showPassword = ref(false)
const loginError = ref('')
const loading = ref(false)

const ERROR_MESSAGES = {
  USERNAME_REQUIRED: '请输入账号后再登录',
  PASSWORD_REQUIRED: '请输入密码后再登录',
  USER_NOT_FOUND: '账号不存在，请联系管理员开通',
  PASSWORD_INCORRECT: '密码错误，请检查大小写',
  SESSION_INVALID: '会话异常，请重新登录',
}

const features = [
  '极端政策模拟', '多 Agent 复现',
  '风险看板', '审计留痕',
]

async function doLogin() {
  loginError.value = ''
  loading.value = true
  // Small delay for UX feedback
  await new Promise((r) => setTimeout(r, 300))
  try {
    const session = login(loginForm.username, loginForm.password)
    emit('login', session)
    message.success('欢迎使用 MarketMirror')
  } catch (err) {
    loginError.value = ERROR_MESSAGES[err.code] || err.message || '登录失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

function fillDemoAccount(account) {
  loginForm.username = account.username
  loginForm.password = account.password
  loginError.value = ''
}
</script>

<template>
  <section class="login-scene">
    <!-- Brand Splash -->
    <div class="login-splash">
      <h1>{{ APP_NAME }}</h1>
      <p class="splash-subtitle">{{ APP_DESC }} — 基于文本信息驱动的多类型投资者市场冲击仿真系统</p>
      <div class="login-features">
        <div v-for="item in features" :key="item" class="feature-item">
          <span class="feature-dot"></span>
          {{ item }}
        </div>
      </div>
    </div>

    <!-- Login Card -->
    <a-card class="login-card" :bordered="false">
      <template #title>
        <div class="card-title"><LoginOutlined /> 登录平台</div>
      </template>

      <a-form layout="vertical" @submit.prevent="doLogin">
        <a-form-item label="账号">
          <a-input
            v-model:value="loginForm.username"
            placeholder="请输入账号"
            size="large"
            allow-clear
            @pressEnter="doLogin"
          >
            <template #prefix><UserOutlined style="color: var(--text-tertiary)" /></template>
          </a-input>
        </a-form-item>

        <a-form-item label="密码">
          <a-input
            v-model:value="loginForm.password"
            :type="showPassword ? 'text' : 'password'"
            placeholder="请输入密码"
            size="large"
            @pressEnter="doLogin"
          >
            <template #prefix><KeyOutlined style="color: var(--text-tertiary)" /></template>
          </a-input>
          <label class="password-toggle">
            <input v-model="showPassword" type="checkbox" />
            <span>显示密码</span>
          </label>
        </a-form-item>

        <a-button
          type="primary"
          size="large"
          block
          :loading="loading"
          @click="doLogin"
          style="height: 44px; font-weight: 600; border-radius: var(--border-radius)"
        >
          登 录 平 台
        </a-button>
      </a-form>

      <a-alert
        v-if="loginError"
        type="error"
        :message="loginError"
        show-icon
        class="mt-12"
        closable
        @close="loginError = ''"
      />

      <!-- Demo Accounts -->
      <div class="demo-box">
        <h4>演示账号</h4>
        <p
          v-for="acc in DEMO_ACCOUNTS"
          :key="acc.username"
          class="demo-account-line"
        >
          {{ acc.username }} / {{ acc.password }} — {{ acc.role }}
        </p>
        <a-space wrap style="margin-top: 8px">
          <a-button
            v-for="acc in DEMO_ACCOUNTS"
            :key="`fill-${acc.username}`"
            size="small"
            @click="fillDemoAccount(acc)"
          >
            一键填充 {{ acc.username }}
          </a-button>
        </a-space>
      </div>
    </a-card>
  </section>
</template>
