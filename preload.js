/**
 * ClipMind — Preload 脚本
 * 安全桥接：渲染进程 ← IPC → 主进程 ← stdio → Python
 */
const { contextBridge, ipcRenderer, webUtils } = require('electron');

// ── RPC 事件缓冲（解决主进程先发事件、渲染进程还没注册监听器的竞态）──

let _onMsg = null;
const _msgBuffer = [];

// ── 窗口最大化状态监听器 ──
let _maximizeOnMax = null;
let _maximizeOnUnmax = null;
function offMaximizeChange() {
  if (_maximizeOnMax) { ipcRenderer.removeListener('window-maximized', _maximizeOnMax); _maximizeOnMax = null; }
  if (_maximizeOnUnmax) { ipcRenderer.removeListener('window-unmaximized', _maximizeOnUnmax); _maximizeOnUnmax = null; }
}

// ── 更新状态监听器 ──
let _updateStatusListener = null;
function offUpdateStatus() {
  if (_updateStatusListener) { ipcRenderer.removeListener('update-status', _updateStatusListener); _updateStatusListener = null; }
}

// 始终监听 python-message，一开始缓冲，等回调注册后转发
ipcRenderer.on('python-message', (_, msg) => {
  if (_onMsg) {
    _onMsg(msg);
  } else {
    console.log('[Preload] 缓冲事件:', msg.event || msg.id);
    _msgBuffer.push(msg);
  }
});

contextBridge.exposeInMainWorld('cherryclip', {
  // ── RPC 通信 ──

  rpc: {
    /**
     * 发送 JSON-RPC 请求给 Python 后端
     * @param {string} method - 方法名
     * @param {object} params - 参数
     * @param {number} [id] - 请求 ID（自动生成）
     */
    send(method, params, id) {
      const msgId = id || Date.now() + Math.floor(Math.random() * 1000);
      ipcRenderer.send('rpc-send', {
        id: msgId,
        method,
        params: params || {},
      });
      return msgId;
    },

    /**
     * 注册 Python 事件回调，立即冲刷缓冲的事件
     * @param {(msg: object) => void} callback
     * @returns {() => void} 取消注册
     */
    onMessage(callback) {
      _onMsg = callback;
      // 冲刷缓冲
      console.log('[Preload] onMessage 回调注册，缓冲事件数:', _msgBuffer.length);
      if (_msgBuffer.length > 0) {
        const msgs = _msgBuffer.splice(0);
        for (const m of msgs) {
          callback(m);
        }
      }
      return () => {
        console.log('[Preload] onMessage 取消注册');
        _onMsg = null;
      };
    },
  },

  // ── 文件对话框 ──

  dialogs: {
    openFiles(options) {
      return ipcRenderer.invoke('open-files', options);
    },
    saveFile(options) {
      return ipcRenderer.invoke('save-file', options);
    },
  },

  // ── 窗口控制 ──

  window: {
    minimize() { ipcRenderer.send('window-minimize'); },
    maximize() { ipcRenderer.send('window-maximize'); },
    close() { ipcRenderer.send('window-close'); },
    setTitle(title) { ipcRenderer.send('window-set-title', title); },
    onMaximizeChange(callback) {
      offMaximizeChange();  // 移除旧 listener 防止重复注册
      _maximizeOnMax = () => callback(true);
      _maximizeOnUnmax = () => callback(false);
      ipcRenderer.on('window-maximized', _maximizeOnMax);
      ipcRenderer.on('window-unmaximized', _maximizeOnUnmax);
    },
    offMaximizeChange,
  },

  // ── 平台信息 ──

  platform: process.platform,

  // ── 拖拽文件路径解析 ──

  /** 通过 Electron webUtils 获取拖拽文件的真实路径（替代已弃用的 File.path） */
  getPathForFile(file) {
    try {
      return webUtils.getPathForFile(file);
    } catch {
      return null;
    }
  },

  // ── 文件系统 ──

  fs: {
    /**
     * 读取本地文件为 Blob（用于音频/视频预览）
     */
    async readFileAsBlob(filePath) {
      return ipcRenderer.invoke('fs-read-file-blob', filePath);
    },

    /**
     * 读取目录下的文件名列表
     */
    readDirectory(dirPath) {
      return ipcRenderer.invoke('fs-read-directory', dirPath);
    },

    /**
     * ffmpeg 提取视频片段到临时文件（预览捕获用）
     * @returns {Promise<string>} 临时文件路径
     */
    extractVideoSegment(videoPath, startTime, endTime) {
      return ipcRenderer.invoke('extract-video-segment', { videoPath, startTime, endTime });
    },

    /**
     * 删除文件（清理临时文件用）
     */
    deleteFile(filePath) {
      return ipcRenderer.invoke('fs-delete-file', filePath);
    },

    /**
     * 写入文本文件（UTF-8）
     */
    writeFile(filePath, content) {
      return ipcRenderer.invoke('fs-write-file', filePath, content);
    },
  },

  // ── 桌面通知 ──

  notifications: {
    show(title, body) {
      ipcRenderer.send('show-notification', { title, body });
    },
  },

  // ── 自动更新 ──

  updater: {
    /** 手动检查更新 */
    checkForUpdates() {
      return ipcRenderer.invoke('check-for-updates');
    },
    /** 安装已下载的更新（重启应用） */
    installUpdate() {
      ipcRenderer.send('install-update');
    },
    /** 监听更新状态（仅 'downloaded' 事件） */
    onStatus(callback) {
      offUpdateStatus();  // 移除旧 listener 防止重复注册
      _updateStatusListener = (_, data) => callback(data);
      ipcRenderer.on('update-status', _updateStatusListener);
    },
    /** 取消注册更新状态监听 */
    offStatus() { offUpdateStatus(); },
    /** 获取/设置自动更新开关 */
    getAutoUpdateSetting() {
      return ipcRenderer.invoke('get-auto-update-setting');
    },
    setAutoUpdateSetting(enabled) {
      return ipcRenderer.invoke('set-auto-update-setting', enabled);
    },
  },

  // ── 版本信息 ──

  getAppVersion() {
    return ipcRenderer.invoke('get-app-version');
  },

  // ── 就绪通知 ──

  ready() {
    ipcRenderer.send('window-ready');
  },
});
