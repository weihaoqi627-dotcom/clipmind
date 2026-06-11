

<div align="center">
  <img src="build/icon.png" alt="ClipMind Logo" width="128" height="128">
  <h1>ClipMind / 剪意</h1>
  <p><strong>AI 驱动的智能视频剪辑桌面应用</strong></p>
  <p>AI-powered intelligent video editing desktop application</p>

  <p>
    <a href="https://clipmind.cn">官网</a> ·
    <a href="#-快速开始">快速开始</a> ·
    <a href="#-功能特性">功能特性</a> ·
    <a href="#-技术栈">技术栈</a> ·
    <a href="#-隐私政策">隐私政策</a>
  </p>

  <p>
    <img alt="Windows" src="https://img.shields.io/badge/Windows-11%20%7C%2010-00A4EF?logo=windows&logoColor=white">
    <img alt="License" src="https://img.shields.io/badge/license-All%20Rights%20Reserved-red">
  </p>
</div>

---

## 📥 快速开始

### 方式一：Microsoft Store（推荐）
直接在 Store 搜索 **ClipMind** 安装，零拦截，自动更新。

### 方式二：MSI 安装包
从 [Releases](https://github.com/weihaoqi627-dotcom/clipmind/releases/latest) 下载最新版 MSI 安装包。

```bash
# 或直接用命令行下载
curl -L -o ClipMind.msi https://github.com/weihaoqi627-dotcom/clipmind/releases/latest/download/ClipMind-1.0.0.msi
```

### 方式三：网站下载
访问 [clipmind.cn](https://clipmind.cn) 获取下载链接。

---

## ✨ 功能特性

- **AI 导演系统** — 理解视频内容，自动完成素材分割、粗剪编排、节奏控制
- **AI 智能字幕** — 高精度语音识别，自动对齐时间线，支持多语种
- **影视级调色** — 专业色彩方案，一键电影感氛围
- **智能场景检测** — AI 精准识别镜头切换，毫秒级分割
- **专业转场库** — 丰富的动态过渡效果，衔接丝滑流畅
- **动态字幕模板** — 多种动效文字样式，适配不同视频风格
- **Lottie 动画引擎** — 支持海量矢量动画预设，轻松打造高品质动效
- **音频可视化** — 波形、频谱、脉冲等多种特效，声音看得见
- **数据可视化** — 动态图表引擎，让数据讲故事
- **视觉特效** — 粒子系统、光效、3D 场景，效果拉满
- **ProRes 无损输出** — 支持 Alpha 通道，专业流程无缝衔接
- **多格式兼容** — MP4 / MOV / WebM，分辨率自由定义

---

## 🛠 技术栈

| 层 | 技术 |
|------|------|
| 前端 | Electron 35 + Vue 3 + TypeScript + Vite 6 |
| 后端 | Python FastAPI（本机进程） |
| 渲染引擎 | HyperFrames（Chrome Puppeteer → 视频） |
| 打包 | electron-builder 26 |
| 分发 | Microsoft Store / MSI / GitHub Releases |

---

## ⚙️ 开发构建

```bash
# 克隆
git clone https://github.com/weihaoqi627-dotcom/clipmind.git
cd clipmind

# 安装依赖
npm install

# 渲染引擎需要 HyperFrames CLI
npm install -g hyperframes

# 开发模式
npm run dev

# 打包
npm run build:package
```

> 注意：Python 后端（FastAPI）需要 Python 3.10+，会自动打包进 extraResources。

---

## 🔒 隐私政策

ClipMind 优先本地处理，大部分 AI 运算在本地完成。详情见：

- [隐私政策](https://clipmind.cn/privacy.html)

---

<div align="center">
  <p>
    <a href="https://clipmind.cn">clipmind.cn</a> ·
    <a href="mailto:2217142796@qq.com">联系作者</a>
  </p>
  <p>
    © 2026 ClipMind. All rights reserved.
  </p>
</div>
