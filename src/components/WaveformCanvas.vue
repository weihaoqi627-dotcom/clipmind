<template>
  <div class="waveform-wrap" ref="wrapRef">
    <canvas ref="canvasRef" class="waveform-canvas"></canvas>
    <div
      v-if="currentTime != null && duration > 0"
      class="waveform-playhead"
      :style="{ left: playheadPct + '%' }"
    ></div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'

const props = defineProps<{
  bars: Array<{ t: number; peak: number; rms: number }> | null
  duration: number
  currentTime?: number | null
}>()

const canvasRef = ref<HTMLCanvasElement | null>(null)
const wrapRef = ref<HTMLElement | null>(null)
let _resizeObs: ResizeObserver | null = null

const playheadPct = computed(() => {
  if (props.currentTime == null || props.duration <= 0) return 0
  return Math.min(100, (props.currentTime / props.duration) * 100)
})

function draw() {
  const canvas = canvasRef.value
  if (!canvas || !props.bars || props.bars.length === 0) return

  const dpr = window.devicePixelRatio || 1
  const rect = canvas.getBoundingClientRect()
  canvas.width = rect.width * dpr
  canvas.height = rect.height * dpr
  canvas.style.width = rect.width + 'px'
  canvas.style.height = rect.height + 'px'

  const ctx = canvas.getContext('2d')!
  ctx.scale(dpr, dpr)

  const w = rect.width
  const h = rect.height
  const barW = Math.max(1, w / props.bars.length - 0.5)
  const gap = 0.5

  ctx.clearRect(0, 0, w, h)

  for (let i = 0; i < props.bars.length; i++) {
    const bar = props.bars[i]
    const peakH = bar.peak * h
    const rmsH = bar.rms * h
    const x = i * (barW + gap)

    // RMS（底部较暗）
    ctx.fillStyle = 'rgba(99, 102, 241, 0.25)'
    ctx.fillRect(x, h - rmsH, barW, rmsH)

    // Peak（顶部较亮）
    ctx.fillStyle = 'rgba(99, 102, 241, 0.55)'
    ctx.fillRect(x, h - peakH, barW, peakH)
  }
}

onMounted(() => {
  if (wrapRef.value) {
    _resizeObs = new ResizeObserver(() => draw())
    _resizeObs.observe(wrapRef.value)
  }
  nextTick(draw)
})

onUnmounted(() => {
  _resizeObs?.disconnect()
})

watch(() => props.bars, () => nextTick(draw), { deep: true })
</script>

<style scoped>
.waveform-wrap {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 48px;
}
.waveform-canvas {
  width: 100%;
  height: 100%;
  display: block;
  border-radius: 4px;
}
.waveform-playhead {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  background: #EF4444;
  pointer-events: none;
  z-index: 5;
}
</style>
