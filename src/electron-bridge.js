/**
 * Electron 42 桥接模块
 *
 * 在 Electron 42 中，`require('electron')` 被 npm 包的 index.js 挡住了。
 * 这个桥接模块通过低层级方式导入真正的 Electron 内置模块。
 *
 * 用法：
 *   const { app, BrowserWindow, ipcMain, dialog } = require('./src/electron-bridge')
 */

// 获取真正 Electron 内置模块的方式：
// 在 Electron 42 中，内置模块不直接暴露给 require，
// 但 process._linkedBinding() 可以访问底层 C++ 绑定。
// 而 Electron JS API 可以通过 electron/js2c/browser_init 访问，
// 但需要正确的初始化上下文。
//
// 最可靠的方式：直接使用 process._linkedBinding 获取每个原生模块。

const _bindings = {
  app:              () => process._linkedBinding('electron_browser_app').app,
  autoUpdater:      () => process._linkedBinding('electron_browser_auto_updater'),
  BrowserWindow:    () => process._linkedBinding('electron_browser_window').BrowserWindow,
  BaseWindow:       () => process._linkedBinding('electron_browser_base_window').BaseWindow,
  contentTracing:   () => process._linkedBinding('electron_browser_content_tracing'),
  dialog:           () => process._linkedBinding('electron_browser_dialog'),
  ipcMain:          () => process._linkedBinding('electron_browser_ipc_main'),
  ipcRenderer:      () => process._linkedBinding('electron_renderer_ipc'),
  Menu:             () => process._linkedBinding('electron_browser_menu').Menu,
  MenuItem:         () => process._linkedBinding('electron_browser_menu').MenuItem,
  nativeImage:      () => process._linkedBinding('electron_common_native_image'),
  net:              () => process._linkedBinding('electron_browser_net'),
  netLog:           () => process._linkedBinding('electron_browser_net_log'),
  Notification:     () => process._linkedBinding('electron_browser_notification'),
  powerMonitor:     () => process._linkedBinding('electron_browser_power_monitor'),
  powerSaveBlocker: () => process._linkedBinding('electron_browser_power_save_blocker'),
  protocol:         () => process._linkedBinding('electron_browser_protocol'),
  screen:           () => process._linkedBinding('electron_browser_screen'),
  session:          () => process._linkedBinding('electron_browser_session').session,
  shell:            () => process._linkedBinding('electron_browser_shell'),
  systemPreferences:() => process._linkedBinding('electron_browser_system_preferences'),
  Tray:             () => process._linkedBinding('electron_browser_tray'),
  webContents:      () => process._linkedBinding('electron_browser_web_contents').webContents,
}

// 缓存已加载的绑定
const _cache = {}

function getBinding(name) {
  if (_cache[name]) return _cache[name]
  try {
    const factory = _bindings[name]
    if (!factory) throw new Error(`未知绑定: ${name}`)
    _cache[name] = factory()
    return _cache[name]
  } catch (e) {
    console.error(`[electron-bridge] 加载 ${name} 失败:`, e.message)
    return undefined
  }
}

// 导出代理
module.exports = new Proxy({}, {
  get(_, prop) {
    if (typeof prop === 'string' && prop in _bindings) {
      return getBinding(prop)
    }
    return undefined
  },
  has(_, prop) {
    return typeof prop === 'string' && prop in _bindings
  },
  ownKeys() {
    return Object.keys(_bindings)
  },
  getOwnPropertyDescriptor() {
    return { enumerable: true, configurable: true }
  },
})
