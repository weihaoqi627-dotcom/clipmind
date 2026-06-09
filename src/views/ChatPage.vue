<template>
  <div class="chat-page">
    <!-- 顶部栏 -->
    <div class="chat-header">
      <div class="header-left">
        <h1 class="header-title">Director</h1>
        <span class="header-status" :class="st.statusClass">{{ st.statusText }}</span>
      </div>
      <div class="header-right">
        <span
          v-if="!editingName"
          class="project-name"
          :title="st.name + ' — 点击编辑'"
          @click="startEditName"
        >{{ st.name }}</span>
        <input
          v-else
          ref="nameInputRef"
          v-model="nameEditValue"
          class="project-name-input"
          @keydown.enter="saveEditName"
          @keydown.escape="cancelEditName"
          @blur="saveEditName"
          @click.stop
        />
        <button class="btn-icon" @click="newProject" title="新建项目">
          <SvgIcon name="plus" size="16" />
        </button>
      </div>
    </div>

    <!-- 进度浮窗 -->
    <Transition name="progress-fade">
      <div v-if="st.progressRunning" class="progress-float">
        <div class="progress-stage">{{ st.progressStage || '执行中' }}</div>
        <div class="progress-bar-track">
          <div class="progress-bar-fill" :style="{ width: progressPct + '%' }"></div>
        </div>
        <div class="progress-text">
          <span class="progress-turn">第 {{ st.progressTurn }} 步</span>
          <span v-if="st.progressTool" class="progress-tool">· {{ st.progressTool }}</span>
        </div>
      </div>
    </Transition>

    <!-- 阶段进度条 -->
    <div v-if="stageSteps.length > 0 && st.progressRunning" class="stage-bar">
      <template v-for="(step, i) in stageSteps" :key="step.key">
        <div class="stage-step" :class="step.status">
          <span class="stage-dot" :class="step.status"></span>
          <span class="stage-name">{{ step.label }}</span>
        </div>
        <div v-if="i < stageSteps.length - 1" class="stage-connector" :class="{ done: step.status === 'done' }"></div>
      </template>
    </div>

    <!-- 消息列表 -->
    <div class="messages" ref="messagesRef" @scroll="onScroll">
      <!-- 空状态 -->
      <div v-if="st.messages.length === 0" class="empty">
        <div class="empty-icon"><SvgIcon name="logo" size="56" color="#FFF" /></div>
        <p class="empty-title">告诉 Director 你想剪什么</p>
        <p class="empty-hint" v-if="st.materials.length > 0">
          已导入 {{ st.materials.length }} 个素材，输入你的剪辑需求开始。<br>
          <span class="mat-tag" v-for="(m, i) in st.materials" :key="i">{{ m.name }}</span>
        </p>
        <!-- 快捷指令 -->
        <div class="quick-chips" v-if="st.materials.length > 0">
          <button
            v-for="chip in quickChips"
            :key="chip.label"
            class="quick-chip"
            @click="quickFill(chip.text)"
          >{{ chip.icon }} {{ chip.label }}</button>
        </div>
        <p class="empty-hint" v-else>
          拖拽视频/音频到左侧面板，然后告诉我想怎么剪
        </p>
      </div>

      <!-- 消息列表 -->
      <div
        v-for="(msg, i) in st.messages"
        :key="msg.id"
        class="message"
        :class="msg.role"
      >
        <div class="msg-avatar">
          {{ msg.role === 'user' ? 'U' : 'D' }}
        </div>
        <div class="msg-body">
          <div class="msg-header">
            <span class="msg-name">{{ msg.role === 'user' ? '你' : 'Director' }}</span>
            <button
              class="msg-copy-btn"
              @click="copyMessage(msg)"
              title="复制消息"
            >
              <SvgIcon name="check" size="13" v-if="copiedId === msg.id" />
              <svg v-else width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
            </button>
          </div>

          <!-- 普通文本消息（无 type 或有 type==='text' 且 content 非空） -->
          <div v-if="msg.content && (!msg.type || msg.type === 'text')" class="msg-content" v-html="renderContent(msg.content)"></div>

          <!-- BGM 选择卡片 -->
          <BgmSelector
            v-if="msg.type === 'bgm_selection'"
            :popular="msg.bgmData?.popular ?? []"
            :recommended="msg.bgmData?.recommended ?? []"
            @select="onBgmSelect($event, msg.id)"
            @skip="onBgmSkip(msg.id)"
            @upload="(type, paths) => onBgmUploadWithPaths(type, paths, msg.id)"
          />

          <!-- ask_user 问题 -->
          <div v-if="msg.type === 'ask_user'" class="ask-user-block">
            <div class="ask-question">{{ msg.askData?.question }}</div>
            <div v-if="msg.askData?.options" class="ask-options">
              <button
                v-for="opt in (msg.askData.options || '').split(',').map((s: string) => s.trim()).filter(Boolean)"
                :key="opt"
                class="ask-option-btn"
                @click="answerAsk(opt)"
              >{{ opt }}</button>
            </div>
            <!-- 自定义输入（只在没有预设选项时显示） -->
            <div v-if="!msg.askData?.options" class="ask-custom">
              <input
                v-model="askInput"
                class="ask-input"
                placeholder="输入你的回答..."
                @keydown.enter="answerAsk(askInput)"
              />
              <button class="ask-send-btn" @click="answerAsk(askInput)">确认</button>
            </div>
          </div>

          <!-- 剪辑方案卡片 -->
          <div v-if="msg.type === 'plan_card'" class="plan-card">
            <div class="plan-header">
              <SvgIcon name="logo" size="16" color="var(--brand)" />
              <span>剪辑方案</span>
              <span class="plan-meta">{{ msg.planData?.materials?.join(', ') || '' }}</span>
            </div>
            <div class="plan-body" v-html="renderContent(msg.planData?.plan || '')"></div>
            <div class="plan-actions">
              <button class="plan-btn plan-btn-primary" @click="onConfirmPlan">确认方案，开始剪辑</button>
              <button class="plan-btn plan-btn-secondary" @click="onRejectPlan">不满意，重新来</button>
            </div>
          </div>

          <!-- 反馈卡片 -->
          <div v-if="msg.type === 'feedback_card'" class="feedback-card">
            <div class="feedback-text">这次剪辑效果怎么样？</div>
            <div class="feedback-btns">
              <button class="fb-btn fb-good" :class="{ done: msg.feedbackData?._sent }" @click="submitFeedback(msg, 5)" :disabled="msg.feedbackData?._sent">👍 满意</button>
              <button class="fb-btn fb-ok" :class="{ done: msg.feedbackData?._sent }" @click="submitFeedback(msg, 3)" :disabled="msg.feedbackData?._sent">😐 一般</button>
              <button class="fb-btn fb-bad" :class="{ done: msg.feedbackData?._sent }" @click="submitFeedback(msg, 1)" :disabled="msg.feedbackData?._sent">👎 不满意</button>
            </div>
            <div v-if="msg.feedbackData?._sent" class="feedback-thanks">感谢反馈！</div>
          </div>

          <!-- 工具调用展示 -->
          <div v-if="msg.tools && msg.tools.length > 0" class="msg-tools">
            <div v-for="(tool, ti) in msg.tools" :key="ti" class="tool-call">
              <span class="tool-name"><SvgIcon name="tools" size="12" /> {{ tool.name }}</span>
              <span class="tool-result" :class="tool.status">{{ tool.statusText }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 深度思考 / 执行中指示器（DeepSeek 风格） -->
      <div v-if="st.thinking || st.running" class="message assistant thinking-block">
        <div class="msg-avatar">D</div>
        <div class="msg-body">
          <div class="msg-name">
            Director
            <span v-if="st.thinking" class="thinking-label">深度思考中...</span>
            <span v-if="st.running && !st.thinking" class="thinking-label running">执行中...</span>
          </div>
          <div class="typing-indicator">
            <span class="dot"></span>
            <span class="dot"></span>
            <span class="dot"></span>
          </div>
        </div>
      </div>

      <!-- 回到底部 -->
      <Transition name="scroll-fade">
        <button v-if="showScrollBtn" class="btn-scroll-bottom" @click="scrollToBottom(true)" title="回到底部">
          <SvgIcon name="back" size="14" style="transform: rotate(-90deg)" />
        </button>
      </Transition>
    </div>

    <!-- 输入区 -->
    <div class="input-area">
      <div class="input-wrapper">
        <input
          ref="inputRef"
          v-model="inputText"
          type="text"
          placeholder="输入你的剪辑需求..."
          @keydown.enter="send"
          :disabled="st.running || st.waitingForAnswer || st.thinking"
          class="chat-input"
        />
        <button
          class="btn-send"
          :disabled="!inputText.trim() || st.running || st.waitingForAnswer"
          @click="send"
        >
          发送
        </button>
        <button
          class="btn-start"
          v-if="!st.running && !st.waitingForAnswer"
          :disabled="st.materials.length === 0"
          :title="st.materials.length === 0 ? '请先导入素材' : '开始剪辑'"
          @click="start"
        >
          开始
        </button>
        <button
          v-else-if="st.running || st.waitingForAnswer"
          class="btn-cancel"
          @click="cancel"
        >
          取消
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, inject, nextTick, onMounted, watch } from 'vue'
import BgmSelector from '../components/BgmSelector.vue'
import SvgIcon from '../components/SvgIcon.vue'
import * as rpc from '../services/rpc'
import { useToast } from '../composables/useToast'

const { error: toastError } = useToast()
import type { BgmSong } from '../components/BgmSelector.vue'

const app = inject('app') || {}
const project = inject('project') || {}

// 稳定回退：避免 computed 每次创建新对象导致 mutation 丢弃
const FALLBACK_STATE = {
  name: '未选择项目',
  messages: [],
  materials: [],
  running: false,
  waitingForAnswer: false,
  statusText: '就绪',
  statusClass: 'idle',
  draftId: '',
  progressTurn: 0,
  progressTool: '',
  progressStage: '',
  progressRunning: false,
  stageProgress: null,
}

// 活跃项目的状态
const st = computed(() => {
  const p = project?.value ?? project
  return p?.state ?? FALLBACK_STATE
})

// 获取项目 ID（安全）
function _projectId(): string {
  return project?.value?.state?.id ?? project?.state?.id ?? ''
}

const inputText = ref('')
const askInput = ref('')
const copiedId = ref<string | null>(null)
const messagesRef = ref<HTMLElement | null>(null)
const inputRef = ref<HTMLInputElement | null>(null)
const nameInputRef = ref<HTMLInputElement | null>(null)
const editingName = ref(false)
const nameEditValue = ref('')

function startEditName() {
  const state = project?.value?.state ?? project?.state
  if (!state) return
  nameEditValue.value = state.name
  editingName.value = true
  nextTick(() => { nameInputRef.value?.focus(); nameInputRef.value?.select() })
}
async function saveEditName() {
  editingName.value = false
  const name = nameEditValue.value.trim()
  if (!name) return
  const pid = _projectId()
  if (!pid) return
  const state = project?.value?.state ?? project?.state
  if (!state) return
  if (name === state.name) return
  try {
    await rpc.updateProject(pid, { name })
    // 直接修改源 reactive 对象，不通过 computed
    state.name = name
  } catch (e) {
    console.error('[ChatPage] 保存项目名称失败:', e)
    toastError('保存项目名称失败')
  }
}
function cancelEditName() { editingName.value = false }

function scrollToBottom(smooth = false) {
  nextTick(() => {
    if (messagesRef.value) {
      messagesRef.value.scrollTo({
        top: messagesRef.value.scrollHeight,
        behavior: smooth ? 'smooth' : 'auto',
      })
    }
  })
}

// ── 回到底部按钮 ──
const showScrollBtn = ref(false)
const SCROLL_THRESHOLD = 200

function onScroll() {
  const el = messagesRef.value
  if (!el) return
  const dist = el.scrollHeight - el.scrollTop - el.clientHeight
  showScrollBtn.value = dist > SCROLL_THRESHOLD
}

// ── 快捷指令 ──
const quickChips = [
  { icon: '✂️', label: '剪1分钟精华', text: '帮我剪一个1分钟以内的精华片段' },
  { icon: '📝', label: '自动加字幕', text: '给视频加上中文字幕' },
  { icon: '🎵', label: '配BGM', text: '帮我挑选合适的背景音乐并合成' },
]

function quickFill(text: string) {
  inputText.value = text
  inputRef.value?.focus()
}

// 监听消息变化自动滚动
watch(() => st.value.messages.length, () => { scrollToBottom(); showScrollBtn.value = false })

// ── 复制消息 ──

function getMessageText(msg: any): string {
  if (msg.type === 'text' || !msg.type) return msg.content || ''
  if (msg.type === 'plan_card') return msg.planData?.plan || ''
  if (msg.type === 'ask_user') return msg.askData?.question || ''
  if (msg.type === 'feedback_card') return '请对本次剪辑结果评分'
  if (msg.type === 'bgm_selection') return '[BGM 选择卡片]'
  if (msg.tools && msg.tools.length > 0) {
    return msg.tools.map((t: any) => `[${t.name}: ${t.statusText}]`).join('\n')
  }
  return msg.content || ''
}

async function copyMessage(msg: any) {
  const text = getMessageText(msg)
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    copiedId.value = msg.id
    setTimeout(() => { copiedId.value = null }, 1500)
  } catch {
    // 降级方案
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.left = '-9999px'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
    copiedId.value = msg.id
    setTimeout(() => { copiedId.value = null }, 1500)
  }
}

function renderContent(text: string): string {
  let html = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\n/g, '<br>')
  return html
}

async function send() {
  const text = inputText.value.trim()
  console.log('[ChatPage] send() 触发, text:', text, '| st.running:', st.value.running, '| st.waitingForAnswer:', st.value.waitingForAnswer, '| project.value:', !!project?.value, '| messages.length:', st.value.messages?.length)
  if (!text || st.value.running || st.value.waitingForAnswer) return
  inputText.value = ''

  // 确保项目存在
  if (!project?.value) {
    console.log('[ChatPage] 项目不存在，尝试创建...')
    await app?.createProject?.()
    await nextTick()
    console.log('[ChatPage] 项目创建后 project.value:', !!project?.value)
  }

  console.log('[ChatPage] 调用 sendMessage, project.value:', !!project?.value)
  project?.value?.sendMessage?.(text)
}

async function start() {
  if (st.value.materials.length === 0 || st.value.running || st.value.waitingForAnswer) return
  inputText.value = ''

  // 确保项目存在
  if (!project?.value) {
    await app?.createProject?.()
    await nextTick()
  }

  // 弹出确认框 → 启动管线（项目已在导入素材时自动初始化）
  project?.value?.startPipeline?.()
}

function cancel() {
  project?.value?.cancelProject?.()
}

// ── 阶段进度条 ──

const stageSteps = computed(() => {
  const sp = st.value.stageProgress
  if (!sp || !sp.stages) return []
  return sp.stages
})

const progressPct = computed(() => {
  const steps = stageSteps.value
  if (steps.length === 0) return 0
  const done = steps.filter(s => s.status === 'done').length
  const activeIdx = steps.findIndex(s => s.status === 'active')
  const activeFrac = activeIdx >= 0 ? 0.5 / steps.length : 0
  return Math.round((done / steps.length + activeFrac) * 100)
})

function answerAsk(answer: string) {
  const a = answer.trim()
  if (!a) return
  askInput.value = ''
  project?.value?.respondAsk?.(a)
}

async function newProject() {
  if (app?.createProject) {
    await app.createProject()
  }
}

function onConfirmPlan() {
  project?.value?.confirmPlan?.()
}

function onRejectPlan() {
  project?.value?.rejectPlan?.('方案不满意，请重新生成')
}

const submittedFeedbackIds = new Set<string>()

// ── 反馈 ──
async function submitFeedback(msg: any, rating: number) {
  if (!msg.feedbackData) return
  const key = `${msg.feedbackData.projectId}_${msg.feedbackData.draftId}_${msg.id}`
  if (submittedFeedbackIds.has(key)) return
  submittedFeedbackIds.add(key)
  // 标记 UI 已发送
  msg.feedbackData._sent = true
  try {
    await rpc.saveFeedback(msg.feedbackData.projectId, msg.feedbackData.draftId, rating)
  } catch (e) {
    console.error('[ChatPage] 提交反馈失败:', e)
    toastError('提交反馈失败')
  }
}

onMounted(() => {
  inputRef.value?.focus()
})

// ── BGM 选择事件处理 ──

function _removeBgmCard(msgId: string) {
  const state = project?.value?.state ?? project?.state
  if (!state?.messages) return
  const idx = state.messages.findIndex((m: any) => m.id === msgId)
  if (idx >= 0) state.messages.splice(idx, 1)
}

function onBgmSelect(song: BgmSong, msgId: string) {
  _removeBgmCard(msgId)
  project?.value?.sendMessage?.(`BGM 方向：${song.name}（${song.artist}），按这个风格配`)
}

function onBgmSkip(msgId: string) {
  _removeBgmCard(msgId)
  project?.value?.sendMessage?.('BGM 跳过选择，让 AI 自己决定')
}

function onBgmUpload(type: 'audio' | 'video', msgId: string) {
  _removeBgmCard(msgId)
  project?.value?.sendMessage?.(`已选择上传${type === 'audio' ? 'BGM 音频' : '视频提取音乐'}`)
}
function onBgmUploadWithPaths(type: 'audio' | 'video', paths: string[], msgId: string) {
  _removeBgmCard(msgId)
  if (paths.length > 0) {
    project?.value?.sendMessage?.(`已上传${type === 'audio' ? 'BGM 音频' : '视频提取音乐'}: ${paths[0]}`)
  } else {
    project?.value?.sendMessage?.(`已选择上传${type === 'audio' ? 'BGM 音频' : '视频提取音乐'}`)
  }
}
</script>

<style scoped>
/* ── 布局 ── */
.chat-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--surface-base);
  position: relative;
}

/* ========== 顶部栏 ========== */
.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 24px;
  flex-shrink: 0;
  background: var(--surface-glass);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--surface-glass-edge);
}

.header-left { display: flex; align-items: center; gap: 10px; }

.header-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: -0.1px;
}

.header-status {
  font-size: 10px;
  padding: 2px 8px;
  border-radius: var(--radius-full);
  font-weight: 500;
}
.header-status.idle { background: var(--bg-hover); color: var(--text-secondary); }
.header-status.running { background: rgba(34, 197, 94, 0.1); color: var(--accent-green); }
.header-status.error { background: rgba(239, 68, 68, 0.1); color: var(--accent-red); }

.header-right { display: flex; align-items: center; gap: 8px; }

.project-name {
  font-size: 11px;
  color: var(--text-secondary);
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 4px;
  transition: background 0.12s;
}
.project-name:hover { background: var(--bg-hover); }

.project-name-input {
  font-size: 12px;
  color: var(--text-primary);
  background: var(--surface-overlay);
  border: 1px solid var(--border-active);
  border-radius: 4px;
  padding: 2px 6px;
  width: 160px;
  font-family: inherit;
  outline: none;
}

.btn-icon {
  width: 28px;
  height: 28px;
  background: transparent;
  border: 1px solid var(--border-card);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}
.btn-icon:hover { background: var(--bg-hover); color: var(--text-primary); border-color: var(--brand); }

/* ========== 消息列表 ========== */
.messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px 24px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  position: relative;
}

/* 空状态 */
.empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
}

.empty-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
}
.empty-icon::before {
  content: '';
  position: absolute;
  width: 64px;
  height: 64px;
  border-radius: 50%;
  background: conic-gradient(from 0deg, var(--brand), var(--accent-cyan), var(--accent-green), var(--accent-amber), var(--accent-red), var(--brand));
  opacity: 0.12;
  animation: ringSpin 8s linear infinite;
}
@keyframes ringSpin { to { transform: rotate(360deg); } }
.empty-icon :deep(svg) {
  position: relative;
  z-index: 1;
  opacity: 0.25;
}

.empty-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-muted);
  letter-spacing: -0.1px;
}
.empty-hint {
  font-size: 12px;
  color: var(--text-muted);
  text-align: center;
  line-height: 1.6;
  max-width: 360px;
}

.mat-tag {
  display: inline-block;
  background: var(--brand-subtle);
  color: var(--text-accent);
  font-size: 10px;
  padding: 2px 8px;
  border-radius: 4px;
  margin: 2px;
  font-weight: 500;
}

/* ── 快捷指令 ── */
.quick-chips {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  justify-content: center;
  margin-top: 2px;
}
.quick-chip {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 14px;
  background: var(--surface-overlay);
  border: 1px solid var(--border-card);
  border-radius: var(--radius-full);
  color: var(--text-secondary);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.15s;
}
.quick-chip:hover {
  border-color: var(--brand);
  color: var(--text-accent);
  background: var(--bg-active);
}

/* ── 回到底部按钮 ── */
.btn-scroll-bottom {
  position: absolute;
  bottom: 16px;
  right: 24px;
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: var(--surface-elevated);
  border: 1px solid var(--border-card);
  color: var(--text-secondary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: var(--shadow-sm);
  z-index: 20;
  transition: all 0.15s;
}
.btn-scroll-bottom:hover {
  background: var(--brand);
  color: var(--text-on-brand);
  border-color: var(--brand);
  box-shadow: 0 2px 8px rgba(99, 102, 241, 0.3);
}
.scroll-fade-enter-active { transition: all 0.18s ease-out; }
.scroll-fade-leave-active { transition: all 0.12s ease-in; }
.scroll-fade-enter-from,
.scroll-fade-leave-to { opacity: 0; transform: translateY(6px); }

/* ========== 消息气泡 ========== */
.message {
  display: flex;
  gap: 8px;
  max-width: 80%;
  animation: msgIn 0.2s ease-out;
}
@keyframes msgIn {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}
.message.user { align-self: flex-end; flex-direction: row-reverse; }
.message.assistant { align-self: flex-start; }

.msg-avatar {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  font-weight: 700;
  flex-shrink: 0;
  margin-top: 2px;
}
.message.user .msg-avatar {
  background: linear-gradient(135deg, var(--accent-green), #16A34A);
  color: #052E16;
}
.message.assistant .msg-avatar {
  background: linear-gradient(135deg, var(--brand), #A78BFA);
  color: var(--text-on-brand);
}

.msg-body {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.msg-header {
  display: flex;
  align-items: center;
  gap: 4px;
}
.msg-name {
  font-size: 10px;
  font-weight: 600;
  color: var(--text-muted);
  padding: 0 4px;
}

.msg-copy-btn {
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: none;
  border-radius: 3px;
  color: var(--text-muted);
  cursor: pointer;
  opacity: 0;
  transition: all 0.12s;
}
.message:hover .msg-copy-btn { opacity: 0.5; }
.msg-copy-btn:hover { opacity: 1 !important; background: var(--bg-hover); color: var(--text-primary); }

.msg-content {
  background: var(--surface-overlay);
  border: 1px solid var(--border-card);
  padding: 10px 14px;
  border-radius: var(--radius);
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text-secondary);
}
.message.assistant .msg-content {
  border-left: 2px solid var(--brand);
}
.message.user .msg-content {
  background: var(--bg-active);
  border-color: rgba(99, 102, 241, 0.12);
  color: var(--text-primary);
}

.msg-content :deep(pre) {
  background: var(--bg-code);
  border: 1px solid var(--border-card);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
  overflow-x: auto;
  font-size: 12px;
  line-height: 1.5;
  margin: 6px 0;
  font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', monospace;
}
.msg-content :deep(code) {
  background: var(--brand-subtle);
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 12px;
  color: var(--text-accent);
}
.msg-content :deep(pre code) {
  background: transparent;
  padding: 0;
  color: var(--text-secondary);
}
.msg-content :deep(strong) {
  color: var(--text-primary);
  font-weight: 600;
}

/* ========== ask_user ========== */
.ask-user-block {
  background: var(--bg-active);
  border: 1px solid var(--border-active);
  border-radius: var(--radius);
  padding: 14px;
}
.ask-question {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  margin-bottom: 12px;
  line-height: 1.5;
}
.ask-options { display: flex; gap: 6px; flex-wrap: wrap; }
.ask-option-btn {
  padding: 6px 14px;
  background: var(--surface-overlay);
  border: 1px solid var(--border-card);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.12s;
  font-family: inherit;
}
.ask-option-btn:hover {
  background: var(--bg-active);
  border-color: var(--brand);
  color: var(--text-accent);
}
.ask-custom { display: flex; gap: 6px; }
.ask-input {
  flex: 1;
  background: var(--surface-overlay);
  border: 1px solid var(--border-card);
  border-radius: var(--radius-sm);
  padding: 8px 12px;
  color: var(--text-primary);
  font-size: 12px;
  font-family: inherit;
  outline: none;
  transition: border-color 0.15s;
}
.ask-input:focus { border-color: var(--brand); }
.ask-send-btn {
  padding: 8px 16px;
  background: var(--brand);
  border: none;
  border-radius: var(--radius-sm);
  color: var(--text-on-brand);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.12s;
  font-family: inherit;
}
.ask-send-btn:hover { background: var(--brand-light); }

/* ========== 工具调用指示 ========== */
.msg-tools {
  display: flex;
  flex-direction: column;
  gap: 3px;
  margin-top: 2px;
}
.tool-call {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  background: var(--bg-active);
  border: 1px solid rgba(99, 102, 241, 0.1);
  border-radius: var(--radius-sm);
  font-size: 11px;
  border-left: 2px solid var(--brand);
}
.tool-name {
  color: var(--text-muted);
  font-family: 'SF Mono', 'Cascadia Code', monospace;
  font-size: 10px;
}
.tool-result {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 6px;
  font-weight: 500;
  margin-left: auto;
}
.tool-result.ok { background: rgba(34, 197, 94, 0.1); color: var(--accent-green); }
.tool-result.error { background: rgba(239, 68, 68, 0.1); color: var(--accent-red); }
.tool-result.running { background: var(--brand-subtle); color: var(--text-accent); animation: pulse 1.5s ease-in-out infinite; }

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

/* ========== 打字动画 ========== */
.typing-indicator {
  display: flex;
  gap: 3px;
  padding: 10px 14px;
  background: var(--surface-overlay);
  border: 1px solid var(--border-card);
  border-left: 2px solid var(--brand);
  border-radius: var(--radius);
}
.dot {
  width: 6px;
  height: 6px;
  background: var(--text-muted);
  border-radius: 50%;
  animation: bounce 1.4s infinite ease-in-out;
}
.dot:nth-child(1) { animation-delay: -0.32s; }
.dot:nth-child(2) { animation-delay: -0.16s; }
.dot:nth-child(3) { animation-delay: 0s; }
@keyframes bounce {
  0%, 80%, 100% { transform: scale(0.6); opacity: 0.3; }
  40% { transform: scale(1); opacity: 1; }
}

/* ========== 深度思考标签 ========== */
.msg-name {
  display: flex;
  align-items: center;
  gap: 6px;
}
.thinking-label {
  font-size: 11px;
  font-weight: 400;
  color: var(--brand);
  animation: pulse 1.5s ease-in-out infinite;
}
.thinking-label.running { color: var(--accent-amber); }
.thinking-block .msg-body { opacity: 0.95; }

/* ========== 输入区 ========== */
.input-area {
  padding: 12px 24px 16px;
  flex-shrink: 0;
  background: var(--surface-glass);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-top: 1px solid var(--surface-glass-edge);
}

.input-wrapper {
  display: flex;
  gap: 8px;
  max-width: 800px;
  margin: 0 auto;
}

.chat-input {
  flex: 1;
  background: var(--surface-overlay);
  border: 1px solid var(--border-card);
  border-radius: var(--radius);
  padding: 10px 16px;
  color: var(--text-primary);
  font-size: 13px;
  font-family: inherit;
  outline: none;
  transition: all 0.2s;
}
.chat-input::placeholder { color: var(--text-muted); }
.chat-input:focus {
  border-color: var(--brand);
  box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.08);
}
.chat-input:disabled { opacity: 0.3; }

.btn-send {
  padding: 10px 20px;
  background: var(--brand);
  border: none;
  border-radius: var(--radius);
  color: var(--text-on-brand);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
  letter-spacing: 0.1px;
  font-family: inherit;
}
.btn-send:hover:not(:disabled) {
  background: var(--brand-light);
  box-shadow: 0 2px 10px rgba(99, 102, 241, 0.2);
}
.btn-send:disabled { background: var(--surface-overlay); color: var(--text-muted); cursor: not-allowed; }

.btn-start {
  padding: 10px 20px;
  background: var(--accent-green);
  border: none;
  border-radius: var(--radius);
  color: #FFF;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
  letter-spacing: 0.1px;
  font-family: inherit;
}
.btn-start:hover:not(:disabled) {
  background: #16A34A;
  box-shadow: 0 2px 10px rgba(34, 197, 94, 0.2);
}
.btn-start:disabled { background: var(--surface-overlay); color: var(--text-muted); cursor: not-allowed; }

.btn-cancel {
  padding: 10px 20px;
  background: transparent;
  border: 1px solid var(--accent-red);
  border-radius: var(--radius);
  color: var(--accent-red);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
  font-family: inherit;
}
.btn-cancel:hover {
  background: rgba(239, 68, 68, 0.08);
  box-shadow: 0 2px 10px rgba(239, 68, 68, 0.15);
}

/* ========== 阶段进度条 ========== */
.stage-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0;
  padding: 6px 24px;
  border-bottom: 1px solid var(--surface-glass-edge);
  background: var(--surface-glass);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  flex-shrink: 0;
  overflow-x: auto;
}

.stage-step {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 6px;
  border-radius: 4px;
  white-space: nowrap;
  transition: all 0.2s;
}
.stage-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
  background: var(--text-muted);
  transition: all 0.3s;
}
.stage-dot.active {
  background: var(--brand);
  box-shadow: 0 0 4px var(--brand);
  animation: dotPulse 1.5s ease-in-out infinite;
}
.stage-dot.done { background: var(--accent-green); }
@keyframes dotPulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.6; transform: scale(1.2); }
}
.stage-name {
  font-size: 10px;
  color: var(--text-muted);
  transition: color 0.3s;
}
.stage-step.active .stage-name { color: var(--text-accent); font-weight: 600; }
.stage-step.done .stage-name { color: var(--text-secondary); }
.stage-step.error .stage-name { color: var(--accent-red); }
.stage-step.error .stage-dot { background: var(--accent-red); }

.stage-connector {
  width: 20px;
  height: 1px;
  background: var(--border-card);
  flex-shrink: 0;
  transition: background 0.3s;
}
.stage-connector.done { background: var(--accent-green); }

/* ========== 剪辑方案卡片 ========== */
.plan-card {
  background: linear-gradient(135deg, rgba(99, 102, 241, 0.05), rgba(139, 92, 246, 0.03));
  border: 1px solid var(--border-active);
  border-radius: var(--radius);
  padding: 16px;
}
.plan-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}
.plan-meta {
  font-size: 10px;
  font-weight: 400;
  color: var(--text-muted);
  margin-left: auto;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.plan-body {
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-secondary);
  margin-bottom: 14px;
  max-height: 280px;
  overflow-y: auto;
}
.plan-body :deep(h1), .plan-body :deep(h2), .plan-body :deep(h3) {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 10px 0 5px;
}
.plan-body :deep(ul), .plan-body :deep(ol) { padding-left: 16px; margin: 4px 0; }
.plan-body :deep(li) { margin: 2px 0; }
.plan-body :deep(code) {
  background: var(--brand-subtle);
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 11px;
  color: var(--text-accent);
}
.plan-actions { display: flex; gap: 8px; }
.plan-btn {
  padding: 8px 16px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.12s;
  font-family: inherit;
  border: none;
}
.plan-btn-primary {
  background: var(--brand);
  color: var(--text-on-brand);
}
.plan-btn-primary:hover {
  background: var(--brand-light);
  box-shadow: 0 2px 10px rgba(99, 102, 241, 0.25);
}
.plan-btn-secondary {
  background: var(--surface-overlay);
  border: 1px solid var(--border-card);
  color: var(--text-secondary);
}
.plan-btn-secondary:hover {
  background: var(--bg-hover);
  border-color: var(--border-active);
  color: var(--text-primary);
}

/* ========== 反馈卡片 ========== */
.feedback-card {
  background: var(--surface-overlay);
  border: 1px solid var(--border-card);
  border-radius: var(--radius);
  padding: 12px 14px;
  margin-top: 2px;
}
.feedback-text { font-size: 12px; color: var(--text-secondary); margin-bottom: 8px; }
.feedback-btns { display: flex; gap: 6px; }
.fb-btn {
  padding: 5px 12px;
  border: 1px solid var(--border-card);
  border-radius: 5px;
  background: var(--surface-base);
  color: var(--text-secondary);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.12s;
}
.fb-btn:hover:not(:disabled) { border-color: var(--brand); color: var(--text-primary); }
.fb-btn:disabled { cursor: default; opacity: 0.4; }
.fb-btn.done { opacity: 0.4; }
.feedback-thanks { margin-top: 6px; font-size: 11px; color: var(--accent-green); }

/* ========== 进度浮窗 ========== */
.progress-float {
  position: absolute;
  top: 16px;
  right: 24px;
  z-index: 10;
  background: var(--surface-elevated);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border: 1px solid var(--border-card);
  border-radius: var(--radius);
  padding: 10px 14px;
  min-width: 170px;
  box-shadow: var(--shadow-md);
  pointer-events: none;
}
.progress-stage {
  font-size: 10px;
  color: var(--brand);
  font-weight: 600;
  margin-bottom: 5px;
  letter-spacing: 0.3px;
}
.progress-bar-track {
  height: 3px;
  background: rgba(255, 255, 255, 0.05);
  border-radius: 2px;
  overflow: hidden;
  margin-bottom: 6px;
}
.progress-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--brand), var(--brand-light));
  border-radius: 2px;
  transition: width 0.3s ease;
}
.progress-text {
  display: flex;
  align-items: center;
  gap: 3px;
  font-size: 11px;
}
.progress-turn { color: var(--text-accent); font-weight: 600; }
.progress-tool { color: var(--text-muted); }

.progress-fade-enter-active { transition: all 0.18s ease-out; }
.progress-fade-leave-active { transition: all 0.25s ease-in; }
.progress-fade-enter-from { opacity: 0; transform: translateY(-3px); }
.progress-fade-leave-to { opacity: 0; transform: translateY(-3px); }
</style>
