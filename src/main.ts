import { createApp } from 'vue'
import App from './App.vue'
import router from './router'

// 构建标记：每次构建更新此时间戳，用于验证前端是否加载了最新版本
console.log('[ClipMind] 🏗 前端构建时间: 2026-05-29T21:10:00+08:00 (fix: 拖拽路径验证 + 分析失败检测 + 管道解耦 + VL超时)')

// 通知主进程渲染进程已就绪
window.cherryclip?.ready()

const app = createApp(App)

// 全局错误捕获（防止 Vue 内部错误静默丢失）
app.config.errorHandler = (err, instance, info) => {
  console.error('[Vue Error]', err, '|', info)
}
app.config.warnHandler = (msg, instance, trace) => {
  console.warn('[Vue Warn]', msg)
}

app.use(router)
app.mount('#app')
