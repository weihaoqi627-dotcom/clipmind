<template>
  <aside class="left-panel">
    <!-- 顶部：Logo / 项目名 -->
    <div class="panel-header">
      <div class="logo">
        <SvgIcon name="logo" size="24" color="#FFF" />
      </div>
      <span class="title">ClipMind</span>
      <span class="conn-badge" :class="{ on: connected }" :title="connected ? '后端服务已连接' : '后端服务未连接'">
        <span class="conn-dot"></span>{{ connected ? '已连接' : '未连接' }}
      </span>
    </div>

    <!-- 项目抽屉 -->
    <div class="drawer" :class="{ expanded: projectExpanded }">
      <button class="drawer-header" @click="projectExpanded = !projectExpanded">
        <span class="drawer-arrow">{{ projectExpanded ? '▾' : '▸' }}</span>
        <SvgIcon name="folder" size="14" />
        <span class="drawer-title">项目</span>
        <span v-if="projects.length > 0" class="drawer-count">{{ projects.length }}</span>
      </button>
      <div v-if="projectExpanded" class="drawer-body">
        <button class="btn-new-project" @click="$emit('create-project')">+ 创建新项目</button>
        <div v-if="projects.length === 0" class="empty-hint">点击上方「+ 创建新项目」开始</div>
        <div
          v-for="p in sortedProjects"
          :key="p.id"
          class="project-item"
          :class="{ active: p.id === activeProjectId, running: p.running }"
          @click="editingPid !== p.id && $emit('switch-project', p.id)"
          @contextmenu.prevent.stop="openCtxMenu($event, p.id, 'project')"
        >
          <div class="project-left">
            <span class="project-dot" :class="p.running ? 'dot-running' : 'dot-idle'"></span>
            <!-- 编辑模式 -->
            <input
              v-if="editingPid === p.id"
              :ref="(el: any) => { if (el) renameInputs[p.id] = el }"
              v-model="renameValue"
              class="project-name-input"
              @keydown.enter="saveRename(p.id)"
              @keydown.escape="cancelRename"
              @blur="saveRename(p.id)"
              @click.stop
            />
            <!-- 显示模式 -->
            <span
              v-else
              class="project-name"
              :title="p.name"
              @click.stop="startRename(p)"
            >{{ p.name }}</span>
          </div>
          <div class="project-actions">
            <button class="project-menu-btn" @click.stop="openCtxMenu($event, p.id, 'project')" title="更多操作">⋮</button>
            <button class="project-delete" @click.stop="onDeleteProject(p.id)" title="删除项目">×</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 素材抽屉 -->
    <div class="drawer" :class="{ expanded: materialExpanded }">
      <button class="drawer-header" @click="materialExpanded = !materialExpanded">
        <span class="drawer-arrow">{{ materialExpanded ? '▾' : '▸' }}</span>
        <SvgIcon name="folder" size="14" />
        <span class="drawer-title">素材</span>
        <span v-if="materials.length > 0" class="drawer-count">{{ materials.length }}</span>
      </button>
      <div v-if="materialExpanded" class="drawer-body">
        <div class="drop-zone" @dragover.prevent @drop="onDrop">
          <div class="drop-icon">+</div>
          <p class="drop-text">拖拽素材到此处</p>
          <button class="btn-select" @click="selectFiles">选择视频/音频</button>
        </div>
        <div v-if="materials.length > 0" class="material-list">
          <div v-for="(m, i) in materials" :key="i" class="material-item" :class="'type-' + m.type" :title="m.path" @contextmenu.prevent.stop="openCtxMenu($event, m.path, 'material', i)">
            <span class="mat-icon"><SvgIcon :name="m.type === 'video' ? 'play' : 'music'" size="14" /></span>
            <span class="mat-name">{{ m.name }}</span>
            <span class="mat-remove" @click="$emit('remove-material', i)">×</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 草稿抽屉 -->
    <div class="drawer" :class="{ expanded: draftExpanded }">
      <button class="drawer-header" @click="draftExpanded = !draftExpanded">
        <span class="drawer-arrow">{{ draftExpanded ? '▾' : '▸' }}</span>
        <SvgIcon name="file" size="14" />
        <span class="drawer-title">草稿</span>
        <span v-if="allDrafts.length > 0" class="drawer-count">{{ allDrafts.length }}</span>
      </button>
      <div v-if="draftExpanded" class="drawer-body">
        <div v-if="allDrafts.length === 0" class="empty-hint">启动剪辑后，草稿会自动生成</div>
        <div
          v-for="d in allDrafts"
          :key="d.draftId"
          class="draft-item"
          @click="$emit('open-draft', d.draftId)"
          @contextmenu.prevent.stop="openCtxMenu($event, d.draftId, 'draft')"
        >
          <div class="draft-left">
            <SvgIcon name="play" size="12" />
            <span class="draft-id">{{ d.draftId }}</span>
          </div>
          <div class="draft-right">
            <span class="draft-project">{{ d.projectName }}</span>
            <button class="draft-delete" @click.stop="onDeleteDraft(d)" title="删除草稿">×</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 历史抽屉（已完成视频） -->
    <div class="drawer" :class="{ expanded: historyExpanded }">
      <button class="drawer-header" @click="historyExpanded = !historyExpanded">
        <span class="drawer-arrow">{{ historyExpanded ? '▾' : '▸' }}</span>
        <SvgIcon name="history" size="14" />
        <span class="drawer-title">历史</span>
        <span v-if="historyDrafts.length > 0" class="drawer-count">{{ historyDrafts.length }}</span>
      </button>
      <div v-if="historyExpanded" class="drawer-body">
        <div v-if="historyLoading" class="loading-spin">
          <span class="spinner"></span>
          <span>加载中...</span>
        </div>
        <div v-else-if="historyDrafts.length === 0" class="empty-hint">导出完成后，视频会出现在这里</div>
        <div
          v-for="h in historyDrafts"
          :key="h.draftId"
          class="history-item"
        >
          <div class="draft-left" @click="$emit('open-draft', h.draftId)" style="cursor:pointer">
            <SvgIcon name="play" size="12" />
            <span class="draft-id">{{ h.draftId }}</span>
          </div>
          <div class="draft-right">
            <span class="draft-project">{{ h.projectName }}</span>
            <button class="draft-delete" @click.stop="onDeleteHistory(h)" title="删除历史">×</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 右键菜单 -->
    <Teleport to="body">
      <div v-if="ctx.visible" class="ctx-overlay" @click="closeCtxMenu" @contextmenu.prevent="closeCtxMenu"></div>
      <div v-if="ctx.visible" class="ctx-menu" :style="{ top: ctx.y + 'px', left: ctx.x + 'px' }">
        <template v-if="ctx.type === 'project'">
          <button class="ctx-item" @click="ctxRename"><span class="ctx-icon">✏️</span> 重命名</button>
          <button class="ctx-item" @click="ctxDuplicate"><span class="ctx-icon">📋</span> 复制项目</button>
          <div class="ctx-sep"></div>
          <button class="ctx-item ctx-danger" @click="ctxDelete"><span class="ctx-icon">🗑</span> 删除项目</button>
        </template>
        <template v-if="ctx.type === 'draft'">
          <button class="ctx-item" @click="ctxOpenDraft"><span class="ctx-icon">👁</span> 预览草稿</button>
          <div class="ctx-sep"></div>
          <button class="ctx-item ctx-danger" @click="ctxDeleteDraft"><span class="ctx-icon">🗑</span> 删除草稿</button>
        </template>
        <template v-if="ctx.type === 'material'">
          <button class="ctx-item ctx-danger" @click="ctxRemoveMaterial"><span class="ctx-icon">🗑</span> 移除素材</button>
        </template>
      </div>
    </Teleport>

    <!-- 确认弹窗 -->
    <ConfirmModal
      :visible="confirm.visible"
      :title="confirm.title"
      :message="confirm.message"
      :confirm-text="confirm.confirmText"
      @confirm="confirm.onConfirm"
      @cancel="confirm.onCancel"
    />

    <!-- 回收站抽屉 -->
    <div class="drawer" :class="{ expanded: trashExpanded }">
      <button class="drawer-header" @click="toggleTrash">
        <span class="drawer-arrow">{{ trashExpanded ? '▾' : '▸' }}</span>
        <span class="trash-drawer-icon">🗑</span>
        <span class="drawer-title">回收站</span>
        <span v-if="trashItems.length > 0" class="drawer-count trash-count">{{ trashItems.length }}</span>
      </button>
      <div v-if="trashExpanded" class="drawer-body">
        <div v-if="trashLoading" class="loading-spin">
          <span class="spinner"></span>
          <span>加载中...</span>
        </div>
        <div v-else-if="trashItems.length === 0" class="empty-hint">回收站是空的</div>
        <div
          v-for="item in trashItems"
          :key="item.project_id"
          class="trash-item"
        >
          <div class="trash-left">
            <span class="trash-name">{{ item.name }}</span>
            <span class="trash-time">{{ formatTimeAgo(item.deleted_at) }}</span>
          </div>
          <div class="trash-actions">
            <button class="trash-restore" @click="onRestore(item.project_id)" title="恢复">↩</button>
            <button class="trash-perm-delete" @click="onPermDelete(item)" title="永久删除">×</button>
          </div>
        </div>
        <button class="btn-rescan" @click="onRescan">🔄 重新扫描项目</button>
      </div>
    </div>

    <!-- 操作入口：账户 + 预览 + 导出 -->
    <div class="action-buttons">
      <button class="action-btn" :class="{ active: isAccountPage }" @click="goAccount">
        <SvgIcon name="user" size="16" />
        <span>账户</span>
      </button>
      <button class="action-btn" :class="{ active: isPreviewPage }" @click="goPreview">
        <SvgIcon name="play" size="16" />
        <span>预览</span>
      </button>
      <button
        class="action-btn export-btn"
        :disabled="!projState?.draftId || projState?.exporting"
        :title="!projState?.draftId ? '暂无草稿可导出' : '导出最终视频'"
        @click="onExport"
      >
        <SvgIcon name="export" size="16" />
        <span>{{ projState?.exporting ? '导出中...' : '导出' }}</span>
      </button>
    </div>

    <!-- 底部：状态 -->
    <div class="panel-footer">
      <div class="footer-left">
        <button class="footer-settings-btn" @click="$emit('open-settings')" title="设置">
          <SvgIcon name="gear" size="14" />
        </button>
        <span class="footer-conn" :class="{ on: connected }">{{ connected ? '● 已连接' : '○ 未连接' }}</span>
      </div>
      <div class="footer-right">
        <span v-if="loggedIn" class="footer-user" title="点击退出" @click="$emit('logout')" style="cursor:pointer">
          {{ authUser?.display_name || authUser?.email?.split('@')[0] || '已登录' }}
        </span>
        <button v-else class="footer-login-btn" @click="$emit('show-auth')">登录</button>
        <span class="footer-status" :class="projState?.statusClass || 'idle'">{{ projState?.statusText || '就绪' }}</span>
      </div>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { ref, computed, inject, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import SvgIcon from './SvgIcon.vue'
import ConfirmModal from './ConfirmModal.vue'
import * as rpc from '../services/rpc'
import { useToast } from '../composables/useToast'
import type { Material } from '../types'

const { error: toastError } = useToast()

const router = useRouter()

const props = defineProps<{
  projects: Array<{ id: string; name: string; running: boolean; materials: Material[]; draftId: string; createdAt: number }>
  activeProjectId: string | null
  connected: boolean
  loggedIn: boolean
  authUser: any
}>()

const emit = defineEmits<{
  'create-project': []
  'switch-project': [id: string]
  'delete-project': [id: string]
  'files-selected': [paths: string[]]
  'remove-material': [index: number]
  'open-draft': [draftId: string]
  'refresh-drafts': []
  'open-settings': []
  'duplicate-project': [id: string]
  'restore-project': [id: string]
  'rescan-projects': []
  'show-auth': []
  'logout': []
}>()

// 从 App.vue 注入当前活跃项目（provide 传的是 ComputedRef，需要 .value）
const _injectedProject: any = inject('project', null)
const projectState = computed(() => {
  // inject 返回 ComputedRef，需要 .value 取值
  const p = _injectedProject?.value ?? _injectedProject
  return p?.state ?? null
})
const materials = computed(() => projectState.value?.materials ?? [])
const projState = computed(() => projectState.value)

const projectExpanded = ref(true)
const materialExpanded = ref(true)
const draftExpanded = ref(false)
const historyExpanded = ref(false)
const trashExpanded = ref(false)
const trashItems = ref<any[]>([])
const trashLoading = ref(false)

// ── 项目重命名 ──

const editingPid = ref<string | null>(null)
const renameValue = ref('')
const renameInputs: Record<string, HTMLInputElement> = {}

function startRename(p: { id: string; name: string }) {
  editingPid.value = p.id
  renameValue.value = p.name
  nextTick(() => {
    const el = renameInputs[p.id]
    if (el) {
      el.focus()
      el.select()
    }
  })
}

async function saveRename(pid: string) {
  const name = renameValue.value.trim()
  if (!name || name === props.projects.find(p => p.id === pid)?.name) {
    cancelRename()
    return
  }
  try {
    await rpc.updateProject(pid, { name })
    emit('refresh-drafts')
  } catch (e) {
    console.error('[LeftPanel] 重命名项目失败:', e)
    toastError('重命名项目失败')
  }
  cancelRename()
}

function cancelRename() {
  editingPid.value = null
  renameValue.value = ''
}

// ── 右键菜单 ──

interface CtxState { visible: boolean; x: number; y: number; targetId: string; type: 'project' | 'draft' | 'material'; extra?: any }
const ctx = ref<CtxState>({ visible: false, x: 0, y: 0, targetId: '', type: 'project' })

function openCtxMenu(e: MouseEvent, targetId: string, type: 'project' | 'draft' | 'material', extra?: any) {
  const rect = (e.currentTarget as HTMLElement)?.getBoundingClientRect?.()
  const x = e.clientX
  const y = e.clientY
  // 防止菜单溢出屏幕
  const menuW = 170; const menuH = type === 'project' ? 120 : (type === 'draft' ? 80 : 36)
  const adjX = Math.min(x, window.innerWidth - menuW - 8)
  const adjY = Math.min(y, window.innerHeight - menuH - 8)
  ctx.value = { visible: true, x: adjX, y: adjY, targetId, type, extra }
}

function closeCtxMenu() { ctx.value.visible = false }

function ctxRename() {
  const p = props.projects.find(p => p.id === ctx.value.targetId)
  if (p) { startRename(p); closeCtxMenu() }
}
function ctxDuplicate() {
  emit('duplicate-project', ctx.value.targetId)
  closeCtxMenu()
}
function ctxDelete() {
  onDeleteProject(ctx.value.targetId)
  closeCtxMenu()
}
function ctxOpenDraft() {
  emit('open-draft', ctx.value.targetId)
  closeCtxMenu()
}
function ctxDeleteDraft() {
  const d = allDrafts.value.find(d => d.draftId === ctx.value.targetId)
  if (d) { onDeleteDraft(d); closeCtxMenu() }
}
function ctxRemoveMaterial() {
  const idx = ctx.value.extra
  if (idx != null) { emit('remove-material', idx); closeCtxMenu() }
}

// 键盘监听 F2 → 重命名活跃项目
function onKeyDown(e: KeyboardEvent) {
  if (e.key === 'F2' && props.activeProjectId && editingPid.value !== props.activeProjectId) {
    const p = props.projects.find(p => p.id === props.activeProjectId)
    if (p) { e.preventDefault(); startRename(p) }
  }
}

// ── 确认弹窗 ──

interface ConfirmState {
  visible: boolean
  title: string
  message: string
  confirmText: string
  onConfirm: () => void
  onCancel: () => void
}

const confirm = ref<ConfirmState>({
  visible: false,
  title: '',
  message: '',
  confirmText: '确认删除',
  onConfirm: () => closeConfirm(),
  onCancel: () => closeConfirm(),
})

function closeConfirm() {
  confirm.value.visible = false
}

function showConfirm(title: string, message: string, onConfirm: () => void) {
  confirm.value = {
    visible: true,
    title,
    message,
    confirmText: '确认删除',
    onConfirm: () => { onConfirm(); closeConfirm() },
    onCancel: () => closeConfirm(),
  }
}

// ── 项目排序：活跃 > 最近创建 ──

const sortedProjects = computed(() => {
  return [...props.projects].sort((a, b) => {
    if (a.id === props.activeProjectId) return -1
    if (b.id === props.activeProjectId) return 1
    return b.createdAt - a.createdAt
  })
})

// ── 草稿 ──

const allDrafts = computed(() => {
  const drafts: Array<{ draftId: string; projectName: string }> = []
  for (const p of props.projects) {
    if (p.draftId) {
      drafts.push({ draftId: p.draftId, projectName: p.name })
    }
  }
  return drafts
})

function onDeleteDraft(d: { draftId: string; projectName: string }) {
  showConfirm(
    '删除草稿',
    `确定要删除草稿「${d.draftId}」吗？\n所有版本数据和关联的输出文件都将被永久删除。`,
    async () => {
      try {
        await rpc.deleteDraft(d.draftId)
        emit('refresh-drafts')
      } catch (e) {
        console.error('[LeftPanel] 删除草稿失败:', e)
        toastError('删除草稿失败')
      }
    }
  )
}

// ── 历史（已完成的草稿，有输出文件）──

const historyDrafts = ref<Array<{ draftId: string; projectName: string; outputPath: string }>>([])
const historyLoading = ref(false)

async function loadHistory() {
  historyLoading.value = true

  try {
    const drafts = await rpc.listDrafts()
    historyDrafts.value = drafts
      .filter((d: any) => d.has_output)
      .map((d: any) => ({
        draftId: d.draft_id,
        projectName: d.name || d.draft_id,
        outputPath: d.output_path,
      }))
  } catch (e) {
    console.error('[LeftPanel] 加载历史列表失败:', e)
  } finally {
    historyLoading.value = false
  }
}

function onDeleteHistory(h: { draftId: string; projectName: string }) {
  showConfirm(
    '删除历史',
    `确定要删除「${h.draftId}」的输出文件和历史记录吗？\n输出视频将被永久删除。`,
    async () => {
      try {
        await rpc.deleteDraft(h.draftId)
        loadHistory()
      } catch (e) {
        console.error('[LeftPanel] 删除历史记录失败:', e)
        toastError('删除历史记录失败')
      }
    }
  )
}

// ── 回收站 ──

async function toggleTrash() {
  trashExpanded.value = !trashExpanded.value
  if (trashExpanded.value && trashItems.value.length === 0) {
    await loadTrash()
  }
}

async function loadTrash() {
  trashLoading.value = true
  try {
    trashItems.value = await rpc.listTrash()
  } catch (e) {
    console.error('[LeftPanel] 加载回收站失败:', e)
    toastError('加载回收站失败')
  } finally {
    trashLoading.value = false
  }
}

async function onRestore(pid: string) {
  try {
    await rpc.restoreProject(pid)
    trashItems.value = trashItems.value.filter((i: any) => i.project_id !== pid)
    emit('restore-project', pid)
  } catch (e) {
    console.error('[LeftPanel] 恢复项目失败:', e)
    toastError('恢复项目失败')
  }
}

function onPermDelete(item: any) {
  showConfirm(
    '永久删除',
    `确定要永久删除「${item.name}」吗？\n聊天记录和素材将彻底清除，无法恢复。`,
    async () => {
      try {
        await rpc.permanentlyDeleteProject(item.project_id)
        trashItems.value = trashItems.value.filter((i: any) => i.project_id !== item.project_id)
      } catch (e) {
        console.error('[LeftPanel] 永久删除项目失败:', e)
        toastError('永久删除项目失败')
      }
    }
  )
}

async function onRescan() {
  try {
    await rpc.rescanProjects()
    emit('rescan-projects')
  } catch (e) {
    console.error('[LeftPanel] 重新扫描项目失败:', e)
    toastError('重新扫描项目失败')
  }
}

function formatTimeAgo(ts: number): string {
  const diff = Date.now() / 1000 - ts
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  const days = Math.floor(diff / 86400)
  return `${days} 天前`
}

onMounted(() => { loadHistory(); window.addEventListener('keydown', onKeyDown) })
onUnmounted(() => { window.removeEventListener('keydown', onKeyDown) })

// ── 页面导航 ──
const currentPath = computed(() => router.currentRoute.value.path)
const isAccountPage = computed(() => currentPath.value.startsWith('/account'))
const isPreviewPage = computed(() => currentPath.value === '/preview')

function goAccount() {
  router.push('/account')
}

function goPreview() {
  router.push('/preview')
}

// ── 导出 ──
const exportModal: any = inject('exportModal', null)

function onExport() {
  const state = projectState.value
  if (!state?.draftId || state?.exporting) return
  const pid = props.activeProjectId
  exportModal?.open(state.draftId, pid)
}

function onDeleteProject(pid: string) {
  if (props.projects.length <= 1) {
    toastError('至少保留一个项目')
    return
  }
  const proj = props.projects.find(p => p.id === pid)
  const name = proj?.name || pid
  showConfirm(
    '删除项目',
    `确定要删除项目「${name}」吗？\n项目内的聊天记录和导入素材都将被永久删除。`,
    () => emit('delete-project', pid)
  )
}

// 文件选择
function onDrop(e: DragEvent) {
  const files = e.dataTransfer?.files
  if (!files || files.length === 0) return

  const videoExts = new Set(['.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'])
  const audioExts = new Set(['.mp3', '.wav', '.m4a', '.ogg', '.flac', '.aac'])

  // 收集路径：先试 File.path，再试 text/uri-list
  let paths: string[] = []
  let hasValidType = false

  for (const file of files) {
    const ext = (file.name.split('.').pop() || '').toLowerCase()
    const isVideo = file.type.startsWith('video') || videoExts.has('.' + ext)
    const isAudio = file.type.startsWith('audio') || audioExts.has('.' + ext)
    if (!isVideo && !isAudio) continue
    hasValidType = true

    // Electron 35+: 用 webUtils.getPathForFile（优先于已废弃的 File.path）
    const fp = (window as any).cherryclip?.getPathForFile?.(file) || (file as any).path
    if (fp && (fp.includes('\\') || fp.includes('/'))) {
      paths.push(fp)
    }
  }

  // 回退 1：从 text/uri-list 解析（Windows Explorer 拖拽会带此数据）
  if (paths.length === 0 && hasValidType) {
    const uriList = e.dataTransfer?.getData('text/uri-list')
    if (uriList) {
      const uris = uriList.split('\n').map(u => u.trim()).filter(Boolean)
      for (const uri of uris) {
        if (uri.startsWith('file:///')) {
          const p = decodeURIComponent(uri.replace(/^file:\/\/\//, ''))
          if (p) paths.push(p)
        }
      }
    }
  }

  if (paths.length > 0) {
    emit('files-selected', paths)
  } else if (hasValidType) {
    // 回退 2：所有方案都拿不到路径 → 弹出文件选择器
    selectFiles()
  }
}

async function selectFiles() {
  try {
    const paths = await window.cherryclip?.dialogs.openFiles({
      filters: [
        { name: '视频/音频', extensions: ['mp4', 'mov', 'avi', 'mkv', 'webm', 'mp3', 'wav', 'm4a'] }
      ]
    })
    if (paths && paths.length > 0) {
      emit('files-selected', paths)
    }
  } catch (err) {
    console.error('选择文件失败:', err)
  }
}
</script>

<style scoped>
/* ========== 侧栏容器 — 毛玻璃 ========== */
.left-panel {
  background: var(--surface-glass);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border-right: 1px solid var(--surface-glass-edge);
  display: flex;
  flex-direction: column;
  height: 100%;
  user-select: none;
}

/* ========== 顶部品牌区 ========== */
.panel-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 16px 12px;
  border-bottom: 1px solid var(--border-subtle);
}

.logo {
  width: 28px;
  height: 28px;
  background: var(--bg-logo);
  border-radius: 7px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  box-shadow: 0 0 10px var(--brand-glow);
}

.title {
  font-size: 14px;
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: -0.1px;
}

.conn-badge {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 10px;
  font-weight: 500;
  color: var(--text-secondary);
  padding: 2px 8px;
  border-radius: var(--radius-full);
  border: 1px solid var(--border-subtle);
  transition: all 0.2s;
}
.conn-badge.on {
  color: var(--accent-green);
  border-color: rgba(34, 197, 94, 0.25);
  background: rgba(34, 197, 94, 0.04);
}
.conn-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--text-muted);
  transition: background 0.2s;
}
.conn-badge.on .conn-dot {
  background: var(--accent-green);
  box-shadow: 0 0 4px rgba(34, 197, 94, 0.4);
}

/* ========== 抽屉通用 ========== */
.drawer {
  border-bottom: 1px solid var(--border-subtle);
}

.drawer-header {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 9px 16px;
  background: transparent;
  border: none;
  color: var(--text-secondary);
  cursor: pointer;
  font-family: inherit;
  font-size: 12px;
  font-weight: 500;
  transition: background 0.1s;
  text-align: left;
}
.drawer-header:hover { background: var(--bg-hover); }

.drawer-arrow {
  font-size: 9px;
  color: var(--text-muted);
  width: 10px;
  flex-shrink: 0;
}

.drawer-title { flex: 1; }

.drawer-count {
  font-size: 10px;
  color: var(--text-muted);
  background: var(--bg-hover);
  padding: 1px 6px;
  border-radius: 6px;
  font-weight: 600;
}

.drawer-body {
  padding: 0 12px 10px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

/* ========== 项目列表 ========== */
.btn-new-project {
  width: 100%;
  padding: 8px;
  background: var(--bg-active);
  border: 1px solid var(--border-active);
  border-radius: var(--radius-sm);
  color: var(--text-accent);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.12s;
}
.btn-new-project:hover {
  background: rgba(99, 102, 241, 0.12);
}

.project-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 7px 8px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.12s;
}
.project-item:hover { background: var(--bg-hover); }
.project-item.active {
  background: var(--bg-active);
  border-left: 2px solid var(--brand);
  padding-left: 6px;
}
.project-item.running {
  border-left: 2px solid var(--accent-green);
  padding-left: 6px;
}
.project-item.active.running { border-left: 2px solid var(--brand); }

.project-left {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.project-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}
.dot-idle { background: var(--text-muted); }
.dot-running { background: var(--accent-green); animation: dotPulse 1.5s ease-in-out infinite; }

@keyframes dotPulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

.project-name {
  font-size: 12px;
  color: var(--text-primary);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: default;
  opacity: 0.85;
}

.project-name-input {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-primary);
  background: var(--surface-overlay);
  border: 1px solid var(--border-active);
  border-radius: 4px;
  padding: 2px 5px;
  width: 100%;
  font-family: inherit;
  outline: none;
}

.project-delete {
  background: transparent;
  border: none;
  color: var(--text-muted);
  font-size: 14px;
  cursor: pointer;
  padding: 0 2px;
  border-radius: 3px;
  flex-shrink: 0;
  transition: all 0.1s;
  line-height: 1;
  opacity: 0.4;
}
.project-item:hover .project-delete { opacity: 1; }
.project-delete:hover { color: var(--accent-red); background: rgba(239, 68, 68, 0.08); }

/* ========== 草稿/历史列表 ========== */
.draft-item,
.history-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 7px 8px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.12s;
}
.draft-item:hover { background: var(--bg-hover); }
.history-item:hover { background: var(--bg-hover); }

.draft-left {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--accent-green);
  min-width: 0;
}

.draft-id {
  font-size: 11px;
  font-family: 'SF Mono', 'Cascadia Code', monospace;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.draft-right {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.draft-project {
  font-size: 10px;
  color: var(--text-muted);
}

.draft-delete {
  background: transparent;
  border: none;
  color: var(--text-muted);
  font-size: 14px;
  cursor: pointer;
  padding: 0 2px;
  border-radius: 3px;
  flex-shrink: 0;
  transition: all 0.1s;
  line-height: 1;
}
.draft-delete:hover { color: var(--accent-red); background: rgba(239, 68, 68, 0.08); }

/* ========== 回收站 ========== */
.trash-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 8px;
  margin-bottom: 3px;
  border-radius: var(--radius-sm);
  font-size: 11px;
  transition: all 0.12s;
}
.trash-item:hover { background: var(--bg-hover); }
.trash-left {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
  flex: 1;
}
.trash-name { color: var(--text-secondary); font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.trash-time { color: var(--text-muted); font-size: 10px; }
.trash-actions { display: flex; gap: 3px; flex-shrink: 0; }
.trash-restore, .trash-perm-delete {
  width: 24px; height: 24px;
  border: none; border-radius: 4px;
  font-size: 13px; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: all 0.12s;
  background: transparent; color: var(--text-muted);
}
.trash-restore:hover { background: rgba(34, 197, 94, 0.12); color: var(--accent-green); }
.trash-perm-delete:hover { background: rgba(239, 68, 68, 0.12); color: var(--accent-red); }
.trash-count { background: rgba(239, 68, 68, 0.15) !important; color: var(--accent-red) !important; }
.btn-rescan {
  width: 100%;
  margin-top: 4px;
  padding: 6px 8px;
  border: 1px dashed var(--border-subtle);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-muted);
  font-size: 11px;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.12s;
  text-align: center;
}
.btn-rescan:hover { border-color: var(--brand); color: var(--text-accent); background: var(--bg-active); }

/* ========== 操作入口 ========== */
.action-buttons {
  border-top: 1px solid var(--border-subtle);
}
.action-btn, .preview-btn, .export-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 10px 16px;
  background: transparent;
  border: none;
  border-bottom: 1px solid var(--border-subtle);
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 500;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.12s;
  text-align: left;
}
.action-btn:hover { background: var(--bg-hover); color: var(--brand); }
.action-btn.active { color: var(--brand); background: var(--bg-active); }
.preview-btn:hover { background: var(--bg-hover); color: var(--accent-green); }
.export-btn:hover:not(:disabled) { background: var(--bg-hover); color: var(--brand); }
.export-btn:disabled { opacity: 0.3; cursor: not-allowed; }

/* ========== 拖拽区 ========== */
.drop-zone {
  border: 1.5px dashed var(--border-card);
  border-radius: var(--radius);
  padding: 20px 14px;
  text-align: center;
  cursor: pointer;
  transition: all 0.2s;
  background: var(--bg-hover);
}
.drop-zone:hover { border-color: var(--brand); background: var(--bg-active); }

.drop-icon { font-size: 26px; font-weight: 200; color: var(--text-accent); line-height: 1; margin-bottom: 8px; }
.drop-text { font-size: 12px; color: var(--text-secondary); font-weight: 500; margin-bottom: 10px; }

.btn-select {
  padding: 6px 16px;
  background: var(--brand);
  border: none;
  border-radius: var(--radius-sm);
  color: var(--text-on-brand);
  font-size: 11px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.12s;
  font-family: inherit;
}
.btn-select:hover { background: var(--brand-light); }

/* ========== 素材列表 ========== */
.material-list { display: flex; flex-direction: column; gap: 2px; }
.material-item {
  display: flex; align-items: center; gap: 6px;
  padding: 7px 8px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  transition: all 0.12s;
}
.material-item:hover { background: var(--bg-hover); }
.mat-icon { flex-shrink: 0; opacity: 0.4; }
.mat-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-secondary); }
.mat-remove {
  color: var(--text-muted); cursor: pointer; font-size: 14px;
  padding: 0 2px; border-radius: 3px; transition: all 0.1s;
}
.mat-remove:hover { color: var(--accent-red); background: rgba(239, 68, 68, 0.08); }

.material-item.type-video { border-left: 2px solid transparent; }
.material-item.type-video:hover { border-left-color: var(--accent-cyan); }
.material-item.type-audio { border-left: 2px solid transparent; }
.material-item.type-audio:hover { border-left-color: var(--accent-amber); }

/* ========== 空状态 ========== */
.empty-hint { font-size: 12px; color: var(--text-muted); text-align: center; padding: 20px 0; }

/* ========== 底部状态 ========== */
.panel-footer {
  padding: 8px 16px;
  margin-top: auto;
  border-top: 1px solid var(--border-subtle);
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.footer-left { display: flex; align-items: center; gap: 6px; }
.footer-right { display: flex; align-items: center; gap: 6px; }
.footer-user { font-size: 10px; color: var(--brand, #7C3AED); max-width: 80px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.footer-user:hover { color: var(--brand-hover, #6D28D9); }
.footer-login-btn { font-size: 10px; padding: 2px 8px; border-radius: 4px; border: 1px solid var(--border, #27272A); background: transparent; color: var(--text-muted, #A1A1AA); cursor: pointer; }
.footer-login-btn:hover { background: var(--bg-hover, #1A1A1F); color: var(--text, #E4E4E7); }
.footer-settings-btn {
  width: 24px; height: 24px;
  display: flex; align-items: center; justify-content: center;
  background: transparent; border: none; border-radius: 4px;
  color: var(--text-muted); cursor: pointer; transition: all 0.12s;
}
.footer-settings-btn:hover { background: var(--bg-hover); color: var(--text-secondary); }
.footer-conn { font-size: 10px; color: var(--text-muted); }
.footer-conn.on { color: var(--accent-green); }
.footer-status { font-size: 10px; padding: 2px 8px; border-radius: 6px; font-weight: 500; }
.footer-status.idle { color: var(--text-secondary); }
.footer-status.running { color: var(--accent-green); }
.footer-status.error { color: var(--accent-red); }

/* ========== Loading Spinner ========== */
.loading-spin {
  display: flex; align-items: center; gap: 6px;
  padding: 10px 0; justify-content: center;
  font-size: 11px; color: var(--text-muted);
}
.spinner {
  width: 12px; height: 12px;
  border: 2px solid var(--border-subtle);
  border-top-color: var(--brand);
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* ========== 项目操作按钮 ========== */
.project-actions { display: flex; align-items: center; gap: 1px; flex-shrink: 0; }
.project-menu-btn {
  background: transparent; border: none;
  color: var(--text-secondary); font-size: 13px; font-weight: 700;
  cursor: pointer; padding: 0 2px; border-radius: 3px;
  transition: all 0.1s; line-height: 1; opacity: 0.35;
}
.project-item:hover .project-menu-btn { opacity: 1; }
.project-menu-btn:hover { color: var(--text-primary); background: var(--bg-hover); }

/* ========== 右键菜单 ========== */
.ctx-overlay { position: fixed; inset: 0; z-index: 999; }
.ctx-menu {
  position: fixed; z-index: 1000;
  background: var(--surface-elevated);
  border: 1px solid var(--border-card);
  border-radius: var(--radius);
  padding: 4px;
  min-width: 150px;
  box-shadow: var(--shadow-lg);
  animation: ctxIn 0.1s ease-out;
}
@keyframes ctxIn { from { opacity: 0; transform: scale(0.96) translateY(-3px); } to { opacity: 1; transform: none; } }
.ctx-item {
  display: flex; align-items: center; gap: 6px;
  width: 100%; padding: 7px 10px;
  background: transparent; border: none; border-radius: 4px;
  color: var(--text-secondary); font-size: 11px; font-family: inherit;
  cursor: pointer; text-align: left; transition: all 0.08s;
}
.ctx-item:hover { background: var(--bg-hover); color: var(--text-primary); }
.ctx-icon { font-size: 12px; width: 16px; text-align: center; flex-shrink: 0; }
.ctx-danger { color: var(--accent-red); }
.ctx-danger:hover { background: rgba(239, 68, 68, 0.08); color: #FCA5A5; }
.ctx-sep { height: 1px; background: var(--border-subtle); margin: 2px 6px; }
</style>
