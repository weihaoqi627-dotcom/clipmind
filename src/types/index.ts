/** 素材类型 */
export interface Material {
  name: string
  type: 'video' | 'audio'
  path: string
}

/** RPC 事件 */
export interface RpcEvent {
  event: string
  project_id?: string
  [key: string]: any
}

/** RPC 事件的典型字段（扩展用） */
export interface RpcEventWithData extends RpcEvent {
  content?: string
  message?: string
  stage?: string
  label?: string
  workflow?: string
  output_path?: string
  error?: string
  video_path?: string
  start_time?: number
  end_time?: number
  status?: string
  draft_id?: string
  active_stages?: string[]
}

/** App.vue provide('app') 的注入类型 */
export interface AppInjection {
  projects: any
  activeProjectId: any
  connected: any
  createProject: () => Promise<any>
  switchProject: (id: string) => void
  deleteProject: (id: string) => void
  openDraft: (draftId: string) => void
}

/** App.vue provide('project') 的注入类型 */
export interface ProjectInjection {
  state: any
  sendMessage: (text: string) => void
  startProject: (text: string) => void
  startPipeline: () => void
  respondAsk: (answer: string) => void
  cancelProject: () => void
  confirmPlan: () => void
  rejectPlan: (reason: string) => void
}
