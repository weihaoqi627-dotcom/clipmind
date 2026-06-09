"""
视频观看工具 — DashScope 原生 SDK 直接看视频
===============================================

传本地视频路径给 qwen-vl-max，SDK 自动处理上传和推理.
不走代理，直接用企业 API Key 调 DashScope 原生接口.

用法(AI 视角,不需要 AI 知道内部实现):
    watch_video(video_path="/path/to/video.mp4")
    -> 返回结构化场景分析结果
"""

import os, json, time, threading, subprocess, base64, re, hashlib
from pathlib import Path

from director.registry import tool
from director.logging_config import get_logger

log = get_logger("tools.watch")

# ─── VL 结果缓存（SHA256 → JSON，避免同一文件重复分析）────────
_VL_CACHE_TTL = 86400 * 7   # 7 天
_VL_CACHE_MAX = 500         # 最多 500 条

def _sha256_file(path: str) -> str:
    """流式计算文件 SHA256（64KB 分块，大文件不爆内存）"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()

def _vl_cache_path(work_dir: str) -> str:
    cache_dir = os.path.join(work_dir, "_cache")
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, "vl_cache.json")

def _vl_cache_load(cache_path: str) -> dict:
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _vl_cache_save(cache_path: str, cache: dict):
    """原子写入 cache（写 tmp → rename）"""
    tmp = cache_path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(tmp, cache_path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass

def _vl_cache_get(cache: dict, sha: str) -> str | None:
    """返回缓存结果（字符串），过期或缺失返回 None"""
    entry = cache.get(sha)
    if not entry:
        return None
    if time.time() - entry.get("timestamp", 0) > _VL_CACHE_TTL:
        return None
    return entry.get("result")

def _vl_cache_put(cache: dict, sha: str, result: str, video_path: str):
    """写入缓存 + 超过上限时淘汰最旧的 50 条"""
    cache[sha] = {
        "result": result,
        "timestamp": time.time(),
        "video_path": video_path,
    }
    if len(cache) > _VL_CACHE_MAX:
        # 按时间戳排序，保留最新的 _VL_CACHE_MAX 条
        sorted_items = sorted(cache.items(), key=lambda kv: kv[1].get("timestamp", 0))
        for k, _ in sorted_items[:50]:
            cache.pop(k, None)

# ─── OSS 上传并发控制 ────────────────────────────────────────
# 已移除 semaphore 限制 — 全并发上传，DashScope SDK 内部会处理重试。

# ─── 多轮 VL 分析提示词 ───────────────────────────────────
# 每一轮聚焦不同维度，三轮结果合并为统一结构化 JSON

PROMPT_SCENE = """请分析这个视频的场景结构，输出 JSON 数组（不要额外解释）。

每段一条，格式：
{
  "start": 起始秒数,
  "end": 结束秒数,
  "content": "这一段在发生什么（具体描述画面+语音内容）",
  "quality": "good" | "ok" | "poor",
  "keep": true | false,
  "reason": "为什么保留/丢弃",
  "speech_summary": "如果有语音，摘录关键语句"
}

额外输出 summary JSON：
{
  "total_duration": 总秒数,
  "keep_count": 保留段数,
  "total_keep_duration": 保留总时长,
  "has_speech": true/false,
  "has_face": true/false
}

先输出场景数组，单独一行输出 "---SUMMARY---"，再输出 summary。"""

PROMPT_QUALITY = """请评估这个视频的视觉质量。输出 JSON（不要额外解释）：

{
  "lighting": "excellent"|"good"|"fair"|"poor",
  "lighting_notes": "",
  "composition": "excellent"|"good"|"fair"|"poor",
  "composition_notes": "",
  "motion_blur": "none"|"slight"|"moderate"|"severe",
  "noise_level": "none"|"low"|"moderate"|"high",
  "overall_score": <1-10>,
  "issues": ["问题1", "问题2"],
  "color_tone": "warm"|"cool"|"neutral"|"mixed",
  "stability": "stable"|"shaky"|"very_shaky"
}

如果你无法准确评估某些维度，用 null 标记而不是乱猜。"""

PROMPT_OVERLAY = """请检测这个视频中是否存在文字、叠加层(overlay)或UI元素。输出 JSON（不要额外解释）：

{
  "has_text_overlay": true/false,
  "text_regions": [
    {
      "timestamp": 秒数,
      "approximate_content": "可读的文字内容（如果可见）",
      "type": "subtitle"|"title"|"watermark"|"ui"|"unknown"
    }
  ],
  "has_ui_elements": true/false,
  "coverage_percentage": <0-100>,
  "notes": "其他发现"
}

大部分视频可能没有任何文字或叠层，如实 report 即可。"""


_MAX_VIDEO_DIM = 1280  # 最长边不超过 1280px（720p 水平），防止服务端暴力降采样


def _get_video_resolution(video_path: str) -> tuple:
    """用 ffmpeg -i 输出解析视频分辨率（兼容无真 ffprobe 的环境）.

    Returns:
        (width, height) 或 (0, 0)
    """
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", video_path],
            capture_output=True, timeout=30,
        )
        output = (r.stdout + r.stderr).decode("utf-8", errors="replace")
        for line in output.split("\n"):
            if "Stream #" in line and "Video:" in line:
                m = re.search(r",\s*(\d{3,})x(\d{3,})", line)
                if m:
                    return int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    return 0, 0


def _prepare_video_for_vl(video_path: str, work_dir: str) -> str:
    """预处理视频到合适的 VL 分辨率.

    DashScope 服务端收到视频后会自行降采样，原始 1920x3414 的竖屏视频
    会被压到看不清细节。此函数用 ffmpeg 先缩放到最长边 ≤1280px，
    再传给 API，保证服务端看到的画面质量。

    同时做有损压缩（CRF 28），大幅减小文件体积，避免 OSS 上传被安全软件拦截.

    Args:
        video_path: 原始视频路径
        work_dir: 工作目录（放临时文件）

    Returns:
        预处理后的视频路径（与原始路径相同时未做处理）
    """
    # 检查分辨率（用兼容的解析方式）
    w, h = _get_video_resolution(video_path)
    if w <= 0 or h <= 0:
        log.warning("无法解析视频分辨率，尝试直接压缩: %s", video_path)
        w, h = 1920, 1080  # 假设 Full HD

    # ── 跳过已压缩的文件 ──
    # compress_segments 已经把所有片段压到 720p CRF28，
    # 对于这些已压缩文件直接跳过，避免重复压缩。
    # 阈值：<150MB 且最长边 ≤1280px → 认为已 VL-ready
    try:
        fsize_mb = os.path.getsize(video_path) / 1024 / 1024
        if fsize_mb < 150 and max(w, h) <= _MAX_VIDEO_DIM:
            log.info("VL 预处理跳过: 文件已适合直接分析 (%.1fMB, %dx%d)",
                     fsize_mb, w, h)
            return video_path
    except OSError:
        pass

    # 计算缩放
    longest = max(w, h)
    scale = _MAX_VIDEO_DIM / longest
    new_w = int(w * scale)
    new_h = int(h * scale)
    # 确保偶数（编码要求）
    new_w = new_w if new_w % 2 == 0 else new_w + 1
    new_h = new_h if new_h % 2 == 0 else new_h + 1

    # 输出临时文件
    tmp_dir = os.path.join(work_dir, "_tmp_video")
    os.makedirs(tmp_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(video_path))[0]
    out_path = os.path.join(tmp_dir, f"{base}_vl.mp4")

    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path,
             "-vf", f"scale={new_w}:{new_h}", "-sws_flags", "lanczos",
             "-c:v", "libx264", "-preset", "fast", "-crf", "28",
             out_path],
            capture_output=True, timeout=600, check=True,
        )
        out_size = os.path.getsize(out_path) / 1024 / 1024
        in_size = os.path.getsize(video_path) / 1024 / 1024
        log.info("VL 预处理: %d×%d→%d×%d (%.1fMB→%.1fMB, ratio=%.0f%%)",
                 w, h, new_w, new_h, in_size, out_size, out_size / in_size * 100 if in_size else 0)
        return out_path
    except Exception as e:
        log.warning("VL 预处理失败，使用原始分辨率: %s", e)
        return video_path


def _get_enterprise_api_key() -> str:
    """获取企业 API Key(不是 JWT).

    环境变量 DASHSCOPE_API_KEY 可能被 Runner 覆盖为 JWT，所以不从环境变量读.
    直接从 .env 文件读取原始企业 Key.
    """
    # 先查 .env 文件
    env_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        ".env",
    )
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("DASHSCOPE_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val

    # 回退:config.json 中存储的企业 Key(不一定是当前的)
    try:
        from director.config import CONFIG_FILE
        import json as _json
        if CONFIG_FILE.exists():
            cfg = _json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            key = cfg.get("api_key", "")
            # JWT 以 eyJ 开头,企业 Key 以 sk- 开头
            if key and not key.startswith("eyJ"):
                return key
    except Exception:
        pass

    return ""


def _run_single_turn(messages: list, model: str, api_key: str,
                     timeout: int = 300) -> tuple[str, dict | None]:
    """执行一轮 MultiModalConversation 调用.

    Args:
        messages: 完整 messages 列表（包含历史）
        model: 模型名
        api_key: DashScope API Key
        timeout: 超时秒数

    Returns:
        (response_text, assistant_message_dict_or_None)
        assistant_message 可用于追加到 messages 进行下一轮.
        调用失败时返回 ("错误: ...", None).
    """
    import dashscope
    dashscope.api_key = api_key
    dashscope.base_http_api_url = "https://dashscope.aliyuncs.com/api/v1"

    try:
        response = dashscope.MultiModalConversation.call(
            model=model,
            messages=messages,
            result_format="message",
        )
        if response and response.status_code == 200:
            text = response.output.choices[0].message.content[0]["text"]
            assistant_msg = response.output.choices[0].message
            return text, assistant_msg
        else:
            err = f"status={getattr(response, 'status_code', 'None')}"
            log.error("DashScope 调用失败: %s", err)
            return f"错误: 视频分析失败 ({err})", None
    except Exception as e:
        log.exception("DashScope 调用异常")
        return f"错误: {type(e).__name__}: {e}", None


def _combine_vl_results(turn_scene: str, turn_quality: str,
                        turn_overlay: str, processed_path: str,
                        orig_path: str) -> str:
    """合并三轮结果为一个结构化 JSON 字符串."""
    combined = {
        "video_analyzed": os.path.basename(orig_path),
        "source_path": orig_path,
        "analysis_timestamp": time.time(),
        "scenes": [],
        "summary": {},
        "quality_assessment": {},
        "overlay_detection": {},
    }

    # 解析 Turn 1：场景（含 summary）
    try:
        if "---SUMMARY---" in turn_scene:
            parts = turn_scene.split("---SUMMARY---")
            scenes_text = parts[0].strip()
            summary_text = parts[1].strip()
        else:
            scenes_text = turn_scene
            summary_text = "{}"

        scenes_parsed = json.loads(scenes_text)
        if isinstance(scenes_parsed, list):
            combined["scenes"] = scenes_parsed
        elif isinstance(scenes_parsed, dict):
            combined["scenes"] = scenes_parsed.get("scenes", scenes_parsed.get("segments", []))

        summary_parsed = json.loads(summary_text)
        if isinstance(summary_parsed, dict):
            combined["summary"] = summary_parsed
    except (json.JSONDecodeError, Exception) as e:
        combined["scenes"] = [{"error": f"场景解析失败: {e}", "raw": turn_scene[:500]}]

    # 解析 Turn 2：视觉质量
    try:
        quality = json.loads(turn_quality)
        if isinstance(quality, dict):
            combined["quality_assessment"] = quality
    except (json.JSONDecodeError, Exception):
        combined["quality_assessment"] = {"error": "质量评估解析失败", "raw": turn_quality[:300]}

    # 解析 Turn 3：文字/叠层
    try:
        overlay = json.loads(turn_overlay)
        if isinstance(overlay, dict):
            combined["overlay_detection"] = overlay
    except (json.JSONDecodeError, Exception):
        combined["overlay_detection"] = {"error": "叠层检测解析失败", "raw": turn_overlay[:300]}

    return json.dumps(combined, ensure_ascii=False, indent=2)


def _call_dashscope_vision(video_path: str, prompt: str,
                           fps: float = 2.0) -> str:
    """
    用 DashScope SDK 原生接口直接看本地视频（多轮问答 + 缓存）.

    与旧的单轮调用兼容，但内部做三轮分析：
      1. 场景结构 + 内容描述
      2. 视觉质量评估
      3. 文字/叠层检测
    结果缓存到 <work_dir>/_cache/vl_cache.json，同一文件不重复分析.

    Args:
        video_path: 视频文件路径
        prompt: （兼容旧接口）保留参数，实际使用固定多轮提示词
        fps: 采样帧率

    Returns:
        结构化 JSON 字符串
    """

    api_key = _get_enterprise_api_key()
    if not api_key:
        return "错误: 企业 API Key 未配置(请检查 .env 文件中的 DASHSCOPE_API_KEY)"

    # 模型名（优先环境变量 VL_MODEL > 模型配置 > 默认）
    model = os.environ.get("VL_MODEL", "")
    if not model:
        model = os.environ.get("LLM_MODEL", "")
    if not model:
        model = _get_vision_model()

    from director.tools.cut import _find_draft_dir
    work_dir = _find_draft_dir()

    # 预处理视频到合理分辨率
    if not os.path.exists(video_path):
        return "错误: 视频文件不存在"
    fsize_mb = os.path.getsize(video_path) / 1024 / 1024
    if fsize_mb > 1500:  # >1.5GB，找压缩版
        try:
            from director.pipeline_state import PipelineState
            state = PipelineState(work_dir)
            for seg in state.segments:
                src = seg.get("source_path", seg.get("source", ""))
                cp = seg.get("compressed_path", "")
                if cp and os.path.exists(cp) and src:
                    src_norm = os.path.normpath(os.path.abspath(src)).lower()
                    vp_norm = os.path.normpath(os.path.abspath(video_path)).lower()
                    if src_norm == vp_norm:
                        video_path = cp
                        break
        except Exception:
            pass

    processed_path = _prepare_video_for_vl(video_path, work_dir)
    orig_size = os.path.getsize(video_path) / 1024 / 1024
    proc_size = os.path.getsize(processed_path) / 1024 / 1024

    # ── 缓存检查 ──
    sha = _sha256_file(processed_path)
    cache_path = _vl_cache_path(work_dir)
    cache = _vl_cache_load(cache_path)
    cached = _vl_cache_get(cache, sha)
    if cached is not None:
        log.info("VL 缓存命中: %s (sha256=%s...)", os.path.basename(video_path), sha[:12])
        if processed_path != video_path and os.path.exists(processed_path):
            try:
                os.remove(processed_path)
            except OSError:
                pass
        return cached

    log.info("DashScope 多轮分析: %s (%.1fMB→%.1fMB, fps=%.1f)",
             os.path.basename(video_path), orig_size, proc_size, fps)

    start = time.time()

    # ── Turn 1: 场景分析 ──
    messages = [
        {
            "role": "user",
            "content": [
                {"video": processed_path, "fps": fps},
                {"text": PROMPT_SCENE},
            ]
        }
    ]
    turn1_text, assistant_msg = _run_single_turn(messages, model, api_key)
    if assistant_msg is None:
        return turn1_text  # 错误消息

    # ── Turn 2: 质量评估 ──
    messages.append(assistant_msg)
    messages.append({
        "role": "user",
        "content": [{"text": PROMPT_QUALITY}]
    })
    turn2_text, assistant_msg2 = _run_single_turn(messages, model, api_key)
    if assistant_msg2 is None:
        # Turn 2 失败，用 Turn 1 的结果 + 空的质量评估
        turn2_text = json.dumps({"error": "质量评估轮次失败", "detail": turn2_text})

    # ── Turn 3: 叠层检测 ──
    turn3_messages = list(messages)
    if assistant_msg2:
        turn3_messages.append(assistant_msg2)
    turn3_messages.append({
        "role": "user",
        "content": [{"text": PROMPT_OVERLAY}]
    })
    turn3_text, _ = _run_single_turn(turn3_messages, model, api_key)
    if not _:
        turn3_text = json.dumps({"error": "叠层检测轮次失败", "detail": turn3_text})

    elapsed = time.time() - start

    # ── 合并结果 ──
    combined = _combine_vl_results(turn1_text, turn2_text, turn3_text,
                                    processed_path, video_path)

    # ── 写缓存 ──
    _vl_cache_put(cache, sha, combined, video_path)
    _vl_cache_save(cache_path, cache)

    # ── 清理 ──
    if processed_path != video_path and os.path.exists(processed_path):
        try:
            os.remove(processed_path)
        except OSError:
            pass

    log.info("多轮分析完成: %d+%d+%d 字符, 耗时 %.1fs",
             len(turn1_text), len(turn2_text), len(turn3_text), elapsed)
    return combined


# ─── 工具定义 ──────────────────────────────────────────────

@tool(
    name="watch_video",
    description="""AI 直接观看完整视频,返回结构化场景分析结果（多轮问答 + 自动缓存）.

AI 在一次会话内完成三轮分析:
  1. 场景分段 + 内容描述 + 语音摘要 (scenes)
  2. 视觉质量评估 (quality_assessment): 光照/构图/运动模糊/噪声/色彩/稳定性
  3. 文字/叠层/UI 检测 (overlay_detection)

结果自动缓存(SHA256),同一视频文件不会重复分析.
同一管线内多次引用同一视频时自动命中缓存,不消耗 API 额度.

参数:
    video_path: 视频文件的完整路径
    fps: (可选) 采样帧率,默认 2.0.处理长视频可降到 1.0 节省 token
    custom_prompt: (可选) 兼容旧接口,被内部固定提示词覆盖

何时用:
    - 粗筛时,拿到素材后第一个调用的工具
    - 需要理解视频整体内容,找有价值的片段时

何时千万别用:
    - 已经看过的视频不要再重复看(list_segments 会显示已有分析结果)
    - 不要在后续阶段再调用(后续 AI 只看切割后的片段)""",
    phase="analyze",
    category="timeline",
    tags=["video", "watch", "analyze", "dashscope"],
    group="画面与场景",
)
def watch_video(video_path: str, fps: float = 2.0,
                custom_prompt: str = "") -> str:
    """AI 观看完整视频,返回结构化场景分析."""
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"

    if not os.path.isfile(video_path):
        return f"不是文件: {video_path}"

    size_mb = os.path.getsize(video_path) / 1024 / 1024
    if size_mb > 2048:
        # 文件可能超过2GB，但 _prepare_video_for_vl 会先压缩再上传。
        # 只对极端大的文件（>4GB）直接拒绝，其余交给预处理降采样。
        if size_mb > 4096:
            return f"文件过大 (>{size_mb:.0f}MB),qwen3.6-plus 支持 2GB 上限"

    prompt = custom_prompt if custom_prompt else PROMPT_SCENE
    result = _call_dashscope_vision(video_path, prompt, fps)

    # 尝试结构化展示结果
    try:
        data = json.loads(result)
        if isinstance(data, dict):
            scenes = data.get("scenes", [])
            summary = data.get("summary", {})
            quality = data.get("quality_assessment", {})
            overlay = data.get("overlay_detection", {})
            output = [f"✅ 多轮视频分析完成 ({os.path.basename(video_path)})"]
            if scenes:
                output.append(f"\n## 场景分段 ({len(scenes)} 段)")
                output.append(json.dumps(scenes, ensure_ascii=False, indent=2))
            if summary:
                output.append(f"\n## 摘要")
                output.append(json.dumps(summary, ensure_ascii=False, indent=2))
            if quality:
                output.append(f"\n## 视觉质量")
                output.append(json.dumps(quality, ensure_ascii=False, indent=2))
            if overlay:
                output.append(f"\n## 文字/叠层")
                output.append(json.dumps(overlay, ensure_ascii=False, indent=2))
            return "\n".join(output)
        elif isinstance(data, list):
            return (
                f"✅ 视频分析完成 ({os.path.basename(video_path)})\n"
                f"\n{json.dumps(data, ensure_ascii=False, indent=2)}"
            )
    except (json.JSONDecodeError, TypeError):
        pass

    # 非 JSON，直接展示
    return f"✅ 视频分析完成 ({os.path.basename(video_path)})\n\n{result}"


# ═══════════════════════════════════════════════════════════
#  analyze_audio — AI 听音频,返回结构化分析
# ═══════════════════════════════════════════════════════════

AUDIO_ANALYSIS_PROMPT = """仔细听这段音频,输出结构化分析结果.

请分析以下几个方面:

1. 语音内容 (speech):
   - 有没有人在说话?
   - 说的主要内容是什么(摘要,不是逐字转录)?
   - 语速是快是慢?
   - 情绪如何(兴奋/平静/紧张/愤怒/悲伤)?
   - 是单人还是多人对话?

2. 音频质量 (audio_quality):
   - 清晰度(清晰/一般/嘈杂)
   - 有没有背景噪音?
   - 有没有回声?
   - 音量是否稳定?

3. 背景音乐 (background_music):
   - 有没有 BGM?
   - BGM 的风格和节奏?
   - BGM 是否盖过人声?

4. 静音段落 (silence):
   - 有哪些明显的静音/无声段落(开始-结束秒数)?

5. 总体评价 (overall):
   - 这段音频的整体质量如何?
   - 有什么需要注意的音频问题?

输出 JSON 格式.如果某方面不适用,用 null 或空列表."""


def _get_audio_model() -> str:
    """获取配置的语音分析模型名"""
    try:
        from director.config import get_model_for_role
        return get_model_for_role("audio")
    except Exception:
        return "qwen-omni-turbo"


def _get_vision_model() -> str:
    """获取配置的画面理解模型名"""
    try:
        from director.config import get_model_for_role
        return get_model_for_role("vision")
    except Exception:
        return "qwen-vl-max"


def _call_dashscope_audio(audio_path: str, prompt: str) -> str:
    """
    用 DashScope SDK 发音频给 AI 分析.

    qwen-omni-turbo 支持音频输入,传 base64.
    对于长音频(>60s),分段处理.
    """
    import base64

    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        try:
            from director.config import get_api_key
            api_key = get_api_key()
        except Exception:
            pass
    if not api_key:
        return "错误: API Key 未配置"

    import requests
    api_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    audio_size = os.path.getsize(audio_path)
    audio_duration = audio_size / 32000  # 16kHz 16bit mono = 32000 bytes/s
    max_chunk_dur = 60

    if audio_duration <= max_chunk_dur:
        # 整段发送
        with open(audio_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        payload = {
            "model": _get_audio_model(),
            "input": {
                "messages": [{
                    "role": "user",
                    "content": [
                        {"audio": f"data:audio/wav;base64,{b64}"},
                        {"text": prompt},
                    ]
                }]
            },
            "parameters": {"max_tokens": 2048},
        }
        try:
            resp = requests.post(api_url, headers=headers, json=payload, timeout=300)
            data = resp.json()
            choices = data.get("output", {}).get("choices", [])
            if choices:
                content_list = choices[0].get("message", {}).get("content", [])
                text = "".join(c.get("text", "") for c in content_list if "text" in c)
                return text if text else "(AI 未返回文本)"
            return f"(API 返回空: {data})"
        except Exception as e:
            return f"错误: {type(e).__name__}: {e}"
    else:
        # 长音频分段处理,每段先分析再合并
        import math, subprocess
        num_chunks = math.ceil(audio_duration / max_chunk_dur)
        all_results = []
        for i in range(num_chunks):
            chunk_start = i * max_chunk_dur
            chunk_file = audio_path.replace(".wav", f"_chunk{i}.wav")
            try:
                subprocess.run([
                    "ffmpeg", "-y", "-ss", str(chunk_start),
                    "-i", audio_path, "-t", str(max_chunk_dur),
                    "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                    chunk_file,
                ], capture_output=True, timeout=60, check=False)

                if os.path.exists(chunk_file) and os.path.getsize(chunk_file) > 0:
                    with open(chunk_file, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode("utf-8")
                    chunk_prompt = f"这是音频的第{i+1}/{num_chunks}段(从{chunk_start:.0f}秒开始).{prompt}"
                    payload = {
                        "model": "qwen-omni-turbo",
                        "input": {
                            "messages": [{
                                "role": "user",
                                "content": [
                                    {"audio": f"data:audio/wav;base64,{b64}"},
                                    {"text": chunk_prompt},
                                ]
                            }]
                        },
                        "parameters": {"max_tokens": 2048},
                    }
                    resp = requests.post(api_url, headers=headers, json=payload, timeout=300)
                    data = resp.json()
                    choices = data.get("output", {}).get("choices", [])
                    if choices:
                        text = "".join(c.get("text", "") for c in choices[0]["message"]["content"] if "text" in c)
                        all_results.append(f"[第{i+1}段 {chunk_start:.0f}s-{chunk_start + max_chunk_dur:.0f}s]\n{text}")
                # 清理 chunk 文件
                try:
                    os.remove(chunk_file)
                except:
                    pass
            except Exception as e:
                all_results.append(f"[第{i+1}段] 分析失败: {e}")

        if all_results:
            return "\n\n".join(all_results)
        return "(音频分析无结果)"


@tool(
    name="analyze_audio",
    description="""提取视频中的音频,AI 听完后返回结构化音频分析结果.

AI 会分析: 语音内容摘要,音频清晰度/质量,背景音乐检测,
静音段落,说话人情绪.返回结构化 JSON.

参数:
    video_path: 视频文件的完整路径
    custom_prompt: (可选) 自定义分析要求,覆盖默认提示词

何时用:
    - 粗筛时,watch_video() 看完画面后,调用此工具听音频
    - 有语音轨道的视频建议做音频分析(语音内容比画面更重要)
    - 需要了解音频质量(噪音,回声,音量问题)

何时千万别用:
    - 无音频轨道或纯音乐的视频(不会报错但没必要)
    - 已经分析过的视频不要重复分析""",
    phase="analyze",
    category="audio",
    tags=["audio", "analyze", "speech", "quality"],
    group="音频处理",
)
def analyze_audio(video_path: str, custom_prompt: str = "") -> str:
    """提取视频音频,AI 听完返回结构化分析."""
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"

    # 用 ffmpeg 提取音频(WAV 16kHz mono)
    tmp_dir = os.path.join(str(Path(__file__).parent.parent.parent), "_tmp_audio")
    os.makedirs(tmp_dir, exist_ok=True)
    wav_path = os.path.join(tmp_dir, "analyze_audio.wav")

    try:
        import subprocess
        result = subprocess.run([
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            wav_path,
        ], capture_output=True, text=True, timeout=120, check=False)

        if not os.path.exists(wav_path) or os.path.getsize(wav_path) == 0:
            return "音频提取失败(可能视频无音频轨道)"
    except subprocess.TimeoutExpired:
        return "音频提取超时"
    except Exception as e:
        return f"音频提取失败: {e}"

    prompt = custom_prompt if custom_prompt else AUDIO_ANALYSIS_PROMPT
    result = _call_dashscope_audio(wav_path, prompt)

    # 清理临时文件
    try:
        os.remove(wav_path)
    except:
        pass

    return (
        f"✅ 音频分析完成 ({os.path.basename(video_path)})\n\n{result}"
    )


@tool(
    name="batch_analyze",
    description=(
        "并行分析一批视频片段,同时看画面+听语音."
        "内部 50 路并行,一次分析所有指定片段."
        "结果自动持久化到项目索引,后续可用 search_memory 检索."
        "分析完成后返回汇总报告."
    ),
    phase="analyze",
    category="watch",
    tags=["batch", "parallel", "analyze", "ASR"],
    group="画面与场景",
)
def batch_analyze(
    segment_ids: str = "all",
    analysis_prompt: str = "",
) -> str:
    """
    并行分析一批视频片段(画面+语音).

    Args:
        segment_ids: 要分析的片段 ID 列表.
            - "all": 分析所有已裁切的片段
            - JSON 数组: ["seg_000", "seg_001", ...]
        analysis_prompt: (可选) 自定义分析提示词,覆盖默认提示词

    Returns:
        分析汇总报告
    """
    import json as _json
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from director.pipeline_state import PipelineState
    from director.tools.cut import _find_draft_dir

    work_dir = _find_draft_dir()
    state = PipelineState(work_dir)

    # 确定要分析的片段
    # state.segments 是 list[dict],每个 segment 有 id/path/source/start/end/duration
    all_segments = state.segments  # list[dict]
    segments = []
    if segment_ids == "all":
        segments = list(all_segments)
    else:
        # 支持多种 ID 格式:
        #   '["seg_000","seg_001"]' — JSON 数组字符串
        #   "seg_001,seg_002,seg_003" — 逗号分隔
        #   "seg_000" — 单个 ID
        ids = []
        # 先尝试 JSON 解析
        try:
            parsed = _json.loads(segment_ids)
            if isinstance(parsed, list):
                ids = parsed
            else:
                ids = [str(parsed)]
        except (_json.JSONDecodeError, TypeError):
            # JSON 解析失败 -> 逗号分隔
            raw = segment_ids.strip()
            ids = [s.strip() for s in raw.split(",") if s.strip()]
            # 去掉可能的路径前缀/后缀
            ids = [os.path.splitext(os.path.basename(sid))[0] for sid in ids]

        for sid in ids:
            for seg in all_segments:
                if seg.get("id") == sid:
                    segments.append(seg)
                    break

    if not segments:
        return (
            f"无片段需要分析.请检查片段ID.\n"
            f"可用选项:\n"
            f"  1. segment_ids='all' — 分析所有片段\n"
            f"  2. segment_ids='[\"seg_000\"]' — 指定片段ID(JSON数组)\n"
            f"可用的片段ID: {', '.join(s.get('id','?') for s in all_segments[:10])}\n"
            f"使用 list_segments 查看完整列表."
        )

    prompt = analysis_prompt or PROMPT_SCENE

    # 并行分析
    MAX_WORKERS = min(len(segments), 50)
    chunk_results = []
    _lock = threading.Lock()

    def _analyze_one(seg: dict) -> dict:
        # 压缩版路径（VL 画面分析用，无音频但够看画面）
        video_path = seg.get("compressed_path", seg.get("path", ""))
        # 原始路径（音频提取用，有完整音频轨道）
        original_path = seg.get("path", "")
        # 虚拟片段（path为空时）从 source_path+start/end 提取音频
        source_path = seg.get("source_path", "")
        seg_start = seg.get("start", 0)
        seg_end = seg.get("end", 0)
        seg_dur = seg_end - seg_start
        seg_id = seg.get("id", "unknown")
        result = {"id": seg_id, "path": original_path, "source": seg.get("source", ""),
                  "start_offset": seg_start, "end_offset": seg_end,
                  "duration": seg.get("duration", 0)}

        # 检查视频是否存在（compressed_path 或 path）
        if not video_path or not os.path.exists(video_path):
            result["video_analysis"] = "(视频文件不存在，跳过VL分析)"
            result["audio_analysis"] = "(视频文件不存在，跳过ASR)"
            return result

        # 检查视频时长，跳过太短的片段（< 2秒，VL 会返回 400）
        seg_dur = seg.get("duration", 0)
        if seg_dur < 2.0:
            result["video_analysis"] = f"(片段太短: {seg_dur:.1f}s,跳过VL分析)"
        else:
            # 画面分析（用压缩版路径）
            try:
                vresult = _call_dashscope_vision(video_path, prompt)
                result["video_analysis"] = vresult
            except Exception as e:
                result["video_analysis"] = f"(画面分析失败: {e})"

        # 音频提取 + ASR（用原始路径，有音频轨道）
        audio_path = None
        try:
            import subprocess
            tmp_dir = os.path.join(work_dir, "_tmp_audio")
            os.makedirs(tmp_dir, exist_ok=True)
            audio_path = os.path.join(tmp_dir, f"audio_{seg_id}.wav")

            # 判断音频来源：优先 path，否则从 source_path+start/end 提取
            if original_path and os.path.exists(original_path):
                audio_src = original_path
                audio_cmd = ["ffmpeg", "-y", "-i", audio_src,
                             "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                             audio_path]
            elif source_path and os.path.exists(source_path) and seg_dur > 0:
                audio_src = source_path
                audio_cmd = ["ffmpeg", "-y", "-ss", str(seg_start), "-i", audio_src,
                             "-t", str(seg_dur),
                             "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                             audio_path]
            else:
                result["audio_analysis"] = "(无原始文件，跳过音频分析)"
                return result

            subprocess.run(audio_cmd, capture_output=True, timeout=120, check=False)

            if os.path.exists(audio_path) and os.path.getsize(audio_path) > 0:
                asr_result = _call_dashscope_audio(audio_path, AUDIO_ANALYSIS_PROMPT)
                result["audio_analysis"] = asr_result
            else:
                result["audio_analysis"] = "(无音频)"
        except Exception as e:
            result["audio_analysis"] = f"(音频分析失败: {e})"
        finally:
            if audio_path and os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                except OSError:
                    pass
        return result

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_analyze_one, seg): seg for seg in segments}
        for f in as_completed(futures):
            try:
                result = f.result(timeout=900)
                with _lock:
                    chunk_results.append(result)
            except Exception as e:
                seg = futures[f]
                with _lock:
                    chunk_results.append({
                        "id": seg.get("id", "?"),
                        "path": seg.get("path", ""),
                        "error": str(e),
                    })

    # 排序
    seg_order = {seg["id"]: i for i, seg in enumerate(segments)}
    chunk_results.sort(key=lambda r: seg_order.get(r.get("id", ""), 999))

    # 持久化完整分析结果到 _index/ (包含全量 VL+ASR)
    try:
        from director.memory_store import save_analysis_index
        index_data = {
            "per_material": chunk_results,
            "elapsed": 0,
            "chunk_count": len(chunk_results),
            "report": "",
        }
        save_analysis_index(work_dir, index_data)
    except Exception as e:
        log.warning("batch_analyze 持久化索引失败: %s", e)

    # 返回完整预览,Director 能直接看到内容,不需要再用 search_memory 去猜
    has_video = sum(1 for r in chunk_results if r.get("video_analysis", "") not in ("N/A", "", "(路径不存在)"))
    has_audio = sum(1 for r in chunk_results if r.get("audio_analysis", "") not in ("N/A", "", "(无音频)"))
    previews = []
    for r in chunk_results:
        seg_id = r.get("id", "?")
        va = r.get("video_analysis", "")
        aa = r.get("audio_analysis", "")
        # 截取画面描述前 500 字 + 语音转写前 500 字
        va_prev = va[:500] + ("..." if len(va) > 500 else "")
        aa_prev = aa[:500] + ("..." if len(aa) > 500 else "")
        if va_prev or aa_prev:
            previews.append(f"\n--- {seg_id} ---\n[画面] {va_prev}\n[语音] {aa_prev}")
    preview_block = "\n".join(previews) if previews else ""
    return (
        f"## 批量分析完成\n"
        f"- 共 {len(chunk_results)} 个片段,{MAX_WORKERS} 路并行\n"
        f"- {has_video} 个片段完成画面分析\n"
        f"- {has_audio} 个片段完成语音转写\n"
        f"- 结果已存入索引(_index/analysis_index.json)\n"
        f"{preview_block}\n"
    )
