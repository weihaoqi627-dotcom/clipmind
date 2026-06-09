<template>
  <Teleport to="body">
    <Transition name="modal-fade">
      <div v-if="visible" class="shortcuts-overlay" @click.self="$emit('close')">
        <div class="shortcuts-panel">
          <div class="shortcuts-header">
            <h2 class="shortcuts-title">键盘快捷键</h2>
            <button class="shortcuts-close" @click="$emit('close')">×</button>
          </div>
          <div class="shortcuts-body">
            <div v-for="group in groups" :key="group.name" class="shortcut-group">
              <div class="group-label">{{ group.name }}</div>
              <div v-for="s in group.items" :key="s.keys" class="shortcut-row">
                <span class="shortcut-keys">
                  <kbd v-for="k in s.keyList" :key="k">{{ k }}</kbd>
                </span>
                <span class="shortcut-desc">{{ s.desc }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
interface ShortcutItem {
  keyList: string[]
  desc: string
}

interface ShortcutGroup {
  name: string
  items: ShortcutItem[]
}

defineProps<{ visible: boolean }>()
defineEmits<{ close: [] }>()

const groups: ShortcutGroup[] = [
  {
    name: '播放控制',
    items: [
      { keyList: ['Space'], desc: '播放 / 暂停' },
      { keyList: ['←'], desc: '后退一帧（暂停时）' },
      { keyList: ['→'], desc: '前进一帧（暂停时）' },
    ],
  },
  {
    name: '导航',
    items: [
      { keyList: ['⌘', '1'], desc: '切换到聊天' },
      { keyList: ['⌘', '2'], desc: '切换到预览' },
    ],
  },
  {
    name: '通用',
    items: [
      { keyList: ['F2'], desc: '重命名当前项目' },
      { keyList: ['?'], desc: '显示此快捷面板' },
      { keyList: ['Esc'], desc: '关闭面板 / 取消' },
    ],
  },
]
</script>

<style scoped>
.shortcuts-overlay {
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

.shortcuts-panel {
  width: 440px;
  max-height: 70vh;
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

.shortcuts-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px 12px;
  border-bottom: 1px solid var(--border-subtle);
}

.shortcuts-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0;
}

.shortcuts-close {
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
.shortcuts-close:hover { background: var(--bg-hover); color: var(--text-primary); }

.shortcuts-body {
  flex: 1;
  overflow-y: auto;
  padding: 14px 20px 20px;
}

.shortcut-group { margin-bottom: 16px; }
.shortcut-group:last-child { margin-bottom: 0; }

.group-label {
  font-size: 10px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.4px;
  margin-bottom: 8px;
}

.shortcut-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 0;
}

.shortcut-keys { display: flex; gap: 3px; }

.shortcut-keys kbd {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 24px;
  height: 22px;
  padding: 0 6px;
  background: var(--surface-elevated);
  border: 1px solid var(--border-card);
  border-radius: 4px;
  font-size: 11px;
  font-family: 'SF Mono', 'Cascadia Code', monospace;
  color: var(--text-primary);
  box-shadow: 0 1px 0 rgba(0,0,0,0.2);
}

.shortcut-desc {
  font-size: 12px;
  color: var(--text-secondary);
}

/* 复用 modal-fade 动画 */
.modal-fade-enter-active { transition: all 0.2s ease-out; }
.modal-fade-leave-active { transition: all 0.15s ease-in; }
.modal-fade-enter-from { opacity: 0; }
.modal-fade-enter-from .shortcuts-panel { transform: scale(0.96) translateY(-6px); }
.modal-fade-leave-to { opacity: 0; }
.modal-fade-leave-to .shortcuts-panel { transform: scale(0.96) translateY(-6px); }
</style>
