/**
 * ClipMind RPC Client（多项目并行版）
 * ======================================
 * 封装前端 ↔ Python Director 通信。
 *
 * 架构:
 *   渲染进程 (Vue) → window.cherryclip.rpc.send()  → IPC
 *                  → window.cherryclip.rpc.onMessage() ← IPC
 *
 * 多项目:
 *   所有项目级调用需要 project_id，事件携带 project_id 用于路由。
 *
 * 事件类型（Python → 前端，均含 project_id）:
 *   ready             后端就绪（无 project_id）
 *   project_created   项目已创建
 *   project_deleted   项目已删除
 *   ai_message        AI 文本消息
 *   tool_start        工具开始执行
 *   tool_end          工具执行完成
 *   ask_user          AI 等你回答
 *   progress          进度更新
 *   project_complete  项目完成
 *   error             出错
 *   plan_ready        方案已生成
 */

// ── Types ──

export interface RpcEvent {
  event: string
  project_id?: string
  [key: string]: any
}

export interface AiMessageEvent extends RpcEvent {
  event: 'ai_message'
  content: string
}

export interface ToolStartEvent extends RpcEvent {
  event: 'tool_start'
  name: string
}

export interface ToolEndEvent extends RpcEvent {
  event: 'tool_end'
  name: string
  result?: string
  elapsed?: number
}

export interface AskUserEvent extends RpcEvent {
  event: 'ask_user'
  question: string
  options?: string
}

export interface ProgressEvent extends RpcEvent {
  event: 'progress'
  status: string
}

export interface ProjectCompleteEvent extends RpcEvent {
  event: 'project_complete'
  draft_id: string
  turns: number
}

export interface ErrorEvent extends RpcEvent {
  event: 'error'
  message: string
}

export type EventHandler = (event: RpcEvent) => void

// ── RPC Client ──

let _nextId = 1
let _unsubscribe: (() => void) | null = null
let _handlers: EventHandler[] = []
const _pendingRequests = new Map<number, (result: any) => void>()

export function initRpc() {
  if (!window.cherryclip?.rpc) {
    console.warn('[RPC] window.cherryclip 不可用（不在 Electron 中？）')
    return
  }
  _unsubscribe?.()
  _unsubscribe = window.cherryclip.rpc.onMessage((msg: any) => {
    // RPC 响应（有 id，不是 event）→ 分发给等待者
    if (msg.id != null && !msg.event) {
      const resolve = _pendingRequests.get(msg.id)
      if (resolve) {
        _pendingRequests.delete(msg.id)
        if (msg.error) {
          // 后端返回了错误 → 用 resolve 但不抛出（callAsync 通过结果判断）
          resolve({ _error: true, message: msg.error.message || '未知错误' })
        } else {
          resolve(msg.result)
        }
      }
      return
    }

    // 事件 → 分发给所有 handler
    // 事件 → 分发给所有 handler
    console.log('[RPC] 收到事件:', msg.event, msg.project_id ? `(${msg.project_id})` : '')
    for (const h of _handlers) {
      try { h(msg) } catch {}
    }
  })
  console.log('[RPC] 已连接，handlers:', _handlers.length)
}

export function destroyRpc() {
  _unsubscribe?.()
  _unsubscribe = null
  _handlers = []
}

export function onEvent(handler: EventHandler): () => void {
  _handlers.push(handler)
  return () => {
    const idx = _handlers.indexOf(handler)
    if (idx >= 0) _handlers.splice(idx, 1)
  }
}

// ── 请求方法 ──

function call(method: string, params: Record<string, any> = {}): number {
  const id = _nextId++
  window.cherryclip?.rpc?.send(method, params, id)
  return id
}

/** 异步 RPC 调用（等待 Python 返回结果） */
function callAsync(method: string, params: Record<string, any> = {}): Promise<any> {
  return new Promise((resolve, reject) => {
    const id = call(method, params)
    _pendingRequests.set(id, resolve)
    setTimeout(() => {
      if (_pendingRequests.has(id)) {
        _pendingRequests.delete(id)
        reject(new Error(`RPC ${method} 超时 (30s)`))
      }
    }, 30000)
  })
}

// ── 项目管理 ──

/** 创建新项目，返回 { project_id } */
export function createProject() {
  return callAsync('create_project')
}

/** 删除项目（移入回收站） */
export function deleteProject(projectId: string) {
  return callAsync('delete_project', { project_id: projectId })
}

/** 从回收站恢复项目 */
export function restoreProject(projectId: string) {
  return callAsync('restore_project', { project_id: projectId })
}

/** 永久删除项目 */
export function permanentlyDeleteProject(projectId: string) {
  return callAsync('permanently_delete_project', { project_id: projectId })
}

/** 列出回收站 */
export function listTrash() {
  return callAsync('list_trash')
}

/** 根据素材文件名建议项目名 */
export function suggestProjectName(projectId: string) {
  return callAsync('suggest_project_name', { project_id: projectId })
}

/** 重新扫描孤立项目 */
export function rescanProjects() {
  return callAsync('rescan_projects')
}

/** 列出所有项目 */
export function listProjects() {
  return callAsync('list_projects')
}

// ── 全局方法 ──

/** 设置 API 配置（支持代理模式） */
export function configure(baseUrl: string, apiKey: string, model: string, backendUrl?: string) {
  return call('configure', { base_url: baseUrl, api_key: apiKey, model, backend_url: backendUrl || '' })
}

// ── 项目级方法（需要 project_id）──

/** 启动 Director 项目 */
export function startProject(projectId: string, paths: string[], task: string) {
  return call('start_project', { project_id: projectId, paths, task })
}

/** 发送反馈给 Director */
export function sendMessage(projectId: string, text: string) {
  return call('send_message', { project_id: projectId, text })
}

/** 回答 AI 的 ask_user 问题 */
export function respondAsk(projectId: string, text: string) {
  return call('respond_ask', { project_id: projectId, text })
}

/** 开始 Pipeline 逐阶段执行（用户点"开始"按钮后调用） */
export function startPipeline(projectId: string) {
  return call('start_pipeline', { project_id: projectId })
}

/** 取消当前项目 */
export function cancelProject(projectId: string) {
  return call('cancel', { project_id: projectId })
}

/** 确认方案，开始剪辑 */
export function confirmPlan(projectId: string) {
  return call('confirm_plan', { project_id: projectId })
}

/** 纯聊天（无素材时） */
export function chat(projectId: string, text: string) {
  return call('chat', { project_id: projectId, text })
}

/** 回复预览视频片段 */
export function respondPreviewClip(projectId: string, data: string) {
  return call('respond_preview_clip', { project_id: projectId, data })
}

// ── 无需 project_id 的方法 ──

/** 获取音频波形数据 */
export function getWaveform(audioPath: string, numBars: number = 200) {
  return callAsync('get_waveform', { audio_path: audioPath, num_bars: numBars })
}

/** 获取草稿完整信息 */
export function getDraftInfo(draftId: string) {
  return callAsync('get_draft_info', { draft_id: draftId })
}

/** 列出所有草稿 */
export function listDrafts() {
  return callAsync('list_drafts')
}

/** 重排主轨道片段顺序 */
export function reorderClips(draftId: string, clipIds: number[]) {
  return callAsync('reorder_clips', { draft_id: draftId, clip_ids: clipIds })
}

/** 删除草稿 */
export function deleteDraft(draftId: string) {
  return callAsync('delete_draft', { draft_id: draftId })
}

// ── 持久化 ──

/** 保存聊天记录 */
export function saveChatMessages(projectId: string, messages: any[]) {
  return callAsync('save_chat_messages', { project_id: projectId, messages })
}

/** 加载聊天记录 */
export function loadChatMessages(projectId: string) {
  return callAsync('load_chat_messages', { project_id: projectId })
}

/** 更新项目元数据 */
export function updateProject(projectId: string, updates: { name?: string; draft_id?: string; materials?: any[] }) {
  return callAsync('update_project', { project_id: projectId, ...updates })
}

/** 手动导出草稿（不经过 agent loop） */
export function exportDraft(draftId: string, projectId?: string, preset?: string) {
  return callAsync('export_draft', { draft_id: draftId, project_id: projectId || '', preset: preset || '' })
}

/** 保存用户反馈 */
export function saveFeedback(projectId: string, draftId: string, rating: number, comment?: string) {
  return callAsync('save_feedback', { project_id: projectId, draft_id: draftId, rating, comment: comment || '' })
}

/** 获取 API 配置（key 脱敏） */
export function getApiConfig() {
  return callAsync('get_api_config', {})
}

/** 获取用户设置 */
export function getSettings() {
  return callAsync('get_settings', {})
}

/** 保存用户设置 */
export function saveSettings(settings: Record<string, unknown>) {
  return callAsync('save_settings', { settings })
}

/** 导出项目报告（Markdown） */
export function exportProjectReport(draftId: string) {
  return callAsync('export_project_report', { draft_id: draftId })
}

// ── 工具函数 ──

/** 判断是否在 Electron 环境中 */
export function isElectron(): boolean {
  return !!window.cherryclip?.rpc
}
