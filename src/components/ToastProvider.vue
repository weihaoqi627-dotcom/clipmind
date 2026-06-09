<template>
  <Teleport to="body">
    <TransitionGroup name="toast" tag="div" class="toast-container">
      <div
        v-for="t in toasts"
        :key="t.id"
        class="toast-item"
        :class="t.type"
      >
        <span class="toast-icon">{{ iconMap[t.type] }}</span>
        <span class="toast-msg">{{ t.message }}</span>
      </div>
    </TransitionGroup>
  </Teleport>
</template>

<script setup lang="ts">
import { useToast } from '../composables/useToast'

const { toasts } = useToast()

const iconMap: Record<string, string> = {
  success: '✓',
  error: '✗',
  info: 'ℹ',
}
</script>

<style scoped>
.toast-container {
  position: fixed;
  bottom: 24px;
  right: 24px;
  z-index: 9999;
  display: flex;
  flex-direction: column-reverse;
  gap: 8px;
  pointer-events: none;
}

.toast-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 14px;
  border-radius: var(--radius);
  background: var(--surface-glass);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border: 1px solid var(--surface-glass-edge);
  box-shadow: var(--shadow-md);
  font-size: 12px;
  color: var(--text-primary);
  max-width: 340px;
  pointer-events: auto;
}

.toast-item.success { border-color: rgba(34, 197, 94, 0.3); }
.toast-item.error   { border-color: rgba(239, 68, 68, 0.3); }
.toast-item.info    { border-color: rgba(99, 102, 241, 0.2); }

.toast-icon {
  flex-shrink: 0;
  width: 18px;
  text-align: center;
  font-size: 12px;
  font-weight: 700;
}
.toast-item.success .toast-icon { color: var(--accent-green); }
.toast-item.error   .toast-icon { color: var(--accent-red); }
.toast-item.info    .toast-icon { color: var(--brand); }

.toast-msg { line-height: 1.4; }

/* ========== 过渡动画 ========== */
.toast-enter-active { transition: all 0.25s ease-out; }
.toast-leave-active { transition: all 0.2s ease-in; }
.toast-enter-from { opacity: 0; transform: translateX(40px) scale(0.95); }
.toast-leave-to { opacity: 0; transform: translateX(20px); }
.toast-move { transition: transform 0.25s ease; }
</style>
