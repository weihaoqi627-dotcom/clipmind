<template>
  <div class="preview-page">
    <!-- 加载遮罩 -->
    <Transition name="fade">
      <div v-if="draftLoading" class="preview-loading">
        <span class="spinner"></span>
        <span>加载草稿数据...</span>
      </div>
    </Transition>

    <!-- 顶部栏 -->
    <div class="preview-header">
      <button class="btn-back" @click="goBack">
        <SvgIcon name="back" size="14" /> 返回聊天
      </button>
      <div class="draft-selector" v-if="draftsList.length > 0">
        <SvgIcon name="file" size="12" />
        <select v-model="selectedDraftId" @change="onDraftSelect">
          <option value="">-- 选择草稿 --</option>
          <option v-for="d in draftsList" :key="d.draft_id" :value="d.draft_id">
            {{ d.name || d.draft_id }}
          </option>
        </select>
      </div>
      <span class="preview-title">预览 / 检查</span>
      <button class="btn-refresh" @click="loadDraftTracks" title="刷新草稿轨道数据">
        ↻ 刷新
      </button>
      <button class="btn-export-report" @click="exportReport" title="导出项目报告 (Markdown)" :disabled="!draftId || exportingReport">
        {{ exportingReport ? '导出中...' : '📄 导出报告' }}
      </button>
      <div class="header-spacer"></div>
    </div>

    <!-- 主体 -->
    <div class="preview-body">
      <!-- 视频播放器 -->
      <div class="player-section">
        <div class="player-container" ref="playerRef">
          <video
            ref="videoRef"
            v-if="currentVideo"
            :src="currentVideo"
            class="video-player"
            controls
            @timeupdate="onTimeUpdate"
            @loadedmetadata="onLoaded"
          ></video>
          <!-- 导出按钮：悬浮在播放器右上角 -->
          <button
            v-if="currentVideo"
            class="btn-export-video"
            :class="{ active: !!canExport, exporting: isExporting }"
            :disabled="!canExport"
            :title="exportTooltip"
            @click="onExportVideo"
          >
            <SvgIcon name="export" :size="18" />
          </button>
          <div v-else class="player-placeholder">
            <div class="placeholder-ring">
              <SvgIcon name="logo" size="40" color="#54545E" />
            </div>
            <p class="placeholder-text">暂无预览内容</p>
            <p class="placeholder-hint">
              Director 完成剪辑后，预览结果将在此显示
            </p>
          </div>
        </div>

        <!-- 播放控制信息 -->
        <div v-if="currentVideo" class="player-info">
          <span class="time-display">{{ formatTime(currentTime) }} / {{ formatTime(duration) }}</span>
          <span class="clip-info" v-if="clipRange">
            片段: {{ formatTime(clipRange[0]) }} - {{ formatTime(clipRange[1]) }}
            ({{ formatDuration(clipRange[1] - clipRange[0]) }})
          </span>
        </div>
      </div>

      <!-- 轨道 / 时间线 -->
      <div class="timeline-section">
        <div class="section-label-row">
          <span class="section-label">时间线</span>
          <div class="zoom-controls">
            <button class="zoom-btn" @click="zoomOut" :disabled="zoomLevel <= ZOOM_MIN" title="缩小">−</button>
            <span class="zoom-label">{{ Math.round(zoomLevel * 100) }}%</span>
            <button class="zoom-btn" @click="zoomIn" :disabled="zoomLevel >= ZOOM_MAX" title="放大">+</button>
          </div>
        </div>
        <div class="timeline-container" ref="timelineRef" @wheel="onWheel">
          <div class="timeline-ruler" @dblclick.prevent="addMarker($event)">
            <div
              v-for="tick in timeTicks"
              :key="tick"
              class="tick"
              :style="{ left: tickToPx(tick) + 'px' }"
            >
              <span class="tick-label">{{ formatTime(tick) }}</span>
            </div>
            <div
              v-for="(m, i) in markers"
              :key="i"
              class="marker"
              :style="{ left: tickToPx(m.time) + 'px' }"
              :title="'标记: ' + formatTime(m.time)"
              @click.stop="seekTo(m.time)"
              @contextmenu.prevent.stop="removeMarker(i)"
            >
              <span class="marker-diamond"></span>
            </div>
          </div>
          <div class="timeline-tracks">
            <div class="track-row" v-if="tracks.video.length > 0">
              <span class="track-label" title="拖动 V1 片段可重排顺序| 点击可查看详情">V1</span>
              <div
                class="track-bar track-bar-droppable"
                @dragover.prevent="() => {}"
                @click="seekTimeline($event)"
              >
                <div
                  v-for="(seg, i) in tracks.video"
                  :key="seg.id ?? i"
                  class="track-segment video-seg"
                  :class="{
                    'has-thumb': !!segThumbUrl(seg),
                    'dragging': dragIndex === i,
                    'drag-over': dragOverIndex === i,
                    'selected': selectedSegment?.id === seg.id,
                  }"
                  :style="{ ...segStyle(seg), ...(segThumbUrl(seg) ? { backgroundImage: `url(${segThumbUrl(seg)})`, backgroundSize: 'cover', backgroundPosition: 'center' } : {}) }"
                  :title="`${seg.label}${seg.id != null ? ' (ID:' + seg.id + ')' : ''} | 拖动重排 | 点击查看详情`"
                  draggable="true"
                  @dragstart="onDragStart(i, $event)"
                  @dragend="onDragEnd"
                  @dragover="onDragOver(i, $event)"
                  @dragleave="onDragLeave"
                  @drop="onDrop(i)"
                  @click.stop="selectSegment(seg, i)"
                  @contextmenu.prevent="openSegCtxMenu($event, seg, i)"
                >
                  <span class="seg-label">{{ seg.label }}</span>
                </div>
              </div>
            </div>
            <div class="track-row" v-if="tracks.audio.length > 0">
              <span class="track-label">A1</span>
              <div class="track-bar">
                <div
                  v-for="(seg, i) in tracks.audio"
                  :key="i"
                  class="track-segment audio-seg"
                  :style="segStyle(seg)"
                  :title="seg.label"
                ></div>
              </div>
            </div>
            <div class="track-row" v-if="waveformBars">
              <span class="track-label">WF</span>
              <div class="track-bar" style="background: transparent; overflow: visible;">
                <WaveformCanvas
                  :bars="waveformBars"
                  :duration="waveformDuration"
                  :currentTime="currentTime"
                />
              </div>
            </div>
            <div class="track-row" v-if="tracks.subtitle.length > 0">
              <span class="track-label">TX</span>
              <div class="track-bar">
                <div
                  v-for="(seg, i) in tracks.subtitle"
                  :key="i"
                  class="track-segment subtitle-seg"
                  :style="segStyle(seg)"
                  :title="seg.label"
                ></div>
              </div>
            </div>
            <div v-if="isEmptyTrack" class="track-empty">
              暂无轨道数据 — Director 完成剪辑后时间线将在此显示
            </div>
          </div>
          <div
            v-if="currentVideo"
            class="playhead"
            :style="{ left: progressToPx + 'px' }"
          ></div>
        </div>
      </div>

      <!-- 转录文本 -->
      <div class="transcript-section" v-if="transcriptData?.segments?.length">
        <div class="section-label">
          转录文本 ({{ transcriptData.segments.length }} 句)
          <span class="transcript-meta">— 点击句子跳转</span>
        </div>
        <div class="transcript-list">
          <div
            v-for="seg in transcriptData.segments"
            :key="seg.index"
            :data-idx="seg.index"
            class="transcript-line"
            :class="{
              'active': currentTime >= seg.start && currentTime <= seg.end,
              'odd': seg.index % 2 === 0,
              'editing': editingTransIdx === seg.index
            }"
            @click="seekTo(seg.start)"
          >
            <span class="transcript-time">{{ formatTime(seg.start) }}</span>
            <div class="transcript-text-wrap">
              <template v-if="editingTransIdx === seg.index">
                <input
                  ref="transEditRef"
                  v-model="editTransText"
                  class="transcript-input"
                  @blur="saveTranscriptEdit(seg)"
                  @keydown.enter.prevent="saveTranscriptEdit(seg)"
                  @keydown.escape.prevent="cancelTranscriptEdit"
                  @click.stop
                />
              </template>
              <span v-else class="transcript-text" @dblclick.prevent="startTranscriptEdit(seg)">{{ seg.text }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 片段详情 -->
      <div class="detail-section" v-if="selectedSegment">
        <div class="section-label">片段详情</div>
        <div class="detail-grid">
          <div class="detail-item">
            <span class="detail-key">名称</span>
            <span class="detail-val">{{ selectedSegment.label }}</span>
          </div>
          <div class="detail-item">
            <span class="detail-key">ID</span>
            <span class="detail-val">#{{ selectedSegment.id }}</span>
          </div>
          <div class="detail-item">
            <span class="detail-key">起止</span>
            <span class="detail-val">{{ formatTime(selectedSegment.start) }} - {{ formatTime(selectedSegment.end) }}</span>
          </div>
          <div class="detail-item">
            <span class="detail-key">时长</span>
            <span class="detail-val">{{ formatDuration(selectedSegment.end - selectedSegment.start) }}</span>
          </div>
          <div class="detail-item" v-if="selectedSegment.source">
            <span class="detail-key">来源</span>
            <span class="detail-val detail-src">{{ selectedSegment.source }}</span>
          </div>
        </div>
      </div>

      <!-- 检查结果 -->
      <div class="info-section" v-if="checkResult">
        <div class="section-label">检查结果</div>
        <div class="info-content">{{ checkResult }}</div>
      </div>
    </div>

    <!-- 时间线右键菜单 -->
    <Teleport to="body">
      <div v-if="segCtx.visible" class="seg-ctx-overlay" @click="closeSegCtx" @contextmenu.prevent="closeSegCtx"></div>
      <div v-if="segCtx.visible" class="seg-ctx-menu" :style="{ top: segCtx.y + 'px', left: segCtx.x + 'px' }">
        <button class="ctx-item" @click="segCtxSeekStart"><span class="ctx-icon">👁</span> 跳转到开始</button>
        <button class="ctx-item" @click="segCtxDuplicate"><span class="ctx-icon">📋</span> 复制片段</button>
        <div class="ctx-sep"></div>
        <button class="ctx-item ctx-danger" @click="segCtxDelete"><span class="ctx-icon">🗑</span> 删除片段</button>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, inject, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import SvgIcon from '../components/SvgIcon.vue'
import WaveformCanvas from '../components/WaveformCanvas.vue'
import * as rpc from '../services/rpc'

const router = useRouter()

// ── 共享状态 ──
// inject 返回 ComputedRef，需要 .value 取值
const project: any = inject('project')
const projectState = computed(() => {
  const p = project?.value ?? project
  return p?.state ?? null
})

// 视频播放
const videoRef = ref<HTMLVideoElement | null>(null)
const playerRef = ref<HTMLElement | null>(null)
const timelineRef = ref<HTMLElement | null>(null)
const currentVideo = ref<string | null>(null)
const currentTime = ref(0)
const duration = ref(0)
const clipRange = ref<[number, number] | null>(null)

// ── 导出 ──
const exportModal: any = inject('exportModal', null)
const canExport = computed(() => {
  const s = projectState.value
  return !!s?.draftId && !s?.exporting
})
const isExporting = computed(() => {
  return !!projectState.value?.exporting
})
const exportTooltip = computed(() => {
  const s = projectState.value
  if (!s?.draftId) return '暂无草稿可导出'
  if (s?.exporting) return '导出中...'
  return '导出最终视频'
})
function onExportVideo() {
  const s = projectState.value
  if (!s?.draftId || s?.exporting) return
  const pid = (project?.value ?? project)?.id
  exportModal?.open(s.draftId, pid)
}

// 轨道数据
interface TrackSegment {
  start: number
  end: number
  label: string
  id?: number        // draft segment id
  source?: string    // source path
}
const tracks = ref<{
  video: TrackSegment[]
  audio: TrackSegment[]
  subtitle: TrackSegment[]
}>({
  video: [],
  audio: [],
  subtitle: [],
})

// 缩略图缓存 key: segId → data URL
const thumbnails = ref<Record<string, string>>({})
const thumbsLoading = ref(false)

// 缩略图生成防竞态计数器
let _thumbnailGenId = 0

/** 组件挂载标记：离场后停止异步缩略图生成 */
let _isMounted = true

// 拖拽状态
const dragIndex = ref(-1)
const dragOverIndex = ref(-1)

// 选中状态
const selectedSegment = ref<TrackSegment | null>(null)

function selectSegment(seg: TrackSegment, _idx: number) {
  selectedSegment.value = seg
  // 点击片段后跳到该片段开始时间
  if (videoRef.value && seg.start != null) {
    videoRef.value.currentTime = seg.start
  }
}

function seekTimeline(e: MouseEvent) {
  // 点击时间线空白处取消选择
  selectedSegment.value = null

  // 跳转到点击位置
  if (!videoRef.value || !timelineRef.value || duration.value <= 0) return
  const rect = timelineRef.value.getBoundingClientRect()
  const x = e.clientX - rect.left + timelineRef.value.scrollLeft
  const time = x / timelineScale.value
  videoRef.value.currentTime = Math.max(0, Math.min(time, duration.value))
}

// ── 缩略图生成 ──

async function captureFrame(videoSrc: string, time: number): Promise<string | null> {
  return new Promise((resolve) => {
    let resolved = false
    const once = (val: string | null) => {
      if (resolved) return
      resolved = true
      resolve(val)
    }

    const vid = document.createElement('video')
    vid.crossOrigin = 'anonymous'
    vid.preload = 'auto'
    vid.muted = true
    vid.src = videoSrc

    const cleanup = () => { vid.remove() }

    vid.onloadedmetadata = () => {
      vid.currentTime = Math.min(time, Math.max(0, vid.duration - 0.1))
    }
    vid.onseeked = () => {
      const canvas = document.createElement('canvas')
      canvas.width = 160
      canvas.height = 90
      const ctx = canvas.getContext('2d')
      if (ctx) {
        ctx.drawImage(vid, 0, 0, canvas.width, canvas.height)
        once(canvas.toDataURL('image/jpeg', 0.6))
      } else {
        once(null)
      }
      cleanup()
    }
    vid.onerror = () => { once(null); cleanup() }
    setTimeout(() => { once(null); cleanup() }, 5000)
  })
}

async function generateThumbnails() {
  if (!_isMounted || !currentVideo.value || tracks.value.video.length === 0) return
  const src = currentVideo.value
  const genId = ++_thumbnailGenId
  thumbsLoading.value = true

  // 快照轨道数组，避免循环中被外部修改
  const segments = [...tracks.value.video]

  // 拷贝旧缓存作为基础，避免并发覆盖已生成的缩略图
  const newThumbs: Record<string, string> = { ...thumbnails.value }

  for (const seg of segments) {
    if (genId !== _thumbnailGenId) {
      // 被新请求取代，放弃本次生成
      return
    }
    const key = String(seg.id ?? seg.start)
    if (newThumbs[key]) continue // 已有缓存
    try {
      const url = await captureFrame(src, seg.start + 0.5)
      if (genId !== _thumbnailGenId) return
      if (url) newThumbs[key] = url
    } catch (e) {
      console.warn('[Preview] 生成缩略图失败:', e)
    }
  }

  // 再次确认仍是当前请求的结果
  if (genId !== _thumbnailGenId) return
  thumbnails.value = newThumbs
  thumbsLoading.value = false
}

// 草稿版本跟踪（替代 (window as any).__draftVersion 全局变量）
let _draftVersion = 0

// 草稿自动刷新轮询
let _draftPollTimer: ReturnType<typeof setInterval> | null = null

// 转录数据
const transcriptData = ref<{ source_video: string; segments: Array<{ index: number; start: number; end: number; text: string }> } | null>(null)

function startDraftPolling() {
  stopDraftPolling()
  _draftPollTimer = setInterval(async () => {
    const did = projectState.value?.draftId
    if (!did || did !== draftId.value) return
    try {
      const info = await rpc.getDraftInfo(did)
      if (info && info.current_version) {
        const curVer = _draftVersion
        if (curVer && curVer >= info.current_version) return
        ;(window as any).__draftVersion = info.current_version
        loadDraftTracks()
      }
    } catch (e) {
      console.warn('[Preview] 草稿轮询失败:', e)
    }
  }, 5000)
}

function stopDraftPolling() {
  if (_draftPollTimer) {
    clearInterval(_draftPollTimer)
    _draftPollTimer = null
  }
}

const isEmptyTrack = computed(() =>
  tracks.value.video.length === 0 &&
  tracks.value.audio.length === 0 &&
  tracks.value.subtitle.length === 0
)

// 检查结果
const checkResult = ref<string | null>(null)

// 时间刻度
const timeTicks = computed(() => {
  const d = Math.max(duration.value, 10)
  const interval = d <= 30 ? 5 : d <= 120 ? 15 : 30
  const ticks: number[] = []
  for (let t = 0; t <= d; t += interval) {
    ticks.push(t)
  }
  return ticks
})

const ZOOM_MIN = 0.25
const ZOOM_MAX = 4.0
const BASE_WIDTH = 2000
const zoomLevel = ref(1.0)
const timelineWidth = computed(() => BASE_WIDTH * zoomLevel.value)
const timelineScale = computed(() => duration.value > 0 ? timelineWidth.value / duration.value : 1)

function zoomIn()  { zoomLevel.value = Math.min(ZOOM_MAX, zoomLevel.value * 1.5) }
function zoomOut() { zoomLevel.value = Math.max(ZOOM_MIN, zoomLevel.value / 1.5) }
function onWheel(e: WheelEvent) {
  // Ctrl/Meta+滚轮 = 缩放（标准 UX）
  if (e.ctrlKey || e.metaKey) {
    e.preventDefault()
    if (e.deltaY < 0) zoomIn()
    else zoomOut()
    return
  }
  // 无修饰键时让浏览器默认滚动时间线
}

function tickToPx(t: number): number {
  return t * timelineScale.value
}

const progressToPx = computed(() =>
  currentTime.value * timelineScale.value
)

function segStyle(seg: TrackSegment) {
  const left = seg.start * timelineScale.value
  const width = Math.max((seg.end - seg.start) * timelineScale.value, 4)
  return { left: left + 'px', width: width + 'px' }
}

function segThumbUrl(seg: TrackSegment): string | null {
  const key = String(seg.id ?? seg.start)
  return thumbnails.value[key] || null
}

// 格式化
function formatTime(s: number): string {
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return `${m}:${sec.toString().padStart(2, '0')}`
}

function formatDuration(s: number): string {
  if (s < 60) return `${Math.round(s)}秒`
  const m = Math.floor(s / 60)
  const sec = Math.round(s % 60)
  return sec > 0 ? `${m}分${sec}秒` : `${m}分钟`
}

// 事件
function onTimeUpdate() {
  if (videoRef.value) {
    currentTime.value = videoRef.value.currentTime
    syncTimelineScroll()
    syncTranscriptScroll()
  }
}

function onLoaded() {
  if (videoRef.value) {
    duration.value = videoRef.value.duration
  }
}

// ── 滚动同步 ──

function syncTimelineScroll() {
  if (!timelineRef.value || duration.value <= 0) return
  const container = timelineRef.value
  const px = progressToPx.value
  const viewW = container.clientWidth
  // 当播放头超出可视区 70% 时自动滚动
  const threshold = container.scrollLeft + viewW * 0.7
  if (px > threshold || px < container.scrollLeft) {
    container.scrollTo({ left: Math.max(0, px - viewW * 0.3), behavior: 'smooth' })
  }
}

function syncTranscriptScroll() {
  if (!transcriptData.value?.segments) return
  const ct = currentTime.value
  const activeSeg = transcriptData.value.segments.find(
    s => ct >= s.start && ct <= s.end
  )
  if (!activeSeg) return
  const el = document.querySelector(`.transcript-line[data-idx="${activeSeg.index}"]`)
  if (el) {
    el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }
}

function goBack() {
  router.push('/')
}

// 跳转到指定时间
function seekTo(t: number) {
  if (videoRef.value) {
    videoRef.value.currentTime = t
  }
}

// ── 从共享状态加载预览数据 ──
function loadPreview(data: {
  videoUrl: string
  startTime?: number
  endTime?: number
  result?: string
  tracks?: {
    video?: TrackSegment[]
    audio?: TrackSegment[]
    subtitle?: TrackSegment[]
  }
}) {
  currentVideo.value = data.videoUrl || null
  clipRange.value = data.startTime != null && data.endTime != null
    ? [data.startTime, data.endTime]
    : null
  checkResult.value = data.result || null
  // 如果预览数据自带 tracks（来自 Director），就用它；否则等 draft 加载
  if (data.tracks && (data.tracks.video?.length || data.tracks.audio?.length || data.tracks.subtitle?.length)) {
    tracks.value = {
      video: data.tracks.video || [],
      audio: data.tracks.audio || [],
      subtitle: data.tracks.subtitle || [],
    }
  }
  duration.value = 0
  currentTime.value = 0
}

// ── 从草稿加载轨道数据 ──
const draftSegments = ref<TrackSegment[]>([])
const draftId = ref('')

const draftLoading = ref(false)
const exportingReport = ref(false)

// ── 草稿选择器 ──
const draftsList = ref<Array<{ draft_id: string; name: string }>>([])
const selectedDraftId = ref('')

async function loadDraftsList() {
  try {
    const drafts = await rpc.listDrafts()
    draftsList.value = drafts.map((d: any) => ({
      draft_id: d.draft_id,
      name: d.name || d.draft_id,
    }))
  } catch (e) {
    console.warn('[Preview] 加载草稿列表失败:', e)
  }
}

async function onDraftSelect() {
  if (!selectedDraftId.value) return
  draftId.value = selectedDraftId.value
  draftLoading.value = true
  try {
    const info = await rpc.getDraftInfo(selectedDraftId.value)
    if (!info || !info.segments) return

    _draftVersion = info.current_version || 0

    const segs: TrackSegment[] = info.segments.map((s: any, idx: number) => ({
      id: s.id ?? idx,
      start: s.start ?? 0,
      end: s.end ?? 0,
      label: s.text || s.status || `片段${idx + 1}`,
      source: s.source_path || '',
    }))
    draftSegments.value = segs
    tracks.value.video = [...segs]

    generateThumbnails()

    const subs = info.subtitles || []
    if (subs.length > 0) {
      tracks.value.subtitle = subs.map((s: any, idx: number) => ({
        id: idx,
        start: s.start ?? 0,
        end: s.end ?? 0,
        label: s.text || '',
      }))
    }

    transcriptData.value = info.transcript || null
    // 设置当前视频（如果草稿片段包含 source）
    const firstSeg = segs.find(s => s.source)
    if (firstSeg?.source) {
      currentVideo.value = firstSeg.source
    }
  } catch (err) {
    console.warn('[Preview] 加载草稿失败:', err)
  } finally {
    draftLoading.value = false
  }
}

async function exportReport() {
  if (!draftId.value || exportingReport.value) return
  exportingReport.value = true
  try {
    const result = await rpc.exportProjectReport(draftId.value)
    if (!result?.markdown) throw new Error('后端未返回报告数据')

    const name = (result.name || draftId.value).replace(/[/\\?%*:|"<>]/g, '_')
    const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
    const defaultName = `${name}_报告_${ts}.md`

    // 使用正式保存对话 + 写入文件，确保文件存到用户选的位置
    const savePath = await window.cherryclip?.dialogs.saveFile?.({
      defaultPath: defaultName,
      filters: [{ name: 'Markdown', extensions: ['md'] }],
    })
    if (!savePath) {
      throw new Error('SAVE_CANCELLED')
    }
    const written = await window.cherryclip?.fs?.writeFile?.(savePath, result.markdown)
    if (!written) {
      // Electron writeFile 不可用时降级为浏览器下载
      throw new Error('WRITE_FAILED')
    }
  } catch (err: any) {
    console.error('[Preview] 导出报告失败:', err)
    alert('导出报告失败: ' + (err?.message || err))
  } finally {
    exportingReport.value = false
  }
}

async function loadDraftTracks() {
  const did = projectState.value?.draftId
  if (!did) return
  draftId.value = did

  draftLoading.value = true
  try {
    const info = await rpc.getDraftInfo(did)
    if (!info || !info.segments) return

    _draftVersion = info.current_version || 0

    const segs: TrackSegment[] = info.segments.map((s: any, idx: number) => ({
      id: s.id ?? idx,
      start: s.start ?? 0,
      end: s.end ?? 0,
      label: s.text || s.status || `片段${idx + 1}`,
      source: s.source_path || '',
    }))
    draftSegments.value = segs

    // 只在没有 Director tracks 时自动同步
    if (tracks.value.video.length === 0 || !tracks.value.video[0]?.id) {
      tracks.value.video = [...segs]
    }

    // 同步字幕轨道
    const subs = info.subtitles || []
    if (subs.length > 0) {
      tracks.value.subtitle = subs.map((s: any, idx: number) => ({
        id: idx,
        start: s.start ?? 0,
        end: s.end ?? 0,
        label: s.text || '',
      }))
    }

    // 转录数据
    transcriptData.value = info.transcript || null

    generateThumbnails()
  } catch (err) {
    console.warn('[Preview] 加载草稿轨道失败:', err)
  } finally {
    draftLoading.value = false
  }
}

// ── 拖拽 ──
function onDragStart(idx: number, e: DragEvent) {
  dragIndex.value = idx
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('text/plain', String(idx))
  }
}

function onDragEnd() {
  dragIndex.value = -1
  dragOverIndex.value = -1
}

function onDragOver(idx: number, e: DragEvent) {
  e.preventDefault()
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'
  dragOverIndex.value = idx
}

function onDragLeave() {
  dragOverIndex.value = -1
}

async function onDrop(idx: number) {
  dragOverIndex.value = -1
  const from = dragIndex.value
  dragIndex.value = -1
  if (from < 0 || from === idx) return

  // 保存旧状态，失败时回滚
  const oldVideo = [...tracks.value.video]
  const oldSegs = [...draftSegments.value]

  // 本地重排
  const segs = [...tracks.value.video]
  const [moved] = segs.splice(from, 1)
  segs.splice(idx, 0, moved)
  tracks.value.video = segs
  draftSegments.value = segs

  // 持久化
  const clipIds = segs.map(s => s.id!).filter(id => id != null)
  try {
    const res = await rpc.reorderClips(draftId.value, clipIds)
    if (!res.ok) {
      console.warn('[Preview] 重排失败:', res.error)
      tracks.value.video = oldVideo
      draftSegments.value = oldSegs
    }
  } catch (err) {
    console.warn('[Preview] 重排 RPC 失败:', err)
    tracks.value.video = oldVideo
    draftSegments.value = oldSegs
  }
}

// ── 时间线右键菜单 ──
interface SegCtxState {
  visible: boolean
  x: number
  y: number
  seg: TrackSegment | null
  index: number
}
const segCtx = ref<SegCtxState>({ visible: false, x: 0, y: 0, seg: null, index: -1 })

function openSegCtxMenu(e: MouseEvent, seg: TrackSegment, idx: number) {
  selectSegment(seg, idx)
  const adjX = Math.min(e.clientX, window.innerWidth - 170 - 8)
  const adjY = Math.min(e.clientY, window.innerHeight - 120 - 8)
  segCtx.value = { visible: true, x: adjX, y: adjY, seg, index: idx }
}

function closeSegCtx() {
  segCtx.value.visible = false
}

function segCtxSeekStart() {
  const seg = segCtx.value.seg
  if (seg && videoRef.value) {
    videoRef.value.currentTime = seg.start
  }
  closeSegCtx()
}

function segCtxDuplicate() {
  const idx = segCtx.value.index
  if (idx < 0 || !tracks.value.video[idx]) return
  const original = tracks.value.video[idx]
  const dup: TrackSegment = {
    ...original,
    id: undefined, // 无 id 的新片段，让后端分配
    label: original.label + ' (副本)',
  }
  tracks.value.video.splice(idx + 1, 0, dup)
  draftSegments.value = [...tracks.value.video]
  // 不需要持久化—预览层操作
  closeSegCtx()
}

function segCtxDelete() {
  const idx = segCtx.value.index
  if (idx < 0 || !tracks.value.video[idx]) return
  tracks.value.video.splice(idx, 1)
  draftSegments.value = [...tracks.value.video]
  // 持久化剩余片段顺序
  const clipIds = tracks.value.video.map(s => s.id!).filter(id => id != null)
  if (clipIds.length > 0) {
    rpc.reorderClips(draftId.value, clipIds).catch((err: any) => {
      console.warn('[Preview] 删除后重排持久化失败:', err)
    })
  }
  selectedSegment.value = null
  closeSegCtx()
}

// ── 转录文本编辑 ──
const editingTransIdx = ref<number | null>(null)
const editTransText = ref('')
const transEditRef = ref<HTMLInputElement | null>(null)

function startTranscriptEdit(seg: any) {
  editingTransIdx.value = seg.index
  editTransText.value = seg.text
  nextTick(() => {
    transEditRef.value?.focus()
    transEditRef.value?.select()
  })
}

function saveTranscriptEdit(seg: any) {
  if (!transcriptData.value) return
  const target = transcriptData.value.segments.find(s => s.index === seg.index)
  if (target) target.text = editTransText.value
  editingTransIdx.value = null
}

function cancelTranscriptEdit() {
  editingTransIdx.value = null
}

// ── 时间线标记 ──
const markers = ref<Array<{ time: number }>>([])

function addMarker(e: MouseEvent) {
  if (!timelineRef.value || duration.value <= 0) return
  const rect = timelineRef.value.getBoundingClientRect()
  const x = e.clientX - rect.left + timelineRef.value.scrollLeft
  const time = Math.round(x / timelineScale.value)
  if (time < 0 || time > Math.round(duration.value)) return
  if (markers.value.some(m => Math.abs(m.time - time) < 0.5)) return
  markers.value.push({ time })
  markers.value.sort((a, b) => a.time - b.time)
}

function removeMarker(idx: number) {
  markers.value.splice(idx, 1)
}

// ── 波形 ──
const waveformBars = ref<Array<{ t: number; peak: number; rms: number }> | null>(null)
const waveformDuration = ref(0)
const waveformPath = ref('')

async function fetchWaveform(audioPath: string) {
  waveformBars.value = null
  waveformDuration.value = 0
  waveformPath.value = audioPath

  try {
    const data = await rpc.getWaveform(audioPath, 200)
    if (data && data.bars) {
      waveformBars.value = data.bars
      waveformDuration.value = data.duration || 0
    }
  } catch (err) {
    console.warn('[Waveform] 获取失败:', err)
  }
}

// 监听共享状态中的 previewData 变化
watch(
  () => projectState.value?.previewData,
  (data) => {
    if (data) loadPreview(data)
  },
  { immediate: true }
)

// 当有音频素材时自动拉波形
watch(
  () => projectState.value?.materials,
  (materials) => {
    if (!materials) return
    const audio = materials.find((m: any) =>
      m.type === 'audio' && m.path !== waveformPath.value
    )
    if (audio) fetchWaveform(audio.path)
  },
  { immediate: true, deep: true }
)

// 当 draftId 变化时重载轨道数据
watch(
  () => projectState.value?.draftId,
  (newId) => {
    if (newId && newId !== draftId.value) {
      loadDraftTracks()
    }
  }
)

// ── 快捷键：Space 暂停/播放，← → 逐帧步进 ──
const FRAME_STEP = 1 / 30 // 约 33ms，覆盖 24/25/30fps 足够精确

function onKeyDown(e: KeyboardEvent) {
  if (!videoRef.value) return
  // 不在输入框中触发
  if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return

  if (e.key === ' ') {
    e.preventDefault()
    if (videoRef.value.paused) {
      videoRef.value.play()
    } else {
      videoRef.value.pause()
    }
    return
  }

  // M 键在当前时间添加标记
  if (e.key === 'm' || e.key === 'M') {
    e.preventDefault()
    const t = Math.round(currentTime.value)
    if (!markers.value.some(m => Math.abs(m.time - t) < 0.5)) {
      markers.value.push({ time: t })
      markers.value.sort((a, b) => a.time - b.time)
    }
    return
  }

  // 逐帧步进仅在暂停时生效
  if (!videoRef.value.paused) return

  if (e.key === 'ArrowLeft') {
    e.preventDefault()
    videoRef.value.currentTime = Math.max(0, videoRef.value.currentTime - FRAME_STEP)
  } else if (e.key === 'ArrowRight') {
    e.preventDefault()
    videoRef.value.currentTime = Math.min(
      videoRef.value.duration || Infinity,
      videoRef.value.currentTime + FRAME_STEP
    )
  }
}

onMounted(() => {
  _isMounted = true
  loadDraftsList()
  if (projectState.value?.draftId) {
    loadDraftTracks()
  }
  startDraftPolling()
  window.addEventListener('keydown', onKeyDown)
})

onUnmounted(() => {
  _isMounted = false
  stopDraftPolling()
  window.removeEventListener('keydown', onKeyDown)
})

defineExpose({ loadPreview })
</script>

<style scoped>
.preview-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--surface-base);
}

/* ========== 顶部 ========== */
.preview-header {
  display: flex;
  align-items: center;
  padding: 10px 24px;
  flex-shrink: 0;
  background: var(--surface-glass);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--surface-glass-edge);
}

.btn-back {
  display: flex;
  align-items: center;
  gap: 4px;
  background: transparent;
  border: 1px solid var(--border-card);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  padding: 5px 12px;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.12s;
  font-family: inherit;
}
.btn-back:hover { background: var(--bg-hover); color: var(--text-primary); border-color: var(--brand); }

/* ========== 草稿选择器 ========== */
.draft-selector {
  display: flex;
  align-items: center;
  gap: 4px;
  background: var(--surface-overlay);
  border: 1px solid var(--border-card);
  border-radius: var(--radius-sm);
  padding: 3px 8px;
  color: var(--text-secondary);
  margin-left: 8px;
}
.draft-selector select {
  background: transparent;
  border: none;
  color: var(--text-secondary);
  font-size: 11px;
  font-family: inherit;
  outline: none;
  cursor: pointer;
  min-width: 100px;
  max-width: 180px;
}
.draft-selector select option { background: var(--surface-elevated); color: var(--text-primary); }

.preview-title {
  flex: 1;
  text-align: center;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
}

.btn-refresh {
  display: flex;
  align-items: center;
  gap: 3px;
  background: transparent;
  border: 1px solid var(--border-card);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  padding: 4px 8px;
  font-size: 10px;
  cursor: pointer;
  transition: all 0.12s;
  font-family: inherit;
  margin-right: 6px;
}
.btn-refresh:hover { background: var(--bg-hover); color: var(--text-primary); border-color: var(--brand); }

.btn-export-report {
  display: flex;
  align-items: center;
  gap: 3px;
  background: var(--brand);
  border: none;
  border-radius: var(--radius-sm);
  color: var(--text-on-brand);
  padding: 4px 10px;
  font-size: 10px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.12s;
  font-family: inherit;
  margin-right: 6px;
}
.btn-export-report:hover:not(:disabled) { background: var(--brand-light); }
.btn-export-report:disabled { opacity: 0.4; cursor: not-allowed; }

.header-spacer { width: 60px; }

/* ========== 主体 ========== */
.preview-body {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0;
}

/* ========== 播放器 ========== */
.player-section {
  padding: 14px 24px;
  border-bottom: 1px solid var(--border-subtle);
}

.player-container {
  width: 100%;
  max-width: 800px;
  margin: 0 auto;
  background: var(--bg-logo);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius);
  overflow: hidden;
  aspect-ratio: 16 / 9;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
}

/* 导出按钮 — 悬浮在播放器右上角 */
.btn-export-video {
  position: absolute;
  top: 12px;
  right: 12px;
  width: 40px;
  height: 40px;
  border-radius: 10px;
  border: none;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s ease;
  z-index: 10;
  background: rgba(0, 0, 0, 0.55);
  color: var(--text-secondary);
}
.btn-export-video.active {
  background: var(--brand);
  color: #fff;
  box-shadow: 0 2px 8px rgba(99, 102, 241, 0.35);
}
.btn-export-video.active:hover {
  background: var(--brand-light);
  transform: scale(1.05);
}
.btn-export-video.exporting {
  opacity: 0.6;
  animation: pulse_btn 1.2s ease-in-out infinite;
}
.btn-export-video:disabled { cursor: not-allowed; opacity: 0.5; }
@keyframes pulse_btn {
  0%, 100% { opacity: 0.6; }
  50% { opacity: 1; }
}

.video-player { width: 100%; height: 100%; display: block; outline: none; }
.player-placeholder { text-align: center; }

.placeholder-ring {
  display: flex; align-items: center; justify-content: center;
  margin-bottom: 12px; position: relative;
}
.placeholder-ring::before {
  content: '';
  position: absolute;
  width: 52px; height: 52px;
  border-radius: 50%;
  background: conic-gradient(from 0deg, var(--brand), var(--accent-cyan), var(--accent-green), var(--accent-amber), var(--brand));
  opacity: 0.1;
  animation: ringSpin 6s linear infinite;
}
.placeholder-ring :deep(svg) { position: relative; z-index: 1; opacity: 0.25; }

@keyframes ringSpin { to { transform: rotate(360deg); } }

.placeholder-text { font-size: 14px; font-weight: 500; color: var(--text-muted); margin-bottom: 4px; }
.placeholder-hint { font-size: 11px; color: var(--text-muted); }

.player-info {
  max-width: 800px;
  margin: 6px auto 0;
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--text-muted);
}
.clip-info { color: var(--text-accent); }

/* ========== 时间线 ========== */
.timeline-section {
  padding: 14px 24px;
  border-bottom: 1px solid var(--border-subtle);
}

.section-label {
  font-size: 10px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.4px;
}
.section-label-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.zoom-controls { display: flex; align-items: center; gap: 4px; }
.zoom-btn {
  width: 20px; height: 20px;
  display: flex; align-items: center; justify-content: center;
  background: var(--surface-overlay);
  border: 1px solid var(--border-card);
  border-radius: 3px;
  color: var(--text-muted);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.12s;
}
.zoom-btn:hover:not(:disabled) { color: var(--text-primary); border-color: var(--border-active); }
.zoom-btn:disabled { opacity: 0.25; cursor: default; }
.zoom-label { font-size: 9px; color: var(--text-muted); min-width: 28px; text-align: center; font-variant-numeric: tabular-nums; }

.timeline-container {
  position: relative;
  overflow-x: auto;
  min-height: 90px;
  background: var(--surface-overlay);
  border-radius: var(--radius);
  border: 1px solid var(--border-card);
}
.timeline-ruler {
  position: relative;
  height: 22px;
  border-bottom: 1px solid var(--border-card);
  min-width: v-bind(timelineWidth + 'px');
}
.tick {
  position: absolute; top: 0;
  width: 1px; height: 100%;
  background: var(--border-card);
}
.tick-label {
  position: absolute; top: 3px; left: 4px;
  font-size: 9px; color: var(--text-muted);
  white-space: nowrap;
}

/* 时间线标记 */
.marker {
  position: absolute; top: 0;
  width: 10px; height: 100%;
  cursor: pointer;
  z-index: 5;
  margin-left: -5px;
}
.marker-diamond {
  position: absolute;
  top: -1px; left: 50%;
  transform: translateX(-50%);
  width: 8px; height: 8px;
  background: var(--accent-amber);
  border: 1px solid rgba(0,0,0,0.4);
  border-radius: 1px;
  rotate: 45deg;
}
.marker:hover .marker-diamond {
  background: #FBBF24;
  box-shadow: 0 0 6px rgba(245, 158, 11, 0.5);
}

.timeline-tracks {
  position: relative;
  min-width: v-bind(timelineWidth + 'px');
  padding: 3px 0;
}
.track-row {
  display: flex;
  align-items: center;
  height: 30px;
  margin: 1px 0;
  position: relative;
}
.track-label {
  width: 26px;
  font-size: 9px;
  font-weight: 600;
  color: var(--text-muted);
  text-align: center;
  flex-shrink: 0;
}
.track-bar {
  position: relative;
  flex: 1;
  height: 22px;
  background: rgba(255, 255, 255, 0.02);
  border-radius: 3px;
  margin-right: 6px;
  overflow: hidden;
}
.track-segment {
  position: absolute;
  top: 2px;
  height: 18px;
  border-radius: 3px;
  cursor: grab;
  transition: opacity 0.12s, transform 0.08s;
  display: flex;
  align-items: center;
  overflow: hidden;
}
.track-segment:hover { opacity: 0.85; }
.track-segment:active { cursor: grabbing; }
.track-segment.dragging { opacity: 0.35; transform: scale(0.95); }
.track-segment.drag-over { opacity: 1; box-shadow: 0 0 0 2px var(--text-primary), 0 0 6px rgba(255,255,255,0.2); z-index: 5; }
.track-segment.selected { opacity: 1; border: 2px solid var(--text-primary); box-shadow: 0 0 8px rgba(99, 102, 241, 0.5); z-index: 4; }

.seg-label {
  font-size: 9px;
  color: var(--text-on-brand);
  padding: 0 5px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  pointer-events: none;
}

.video-seg { background: var(--brand); opacity: 0.7; }
.video-seg.has-thumb { opacity: 1; background: rgba(0, 0, 0, 0.3); }
.video-seg.has-thumb::before {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(to bottom, rgba(0,0,0,0.1), rgba(0,0,0,0.4));
  border-radius: inherit;
  z-index: 1;
}
.video-seg.has-thumb .seg-label { position: relative; z-index: 2; text-shadow: 0 1px 2px rgba(0,0,0,0.7); }
.audio-seg { background: var(--accent-green); opacity: 0.7; }
.subtitle-seg { background: var(--accent-amber); opacity: 0.7; }

.track-bar-droppable {
  position: relative;
  flex: 1;
  height: 22px;
  background: rgba(255, 255, 255, 0.02);
  border-radius: 3px;
  margin-right: 6px;
  overflow: hidden;
}

.track-empty { padding: 20px 14px; text-align: center; font-size: 11px; color: var(--text-muted); }

/* 播放头 */
.playhead {
  position: absolute;
  top: 0; bottom: 0;
  width: 2px;
  background: var(--accent-red);
  z-index: 10;
  pointer-events: none;
  transition: left 0.05s linear;
}
.playhead::before {
  content: '';
  position: absolute;
  top: -3px;
  left: -3px;
  width: 8px; height: 8px;
  background: var(--accent-red);
  border-radius: 50%;
}

/* ========== 转录文本 ========== */
.transcript-section { padding: 10px 24px; border-bottom: 1px solid var(--border-subtle); }
.transcript-meta { font-weight: 400; font-size: 9px; color: var(--text-muted); }
.transcript-list {
  max-height: 200px;
  overflow-y: auto;
  border: 1px solid var(--border-card);
  border-radius: var(--radius);
  background: var(--surface-overlay);
}
.transcript-line {
  display: flex;
  gap: 8px;
  padding: 5px 10px;
  font-size: 12px;
  cursor: pointer;
  transition: background 0.08s;
  border-bottom: 1px solid rgba(255, 255, 255, 0.02);
}
.transcript-line:last-child { border-bottom: none; }
.transcript-line:hover { background: var(--bg-hover); }
.transcript-line.active { background: var(--bg-active); color: var(--text-accent); }
.transcript-line.odd { background: rgba(255, 255, 255, 0.01); }
.transcript-line.odd.active { background: var(--bg-active); }
.transcript-line.editing { background: var(--bg-active); }
.transcript-time { flex-shrink: 0; width: 44px; font-size: 10px; color: var(--text-muted); text-align: right; padding-top: 1px; }
.transcript-text-wrap { flex: 1; min-width: 0; }
.transcript-text {
  display: block;
  line-height: 1.5;
  cursor: text;
  padding: 1px 4px;
  border-radius: 3px;
  transition: background 0.1s;
}
.transcript-text:hover { background: var(--bg-hover); }
.transcript-input {
  width: 100%;
  padding: 3px 6px;
  background: var(--surface-raised);
  border: 1px solid var(--border-active);
  border-radius: 4px;
  color: var(--text-primary);
  font-size: 12px;
  font-family: inherit;
  outline: none;
}

/* ========== 片段详情 ========== */
.detail-section { padding: 10px 24px; border-bottom: 1px solid var(--border-subtle); }
.detail-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 6px;
}
.detail-item {
  background: var(--surface-overlay);
  border: 1px solid var(--border-card);
  border-radius: 5px;
  padding: 7px 10px;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.detail-key { font-size: 9px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.4px; }
.detail-val { font-size: 12px; color: var(--text-primary); font-weight: 500; }
.detail-src { font-size: 10px; color: var(--text-secondary); word-break: break-all; }

/* ========== 检查结果 ========== */
.info-section { padding: 14px 24px 20px; }
.info-content {
  background: var(--surface-overlay);
  border: 1px solid var(--border-card);
  border-radius: var(--radius);
  padding: 12px 14px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-secondary);
  white-space: pre-wrap;
}

/* ========== 加载遮罩 ========== */
.preview-loading {
  position: absolute;
  inset: 0;
  z-index: 50;
  background: var(--surface-raised);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  font-size: 12px;
  color: var(--text-muted);
}
.preview-loading .spinner {
  width: 24px; height: 24px;
  border: 2px solid var(--border-subtle);
  border-top-color: var(--brand);
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

.fade-enter-active { transition: opacity 0.18s ease; }
.fade-leave-active { transition: opacity 0.12s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }

/* ========== 片段右键菜单 ========== */
.seg-ctx-overlay {
  position: fixed; inset: 0; z-index: 999;
}
.seg-ctx-menu {
  position: fixed; z-index: 1000;
  background: var(--surface-elevated);
  border: 1px solid var(--border-card);
  border-radius: var(--radius);
  padding: 4px;
  min-width: 150px;
  box-shadow: var(--shadow-lg);
  animation: ctxIn 0.1s ease-out;
}
@keyframes ctxIn { from { opacity: 0; transform: scale(0.96) translateY(-3px); } to { opacity: 1; transform: none; } }
.seg-ctx-menu .ctx-item {
  display: flex; align-items: center; gap: 6px;
  width: 100%; padding: 7px 10px;
  background: transparent; border: none; border-radius: 4px;
  color: var(--text-secondary); font-size: 11px; font-family: inherit;
  cursor: pointer; text-align: left; transition: all 0.08s;
}
.seg-ctx-menu .ctx-item:hover { background: var(--bg-hover); color: var(--text-primary); }
.seg-ctx-menu .ctx-icon { font-size: 12px; width: 16px; text-align: center; flex-shrink: 0; }
.seg-ctx-menu .ctx-danger { color: var(--accent-red); }
.seg-ctx-menu .ctx-danger:hover { background: rgba(239, 68, 68, 0.08); color: #FCA5A5; }
.seg-ctx-menu .ctx-sep { height: 1px; background: var(--border-subtle); margin: 2px 6px; }
</style>
