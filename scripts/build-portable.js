/**
 * ClipMind 便携版打包脚本
 * ============================
 * 流程:
 *   1. vite build        → 构建 Vue 前端到 dist/
 *   2. electron-builder  → 打包为 Windows 便携版 exe
 *
 * 用法:
 *   npm run build:package
 *   node scripts/build-portable.js
 *
 * 输出:
 *   dist-electron/ClipMind-1.0.0-setup.exe   (NSIS 安装包)
 *   dist-electron/ClipMind-1.0.0-portable.exe (便携版)
 */

const { execSync } = require('child_process')
const path = require('path')
const fs = require('fs')

const PROJECT_ROOT = path.resolve(__dirname, '..')
const START_TIME = Date.now()

function log(msg) {
  const ts = new Date().toISOString().slice(11, 19)
  console.log(`[${ts}] ${msg}`)
}

function run(cmd, opts = {}) {
  log(`运行: ${cmd}`)
  execSync(cmd, {
    cwd: PROJECT_ROOT,
    stdio: 'inherit',
    env: { ...process.env, ...opts.env },
    ...opts,
  })
}

// ── 前置检查 ──
function preflight() {
  log('--- 前置检查 ---')

  // 检查 dist/ 不含残留旧构建
  const distDir = path.join(PROJECT_ROOT, 'dist')
  if (fs.existsSync(distDir)) {
    log('dist/ 目录存在,将在构建时覆盖')
  }

  // 确保关键文件存在
  const required = [
    'index.html',
    'vite.config.ts',
    'main.js',
    'preload.js',
    'package.json',
    'src/main.ts',
    'server/main.py',
  ]
  for (const f of required) {
    if (!fs.existsSync(path.join(PROJECT_ROOT, f))) {
      console.error(`[ERROR] 缺少必需文件: ${f}`)
      process.exit(1)
    }
  }

  // 检查 node_modules
  if (!fs.existsSync(path.join(PROJECT_ROOT, 'node_modules'))) {
    log('node_modules 不存在,运行 npm install...')
    run('npm install')
  }

  log('前置检查通过')
}

// ── 第一步: 构建 Vue 前端 ──
function buildRenderer() {
  log('--- 构建前端 (vite build) ---')
  try {
    run('npx vite build')
  } catch (e) {
    console.error('[ERROR] Vite 构建失败:', e.message)
    process.exit(1)
  }
  const distSize = fs.statSync(path.join(PROJECT_ROOT, 'dist'))
    ? getDirSize(path.join(PROJECT_ROOT, 'dist'))
    : 0
  log(`前端构建完成, dist/ 大小: ${(distSize / 1024 / 1024).toFixed(1)} MB`)
}

function getDirSize(dirPath) {
  let total = 0
  try {
    const entries = fs.readdirSync(dirPath, { withFileTypes: true })
    for (const entry of entries) {
      const fullPath = path.join(dirPath, entry.name)
      if (entry.isFile()) total += fs.statSync(fullPath).size
      else if (entry.isDirectory()) total += getDirSize(fullPath)
    }
  } catch {}
  return total
}

// ── 第二步: electron-builder 打包 ──
function packageApp() {
  log('--- 打包应用 (electron-builder) ---')

  // 检查 electron-builder 是否可用
  try {
    require.resolve('electron-builder')
  } catch {
    log('electron-builder 未安装,跳过打包步骤')
    log('请运行: npm install --save-dev electron-builder')
    return
  }

  // 可选参数: --win portable 只打便携版,省时间
  const extraArgs = process.argv.includes('--installer') ? '' : '--win portable'

  try {
    run(`npx electron-builder ${extraArgs}`, { timeout: 600000 })
  } catch (e) {
    console.error('[ERROR] electron-builder 打包失败:', e.message)
    process.exit(1)
  }

  const outDir = path.join(PROJECT_ROOT, 'dist-electron')
  if (fs.existsSync(outDir)) {
    log('打包完成,输出文件:')
    const files = fs.readdirSync(outDir)
    for (const f of files) {
      const fp = path.join(outDir, f)
      const size = fs.statSync(fp).size
      log(`  ${f}  (${(size / 1024 / 1024).toFixed(1)} MB)`)
    }
  }
}

// ── 主流程 ──
function main() {
  const args = process.argv.slice(2)
  const skipBuild = args.includes('--skip-build')
  const skipPackage = args.includes('--skip-package')

  log('=== ClipMind 便携版打包 ===')
  log(`工作目录: ${PROJECT_ROOT}`)
  if (skipBuild) log('[跳过] 前端构建')
  if (skipPackage) log('[跳过] 应用打包')

  preflight()

  if (!skipBuild) buildRenderer()
  else log('跳过 vite build')

  if (!skipPackage) packageApp()
  else log('跳过 electron-builder')

  const elapsed = ((Date.now() - START_TIME) / 1000).toFixed(1)
  log(`=== 完成 (${elapsed}秒) ===`)
}

main()
