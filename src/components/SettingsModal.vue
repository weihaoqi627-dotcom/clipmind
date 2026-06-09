<template>
  <Teleport to="body">
    <Transition name="modal-fade">
      <div v-if="visible" class="settings-overlay" @click.self="$emit('close')">
        <div class="settings-modal">
          <!-- 头部 -->
          <div class="settings-header">
            <h2 class="settings-title">设置</h2>
            <button class="settings-close" @click="$emit('close')">×</button>
          </div>

          <!-- 标签页 -->
          <div class="settings-tabs">
            <button
              v-for="tab in tabs"
              :key="tab.key"
              :class="{ active: activeTab === tab.key }"
              @click="activeTab = tab.key"
            >{{ tab.label }}</button>
          </div>

          <!-- 内容区 -->
          <div class="settings-body">
            <!-- 通用 -->
            <div v-if="activeTab === 'general'" class="tab-content">
              <div class="setting-row">
                <div class="setting-label">
                  <div class="setting-name">启动时打开上次项目</div>
                  <div class="setting-desc">启动剪意时自动恢复上次关闭时的活跃项目</div>
                </div>
                <label class="toggle">
                  <input type="checkbox" v-model="form.general.open_last_project">
                  <span class="toggle-slider"></span>
                </label>
              </div>

              <div class="setting-row">
                <div class="setting-label">
                  <div class="setting-name">自动保存间隔</div>
                  <div class="setting-desc">项目数据自动保存的时间间隔（秒）</div>
                </div>
                <select v-model.number="form.general.auto_save_interval" class="field-select">
                  <option :value="60">60 秒</option>
                  <option :value="180">3 分钟</option>
                  <option :value="300">5 分钟</option>
                  <option :value="600">10 分钟</option>
                </select>
              </div>

              <div class="setting-row">
                <div class="setting-label">
                  <div class="setting-name">允许自动更新</div>
                  <div class="setting-desc">启动时后台自动下载新版本，重启后生效。关闭后需手动检查更新</div>
                </div>
                <label class="toggle">
                  <input type="checkbox" v-model="form.general.auto_update">
                  <span class="toggle-slider"></span>
                </label>
              </div>
            </div>

            <!-- 外观 -->
            <div v-if="activeTab === 'appearance'" class="tab-content">
              <div class="setting-row">
                <div class="setting-label">
                  <div class="setting-name">主题</div>
                  <div class="setting-desc">选择界面颜色主题</div>
                </div>
                <div class="theme-options">
                  <label
                    v-for="opt in themeOptions"
                    :key="opt.value"
                    class="theme-opt"
                    :class="{ active: form.appearance.theme === opt.value }"
                  >
                    <input
                      type="radio"
                      :value="opt.value"
                      v-model="form.appearance.theme"
                      hidden
                    />
                    <span class="theme-preview" :class="opt.value">
                      <span class="theme-dot-bar">
                        <span class="theme-dot"></span>
                        <span class="theme-dot"></span>
                        <span class="theme-dot"></span>
                      </span>
                      <span class="theme-rect"></span>
                    </span>
                    <span class="theme-label">{{ opt.label }}</span>
                  </label>
                </div>
              </div>
            </div>

            <!-- 导出 -->
            <div v-if="activeTab === 'export'" class="tab-content">
              <div class="setting-row">
                <div class="setting-label">
                  <div class="setting-name">输出目录</div>
                  <div class="setting-desc">导出视频默认保存位置（留空使用项目目录）</div>
                </div>
                <div class="field-row">
                  <input
                    type="text"
                    v-model="form.export.output_dir"
                    class="field-input"
                    placeholder="默认输出目录..."
                    readonly
                  />
                  <button class="field-btn" @click="pickOutputDir">选择</button>
                </div>
              </div>
            </div>

            <!-- 关于 -->
            <div v-if="activeTab === 'about'" class="tab-content">
              <div class="about-section">
                <div class="about-logo">
                  <SvgIcon name="logo" size="40" color="#7C3AED" />
                </div>
                <div class="about-name">ClipMind / 剪意</div>
                <div class="about-version" v-if="about.version">v{{ about.version }}</div>
                <div class="about-desc">AI 智能视频剪辑工具</div>

                <div class="about-divider"></div>

                <div class="about-info">
                  <div class="info-row">
                    <span class="info-label">Electron</span>
                    <span class="info-value">{{ about.electron || '-' }}</span>
                  </div>
                  <div class="info-row">
                    <span class="info-label">Chrome</span>
                    <span class="info-value">{{ about.chrome || '-' }}</span>
                  </div>
                  <div class="info-row">
                    <span class="info-label">Node.js</span>
                    <span class="info-value">{{ about.node || '-' }}</span>
                  </div>
                  <div class="info-row">
                    <span class="info-label">平台</span>
                    <span class="info-value">{{ about.platform || '-' }}</span>
                  </div>
                </div>

                <div class="about-divider"></div>

                <div class="about-actions">
                  <button class="about-btn" @click="checkUpdate" :disabled="checkingUpdate">
                    {{ checkingUpdate ? '检查中...' : '检查更新' }}
                  </button>
                </div>
                <div v-if="updateResult" class="about-update-result" :class="updateResult.type">
                  {{ updateResult.text }}
                </div>
                <div v-if="updateResult?.type === 'available'" class="about-update-install">
                  <button class="about-btn primary" @click="installUpdate">立即更新</button>
                </div>

                <div class="about-footer">
                  <a href="https://clipmind.cn" target="_blank" class="about-link">clipmind.cn</a>
                  <span class="about-dot">·</span>
                  <span class="about-copy">© 2026 ClipMind</span>
                </div>
                <div class="about-contact">
                  反馈联系：QQ 2217142796 ｜ 微信 w1786854468
                </div>
              </div>
            </div>
          </div>

          <!-- 底部按钮 -->
          <div class="settings-footer">
            <button class="footer-btn cancel" @click="$emit('close')">取消</button>
            <button class="footer-btn save" @click="onSave">保存</button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, reactive, watch, onMounted } from 'vue'
import SvgIcon from './SvgIcon.vue'

const props = defineProps<{
  visible: boolean
  settings: Record<string, any>
}>()

const emit = defineEmits<{
  'close': []
  'save': [settings: Record<string, any>]
}>()

const tabs = [
  { key: 'general', label: '通用' },
  { key: 'appearance', label: '外观' },
  { key: 'export', label: '导出' },
  { key: 'about', label: '关于' },
]

const themeOptions = [
  { value: 'dark', label: '暗色' },
  { value: 'light', label: '纯白' },
  { value: 'system', label: '跟随系统' },
]

const activeTab = ref('general')

const form = reactive({
  general: {
    open_last_project: true,
    auto_save_interval: 300,
    auto_update: true,
  },
  appearance: {
    theme: 'dark',
  },
  export: {
    output_dir: '',
  },
})

// 打开时同步外部 settings 进来
watch(() => props.visible, (v) => {
  if (v) {
    const s = props.settings || {}
    form.general.open_last_project = s.general?.open_last_project ?? true
    form.general.auto_save_interval = s.general?.auto_save_interval ?? 300
    form.general.auto_update = s.general?.auto_update ?? true
    form.appearance.theme = s.appearance?.theme ?? 'dark'
    form.export.output_dir = s.export?.output_dir ?? ''
    activeTab.value = 'general'
    _applyThemePreview(form.appearance.theme)
  }
})

// 主题切换即时预览(不点保存也生效)
watch(() => form.appearance.theme, (val) => {
  _applyThemePreview(val)
})

function _applyThemePreview(theme: string) {
  if (theme === 'system') {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    document.documentElement.setAttribute('data-theme', prefersDark ? 'dark' : 'light')
  } else {
    document.documentElement.setAttribute('data-theme', theme)
  }
}

function onSave() {
  window.cherryclip?.updater.setAutoUpdateSetting(form.general.auto_update)
  emit('save', JSON.parse(JSON.stringify(form)))
}

async function pickOutputDir() {
  try {
    const paths = await window.cherryclip?.dialogs.openFiles?.({
      properties: ['openDirectory'],
    })
    if (paths && paths.length > 0) {
      form.export.output_dir = paths[0]
    }
  } catch (e) {
    console.warn('[Settings] 选择输出目录失败:', e)
  }
}

// ── 关于 ──

interface AppVersion {
  version: string
  name: string
  electron: string
  chrome: string
  node: string
  platform: string
  arch: string
}

const about = ref<AppVersion>({
  version: '',
  name: '',
  electron: '',
  chrome: '',
  node: '',
  platform: '',
  arch: '',
})

const checkingUpdate = ref(false)
const updateResult = ref<{ type: string; text: string } | null>(null)

// 打开设置时加载版本信息
watch(() => props.visible, async (v) => {
  if (v && activeTab.value === 'about') {
    loadVersion()
  }
})

// 切到关于标签时也加载
watch(activeTab, (tab) => {
  if (tab === 'about' && props.visible) {
    loadVersion()
  }
})

async function loadVersion() {
  try {
    const info = await window.cherryclip?.getAppVersion()
    if (info) about.value = info
  } catch {
    // 开发模式下可能拿不到
  }
}

async function checkUpdate() {
  checkingUpdate.value = true
  updateResult.value = null
  try {
    const info = await window.cherryclip?.updater.checkForUpdates()
    if (!info) {
      updateResult.value = { type: 'error', text: '无法检查更新（开发环境）' }
    } else if (info.version && info.version !== about.value.version) {
      updateResult.value = {
        type: 'available',
        text: `发现新版本 v${info.version}（${new Date(info.releaseDate).toLocaleDateString('zh-CN')}），正在后台下载...`,
      }
    } else {
      updateResult.value = { type: 'latest', text: '已是最新版本' }
    }
  } catch (e: any) {
    updateResult.value = { type: 'error', text: '检查更新失败：' + (e.message || '未知错误') }
  } finally {
    checkingUpdate.value = false
  }
}

function installUpdate() {
  window.cherryclip?.updater.installUpdate()
}

function sendFeedback() {
  const subject = encodeURIComponent(`ClipMind v${about.value.version} 反馈`)
  const body = encodeURIComponent(`\n\n---\n版本: v${about.value.version}\n平台: ${about.value.platform} ${about.value.arch}`)
  window.open(`mailto:feedback@clipmind.cn?subject=${subject}&body=${body}`, '_blank')
}

// ── 兼容 SvgIcon ──
defineOptions({ inheritAttrs: false })
</script>

<style scoped>
/* ========== 遮罩 ========== */
.settings-overlay {
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

/* ========== 弹窗 ========== */
.settings-modal {
  width: 500px;
  max-height: 70vh;
  background: var(--surface-glass);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border: 1px solid var(--surface-glass-edge);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* ========== 头部 ========== */
.settings-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px 12px;
  border-bottom: 1px solid var(--border-subtle);
}

.settings-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0;
}

.settings-close {
  width: 26px;
  height: 26px;
  background: transparent;
  border: none;
  border-radius: 5px;
  color: var(--text-muted);
  font-size: 16px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.12s;
}
.settings-close:hover { background: var(--bg-hover); color: var(--text-primary); }

/* ========== 标签页 ========== */
.settings-tabs {
  display: flex;
  gap: 1px;
  padding: 0 20px;
  border-bottom: 1px solid var(--border-subtle);
}

.settings-tabs button {
  padding: 9px 14px;
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.12s;
}
.settings-tabs button:hover { color: var(--text-secondary); }
.settings-tabs button.active {
  color: var(--text-accent);
  border-bottom-color: var(--brand);
}

/* ========== 内容区 ========== */
.settings-body {
  flex: 1;
  overflow-y: auto;
  padding: 18px 20px;
}
.tab-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ========== 设置行 ========== */
.setting-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
}
.setting-label { flex: 1; min-width: 0; }
.setting-name {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 1px;
}
.setting-desc { font-size: 10px; color: var(--text-muted); line-height: 1.4; }

/* ========== Toggle 开关 ========== */
.toggle {
  position: relative;
  width: 36px;
  height: 20px;
  flex-shrink: 0;
  cursor: pointer;
}
.toggle input { display: none; }
.toggle-slider {
  position: absolute;
  inset: 0;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 10px;
  transition: all 0.2s;
}
.toggle-slider::after {
  content: '';
  position: absolute;
  top: 2px;
  left: 2px;
  width: 16px;
  height: 16px;
  background: var(--text-on-brand);
  border-radius: 50%;
  transition: all 0.2s;
}
.toggle input:checked + .toggle-slider { background: var(--brand); }
.toggle input:checked + .toggle-slider::after { transform: translateX(16px); }

/* ========== 主题选择 ========== */
.theme-options { display: flex; gap: 8px; }
.theme-opt {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  padding: 6px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  transition: all 0.12s;
}
.theme-opt.active { border-color: var(--brand); background: var(--bg-active); }
.theme-preview {
  width: 56px;
  height: 36px;
  border-radius: 5px;
  border: 1px solid var(--border-subtle);
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 5px;
  overflow: hidden;
}
.theme-preview.dark { background: #131316; }
.theme-preview.light { background: #FFFFFF; }
.theme-preview.system { background: linear-gradient(135deg, #131316 50%, #FFFFFF 50%); }
.theme-dot-bar { display: flex; gap: 2px; }
.theme-dot { width: 3px; height: 3px; border-radius: 50%; }
.theme-preview.dark .theme-dot { background: var(--brand); }
.theme-preview.light .theme-dot { background: var(--brand); }
.theme-preview.system .theme-dot { background: #888; }
.theme-rect { height: 10px; border-radius: 2px; }
.theme-preview.dark .theme-rect { background: rgba(255, 255, 255, 0.08); }
.theme-preview.light .theme-rect { background: rgba(0, 0, 0, 0.06); }
.theme-preview.system .theme-rect { background: rgba(255, 255, 255, 0.08); }
.theme-label { font-size: 10px; color: var(--text-muted); font-weight: 500; }

/* ========== 表单控件 ========== */
.field-select {
  padding: 6px 10px;
  background: var(--surface-overlay);
  border: 1px solid var(--border-card);
  border-radius: 5px;
  color: var(--text-primary);
  font-size: 11px;
  font-family: inherit;
  cursor: pointer;
  appearance: none;
  -webkit-appearance: none;
  padding-right: 22px;
  background-image: url("data:image/svg+xml,%3Csvg width='8' height='5' viewBox='0 0 8 5' fill='none' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M1 1l3 3 3-3' stroke='%23888' stroke-width='1.2' stroke-linecap='round'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 7px center;
}
.field-row { display: flex; gap: 6px; align-items: center; min-width: 0; }
.field-input {
  flex: 1;
  min-width: 0;
  padding: 6px 10px;
  background: var(--surface-overlay);
  border: 1px solid var(--border-card);
  border-radius: 5px;
  color: var(--text-secondary);
  font-size: 11px;
  font-family: inherit;
  outline: none;
}
.field-input:focus { border-color: var(--brand); }
.field-btn {
  padding: 6px 12px;
  background: var(--surface-overlay);
  border: 1px solid var(--border-card);
  border-radius: 5px;
  color: var(--text-secondary);
  font-size: 11px;
  font-weight: 500;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.12s;
  white-space: nowrap;
}
.field-btn:hover { border-color: var(--brand); color: var(--text-accent); }

/* ========== 底部 ========== */
.settings-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 20px;
  border-top: 1px solid var(--border-subtle);
}
.footer-btn {
  padding: 7px 18px;
  border: none;
  border-radius: var(--radius-sm);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.12s;
}
.footer-btn.cancel { background: var(--surface-overlay); color: var(--text-secondary); border: 1px solid var(--border-card); }
.footer-btn.cancel:hover { background: var(--bg-hover); }
.footer-btn.save { background: var(--brand); color: var(--text-on-brand); }
.footer-btn.save:hover { background: var(--brand-light); }

/* ========== 关于 ========== */
.about-section {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 12px 0;
}
.about-logo { margin-bottom: 8px; }
.about-name {
  font-size: 16px;
  font-weight: 700;
  color: var(--text-primary);
}
.about-version {
  font-size: 12px;
  color: var(--brand);
  font-weight: 600;
  margin-top: 2px;
}
.about-desc {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 4px;
}
.about-divider {
  width: 100%;
  height: 1px;
  background: var(--border-subtle);
  margin: 16px 0;
}
.about-info {
  width: 100%;
}
.info-row {
  display: flex;
  justify-content: space-between;
  padding: 4px 0;
  font-size: 11px;
}
.info-label { color: var(--text-muted); }
.info-value { color: var(--text-secondary); }
.about-actions {
  display: flex;
  gap: 8px;
  width: 100%;
}
.about-btn {
  flex: 1;
  padding: 8px 12px;
  border: 1px solid var(--border-card);
  border-radius: 6px;
  background: var(--surface-overlay);
  color: var(--text-secondary);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.12s;
  text-align: center;
}
.about-btn:hover { border-color: var(--brand); color: var(--text-accent); }
.about-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.about-btn.primary {
  background: var(--brand);
  color: var(--text-on-brand);
  border-color: var(--brand);
}
.about-btn.primary:hover { background: var(--brand-light); }
.about-update-result {
  margin-top: 8px;
  font-size: 11px;
  padding: 6px 10px;
  border-radius: 4px;
  width: 100%;
  text-align: center;
}
.about-update-result.latest { color: var(--accent-green); background: rgba(34, 197, 94, 0.08); }
.about-update-result.available { color: var(--brand); background: rgba(124, 58, 237, 0.08); }
.about-update-result.error { color: var(--accent-red); background: rgba(239, 68, 68, 0.08); }
.about-update-install {
  margin-top: 6px;
  width: 100%;
}
.about-footer {
  margin-top: 16px;
  font-size: 11px;
  color: var(--text-muted);
  display: flex;
  align-items: center;
  gap: 6px;
}
.about-link {
  color: var(--brand);
  text-decoration: none;
}
.about-link:hover { text-decoration: underline; }
.about-contact {
  margin-top: 8px;
  font-size: 11px;
  color: var(--text-muted);
  padding: 6px 10px;
  background: var(--surface-overlay);
  border-radius: 6px;
  text-align: center;
}
.about-dot { color: var(--border-subtle); }
.about-copy { color: var(--text-muted); }

/* ========== 动画 ========== */
.modal-fade-enter-active { transition: all 0.2s ease-out; }
.modal-fade-leave-active { transition: all 0.15s ease-in; }
.modal-fade-enter-from { opacity: 0; }
.modal-fade-enter-from .settings-modal { transform: scale(0.96) translateY(-6px); }
.modal-fade-leave-to { opacity: 0; }
.modal-fade-leave-to .settings-modal { transform: scale(0.96) translateY(-6px); }
</style>
