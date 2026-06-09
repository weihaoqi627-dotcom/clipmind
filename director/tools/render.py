"""
渲染工具 — 最终输出阶段
========================
渲染管线(3 步):

  Step 1 (Grade): 逐镜头调色 -> 中间文件
  Step 2 (Concat): concat 拼接 -> 合成视频
  Step 3 (Final): 字幕叠加 + BGM 混音 -> 成品

草稿模式:render_final 可接受 draft_id 代替分散参数,直接从草稿读取所有效果.

纯 ffmpeg filter_complex,零额外依赖.
"""
import json, os, subprocess, re, hashlib
from pathlib import Path

from director.registry import tool
from director.hardware_profile import get_pipeline_config

_PROJECT_DIR = Path(__file__).parent.parent.parent

# 硬件配置缓存（懒加载，一次读取全文件共享）
_HW_CFG = None
def _get_render_config():
    global _HW_CFG
    if _HW_CFG is None:
        _HW_CFG = get_pipeline_config()
    return _HW_CFG

def _select_encoder(profile_section: str = "compress", hw_fallback: str = "") -> tuple[str, list]:
    """从硬件画像选择编码器. 返回 (vcodec, params_list)

    profile_section: "compress"=中间产物(快速/低质量), "render"=最终输出(高质量)
    hw_fallback: 指定硬件编码不可用时的降级编码器名(如 libx264)
    """
    cfg = _get_render_config()
    if profile_section == "compress":
        c = cfg.get("compress", {})
        crf = c.get("cq_or_crf", 28)
        preset = c.get("preset", "ultrafast")
    elif profile_section == "original":
        c = cfg.get("render", {})
        crf = 16  # 接近视觉无损
        preset = "p6"
    else:
        c = cfg.get("render", {})
        crf = c.get("crf", 22)
        preset = c.get("preset", "p4")

    enc = c.get("encoder") or os.environ.get("CLIPMIND_HW_ENCODER", "")
    hw_fallback = hw_fallback or "libx264"

    if enc:
        if "nvenc" in enc:
            nv_preset = "p6" if profile_section == "original" else preset
            return enc, ["-qp", str(crf), "-preset", nv_preset, "-rc", "vbr"]
        elif "amf" in enc:
            amf_q = "quality" if profile_section == "original" else "speed"
            return enc, ["-quality", amf_q, "-qp_i", str(crf), "-qp_p", str(crf)]
        elif "qsv" in enc:
            qsv_p = "slower" if profile_section == "original" else "veryfast"
            return enc, ["-preset", qsv_p, "-global_quality", str(crf)]
        elif "libx264" in enc or "libx265" in enc:
            sw_p = "slow" if profile_section == "original" else preset
            return enc, ["-crf", str(crf), "-preset", sw_p]
        else:
            return enc, []
    sw_preset = "slow" if profile_section == "original" else preset
    return hw_fallback, ["-crf", str(crf), "-preset", sw_preset]

# ═══════════════════════════════════════════════════════
#  可用转场映射
# ═══════════════════════════════════════════════════════

XFADE_MAP = {
    "cut":         None,
    "fade":        "fade",
    "dissolve":    "dissolve",
    "fadeblack":   "fadeblack",
    "fadewhite":   "fadewhite",
    "slide_left":  "slideright",
    "slide_right": "slideleft",
    "slide_up":    "slidedown",
    "slide_down":  "slideup",
    "wipe_left":   "wiperight",
    "wipe_right":  "wipeleft",
    "wipe_up":     "wipedown",
    "wipe_down":   "wipeup",
    "zoom_in":     "zoomin",
    "pixelize":    "pixelize",
    "circleopen":  "circleopen",
    "circleclose": "circleclose",
    "radial":      "radial",
}

DEFAULT_TRANSITION = "fade"
DEFAULT_TRANS_DUR = 0.3

# ═══════════════════════════════════════════════════════
#  输出格式预设
# ═══════════════════════════════════════════════════════

OUTPUT_PRESETS = {
    "original":             {"width": 0, "height": 0, "fps": 0, "bitrate": "", "original": True},
    "bilibili_1080p":      {"width": 1920, "height": 1080, "fps": 30, "bitrate": "10M"},
    "bilibili_4k":          {"width": 3840, "height": 2160, "fps": 30, "bitrate": "35M"},
    "douyin":               {"width": 1080, "height": 1920, "fps": 30, "bitrate": "8M"},
    "kuaishou":             {"width": 1080, "height": 1920, "fps": 30, "bitrate": "6M"},
    "xiaohongshu":          {"width": 1080, "height": 1920, "fps": 30, "bitrate": "8M"},
    "youtube_1080p":        {"width": 1920, "height": 1080, "fps": 30, "bitrate": "12M"},
    "youtube_4k":           {"width": 3840, "height": 2160, "fps": 60, "bitrate": "45M"},
    "instagram_reel":       {"width": 1080, "height": 1920, "fps": 30, "bitrate": "6M"},
    "wechat_moment":        {"width": 1080, "height": 1920, "fps": 30, "bitrate": "4M"},
    "twitter":              {"width": 1920, "height": 1080, "fps": 30, "bitrate": "8M"},
}


@tool(
    name="list_output_presets",
    description="列出所有输出格式预设(平台/分辨率/码率)",
    phase="render",
    category="render",
    tags=["render"],
    group="渲染输出",
)
def list_output_presets() -> str:
    """列出所有可用的输出格式预设"""
    lines = []
    for name, p in OUTPUT_PRESETS.items():
        if p.get("original"):
            lines.append(f"- {name}: 原画输出(保持原始分辨率/最高品质)")
        else:
            lines.append(
                f"- {name}: {p['width']}x{p['height']} "
                f"{p['fps']}fps {p['bitrate']}"
            )
    return "可用输出预设:\n" + "\n".join(lines) if lines else "无可用预设"


@tool(
    name="render_final",
    description=(
        "专业渲染:调色 -> 转场拼接 -> 字幕+BGM 混音 -> 动效."
        "支持 grade_json(逐镜头曲线/色轮/色阶), "
        "transitions_json(转场序列), "
        "bgm_path(背景音乐+音频闪避), "
        "subtitle_json(字幕), "
        "animations_json(Ken Burns缩放动效), "
        "vocal_track_path(人声轨——提供后用干净人声做闪避侧链,闪避更精准)"
    ),
    phase="render",
    category="render",
    tags=["render"],
    group="渲染输出",
)
def render_final(
    video_path: str,
    arrangement_json: str,
    grade_json: str = "",
    transitions_json: str = "",
    bgm_path: str = "",
    bgm_volume: float = 0.4,
    subtitle_json: str = "",
    bgm_ducking: bool = True,
    animations_json: str = "",
    vocal_track_path: str = "",
    color_preset_path: str = "",
    flower_text_video_path: str = "",
    overlays_json: str = "",
    sfx_json: str = "",
    output_path: str = "",
    preset: str = "",
    fill_mode: str = "blur",
) -> str:
    """
    专业级渲染:调色 -> 转场拼接 -> 字幕+BGM 混音 -> 动画 -> 叠层+音效.

    Args:
        video_path: 源视频路径
        arrangement_json: 排版JSON [{"start":0, "end":30, "duration":30}, ...]
        grade_json: 逐镜头调色参数 {"0": {"curves_master": "...", "shadows_rgb": "..."}, ...}
        transitions_json: 转场序列 [{"from":0, "to":1, "type":"fade", "duration":0.5}, ...]
        bgm_path: 背景音乐路径
        bgm_volume: BGM 音量 0.0~1.0
        subtitle_json: 字幕数据 [{"start":0, "end":2.5, "text":"..."}, ...]
        bgm_ducking: 是否启用音频闪避(BGM 在有人声时自动压低,默认开启)
        animations_json: 动效数据 [{"type":"kenburns", "start":0, "end":44, "zoom":1.08}, ...]
        vocal_track_path: 人声轨路径(可选).提供后音频闪避会用干净人声做侧链,闪避更精准.
                          先用 separate_audio 分离出 vocals.wav,然后传入此参数.
        color_preset_path: 预调色视频路径(可选).提供后跳过 Stage1 调色,直接使用此视频作为调色后源素材.
                          用 apply_color_preset 等工具的输出路径传入.
        flower_text_video_path: 花字视频路径(可选).提供后在 Stage3 叠加花字到最终输出.
                               用 apply_flower_text 的输出路径传入.
        overlays_json: 叠层JSON(可选),[{source_path, start_time, duration, x, y, width, height, opacity}]
                      用于画中画,双屏,花字(GSAP/HF渲染),贴图等.
        sfx_json: 音效JSON(可选),[{source, start_time, duration, volume}]
                  音效在指定时间点播放,与BGM和人声混音.
        output_path: 输出路径(可选)
        preset: 输出格式预设(可选),如 "douyin" "bilibili_1080p" "youtube_1080p".
                用 list_output_presets 查看所有可用预设.
        fill_mode: 比例不匹配时的填充方式."blur"=模糊填充(默认,推荐),"black"=黑边.

    Returns:
        结果信息
    """
    # ── 解析参数 ──
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"

    arrangement = _parse_json(arrangement_json)
    if not arrangement:
        return "渲染失败:空排版"
    if not isinstance(arrangement, list):
        return "渲染失败:arrangement_json 必须为数组格式 [{\"start\":0, ...}]"
    # 整数ID列表自动解析
    if arrangement and isinstance(arrangement[0], (int, float)):
        from director.tools.analyze import _get_segments_cached
        segs = _get_segments_cached(video_path)
        id_map = {s["id"]: s for s in segs}
        resolved = []
        for cid in arrangement:
            seg = id_map.get(int(cid))
            if seg:
                resolved.append(seg)
        arrangement = resolved

    grades = _parse_json(grade_json) or {}
    transitions = _parse_json(transitions_json) or []
    subtitles = _parse_json(subtitle_json) or []
    animations = _parse_json(animations_json) or []
    overlays = _parse_json(overlays_json) or []
    sfx_items = _parse_json(sfx_json) or []

    # 解析输出预设
    preset_cfg = OUTPUT_PRESETS.get(preset, None) if preset else None
    if preset and not preset_cfg:
        return f"未知输出预设: {preset}.用 list_output_presets 查看可用预设."

    if not output_path:
        tag = hashlib.md5(video_path.encode()).hexdigest()[:8]
        suffix = f"_{preset}" if preset else ""
        output_path = os.path.join(_PROJECT_DIR, "output", f"final_{tag}{suffix}.mp4")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    # 唯一临时目录:PID + 随机后缀,防止并发渲染冲突
    _run_tag = f"{os.getpid()}_{os.urandom(3).hex()}"
    tmp_dir = os.path.join(_PROJECT_DIR, "_tmp_render", _run_tag)
    os.makedirs(tmp_dir, exist_ok=True)

    # ════════════════════════════════════════════════════
    #  Step 1: 逐镜头调色(如有 color_preset_path,用它替换视频源)
    # ════════════════════════════════════════════════════
    source_path = color_preset_path if color_preset_path and os.path.exists(color_preset_path) else video_path
    encoder_profile = "original" if preset_cfg and preset_cfg.get("original") else "compress"
    grade_files = _render_grade(arrangement, source_path, grades, tmp_dir, encoder_profile=encoder_profile)
    if not grade_files:
        return "Step 1 失败:无有效片段"

    # ════════════════════════════════════════════════════
    #  Step 2: concat 拼接(多镜头 -> 单视频)
    # ════════════════════════════════════════════════════

    # 检查是否有需要 Step 3 处理的内容
    need_sub = len(subtitles) > 0
    need_bgm = bgm_path and os.path.exists(bgm_path)
    need_anim = len(animations) > 0
    need_vocal = bgm_ducking and need_bgm and vocal_track_path and os.path.exists(vocal_track_path)
    need_flower = flower_text_video_path and os.path.exists(flower_text_video_path)
    need_overlays = len(overlays) > 0
    need_sfx = len(sfx_items) > 0

    if len(grade_files) > 1 or need_sub or need_bgm or need_anim or need_flower or need_overlays or need_sfx:
        # 合并 Step 2+3: concat + 字幕 + BGM + 叠层 一条命令完成
        result_str = _render_concat_and_final(
            grade_files, subtitles, bgm_path, bgm_volume,
            bgm_ducking, animations, output_path, vocal_track_path,
            flower_text_video_path, preset_cfg, fill_mode,
            overlays=overlays, sfx_items=sfx_items,
        )
    else:
        # 只有一个文件且无后续处理:直接复制到最终输出
        ok = _render_concat(grade_files, output_path)
        result_str = f"✅ 渲染完成: {output_path}" if ok else None

    if result_str and result_str.startswith("✅"):
        _cleanup_tmp(grade_files, "", tmp_dir)
        return result_str

    return f"❌ 渲染失败: {result_str or 'Step 1/2 无有效输出'}"


# ═══════════════════════════════════════════════════════
#  Step 1: 逐镜头调色
# ═══════════════════════════════════════════════════════

def _render_grade(arrangement: list, src: str, grades: dict, tmp_dir: str, encoder_profile: str = "compress") -> list[str]:
    """逐镜头提取 + 可选调色 -> 返回中间文件路径列表

    如果 shot 有 file_path(已是裁切好的独立文件),直接用该文件而非从 src 裁剪.
    这使多素材渲染成为可能——每个片段可以来自不同源视频.
    """
    grade_files = []
    for i, shot in enumerate(arrangement):
        start = shot.get("start", 0)
        dur = shot.get("duration", shot.get("end", 30) - start)
        if dur < 0.1:
            continue

        shot_path = os.path.join(tmp_dir, f"g_{i}.mp4")
        grade_params = grades.get(str(i), grades.get(i, {}))

        # 优先使用已裁切的独立文件(多素材场景/代理→原画替换)
        file_path = shot.get("file_path", "")
        if file_path and os.path.exists(file_path):
            # 提取指定时间范围(支持原始素材的精确裁剪)
            subprocess.run([
                "ffmpeg", "-y",
                "-ss", str(start), "-i", file_path,
                "-t", str(dur),
                "-c", "copy", "-avoid_negative_ts", "1",
                shot_path,
            ], capture_output=True, timeout=300, check=False)
        elif grade_params:
            _render_shot_with_grade(src, start, dur, grade_params, shot_path, encoder_profile)
        else:
            # 无调色:直接复制
            subprocess.run([
                "ffmpeg", "-y",
                "-ss", str(start), "-i", src,
                "-t", str(dur),
                "-c", "copy", "-avoid_negative_ts", "1",
                shot_path,
            ], capture_output=True, timeout=300, check=False)

        if os.path.exists(shot_path) and os.path.getsize(shot_path) > 0:
            grade_files.append(shot_path)
    return grade_files


def _render_shot_with_grade(src: str, start: float, dur: float,
                            grade: dict, out_path: str, encoder_profile: str = "compress"):
    """单镜头调色渲染(硬件编码优先)"""
    from director.tools.colors import _build_grade_filter
    filters = _build_grade_filter(grade)
    if not filters:
        subprocess.run([
            "ffmpeg", "-y", "-ss", str(start), "-i", src,
            "-t", str(dur), "-c", "copy", "-avoid_negative_ts", "1",
            out_path,
        ], capture_output=True, timeout=300, check=False)
        return

    vcodec, vparams = _select_encoder(encoder_profile)

    cmd = [
        "ffmpeg", "-y", "-ss", str(start), "-i", src,
        "-t", str(dur),
        "-vf", filters,
        "-c:v", vcodec, *vparams,
        "-c:a", "copy",
        out_path,
    ]
    subprocess.run(cmd, capture_output=True, timeout=600, check=False)




# ═══════════════════════════════════════════════════════
#  Step 2: concat 拼接
# ═══════════════════════════════════════════════════════

def _has_audio_stream(video_path: str) -> bool:
    """检测视频是否有音频流"""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet",
             "-select_streams", "a",
             "-show_entries", "stream=index",
             "-of", "csv=p=0", video_path],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
        return bool(r.stdout.strip())
    except Exception:
        # 无法检测时保守假设无音频,避免下游 concat 以 a=1 模式拼接导致崩溃
        return False


def _render_concat(grade_files: list, out_path: str) -> bool:
    """用 concat 拼接多镜头 -> 单视频(确保音视频严格对齐,不产生 xfade 飘移)"""
    n = len(grade_files)
    if n == 0:
        return False
    if n == 1:
        subprocess.run([
            "ffmpeg", "-y", "-i", grade_files[0],
            "-c", "copy", "-movflags", "+faststart", out_path,
        ], capture_output=True, timeout=300, check=False)
        return os.path.exists(out_path) and os.path.getsize(out_path) > 0

    # 检查各片段的分辨率,不一致时统一缩放到第一个片段的分辨率
    target_w, target_h = _get_common_resolution(grade_files)

    # 检查是否有音频流(所有输入都无音频时 concat 跳过 audio)
    has_audio = any(_has_audio_stream(f) for f in grade_files)
    has_audio_str = "1" if has_audio else "0"

    # concat filter:视频和音频同时拼接,音视频严格对齐
    # 格式: [0:v][0:a][1:v][1:a]...concat=n=N:v=1:a=1[outv][outa]
    inputs = []
    for i in range(n):
        if target_w and target_h:
            inputs.append(f"[{i}:v]scale={target_w}:{target_h}:flags=bilinear:force_original_aspect_ratio=decrease,pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black[v{i}];")
            if has_audio:
                inputs.append(f"[{i}:a]anull[a{i}];")
                inputs.append(f"[v{i}][a{i}]")
            else:
                inputs.append(f"[v{i}]")
        else:
            inputs.append(f"[{i}:v]")
            if has_audio:
                inputs.append(f"[{i}:a]")

    filter_str = "".join(inputs)
    filter_str += f"concat=n={n}:v=1:a={has_audio_str}[outv]" + ("[outa]" if has_audio else "")

    vcodec, vparams = _select_encoder("compress")

    cmd = [
        "ffmpeg", "-y",
    ]
    for gf in grade_files:
        cmd += ["-i", gf]
    cmd += [
        "-filter_complex", filter_str,
        "-map", "[outv]",
    ]
    if has_audio:
        cmd += ["-map", "[outa]", "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-an"]
    cmd += [
        "-c:v", vcodec, *vparams,
        "-movflags", "+faststart",
        out_path,
    ]
    subprocess.run(cmd, capture_output=True, timeout=600, check=False)
    return os.path.exists(out_path) and os.path.getsize(out_path) > 0


def _render_concat_and_final(
    grade_files: list, subtitles: list, bgm_path: str, bgm_volume: float,
    bgm_ducking: bool, animations: list, output_path: str,
    vocal_track_path: str = "", flower_text_video_path: str = "",
    preset: dict = None, fill_mode: str = "blur",
    overlays: list = None, sfx_items: list = None,
) -> str:
    """合并 concat + 字幕/BGM/叠层 为一条 FFmpeg 命令.

    不产生中间文件,grade文件直接 concat 后输入后续处理链,一次编码输出最终视频.
    返回结果字符串 (✅ 或 ❌).
    """
    overlays = overlays or []
    sfx_items = sfx_items or []
    n = len(grade_files)
    if n == 0:
        return None

    grade_has_audio = any(_has_audio_stream(f) for f in grade_files)

    # 预估视频总时长（用于限制 BGM 循环长度，避免 atrim=99999 导致卡死）
    _total_dur = 0.0
    for gf in grade_files:
        try:
            r = subprocess.run(
                ["ffmpeg", "-i", gf],
                capture_output=True, timeout=10, check=False,
            )
            out = (r.stdout + r.stderr).decode("utf-8", errors="replace")
            m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", out)
            if m:
                h, m_, s = float(m.group(1)), float(m.group(2)), float(m.group(3))
                _total_dur += h * 3600 + m_ * 60 + s
            else:
                _total_dur += 10.0
        except Exception:
            _total_dur += 10.0
    if _total_dur <= 0:
        _total_dur = 30.0
    _bgm_trim_dur = int(_total_dur) + 5  # 多给 5 秒余量
    need_sub = len(subtitles) > 0
    need_bgm = bgm_path and os.path.exists(bgm_path)
    need_anim = len(animations) > 0
    need_vocal = bgm_ducking and need_bgm and vocal_track_path and os.path.exists(vocal_track_path)
    need_flower = flower_text_video_path and os.path.exists(flower_text_video_path)
    need_overlays = len(overlays) > 0
    need_sfx = len(sfx_items) > 0
    # 是否有任何音频来源（级联素材原始音频 / BGM / 音效）
    has_any_audio = grade_has_audio or need_bgm or need_sfx
    has_audio_str = "1" if grade_has_audio else "0"

    # ── 1. 构建 concat filter (grade 文件为输入 0..n-1) ──
    target_w, target_h = _get_common_resolution(grade_files)
    concat_parts = []
    for i in range(n):
        if target_w and target_h:
            concat_parts.append(
                f"[{i}:v]scale={target_w}:{target_h}:"
                f"flags=bilinear:force_original_aspect_ratio=decrease,"
                f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black[v{i}];"
            )
            if grade_has_audio:
                concat_parts.append(f"[{i}:a]anull[a{i}];")
                concat_parts.append(f"[v{i}][a{i}]")
            else:
                concat_parts.append(f"[v{i}]")
        else:
            concat_parts.append(f"[{i}:v]")
            if grade_has_audio:
                concat_parts.append(f"[{i}:a]")

    filter_parts = []
    concat_str = "".join(concat_parts)
    if grade_has_audio:
        concat_str += f"concat=n={n}:v=1:a=1[vout][aout]"
    else:
        concat_str += f"concat=n={n}:v=1:a=0[vout]"
    filter_parts.append(concat_str)

    # ── 后续输入从 n 开始 ──
    inputs = list(grade_files)

    # ── 2. 获取视频尺寸（取第一个 grade 文件）──
    vw, vh = 1920, 1080
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", grade_files[0]],
            capture_output=True, timeout=15, check=False,
        )
        out = (r.stdout + r.stderr).decode("utf-8", errors="replace")
        res_m = re.search(r",\s*(\d{3,})x(\d{3,})", out)
        if res_m:
            vw, vh = int(res_m.group(1)), int(res_m.group(2))
    except Exception:
        pass

    # ── 3. 动效(Ken Burns) ──
    if need_anim:
        anim_vf = _build_animation_filter(animations, vw, vh)
        if anim_vf:
            filter_parts.append(f"[vout]{anim_vf}[vout]")

    # ── 4. 字幕 ──
    if need_sub:
        dt_filters = _make_subtitle_filters(subtitles)
        if dt_filters:
            filter_parts.append(f"[vout]{dt_filters}[vout]")

    # ── 5. BGM 混音 ──
    #     grade_has_audio + BGM: [aout] + BGM → amix → [afinal]
    #     no grade_audio + BGM: BGM alone → [afinal]
    audio_map = "[afinal]"
    if need_bgm:
        inputs.append(bgm_path)
        bgm_idx = len(inputs) - 1
        filter_parts.append(
            f"[{bgm_idx}:a]aloop=loop=-1:size=44100*60,"
            f"atrim=0:{_bgm_trim_dur},"
            f"volume={bgm_volume}[bgm]"
        )
        if grade_has_audio:
            filter_parts.append("[aout]volume=1.0[main_a]")
            if bgm_ducking and need_vocal:
                inputs.append(vocal_track_path)
                voc_idx = len(inputs) - 1
                filter_parts.append(f"[{voc_idx}:a]volume=1.0[vocal_sidechain]")
                filter_parts.append(
                    "[bgm][vocal_sidechain]sidechaincompress="
                    "threshold=0.1:ratio=20:attack=100:release=500:makeup=3[bgm_ducked]"
                )
                filter_parts.append(
                    "[main_a][bgm_ducked]amix=inputs=2:duration=first:dropout_transition=2[afinal]"
                )
            elif bgm_ducking:
                filter_parts.append(
                    "[bgm][main_a]sidechaincompress="
                    "threshold=0.1:ratio=20:attack=100:release=500:makeup=3[bgm_ducked]"
                )
                filter_parts.append(
                    "[main_a][bgm_ducked]amix=inputs=2:duration=first:dropout_transition=2[afinal]"
                )
            else:
                filter_parts.append(
                    "[main_a][bgm]amix=inputs=2:duration=first:dropout_transition=2[afinal]"
                )
        else:
            # 无级联音频, BGM 是唯一音频来源
            filter_parts.append("[bgm]anull[afinal]")
    elif grade_has_audio:
        filter_parts.append("[aout]anull[afinal]")
    # else: 无任何音频, 后面用 -an

    # ── 6. 花字叠加 ──
    if need_flower:
        inputs.append(flower_text_video_path)
        ft_idx = len(inputs) - 1
        filter_parts.append(f"[vout][{ft_idx}:v]overlay=format=auto:shortest=1[vout]")

    # ── 7. 叠层(画中画) ──
    if need_overlays:
        for ov in overlays:
            ov_path = ov.get("source_path", "")
            if not os.path.exists(ov_path):
                continue
            inputs.append(ov_path)
            ov_idx = len(inputs) - 1
            ov_x_px = int(ov.get("x", 0.5) * vw)
            ov_y_px = int(ov.get("y", 0.5) * vh)
            ov_w_px = int(ov.get("width", 0.3) * vw)
            ov_h_px = int(ov.get("height", 0.3) * vh)
            ov_start = ov.get("start_time", 0)
            ov_dur = ov.get("duration", 0)
            if ov_dur > 0:
                enable_expr = f"between(t\\,{ov_start}\\,{ov_start + ov_dur})"
                filter_parts.append(
                    f"[{ov_idx}:v]scale={ov_w_px}:{ov_h_px}:flags=bilinear[ov_scaled_{ov_idx}];"
                    f"[vout][ov_scaled_{ov_idx}]overlay={ov_x_px}:{ov_y_px}:"
                    f"enable='{enable_expr}'[vout]"
                )
            else:
                filter_parts.append(
                    f"[{ov_idx}:v]scale={ov_w_px}:{ov_h_px}:flags=bilinear[ov_scaled_{ov_idx}];"
                    f"[vout][ov_scaled_{ov_idx}]overlay={ov_x_px}:{ov_y_px}[vout]"
                )

    # ── 8. 输出预设缩放 ──
    if preset:
        if preset.get("original"):
            # 原画输出:不缩放,保持原始分辨率
            pass
        else:
            pw, ph = preset["width"], preset["height"]
            if fill_mode == "blur":
                filter_parts.append(
                    f"[vout]scale={pw}:{ph}:force_original_aspect_ratio=decrease,"
                    f"split[src][bg];"
                    f"[bg]scale={pw}:{ph}:force_original_aspect_ratio=increase,"
                    f"crop={pw}:{ph},gblur=sigma=30[bg_blur];"
                    f"[bg_blur][src]overlay=(W-w)/2:(H-h)/2[vout]"
                )
            else:
                filter_parts.append(
                    f"[vout]scale={pw}:{ph}:force_original_aspect_ratio=decrease,"
                    f"pad={pw}:{ph}:(ow-iw)/2:(oh-ih)/2:black[vout]"
                )

    # ── 9. 音效(在已有音频映射链尾追加混音) ──
    if need_sfx:
        sfx_labels = []
        for i, sfx_item in enumerate(sfx_items):
            sfx_path = sfx_item.get("source", "")
            if not os.path.exists(sfx_path):
                continue
            inputs.append(sfx_path)
            sfx_idx = len(inputs) - 1
            delay_ms = int(sfx_item.get("start_time", 0) * 1000)
            vol = sfx_item.get("volume", 1.0)
            filter_parts.append(
                f"[{sfx_idx}:a]adelay={delay_ms}|{delay_ms},volume={vol}[sfx_{i}]"
            )
            sfx_labels.append(f"[sfx_{i}]")

        if sfx_labels:
            # 如果有前置音频(concat的aout或BGM),把SFX混进去;否则SFX就是唯一音频
            sfx_input_str = "".join(sfx_labels)
            has_pre_sfx_audio = grade_has_audio or need_bgm
            if has_pre_sfx_audio:
                num_inputs = len(sfx_labels) + 1
                filter_parts.append(
                    f"{audio_map}{sfx_input_str}amix=inputs={num_inputs}:duration=first[afinal_sfx]"
                )
            else:
                # 无前置音频:多个SFX互混;单个SFX直接输出
                if len(sfx_labels) > 1:
                    filter_parts.append(
                        f"{sfx_input_str}amix=inputs={len(sfx_labels)}:duration=first[afinal_sfx]"
                    )
                else:
                    filter_parts.append(f"{sfx_labels[0]}anull[afinal_sfx]")
            audio_map = "[afinal_sfx]"

    # 如果所有SFX文件都不存在,恢复音频状态(避免 -map 不存在的 [afinal])
    if need_sfx and not sfx_labels:
        has_any_audio = grade_has_audio or need_bgm

    # ── 10. 构建 filter_graph ──
    filter_graph = ";".join(filter_parts)

    # ── 11. 编码器 ──
    if preset and preset.get("original"):
        vcodec, vparams = _select_encoder("original", hw_fallback="libx264")
    else:
        vcodec, vparams = _select_encoder("render", hw_fallback="libx264")

    if preset and not preset.get("original"):
        br = preset["bitrate"]
        bufsize = _double_bitrate(br)
        vparams = ["-b:v", br, "-maxrate", br, "-bufsize", bufsize]
        if "nvenc" in vcodec or "amf" in vcodec:
            vparams += ["-rc", "vbr"]

    cmd = ["ffmpeg", "-y"]
    for inp in inputs:
        cmd += ["-i", inp]
    cmd += [
        "-filter_complex", filter_graph,
        "-map", "[vout]",
    ]
    if has_any_audio:
        cmd += [
            "-map", audio_map,
            "-c:a", "aac", "-b:a", "192k",
        ]
    else:
        cmd += ["-an"]
    cmd += [
        "-c:v", vcodec, *vparams,
        "-movflags", "+faststart",
        output_path,
    ]

    if preset and not preset.get("original"):
        pidx = cmd.index(output_path) if output_path in cmd else len(cmd)
        if pidx > 0 and preset.get("fps", 0) > 0:
            cmd.insert(pidx, str(preset["fps"]))
            cmd.insert(pidx, "-r")

    result = subprocess.run(cmd, capture_output=True, timeout=900, check=False)

    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        size = os.path.getsize(output_path) / (1024 * 1024)
        details = []
        if need_sub:
            details.append(f"字幕({len(subtitles)}段)")
        if need_bgm:
            details.append("BGM")
        if need_overlays:
            details.append(f"叠层({len(overlays)}个)")
        if need_sfx:
            details.append(f"音效({len(sfx_items)}个)")
        detail_str = f" ({', '.join(details)})" if details else ""
        return f"✅ 渲染完成{detail_str}: {output_path} ({size:.1f}MB)"

    err = result.stderr.decode("utf-8", errors="replace")[-300:]
    return f"❌ 渲染失败: {err}"


def _get_video_resolution(video_path: str) -> tuple:
    """检测视频分辨率,返回 (width, height) 或 (0, 0)."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet",
             "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=p=0", video_path],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
        parts = r.stdout.strip().split(",")
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return 0, 0


def _get_common_resolution(file_paths: list[str]) -> tuple:
    """获取多个视频的公共分辨率.不一致时取第一个有效分辨率+黑边填充."""
    if not file_paths:
        return 0, 0
    # 取第一个有效分辨率作为目标
    for fp in file_paths:
        w, h = _get_video_resolution(fp)
        if w > 0 and h > 0:
            # 检查其他文件是否一致
            all_same = True
            for fp2 in file_paths:
                w2, h2 = _get_video_resolution(fp2)
                if w2 > 0 and h2 > 0 and (w2 != w or h2 != h):
                    all_same = False
                    break
            if all_same:
                return 0, 0  # 全部一致,无需缩放
            return w, h
    return 0, 0


def _build_transition_seq(transitions: list, n_shots: int,
                          shot_durs: list) -> list[dict]:
    """将用户提供的转场列表转为完整 n-1 序列,缺失的用默认值"""
    seq = []
    # 用户提供的转场:按 from 索引排序
    user_map = {}
    for t in transitions:
        fidx = t.get("from", -1)
        if 0 <= fidx < n_shots - 1:
            user_map[fidx] = {
                "type": t.get("type", DEFAULT_TRANSITION),
                "duration": t.get("duration", DEFAULT_TRANS_DUR),
            }

    for i in range(n_shots - 1):
        if i in user_map:
            seq.append(user_map[i])
        else:
            seq.append({
                "type": DEFAULT_TRANSITION,
                "duration": min(DEFAULT_TRANS_DUR, shot_durs[i] * 0.3, shot_durs[i+1] * 0.3),
            })
    return seq


# ═══════════════════════════════════════════════════════
#  动画/动效
# ═══════════════════════════════════════════════════════

def _build_animation_filter(animations: list, vw: int, vh: int) -> str:
    """生成动画 filter 链(Ken Burns 缩放等)"""
    parts = []
    for anim in animations:
        atype = anim.get("type", "")
        if atype == "kenburns":
            zoom_target = anim.get("zoom", 1.05)
            start = anim.get("start", 0)
            end = anim.get("end", 60)
            dur = end - start
            if dur <= 0 or zoom_target <= 1.0:
                continue
            # zoompan: z 从 1.0 线性变化到 zoom_target
            # on = output frame number (从 1 开始)
            # 速率 = (zoom_target - 1) / (dur * fps)
            rate = (zoom_target - 1.0) / dur
            # 使用 zoompan: z='1+rate*floor(on...)' 但表达式有限制
            # 简化:z='min(1+rate*on/30, zoom)'
            max_z = zoom_target
            parts.append(
                f"zoompan=z='min(1+{rate}*on,{max_z})'"
                f":d=1:s={vw}x{vh}:fps=30"
            )
        elif atype == "fade_in":
            dur = anim.get("duration", 0.5)
            parts.append(f"fade=t=in:st={anim.get('start',0)}:d={dur}")
        elif atype == "fade_out":
            dur = anim.get("duration", 0.5)
            parts.append(f"fade=t=out:st={anim.get('start',0)}:d={dur}:color=black")

    return ",".join(parts)


# ═══════════════════════════════════════════════════════
def _make_subtitle_filters(segments: list) -> str:
    """生成字幕 drawtext filter 链"""
    from director.tools.effects import find_font
    from director.tools.mask import _get_video_dimensions

    if not segments:
        return ""

    # dummy video 获取尺寸
    vw, vh = 1080, 1920  # 默认
    for s in segments:
        if "video_w" in s and "video_h" in s:
            vw, vh = s["video_w"], s["video_h"]
            break

    font_size = max(50, min(180, int(vh * 0.042)))
    box_border = max(6, font_size // 12)
    line_spacing = max(2, int(font_size * 0.02))
    max_text_width = vw * 0.82
    chars_per_line = max(4, int(max_text_width / font_size))

    font_path = find_font("") or _find_chinese_font()
    # 去掉盘符冒号,避免 -vf 中冒号被当参数分隔符
    if font_path:
        font_path = _sanitize_font_path(font_path)

    dt_parts = []
    for i, seg in enumerate(segments):
        raw_start, raw_end, text = seg.get("start", 0), seg.get("end", 1), seg.get("text", "")
        if not text:
            continue

        # 断句间隔判断
        if i < len(segments) - 1:
            gap = segments[i + 1]["start"] - raw_end
        else:
            gap = 2.0

        if gap >= 0.3:
            start, end = raw_start, raw_end
        else:
            pad = 0.3
            start = max(0.0, raw_start - pad)
            end = raw_end + pad
            if i < len(segments) - 1:
                end = min(end, segments[i + 1]["start"] - 0.05)

        alpha = (
            f"if(lt(t,{start}+0.1),(t-{start})/0.1,"
            f"if(gt(t,{end}-0.1),({end}-t)/0.1,1))"
        )

        lines = _wrap_text(text, chars_per_line)
        num_lines = len(lines)
        line_height = font_size + box_border + line_spacing
        total_text_h = num_lines * line_height
        base_y = f"(h-{total_text_h})*0.92"

        for li, line in enumerate(lines):
            line_y = f"({base_y})+{li}*{line_height}"
            dt = _make_drawtext_params(
                text=line, font_size=font_size, fontcolor="white",
                x_expr="(w-text_w)/2", y_expr=line_y,
                start_t=start, end_t=end,
                fontfile=font_path,
                borderw=3, bordercolor="black@0.7",
                box=1, boxcolor="black@0.4", boxborderw=box_border,
                alpha_expr=alpha,
            )
            dt_parts.append(dt)

    return ",".join(dt_parts) if dt_parts else ""


# ═══════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════

def _detect_fps(video_path: str) -> float:
    """检测视频帧率,用于 xfade 的恒定帧率要求"""
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", video_path],
            capture_output=True, timeout=15, check=False,
        )
        out = (r.stdout + r.stderr).decode("utf-8", errors="replace")
        # 匹配 "25 fps" 或 "30000/1001" 等
        fps_m = re.search(r"(\d+(?:\.\d+)?)\s*(?:fps|tb\(r\))", out)
        if fps_m:
            fps = float(fps_m.group(1))
            if 10 <= fps <= 120:
                return round(fps, 1)
        # 尝试匹配分数形式
        fps_m2 = re.search(r"(\d+)/(\d+)\s*tb", out)
        if fps_m2:
            fps = float(fps_m2.group(1)) / float(fps_m2.group(2))
            if 10 <= fps <= 120:
                return round(fps, 1)
    except Exception:
        pass
    return 30.0  # 默认


def _double_bitrate(br: str) -> str:
    """将 '8M' 变成 '16M', '1.5M' 变成 '3M'"""
    m = re.match(r"([\d.]+)([kKmM])", br)
    if m:
        v = float(m.group(1)) * 2
        return f"{v}{m.group(2)}"
    return br


def _parse_json(data) -> any:
    """安全解析 JSON 字符串或直接返回已解析对象"""
    if data is None or data == "":
        return None
    if isinstance(data, bool):
        return None  # bool 是 int 的子类,单独处理避免误判
    if isinstance(data, str):
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return None
    return data


# ═══════════════════════════════════════════════════════
#  硬件编码检测
# ═══════════════════════════════════════════════════════

def _detect_encoders() -> dict:
    """一次性检测所有可用的硬件编码器,返回 dict

    每个条目: {'codec': 'h264_nvenc', 'type': 'nvenc', 'preset': 'p4'}
    """
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, timeout=10, check=False,
        )
        encoders = (r.stdout + r.stderr).decode("utf-8", errors="replace")
    except Exception:
        encoders = ""

    available = {}

    # H.264
    if "h264_nvenc" in encoders:
        available["h264"] = {"codec": "h264_nvenc", "type": "nvenc", "label": "NVIDIA NVENC"}
    elif "h264_qsv" in encoders:
        available["h264"] = {"codec": "h264_qsv", "type": "qsv", "label": "Intel QSV"}
    elif "h264_amf" in encoders:
        available["h264"] = {"codec": "h264_amf", "type": "amf", "label": "AMD AMF"}

    # HEVC
    if "hevc_nvenc" in encoders:
        available["hevc"] = {"codec": "hevc_nvenc", "type": "nvenc", "label": "NVIDIA NVENC HEVC"}
    elif "hevc_qsv" in encoders:
        available["hevc"] = {"codec": "hevc_qsv", "type": "qsv", "label": "Intel QSV HEVC"}
    elif "hevc_amf" in encoders:
        available["hevc"] = {"codec": "hevc_amf", "type": "amf", "label": "AMD AMF HEVC"}

    return available


def _get_encoder_params(codec_info: dict, quality: str = "high") -> tuple[str, list[str]]:
    """根据编码器信息返回 (codec, params_list)

    quality: "high" = 接近无损, "medium" = 平衡, "fast" = 预览
    """
    codec = codec_info["codec"]
    etype = codec_info["type"]

    if etype == "nvenc":
        if quality == "high":
            return codec, ["-qp", "18", "-preset", "p4", "-rc", "vbr"]
        elif quality == "medium":
            return codec, ["-qp", "23", "-preset", "p4"]
        else:
            return codec, ["-qp", "28", "-preset", "p1"]
    elif etype == "qsv":
        if quality == "high":
            return codec, ["-global_quality", "18", "-preset", "slower"]
        elif quality == "medium":
            return codec, ["-global_quality", "23", "-preset", "medium"]
        else:
            return codec, ["-global_quality", "28", "-preset", "fast"]
    elif etype == "amf":
        if quality == "high":
            return codec, ["-qp_i", "18", "-qp_p", "20", "-quality", "quality"]
        elif quality == "medium":
            return codec, ["-qp_i", "23", "-qp_p", "25", "-quality", "balanced"]
        else:
            return codec, ["-qp_i", "28", "-qp_p", "30", "-quality", "speed"]
    else:
        # 软件编码
        if quality == "high":
            return "libx264", ["-crf", "18", "-preset", "slow"]
        elif quality == "medium":
            return "libx264", ["-crf", "23", "-preset", "medium"]
        else:
            return "libx264", ["-crf", "28", "-preset", "ultrafast"]


def _check_nvenc() -> bool:
    """检查 NVENC 是否可用（从硬件画像读取）"""
    cfg = _get_render_config()
    enc = cfg.get("compress", {}).get("encoder", "")
    return bool(enc and "nvenc" in enc)


def _cleanup_tmp(grade_files: list, concat_path: str = "", tmp_dir: str = ""):
    """清理临时文件"""
    for f in grade_files:
        try:
            os.remove(f)
        except:
            pass
    if concat_path:
        try:
            os.remove(concat_path)
        except:
            pass
    # 清理唯一临时子目录
    if tmp_dir:
        try:
            os.rmdir(tmp_dir)  # 仅当目录为空时删除
        except:
            pass


# ═══════════════════════════════════════════════════════
#  add_subtitles(保留,独立预览用)
# ═══════════════════════════════════════════════════════

@tool(
    name="add_subtitles",
    description="给视频添加字幕(独立预览用)",
    phase="edit",
    category="render",
    tags=["render"],
    group="语音与转写",
)
def add_subtitles(video_path: str, transcript_json: str, font_name: str = "") -> str:
    """给视频添加字幕(独立预览用,render_final 已内置字幕功能)"""
    if not video_path:
        return json.dumps({"error": "video_path 为空"}, ensure_ascii=False)
    if not os.path.exists(video_path):
        return json.dumps({"error": f"视频文件不存在: {video_path}"}, ensure_ascii=False)
    from director.tools.effects import find_font
    from director.tools.mask import _get_video_dimensions

    segments = _parse_json(transcript_json) or []
    if not segments:
        return "无字幕数据"

    vw, vh = _get_video_dimensions(video_path)
    if not vw or not vh:
        vw, vh = 1080, 1920
    font_size = max(50, min(180, int(vh * 0.042)))
    box_border = max(6, font_size // 12)
    line_spacing = max(2, int(font_size * 0.02))
    max_text_width = vw * 0.82
    chars_per_line = max(4, int(max_text_width / font_size))

    font_path = find_font(font_name) if font_name else _find_chinese_font()
    if font_path:
        font_path = _sanitize_font_path(font_path)
    filters = []
    for i, seg in enumerate(segments):
        raw_start, raw_end, text = seg.get("start", 0), seg.get("end", 1), seg.get("text", "")
        if not text:
            continue

        if i < len(segments) - 1:
            gap = segments[i + 1]["start"] - raw_end
        else:
            gap = 2.0

        if gap >= 0.3:
            start, end = raw_start, raw_end
        else:
            pad = 0.3
            start = max(0.0, raw_start - pad)
            end = raw_end + pad
            if i < len(segments) - 1:
                end = min(end, segments[i + 1]["start"] - 0.05)

        alpha = f"if(lt(t,{start}+0.1),(t-{start})/0.1,if(gt(t,{end}-0.1),({end}-t)/0.1,1))"

        lines = _wrap_text(text, chars_per_line)
        num_lines = len(lines)
        line_height = font_size + box_border + line_spacing
        total_text_h = num_lines * line_height
        base_y = f"(h-{total_text_h})*0.92"

        for li, line in enumerate(lines):
            line_y = f"({base_y})+{li}*{line_height}"
            dt = _make_drawtext_params(
                text=line, font_size=font_size, fontcolor="white",
                x_expr="(w-text_w)/2", y_expr=line_y,
                start_t=start, end_t=end,
                fontfile=font_path,
                borderw=3, bordercolor="black@0.7",
                box=1, boxcolor="black@0.4", boxborderw=box_border,
                alpha_expr=alpha,
            )
            filters.append(dt)

    if not filters:
        return "无字幕可添加"

    vcodec, vparams = _select_encoder("render", hw_fallback="libx264")

    out_path = video_path.replace(".mp4", "_subs.mp4")
    result = subprocess.run([
        "ffmpeg", "-y", "-i", video_path,
        "-vf", ",".join(filters),
        "-c:v", vcodec, *vparams,
        "-c:a", "copy", "-movflags", "+faststart", out_path,
    ], capture_output=True, timeout=600)

    if result.returncode == 0 and os.path.exists(out_path):
        return f"✅ 字幕添加完成: {out_path}"
    return "字幕添加失败"


# ═══════════════════════════════════════════════════════
#  drawtext 工具函数
# ═══════════════════════════════════════════════════════

def _escape_dt_text(text: str) -> str:
    """转义 drawtext text 参数中的特殊字符"""
    text = text.replace("'", "'")
    text = text.replace("%", "\\%")        # % 是 drawtext 表达式前缀
    text = text.replace("{", "\\{")        # { } 是表达式分隔符
    text = text.replace("}", "\\}")
    text = text.replace(":", "\\:")
    text = text.replace(";", "")
    text = text.replace("\n", " ").replace("\r", "")
    return text


def _escape_dt_expr(expr: str) -> str:
    return expr.replace(",", "\\,")


def _make_drawtext_params(
    text: str, font_size: int, fontcolor: str,
    x_expr: str, y_expr: str, start_t: float, end_t: float,
    fontfile: str = "", borderw: int = 0, bordercolor: str = "",
    box: int = 0, boxcolor: str = "", boxborderw: int = 0,
    alpha_expr: str = "",
) -> str:
    """构建 drawtext filter 参数字符串"""
    parts = [f"drawtext=text={_escape_dt_text(text)}"]
    if fontfile:
        parts.append(f"fontfile={fontfile}")
    parts.append(f"fontsize={font_size}")
    parts.append(f"fontcolor={fontcolor}")
    if borderw > 0 and bordercolor:
        parts.append(f"borderw={borderw}")
        parts.append(f"bordercolor={bordercolor}")
    if box:
        parts.append(f"box={box}")
        parts.append(f"boxcolor={boxcolor}")
        parts.append(f"boxborderw={boxborderw}")
    if x_expr:
        parts.append(f"x={_escape_dt_expr(x_expr)}")
    if y_expr:
        parts.append(f"y={_escape_dt_expr(y_expr)}")
    if alpha_expr:
        parts.append(f"alpha={_escape_dt_expr(alpha_expr)}")
    parts.append(f"enable={_escape_dt_expr(f'between(t,{start_t},{end_t})')}")
    return ":".join(parts)


def _wrap_text(text: str, chars_per_line: int) -> list[str]:
    """自动换行,优先在标点处断开"""
    if len(text) <= chars_per_line:
        return [text]
    lines = []
    while text:
        if len(text) <= chars_per_line:
            lines.append(text)
            break
        cut = chars_per_line
        for pos in range(chars_per_line, max(chars_per_line // 2, 1) - 1, -1):
            if pos < len(text) and text[pos] in ',.,;:?!),.?!;:)]>」』】)':
                cut = pos + 1
                break
        lines.append(text[:cut].strip())
        text = text[cut:].strip()
    return [l for l in lines if l]


def _sanitize_font_path(font_path: str) -> str:
    """去掉盘符冒号 + 反斜杠转正斜杠,避免 -vf 中冒号被当参数分隔符"""
    if not font_path:
        return ""
    # 去掉盘符冒号(C: D: E: -> '')
    font_path = re.sub(r'^[A-Za-z]:', '', font_path)
    font_path = font_path.replace("\\", "/")
    return font_path


def _find_chinese_font() -> str:
    """查找系统可用的中文字体(优先 TTF,drawtext 兼容性更好)"""
    windir = os.environ.get("WINDIR", "C:\\Windows")
    candidates = [
        os.path.join(windir, "Fonts", "simhei.ttf"),
        os.path.join(windir, "Fonts", "msyh.ttc"),
        os.path.join(windir, "Fonts", "msyhbd.ttc"),
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            return _sanitize_font_path(p)
    return ""


# ═══════════════════════════════════════════════════════
#  草稿模式渲染
# ═══════════════════════════════════════════════════════


@tool(
    name="render_from_draft",
    description=(
        "从草稿渲染最终视频(推荐方式).读取草稿中累积的所有效果——裁剪,抠像,调色,"
        "动画,叠层,花字,字幕,BGM,音效——一次性渲染导出."
        "这是搭积木模式的最终出口,最后一步只用它."
    ),
    phase="render",
    category="render",
    tags=["render"],
    group="渲染输出",
)
def render_from_draft(
    draft_id: str = "",
    output_path: str = "",
    version: int = 0,
    preset: str = "",
    fill_mode: str = "blur",
) -> str:
    """
    从草稿渲染最终视频.读取草稿中累积的所有效果直接导出.

    这是「搭积木」模式的最终出口——草稿里已经记录了每一步操作,
    渲染器只需忠实地把草稿的内容拼出来.

    Args:
        draft_id: 草稿 ID(必填)
        output_path: 输出路径(可选,默认自动生成)
        version: 草稿版本号(0=最新版)
        preset: 输出格式预设(可选),如 "douyin" "bilibili_1080p"
        fill_mode: 比例不匹配时的填充方式."blur"(默认)或 "black"

    Returns:
        结果信息
    """
    from director.draft import Draft

    draft = Draft(draft_id)
    data = draft.load_version(version) if version > 0 else draft.load()
    if data is None:
        return f"❌ 草稿 {draft_id} 不存在"

    tl = data["timeline"]
    main_track = tl.get("main_track", {})
    segments = main_track.get("segments", [])
    transitions = main_track.get("transitions", [])
    subtitles = tl.get("subtitles", [])
    overlays = tl.get("overlay_track", [])
    graphics = tl.get("graphic_track", [])
    flower_texts = tl.get("flower_texts", [])
    audio_cfg = tl.get("audio", data.get("audio", {}))  # 兼容旧位置

    if not segments:
        return "❌ 草稿中没有片段,无法渲染"

    src_videos = data.get("source_videos", [])
    if src_videos:
        video_path = src_videos[0]
    else:
        # 从片段的 original_source 推导源素材路径
        source_set = set()
        for s in segments:
            src = s.get("original_source", s.get("path", ""))
            if src:
                source_set.add(src)
        if source_set:
            video_path = list(source_set)[0]
        else:
            return "❌ 草稿中没有源素材信息(片段缺少 original_source)"

    video_path = src_videos[0]  # 多素材需要更复杂的处理,先取第一个

    # ── 构建 render_final 所需参数 ──

    # arrangement: 从 segments 提取时间范围
    # 使用 original_source/original_start/original_end 实现代理→原画替换
    arrangement_json = json.dumps([{
        "start": s.get("original_start", s.get("start", 0)),
        "end": s.get("original_end", s.get("end", 30)),
        "id": s.get("id"),
        "duration": s.get("duration", 30),
        "file_path": s.get("original_source", s.get("path", "")),
    } for s in segments])

    # grade: 从 filter.color_grading 提取
    grades = {}
    for s in segments:
        sid = s.get("id")
        cg = s.get("filters", {}).get("color_grading")
        if cg:
            grades[str(sid)] = cg

    # animation: 从 filter.animation 提取
    animations = []
    for s in segments:
        anim = s.get("filters", {}).get("animation")
        if anim and isinstance(anim, dict):
            anim_copy = dict(anim)
            anim_copy.setdefault("start", s.get("start", 0))
            anim_copy.setdefault("end", s.get("end", 30))
            animations.append(anim_copy)

    # 转场
    transitions_json = json.dumps(transitions) if transitions else ""

    # 音频
    bgm_path = ""
    bgm_volume = 0.4
    bgm_ducking = True
    vocal_track = ""
    if audio_cfg:
        bgm_info = audio_cfg.get("bgm")
        if bgm_info and isinstance(bgm_info, dict):
            bgm_path = bgm_info.get("source", "")
            bgm_volume = bgm_info.get("volume", 0.4)
        bgm_ducking = audio_cfg.get("bgm_ducking", True)
        vocal_track = audio_cfg.get("vocal_track", "") or ""

    # 字幕
    subtitle_json = json.dumps(subtitles) if subtitles else ""

    # ── 收集所有叠层(花字渲染后 -> 叠层列表,加上 overlay_track 和 graphic_track)──
    overlay_list = []

    # 花字:遍历全部,每个花字渲染出自己的花字视频后作为叠层加入
    for ft in flower_texts:
        try:
            from director.tools.effects import apply_flower_text
            ft_result = apply_flower_text(
                flower_id=ft.get("flower_id", "fx_word_reveal"),
                text=ft.get("text", ""),
                font_path=ft.get("font_path", ""),
                video_path=video_path,
                start_time=ft.get("time", 0.5),
                duration=ft.get("duration", 3.0),
            )
            m = re.search(r'([A-Za-z]:\\[^\n]+?\.mp4)', ft_result)
            if m:
                overlay_list.append({
                    "source_path": m.group(1),
                    "start_time": ft.get("time", 0.5),
                    "duration": ft.get("duration", 3.0),
                    "x": ft.get("x", 0.5),
                    "y": ft.get("y", 0.5),
                    "width": 1.0,
                    "height": 1.0,
                    "opacity": 1.0,
                })
        except Exception as e:
            print(f"  ⚠ 花字渲染失败(跳过): {e}")

    # overlay_track(画中画,双屏)
    for ov in overlays:
        if os.path.exists(ov.get("source_path", "")):
            overlay_list.append({
                "source_path": ov["source_path"],
                "start_time": ov.get("start_time", 0),
                "duration": ov.get("duration", 0),
                "x": ov.get("x", 0.6),
                "y": ov.get("y", 0.6),
                "width": ov.get("width", 0.35),
                "height": ov.get("height", 0.35),
                "opacity": ov.get("opacity", 1.0),
            })

    # graphic_track(贴图,Logo)
    for g in graphics:
        if os.path.exists(g.get("source_path", "")):
            overlay_list.append({
                "source_path": g["source_path"],
                "start_time": g.get("start_time", 0),
                "duration": g.get("duration", 0),
                "x": g.get("x", 0.5),
                "y": g.get("y", 0.5),
                "width": g.get("width", 0.2),
                "height": g.get("height", 0.2),
                "opacity": g.get("opacity", 1.0),
            })

    # ── SFX(音效)──
    sfx_list = []
    if audio_cfg:
        for sfx_item in audio_cfg.get("sfx", []):
            sfx_path = sfx_item.get("source", "")
            if os.path.exists(sfx_path):
                sfx_list.append({
                    "source": sfx_path,
                    "start_time": sfx_item.get("start_time", 0),
                    "duration": sfx_item.get("duration", 0),
                    "volume": sfx_item.get("volume", 1.0),
                })

    # ── 调用 render_final(传新参数,花字全走 overlays_json,不再用 flower_text_video_path)──
    result = render_final(
        video_path=video_path,
        arrangement_json=arrangement_json,
        grade_json=json.dumps(grades) if grades else "",
        transitions_json=transitions_json,
        bgm_path=bgm_path,
        bgm_volume=bgm_volume if isinstance(bgm_volume, (int, float)) else 0.4,
        subtitle_json=subtitle_json,
        bgm_ducking=bgm_ducking,
        animations_json=json.dumps(animations) if animations else "",
        vocal_track_path=vocal_track,
        overlays_json=json.dumps(overlay_list) if overlay_list else "",
        sfx_json=json.dumps(sfx_list) if sfx_list else "",
        output_path=output_path,
        preset=preset,
        fill_mode=fill_mode,
    )

    return result


# ═══════════════════════════════════════════════════════
#  工具定义
# ═══════════════════════════════════════════════════════

# 工具已通过 @tool 装饰器自动注册到 Registry
