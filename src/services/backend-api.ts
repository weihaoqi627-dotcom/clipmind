/**
 * ClipMind 后端 HTTP API 客户端
 * ==============================
 * 桌面端通过此模块调用 ClipMind 云后端（注册/登录/查余额等）。
 * AI 请求走 RPC 代理（Director 通过 base_url 调后端 proxy）。
 */
import * as rpc from './rpc'

// ── 配置 ──
// 可在设置页面修改
let _backendUrl = localStorage.getItem('clipmind_backend_url') || 'http://localhost:8765'

export function getBackendUrl(): string {
  return _backendUrl
}

export function setBackendUrl(url: string) {
  _backendUrl = url
  localStorage.setItem('clipmind_backend_url', url)
}

// ── 认证 ──

let _token: string | null = localStorage.getItem('clipmind_token')
let _user: UserInfo | null = null

export interface UserInfo {
  id: string
  email: string
  display_name: string
  membership: string
  status: string
  free_tier_remaining: number
  prepaid_tokens: number
  total_remaining: number
  concurrency_limit: number
  created_at: string
}

export interface BalanceInfo {
  free_tier_remaining: number
  prepaid_tokens: number
  total_remaining: number
  membership: string
  concurrency_limit: number
}

export function isLoggedIn(): boolean {
  return !!_token
}

export function getToken(): string | null {
  return _token
}

export function getUser(): UserInfo | null {
  return _user
}

async function httpPost(path: string, body: any): Promise<any> {
  const resp = await fetch(`${_backendUrl}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!resp.ok) {
    const text = await resp.text()
    let detail: string
    try {
      detail = JSON.parse(text).detail || text
    } catch {
      detail = text
    }
    throw new Error(detail || `HTTP ${resp.status}`)
  }
  return resp.json()
}

async function httpGet(path: string): Promise<any> {
  const resp = await fetch(`${_backendUrl}${path}`, {
    headers: { Authorization: `Bearer ${_token}` },
  })
  if (!resp.ok) {
    const text = await resp.text()
    throw new Error(text.slice(0, 200))
  }
  return resp.json()
}

// ── API ──

export async function register(email: string, password: string, displayName?: string): Promise<{ access_token: string; user: UserInfo }> {
  const data = await httpPost('/api/auth/register', {
    email,
    password,
    display_name: displayName || '',
  })
  _token = data.access_token
  _user = data.user
  localStorage.setItem('clipmind_token', _token!)
  return data
}

export async function login(email: string, password: string): Promise<{ access_token: string; user: UserInfo }> {
  const data = await httpPost('/api/auth/login', { email, password })
  _token = data.access_token
  _user = data.user
  localStorage.setItem('clipmind_token', _token!)
  return data
}

export function logout() {
  _token = null
  _user = null
  localStorage.removeItem('clipmind_token')
}

export async function fetchBalance(): Promise<BalanceInfo> {
  return httpGet('/api/user/balance')
}

export async function fetchUsage(timeRange: string = 'month', model?: string): Promise<any> {
  return httpPost('/api/user/usage', { time_range: timeRange, model: model || '' })
}

// ── 支付 ──

export async function createPayment(amountYuan: number): Promise<any> {
  const resp = await fetch(`${_backendUrl}/api/payment/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${_token}` },
    body: JSON.stringify({ amount_yuan: amountYuan }),
  })
  if (!resp.ok) {
    const text = await resp.text()
    let detail: string
    try { detail = JSON.parse(text).detail || text } catch { detail = text }
    throw new Error(detail || `HTTP ${resp.status}`)
  }
  return resp.json()
}

export async function checkPayment(orderId: string): Promise<any> {
  return httpGet(`/api/payment/status/${orderId}`)
}

export async function fetchOrders(): Promise<any[]> {
  return httpGet('/api/user/orders')
}

// ── Director 配置 ──
// 登录成功后，将后端 URL + JWT 传给 Director，让它走代理

export function applyToDirector(model?: string) {
  if (!_token) return
  // 直连模式: 不传代理 URL,空字符串让 Director 保持原有 DashScope 直连配置
  // 只传 JWT + backend URL,用于用量上报
  rpc.configure('', _token, model || 'qwen3.6-plus', _backendUrl)
}

// ── 忘记密码 ──

export async function forgotPassword(email: string): Promise<void> {
  await httpPost('/api/auth/forgot-password', { email })
}

export async function resetPassword(email: string, code: string, newPassword: string): Promise<void> {
  await httpPost('/api/auth/reset-password', { email, code, new_password: newPassword })
}
