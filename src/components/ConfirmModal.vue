<template>
  <Teleport to="body">
    <div v-if="visible" class="modal-overlay" @click.self="onCancel">
      <div class="modal-box">
        <div class="modal-header">
          <span class="modal-title">{{ title }}</span>
        </div>
        <div class="modal-body">{{ message }}</div>
        <div class="modal-actions">
          <button class="btn-cancel" @click="onCancel" ref="cancelBtn">{{ cancelText }}</button>
          <button class="btn-confirm" :class="danger ? 'danger' : ''" @click="onConfirm" ref="confirmBtn">{{ confirmText }}</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'

const props = withDefaults(defineProps<{
  visible: boolean
  title?: string
  message?: string
  confirmText?: string
  cancelText?: string
  danger?: boolean
}>(), {
  title: '确认操作',
  message: '确定要执行此操作吗？',
  confirmText: '确认',
  cancelText: '取消',
  danger: true,
})

const emit = defineEmits<{
  confirm: []
  cancel: []
}>()

const cancelBtn = ref<HTMLElement | null>(null)

watch(() => props.visible, async (v) => {
  if (v) {
    await nextTick()
    cancelBtn.value?.focus()
  }
})

function onConfirm() {
  emit('confirm')
}

function onCancel() {
  emit('cancel')
}
</script>

<style scoped>
.modal-overlay {
  position: fixed;
  inset: 0;
  background: var(--overlay-bg);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal-box {
  background: var(--surface-glass);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border: 1px solid var(--surface-glass-edge);
  border-radius: var(--radius-xl);
  padding: 22px;
  min-width: 340px;
  max-width: 420px;
  box-shadow: var(--shadow-lg);
}

.modal-header { margin-bottom: 10px; }
.modal-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--text-primary);
}

.modal-body {
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.6;
  margin-bottom: 20px;
  white-space: pre-wrap;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.btn-cancel {
  padding: 7px 16px;
  background: var(--surface-overlay);
  border: 1px solid var(--border-card);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.12s;
}
.btn-cancel:hover { background: var(--bg-hover); color: var(--text-primary); }

.btn-confirm {
  padding: 7px 16px;
  background: var(--brand);
  border: none;
  border-radius: var(--radius-sm);
  color: var(--text-on-brand);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.12s;
}
.btn-confirm:hover { background: var(--brand-light); }
.btn-confirm.danger { background: #DC2626; }
.btn-confirm.danger:hover { background: #EF4444; }
</style>
