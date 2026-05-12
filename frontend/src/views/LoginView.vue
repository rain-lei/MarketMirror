<script setup>
import { reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { LoginOutlined } from '@ant-design/icons-vue'
import { APP_NAME, DEMO_ACCOUNTS } from '../config'
import { login } from '../services/auth'

const emit = defineEmits(['login'])

const loginForm = reactive({
  username: 'admin',
  password: 'Admin@2026'
})
const showPassword = ref(false)
const loginError = ref('')

const ERROR_MESSAGES = {
  USERNAME_REQUIRED: '请输入账号后再登录',
  PASSWORD_REQUIRED: '请输入密码后再登录',
  USER_NOT_FOUND: '账号不存在，请联系管理员开通',
  PASSWORD_INCORRECT: '密码错误，请检查大小写',
  SESSION_INVALID: '会话异常，请重新登录'
}

function doLogin() {
  loginError.value = ''
  try {
    const session = login(loginForm.username, loginForm.password)
    emit('login', session)
    message.success('登录成功')
  } catch (err) {
    loginError.value = ERROR_MESSAGES[err.code] || err.message || '登录失败，请稍后重试'
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
    <div class="login-splash">
      <h1>{{ APP_NAME }}</h1>
      <p>监管科技动态沙盒平台，支持冲击模拟、风险预警和审计追溯。</p>
      <div class="chip-grid">
        <span>极端政策模拟</span>
        <span>多 Agent 复现</span>
        <span>风险看板</span>
        <span>审计留痕</span>
      </div>
    </div>

    <a-card class="login-card" :bordered="false">
      <template #title>
        <div class="card-title"><LoginOutlined /> 账号登录</div>
      </template>
      <a-form layout="vertical" @submit.prevent>
        <a-form-item label="账号">
          <a-input
            v-model:value="loginForm.username"
            placeholder="请输入账号"
            allow-clear
            @pressEnter="doLogin"
          />
        </a-form-item>
        <a-form-item label="密码">
          <a-input
            v-model:value="loginForm.password"
            :type="showPassword ? 'text' : 'password'"
            placeholder="请输入密码"
            @pressEnter="doLogin"
          />
          <label class="password-toggle">
            <input v-model="showPassword" type="checkbox" />
            <span>显示密码</span>
          </label>
        </a-form-item>
        <a-button type="primary" size="large" block @click="doLogin">登录平台</a-button>
      </a-form>

      <a-alert v-if="loginError" type="error" :message="loginError" show-icon class="mt-12" />

      <div class="demo-box">
        <h4>演示账号</h4>
        <p v-for="acc in DEMO_ACCOUNTS" :key="acc.username">{{ acc.username }} / {{ acc.password }} / {{ acc.role }}</p>
        <a-space wrap>
          <a-button
            v-for="acc in DEMO_ACCOUNTS"
            :key="`${acc.username}-fill`"
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
