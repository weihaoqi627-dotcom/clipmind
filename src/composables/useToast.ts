/**
 * useToast — 全局 Toast 通知
 * 使用单例模式，所有组件共享同一个 toast 队列
 */
import { ref, readonly } from 'vue'

export interface Toast {
  id: number
  message: string
  type: 'success' | 'error' | 'info'
}

let _nextId = 0
const toasts = ref<Toast[]>([])
const DURATION = 3000

export function useToast() {
  function show(message: string, type: 'success' | 'error' | 'info' = 'info', duration = DURATION) {
    const id = ++_nextId
    toasts.value.push({ id, message, type })
    setTimeout(() => {
      toasts.value = toasts.value.filter(t => t.id !== id)
    }, duration)
  }

  function success(msg: string) { show(msg, 'success') }
  function error(msg: string) { show(msg, 'error', 5000) }
  function info(msg: string) { show(msg, 'info') }

  return {
    toasts: readonly(toasts),
    toast: show,
    success,
    error,
    info,
  }
}
