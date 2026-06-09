/**
 * ClipMind — Electron 主进程（新版）
 * ======================================
 * 启动 Python RPC 子进程（stdio 管道通信）
 * 创建桌面窗口，加载 Vue 前端
 *
 * 通信架构:
 *   渲染进程 ← IPC → 主进程 ← stdio → Python RPC
 *
 * 开发模式: Vite dev server (localhost:5173)
 * 生产模式: 加载 dist/index.html (file://)
 */
const { app, BrowserWindow, ipcMain, dialog, Notification } = require('electron');
const { spawn, execSync } = require('child_process');
const { autoUpdater } = require('electron-updater');
const path = require('path');
const pkg = require('./package.json');
const fs = require('fs');
const os = require('os');

// ── 启动日志 ───────────────────────────
// 持久化到用户数据目录（打包后也不会丢失）
function _getLogDir() {
  try {
    const dir = path.join(app.getPath('userData'), 'logs');
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    return dir;
  } catch {
    // 回退：exe 同目录（便携版）
    try {
      const d = path.dirname(app.getPath('exe'));
      if (!fs.existsSync(d)) fs.mkdirSync(d, { recursive: true });
      return d;
    } catch {
      return __dirname;
    }
  }
}
const LOG_DIR = _getLogDir();
const LOG_FILE = path.join(LOG_DIR, 'startup.log');
function startupLog(msg) {
  const ts = new Date().toISOString();
  const line = `[${ts}] ${msg}\n`;
  try { fs.appendFileSync(LOG_FILE, line, 'utf8'); } catch {}
  console.log(`[ClipMind] ${msg}`);
}
// 窗口状态文件
const WINDOW_STATE_FILE = path.join(LOG_DIR, 'window-state.json');

function loadWindowState() {
  try {
    if (fs.existsSync(WINDOW_STATE_FILE)) {
      return JSON.parse(fs.readFileSync(WINDOW_STATE_FILE, 'utf8'));
    }
  } catch (e) {
    startupLog(`加载窗口状态失败: ${e.message}`);
  }
  return null;
}

function saveWindowState() {
  if (!mainWindow) return;
  try {
    const bounds = mainWindow.getBounds();
    const state = {
      x: bounds.x,
      y: bounds.y,
      width: bounds.width,
      height: bounds.height,
      isMaximized: mainWindow.isMaximized(),
    };
    fs.writeFileSync(WINDOW_STATE_FILE, JSON.stringify(state, null, 2), 'utf8');
  } catch (e) {
    startupLog(`保存窗口状态失败: ${e.message}`);
  }
}

// 不覆盖旧日志，追加模式
startupLog('=== 新会话 ===');
startupLog('Electron 主进程启动');
startupLog(`日志目录: ${LOG_DIR}`);
startupLog(`进程: ${process.execPath}`);
startupLog(`工作目录: ${process.cwd()}`);
startupLog(`Node: ${process.version}`);
startupLog(`平台: ${process.platform} ${process.arch}`);

// 开发模式: 项目根目录 = __dirname
// 打包模式: Python 代码在 process.resourcesPath（extraResources）
const PROJECT_ROOT = app.isPackaged ? process.resourcesPath : __dirname;

// ── 自动更新配置 ───────────────────────────
const CONFIG_FILE = path.join(PROJECT_ROOT, 'config.json');
function _readAutoUpdateSetting() {
  try {
    if (fs.existsSync(CONFIG_FILE)) {
      const cfg = JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf8'));
      return cfg?.settings?.general?.auto_update !== false;
    }
  } catch {}
  return true;
}
const UPDATE_FEED_URL = process.env.CLIPMIND_UPDATE_URL || '';
if (UPDATE_FEED_URL) {
  autoUpdater.setFeedURL(UPDATE_FEED_URL);
}
autoUpdater.autoDownload = _readAutoUpdateSetting();
autoUpdater.autoInstallOnAppQuit = true;

// ── Windows AppUserModelID（必须在 app.whenReady 之前设置）──
// 让 Windows 把我们的进程关联到正确的任务栏图标和名称，
// 而不是显示默认的 Electron 图标
if (process.platform === 'win32') {
  app.setAppUserModelId('com.clipmind.app');
}

// 开发模式检测：NODE_ENV=development 时加载 Vite dev server
const isDev = !app.isPackaged && process.env.NODE_ENV === 'development';

let mainWindow = null;
let pythonProcess = null;
let backendProcess = null;  // FastAPI HTTP 后端进程（端口 8765）
let _restartAttempts = 0;
let _isShuttingDown = false;  // cleanup() 设为 true，防自动重启
const MAX_RESTART_ATTEMPTS = 3;

// 已收到的 RPC 事件缓存（在 Vue 加载前暂存）
let eventBuffer = [];

// ── Python 进程管理 ───────────────────────────

function findEnvPath() {
  // 1. 便携版：PORTABLE_EXECUTABLE_DIR 指向原始 exe 所在目录
  const portableDir = process.env.PORTABLE_EXECUTABLE_DIR;
  if (portableDir) {
    const portableEnv = path.join(portableDir, '.env');
    if (fs.existsSync(portableEnv)) {
      startupLog(`找到 .env (便携版): ${portableEnv}`);
      return portableEnv;
    }
  }
  // 2. exe 同目录（打包后用户把 .env 放 exe 旁边）
  try {
    const exeDir = path.dirname(app.getPath('exe'));
    const envPath = path.join(exeDir, '.env');
    if (fs.existsSync(envPath)) {
      startupLog(`找到 .env: ${envPath}`);
      return envPath;
    }
  } catch {}
  // 3. 项目根目录（开发模式）
  const rootEnv = path.join(PROJECT_ROOT, '.env');
  if (fs.existsSync(rootEnv)) {
    startupLog(`找到 .env (项目根): ${rootEnv}`);
    return rootEnv;
  }
  startupLog('⚠ .env 未找到');
  return '';
}

function findPython() {
  // 优先级：打包的嵌入运行时 > 系统 python
  const embedded = path.join(PROJECT_ROOT, 'python_runtime', 'python.exe');
  if (fs.existsSync(embedded)) {
    startupLog(`使用嵌入式 Python: ${embedded}`);
    return embedded;
  }

  const cmd = process.platform === 'win32' ? 'python' : 'python3';
  // 快速探测 Python 是否在 PATH 中
  try {
    const result = execSync(`"${cmd}" --version`, {
      encoding: 'utf8', timeout: 5000, stdio: 'pipe', windowsHide: true,
    });
    startupLog(`系统 Python: ${result.trim()}`);
    return cmd;
  } catch (e) {
    startupLog(`ERROR: ${cmd} 不在 PATH 中 — ${e.message}`);
    // 尝试常见路径
    const altPaths = process.platform === 'win32'
      ? ['C:\\Python314\\python.exe', 'C:\\Python313\\python.exe', 'C:\\Python312\\python.exe',
         'C:\\Python311\\python.exe', 'C:\\Python310\\python.exe']
      : [];
    for (const alt of altPaths) {
      if (fs.existsSync(alt)) {
        startupLog(`使用备用路径: ${alt}`);
        return alt;
      }
    }
    throw new Error('找不到 Python，请确保 python 在 PATH 中或安装 Python');
  }
}

function startPythonProcess() {
  return new Promise((resolve, reject) => {
    const python = findPython();
    const cwd = PROJECT_ROOT;
    let stderrBuffer = '';  // 收集 stderr 用于错误诊断

    console.log(`[ClipMind] 启动 Python: ${python} -m server.main`);
    console.log(`[ClipMind] 工作目录: ${cwd}`);
    startupLog(`启动 Python: ${python} -m server.main`);
    startupLog(`工作目录: ${cwd}`);

    pythonProcess = spawn(python, ['-m', 'server.main'], {
      cwd,
      env: {
        ...process.env,
        PYTHONUNBUFFERED: '1',
        PYTHONPATH: cwd,  // 嵌入式 Python 的 _pth 不包含工作目录，需显式指定
        // 让 Python 能找到 .env（打包后在 exe 同目录，开发时在项目根）
        CLIPMIND_ENV_PATH: findEnvPath(),
        // 可写数据目录（打包后用户数据目录，避免写入 Program Files）
        CLIPMIND_DATA_HOME: app.getPath('userData'),
      },
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    });

    pythonProcess.on('error', (err) => {
      startupLog(`ERROR: Python spawn 失败 — ${err.message}`);
      reject(new Error(`Python 启动失败: ${err.message}`));
    });

    pythonProcess.on('exit', (code) => {
      startupLog(`Python 进程退出 (code=${code})`);
      if (stderrBuffer) startupLog(`Python stderr 汇总: ${stderrBuffer.slice(0, 500)}`);
      console.log(`[ClipMind] Python 进程退出 (code=${code})`);
      pythonProcess = null;
      // 通知前端后端已崩溃（用缓冲机制，因为 mainWindow 可能还不存在）
      const errMsg = { event: 'backend_error', message: `Python 进程异常退出 (code=${code})` };
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('python-message', errMsg);
      } else {
        eventBuffer.push(errMsg);
      }
      // 非正常退出时自动重启（最多 MAX_RESTART_ATTEMPTS 次）
      // 仅在窗口仍活跃且非主动关闭时重启
      // 开发模式（npm run dev）下不自动重启，避免级联
      if (code !== 0 && !_isShuttingDown && !isDev && mainWindow && !mainWindow.isDestroyed()
          && _restartAttempts < MAX_RESTART_ATTEMPTS) {
        _restartAttempts++;
        startupLog(`Python 进程退出，2 秒后尝试第 ${_restartAttempts} 次重启...`);
        setTimeout(() => {
          startPythonProcess().catch(err => {
            startupLog(`Python 进程第 ${_restartAttempts} 次重启失败: ${err.message}`);
          });
        }, 2000);
      } else if (code !== 0) {
        startupLog('Python 进程已超过最大重启次数');
      } else {
        // 正常退出(code=0)，重置重启计数器
        _restartAttempts = 0;
      }
    });

    // 就绪标记：持久监听器负责检测 ready，不再用临时监听器
    let _readyReceived = false;

    // 监听 stdout —— 每行一个 JSON（RPC 响应或事件）
    let buffer = '';
    pythonProcess.stdout.on('data', (chunk) => {
      buffer += chunk.toString();
      const lines = buffer.split('\n');
      buffer = lines.pop(); // 保留最后一个不完整的行

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        try {
          const msg = JSON.parse(trimmed);
          // 检测 ready：用持久监听器统一处理，避免多行 chunk 丢事件
          if (!_readyReceived && msg.event === 'ready') {
            _readyReceived = true;
            clearTimeout(timeout);
            startupLog('收到 Python ready 事件');
            resolve();
          }
          handlePythonMessage(msg);
        } catch (parseErr) {
          // Python stdout 可能输出连续 JSON（buffering），尝试按 }}{ 拆开
          let recovered = false;
          if (trimmed.includes('}{')) {
            const parts = trimmed.split(/(?<=\})(?=\{)/);
            for (const part of parts) {
              const p = part.trim();
              if (!p) continue;
              try {
                const msg = JSON.parse(p);
                if (!_readyReceived && msg.event === 'ready') {
                  _readyReceived = true;
                  clearTimeout(timeout);
                  startupLog('收到 Python ready 事件');
                  resolve();
                }
                handlePythonMessage(msg);
                recovered = true;
              } catch { /* 还是失败，放弃这个片段 */ }
            }
          }
          if (!recovered) {
            const hex = Buffer.from(trimmed.slice(0, 20), 'utf8').toString('hex');
            console.warn('[ClipMind] 无法解析 Python 输出:', trimmed.slice(0, 200), '| parseError:', parseErr.message, '| hex[0:20]:', hex);
          }
        }
      }
    });

    pythonProcess.stderr.on('data', (data) => {
      const text = data.toString().trim();
      if (text) {
        stderrBuffer += text + '\n';
        startupLog(`Python stderr: ${text.slice(0, 200)}`);
        console.error(`[Python 错误] ${data}`);
      }
    });

    // 等待 ready 事件
    const timeout = setTimeout(() => {
      if (_readyReceived) {
        // ready 已被持久监听器捕获，忽略超时（临时监听器 removed）
        startupLog('Python ready 已由持久监听器接收，忽略超时');
        resolve();
        return;
      }
      const errMsg = stderrBuffer
        ? `Python 启动超时（15 秒未收到 ready）\nstderr: ${stderrBuffer.slice(0, 300)}`
        : 'Python 启动超时（15 秒未收到 ready）';
      reject(new Error(errMsg));
    }, 15000);
  });
}

function sendToPython(jsonMsg) {
  if (!pythonProcess || !pythonProcess.stdin.writable) {
    console.error('[ClipMind] Python 进程不可写');
    return;
  }
  const line = JSON.stringify(jsonMsg) + '\n';
  pythonProcess.stdin.write(line);
}

// ── FastAPI HTTP 后端进程（认证/计费 API） ──────

function startBackendProcess() {
  return new Promise((resolve, reject) => {
    const python = findPython();
    const cwd = PROJECT_ROOT;
    let stderrBuffer = '';

    startupLog(`启动 HTTP 后端: ${python} -m uvicorn backend.main:app --host 127.0.0.1 --port 8765`);
    console.log(`[ClipMind] 启动 HTTP 后端: ${python} -m uvicorn backend.main:app --host 127.0.0.1 --port 8765`);

    backendProcess = spawn(python, [
      '-m', 'uvicorn', 'backend.main:app',
      '--host', '127.0.0.1', '--port', '8765',
    ], {
      cwd,
      env: {
        ...process.env,
        PYTHONUNBUFFERED: '1',
        PYTHONPATH: cwd,
        CLIPMIND_ENV_PATH: findEnvPath(),
        CLIPMIND_DATA_HOME: app.getPath('userData'),
      },
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    });

    backendProcess.on('error', (err) => {
      startupLog(`ERROR: HTTP 后端 spawn 失败 — ${err.message}`);
      reject(new Error(`HTTP 后端启动失败: ${err.message}`));
    });

    backendProcess.on('exit', (code) => {
      startupLog(`HTTP 后端进程退出 (code=${code})`);
      if (stderrBuffer) startupLog(`HTTP 后端 stderr: ${stderrBuffer.slice(0, 300)}`);
      console.log(`[ClipMind] HTTP 后端退出 (code=${code})`);
      backendProcess = null;
    });

    backendProcess.stderr.on('data', (data) => {
      const text = data.toString().trim();
      if (text) {
        stderrBuffer += text + '\n';
        startupLog(`HTTP 后端 stderr: ${text.slice(0, 200)}`);
      }
    });

    // 后端启动可能比 RPC 慢，给 30 秒超时
    // 监听 stdout/stderr 含 "Uvicorn running on" 视为就绪
    let resolved = false;
    const onData = (chunk) => {
      const text = chunk.toString();
      if (!resolved && text.includes('Uvicorn running on')) {
        resolved = true;
        clearTimeout(timeout);
        startupLog('HTTP 后端就绪 (Uvicorn running)');
        resolve();
      }
    };
    backendProcess.stdout.on('data', onData);
    backendProcess.stderr.on('data', onData);  // uvicorn 的启动日志在 stderr

    const timeout = setTimeout(() => {
      if (!resolved) {
        // 超时但不阻塞——uvicorn 可能还在初始化数据库
        resolved = true;
        startupLog('HTTP 后端启动超时(30s)，继续启动窗口');
        console.warn('[ClipMind] HTTP 后端超时，继续启动...');
        resolve();
      }
    }, 30000);
  });
}

// ── 消息分发 ─────────────────────────────────

function handlePythonMessage(msg) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    // result 可能是字符串或对象，安全序列化
    const resultPreview = msg.result != null
      ? (typeof msg.result === 'string' ? msg.result : JSON.stringify(msg.result)).slice(0, 50)
      : '';
    console.log('[ClipMind] Python → 前端 转发:', msg.event || msg.id, resultPreview);
    mainWindow.webContents.send('python-message', msg);
  } else {
    // 窗口还没准备好 → 缓存
    startupLog(`缓冲 Python 消息: ${msg.event || msg.id} (缓冲区: ${eventBuffer.length + 1})`);
    console.log('[ClipMind] Python → 缓冲:', msg.event || msg.id);
    eventBuffer.push(msg);
  }
}

// ── IPC handlers ─────────────────────────────

// 路径白名单：只允许读取这些目录下的文件（防止渲染进程越权访问）
const ALLOWED_PREFIXES = [
  path.join(PROJECT_ROOT, 'data').toLowerCase(),
  path.join(PROJECT_ROOT, 'drafts').toLowerCase(),
  path.join(PROJECT_ROOT, 'output').toLowerCase(),
];
function _isPathAllowed(targetPath) {
  const normalized = path.resolve(targetPath).toLowerCase();
  return ALLOWED_PREFIXES.some(prefix => normalized.startsWith(prefix));
}

function setupIPC() {
  // ── 窗口控制 ──
  ipcMain.on('window-minimize', () => {
    mainWindow?.minimize();
  });
  ipcMain.on('window-maximize', () => {
    if (mainWindow?.isMaximized()) {
      mainWindow.unmaximize();
    } else {
      mainWindow?.maximize();
    }
  });
  ipcMain.on('window-close', () => {
    mainWindow?.close();
  });

  // 渲染进程发送消息给 Python
  ipcMain.on('rpc-send', (_, msg) => {
    startupLog(`前端 RPC: ${msg.method} (id=${msg.id})`);
    console.log('[ClipMind] 收到前端 RPC:', msg.method, msg.id);
    sendToPython(msg);
  });

  // 文件对话框（素材选择）
  ipcMain.handle('open-files', async (_, options) => {
    const result = await dialog.showOpenDialog(mainWindow, {
      properties: ['openFile', 'multiSelections'],
      filters: options?.filters || [
        { name: '视频文件', extensions: ['mp4', 'mov', 'avi', 'mkv', 'webm'] },
        { name: '所有文件', extensions: ['*'] },
      ],
      ...options,
    });
    return result.canceled ? [] : result.filePaths;
  });

  ipcMain.handle('save-file', async (_, options) => {
    const result = await dialog.showSaveDialog(mainWindow, {
      filters: options?.filters || [
        { name: 'MP4 视频', extensions: ['mp4'] },
      ],
      ...options,
    });
    return result.canceled ? null : result.filePath;
  });

  // 窗口标题
  ipcMain.on('window-set-title', (_, title) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.setTitle(title)
    }
  })

  // 桌面通知
  ipcMain.on('show-notification', (_, { title, body }) => {
    if (Notification.isSupported()) {
      new Notification({ title, body }).show()
    }
  })

  // ── 自动更新 ──
  ipcMain.handle('check-for-updates', async () => {
    try {
      // 临时允许下载（即使用户关了自动更新，手动检查也应该能下载）
      const oldAutoDownload = autoUpdater.autoDownload;
      autoUpdater.autoDownload = true;
      const result = await autoUpdater.checkForUpdates();
      autoUpdater.autoDownload = oldAutoDownload;
      const info = result?.updateInfo;
      return {
        version: info?.version || '',
        releaseDate: info?.releaseDate || '',
        releaseNotes: info?.releaseNotes || '',
      };
    } catch (err) {
      startupLog(`检查更新失败: ${err.message}`);
      return null;
    }
  });

  ipcMain.on('install-update', () => {
    autoUpdater.quitAndInstall();
  });

  ipcMain.handle('get-auto-update-setting', () => {
    return _readAutoUpdateSetting();
  });

  ipcMain.handle('set-auto-update-setting', (_, enabled) => {
    autoUpdater.autoDownload = !!enabled;
    try {
      const cfg = fs.existsSync(CONFIG_FILE)
        ? JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf8'))
        : {};
      if (!cfg.settings) cfg.settings = {};
      if (!cfg.settings.general) cfg.settings.general = {};
      cfg.settings.general.auto_update = !!enabled;
      fs.writeFileSync(CONFIG_FILE, JSON.stringify(cfg, null, 2), 'utf8');
      startupLog(`自动更新设置已${enabled ? '开启' : '关闭'}`);
      return true;
    } catch (e) {
      startupLog(`保存自动更新设置失败: ${e.message}`);
      return false;
    }
  });

  // ── 应用版本信息 ──
  ipcMain.handle('get-app-version', () => {
    return {
      version: pkg.version || '1.0.0',
      name: pkg.productName || pkg.name || 'ClipMind',
      electron: process.versions.electron,
      chrome: process.versions.chrome,
      node: process.versions.node,
      platform: process.platform,
      arch: process.arch,
    };
  });

  // 静默后台更新 — 只在下载完成时通知前端
  autoUpdater.on('checking-for-update', () => {
    startupLog('正在检查更新...');
  });

  autoUpdater.on('update-available', (info) => {
    startupLog(`发现新版本: ${info.version}，自动下载中...`);
  });

  autoUpdater.on('update-not-available', () => {
    startupLog('已是最新版本');
  });

  autoUpdater.on('update-downloaded', (info) => {
    startupLog(`更新已下载: v${info.version}，下次启动时安装`);
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('update-status', {
        status: 'downloaded',
        version: info.version,
      });
    }
  });

  autoUpdater.on('error', (err) => {
    startupLog(`更新错误: ${err.message}`);
  });

  // 窗口关闭后清空缓存
  ipcMain.on('window-ready', () => {
    startupLog(`收到 window-ready，冲刷缓冲事件: ${eventBuffer.length} 条`);
    console.log('[ClipMind] 收到 window-ready，冲刷缓冲事件:', eventBuffer.length, '条');
    // 把缓存的事件一次性发给渲染进程
    if (mainWindow && !mainWindow.isDestroyed()) {
      for (const msg of eventBuffer) {
        mainWindow.webContents.send('python-message', msg);
      }
      eventBuffer = [];
    }
  });

  // ── 文件系统 API ──

  // 读取本地文件为 Blob（用于预览试听）
  ipcMain.handle('fs-read-file-blob', async (_, filePath) => {
    try {
      // 路径安全检查
      if (!_isPathAllowed(filePath)) {
        startupLog(`路径被拒绝: ${filePath}`);
        return null;
      }
      const data = fs.readFileSync(filePath);
      const ext = path.extname(filePath).toLowerCase();
      let mime = 'application/octet-stream';
      if (ext === '.mp3') mime = 'audio/mpeg';
      else if (ext === '.wav') mime = 'audio/wav';
      else if (ext === '.m4a') mime = 'audio/mp4';
      else if (ext === '.flac') mime = 'audio/flac';
      else if (ext === '.ogg') mime = 'audio/ogg';
      else if (ext === '.mp4') mime = 'video/mp4';
      else if (ext === '.mov') mime = 'video/quicktime';
      else if (ext === '.webm') mime = 'video/webm';
      // 返回 { data: Base64, mime }
      return { data: data.toString('base64'), mime };
    } catch (err) {
      console.error(`[ClipMind] 读取文件失败: ${filePath}`, err.message);
      return null;
    }
  });

  // 读取目录下的文件名列表
  ipcMain.handle('fs-read-directory', async (_, dirPath) => {
    try {
      const files = fs.readdirSync(dirPath);
      return files.filter(f => !f.startsWith('.')).map(f => {
        const fullPath = path.join(dirPath, f);
        const stat = fs.statSync(fullPath);
        return {
          name: f,
          path: fullPath,
          isDirectory: stat.isDirectory(),
          size: stat.size,
          mtime: stat.mtime.toISOString(),
        };
      });
    } catch (err) {
      console.error(`[ClipMind] 读取目录失败: ${dirPath}`, err.message);
      return [];
    }
  });

  // 删除文件（清理临时文件用）
  ipcMain.handle('fs-delete-file', async (_, filePath) => {
    try {
      fs.unlinkSync(filePath);
      return true;
    } catch (err) {
      console.error(`[ClipMind] 删除文件失败: ${filePath}`, err.message);
      return false;
    }
  });

  // 写入文件（导出项目报告等用）
  ipcMain.handle('fs-write-file', async (_, filePath, content) => {
    try {
      fs.writeFileSync(filePath, content, 'utf8');
      return true;
    } catch (err) {
      startupLog(`写入文件失败: ${filePath} — ${err.message}`);
      return false;
    }
  });

  // ffmpeg 提取视频片段 → 临时 mp4（预览捕获前端用）
  ipcMain.handle('extract-video-segment', async (_, { videoPath, startTime, endTime }) => {
    // 路径安全检查
    if (!_isPathAllowed(videoPath)) {
      startupLog(`extract-video-segment 路径被拒绝: ${videoPath}`);
      throw new Error('不允许读取该路径下的文件');
    }

    const tmpFile = path.join(os.tmpdir(), `clipmind_prv_${Date.now()}.mp4`);
    const duration = endTime - startTime;

    return new Promise((resolve, reject) => {
      const ffmpeg = spawn('ffmpeg', [
        '-y',
        '-ss', String(startTime),
        '-i', videoPath,
        '-t', String(duration),
        '-c:v', 'libx264', '-crf', '32', '-preset', 'ultrafast',
        '-vf', 'scale=-2:480',
        '-an',
        tmpFile,
      ], { windowsHide: true });

      let stderr = '';
      ffmpeg.stderr.on('data', d => { stderr += d.toString(); });
      ffmpeg.on('close', code => {
        if (code === 0 && fs.existsSync(tmpFile) && fs.statSync(tmpFile).size > 0) {
          resolve(tmpFile);
        } else {
          reject(new Error(`ffmpeg 提取失败 (code=${code}): ${stderr.slice(0, 200)}`));
        }
      });
    });
  });
}

// ── 窗口创建 ─────────────────────────────────

function createWindow() {
  const winState = loadWindowState();
  try {
    mainWindow = new BrowserWindow({
      width: winState?.width || 1400,
      height: winState?.height || 900,
      x: winState?.x,
      y: winState?.y,
      minWidth: 1100,
      minHeight: 700,
      title: 'ClipMind',
      backgroundColor: '#0D0D0D',
      show: false,
      frame: false,     // ← 去掉原生白条标题栏
      icon: path.join(PROJECT_ROOT, 'build', 'icon.ico'),
      webPreferences: {
        preload: path.join(__dirname, 'preload.js'),
        contextIsolation: true,
        nodeIntegration: false,
      },
    });
  } catch (err) {
    startupLog(`ERROR: BrowserWindow 创建失败 — ${err.message}`);
    throw err; // 重新抛出，让上层捕获
  }

  // 恢复最大化状态
  if (winState?.isMaximized) {
    mainWindow.maximize();
  }

  // 开发模式：加载 Vite dev server
  if (isDev) {
    mainWindow.loadURL('http://localhost:5173').catch(err => {
      startupLog(`加载前端失败 (dev): ${err.message}`);
      // 尝试加载 dist 目录的 fallback
      const fallbackPath = path.join(__dirname, 'dist', 'index.html');
      if (fs.existsSync(fallbackPath)) {
        mainWindow.loadFile(fallbackPath).catch(e => {
          startupLog(`加载 fallback 也失败: ${e.message}`);
        });
      }
    });
    mainWindow.webContents.openDevTools({ mode: 'detach' });
  } else {
    const indexPath = path.join(__dirname, 'dist', 'index.html');
    mainWindow.loadFile(indexPath).catch(err => {
      startupLog(`加载前端失败 (prod): ${err.message}`);
    });
  }

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  // 最大化/还原状态通知给渲染进程
  mainWindow.on('maximize', () => {
    mainWindow.webContents.send('window-maximized');
  });
  mainWindow.on('unmaximize', () => {
    mainWindow.webContents.send('window-unmaximized');
  });

  // 把渲染进程 console 转发到主进程终端（调试用）
  mainWindow.webContents.on('console-message', (_, level, message) => {
    const prefix = level === 3 ? '[前端错误]' : '[前端]';
    console.log(`${prefix} ${message}`);
  });

  // 阻止拖入视频文件时窗口导航到文件路径（否则 Electron 会内置播放视频）
  // 仅阻止 file:// 导航，不影响 Vite dev server (localhost) 和 vue-router
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (url.startsWith('file://')) {
      event.preventDefault();
    }
  });

  // 窗口状态持久化
  mainWindow.on('close', () => {
    saveWindowState();
  });
  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// ── 清理 ────────────────────────────────────

function cleanup() {
  _isShuttingDown = true;
  if (pythonProcess && !pythonProcess.killed) {
    sendToPython({ id: 999, method: 'shutdown', params: {} });
    setTimeout(() => {
      if (pythonProcess && !pythonProcess.killed) {
        pythonProcess.kill('SIGTERM');
      }
    }, 3000);
  }
  if (backendProcess && !backendProcess.killed) {
    backendProcess.kill('SIGTERM');
  }
}

// ── Windows：自动创建开始菜单快捷方式（解决开发模式下任务栏图标问题）──
function ensureWindowsShortcut() {
  if (process.platform !== 'win32') return;
  // 打包后的 exe 自带图标，不需要这个
  if (app.isPackaged) return;

  const shortcutDir = path.join(app.getPath('appData'), 'Microsoft\\Windows\\Start Menu\\Programs');
  const shortcutPath = path.join(shortcutDir, 'ClipMind.lnk');
  const iconPath = path.join(PROJECT_ROOT, 'build', 'icon.ico');

  if (!fs.existsSync(iconPath)) {
    console.warn('[ClipMind] icon.ico 不存在，跳过快捷方式创建');
    return;
  }

  // 已存在且指向正确路径则跳过
  try {
    if (fs.existsSync(shortcutPath)) {
      const stat = fs.statSync(shortcutPath);
      // 24小时内的快捷方式不重建
      if (Date.now() - stat.mtimeMs < 86400000) return;
    }
  } catch { /* 无视 */ }

  // 用 PowerShell 创建快捷方式
  const psScript = [
    '$ws = New-Object -ComObject WScript.Shell',
    `$sc = $ws.CreateShortcut('${shortcutPath.replace(/'/g, "''")}')`,
    `$sc.TargetPath = '${process.execPath.replace(/'/g, "''")}'`,
    `$sc.Arguments = '.\\'`,
    `$sc.WorkingDirectory = '${PROJECT_ROOT.replace(/'/g, "''")}'`,
    `$sc.IconLocation = '${iconPath.replace(/'/g, "''")},0'`,
    '$sc.Save()',
    `Write-Output 'OK'`,
  ].join('\n');

  const tmpFile = path.join(app.getPath('temp'), 'clipmind_shortcut.ps1');
  try {
    fs.writeFileSync(tmpFile, psScript, 'utf8');
    const { execSync } = require('child_process');
    execSync(`powershell -NoProfile -ExecutionPolicy Bypass -File "${tmpFile}"`, {
      windowsHide: true, timeout: 5000,
    });
    console.log('[ClipMind] 开始菜单快捷方式已更新');
  } catch (err) {
    console.warn('[ClipMind] 创建快捷方式失败:', err.message);
  } finally {
    try { fs.unlinkSync(tmpFile); } catch {}
  }
}

// ── App 生命周期 ───────────────────────────

app.whenReady().then(async () => {
  startupLog('app.whenReady');
  setupIPC();

  // ── Windows 快捷方式（必须优先于窗口创建，确保任务栏图标正确）──
  ensureWindowsShortcut();

  // ── Windows 右键菜单（JumpList）— 必须在窗口创建前设置 ──
  if (process.platform === 'win32') {
    try {
      const icoPath = path.join(PROJECT_ROOT, 'build', 'icon.ico');
      if (fs.existsSync(icoPath)) {
        app.setUserTasks([
          {
            program: process.execPath,
            arguments: '',
            iconPath: icoPath,
            iconIndex: 0,
            title: 'ClipMind',
            description: '启动 ClipMind',
          },
        ]);
      }
    } catch (e) {
      console.warn('[ClipMind] 设置 JumpList 失败:', e.message);
    }
  }

  try {
    startupLog('正在启动 Python RPC...');
    await startPythonProcess();
    startupLog('Python RPC 就绪');
    console.log('[ClipMind] Python RPC 就绪');
  } catch (err) {
    startupLog(`ERROR: Python RPC 启动失败 — ${err.message}`);
    console.error('[ClipMind] Python RPC 启动失败:', err.message);
    eventBuffer.push({
      event: 'backend_error',
      message: err.message,
    });
  }

  // 同时启动 HTTP 后端（不阻塞窗口创建）
  startBackendProcess().then(() => {
    startupLog('HTTP 后端就绪');
    console.log('[ClipMind] HTTP 后端就绪');
  }).catch(err => {
    startupLog(`ERROR: HTTP 后端启动失败 — ${err.message}`);
    console.error('[ClipMind] HTTP 后端启动失败:', err.message);
    eventBuffer.push({
      event: 'http_backend_error',
      message: err.message,
    });
  });

  createWindow();
  startupLog('窗口已创建');

  // 启动后 5 秒自动检查更新（不阻塞启动流程）
  setTimeout(() => {
    try {
      autoUpdater.checkForUpdates();
    } catch (e) {
      startupLog(`检查更新异常: ${e.message}`);
    }
  }, 5000);
});

app.on('window-all-closed', () => {
  cleanup();
  app.quit();
});

// SIGTERM/SIGINT — 跨平台进程终止信号
// 注意：Windows 上 SIGTERM 不会触发，但保留以覆盖非 Windows 平台
process.on('SIGTERM', cleanup);
process.on('SIGINT', cleanup);

// before-quit — Electron 退出前钩子（Windows 关闭主窗口时触发，弥补 SIGTERM 缺失）
app.on('before-quit', cleanup);

app.on('activate', () => {
  if (mainWindow === null) {
    createWindow();
  }
});
