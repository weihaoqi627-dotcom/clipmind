<template>
  <div class="bgm-selector">
    <div class="bgm-header">
      <span class="bgm-icon">🎵</span>
      <span class="bgm-title">选择背景音乐</span>
      <span class="bgm-subtitle">从曲库中选一首，或上传你自己的 BGM / 视频</span>
    </div>

    <!-- 热门推荐 -->
    <div class="bgm-section">
      <div class="section-tag hot">🔥 热门推荐</div>
      <div class="song-list">
        <div
          v-for="(song, i) in visiblePopular"
          :key="'pop-' + i"
          class="song-card"
          :class="{ selected: selectedKey === 'pop-' + i }"
          @click="select('pop-' + i, song)"
        >
          <button class="btn-play" @click.stop="togglePreview(song)" :title="playing === song.path ? '暂停' : '试听'">
            {{ playing === song.path ? '⏸' : '▶' }}
          </button>
          <div class="song-info">
            <span class="song-name">{{ song.name }}</span>
            <span class="song-artist">{{ song.artist }}</span>
          </div>
          <span class="song-check" v-if="selectedKey === 'pop-' + i">✓</span>
        </div>
        <button v-if="popular.length > 5" class="btn-more" @click="showMorePopular">
          展开全部 {{ popular.length }} 首
        </button>
      </div>
    </div>

    <!-- AI 根据素材推荐 -->
    <div class="bgm-section">
      <div class="section-tag ai">🤖 AI 根据素材推荐</div>
      <div v-if="recommended.length > 0" class="song-list">
        <div
          v-for="(song, i) in visibleRecommended"
          :key="'rec-' + i"
          class="song-card"
          :class="{ selected: selectedKey === 'rec-' + i }"
          @click="select('rec-' + i, song)"
        >
          <button class="btn-play" @click.stop="togglePreview(song)" :title="playing === song.path ? '暂停' : '试听'">
            {{ playing === song.path ? '⏸' : '▶' }}
          </button>
          <div class="song-info">
            <span class="song-name">{{ song.name }}</span>
            <span class="song-artist">{{ song.artist }}</span>
          </div>
          <span class="song-check" v-if="selectedKey === 'rec-' + i">✓</span>
        </div>
        <button v-if="recommended.length > 5 && !showAllRec" class="btn-more" @click="showAllRec = true">
          展开全部 {{ recommended.length }} 首
        </button>
      </div>
      <div v-else class="ai-loading">
        <span class="loading-dot"></span>
        <span class="loading-dot"></span>
        <span class="loading-dot"></span>
        <span class="loading-text">AI 正在分析素材风格...</span>
      </div>
    </div>

    <!-- 上传自己的素材 -->
    <div class="upload-section">
      <div class="section-divider"><span>或</span></div>
      <p class="upload-hint">上传你自己的 BGM 或视频，我们会存到曲库里</p>
      <div class="upload-buttons">
        <button class="btn-upload" @click="uploadAudio">
          <span class="upload-icon">🎵</span> 上传 BGM
        </button>
        <button class="btn-upload" @click="uploadVideo">
          <span class="upload-icon">🎬</span> 上传视频提取
        </button>
      </div>
    </div>

    <!-- 操作 -->
    <div class="bgm-actions">
      <button class="btn-skip" @click="skip">跳过，让 AI 自己决定</button>
    </div>

    <!-- 隐藏的 audio 播放器 -->
    <audio ref="audioRef" @ended="playing = ''" @error="playing = ''" style="display:none"></audio>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onUnmounted } from 'vue'
import { useToast } from '../composables/useToast'

const { error: toastError } = useToast()

export interface BgmSong {
  name: string
  artist: string
  path?: string       // 文件路径（本地曲库或用户上传）
  isUploaded?: boolean  // 是否用户上传的
}

const props = defineProps<{
  popular: BgmSong[]
  recommended: BgmSong[]
}>()

const emit = defineEmits<{
  select: [song: BgmSong]
  skip: []
  upload: [type: 'audio' | 'video', paths: string[]]
}>()

const audioRef = ref<HTMLAudioElement | null>(null)
const playing = ref('')       // 当前播放的文件路径

const showAllPop = ref(false)
const showAllRec = ref(false)

const visiblePopular = computed(() =>
  showAllPop.value ? props.popular : props.popular.slice(0, 5)
)
const visibleRecommended = computed(() =>
  showAllRec.value ? props.recommended : props.recommended.slice(0, 5)
)

const selectedKey = ref<string | null>(null)
const selectedSong = ref<BgmSong | null>(null)

watch(() => props.recommended, () => {
  showAllRec.value = false
})

function togglePreview(song: BgmSong) {
  if (!song.path || !audioRef.value) return
  if (playing.value === song.path) {
    audioRef.value.pause()
    playing.value = ''
  } else {
    // 通过 Electron IPC 读取本地文件转为 blob URL
    loadAndPlay(song.path)
  }
}

let _lastBlobUrl: string | null = null  // 追踪上次的 blob URL 以便释放

async function loadAndPlay(filePath: string) {
  if (!audioRef.value) return
  try {
    const result = await window.cherryclip?.fs?.readFileAsBlob(filePath)
    if (result?.data) {
      // 释放旧 blob URL 防止内存泄漏
      if (_lastBlobUrl) {
        URL.revokeObjectURL(_lastBlobUrl)
        _lastBlobUrl = null
      }
      // Base64 → Blob → Object URL
      const byteStr = atob(result.data)
      const bytes = new Uint8Array(byteStr.length)
      for (let i = 0; i < byteStr.length; i++) {
        bytes[i] = byteStr.charCodeAt(i)
      }
      const blob = new Blob([bytes], { type: result.mime || 'audio/mpeg' })
      const url = URL.createObjectURL(blob)
      _lastBlobUrl = url
      audioRef.value.src = url
      audioRef.value.play()
      playing.value = filePath
    }
  } catch (e) {
    console.warn('[Bgm] 播放音频失败:', e)
    toastError('播放音频失败')
    playing.value = ''
  }
}

function showMorePopular() {
  showAllPop.value = true
}

function select(key: string, song: BgmSong) {
  selectedKey.value = key
  selectedSong.value = song
  emit('select', song)
}

function skip() {
  selectedKey.value = null
  selectedSong.value = null
  emit('skip')
}

// 组件卸载时释放 blob URL，防止内存泄漏
onUnmounted(() => {
  if (_lastBlobUrl) {
    URL.revokeObjectURL(_lastBlobUrl)
    _lastBlobUrl = null
  }
})

async function uploadAudio() {
  try {
    const paths = await window.cherryclip?.dialogs.openFiles({
      filters: [{ name: '音频文件', extensions: ['mp3', 'wav', 'm4a', 'flac', 'ogg', 'aac'] }]
    })
    if (paths && paths.length > 0) {
      emit('upload', 'audio', paths)
    }
  } catch (e) {
    console.warn('[Bgm] 上传音频失败:', e)
  }
}

async function uploadVideo() {
  try {
    const paths = await window.cherryclip?.dialogs.openFiles({
      filters: [{ name: '视频文件', extensions: ['mp4', 'mov', 'avi', 'mkv', 'webm'] }]
    })
    if (paths && paths.length > 0) {
      emit('upload', 'video', paths)
    }
  } catch (e) {
    console.warn('[Bgm] 上传视频失败:', e)
  }
}
</script>

<style scoped>
.bgm-selector {
  background: var(--bg-input-zone);
  border: 1px solid var(--border-card);
  border-radius: var(--radius);
  padding: 14px;
  min-width: 320px;
  max-width: 440px;
}

.bgm-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}
.bgm-icon { font-size: 20px; }
.bgm-title { font-size: 14px; font-weight: 600; color: var(--text-primary); }
.bgm-subtitle {
  font-size: 12px;
  color: var(--text-muted);
  width: 100%;
  margin-left: 28px;
  line-height: 1.5;
}

.bgm-section { margin-bottom: 14px; }

.section-tag {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 4px;
  display: inline-block;
  margin-bottom: 8px;
}
.section-tag.hot { background: rgba(245, 158, 11, 0.1); color: #F59E0B; }
.section-tag.ai { background: rgba(99, 102, 241, 0.1); color: var(--brand-light); }

.song-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.song-card {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  background: transparent;
  border: 1px solid var(--border-card);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.15s;
}
.song-card:hover { background: var(--bg-hover); border-color: rgba(255,255,255,0.12); }
.song-card.selected {
  background: rgba(99, 102, 241, 0.08);
  border-color: var(--border-active);
}

.btn-play {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--bg-hover);
  border: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  flex-shrink: 0;
  transition: background 0.15s;
  color: var(--text-muted);
}
.btn-play:hover { background: rgba(255,255,255,0.1); color: var(--text-primary); }

.song-info { flex: 1; min-width: 0; }
.song-name { display: block; font-size: 13px; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.song-artist { display: block; font-size: 11px; color: var(--text-muted); }
.song-check { color: var(--brand); font-size: 14px; font-weight: 700; flex-shrink: 0; }

.btn-more {
  width: 100%;
  padding: 6px;
  background: transparent;
  border: 1px dashed var(--border-card);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  font-size: 12px;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.15s;
}
.btn-more:hover { border-color: var(--brand); color: var(--text-secondary); }

/* AI loading */
.ai-loading {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 12px 10px;
}
.loading-dot {
  width: 6px; height: 6px;
  background: var(--brand-light);
  border-radius: 50%;
  animation: bounce 1.4s infinite ease-in-out;
}
.loading-dot:nth-child(2) { animation-delay: -0.16s; }
.loading-dot:nth-child(3) { animation-delay: -0.08s; }
@keyframes bounce {
  0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
  40% { transform: scale(1); opacity: 1; }
}
.loading-text { font-size: 12px; color: var(--text-muted); margin-left: 6px; }

/* Upload */
.upload-section { margin-bottom: 14px; }
.section-divider {
  display: flex;
  align-items: center;
  gap: 12px;
  color: var(--text-muted);
  font-size: 11px;
  margin-bottom: 10px;
}
.section-divider::before,
.section-divider::after {
  content: '';
  flex: 1;
  height: 1px;
  background: var(--border-card);
}
.upload-hint { font-size: 12px; color: var(--text-muted); text-align: center; margin-bottom: 10px; }
.upload-buttons { display: flex; gap: 8px; }
.btn-upload {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 10px;
  background: rgba(255,255,255,0.02);
  border: 1px dashed var(--border-card);
  border-radius: var(--radius);
  color: var(--text-muted);
  font-size: 12px;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.15s;
}
.btn-upload:hover {
  border-color: var(--brand);
  color: var(--text-secondary);
  background: rgba(99, 102, 241, 0.06);
}
.upload-icon { font-size: 16px; }

.bgm-actions { display: flex; gap: 8px; margin-top: 4px; }
.btn-skip {
  flex: 1;
  padding: 8px;
  background: transparent;
  border: 1px solid var(--border-card);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  font-size: 12px;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.15s;
}
.btn-skip:hover { background: var(--bg-card); color: var(--text-secondary); }
</style>
