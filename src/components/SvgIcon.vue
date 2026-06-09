<template>
  <svg
    class="svg-icon"
    :width="size"
    :height="size"
    :viewBox="iconDef.viewBox || '0 0 24 24'"
    :fill="iconDef.fill || 'none'"
    :stroke="iconDef.stroke || 'currentColor'"
    :stroke-width="iconDef.strokeWidth ?? 2"
    :stroke-linecap="iconDef.strokeLinecap || 'round'"
    :stroke-linejoin="iconDef.strokeLinejoin || 'round'"
    xmlns="http://www.w3.org/2000/svg"
  >
    <defs v-if="iconDef._defs" v-html="iconDef._defs"></defs>
    <g :style="iconDef.filterStyle" :transform="iconDef.transform">
      <path v-for="(path, i) in iconDef.paths" :key="i" :d="path" />
      <circle v-for="(c, i) in iconDef.circles" :key="'c'+i" :cx="c[0]" :cy="c[1]" :r="c[2]" />
      <line v-for="(l, i) in iconDef.lines" :key="'l'+i" :x1="l[0]" :y1="l[1]" :x2="l[2]" :y2="l[3]" />
      <polygon v-for="(p, i) in iconDef.polygons" :key="'p'+i" :points="p[0]" />
      <rect v-for="(r, i) in iconDef.rects" :key="'r'+i" :x="r[0]" :y="r[1]" :width="r[2]" :height="r[3]" :rx="r[4] || 0" />
    </g>
  </svg>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  name: string
  size?: number | string
  color?: string
}>(), {
  size: 24,
  color: undefined,
})

interface IconDef {
  viewBox?: string
  fill?: string
  stroke?: string
  strokeWidth?: number
  strokeLinecap?: 'round' | 'square' | 'butt'
  strokeLinejoin?: 'round' | 'miter' | 'bevel'
  paths?: string[]
  circles?: number[][]
  lines?: number[][]
  polygons?: string[][]
  rects?: number[][]
  filterStyle?: Record<string, string>
  transform?: string
  _defs?: string
}

const icons: Record<string, IconDef> = {
  // ── ClipMind Logo（一笔画"鱼"）──
  logo: {
    viewBox: '0 0 60 60',
    strokeWidth: 4.5,
    stroke: '#FFF',
    transform: 'scale(1.35) translate(-7.5, -7.5)',
    paths: [
      'M44 13 C24 15, 14 24, 14 30 C14 36, 22 45, 28 45 L28 18 L34 36 L40 18 L40 45',
    ],
  },

  // ── 素材 / Folder ──
  folder: {
    paths: ['M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z'],
  },

  // ── 工具 / Grid ──
  tools: {
    paths: [
      'M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94z',
    ],
  },

  // ── 预览 / Play ──
  play: {
    paths: ['M5 3l14 9-14 9V3z'],
  },

  // ── 历史 / Clock ──
  history: {
    paths: ['M12 8v4l3 3', 'M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20z'],
  },

  // ── 设置 / Settings ──
  settings: {
    paths: [
      'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
      'M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z',
    ],
  },

  // ── 场景分割 / Film ──
  scene: {
    paths: [
      'M4 4h7v7H4V4z',
      'M13 4h7v7h-7V4z',
      'M4 13h7v7H4v-7z',
      'M13 13h7v7h-7v-7z',
    ],
  },

  // ── 语音转文字 / Mic ──
  transcribe: {
    paths: [
      'M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z',
      'M19 10v2a7 7 0 0 1-14 0v-2',
      'M12 19v4',
      'M8 23h8',
    ],
  },

  // ── 素材分析 / Search ──
  analyze: {
    paths: [
      'M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14z',
      'M21 21l-6-6',
    ],
  },

  // ── 调色 / Palette ──
  color: {
    paths: [
      'M10 4a6 6 0 0 0-6 6v4a6 6 0 0 0 6 6h1a2 2 0 0 0 2-2v-1a2 2 0 0 1 2-2 2 2 0 0 0 2-2v-1a4 4 0 0 1 4-4h.2a.2.2 0 0 0 .2-.2A6 6 0 0 0 14 4h-4z',
    ],
    circles: [[18, 16]],
  },

  // ── 音频 / Speaker ──
  audio: {
    paths: [
      'M9 18V5l12-2v13',
    ],
    circles: [[9, 18], [21, 16]],
  },

  // ── 字幕 / Type ──
  subtitle: {
    paths: [
      'M3 7h18',
      'M3 12h14',
      'M3 17h10',
    ],
  },

  // ── 发送 / Send ──
  send: {
    paths: [
      'M22 2L11 13',
      'M22 2l-7 20-4-9-9-4 20-7z',
    ],
  },

  // ── 加号 / Plus ──
  plus: {
    paths: [
      'M12 5v14',
      'M5 12h14',
    ],
  },

  // ── 关闭 / X ──
  close: {
    paths: [
      'M18 6L6 18',
      'M6 6l12 12',
    ],
  },

  // ── 返回 / Arrow left ──
  back: {
    paths: [
      'M19 12H5',
      'M12 19l-7-7 7-7',
    ],
  },

  // ── 音乐 / Music note ──
  music: {
    paths: [
      'M9 18V5l12-2v13',
    ],
    circles: [[9, 18], [21, 16]],
  },

  // ── 拖拽 / Move ──
  drag: {
    paths: [
      'M12 2v20',
      'M2 12h20',
    ],
    circles: [[12, 6], [12, 18], [6, 12], [18, 12]],
  },

  // ── 状态 / Status ──
  check: {
    paths: ['M20 6L9 17l-5-5'],
  },

  // ── 关于 / Info ──
  info: {
    paths: ['M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z'],
    lines: [[12, 17, 12, 11]],
    circles: [[12, 8, 0.8]],
  },

  // ── 用户 / User ──
  user: {
    paths: [
      'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2',
    ],
    circles: [[12, 7, 4]],
  },

  // ── 导出 / Download ──
  export: {
    paths: [
      'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4',
      'M7 10l5 5 5-5',
      'M12 15V3',
    ],
  },

  // ── 设置 / Gear ──
  gear: {
    paths: [
      'M12 1v2',
      'M12 21v2',
      'M4.22 4.22l1.41 1.41',
      'M18.36 18.36l1.41 1.41',
      'M1 12h2',
      'M21 12h2',
      'M4.22 19.78l1.41-1.41',
      'M18.36 5.64l1.41-1.41',
    ],
    circles: [[12, 12, 3]],
  },

  // ── 暂停 / Stop ──
  stop: {
    rects: [[4, 4, 16, 16, 2]],  // 这里的 rx=2 会被忽略，用下面路径
    paths: ['M6 6h12v12H6z'],
  },

  // ── 草稿 / File ──
  file: {
    paths: [
      'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z',
      'M14 2v6h6',
      'M16 13H8',
      'M16 17H8',
      'M10 9H8',
    ],
  },
}

const iconDef = computed(() => {
  const def = icons[props.name]
  if (!def) return { paths: [] }
  // 如果传了 color，覆盖 stroke（但带渐变的图标不覆盖）
  if (props.color && !def._defs) {
    return { ...def, stroke: props.color }
  }
  return def
})
</script>

<style scoped>
.svg-icon {
  display: inline-block;
  vertical-align: middle;
  flex-shrink: 0;
}
</style>
