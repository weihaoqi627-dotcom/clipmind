/// <reference types="vite/client" />

declare global {
  interface Window {
    cherryclip?: CherryclipAPI
  }
}

interface CherryclipAPI {
  rpc: {
    send(method: string, params?: Record<string, unknown>, id?: number): number
    onMessage(callback: (msg: Record<string, unknown>) => void): () => void
  }
  dialogs: {
    openFiles(options?: Record<string, unknown>): Promise<string[]>
    saveFile(options?: Record<string, unknown>): Promise<string | null>
  }
  window: {
    close(): void
    minimize(): void
    maximize(): void
    setTitle(title: string): void
    onMaximizeChange(callback: (isMaximized: boolean) => void): void
  }
  platform: string
  ready(): void
  notifications: {
    show(title: string, body: string): void
  }
  /** 文件系统 API（本地文件读取等） */
  fs: {
    /** 读取本地文件，返回 Base64 编码的数据 */
    readFileAsBlob(filePath: string): Promise<{ data: string; mime: string } | null>
    /** 读取目录下的文件列表 */
    readDirectory(dirPath: string): Promise<Array<{ name: string; path: string; isDirectory: boolean; size: number; mtime: string }>>
  }
  /** 自动更新 */
  updater: {
    checkForUpdates(): Promise<{ version: string; releaseDate: string; releaseNotes: string } | null>
    installUpdate(): void
    onStatus(callback: (data: { status: string; version?: string }) => void): void
    getAutoUpdateSetting(): Promise<boolean>
    setAutoUpdateSetting(enabled: boolean): Promise<boolean>
  }
}

export {}
