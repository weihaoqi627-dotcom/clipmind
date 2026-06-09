<template>
  <Teleport to="body">
    <Transition name="modal-fade">
      <div v-if="visible" class="export-overlay" @click.self="$emit('close')">
        <div class="export-modal">

          <!-- ─── 头部 ─── -->
          <div class="export-header">
            <h2 class="export-title">
              <SvgIcon name="export" :size="16" />
              导出管理
            </h2>
            <div class="header-right">
              <span class="concurrency-badge" :class="{ full: runningCount >= limit }">
                {{ runningCount }}/{{ limit }}
              </span>
              <button class="export-close" @click="$emit('close')">×</button>
            </div>
          </div>

          <div class="export-body">

            <!-- ─── 新建导出 ─── -->
            <div class="new-export-block">
              <button class="new-export-toggle" @click="showNewForm = !showNewForm">
                <span class="toggle-arrow">{{ showNewForm ? '▾' : '▸' }}</span>
                新建导出
                <span v-if="runningCount >= limit" class="limit-hint">已达并发上限</span>
              </button>

              <div v-if="showNewForm" class="new-export-form">
                <div class="form-row">
                  <label class="form-label">项目</label>
                  <select v-model="newExportProjectId" class="field-select">
                    <option value="" disabled>-- 选择项目 --</option>
                    <option v-for="p in projects" :key="p.id" :value="p.id"
                      :disabled="!p.draftId">
                      {{ p.name }} <template v-if="!p.draftId">(无草稿)</template>
                    </option>
                  </select>
                </div>

                <label class="setting-label">目标平台 / 预设</label>
                <div class="preset-grid">
                  <button v-for="p in presetList" :key="p.key"
                    class="preset-card" :class="{ active: selectedPreset === p.key }"
                    @click="selectedPreset = p.key">
                    <span class="preset-name">{{ p.label }}</span>
                    <span class="preset-meta">{{ p.resolution }} · {{ p.bitrate }}</span>
                  </button>
                </div>

                <div class="new-export-actions">
                  <span v-if="startError" class="error-hint">{{ startError }}</span>
                  <button class="footer-btn primary" @click="startNewExport"
                    :disabled="!canStartNew">
                    <SvgIcon name="export" :size="14" /> 开始导出
                  </button>
                </div>
              </div>
            </div>

            <!-- ─── 任务列表 ─── -->
            <div v-if="tasks.length === 0" class="empty-state">
              <p>暂无导出任务</p>
              <p class="empty-hint">点击上方「新建导出」添加导出任务</p>
            </div>

            <div v-else class="task-list">
              <!-- 进行中 -->
              <template v-if="runningTasks.length > 0">
                <div class="task-group-label">进行中 ({{ runningTasks.length }})</div>
                <div v-for="t in runningTasks" :key="t.taskId" class="task-card running">
                  <div class="task-main">
                    <span class="task-spinner"></span>
                    <div class="task-info">
                      <span class="task-project">{{ t.projectName }}</span>
                      <span class="task-meta">{{ t.presetLabel }}</span>
                    </div>
                    <button class="task-btn-cancel" @click="cancelTask(t)" title="取消导出">×</button>
                  </div>
                </div>
              </template>

              <!-- 已完成 -->
              <template v-if="doneTasks.length > 0">
                <div class="task-group-label">已完成 ({{ doneTasks.length }})</div>
                <div v-for="t in doneTasks" :key="t.taskId" class="task-card done">
                  <div class="task-main">
                    <span class="task-icon done-icon">✓</span>
                    <div class="task-info">
                      <span class="task-project">{{ t.projectName }}</span>
                      <span class="task-meta">{{ t.presetLabel }}</span>
                    </div>
                  </div>
                  <div v-if="t.outputPath" class="task-path" :title="t.outputPath">
                    📁 {{ t.outputPath }}
                  </div>
                </div>
              </template>

              <!-- 失败 -->
              <template v-if="failedTasks.length > 0">
                <div class="task-group-label">失败 ({{ failedTasks.length }})</div>
                <div v-for="t in failedTasks" :key="t.taskId" class="task-card error">
                  <div class="task-main">
                    <span class="task-icon error-icon">✗</span>
                    <div class="task-info">
                      <span class="task-project">{{ t.projectName }}</span>
                      <span class="task-meta">{{ t.presetLabel }}</span>
                    </div>
                    <button class="task-btn-retry" @click="retryTask(t)">重试</button>
                  </div>
                  <div v-if="t.errorMsg" class="task-errormsg">{{ t.errorMsg }}</div>
                </div>
              </template>
            </div>

          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, computed, watch, inject } from 'vue'
import SvgIcon from './SvgIcon.vue'

// ── Types ──
interface PresetItem {
  key: string; label: string; resolution: string; bitrate: string
}

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

const presetList: PresetItem[] = [
  { key: 'douyin',        label: '抖音',          resolution: '1080×1920', bitrate: '8M' },
  { key: 'kuaishou',      label: '快手',          resolution: '1080×1920', bitrate: '6M' },
  { key: 'xiaohongshu',   label: '小红书',        resolution: '1080×1920', bitrate: '8M' },
  { key: 'instagram_reel',label: 'Instagram Reel', resolution: '1080×1920', bitrate: '6M' },
  { key: 'wechat_moment', label: '朋友圈视频',    resolution: '1080×1920', bitrate: '4M' },
  { key: 'bilibili_1080p',label: 'B站 1080p',     resolution: '1920×1080', bitrate: '10M' },
  { key: 'youtube_1080p', label: 'YouTube 1080p', resolution: '1920×1080', bitrate: '12M' },
  { key: 'bilibili_4k',   label: 'B站 4K',        resolution: '3840×2160', bitrate: '35M' },
  { key: 'youtube_4k',    label: 'YouTube 4K',    resolution: '3840×2160', bitrate: '45M' },
]

// ── Props / Emits ──
const props = defineProps<{
  visible: boolean
  draftId: string
  projectId?: string
}>()

defineEmits<{ close: [] }>()

// ── State ──
const showNewForm = ref(false)
const newExportProjectId = ref('')
const selectedPreset = ref('douyin')
const startError = ref('')

// ── Inject ──
const exportManager: any = inject('exportManager', null)
const app: any = inject('app', null)

const tasks = computed<ExportTask[]>(() => exportManager?.tasks ?? [])
const limit = computed(() => exportManager?.limit ?? 5)
const runningCount = computed(() => exportManager?.runningCount ?? 0)

const projects = computed(() => app?.projects ?? [])

const runningTasks = computed(() => tasks.value.filter(t => t.status === 'running'))
const doneTasks = computed(() => tasks.value.filter(t => t.status === 'done'))
const failedTasks = computed(() => tasks.value.filter(t => t.status === 'error'))

const canStartNew = computed(() => {
  const p = newExportProjectId.value
  if (!p) return false
  const proj = projects.value.find((x: any) => x.id === p)
  return !!proj?.draftId && runningCount.value < limit.value
})

// ── 打开时：如果有 projectId 预设，自动展开新建表单 ──
watch(() => props.visible, (v) => {
  if (v) {
    showNewForm.value = !!props.projectId
    newExportProjectId.value = props.projectId || ''
    startError.value = ''
  }
})

// ── 新建导出 ──
function startNewExport() {
  const p = projects.value.find((x: any) => x.id === newExportProjectId.value)
  if (!p || !p.draftId) { startError.value = '请选择有草稿的项目'; return }
  if (runningCount.value >= limit.value) { startError.value = `已达并发上限 ${limit.value}，请等待完成任务`; return }

  startError.value = ''
  const preset = presetList.find(x => x.key === selectedPreset.value)
  const ok = exportManager?.start?.(p.draftId, p.id, p.name, selectedPreset.value, preset?.label || selectedPreset.value)
  if (ok === false) {
    startError.value = '启动导出失败，请重试'
  } else {
    showNewForm.value = false
  }
}

// ── 取消 / 重试 ──
function cancelTask(t: ExportTask) {
  exportManager?.cancel?.(t.taskId, t.projectId)
}

function retryTask(t: ExportTask) {
  const preset = t.preset
  const presetLabel = presetList.find(x => x.key === preset)?.label || preset
  exportManager?.start?.(t.draftId, t.projectId, t.projectName, preset, presetLabel)
}
</script>

<style scoped>
/* ─── 遮罩 ─── */
.export-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: var(--overlay-bg);
  display: flex;
  align-items: center;
  justify-content: center;
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
}

/* ─── 弹窗 ─── */
.export-modal {
  width: 520px;
  max-height: 75vh;
  background: var(--surface-glass);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border: 1px solid var(--surface-glass-edge);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

/* ─── 头部 ─── */
.export-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px 12px;
  border-bottom: 1px solid var(--border-subtle);
}

.export-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0;
}

.header-right { display: flex; align-items: center; gap: 8px; }

.concurrency-badge {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
  background: var(--surface-overlay);
  border: 1px solid var(--border-card);
  border-radius: var(--radius-full);
  padding: 2px 10px;
}
.concurrency-badge.full {
  color: var(--accent-amber);
  border-color: rgba(245, 158, 11, 0.3);
}

.export-close {
  width: 26px; height: 26px;
  background: transparent; border: none; border-radius: 5px;
  color: var(--text-muted); font-size: 16px; cursor: pointer;
  display: flex; align-items: center; justify-content: center; transition: all 0.12s;
}
.export-close:hover { background: var(--bg-hover); color: var(--text-primary); }

/* ─── 主体 ─── */
.export-body {
  flex: 1;
  overflow-y: auto;
  padding: 14px 20px 20px;
}

/* ─── 新建导出 ─── */
.new-export-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 8px 12px;
  background: var(--surface-overlay);
  border: 1px dashed var(--border-card);
  border-radius: var(--radius);
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.12s;
  margin-bottom: 10px;
}
.new-export-toggle:hover {
  border-color: var(--brand);
  color: var(--text-accent);
  background: var(--bg-active);
}

.toggle-arrow { font-size: 10px; color: var(--text-muted); }
.limit-hint { margin-left: auto; font-size: 10px; color: var(--accent-amber); font-weight: 500; }

.new-export-form {
  background: var(--surface-overlay);
  border: 1px solid var(--border-card);
  border-radius: var(--radius);
  padding: 16px;
  margin-bottom: 14px;
}

.form-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
}
.form-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
  white-space: nowrap;
}

.field-select {
  flex: 1;
  padding: 6px 10px;
  background: var(--surface-raised);
  border: 1px solid var(--border-card);
  border-radius: 5px;
  color: var(--text-primary);
  font-size: 11px;
  font-family: inherit;
  cursor: pointer;
  outline: none;
  appearance: none;
  -webkit-appearance: none;
  padding-right: 22px;
  background-image: url("data:image/svg+xml,%3Csvg width='8' height='5' viewBox='0 0 8 5' fill='none' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M1 1l3 3 3-3' stroke='%23888' stroke-width='1.2' stroke-linecap='round'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 7px center;
}

.setting-label {
  display: block;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.4px;
  margin-bottom: 10px;
}

.preset-grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 6px;
  margin-bottom: 14px;
}

.preset-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 8px 4px;
  background: var(--surface-raised);
  border: 1px solid var(--border-card);
  border-radius: var(--radius);
  cursor: pointer;
  transition: all 0.12s;
  font-family: inherit;
  text-align: center;
}
.preset-card:hover { border-color: var(--text-muted); background: var(--bg-hover); }
.preset-card.active {
  border-color: var(--brand);
  background: var(--bg-active);
  box-shadow: 0 0 0 1px var(--brand);
}
.preset-name { font-size: 11px; font-weight: 600; color: var(--text-primary); }
.preset-meta { font-size: 9px; color: var(--text-muted); }

.new-export-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}
.error-hint { font-size: 11px; color: var(--accent-red); }

/* ─── 底部按钮 ─── */
.footer-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 16px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.12s;
  border: none;
}
.footer-btn.primary {
  background: var(--brand);
  color: var(--text-on-brand);
}
.footer-btn.primary:hover:not(:disabled) { background: var(--brand-light); }
.footer-btn.primary:disabled { opacity: 0.4; cursor: not-allowed; }

/* ─── 空状态 ─── */
.empty-state {
  text-align: center;
  padding: 40px 0;
  color: var(--text-muted);
  font-size: 12px;
}
.empty-hint { font-size: 11px; color: var(--text-muted); margin-top: 4px; }

/* ─── 任务列表 ─── */
.task-list { display: flex; flex-direction: column; gap: 6px; }

.task-group-label {
  font-size: 10px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.3px;
  padding: 8px 0 2px;
}
.task-group-label:first-child { padding-top: 0; }

.task-card {
  background: var(--surface-overlay);
  border: 1px solid var(--border-card);
  border-radius: var(--radius);
  padding: 10px 12px;
}
.task-card.running { border-color: rgba(99, 102, 241, 0.2); }
.task-card.done { border-color: rgba(34, 197, 94, 0.15); }
.task-card.error { border-color: rgba(239, 68, 68, 0.2); }

.task-main {
  display: flex;
  align-items: center;
  gap: 10px;
}

.task-spinner {
  width: 16px; height: 16px;
  border: 2px solid var(--border-subtle);
  border-top-color: var(--brand);
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
  flex-shrink: 0;
}
@keyframes spin { to { transform: rotate(360deg); } }

.task-icon {
  width: 16px; height: 16px;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
  font-size: 13px;
  font-weight: 700;
}
.done-icon { color: var(--accent-green); }
.error-icon { color: var(--accent-red); }

.task-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.task-project { font-size: 12px; font-weight: 600; color: var(--text-primary); }
.task-meta { font-size: 10px; color: var(--text-muted); }

.task-path {
  font-size: 10px;
  color: var(--text-accent);
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px solid var(--border-subtle);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: 'SF Mono', 'Cascadia Code', monospace;
}

.task-errormsg {
  font-size: 10px;
  color: var(--accent-red);
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px solid var(--border-subtle);
  line-height: 1.4;
}

.task-btn-cancel, .task-btn-retry {
  padding: 4px 10px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.12s;
  border: none;
  flex-shrink: 0;
}
.task-btn-cancel {
  background: transparent;
  color: var(--text-muted);
  border: 1px solid var(--border-card);
}
.task-btn-cancel:hover { background: var(--bg-hover); color: var(--text-primary); }

.task-btn-retry {
  background: var(--brand);
  color: var(--text-on-brand);
}
.task-btn-retry:hover { background: var(--brand-light); }

/* ─── 动画 ─── */
.modal-fade-enter-active { transition: all 0.2s ease-out; }
.modal-fade-leave-active { transition: all 0.15s ease-in; }
.modal-fade-enter-from { opacity: 0; }
.modal-fade-enter-from .export-modal { transform: scale(0.96) translateY(-6px); }
.modal-fade-leave-to { opacity: 0; }
.modal-fade-leave-to .export-modal { transform: scale(0.96) translateY(-6px); }
</style>
