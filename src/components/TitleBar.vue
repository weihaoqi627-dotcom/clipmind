<template>
  <div class="title-bar">
    <!-- 可拖拽区域 + Logo -->
    <div class="title-bar-drag">
      <div class="title-bar-brand">
        <div class="title-logo">
          <SvgIcon name="logo" size="18" color="#FFF" />
        </div>
        <span class="title-text">{{ title }}</span>
      </div>
    </div>

    <!-- 更新状态：仅下载完成后显示 -->
    <button
      v-if="updateReady"
      class="update-ready-btn"
      :title="`新版本 ${updateVersion} 已就绪，点击安装并重启`"
      @click="installUpdate"
    >
      <span class="update-ready-dot"></span>
      <span class="update-ready-text">v{{ updateVersion }}</span>
    </button>

    <!-- 关于按钮 -->
    <div class="about-area" ref="aboutRef">
      <button class="about-btn" @click="showAbout = !showAbout" title="关于剪意">
        <SvgIcon name="info" size="14" />
      </button>
      <Transition name="about-drop">
        <div v-if="showAbout" class="about-popup">
          <div class="about-logo">
            <SvgIcon name="logo" size="28" color="#FFF" />
          </div>
          <div class="about-name">剪意 ClipMind</div>
          <div class="about-version">v1.0.0</div>
          <div class="about-desc">AI 驱动的通用视频智能剪辑管线</div>
          <div class="about-tech">Vue 3 · Electron · Python · FFmpeg</div>
          <div class="about-copy">&copy; {{ currentYear }}</div>
        </div>
      </Transition>
    </div>

    <!-- 窗口控制按钮 -->
    <div class="title-bar-controls">
      <button class="ctrl-btn" @click="minimize" title="最小化">
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
          <rect x="2" y="5.5" width="8" height="1" rx="0.5" fill="currentColor"/>
        </svg>
      </button>
      <button class="ctrl-btn" @click="maximize" :title="isMaximized ? '还原' : '最大化'">
        <!-- 最大化图标 -->
        <svg v-if="!isMaximized" width="12" height="12" viewBox="0 0 12 12" fill="none">
          <rect x="2" y="2" width="8" height="8" rx="1" stroke="currentColor" stroke-width="1"/>
        </svg>
        <!-- 还原图标 -->
        <svg v-else width="12" height="12" viewBox="0 0 12 12" fill="none">
          <rect x="4" y="1" width="7" height="7" rx="1" stroke="currentColor" stroke-width="1"/>
          <rect x="1" y="4" width="7" height="7" rx="1" fill="var(--bg-panel)" stroke="currentColor" stroke-width="1"/>
        </svg>
      </button>
      <button class="ctrl-btn ctrl-close" @click="closeWin" title="关闭">
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
          <path d="M3 3l6 6M9 3l-6 6" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
        </svg>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import SvgIcon from './SvgIcon.vue'

defineProps<{ title: string }>()

const showAbout = ref(false)
const aboutRef = ref<HTMLElement | null>(null)
const isMaximized = ref(false)
const currentYear = new Date().getFullYear()

// ── 更新状态 ──
const updateReady = ref(false)
const updateVersion = ref('')

function installUpdate() {
  window.cherryclip?.updater.installUpdate()
}

function onDocClick(e: MouseEvent) {
  if (aboutRef.value && !aboutRef.value.contains(e.target as Node)) {
    showAbout.value = false
  }
}

let _maximizeHandler: ((maximized: boolean) => void) | null = null
let _statusHandler: ((data: any) => void) | null = null

onMounted(() => {
  document.addEventListener('click', onDocClick)
  _maximizeHandler = (maximized: boolean) => {
    isMaximized.value = maximized
  }
  window.cherryclip?.window.onMaximizeChange(_maximizeHandler)

  // 监听更新状态（仅下载完成）
  _statusHandler = (data: any) => {
    if (data.status === 'downloaded') {
      updateReady.value = true
      updateVersion.value = data.version || ''
    }
  }
  window.cherryclip?.updater.onStatus(_statusHandler)
})
onUnmounted(() => {
  document.removeEventListener('click', onDocClick)
  window.cherryclip?.window?.offMaximizeChange?.()
  window.cherryclip?.updater?.offStatus?.()
})

function minimize() {
  window.cherryclip?.window.minimize()
}

function maximize() {
  window.cherryclip?.window.maximize()
}

function closeWin() {
  window.cherryclip?.window.close()
}
</script>

<style scoped>
.title-bar {
  display: flex;
  align-items: center;
  height: 34px;
  background: color-mix(in srgb, var(--surface-base) 78%, transparent);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border-bottom: 1px solid var(--surface-glass-edge);
  flex-shrink: 0;
  user-select: none;
  position: relative;
  z-index: 100;
}
.title-bar::after {
  content: '';
  position: absolute;
  bottom: -1px;
  left: 80px;
  right: 80px;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(99, 102, 241, 0.15) 30%, rgba(99, 102, 241, 0.15) 70%, transparent);
  opacity: 0.4;
}

.title-bar-drag {
  flex: 1;
  display: flex;
  align-items: center;
  height: 100%;
  -webkit-app-region: drag;
  padding-left: 12px;
}

.title-bar-brand {
  display: flex;
  align-items: center;
  gap: 8px;
}

.title-logo {
  width: 20px;
  height: 20px;
  background: var(--bg-logo);
  border-radius: 5px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  box-shadow: 0 0 6px var(--brand-glow);
}

.title-text {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
  letter-spacing: 0.3px;
}

/* ========== 关于 ========== */
.about-area { position: relative; }
.about-btn {
  width: 34px;
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  -webkit-app-region: no-drag;
  transition: all 0.12s;
}
.about-btn:hover { color: var(--text-secondary); background: var(--bg-hover); }

/* ========== 更新就绪按钮 ========== */
.update-ready-btn {
  height: 26px;
  padding: 0 8px;
  display: flex;
  align-items: center;
  gap: 4px;
  background: rgba(34, 197, 94, 0.08);
  border: 1px solid rgba(34, 197, 94, 0.2);
  border-radius: var(--radius-sm);
  color: var(--accent-green);
  cursor: pointer;
  -webkit-app-region: no-drag;
  transition: all 0.12s;
  font-size: 10px;
  font-family: inherit;
  font-weight: 500;
}
.update-ready-btn:hover { background: rgba(34, 197, 94, 0.15); border-color: rgba(34, 197, 94, 0.35); }
.update-ready-dot {
  width: 6px; height: 6px;
  background: var(--accent-green);
  border-radius: 50%;
  animation: pulse-dot 2s ease-in-out infinite;
}
.update-ready-text { font-variant-numeric: tabular-nums; }
@keyframes pulse-dot {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.5; transform: scale(1.3); }
}

.about-popup {
  position: absolute;
  top: 100%;
  right: 0;
  width: 200px;
  background: var(--surface-glass);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border: 1px solid var(--surface-glass-edge);
  border-radius: var(--radius);
  padding: 16px 14px 12px;
  box-shadow: var(--shadow-md);
  text-align: center;
}
.about-logo {
  width: 36px; height: 36px;
  background: var(--bg-logo);
  border-radius: 9px;
  display: flex; align-items: center; justify-content: center;
  margin: 0 auto 8px;
  box-shadow: 0 0 10px var(--brand-glow);
}
.about-name { font-size: 14px; font-weight: 700; color: var(--text-primary); margin-bottom: 1px; }
.about-version { font-size: 10px; color: var(--text-accent); font-weight: 500; margin-bottom: 10px; }
.about-desc { font-size: 10px; color: var(--text-secondary); line-height: 1.5; margin-bottom: 6px; }
.about-tech { font-size: 9px; color: var(--text-muted); margin-bottom: 8px; }
.about-copy { font-size: 9px; color: var(--text-muted); opacity: 0.5; }

.about-drop-enter-active { transition: all 0.15s ease-out; }
.about-drop-leave-active { transition: all 0.1s ease-in; }
.about-drop-enter-from { opacity: 0; transform: translateY(-5px) scale(0.96); }
.about-drop-leave-to { opacity: 0; transform: translateY(-3px) scale(0.97); }

/* ========== 窗口控制 ========== */
.title-bar-controls { display: flex; align-items: center; height: 100%; }
.ctrl-btn {
  width: 42px;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  -webkit-app-region: no-drag;
  transition: background 0.12s, color 0.12s;
}
.ctrl-btn:hover { background: var(--bg-hover); color: var(--text-primary); }
.ctrl-close:hover { background: #E81123; color: #FFF; }
</style>
