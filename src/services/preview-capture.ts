/**
 * 浏览器 Canvas 帧捕获 — 用于 check_edit 预览管道
 * ============================================================
 *
 * 流程:
 *   1. ffmpeg 从源视频提取 [start, end] → 临时低清 mp4
 *   2. 浏览器加载 mp4 → Canvas 逐帧绘制
 *   3. canvas.captureStream + MediaRecorder → WebM
 *   4. FileReader → base64 → 送回 Python
 *   5. 清理临时文件
 *
 * Python ↔ Electron 完整链路:
 *   Python emit("request_preview_clip", {video_path, start, end})
 *     → App.vue handleRpcEvent
 *       → capturePreviewClip(video_path, start, end)
 *         → rpc.respondPreviewClip(base64)
 *           → Python respond_preview_clip(data)
 *             → check_edit 收到视频 bytes → VL 分析
 */

const CAPTURE_FPS = 10       // 预览 10fps 够用
const CANVAS_MAX_W = 480      // 预览宽度上限

function base64ToBlob(base64: string, mime: string): Blob {
  // 用 fetch(data: URL) 解析 base64 → Blob，省去手动二进制拼接
  const dataUrl = `data:${mime};base64,${base64}`
  // 注意：fetch data URL 在浏览器中可同步创建，但 toBlob 需要异步
  const byteChars = atob(base64)
  const byteNums = new Array(byteChars.length)
  for (let i = 0; i < byteChars.length; i++) {
    byteNums[i] = byteChars.charCodeAt(i)
  }
  return new Blob([new Uint8Array(byteNums)], { type: mime })
}

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = reader.result as string
      // "data:video/webm;base64,xxxxx" → 只要 "xxxxx"
      resolve(result.split(',')[1] || '')
    }
    reader.onerror = () => reject(new Error('FileReader 读取 Blob 失败'))
    reader.readAsDataURL(blob)
  })
}

/**
 * 逐帧 seek + Canvas 绘制 + MediaRecorder 录制 → WebM blob
 */
async function recordFramesToWebM(
  video: HTMLVideoElement,
  canvas: HTMLCanvasElement,
  duration: number
): Promise<Blob> {
  const ctx = canvas.getContext('2d')!
  const stream = canvas.captureStream(CAPTURE_FPS)
  const chunks: Blob[] = []

  // 检测可用的 mime type
  const mimeType = MediaRecorder.isTypeSupported('video/webm;codecs=vp8')
    ? 'video/webm;codecs=vp8'
    : 'video/webm'

  const recorder = new MediaRecorder(stream, { mimeType })
  recorder.ondataavailable = (e) => {
    if (e.data.size > 0) chunks.push(e.data)
  }

  const done = new Promise<Blob>((resolve) => {
    recorder.onstop = () => {
      resolve(new Blob(chunks, { type: 'video/webm' }))
    }
  })

  recorder.start()

  // 逐帧 seek —— 比 play() + requestAnimationFrame 更可靠
  const step = 1 / CAPTURE_FPS
  for (let t = 0; t < duration; t += step) {
    video.currentTime = t
    await new Promise<void>((resolve) => {
      video.onseeked = () => {
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
        resolve()
      }
    })
  }

  // 最后一帧
  video.currentTime = Math.max(0, duration - 0.05)
  await new Promise<void>((resolve) => {
    video.onseeked = () => {
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
      resolve()
    }
  })

  // 给 MediaRecorder 一点时间收尾
  await new Promise(r => setTimeout(r, 100))
  recorder.stop()

  return done
}

/**
 * 捕获视频预览片段
 * @param videoPath - 源视频的绝对路径
 * @param startTime - 开始时间（秒）
 * @param endTime   - 结束时间（秒）
 * @returns { base64: base64 编码的 WebM, webmBlob: 原始 Blob }
 */
export async function capturePreviewClip(
  videoPath: string,
  startTime: number,
  endTime: number
): Promise<{ base64: string; webmBlob: Blob }> {
  const cherryclip = (window as any).cherryclip
  if (!cherryclip?.fs?.extractVideoSegment) {
    throw new Error('preview-capture 需要在 Electron 环境中运行')
  }

  // ── 1. ffmpeg 提取片段 → 临时低清 mp4 ──
  let tmpPath: string
  try {
    tmpPath = await cherryclip.fs.extractVideoSegment(videoPath, startTime, endTime)
  } catch (err: any) {
    throw new Error(`ffmpeg 提取失败: ${err.message}`)
  }

  try {
    // ── 2. 读取临时文件 → Blob → video 元素 ──
    const blobData = await cherryclip.fs.readFileAsBlob(tmpPath)
    if (!blobData?.data) throw new Error('读取临时视频文件失败')

    const blob = base64ToBlob(blobData.data, blobData.mime || 'video/mp4')
    const url = URL.createObjectURL(blob)

    const video = document.createElement('video')
    video.src = url
    video.muted = true
    video.preload = 'auto'
    video.crossOrigin = 'anonymous'

    await new Promise<void>((resolve, reject) => {
      video.onloadedmetadata = () => resolve()
      video.onerror = () => reject(new Error('video 元素加载失败'))
    })

    // ── 3. 创建 Canvas（等比缩放） ──
    const scale = Math.min(1, CANVAS_MAX_W / (video.videoWidth || 640))
    const canvas = document.createElement('canvas')
    canvas.width = Math.round((video.videoWidth || 640) * scale)
    canvas.height = Math.round((video.videoHeight || 360) * scale)

    // ── 4. 逐帧录制 → WebM ──
    const duration = video.duration
    if (duration <= 0) throw new Error('视频时长为 0')

    const webmBlob = await recordFramesToWebM(video, canvas, duration)

    // ── 5. WebM → base64 ──
    const base64 = await blobToBase64(webmBlob)

    // ── 6. 清理（保留 webmBlob，调用方需要用它建 blob URL） ──
    URL.revokeObjectURL(url)
    video.remove()
    canvas.remove()

    return { base64, webmBlob }
  } finally {
    // 删除临时文件（无论成功失败）
    try {
      await cherryclip.fs.deleteFile(tmpPath)
    } catch {
      // 删不掉就算了，临时目录会自动清
    }
  }
}
