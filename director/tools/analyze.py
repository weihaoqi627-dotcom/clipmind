"""
素材分析工具 — 筛选阶段
========================
每个工具只做一件事.AI 通过反复调用这些工具来理解素材,筛选内容.
"""
import json, os, base64, subprocess, tempfile, time, re
from pathlib import Path
from typing import Optional

from director.registry import tool
from director.logging_config import get_logger

log = get_logger("tools.analyze")

# 内部状态:clip 索引
# AI 通过 tool 参数传递 clip_id,这里不做全局状态

_PROJECT_DIR = Path(__file__).parent.parent.parent

# ═══════════════════════════════════════════════════════
#  比例预设(9 种,覆盖常见场景)
# ═══════════════════════════════════════════════════════

RATIO_PRESETS = {
    "原始":   {"width": 0, "height": 0, "desc": "保持原始比例,不裁剪不缩放"},
    "9:16":   {"width": 1080, "height": 1920, "desc": "竖屏(抖音/快手/小红书/视频号)"},
    "16:9":   {"width": 1920, "height": 1080, "desc": "横屏(B站/YouTube/桌面)"},
    "1:1":    {"width": 1080, "height": 1080, "desc": "方形(Instagram/电商主图)"},
    "4:3":    {"width": 1440, "height": 1080, "desc": "传统 4:3(老素材/摄像机)"},
    "3:4":    {"width": 1080, "height": 1440, "desc": "竖屏 3:4(小红书封面/淘宝)"},
    "2.35:1": {"width": 1920, "height": 816, "desc": "宽银幕(电影感)"},
    "21:9":   {"width": 2560, "height": 1080, "desc": "超宽屏(电影/游戏录屏)"},
    "2:1":    {"width": 2160, "height": 1080, "desc": "现代影院 2:1"},
}

def _detect_ratio(w: int, h: int) -> str:
    """根据分辨率推断最接近的标准比例名"""
    if w <= 0 or h <= 0:
        return "未知"
    r = w / h
    pairs = [
        (16/9, "16:9"), (9/16, "9:16"), (1.0, "1:1"),
        (4/3, "4:3"), (3/4, "3:4"),
        (2.35, "2.35:1"), (21/9, "21:9"), (2.0, "2:1"),
    ]
    best, best_diff = "自定义", 999
    for target_r, name in pairs:
        diff = abs(r - target_r)
        if diff < 0.05 and diff < best_diff:
            best, best_diff = name, diff
    return best


def _parse_json(data) -> any:
    """安全解析 JSON"""
    if data is None or data == "":
        return None
    if isinstance(data, bool):
        return None
    if isinstance(data, str):
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return None
    return data


# ─── 工具函数 ──────────────────────────────────────────────

@tool(
    name="get_video_metadata",
    description="获取视频文件基本信息:分辨率,时长,编码,帧率.",
    phase="analyze",
    category="timeline",
    tags=["video", "metadata", "info"],
    group="素材信息",
)
def get_video_metadata(video_path: str) -> str:
    """获取视频文件基本信息:分辨率,时长,编码,帧率."""
    import re
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"
    info = {"文件": os.path.basename(video_path),
            "大小": f"{os.path.getsize(video_path)/(1024*1024):.0f}MB"}
    try:
        r = subprocess.run(["ffmpeg", "-i", video_path],
                           capture_output=True, timeout=30, check=False)
        output = (r.stdout + r.stderr).decode("utf-8", errors="replace")
    except Exception as e:
        return f"ffmpeg 检测失败: {e}"

    # 从错误输出提取信息(ffmpeg -i 输出到 stderr)
    dur_m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", output)
    if dur_m:
        h, m, s = dur_m.group(1), dur_m.group(2), dur_m.group(3)
        total_s = int(h)*3600 + int(m)*60 + float(s)
        info["时长"] = f"{total_s:.1f}s"

    for line in output.split("\n"):
        if "Stream #" in line:
            if "Video:" in line:
                res_m = re.search(r",\s*(\d{3,})x(\d{3,})", line)
                if res_m:
                    info["分辨率"] = f"{res_m.group(1)}x{res_m.group(2)}"
                fps_m = re.search(r"(\d+(?:\.\d+)?)\s*(?:fps|tb\(r\))", line)
                if fps_m:
                    info["帧率"] = f"{fps_m.group(1)}fps"
                codec_m = re.search(r"Video:\s*(\w+)", line)
                if codec_m:
                    info["视频编码"] = codec_m.group(1)
            elif "Audio:" in line:
                codec_m = re.search(r"Audio:\s*(\w+)", line)
                if codec_m:
                    rate_m = re.search(r"(\d+)\s*Hz", line)
                    rate = rate_m.group(1) if rate_m else "?"
                    info["音频"] = f"{codec_m.group(1)} {rate}Hz"

    return json.dumps(info, ensure_ascii=False, indent=2)


def _has_sentence_boundary(chunk_text: str, next_word: str) -> bool:
    """
    判断 chunk 末尾是否构成断句边界.
    Whisper 输出的中文通常不带标点,所以用语气词 + 连词模式推断断句.
    """
    if not chunk_text or not next_word:
        return False

    # 句子结束标点
    end_punct = '.!?;!?;'
    if chunk_text[-1] in end_punct:
        return True

    # 句子末尾语气词
    particles = '啊吗呢吧呀嘛哦嗯啦咧'
    # 下一句开头连词(排除"这那你"等句中常见字,减少误判)
    starters = '但可是然而不过而且并且所以因此于是接着然后或者另外对'

    # 长 chunk(>=5字)且末尾语气词 + 下一词首字是连词 -> 断句
    if len(chunk_text) >= 5 and chunk_text[-1] in particles:
        return next_word[0] in starters

    return False


def _refine_segments(segments: list) -> list:
    """
    二次断句:扫描每段文本中的标点和语气词+连词模式,按比例切分时间.
    不影响邻近段的时间边界(不等同于 inline 断句,不会 cascading).
    """
    end_punct = set('.!?.!?')
    particles = '啊吗呢吧呀嘛哦嗯啦咧'
    starters = '但可是然而不过而且并且所以因此于是接着然后或者另外对'

    refined = []
    for seg in segments:
        text = seg["text"]
        start_t = seg["start"]
        end_t = seg["end"]
        duration = end_t - start_t

        # 找断句点:标点 or 语气词+连词
        split_positions = []
        for i, ch in enumerate(text):
            if i >= len(text) - 1:
                break
            next_ch = text[i + 1]
            # 标点
            if ch in end_punct:
                split_positions.append(i + 1)
            # 语气词 + 连词(chunk 至少 5 字才断,避免过短分段)
            elif len(text) >= 5 and ch in particles and next_ch in starters:
                split_positions.append(i + 1)

        if not split_positions:
            refined.append(seg)
            continue

        # 去重(语气词+连词可能和标点重合)
        split_positions = sorted(set(split_positions))

        # 按断句点切分,比例估算时间
        prev = 0
        for sp in split_positions:
            chunk_text = text[prev:sp].strip()
            if chunk_text:
                frac = sp / len(text)
                refined.append({
                    "start": round(start_t + duration * (prev / len(text)), 1),
                    "end": round(start_t + duration * frac, 1),
                    "text": chunk_text,
                })
            prev = sp
        remainder = text[prev:].strip()
        if remainder:
            refined.append({
                "start": round(start_t + duration * (prev / len(text)), 1),
                "end": round(end_t, 1),
                "text": remainder,
            })

    return refined if refined else segments


@tool(
    name="get_asr_transcript",
    description="获取语音转写文本(AI 直接听音频转写).提取音频 -> qwen-omni-turbo 转写 -> 返回 JSON 格式文字(含时间戳).传 segment_index 返回单段文本.",
    phase="analyze",
    category="timeline",
    tags=["asr", "transcript", "speech"],
    group="语音与转写",
)
def get_asr_transcript(video_path: str, segment_index: int = -1,
                       draft_id: str = "") -> str:
    """
    获取语音转写文本(AI 直接听音频转写).
    提取音频 -> qwen-omni-turbo 转写 -> 返回 JSON 格式文字(含时间戳).
    传 segment_index 返回单段文本.
    传 draft_id 将转录结果持久化到草稿(后续切气口/文字剪辑依赖此数据).
    """
    if not os.path.exists(video_path):
        return "文件不存在"

    # 用 ffmpeg 提取完整音频(WAV 16kHz mono)
    tmp_wav = os.path.join(_PROJECT_DIR, "_tmp_asr", "audio_full.wav")
    os.makedirs(os.path.dirname(tmp_wav), exist_ok=True)
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            tmp_wav
        ], capture_output=True, timeout=120, check=False)

        if not os.path.exists(tmp_wav) or os.path.getsize(tmp_wav) == 0:
            return "音频提取失败"
    except Exception as e:
        return f"音频提取失败: {e}"

    # 获取 API key
    api_key = os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        try:
            from director.config import get_api_key as _cfg_get_key
            api_key = _cfg_get_key()
        except Exception:
            pass
    if not api_key:
        return "API Key 未配置"

    import requests
    _base = os.environ.get("DASHSCOPE_API_BASE", "https://dashscope.aliyuncs.com")
    api_url = f"{_base}/api/v1/services/aigc/multimodal-generation/generation"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    # 音频大小估算(16kHz mono 16bit = 32KB/s)
    audio_size = os.path.getsize(tmp_wav)
    audio_duration = audio_size / 32000  # 秒
    max_chunk_dur = 60  # 每段最长 60 秒

    all_segments = []

    if audio_duration <= max_chunk_dur:
        # 不分段:整段发送
        with open(tmp_wav, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        segments = _call_audio_transcribe(b64, 0, api_url, headers)
        all_segments.extend(segments)
    else:
        # 分段处理(长音频)
        import math
        num_chunks = math.ceil(audio_duration / max_chunk_dur)
        for i in range(num_chunks):
            chunk_start = i * max_chunk_dur
            chunk_file = os.path.join(_PROJECT_DIR, "_tmp_asr", f"chunk_{i}.wav")
            subprocess.run([
                "ffmpeg", "-y", "-ss", str(chunk_start),
                "-i", tmp_wav,
                "-t", str(max_chunk_dur),
                "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                chunk_file,
            ], capture_output=True, timeout=60, check=False)

            if os.path.exists(chunk_file) and os.path.getsize(chunk_file) > 0:
                with open(chunk_file, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                segments = _call_audio_transcribe(b64, chunk_start, api_url, headers)
                all_segments.extend(segments)
                try:
                    os.remove(chunk_file)
                except:
                    pass

    # 清理
    try:
        os.remove(tmp_wav)
    except:
        pass

    if not all_segments:
        return "(未检测到语音)"

    # ── 持久化到草稿 ──
    if draft_id:
        try:
            from director.draft import Draft
            d = Draft(draft_id)
            if d.load():
                indexed = []
                for i, seg in enumerate(all_segments):
                    indexed.append({
                        "index": i,
                        "start": seg["start"],
                        "end": seg["end"],
                        "text": seg["text"],
                    })
                d.set_transcript(video_path, indexed)
                # 自动生成字幕(格式与 add_subtitles 完全一致)
                subtitle_segments = [
                    {"start": seg["start"], "end": seg["end"], "text": seg["text"]}
                    for seg in all_segments
                ]
                d.set_subtitles(subtitle_segments)
                d.save("转录完成(含字幕)")
        except Exception as e:
            pass  # 转录本身成功,持久化失败不阻塞

    if segment_index >= 0 and segment_index < len(all_segments):
        seg = all_segments[segment_index]
        return f"[{segment_index}] {seg['start']:.1f}s-{seg['end']:.1f}s: {seg['text']}"

    return json.dumps(all_segments, ensure_ascii=False, indent=2)


def _call_audio_transcribe(audio_b64: str, time_offset: float,
                            api_url: str, headers: dict) -> list:
    """调用 qwen-omni-turbo 转写一段音频,返回 [{"start", "end", "text"}, ...]"""
    import requests, re

    payload = {
        "model": "qwen-omni-turbo",
        "input": {
            "messages": [{
                "role": "user",
                "content": [
                    {"audio": f"data:audio/wav;base64,{audio_b64}"},
                    {"text": "转写这段语音.每句一行,格式:开始秒-结束秒: 文字."
                             "不要加任何额外说明,只输出转写结果."},
                ]
            }]
        },
        "parameters": {"max_tokens": 2000},  # API 上限 2048
    }

    try:
        resp = requests.post(api_url, headers=headers, json=payload, timeout=180)
        data = resp.json()
    except Exception as e:
        return []

    choices = data.get("output", {}).get("choices", [])
    if not choices:
        # 检查 API 是否返回了错误信息
        err_msg = data.get("output", {}).get("message", data.get("message", ""))
        if err_msg:
            log.warning("ASR API 返回无结果,错误: %s", err_msg)
        else:
            log.warning("ASR API 返回空 choices,完整响应: %s", str(data)[:500])
        return []

    content_list = choices[0].get("message", {}).get("content", [])
    text = ""
    for c in content_list:
        if "text" in c and c["text"]:
            text = c["text"]
    if not text:
        return []

    segments = []
    raw_lines = text.strip().split("\n")
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        # 匹配格式: "1.96 - 5.74: 文字" 或 "1.96-5.74:文字"
        m = re.match(r"([\d.]+)\s*[-~]\s*([\d.]+)[:\s]\s*(.+)", line)
        if m:
            try:
                start = float(m.group(1)) + time_offset
                end = float(m.group(2)) + time_offset
                seg_text = m.group(3).strip().rstrip(".,!?.!?")
                if seg_text and len(seg_text) > 0:
                    segments.append({
                        "start": round(start, 1),
                        "end": round(end, 1),
                        "text": seg_text,
                    })
            except ValueError:
                continue

    # 如果正则一行都没解析出来，但 AI 确实返回了文本：
    # 把整个响应作为一段文本，时间范围设为 unknown
    if not segments and text.strip():
        segments.append({
            "start": round(time_offset, 1),
            "end": round(time_offset + 60, 1),
            "text": text.strip()[:2000],
        })

    return segments


def _get_video_duration(video_path: str) -> float:
    """用 ffmpeg -i 获取视频总时长(秒)"""
    try:
        r = subprocess.run(["ffmpeg", "-i", video_path],
                           capture_output=True, timeout=30, check=False)
        output = (r.stdout + r.stderr).decode("utf-8", errors="replace")
        m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", output)
        if m:
            h, m_s, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
            return h * 3600 + m_s * 60 + s
    except:
        pass
    return 0.0


@tool(
    name="extract_clips",
    description="按场景(镜头切换)分段,返回片段列表.用 ffmpeg scene filter 检测真实镜头切换点,合并过短片段.AI 可以逐个看这些片段决定取舍.",
    phase="analyze",
    category="timeline",
    tags=["scene", "segment", "split"],
    group="裁切与提取",
)
def extract_clips(video_path: str, min_duration: float = 600.0) -> str:
    """
    按场景(镜头切换)分段,返回片段列表.
    用 ffmpeg scene filter 检测真实镜头切换点,合并过短片段.
    AI 可以逐个看这些片段决定取舍.

    Returns:
        JSON: [
            {"id": 0, "start": 0, "end": 32.5, "duration": 32.5, "desc": "...", "text": "..."},
            ...
        ]
    """
    if not os.path.exists(video_path):
        return json.dumps([])

    # 获取总时长
    total_dur = _get_video_duration(video_path)
    if total_dur <= 0:
        return json.dumps([])

    # 15分钟以内的视频不用切,整段当1个clip
    if total_dur <= 900:
        return json.dumps([{
            "id": 0,
            "start": 0.0,
            "end": round(total_dur, 1),
            "duration": round(total_dur, 1),
        }], ensure_ascii=False, indent=2)

    # 用 scene.py 的 detect_scenes 检测真实镜头切点
    from director.tools.scene import detect_scenes
    scenes_json = detect_scenes(
        video_path=video_path,
        threshold=0.7,             # 高阈值,只检测明显的镜头切换
        min_scene_duration=min_duration,  # 合并过短片段到最小时长
    )
    try:
        scenes_data = json.loads(scenes_json) if isinstance(scenes_json, str) else scenes_json
    except (json.JSONDecodeError, TypeError):
        scenes_data = {}
    scenes = scenes_data.get("scenes", [])

    if not scenes or "error" in scenes_data:
        return json.dumps([])

    clips = []
    for s in scenes:
        dur = s["end"] - s["start"]
        if dur >= min_duration:
            clips.append({
                "id": s["index"],
                "start": round(s["start"], 1),
                "end": round(s["end"], 1),
                "duration": round(dur, 1),
            })

    # 如果 scene 检测没切出足够的片段(口播等固定机位素材),
    # 回退按时长分割
    if len(clips) <= 1:
        if total_dur <= 0:
            return json.dumps([])
        clips = []
        start = 0.0
        clip_id = 0
        while start < total_dur:
            end = min(start + min_duration, total_dur)
            clips.append({
                "id": clip_id,
                "start": round(start, 1),
                "end": round(end, 1),
                "duration": round(end - start, 1),
            })
            clip_id += 1
            start = end

    return json.dumps(clips, ensure_ascii=False, indent=2)


@tool(
    name="screen_clip",
    description="看一段素材的视觉画面.返回该段时间内的画面分析.",
    phase="analyze",
    category="timeline",
    tags=["preview", "analyze", "clip"],
    group="裁切与提取",
)
def screen_clip(video_path: str, clip_id: int, duration: float = 30.0) -> str:
    """
    看一段素材的视觉画面.返回该段时间内的画面分析.

    Args:
        video_path: 视频文件路径
        clip_id: 片段ID(从 extract_clips 获取)
        duration: 片段时长(秒),最短 30 秒
    """
    if not video_path:
        return json.dumps({"error": "video_path 为空"}, ensure_ascii=False)
    if not os.path.exists(video_path):
        return json.dumps({"error": f"视频文件不存在: {video_path}"}, ensure_ascii=False)
    actual_dur = max(duration, 30.0)
    # 安全上限: 单次 VL 分析不超过 30s,超过的话只取前 30s
    # 更长视频由 agent_loop 多次调 screen_clip(不同 clip_id) 分别分析
    VL_MAX_DURATION = 30.0
    if actual_dur > VL_MAX_DURATION:
        actual_dur = VL_MAX_DURATION
    # 计算该片段的时间范围
    segments = _get_segments_cached(video_path)
    if clip_id < 0 or clip_id >= len(segments):
        return f"无效 clip_id: {clip_id},共有 {len(segments)} 个片段"
    seg = segments[clip_id]
    start, end = seg["start"], seg["end"]
    if end - start < 1:
        return f"片段太短: {end-start:.1f}s"
    # 走 Canvas 预览通路 — 浏览器实时渲染,不碰 ffmpeg
    from director.tools.preview import _get_backend
    backend = _get_backend()
    if not backend:
        return "预览后端不可用(缺少浏览器渲染环境),请通过 Electron 应用使用"
    clip_data = backend(video_path, start, min(end, start + actual_dur))
    if not clip_data or len(clip_data) < 100:
        return "预览后端返回数据为空"
    # base64 -> qwen3.6-plus 视频分析
    try:
        b64 = base64.b64encode(clip_data).decode()
        api_key = os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            try:
                from director.config import get_api_key as _cfg_get_key
                api_key = _cfg_get_key()
            except Exception:
                pass
        if not api_key:
            return "API Key 未配置"
        from openai import OpenAI
        import httpx
        base_url = os.environ.get("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        client = OpenAI(api_key=api_key,
                        base_url=base_url,
                        max_retries=0, timeout=httpx.Timeout(120.0, connect=10.0, read=120.0))
        resp = client.chat.completions.create(
            model="qwen3.6-plus",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "分析这段视频,按以下格式输出(不要额外说明,不要分段散文):\n"
                             "场景类型: <室内/户外/舞台/...>\n"
                             "主体: <人物/物体/空镜>\n"
                             "构图: <特写/中景/全景/...>\n"
                             "光线: <明亮/昏暗/逆光/...>\n"
                             "画面质量: <高/中/低> <原因>\n"
                             "画面问题: <抖动/过曝/模糊/无>\n"
                             "剪辑建议: <保留/舍弃> <理由>\n"
                             "可用手法: <转场/调色/动效方向>"},
                    {"type": "video_url", "video_url": {"url": f"data:video/webm;base64,{b64}"}, "fps": 2},
                ]
            }],
            max_tokens=20000,
        )
        analysis = resp.choices[0].message.content or ""
    except Exception as e:
        analysis = f"VL分析失败: {e}"
    info = f"片段 {clip_id}: {seg['start']:.1f}s -> {seg['end']:.1f}s (时长 {seg['end']-seg['start']:.0f}s)\n"
    if seg.get("text"):
        info += f"语音: \"{seg['text'][:200]}\"\n"
    info += f"画面: {analysis}"
    return info


@tool(
    name="mark_keep",
    description="确认保留某段素材.返回当前保留列表.",
    phase="analyze",
    category="timeline",
    tags=["mark", "keep", "select"],
    group="裁切与提取",
)
def mark_keep(video_path: str, clip_id: int, reason: str = "") -> str:
    """确认保留某段素材.返回当前保留列表."""
    if not video_path:
        return json.dumps({"error": "video_path 为空"}, ensure_ascii=False)
    if not os.path.exists(video_path):
        return json.dumps({"error": f"视频文件不存在: {video_path}"}, ensure_ascii=False)
    segments = _get_segments_cached(video_path)
    if clip_id < 0 or clip_id >= len(segments):
        return f"无效 clip_id: {clip_id}"
    seg = segments[clip_id]
    seg["status"] = "keep"
    seg["reason"] = reason
    kept = [s for s in segments if s.get("status") == "keep"]
    return f"✅ 片段 {clip_id} 已保留{' (' + reason + ')' if reason else ''}\n" \
           f"当前保留: {len(kept)} 个片段: {[s['id'] for s in kept]}"


@tool(
    name="mark_discard",
    description="弃用某段素材(从剪辑项目中移出).",
    phase="analyze",
    category="timeline",
    tags=["mark", "discard", "reject"],
    group="裁切与提取",
)
def mark_discard(video_path: str, clip_id: int, reason: str = "") -> str:
    """弃用某段素材(从剪辑项目中移出)."""
    if not video_path:
        return json.dumps({"error": "video_path 为空"}, ensure_ascii=False)
    if not os.path.exists(video_path):
        return json.dumps({"error": f"视频文件不存在: {video_path}"}, ensure_ascii=False)
    segments = _get_segments_cached(video_path)
    if clip_id < 0 or clip_id >= len(segments):
        return f"无效 clip_id: {clip_id}"
    seg = segments[clip_id]
    seg["status"] = "discard"
    seg["reason"] = reason
    kept = [s for s in segments if s.get("status") == "keep"]
    disc = [s for s in segments if s.get("status") == "discard"]
    return f"❌ 片段 {clip_id} 已弃用{' (' + reason + ')' if reason else ''}\n" \
           f"当前: {len(kept)} 保留, {len(disc)} 弃用"


@tool(
    name="mark_uncertain",
    description="标记为存疑,回头再看.",
    phase="analyze",
    category="timeline",
    tags=["mark", "uncertain", "review"],
    group="裁切与提取",
)
def mark_uncertain(video_path: str, clip_id: int, question: str = "") -> str:
    """标记为存疑,回头再看."""
    if not video_path:
        return json.dumps({"error": "video_path 为空"}, ensure_ascii=False)
    if not os.path.exists(video_path):
        return json.dumps({"error": f"视频文件不存在: {video_path}"}, ensure_ascii=False)
    segments = _get_segments_cached(video_path)
    if clip_id < 0 or clip_id >= len(segments):
        return f"无效 clip_id: {clip_id}"
    seg = segments[clip_id]
    seg["status"] = "uncertain"
    seg["question"] = question
    uncertain = [s for s in segments if s.get("status") == "uncertain"]
    return f"❓ 片段 {clip_id} 标记为存疑{' (' + question + ')' if question else ''}\n" \
           f"存疑: {len(uncertain)} 个片段: {[s['id'] for s in uncertain]}"


@tool(
    name="show_current_clips",
    description="查看当前所有片段的状态.",
    phase="analyze",
    category="timeline",
    tags=["status", "clips", "overview"],
    group="素材信息",
)
def show_current_clips(video_path: str) -> str:
    """查看当前所有片段的状态."""
    if not video_path:
        return json.dumps({"error": "video_path 为空"}, ensure_ascii=False)
    if not os.path.exists(video_path):
        return json.dumps({"error": f"视频文件不存在: {video_path}"}, ensure_ascii=False)
    segments = _get_segments_cached(video_path)
    if not segments:
        return "(暂无片段数据)"
    lines = ["当前片段状态:"]
    for s in segments:
        status = s.get("status", "待分析")
        icon = {"keep": "✅", "discard": "❌", "uncertain": "❓"}.get(status, "⬜")
        reason = s.get("reason", s.get("question", ""))
        extra = f" - {reason[:80]}" if reason else ""
        text = s.get("text", "")[:50]
        lines.append(f"  {icon} [{s['id']}] {s['start']:.0f}s-{s['end']:.0f}s ({s['end']-s['start']:.0f}s){extra}")
        if text:
            lines.append(f"      \"{text}\"")
    kept = sum(1 for s in segments if s.get("status") == "keep")
    disc = sum(1 for s in segments if s.get("status") == "discard")
    unc = sum(1 for s in segments if s.get("status") == "uncertain")
    lines.append(f"\n统计: ✅ {kept} 保留 | ❌ {disc} 弃用 | ❓ {unc} 存疑 | ⬜ {len(segments)-kept-disc-unc} 待分析")
    return "\n".join(lines)


# ─── 缓存 ──────────────────────────────────────────────────

_CLIP_CACHE: dict = {}

def _get_segments_cached(video_path: str) -> list[dict]:
    """获取缓存的片段列表.首次调用会重新提取并缓存."""
    global _CLIP_CACHE
    if video_path in _CLIP_CACHE:
        return _CLIP_CACHE[video_path]
    # 从 extract_clips 结果重建
    raw = extract_clips(video_path)
    try:
        segments = json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError):
        segments = []
    _CLIP_CACHE[video_path] = segments
    return segments


# ─── 比例分析工具 ──────────────────────────────────────────

@tool(
    name="list_ratios",
    description="列出所有可用的画面比例预设(9种,覆盖常见场景)",
    phase="analyze",
    category="timeline",
    tags=["ratio", "preset"],
    group="素材信息",
)
def list_ratios() -> str:
    """列出 9 种画面比例预设"""
    lines = []
    for name, p in RATIO_PRESETS.items():
        if name == "原始":
            lines.append(f"- 原始: {p['desc']}")
        else:
            lines.append(f"- {name}: {p['width']}x{p['height']} ({p['desc']})")
    return "可用比例:\n" + "\n".join(lines)


@tool(
    name="analyze_material_ratios",
    description=(
        "分析素材比例分布.传入素材路径列表,返回每个素材的比例归类,"
        "主流比例(数量最多的),少数比例素材列表及处理建议(PIP小窗/跳过/裁剪填充)."
        "Agent 应在分析素材后调用此工具决定输出比例."
    ),
    phase="analyze",
    category="timeline",
    tags=["ratio", "analyze", "material"],
    group="素材信息",
)
def analyze_material_ratios(video_paths_json: str) -> str:
    """
    分析素材比例分布并推荐目标比例.

    Args:
        video_paths_json: 素材路径 JSON 数组 ["path1", "path2", ...]

    Returns:
        比例分析结果,包含分布,推荐目标,少数素材处理建议
    """
    paths = _parse_json(video_paths_json)
    if not paths:
        return "素材列表为空"

    results = []
    ratio_groups = {}

    for p in paths:
        if not os.path.exists(p):
            results.append({"path": p, "error": "文件不存在"})
            continue

        try:
            r = subprocess.run(["ffmpeg", "-i", p],
                             capture_output=True, timeout=30, check=False)
            out = (r.stdout + r.stderr).decode("utf-8", errors="replace")
        except Exception as e:
            results.append({"path": p, "error": str(e)})
            continue

        res_m = re.search(r",\s*(\d{3,})x(\d{3,})", out)
        if not res_m:
            results.append({"path": p, "error": "无法探测分辨率"})
            continue

        w, h = int(res_m.group(1)), int(res_m.group(2))
        ratio_name = _detect_ratio(w, h)
        results.append({
            "path": p,
            "name": os.path.basename(p),
            "resolution": f"{w}x{h}",
            "ratio": ratio_name,
        })

        ratio_groups.setdefault(ratio_name, []).append(p)

    if not results:
        return "❌ 无法分析任何素材"

    total = len(results)
    distribution = []
    for rname, rpaths in sorted(ratio_groups.items(), key=lambda x: -len(x[1])):
        pct = len(rpaths) / total * 100
        distribution.append({
            "ratio": rname,
            "count": len(rpaths),
            "percent": round(pct, 1),
            "paths": [os.path.basename(x) for x in rpaths],
        })

    majority = distribution[0]
    minority = distribution[1:] if len(distribution) > 1 else []

    out_lines = ["📊 **素材比例分析**\n"]
    out_lines.append("| 比例 | 数量 | 占比 |")
    out_lines.append("|------|------|------|")
    for d in distribution:
        out_lines.append(f"| {d['ratio']} | {d['count']}个 | {d['percent']}% |")

    out_lines.append("\n**逐素材**:")
    for r in results:
        if "error" in r:
            out_lines.append(f"- ❌ {r['name']}: {r['error']}")
        else:
            tag = " ⭐主流" if r["ratio"] == majority["ratio"] else " ⚠少数"
            out_lines.append(f"- {r['name']}: {r['resolution']} -> {r['ratio']}{tag}")

    out_lines.append(f"\n**推荐输出比例: {majority['ratio']}**({majority['count']}/{total} 素材,{majority['percent']}%)")

    if minority:
        out_lines.append(f"\n⚠ **{len(minority)} 种少数比例需处理:**")
        for m in minority:
            names = m["paths"]
            out_lines.append(f"- {m['ratio']}({m['count']}个): {', '.join(names)}")
        out_lines.append("\n**处理建议:**")
        out_lines.append("1. 少数比例素材裁剪或用 `fill_mode=blur` 模糊填充")
        out_lines.append("2. 少数比例素材放入 PIP 小窗(`add_overlay` + 缩小 + 定位到角落)")
        out_lines.append("3. 直接弃用少数比例素材")
    else:
        out_lines.append("\n✅ 所有素材比例一致,无需额外处理.")

    return "\n".join(out_lines)


# 工具已通过 @tool 装饰器自动注册到 Registry
