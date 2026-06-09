<template>
  <Teleport to="body">
    <div class="auth-overlay" v-if="visible">
      <div class="auth-panel">
        <div class="auth-header">
          <h2>{{ headerTitle }}</h2>
          <p class="auth-sub">ClipMind 云端服务</p>
        </div>

        <!-- 登录 / 注册 切换 -->
        <div class="auth-mode-tabs" v-if="mode === 'login' || mode === 'register'">
          <button :class="{ active: mode === 'login' }" @click="mode = 'login'">登录</button>
          <button :class="{ active: mode === 'register' }" @click="mode = 'register'">注册</button>
        </div>

        <!-- ── 登录 / 注册 表单 ── -->
        <template v-if="mode === 'login' || mode === 'register'">
          <div class="auth-fields">
            <input v-model="email" type="email" placeholder="邮箱" @keyup.enter="submit" :disabled="loading" />
            <input v-model="password" type="password" :placeholder="mode === 'login' ? '密码' : '密码（至少6位）'" @keyup.enter="submit" :disabled="loading" />
            <input v-if="mode === 'register'" v-model="displayName" type="text" placeholder="昵称（可选）" @keyup.enter="submit" :disabled="loading" />
            <input v-model="backendUrl" type="text" placeholder="后端地址" @keyup.enter="submit" :disabled="loading" />
          </div>

          <div class="auth-forgot" v-if="mode === 'login'">
            <button class="link-btn" @click="switchToForgot">忘记密码?</button>
          </div>
        </template>

        <!-- ── 忘记密码 表单 ── -->
        <template v-if="mode === 'forgot'">
          <div class="auth-fields">
            <input v-model="email" type="email" placeholder="输入注册邮箱" :disabled="codeSent || loading" />
            <template v-if="codeSent">
              <input v-model="resetCode" type="text" placeholder="验证码" maxlength="6" @keyup.enter="doReset" :disabled="loading" />
              <input v-model="newPassword" type="password" placeholder="新密码（至少6位）" @keyup.enter="doReset" :disabled="loading" />
            </template>
          </div>

          <div class="auth-actions">
            <template v-if="!codeSent">
              <button class="auth-btn-primary" @click="doSendCode" :disabled="loading || !email">
                {{ loading ? '发送中...' : '发送验证码' }}
              </button>
            </template>
            <template v-else>
              <button class="auth-btn-primary" @click="doReset" :disabled="loading || !resetCode || !newPassword">
                {{ loading ? '重置中...' : '重置密码' }}
              </button>
            </template>
          </div>

          <div class="auth-forgot" style="margin-top:12px;text-align:center;">
            <button class="link-btn" @click="backToLogin">← 返回登录</button>
          </div>
        </template>

        <!-- 错误提示 -->
        <div class="auth-error" v-if="error">{{ error }}</div>

        <!-- 成功提示 -->
        <div class="auth-success" v-if="successMsg">{{ successMsg }}</div>

        <!-- 主操作按钮（登录/注册） -->
        <div class="auth-actions" v-if="mode === 'login' || mode === 'register'">
          <button class="auth-btn-primary" @click="submit" :disabled="loading">
            {{ loading ? '处理中...' : (mode === 'login' ? '登录' : '注册') }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import * as backendApi from '../services/backend-api'

const props = withDefaults(defineProps<{
  visible: boolean
}>(), {})

const emit = defineEmits<{
  done: [user: any]
}>()

// ── 模式 ──
type Mode = 'login' | 'register' | 'forgot'
const mode = ref<Mode>('login')

// ── 表单字段 ──
const email = ref('')
const password = ref('')
const displayName = ref('')
const backendUrl = ref(backendApi.getBackendUrl())
const resetCode = ref('')
const newPassword = ref('')

const loading = ref(false)
const error = ref('')
const successMsg = ref('')

// 验证码已发送标记
const codeSent = ref(false)

const headerTitle = computed(() => {
  if (mode.value === 'forgot') return '重置密码'
  return mode.value === 'login' ? '登录' : '注册'
})

// ── 工具函数 ──

function validateEmail(v: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.trim())
}

function friendlyError(msg: string): string {
  const m = msg.toLowerCase()
  if (m.includes('401') || m.includes('unauthorized') || m.includes('invalid')) {
    return '邮箱或密码错误'
  }
  if (m.includes('409') || m.includes('already') || m.includes('exists') || m.includes('registered')) {
    return '该邮箱已注册，请直接登录'
  }
  if (m.includes('402') || m.includes('overdue') || m.includes('arrearage')) {
    return '账户欠费，请充值后继续使用'
  }
  if (m.includes('503') || m.includes('unavailable') || m.includes('not configured')) {
    return '服务暂不可用，请稍后再试'
  }
  if (m.includes('422') || m.includes('validation') || m.includes('invalid email')) {
    return '邮箱格式不正确'
  }
  if (m.includes('timeout') || m.includes('timed out') || m.includes('econnrefused') || m.includes('fetch failed')) {
    return '无法连接到服务器，请检查网络或后端地址'
  }
  return msg.length > 80 ? msg.slice(0, 80) + '…' : msg
}

function resetForm() {
  error.value = ''
  successMsg.value = ''
  resetCode.value = ''
  newPassword.value = ''
  codeSent.value = false
}

function switchToForgot() {
  resetForm()
  // 保留已填的邮箱
  mode.value = 'forgot'
}

function backToLogin() {
  resetForm()
  mode.value = 'login'
}

// ── 登录 / 注册 ──

async function submit() {
  if (!email.value || !password.value) {
    error.value = '请填写邮箱和密码'
    return
  }
  if (!validateEmail(email.value)) {
    error.value = '邮箱格式不正确，请检查'
    return
  }
  if (mode.value === 'register' && password.value.length < 6) {
    error.value = '密码至少6位'
    return
  }

  backendApi.setBackendUrl(backendUrl.value)
  loading.value = true
  error.value = ''

  try {
    let data: any
    if (mode.value === 'login') {
      data = await backendApi.login(email.value, password.value)
    } else {
      data = await backendApi.register(email.value, password.value, displayName.value)
    }
    backendApi.applyToDirector()
    emit('done', data.user)
  } catch (e: any) {
    error.value = friendlyError(e?.message || '操作失败')
  } finally {
    loading.value = false
  }
}

// ── 发送验证码 ──

async function doSendCode() {
  if (!email.value) {
    error.value = '请输入邮箱'
    return
  }
  if (!validateEmail(email.value)) {
    error.value = '邮箱格式不正确'
    return
  }

  backendApi.setBackendUrl(backendUrl.value)
  loading.value = true
  error.value = ''
  successMsg.value = ''

  try {
    await backendApi.forgotPassword(email.value)
    codeSent.value = true
    successMsg.value = '验证码已发送到您的邮箱，请查收'
  } catch (e: any) {
    error.value = friendlyError(e?.message || '发送失败')
  } finally {
    loading.value = false
  }
}

// ── 重置密码 ──

async function doReset() {
  if (!resetCode.value || resetCode.value.length !== 6) {
    error.value = '请输入6位验证码'
    return
  }
  if (!newPassword.value || newPassword.value.length < 6) {
    error.value = '新密码至少6位'
    return
  }

  backendApi.setBackendUrl(backendUrl.value)
  loading.value = true
  error.value = ''
  successMsg.value = ''

  try {
    await backendApi.resetPassword(email.value, resetCode.value, newPassword.value)
    successMsg.value = '密码重置成功！请使用新密码登录'
    // 3 秒后自动返回登录
    setTimeout(() => {
      backToLogin()
    }, 3000)
  } catch (e: any) {
    error.value = friendlyError(e?.message || '重置失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.auth-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.65);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  backdrop-filter: blur(4px);
}

.auth-panel {
  background: var(--surface-glass, rgba(19, 19, 22, 0.95));
  border: 1px solid var(--border, #27272A);
  border-radius: 12px;
  padding: 32px;
  width: 380px;
  max-width: 90vw;
  backdrop-filter: blur(20px);
}

.auth-header {
  text-align: center;
  margin-bottom: 24px;
}
.auth-header h2 {
  font-size: 20px;
  font-weight: 600;
  color: #FAFAFA;
  margin: 0;
}
.auth-sub {
  font-size: 12px;
  color: #A1A1AA;
  margin: 6px 0 0;
}

.auth-mode-tabs {
  display: flex;
  gap: 0;
  margin-bottom: 20px;
  background: #1A1A1F;
  border-radius: 8px;
  overflow: hidden;
}
.auth-mode-tabs button {
  flex: 1;
  padding: 8px;
  border: none;
  background: transparent;
  color: #A1A1AA;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}
.auth-mode-tabs button.active {
  background: #27272A;
  color: #FAFAFA;
  font-weight: 500;
}

.auth-fields {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 16px;
}
.auth-fields input {
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid #27272A;
  background: #1A1A1F;
  color: #E4E4E7;
  font-size: 13px;
  outline: none;
  transition: border-color 0.15s;
}
.auth-fields input:focus {
  border-color: #52525B;
}
.auth-fields input::placeholder {
  color: #52525B;
}

.auth-forgot {
  margin-bottom: 16px;
}
.link-btn {
  background: none;
  border: none;
  color: #A78BFA;
  font-size: 12px;
  cursor: pointer;
  padding: 0;
  text-decoration: underline;
  text-underline-offset: 2px;
}
.link-btn:hover {
  color: #C4B5FD;
}

.auth-error {
  color: #FCA5A5;
  font-size: 12px;
  margin-bottom: 12px;
  padding: 8px 10px;
  background: rgba(220, 38, 38, 0.1);
  border-radius: 6px;
}

.auth-success {
  color: #86EFAC;
  font-size: 12px;
  margin-bottom: 12px;
  padding: 8px 10px;
  background: rgba(34, 197, 94, 0.1);
  border-radius: 6px;
}

.auth-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.auth-btn-primary {
  padding: 10px;
  border: none;
  border-radius: 8px;
  background: #7C3AED;
  color: #fff;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s;
}
.auth-btn-primary:hover { background: #6D28D9; }
.auth-btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
