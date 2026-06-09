<template>
  <div class="account-page">
    <!-- 顶部 -->
    <div class="page-header">
      <h1>账户</h1>
    </div>

    <!-- Tab 切换 -->
    <div class="tab-bar">
      <button
        v-for="t in tabs"
        :key="t.key"
        class="tab-btn"
        :class="{ active: activeTab === t.key }"
        @click="switchTab(t.key)"
      >{{ t.label }}</button>
    </div>

    <!-- ──── Tab: 余额 ──── -->
    <div v-if="activeTab === 'balance'" class="tab-content">
      <div v-if="balanceLoading" class="loading-row"><span class="spinner"></span> 加载中...</div>
      <template v-else-if="balance">
        <div class="balance-main-card">
          <div class="balance-label">可用额度</div>
          <div class="balance-number">{{ formatTokens(balance.total_remaining) }}</div>
          <div class="balance-sub">Token</div>
        </div>
        <div class="balance-detail">
          <div class="detail-row">
            <span class="detail-label">免费剩余</span>
            <span class="detail-value free">{{ formatTokens(balance.free_tier_remaining) }}</span>
          </div>
          <div class="detail-row">
            <span class="detail-label">预付额度</span>
            <span class="detail-value prepaid">{{ formatTokens(balance.prepaid_tokens) }}</span>
          </div>
          <div class="detail-row">
            <span class="detail-label">会员等级</span>
            <span class="detail-value">{{ balance.membership === 'premium' ? '高级会员' : '免费用户' }}</span>
          </div>
          <div class="detail-row">
            <span class="detail-label">并发限制</span>
            <span class="detail-value">{{ balance.concurrency_limit }} 路</span>
          </div>
        </div>
        <div class="price-note">1 元 = {{ formatTokens(configTokenPrice) }} Token</div>
      </template>
    </div>

    <!-- ──── Tab: 消耗记录 ──── -->
    <div v-if="activeTab === 'usage'" class="tab-content">
      <!-- 时间范围 -->
      <div class="usage-controls">
        <button
          v-for="r in timeRanges"
          :key="r.key"
          class="range-btn"
          :class="{ active: usageRange === r.key }"
          @click="switchRange(r.key)"
        >{{ r.label }}</button>
        <button class="refresh-btn" @click="loadUsage" :disabled="usageLoading">刷新</button>
      </div>

      <div v-if="usageLoading" class="loading-row"><span class="spinner"></span> 加载中...</div>
      <template v-else-if="usageStats">
        <!-- 汇总 -->
        <div class="usage-summary">
          <div class="summary-item">
            <span class="sum-label">总消耗</span>
            <span class="sum-value">{{ formatTokens(usageStats.total_tokens) }}</span>
          </div>
          <div class="summary-item">
            <span class="sum-label">总花费</span>
            <span class="sum-value cost">{{ usageStats.total_cost_yuan }} 元</span>
          </div>
        </div>

        <!-- 按模型汇总 -->
        <div v-if="usageStats.by_model?.length" class="usage-section">
          <div class="section-title">按模型</div>
          <div class="model-breakdown">
            <div v-for="m in usageStats.by_model" :key="m.model" class="model-row">
              <span class="model-name">{{ m.model }}</span>
              <span class="model-tokens">{{ formatTokens(m.tokens) }}</span>
              <span class="model-cost">{{ m.cost_yuan }} 元</span>
            </div>
          </div>
        </div>

        <!-- 详细记录 -->
        <div v-if="usageStats.records?.length" class="usage-section">
          <div class="section-title">最近记录</div>
          <div class="record-table">
            <div class="record-header">
              <span>模型</span>
              <span>输入</span>
              <span>输出</span>
              <span>总计</span>
              <span>花费</span>
              <span>时间</span>
            </div>
            <div v-for="r in usageStats.records" :key="r.time" class="record-row">
              <span class="cell-model" :title="r.model">{{ r.model }}</span>
              <span>{{ r.tokens_in }}</span>
              <span>{{ r.tokens_out }}</span>
              <span>{{ r.tokens_total }}</span>
              <span class="cell-cost">{{ r.cost_yuan }} 元</span>
              <span class="cell-time">{{ formatTime(r.time) }}</span>
            </div>
          </div>
        </div>
        <div v-else class="empty-hint">暂无消耗记录</div>
      </template>
    </div>

    <!-- ──── Tab: 充值 ──── -->
    <div v-if="activeTab === 'recharge'" class="tab-content">
      <!-- 金额选择 -->
      <div class="recharge-section">
        <div class="section-title">选择充值金额</div>
        <div class="amount-presets">
          <button
            v-for="a in presetAmounts"
            :key="a"
            class="amount-btn"
            :class="{ active: rechargeAmount === a }"
            @click="rechargeAmount = a"
          >{{ a }} 元</button>
        </div>
        <div class="custom-amount">
          <input v-model.number="customAmount" type="number" min="1" placeholder="自定义金额" @input="onCustomInput" />
          <span class="amount-unit">元</span>
        </div>
        <div class="token-preview">
          可获得 <strong>{{ formatTokens(rechargeAmount * configTokenPrice) }}</strong> Token
        </div>
      </div>

      <!-- 二维码区域 -->
      <div v-if="qrUrl" class="qr-section">
        <div class="section-title">扫码支付</div>
        <div class="qr-container">
          <img :src="qrUrl" class="qr-img" />
          <div v-if="pollStatus === 'pending'" class="qr-status">
            <span class="spinner"></span> 等待支付...
          </div>
          <div v-else-if="pollStatus === 'paid'" class="qr-status paid">✅ 支付成功！</div>
          <div v-else-if="pollStatus === 'expired'" class="qr-status expired">⏰ 二维码已过期</div>
        </div>
        <div class="qr-info">
          订单金额：{{ lastOrder?.amount_yuan }} 元 ·
          获得 {{ formatTokens(lastOrder?.tokens_granted || 0) }} Token
        </div>
        <button v-if="pollStatus !== 'pending'" class="btn-primary" @click="startRecharge">重新充值</button>
      </div>

      <!-- 充值按钮 -->
      <button v-else class="btn-primary btn-recharge" @click="startRecharge" :disabled="recharging">
        {{ recharging ? '生成中...' : '生成充值二维码' }}
      </button>

      <!-- 充值记录 -->
      <div v-if="orders.length > 0" class="recharge-section">
        <div class="section-title">充值记录</div>
        <div class="order-table">
          <div class="order-header">
            <span>金额</span>
            <span>Token</span>
            <span>状态</span>
            <span>时间</span>
          </div>
          <div v-for="o in orders" :key="o.order_id" class="order-row">
            <span>{{ o.amount_yuan }} 元</span>
            <span>{{ formatTokens(o.tokens_granted) }}</span>
            <span class="order-status" :class="o.status">{{ statusLabel(o.status) }}</span>
            <span class="cell-time">{{ formatTime(o.created_at) }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import * as backendApi from '../services/backend-api'
import { useToast } from '../composables/useToast'

const { success: toastSuccess, error: toastError } = useToast()

// ── 常量 ──
const configTokenPrice = 500_000  // 1元 = 50万 token（跟后端保持一致）
const tabs = [
  { key: 'balance',  label: '余额' },
  { key: 'usage',    label: '消耗记录' },
  { key: 'recharge', label: '充值' },
]
const timeRanges = [
  { key: 'day',   label: '今日' },
  { key: 'week',  label: '本周' },
  { key: 'month', label: '本月' },
]
const presetAmounts = [10, 20, 50, 100]

// ── 状态 ──
const activeTab = ref('balance')
const balance = ref<any>(null)
const balanceLoading = ref(false)
const usageStats = ref<any>(null)
const usageLoading = ref(false)
const usageRange = ref('month')

// 充值
const rechargeAmount = ref(10)
const customAmount = ref<number | null>(null)
const recharging = ref(false)
const qrUrl = ref('')
const pollStatus = ref<'idle' | 'pending' | 'paid' | 'expired'>('idle')
const lastOrder = ref<any>(null)
const orders = ref<any[]>([])
let pollTimer: any = null

// ── 生命周期 ──
onMounted(() => {
  loadBalance()
})

onUnmounted(() => {
  clearPollTimer()
})

// ── Tab 切换 ──
function switchTab(key: string) {
  activeTab.value = key
  // 延迟加载数据
  if (key === 'usage' && !usageStats.value) loadUsage()
  if (key === 'recharge' && orders.value.length === 0) loadOrders()
}

// ── 余额 ──
async function loadBalance() {
  balanceLoading.value = true
  try {
    balance.value = await backendApi.fetchBalance()
  } catch (e: any) {
    toastError('获取余额失败：' + (e.message || ''))
  } finally {
    balanceLoading.value = false
  }
}

// ── 消耗记录 ──
async function loadUsage() {
  usageLoading.value = true
  try {
    usageStats.value = await backendApi.fetchUsage(usageRange.value)
  } catch (e: any) {
    toastError('获取消耗记录失败：' + (e.message || ''))
  } finally {
    usageLoading.value = false
  }
}

function switchRange(key: string) {
  usageRange.value = key
  loadUsage()
}

// ── 充值 ──
function onCustomInput() {
  if (customAmount.value && customAmount.value > 0) {
    rechargeAmount.value = customAmount.value
  }
}

async function startRecharge() {
  if (!rechargeAmount.value || rechargeAmount.value <= 0) {
    toastError('请选择充值金额')
    return
  }

  recharging.value = true
  qrUrl.value = ''
  pollStatus.value = 'idle'

  try {
    const data = await backendApi.createPayment(rechargeAmount.value)
    qrUrl.value = data.qr_url
    lastOrder.value = data
    pollStatus.value = 'pending'
    // 开始轮询
    startPolling(data.order_id)
    toastSuccess('二维码已生成，请扫码支付')
  } catch (e: any) {
    toastError('创建订单失败：' + (e.message || ''))
  } finally {
    recharging.value = false
  }
}

async function pollOrder(orderId: string) {
  try {
    const status = await backendApi.checkPayment(orderId)
    if (status.status === 'paid') {
      pollStatus.value = 'paid'
      clearPollTimer()
      toastSuccess('充值成功！')
      // 刷新余额和订单
      loadBalance()
      loadOrders()
    } else if (status.status === 'expired') {
      pollStatus.value = 'expired'
      clearPollTimer()
      toastError('二维码已过期')
    }
  } catch {
    // 轮询失败继续等下一次
  }
}

function startPolling(orderId: string) {
  clearPollTimer()
  pollTimer = setInterval(() => pollOrder(orderId), 3000)
}

function clearPollTimer() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function loadOrders() {
  try {
    orders.value = await backendApi.fetchOrders()
  } catch {
    // 静默失败
  }
}

// ── 工具函数 ──
function formatTokens(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M'
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K'
  return n.toLocaleString()
}

function formatTime(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  // 转为本地时间
  const pad = (n: number) => n.toString().padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function statusLabel(s: string): string {
  switch (s) {
    case 'paid': return '已完成'
    case 'pending': return '待支付'
    case 'expired': return '已过期'
    case 'refunded': return '已退款'
    default: return s
  }
}
</script>

<style scoped>
.account-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.page-header {
  padding: 16px 20px 0;
}
.page-header h1 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary, #FAFAFA);
}

/* ── Tab 栏 ── */
.tab-bar {
  display: flex;
  gap: 0;
  margin: 12px 20px 0;
  background: var(--bg-hover, #1A1A1F);
  border-radius: 8px;
  overflow: hidden;
  flex-shrink: 0;
}
.tab-btn {
  flex: 1;
  padding: 8px 0;
  border: none;
  background: transparent;
  color: var(--text-muted, #A1A1AA);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.15s;
}
.tab-btn.active {
  background: var(--border, #27272A);
  color: var(--text-primary, #FAFAFA);
  font-weight: 500;
}
.tab-btn:hover:not(.active) {
  color: var(--text-secondary, #E4E4E7);
}

/* ── 内容区域 ── */
.tab-content {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px 24px;
}

/* ── 余额 ── */
.balance-main-card {
  text-align: center;
  padding: 28px 20px;
  background: var(--surface-glass, rgba(19, 19, 22, 0.8));
  border: 1px solid var(--border, #27272A);
  border-radius: 12px;
  margin-bottom: 16px;
}
.balance-label {
  font-size: 12px;
  color: var(--text-muted, #A1A1AA);
  margin-bottom: 8px;
}
.balance-number {
  font-size: 40px;
  font-weight: 700;
  color: var(--brand, #7C3AED);
  line-height: 1.1;
}
.balance-sub {
  font-size: 12px;
  color: var(--text-muted, #A1A1AA);
  margin-top: 6px;
}
.balance-detail {
  background: var(--surface-glass, rgba(19, 19, 22, 0.8));
  border: 1px solid var(--border, #27272A);
  border-radius: 10px;
  overflow: hidden;
  margin-bottom: 12px;
}
.detail-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border-subtle, rgba(39, 39, 42, 0.5));
  font-size: 13px;
}
.detail-row:last-child { border-bottom: none; }
.detail-label { color: var(--text-muted, #A1A1AA); }
.detail-value { color: var(--text-primary, #E4E4E7); font-weight: 500; }
.detail-value.free { color: var(--accent-green, #22C55E); }
.detail-value.prepaid { color: var(--brand, #7C3AED); }
.price-note {
  text-align: center;
  font-size: 12px;
  color: var(--text-muted, #52525B);
}

/* ── 消耗记录 ── */
.usage-controls {
  display: flex;
  gap: 6px;
  margin-bottom: 12px;
  align-items: center;
}
.range-btn {
  padding: 5px 14px;
  border-radius: 6px;
  border: 1px solid var(--border, #27272A);
  background: transparent;
  color: var(--text-muted, #A1A1AA);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.12s;
}
.range-btn.active {
  background: var(--brand, #7C3AED);
  border-color: var(--brand, #7C3AED);
  color: #FFF;
}
.range-btn:hover:not(.active) {
  border-color: var(--text-muted, #52525B);
}
.refresh-btn {
  margin-left: auto;
  padding: 5px 14px;
  border-radius: 6px;
  border: 1px solid var(--border, #27272A);
  background: transparent;
  color: var(--text-muted, #A1A1AA);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
}
.refresh-btn:hover { border-color: var(--brand, #7C3AED); color: var(--brand, #7C3AED); }

.usage-summary {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
}
.summary-item {
  flex: 1;
  text-align: center;
  padding: 14px 10px;
  background: var(--surface-glass, rgba(19, 19, 22, 0.8));
  border: 1px solid var(--border, #27272A);
  border-radius: 10px;
}
.sum-label {
  display: block;
  font-size: 11px;
  color: var(--text-muted, #A1A1AA);
  margin-bottom: 6px;
}
.sum-value {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary, #E4E4E7);
}
.sum-value.cost { color: var(--accent-red, #EF4444); }

.usage-section {
  margin-bottom: 16px;
}
.section-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-muted, #A1A1AA);
  margin-bottom: 8px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.model-breakdown {
  background: var(--surface-glass, rgba(19, 19, 22, 0.8));
  border: 1px solid var(--border, #27272A);
  border-radius: 10px;
  overflow: hidden;
}
.model-row {
  display: flex;
  align-items: center;
  padding: 8px 14px;
  border-bottom: 1px solid var(--border-subtle, rgba(39, 39, 42, 0.5));
  font-size: 12px;
  gap: 8px;
}
.model-row:last-child { border-bottom: none; }
.model-name { flex: 1; color: var(--text-primary, #E4E4E7); }
.model-tokens { color: var(--brand, #7C3AED); font-weight: 500; width: 100px; text-align: right; }
.model-cost { color: var(--accent-red, #EF4444); width: 60px; text-align: right; }

.record-table {
  background: var(--surface-glass, rgba(19, 19, 22, 0.8));
  border: 1px solid var(--border, #27272A);
  border-radius: 10px;
  overflow: hidden;
  font-size: 11px;
}
.record-header, .record-row {
  display: grid;
  grid-template-columns: 1fr 60px 60px 60px 60px 90px;
  gap: 4px;
  padding: 7px 10px;
  align-items: center;
}
.record-header {
  color: var(--text-muted, #52525B);
  font-weight: 600;
  border-bottom: 1px solid var(--border, #27272A);
}
.record-row {
  border-bottom: 1px solid var(--border-subtle, rgba(39, 39, 42, 0.5));
  color: var(--text-secondary, #A1A1AA);
}
.record-row:last-child { border-bottom: none; }
.record-row:hover { background: var(--bg-hover, #1A1A1F); }
.cell-model { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cell-cost { color: var(--accent-red, #EF4444); }
.cell-time { color: var(--text-muted, #52525B); font-size: 10px; }

/* ── 充值 ── */
.recharge-section {
  margin-bottom: 20px;
}
.amount-presets {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.amount-btn {
  flex: 1;
  min-width: 60px;
  padding: 10px 8px;
  border-radius: 8px;
  border: 1px solid var(--border, #27272A);
  background: var(--surface-glass, rgba(19, 19, 22, 0.8));
  color: var(--text-secondary, #E4E4E7);
  font-size: 14px;
  font-weight: 500;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.12s;
}
.amount-btn.active {
  border-color: var(--brand, #7C3AED);
  background: rgba(124, 58, 237, 0.12);
  color: var(--brand, #7C3AED);
}
.amount-btn:hover:not(.active) {
  border-color: var(--text-muted, #52525B);
}
.custom-amount {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.custom-amount input {
  flex: 1;
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid var(--border, #27272A);
  background: var(--surface-glass, rgba(19, 19, 22, 0.8));
  color: var(--text-primary, #E4E4E7);
  font-size: 14px;
  font-family: inherit;
  outline: none;
}
.custom-amount input:focus { border-color: var(--brand, #7C3AED); }
.custom-amount input::placeholder { color: var(--text-muted, #52525B); }
.amount-unit { color: var(--text-muted, #A1A1AA); font-size: 14px; }
.token-preview {
  font-size: 13px;
  color: var(--text-muted, #A1A1AA);
  text-align: center;
  padding: 6px 0;
}
.token-preview strong { color: var(--brand, #7C3AED); }

/* 二维码 */
.qr-section {
  text-align: center;
  margin-bottom: 20px;
}
.qr-container {
  display: inline-block;
  padding: 16px;
  background: #FFF;
  border-radius: 12px;
  margin-bottom: 10px;
}
.qr-img {
  width: 200px;
  height: 200px;
  display: block;
}
.qr-status {
  margin-top: 8px;
  font-size: 13px;
  color: var(--text-muted, #A1A1AA);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}
.qr-status.paid { color: var(--accent-green, #22C55E); font-weight: 500; }
.qr-status.expired { color: var(--accent-red, #EF4444); }
.qr-info {
  font-size: 12px;
  color: var(--text-muted, #A1A1AA);
  margin-bottom: 12px;
}

/* 按钮 */
.btn-primary {
  padding: 10px 24px;
  border: none;
  border-radius: 8px;
  background: var(--brand, #7C3AED);
  color: #FFF;
  font-size: 14px;
  font-weight: 500;
  font-family: inherit;
  cursor: pointer;
  transition: background 0.12s;
}
.btn-primary:hover { background: #6D28D9; }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-recharge {
  display: block;
  width: 100%;
  padding: 12px;
  font-size: 15px;
  margin-bottom: 20px;
}

/* 充值记录 */
.order-table {
  background: var(--surface-glass, rgba(19, 19, 22, 0.8));
  border: 1px solid var(--border, #27272A);
  border-radius: 10px;
  overflow: hidden;
  font-size: 12px;
}
.order-header, .order-row {
  display: grid;
  grid-template-columns: 80px 1fr 80px 90px;
  gap: 4px;
  padding: 8px 12px;
  align-items: center;
}
.order-header {
  color: var(--text-muted, #52525B);
  font-weight: 600;
  border-bottom: 1px solid var(--border, #27272A);
}
.order-row {
  border-bottom: 1px solid var(--border-subtle, rgba(39, 39, 42, 0.5));
  color: var(--text-secondary, #A1A1AA);
}
.order-row:last-child { border-bottom: none; }
.order-status.paid { color: var(--accent-green, #22C55E); }
.order-status.pending { color: var(--accent-amber, #F59E0B); }
.order-status.expired { color: var(--text-muted, #52525B); }

/* ── 通用 ── */
.loading-row {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 40px 0;
  font-size: 13px;
  color: var(--text-muted, #A1A1AA);
}
.empty-hint {
  text-align: center;
  padding: 30px 0;
  font-size: 13px;
  color: var(--text-muted, #52525B);
}

/* Spinner */
.spinner {
  display: inline-block;
  width: 14px;
  height: 14px;
  border: 2px solid var(--border-subtle, #27272A);
  border-top-color: var(--brand, #7C3AED);
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
</style>
