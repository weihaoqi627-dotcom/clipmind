<template>
  <div id="layout" @dragover.prevent @drop.prevent>
    <TitleBar :title="windowTitle" />
    <div class="layout-body">
      <LeftPanel
        :projects="projectsList"
        :activeProjectId="activeProjectId"
        :connected="connected"
        :loggedIn="loggedIn"
        :authUser="authUser"
        :style="{ width: leftPanelWidth + 'px', minWidth: leftPanelWidth + 'px' }"
        @create-project="createProject"
        @switch-project="switchProject"
        @delete-project="deleteProject"
        @files-selected="onFilesSelected"
        @remove-material="onRemoveMaterial"
        @open-draft="openDraft"
        @refresh-drafts="onRefreshDrafts"
        @open-settings="showSettings = true"
        @duplicate-project="duplicateProject"
        @restore-project="onRestoreProject"
        @rescan-projects="onRefreshDrafts"
        @show-auth="showAuth = true"
        @logout="logout"
      />
      <div class="resize-handle" @mousedown="onResizeStart"></div>
      <main class="main-area">
        <router-view v-slot="{ Component, route }">
  <Transition name="page" mode="out-in">
    <component :is="Component" :key="route.path" />
  </Transition>
</router-view>
      </main>
    </div>
    <ToastProvider />
    <SettingsModal
      :visible="showSettings"
      :settings="settings"
      @close="showSettings = false"
      @save="onSaveSettings"
    />
    <ConfirmModal
      :visible="showStartConfirm"
      title="开始剪辑"
      message="确定开始剪辑吗？将使用当前素材开始自动剪辑。"
      confirmText="开始"
      cancelText="取消"
      @confirm="doStartPipeline"
      @cancel="showStartConfirm = false"
    />
    <ExportModal
      :visible="exportModalVisible"
      :draft-id="exportModalData.draftId"
      :project-id="exportModalData.projectId || undefined"
      @close="exportModalVisible = false"
    />
    <ShortcutsPanel
      :visible="showShortcuts"
      @close="showShortcuts = false"
    />
    <AuthDialog
      :visible="showAuth"
      @done="onAuthDone"
    />
    <AnnouncementModal
      :visible="showAnnounce"
      @close="showAnnounce = false"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, readonly, provide, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import TitleBar from './components/TitleBar.vue'
import LeftPanel from './components/LeftPanel.vue'
import ToastProvider from './components/ToastProvider.vue'
import SettingsModal from './components/SettingsModal.vue'
import ConfirmModal from './components/ConfirmModal.vue'
import ExportModal from './components/ExportModal.vue'
import ShortcutsPanel from './components/ShortcutsPanel.vue'
import AuthDialog from './components/AuthDialog.vue'
import AnnouncementModal from './components/AnnouncementModal.vue'
import { useToast } from './composables/useToast'
import * as rpc from './services/rpc'
import * as backendApi from './services/backend-api'
import { capturePreviewClip } from './services/preview-capture'
import type { AiMessageEvent, ToolStartEvent, ToolEndEvent, AskUserEvent, ProgressEvent, ProjectCompleteEvent, ErrorEvent } from './services/rpc'
import type { RpcEvent } from './types'

const { success: toastSuccess, error: toastError } = useToast()

// ── 可拖拽面板 ──

const leftPanelWidth = ref(280)
const MIN_PANEL = 200
const MAX_PANEL = 500

function onResizeStart(e: MouseEvent) {
  e.preventDefault()
  const startX = e.clientX
  const startW = leftPanelWidth.value

  function onMove(ev: MouseEvent) {
    leftPanelWidth.value = Math.max(MIN_PANEL, Math.min(MAX_PANEL, startW + ev.clientX - startX))
  }
  function onUp() {
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
  }

  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}

// ── Types ──

interface ToolCall {
  name: string
  status: 'ok' | 'error' | 'running'
  statusText: string
}

interface AskData {
  question: string
  options?: string
}

interface ChatMsg {
  id: string
  role: 'user' | 'assistant'
  type?: 'text' | 'bgm_selection' | 'ask_user' | 'plan_card' | 'feedback_card'
  content: string
  tools?: ToolCall[]
  bgmData?: any
  askData?: AskData
  planData?: PlanData
  feedbackData?: { projectId: string; draftId: string }
}

interface PlanData {
  plan: string
  material_count: number
  materials: string[]
}

interface Material {
  name: string
  type: 'video' | 'audio'
  path: string
}

interface TrackSegment {
  start: number
  end: number
  label: string
}

interface PreviewData {
  videoUrl: string
  startTime?: number
  endTime?: number
  result?: string
  tracks?: {
    video?: TrackSegment[]
    audio?: TrackSegment[]
    subtitle?: TrackSegment[]
  }
}

interface ProjectState {
  id: string
  name: string
  running: boolean
  materials: Material[]
  messages: ChatMsg[]
  statusText: string
  statusClass: 'idle' | 'running' | 'error'
  draftId: string
  outputPath: string
  waitingForAnswer: boolean
  thinking: boolean
  previewData: PreviewData | null
  _hasSentTask: boolean
  exporting: boolean
  createdAt: number
  // 进度
  progressTurn: number
  progressTool: string
  progressStage: string
  progressRunning: boolean
  stageProgress: { stages: Array<{ key: string; label: string; status: 'pending' | 'active' | 'done' | 'error' }>; workflow: string }
  nameLocked: boolean
  /** 内部标记：素材已导入、Pipeline 已初始化（不序列化到存储） */
  _pipelineReady?: boolean
}

// ── Global State ──

const router = useRouter()
const connected = ref(false)
const activeProjectId = ref<string | null>(null)
const showSettings = ref(false)
const showShortcuts = ref(false)
const settings = ref<Record<string, any>>({})

// ── 认证 ──

const showAuth = ref(false)
const loggedIn = ref(backendApi.isLoggedIn())
const authUser = ref<any>(null)
const showAnnounce = ref(false)

// 如果已登录，自动配置 Director
if (loggedIn.value) {
  backendApi.applyToDirector()
}

function onAuthDone(user: any) {
  authUser.value = user
  loggedIn.value = true
  showAuth.value = false
  toastSuccess('登录成功')

  // 新用户弹公告（最多 10 次）
  const key = 'clipmind_announce_count'
  const count = parseInt(localStorage.getItem(key) || '0', 10)
  if (count < 10) {
    showAnnounce.value = true
  }
}

function logout() {
  backendApi.logout()
  loggedIn.value = false
  authUser.value = null
  showAuth.value = true  // 登出后强制重新登录
}

// ── 导出弹窗 & 多任务管理 ──
const exportModalVisible = ref(false)
const exportModalData = ref({ draftId: '', projectId: '' })

provide('exportModal', {
  visible: exportModalVisible,
  open: (draftId: string, projectId?: string) => {
    exportModalData.value = { draftId, projectId: projectId || '' }
    exportModalVisible.value = true
  },
  close: () => { exportModalVisible.value = false },
})

// ── 多任务导出管理 ──
interface ExportTask {
  taskId: string
  projectId: string
  projectName: string
  draftId: string
  preset: string
  presetLabel: string
  status: 'running' | 'done' | 'error'
  outputPath?: string
  errorMsg?: string
  createdAt: number
}

let _taskIdCounter = 0
const exportTasks = ref<ExportTask[]>([])
const exportLimit = ref(5) // free tier; premium = 20

const runningExportCount = computed(() => exportTasks.value.filter(t => t.status === 'running').length)

function startExportTask(draftId: string, projectId: string, projectName: string, preset: string, presetLabel: string): boolean {
  if (runningExportCount.value >= exportLimit.value) return false
  const proj = projects[projectId]
  if (!proj || proj.exporting) return false

  const task: ExportTask = {
    taskId: `exp_${++_taskIdCounter}`,
    projectId, projectName, draftId, preset, presetLabel,
    status: 'running',
    createdAt: Date.now(),
  }
  exportTasks.value.push(task)
  proj.exporting = true

  rpc.exportDraft(draftId, projectId, preset).catch((e: any) => {
    const t = exportTasks.value.find(x => x.projectId === projectId && x.status === 'running')
    if (t) { t.status = 'error'; t.errorMsg = e?.message || '导出调用失败' }
    if (projects[projectId]) projects[projectId].exporting = false
  })
  return true
}

function cancelExportTask(taskId: string, projectId: string) {
  const t = exportTasks.value.find(x => x.taskId === taskId)
  if (t) t.status = 'done' // 从列表中移除视觉，标记完成
  // 从后端取消
  rpc.cancelProject(projectId).catch(() => {})
  if (projects[projectId]) {
    projects[projectId].exporting = false
    projects[projectId].statusText = '就绪'
    projects[projectId].statusClass = 'idle'
  }
}

provide('exportManager', {
  tasks: readonly(exportTasks),
  limit: readonly(exportLimit),
  runningCount: readonly(runningExportCount),
  start: startExportTask,
  cancel: cancelExportTask,
})

// ── 设置 ──

async function loadSettings() {
  try {
    settings.value = await rpc.getSettings()
  } catch (e) {
    console.warn('[App] 加载设置失败:', e)
    toastError('加载设置失败')
    settings.value = {}
  }
  applyTheme()
}

async function onSaveSettings(s: Record<string, any>) {
  settings.value = s
  try {
    await rpc.saveSettings(s)
  } catch (e) {
    console.error('[App] 保存设置失败:', e)
    toastError('保存设置失败')
  }
  showSettings.value = false
  applyTheme()
}

function applyTheme() {
  const theme = settings.value?.appearance?.theme || 'dark'
  if (theme === 'system') {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    document.documentElement.setAttribute('data-theme', prefersDark ? 'dark' : 'light')
    // 监听系统主题变化
    if (!_themeMediaQuery) {
      _themeMediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
      _themeMediaQuery.addEventListener('change', (e) => {
        if (settings.value?.appearance?.theme === 'system') {
          document.documentElement.setAttribute('data-theme', e.matches ? 'dark' : 'light')
        }
      })
    }
  } else {
    document.documentElement.setAttribute('data-theme', theme)
  }
}

let _themeMediaQuery: MediaQueryList | null = null

// ── Projects（必须在 windowTitle 之前声明）──

const projects = reactive<Record<string, ProjectState>>({})
const projectsList = computed(() =>
  Object.values(projects).sort((a, b) => b.createdAt - a.createdAt)
)

const activeProject = computed(() =>
  activeProjectId.value ? projects[activeProjectId.value] : null
)

// ── 窗口标题 ──

const windowTitle = computed(() => {
  const proj = activeProject.value
  return proj ? `剪意 — ${proj.name}` : '剪意'
})

watch(windowTitle, (t) => {
  document.title = t
  window.cherryclip?.window?.setTitle(t)
}, { immediate: true })

function _newProjectState(id: string): ProjectState {
  return reactive({
    id,
    name: `项目 ${id.slice(-6)}`,
    running: false,
    materials: [],
    messages: [],
    statusText: '就绪',
    statusClass: 'idle',
    draftId: '',
    outputPath: '',
    waitingForAnswer: false,
    thinking: false,
    previewData: null,
    _hasSentTask: false,
    exporting: false,
    createdAt: Date.now(),
    nameLocked: false,
    progressTurn: 0,
    progressTool: '',
    progressStage: '',
    progressRunning: false,
    stageProgress: { stages: [], workflow: '' },
  })
}

let _mid = 0
function nid() { return `m_${++_mid}` }

// ── Project Actions ──

/** 本地创建项目（不经过 RPC），用于 fallback */
function _createFallbackProject() {
  const pid = 'proj_' + Date.now()
  projects[pid] = _newProjectState(pid)
  activeProjectId.value = pid
  router.push('/')
  console.log('[App] 本地 fallback 项目已创建:', pid)
}

async function createProject() {
  // 记下旧项目数，用于检测 project_created 事件是否已创建了新项目
  const beforeCount = Object.keys(projects).length
  try {
    const result = await rpc.createProject()
    const pid = result.project_id
    projects[pid] = _newProjectState(pid)
    if (result.name) projects[pid].name = result.name
    activeProjectId.value = pid
    router.push('/')
    return pid
  } catch (err) {
    // RPC 超时 → 检查 project_created 事件是否已提前创建了项目
    const nowKeys = Object.keys(projects)
    if (nowKeys.length > beforeCount) {
      // 有新项目被事件创建了，用它
      const newPid = nowKeys[nowKeys.length - 1]
      if (!projects[newPid]) projects[newPid] = _newProjectState(newPid)
      activeProjectId.value = newPid
      router.push('/')
      console.log('[App] 使用 project_created 事件项目:', newPid)
      return newPid
    }
    // 完全没有新项目 → 本地 fallback
    console.error('[App] RPC 创建项目失败，使用本地 fallback:', err)
    _createFallbackProject()
    return activeProjectId.value
  }
}

async function switchProject(pid: string) {
  if (projects[pid]) {
    activeProjectId.value = pid
    router.push('/')
    // 如果聊天记录为空，从磁盘加载
    if (projects[pid].messages.length === 0) {
      try {
        const msgs = await rpc.loadChatMessages(pid)
        if (msgs && msgs.length > 0) {
          projects[pid].messages = msgs
        }
      } catch (e) {
        console.warn('[App] 切换项目时加载聊天记录失败:', e)
      }
    }
  }
}

async function deleteProject(pid: string) {
  try {
    await rpc.deleteProject(pid)
  } catch (e) {
    console.warn('[App] 删除项目失败:', e)
    toastError('删除项目失败')
  }
  delete projects[pid]
  const remaining = Object.keys(projects)
  activeProjectId.value = remaining.length > 0 ? remaining[0] : null
}

async function onRestoreProject(pid: string) {
  // 从回收站恢复后重新加载项目列表
  try {
    const saved = await rpc.listProjects()
    for (const p of saved) {
      if (!projects[p.project_id]) {
        projects[p.project_id] = _newProjectState(p.project_id)
      }
      const proj = projects[p.project_id]
      if (p.name) proj.name = p.name
      if (p.name_locked) proj.nameLocked = true
      if (p.draft_id) proj.draftId = p.draft_id
      if (p.materials) proj.materials = p.materials as Material[]
    }
  } catch (e) {
    console.warn('[App] 恢复项目列表失败:', e)
    toastError('恢复项目列表失败')
  }
}

function openDraft(draftId: string) {
  for (const pid in projects) {
    if (projects[pid].draftId === draftId) {
      activeProjectId.value = pid
      router.push('/preview')
      return
    }
  }
  router.push('/preview')
}

function onRefreshDrafts() {
  // 草稿被删除后，清理对应项目的 draftId 引用
  for (const pid in projects) {
    const proj = projects[pid]
    if (proj.draftId) {
      // 简单处理：让用户重新加载项目列表
      _saveProjectMeta(pid)
    }
  }
}

// ── Per-Project Helpers ──

function _getProj(pid?: string): ProjectState | null {
  const id = pid || activeProjectId.value
  return id ? (projects[id] ?? null) : null
}

function _ensureActive(): ProjectState {
  if (activeProjectId.value && projects[activeProjectId.value]) {
    return projects[activeProjectId.value]
  }
  // 懒创建第一个项目
  const pid = 'proj_' + Date.now()
  projects[pid] = _newProjectState(pid)
  activeProjectId.value = pid
  return projects[pid]
}

// ── 持久化辅助 ──

function _serializableProject(proj: ProjectState): any {
  return {
    id: proj.id,
    name: proj.name,
    materials: proj.materials.map(m => ({ name: m.name, type: m.type, path: m.path })),
    messages: proj.messages.map(m => ({
      id: m.id, role: m.role, type: m.type, content: m.content,
      tools: m.tools, bgmData: m.bgmData, askData: m.askData, planData: m.planData,
    })),
    draftId: proj.draftId,
    outputPath: proj.outputPath,
    createdAt: proj.createdAt,
  }
}

async function _restoreProjects() {
  try {
    const saved = await rpc.listProjects()
    for (const p of saved) {
      if (!projects[p.project_id]) {
        projects[p.project_id] = _newProjectState(p.project_id)
      }
      const proj = projects[p.project_id]
      if (p.name) proj.name = p.name
      if (p.name_locked) proj.nameLocked = true
      if (p.draft_id) proj.draftId = p.draft_id
      if (p.materials) proj.materials = p.materials as Material[]
      if (p.created_at) {
        // 保留 createdAt 只做备注，实际用 Date.now() 排序
      }
    }
    if (Object.keys(projects).length > 0) {
      const firstId = saved[0]?.project_id
      if (firstId) {
        activeProjectId.value = firstId
        router.push('/')
        // 加载聊天记录
        try {
          const msgs = await rpc.loadChatMessages(firstId)
          if (msgs && msgs.length > 0) {
            projects[firstId].messages = msgs
          }
        } catch (e) {
          console.warn('[App] 恢复项目时加载聊天消息失败:', e)
        }
      }
    }
    console.log(`[App] 从磁盘恢复了 ${saved.length} 个项目`)
  } catch (e) {
    console.error('[App] 恢复项目列表失败:', e)
    toastError('恢复项目数据失败')
  }
}

function _saveProjectMeta(pid: string) {
  const proj = projects[pid]
  if (!proj) return
  rpc.updateProject(pid, {
    name: proj.name,
    draft_id: proj.draftId || '',
    materials: proj.materials.map(m => ({ name: m.name, type: m.type, path: m.path })),
  }).catch((e: any) => {
    console.warn('[App] 保存项目元数据失败:', e)
  })
}

function _saveChat(pid: string) {
  const proj = projects[pid]
  if (!proj) return
  // 深拷贝剥离 Vue Proxy，否则 Electron IPC 的 structured clone 会报错
  const msgs = JSON.parse(JSON.stringify(proj.messages.map(m => ({
    id: m.id, role: m.role, type: m.type, content: m.content,
    tools: m.tools, bgmData: m.bgmData, askData: m.askData, planData: m.planData,
  }))))
  rpc.saveChatMessages(pid, msgs).catch((e: any) => {
    console.warn('[App] 保存聊天记录失败:', e)
  })
}

// ── RPC 事件处理 ──

function handleRpcEvent(ev: RpcEvent) {
  const { event, project_id, ...data } = ev

  // 全局事件
  switch (event) {
    case 'ready':
      connected.value = true
      console.log('[RPC] Python 后端就绪')
      // 启动时从磁盘恢复项目
      if (Object.keys(projects).length === 0) {
        _restoreProjects().then(() => {
          // 恢复后如果还是没有项目（首次启动/清空），自动创建一个
          if (Object.keys(projects).length === 0) {
            createProject().catch(() => {
              // RPC 创建失败也创建本地项目，保证 UI 可用
              _createFallbackProject()
            })
          }
        })
      }
      break

    case 'project_created': {
      const pid = ev.project_id || data.project_id
      if (pid && !projects[pid]) {
        projects[pid] = _newProjectState(pid)
      }
      break
    }

    case 'project_deleted': {
      const pid = ev.project_id || data.project_id
      if (pid && projects[pid]) {
        delete projects[pid]
      }
      break
    }

    case 'project_restored': {
      // 回收站恢复后重新加载项目列表（onRestoreProject 已处理）
      break
    }

    case 'project_permanently_deleted': {
      break
    }

    case 'shutdown':
      connected.value = false
      break

    case 'backend_error':
      // 只有在从未收到 ready 时才标记断连（防止 startup 假阳性超时覆盖 ready）
      const wasAlreadyConnected = connected.value
      connected.value = false
      // 网络/后端断开时推消息到聊天框（但如果已连上过，不推重复错误）
      if (!wasAlreadyConnected) {
        const msg = (ev as any)?.message || '与后端服务断开连接，请检查网络后重启应用。'
        for (const [_pid, proj] of Object.entries(projects)) {
          proj.progressRunning = false
          proj.progressTool = ''
          proj.running = false
          proj.statusClass = 'error'
          proj.statusText = '连接断开'
          proj.messages.push({ id: nid(), role: 'assistant', content: `🔌 **连接断开**: ${msg}` })
        }
      }
      break
  }

  // 项目级事件 → 路由到对应项目
  const proj = _getProj(project_id)
  if (!proj) return

  switch (event) {
    case 'stream_chunk': {
      // 流式内容逐段追加到最后一条 AI 消息
      const content = (ev as Record<string, unknown>).content as string || ''
      if (!content) break
      const msgs = proj.messages
      let last = msgs.length > 0 ? msgs[msgs.length - 1] : null
      if (!last || last.role !== 'assistant' || last._done || last.tools) {
        last = { id: nid(), role: 'assistant', content: '' }
        msgs.push(last)
      }
      last.content += content
      // 触发 Vue 响应式更新
      proj.messages = [...msgs]
      break
    }

    case 'ai_message': {
      proj.thinking = false
      const e = ev as AiMessageEvent
      const msgs = proj.messages
      const last = msgs.length > 0 ? msgs[msgs.length - 1] : null
      if (last && last.role === 'assistant' && !last._done) {
        // 已通过 stream_chunk 收到内容，标记完成
        last._done = true
      } else {
        // 无流式（回退）：直接添加完整消息
        msgs.push({ id: nid(), role: 'assistant', content: e.content })
      }
      _saveChat(project_id)
      break
    }

    case 'tool_start': {
      proj.thinking = false
      const e = ev as ToolStartEvent
      // 如果有正在流式输出的消息，先标记完成
      const lastMsg = proj.messages[proj.messages.length - 1]
      if (lastMsg && lastMsg.role === 'assistant' && !lastMsg._done) {
        lastMsg._done = true
      }
      proj.messages.push({
        id: nid(), role: 'assistant', type: 'text', content: '',
        tools: [{ name: e.name, status: 'running', statusText: '执行中...' }],
      })
      // 更新进度
      proj.progressTurn++
      proj.progressTool = e.name
      proj.progressRunning = true
      _saveChat(project_id)
      break
    }

    case 'tool_end': {
      const e = ev as ToolEndEvent
      const last = proj.messages[proj.messages.length - 1]
      if (last?.tools) {
        const tool = last.tools[0]
        if (tool.name === e.name) {
          // tool_end 的 status 字段可能是 'ok'/'error'
          tool.status = (e as any).status === 'ok' ? 'ok' : 'error'
          const dur = (e as any).duration_ms ? ` (${((e as any).duration_ms / 1000).toFixed(1)}s)` : ''
          tool.statusText = tool.status === 'ok' ? `✓ 完成${dur}` : `✗ ${(e as any).message || '失败'}`
        }
      }
      // 清除当前工具名
      proj.progressTool = ''
      _saveChat(project_id)
      break
    }

    case 'ask_user': {
      proj.thinking = false
      const e = ev as AskUserEvent
      proj.waitingForAnswer = true
      // 标记前一条流式消息完成
      const prevMsg = proj.messages[proj.messages.length - 1]
      if (prevMsg && prevMsg.role === 'assistant' && !prevMsg._done) {
        prevMsg._done = true
      }
      proj.messages.push({
        id: nid(), role: 'assistant', type: 'ask_user', content: '',
        askData: { question: e.question, options: e.options },
      })
      _saveChat(project_id)
      break
    }

    case 'progress': {
      proj.thinking = false
      const e = ev as ProgressEvent
      if (e.status === 'started') {
        proj.running = true
        proj.statusText = '工作中...'
        proj.statusClass = 'running'
        proj.progressTurn = 0
        proj.progressTool = '准备中...'
        proj.progressStage = (e as any).stage || '准备中'
        proj.progressRunning = true
      } else if (e.status === 'cancelling') {
        proj.statusText = '取消中...'
      } else if (e.status === 'cancelled') {
        proj.running = false
        proj.statusText = '就绪'
        proj.statusClass = 'idle'
        proj.waitingForAnswer = false
        proj.progressRunning = false
        proj.progressTool = ''
        proj.progressStage = ''
        proj.stageProgress = { stages: [], workflow: '' }
        proj.messages.push({ id: nid(), role: 'assistant', content: '⏹ 已取消。' })
        _saveChat(project_id!)
      } else if (e.status === 'ask_timeout') {
        // 后端 ask_user 超时 — 释放 waitingForAnswer 锁定
        proj.waitingForAnswer = false
        proj.statusText = '工作中...'
      } else {
        // analyzing / planning / executing → 更新阶段
        proj.progressStage = (e as any).stage || proj.progressStage
      }
      break
    }

    case 'workflow': {
      // Stage 0 路由结果 — 阶段名由 Director 动态定义，前端不做硬编码映射
      const e = ev as any
      proj.progressStage = `${e.label || e.workflow} · 准备中`
      // 初始化阶段进度条
      const activeStages: string[] = e.active_stages || []
      proj.stageProgress = {
        workflow: e.workflow || '',
        stages: activeStages.map((s: string) => ({
          key: s,
          label: s, // 直接用 Director 传回的名字，不硬编码翻译
          status: 'pending' as const,
        })),
      }
      break
    }

    case 'stage_start': {
      const e = ev as any
      const total = e.total_stages || 5
      const num = e.stage_num || 1
      proj.progressStage = `${e.label || e.stage} · ${num}/${total}`
      proj.progressTool = ''
      // 更新阶段进度条
      const sp = proj.stageProgress
      if (sp) {
        sp.stages.forEach(s => {
          if (s.key === e.stage) s.status = 'active'
          else if (s.status === 'active') s.status = 'done'
        })
      }
      break
    }

    case 'stage_end': {
      // 阶段完成，标记 done
      const e = ev as any
      const sp = proj.stageProgress
      if (sp) {
        const s = sp.stages.find(s => s.key === e.stage)
        if (s) s.status = 'done'
      }
      break
    }

    case 'project_complete': {
      const e = ev as ProjectCompleteEvent
      proj.draftId = e.draft_id
      proj.outputPath = (e as any).output_path || ''
      proj._hasSentTask = true
      proj.running = false
      proj.statusText = '就绪'
      proj.statusClass = 'idle'
      proj.progressRunning = false
      proj.progressTool = ''
      proj.progressStage = ''
      _saveProjectMeta(project_id)
      _saveChat(project_id)
      // 桌面通知 + Toast
      window.cherryclip?.notifications?.show('剪意', `「${proj.name}」剪辑完成，可预览和导出。`)
      toastSuccess(`「${proj.name}」剪辑完成`)
      // 添加反馈卡片
      proj.messages.push({
        id: nid(), role: 'assistant', type: 'feedback_card', content: '',
        feedbackData: { projectId: project_id!, draftId: e.draft_id },
      })
      _saveChat(project_id)
      break
    }

    case 'error': {
      proj.thinking = false
      const e = ev as ErrorEvent
      proj.statusClass = 'error'
      proj.statusText = '错误'
      proj.progressRunning = false
      proj.progressTool = ''
      proj.progressStage = ''
      proj.messages.push({ id: nid(), role: 'assistant', content: `❌ **错误**: ${e.message.slice(0, 500)}` })
      proj.running = false
      _saveChat(project_id)
      break
    }

    case 'request_preview_clip': {
      const ev2 = ev as Record<string, unknown>
      const video_path = ev2.video_path as string | undefined
      const start_time = ev2.start_time as number | undefined
      const end_time = ev2.end_time as number | undefined
      if (!video_path || typeof start_time !== 'number' || typeof end_time !== 'number') {
        console.warn('[RPC] request_preview_clip 参数不完整:', ev)
        break
      }
      capturePreviewClip(video_path, start_time, end_time)
        .then(({ base64, webmBlob }) => {
          console.log('[RPC] 预览捕获完成, size:', base64.length)
          rpc.respondPreviewClip(project_id!, base64)

          const blobUrl = URL.createObjectURL(webmBlob)
          proj.previewData = {
            videoUrl: blobUrl,
            startTime: start_time,
            endTime: end_time,
          }
          proj.messages.push({
            id: nid(), role: 'assistant',
            content: `📹 正在检查 ${start_time.toFixed(1)}s–${end_time.toFixed(1)}s 的效果，你可以切换到预览页面查看。`,
          })
          _saveChat(project_id!)
        })
        .catch(err => {
          console.error('[RPC] 预览捕获失败:', err)
          rpc.respondPreviewClip(project_id!, '')
        })
      break
    }

    case 'export_started': {
      proj.exporting = true
      proj.statusText = '导出中...'
      proj.statusClass = 'running'
      break
    }

    case 'export_complete': {
      proj.exporting = false
      proj.statusText = '就绪'
      proj.statusClass = 'idle'
      const outputPath = (ev as any).output_path || ''
      proj.outputPath = outputPath
      proj.draftId = (ev as any).draft_id || proj.draftId
      proj.messages.push({
        id: nid(), role: 'assistant',
        content: outputPath
          ? `✅ 导出完成！视频已保存到 \`${outputPath}\``
          : `✅ 导出完成！`,
      })
      _saveProjectMeta(project_id)
      _saveChat(project_id)
      // 更新多任务列表
      const task = exportTasks.value.find(t => t.projectId === project_id && t.status === 'running')
      if (task) { task.status = 'done'; task.outputPath = outputPath }
      // 桌面通知 + Toast
      window.cherryclip?.notifications?.show('剪意', `「${proj.name}」导出完成`)
      toastSuccess(`「${proj.name}」导出完成`)
      break
    }

    case 'export_error': {
      proj.exporting = false
      proj.statusText = '就绪'
      proj.statusClass = 'idle'
      const errMsg = (ev as any).error || '未知错误'
      proj.messages.push({
        id: nid(), role: 'assistant',
        content: `❌ **导出失败**: ${errMsg.slice(0, 300)}`,
      })
      _saveChat(project_id!)
      // 更新多任务列表
      const task = exportTasks.value.find(t => t.projectId === project_id && t.status === 'running')
      if (task) { task.status = 'error'; task.errorMsg = errMsg }
      // Toast 错误提示
      toastError(`导出失败: ${errMsg.slice(0, 80)}`)
      break
    }
  }
}

// ── Actions（由子组件调用）──

function sendMessage(text: string) {
  const proj = _ensureActive()
  const pid = activeProjectId.value!
  console.log('[App] sendMessage:', text.slice(0, 50), '| pid:', pid, '| waitingForAnswer:', proj.waitingForAnswer)
  if (proj.waitingForAnswer) return

  proj.messages.push({ id: nid(), role: 'user', content: text })
  console.log('[App] 用户消息已推入消息列表, 当前消息数:', proj.messages.length)
  _saveChat(pid)

  if (!rpc.isElectron()) {
    console.log('[App] 非 Electron 环境，推送错误')
    proj.messages.push({ id: nid(), role: 'assistant', content: '❌ **后端不可用**：未检测到 Electron RPC 桥接。' })
    return
  }
  if (!connected.value) {
    console.log('[App] 后端未连接 (connected=false)，推送错误')
    proj.messages.push({ id: nid(), role: 'assistant', content: '❌ **后端未就绪**：Python RPC 服务未连接。' })
    return
  }

  // Director 已就绪 → 带工具回复；未就绪 → 纯聊天（不启动管线）
  proj.thinking = true
  if (proj._pipelineReady) {
    rpc.sendMessage(pid, text)
  } else {
    rpc.chat(pid, text)
  }
}

function startProject(text: string) {
  const proj = _ensureActive()
  const pid = activeProjectId.value!
  if (proj.waitingForAnswer || proj.running) return
  if (proj.materials.length === 0) return

  const intent = text.trim() || '帮我剪辑这段素材'
  proj.messages.push({ id: nid(), role: 'user', content: intent })
  _saveChat(pid)

  if (!rpc.isElectron()) {
    proj.messages.push({ id: nid(), role: 'assistant', content: '❌ **后端不可用**：未检测到 Electron RPC 桥接。' })
    return
  }
  if (!connected.value) {
    proj.messages.push({ id: nid(), role: 'assistant', content: '❌ **后端未就绪**：Python RPC 服务未连接。' })
    return
  }

  proj.thinking = true
  rpc.startProject(pid, proj.materials.map(m => m.path), intent)
}

// ── 确认后启动管线（防误触）──

const showStartConfirm = ref(false)

function confirmAndStartPipeline() {
  const proj = _ensureActive()
  if (!proj) return
  if (proj.materials.length === 0) return
  if (proj.running || proj.waitingForAnswer) return
  showStartConfirm.value = true
}

function doStartPipeline() {
  showStartConfirm.value = false
  const proj = _ensureActive()
  const pid = activeProjectId.value!
  if (!pid) return

  // 管线未初始化 → 先初始化（Director 创建 Pipeline + 打招呼）
  if (!proj._pipelineReady) {
    proj._pipelineReady = true
    rpc.startProject(pid, proj.materials.map(m => m.path), '帮我剪辑这段素材')
  }

  proj.messages.push({ id: nid(), role: 'user', content: '✅ 开始剪辑' })
  _saveChat(pid)
  rpc.startPipeline(pid)
}

function confirmPlan() {
  const proj = _ensureActive()
  const pid = activeProjectId.value!
  proj.messages.push({ id: nid(), role: 'user', content: '✅ 确认方案，开始剪辑' })
  // 标记管线就绪，后续消息走 sendMessage(带 Director 工具)而非 chat(纯聊天)
  proj._pipelineReady = true
  _saveChat(pid)
  rpc.startPipeline(pid)
}

function rejectPlan(reason: string) {
  const proj = _ensureActive()
  proj.messages.push({ id: nid(), role: 'user', content: reason || '方案不满意，请重新生成' })
  proj.messages = proj.messages.filter(m => m.type !== 'plan_card')
  _saveChat(activeProjectId.value!)
}

function respondAsk(answer: string) {
  const proj = _ensureActive()
  const pid = activeProjectId.value!
  if (!proj.waitingForAnswer) return
  proj.waitingForAnswer = false
  proj.messages.push({ id: nid(), role: 'user', content: answer })
  _saveChat(pid)
  rpc.respondAsk(pid, answer)
}

async function cancelProject(pid?: string) {
  const proj = pid ? projects[pid] : _ensureActive()
  if (!proj) return
  const targetId = pid || activeProjectId.value!
  if (!proj.running && !proj.progressRunning) return

  // 保存旧状态用于失败回滚
  const savedState = {
    running: proj.running,
    progressRunning: proj.progressRunning,
    progressTool: proj.progressTool,
    progressStage: proj.progressStage,
    stageProgress: { stages: [...proj.stageProgress.stages], workflow: proj.stageProgress.workflow },
    waitingForAnswer: proj.waitingForAnswer,
    statusText: proj.statusText,
    statusClass: proj.statusClass,
  }

  // 乐观更新
  proj.running = false
  proj.progressRunning = false
  proj.progressTool = ''
  proj.progressStage = ''
  proj.stageProgress = { stages: [], workflow: '' }
  proj.waitingForAnswer = false
  proj.statusText = '就绪'
  proj.statusClass = 'idle'
  proj.messages.push({ id: nid(), role: 'assistant', content: '⏹ 已取消。' })
  _saveChat(targetId)

  try {
    await rpc.cancelProject(targetId)
  } catch (e) {
    // RPC 失败 → 回滚到旧状态（同时移除已推的取消消息）
    console.error('[App] 取消失败:', e)
    proj.messages = proj.messages.filter(m => m.content !== '⏹ 已取消。')
    Object.assign(proj, savedState)
    _saveChat(targetId)
  }
}

async function duplicateProject(pid: string) {
  const src = projects[pid]
  if (!src) return
  try {
    const result = await rpc.createProject()
    const newPid = result.project_id
    projects[newPid] = _newProjectState(newPid)
    projects[newPid].name = `${src.name} - 副本`
    projects[newPid].materials = [...src.materials]
    await rpc.updateProject(newPid, { name: projects[newPid].name })
    activeProjectId.value = newPid
    router.push('/')
  } catch (err) {
    console.error('[App] 复制项目失败:', err)
    toastError('复制项目失败')
  }
}

// ── Material Handlers ──

function onFilesSelected(paths: string[]) {
  const proj = _ensureActive()
  let hasNew = false
  for (const p of paths) {
    const name = p.split(/[/\\]/).pop() || p
    // 验证路径：拒绝纯文件名（不含路径分隔符），拖拽导入的已知缺陷
    if (!p.includes('\\') && !p.includes('/')) {
      console.warn('[App] 拒绝无效路径（缺少目录）:', p)
      continue
    }
    const ext = (name.split('.').pop() || '').toLowerCase()
    const type: 'video' | 'audio' = ['mp3', 'wav', 'm4a', 'ogg', 'flac'].includes(ext) ? 'audio' : 'video'
    if (!proj.materials.some(m => m.path === p)) {
      proj.materials.push({ name, type, path: p })
      hasNew = true
    }
  }
  _saveProjectMeta(activeProjectId.value!)
  // 导入素材后自动改名（仅在未锁定时）
  if (hasNew && activeProjectId.value && !proj.nameLocked) {
    rpc.suggestProjectName(activeProjectId.value).then(res => {
      if (res && res.name && !res.locked) {
        proj.name = res.name
        _saveProjectMeta(activeProjectId.value!)
      }
    }).catch((e: any) => {
      console.warn('[App] 建议项目名称失败:', e)
    })
  }
}

function onRemoveMaterial(index: number) {
  const proj = _ensureActive()
  if (!proj) return
  proj.materials.splice(index, 1)
  // 素材删光了 → 重置初始化标记，下次导入可重新初始化
  if (proj.materials.length === 0) {
    proj._pipelineReady = false
  }
  _saveProjectMeta(activeProjectId.value!)
}

// ── Lifecycle ──

const _unsubRpc = ref<(() => void) | null>(null)

let _startupTimer: ReturnType<typeof setTimeout> | undefined
let _connectionTimer: ReturnType<typeof setTimeout> | undefined

function onGlobalKeyDown(e: KeyboardEvent) {
  // ? 键弹出快捷键面板
  if (e.key === '?' && !(e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement)) {
    e.preventDefault()
    showShortcuts.value = !showShortcuts.value
  }
  // Esc 关闭快捷键面板
  if (e.key === 'Escape' && showShortcuts.value) {
    showShortcuts.value = false
  }
}

onMounted(() => {
  _unsubRpc.value = rpc.onEvent(handleRpcEvent)
  rpc.initRpc()
  loadSettings()
  window.addEventListener('keydown', onGlobalKeyDown)

  // 检查登录状态，未登录则弹出认证
  if (!backendApi.isLoggedIn()) {
    setTimeout(() => { showAuth.value = true }, 500)
  }

  _startupTimer = setTimeout(() => {
    if (!rpc.isElectron()) {
      console.warn('[App] 非 Electron 环境')
    } else if (!connected.value) {
      _connectionTimer = setTimeout(() => {
        if (!connected.value) {
          console.error('[App] 后端超时')
          // 后端一直没连上，本地创建项目保证 UI 可用
          if (Object.keys(projects).length === 0) {
            _createFallbackProject()
          }
        }
      }, 5000)
    }
  }, 2000)
})

onUnmounted(() => {
  if (_startupTimer) clearTimeout(_startupTimer)
  if (_connectionTimer) clearTimeout(_connectionTimer)
  window.removeEventListener('keydown', onGlobalKeyDown)
  rpc.destroyRpc()
})

// ── Provide for children ──

provide('app', {
  projects: projectsList,
  activeProjectId,
  connected,
  createProject,
  switchProject,
  deleteProject,
  openDraft,
})

provide('project', computed(() => {
  const proj = activeProject.value
  if (!proj) return null
  return {
    state: proj,
    sendMessage,
    startProject,
    startPipeline: confirmAndStartPipeline,
    respondAsk,
    cancelProject: () => cancelProject(),
    confirmPlan,
    rejectPlan,
  }
}))
</script>

<style>
/* ===========================================================
   ClipMind Design System — Liquid Glass-inspired
   8px grid · 4-layer elevation · Semantic tokens
   =========================================================== */

:root {
  --brand: #6366F1;
  --brand-light: #818CF8;
  --brand-subtle: rgba(99, 102, 241, 0.15);
  --brand-glow: rgba(99, 102, 241, 0.28);
  --accent-green: #22C55E;
  --accent-amber: #F59E0B;
  --accent-red: #EF4444;
  --accent-cyan: #06B6D4;

  /* 圆角系统（8px 基准） */
  --radius-sm: 6px;
  --radius: 8px;
  --radius-lg: 12px;
  --radius-xl: 16px;
  --radius-full: 999px;
}

/* ── 暗色主题（默认）── */
:root,
[data-theme="dark"] {
  /* 4 层暗色材质（已整体提亮） */
  --surface-base: #121214;       /* 最底层背景 */
  --surface-raised: #18181B;      /* 面板/侧栏 */
  --surface-overlay: #202026;     /* 输入区/卡片 */
  --surface-elevated: #28282E;    /* 最高层（模态框/菜单） */

  --surface-glass: rgba(24, 24, 27, 0.78);
  --surface-glass-edge: rgba(255, 255, 255, 0.08);

  --bg-hover: rgba(255, 255, 255, 0.07);
  --bg-active: rgba(99, 102, 241, 0.10);

  /* 边框 — 越来越细 */
  --border-subtle: rgba(255, 255, 255, 0.08);
  --border-card: rgba(255, 255, 255, 0.11);
  --border-active: rgba(99, 102, 241, 0.35);

  /* 文字层级 */
  --text-primary: #EAEAEF;
  --text-secondary: #9C9CA6;
  --text-muted: #6E6E7A;
  --text-on-brand: #FFF;
  --text-accent: #A5B4FC;

  /* 阴影 */
  --shadow-sm: 0 1px 3px rgba(0, 0, 0, 0.3);
  --shadow-md: 0 4px 16px rgba(0, 0, 0, 0.35);
  --shadow-lg: 0 8px 40px rgba(0, 0, 0, 0.5);

  /* 特殊 */
  --overlay-bg: rgba(0, 0, 0, 0.6);
  --bg-code: rgba(0, 0, 0, 0.25);
  --bg-logo: #000;

  /* 兼容别名（旧组件引用） */
  --bg-root: var(--surface-base);
  --bg-panel: var(--surface-raised);
  --bg-input-zone: var(--surface-overlay);
  --bg-card: var(--surface-overlay);
  --bg-input: var(--surface-overlay);
  --shadow-toast: var(--shadow-md);
}

/* ── 亮色主题 ── */
[data-theme="light"] {
  --surface-base: #F5F5F7;
  --surface-raised: #FFFFFF;
  --surface-overlay: #F9F9FB;
  --surface-elevated: #FFFFFF;

  --surface-glass: rgba(255, 255, 255, 0.82);
  --surface-glass-edge: rgba(0, 0, 0, 0.06);

  --bg-hover: rgba(0, 0, 0, 0.04);
  --bg-active: rgba(99, 102, 241, 0.06);

  --border-subtle: rgba(0, 0, 0, 0.06);
  --border-card: rgba(0, 0, 0, 0.08);
  --border-active: rgba(99, 102, 241, 0.25);

  --text-primary: #1C1C1E;
  --text-secondary: #6C6C74;
  --text-muted: #9C9CA6;
  --text-on-brand: #FFF;
  --text-accent: #6366F1;

  --shadow-sm: 0 1px 3px rgba(0, 0, 0, 0.06);
  --shadow-md: 0 4px 16px rgba(0, 0, 0, 0.06);
  --shadow-lg: 0 8px 40px rgba(0, 0, 0, 0.08);

  --overlay-bg: rgba(0, 0, 0, 0.15);
  --bg-code: #F0F0F4;
  --bg-logo: #000;

  /* 兼容别名 */
  --bg-root: var(--surface-base);
  --bg-panel: var(--surface-raised);
  --bg-input-zone: var(--surface-overlay);
  --bg-card: var(--surface-overlay);
  --bg-input: var(--surface-overlay);
  --shadow-toast: var(--shadow-md);
}

/* ── 重置 ── */
* { margin: 0; padding: 0; box-sizing: border-box; }

html, body, #app {
  height: 100%;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Microsoft YaHei", "PingFang SC", sans-serif;
  background: var(--surface-base);
  color: var(--text-primary);
  overflow: hidden;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  font-size: 13px;
  line-height: 1.5;
}

/* ── 滚动条 ── */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.09); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: rgba(255, 255, 255, 0.18); }

::selection { background: rgba(99, 102, 241, 0.3); color: #FFF; }

/* ── 布局 ── */
#layout {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.layout-body {
  display: flex;
  flex: 1;
  min-height: 0;
}

.main-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  position: relative;
}

/* ========== 拖拽手柄 ========== */
.resize-handle {
  width: 3px;
  cursor: col-resize;
  background: transparent;
  flex-shrink: 0;
  position: relative;
  z-index: 10;
  transition: background 0.2s;
}
.resize-handle:hover,
.resize-handle:active {
  background: var(--brand);
}

/* ========== 路由过渡动画 ========== */
.page-enter-active,
.page-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}
.page-enter-from {
  opacity: 0;
  transform: translateY(6px);
}
.page-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
