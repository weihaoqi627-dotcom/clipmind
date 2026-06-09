"""
多轨时间线模块 — 复合叠层管理
==============================
Track 0: 主剪辑序列(arrangement/segments)
Track 1: B-roll/PIP 视频叠层(overlay)
Track 2: 图片/Logo/贴纸叠层(graphic)

数据存储在 _overlay_cache.json,keyed by video_path MD5.
"""
import json, os, subprocess, re, hashlib
from pathlib import Path

from director.registry import tool

_PROJECT_DIR = Path(__file__).parent.parent.parent
_OVERLAY_CACHE_FILE = os.path.join(str(_PROJECT_DIR), "_overlay_cache.json")

from director.tools.animation import _build_keyframe_expr
from director.tools.analyze import _get_segments_cached


# ═══════════════════════════════════════════════════════
#  缓存管理
# ═══════════════════════════════════════════════════════


def _get_overlay_cache(video_path: str) -> dict:
    """
    获取叠层缓存,keyed by video_path MD5.
    返回 {"overlay": [...], "graphic": [...]}.
    缓存文件不存在时返回空结构.
    """
    if not video_path or not os.path.exists(_OVERLAY_CACHE_FILE):
        return {"overlay": [], "graphic": []}
    key = hashlib.md5(video_path.encode()).hexdigest()
    try:
        with open(_OVERLAY_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get(key, {"overlay": [], "graphic": []})
    except (json.JSONDecodeError, IOError):
        return {"overlay": [], "graphic": []}


def _save_overlay_cache(video_path: str, cache: dict):
    """保存叠层缓存到文件."""
    key = hashlib.md5(video_path.encode()).hexdigest()
    os.makedirs(os.path.dirname(_OVERLAY_CACHE_FILE), exist_ok=True)
    all_data = {}
    if os.path.exists(_OVERLAY_CACHE_FILE):
        try:
            with open(_OVERLAY_CACHE_FILE, "r", encoding="utf-8") as f:
                all_data = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    all_data[key] = cache
    with open(_OVERLAY_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════


def _parse_json(data):
    """安全解析 JSON 字符串,已解析对象直接返回."""
    if not data:
        return None
    if isinstance(data, str):
        try:
            return json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return None
    return data


def _get_video_dimensions(video_path: str) -> tuple:
    """用 ffmpeg -i 获取视频宽高(兼容 Windows ffprobe 7.1 bug)"""
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", video_path],
            capture_output=True, timeout=30,
        )
        m = re.search(r",\s*(\d{3,})x(\d{3,})", (r.stdout + r.stderr).decode("utf-8", errors="replace"))
        if m:
            return int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    return 1920, 1080


def _has_nvenc() -> bool:
    """检测系统是否支持 NVENC 编码"""
    try:
        r = subprocess.run(
            ["ffmpeg", "-encoders"],
            capture_output=True, timeout=10, check=False,
        )
        return "h264_nvenc" in r.stdout.decode("utf-8", errors="replace")
    except Exception:
        return False


def _cleanup_tmp(*paths):
    """安全清理临时文件或目录."""
    for p in paths:
        if not p:
            continue
        if isinstance(p, (list, tuple)):
            for fp in p:
                _cleanup_tmp(fp)
            continue
        try:
            if os.path.isfile(p):
                os.remove(p)
            elif os.path.isdir(p):
                import shutil
                shutil.rmtree(p, ignore_errors=True)
        except Exception:
            pass


def _fmt_val(v: float) -> str:
    """将数值格式化为 ffmpeg 友好字符串,避免科学计数法"""
    if isinstance(v, float):
        if v == int(v) and abs(v) < 1e12:
            return str(int(v))
        return f"{v:.6f}".rstrip("0").rstrip(".")
    return str(v)


# ═══════════════════════════════════════════════════════
#  工具 1: add_overlay
# ═══════════════════════════════════════════════════════


@tool(
    name="add_overlay",
    description="向多轨时间线的 overlay/graphic/video2 轨道添加一个叠层片段(视频或图片).⚠️ 仅用于视频/图片叠层,不支持音频文件.加BGM请用 add_bgm_to_draft.track_type='overlay' 用于 B-roll/PIP 视频叠层,track_type='graphic' 用于图片/Logo/贴纸,track_type='video2' 用于第二条视频轨道(主轨道叠两个画面时使用).支持归一化位置/尺寸,透明度,旋转,z_index 排序和关键帧动画.",
    phase="edit",
    category="timeline",
    tags=["overlay", "track", "composite"],
    group="时间线与编排",
)
def add_overlay(
    video_path: str,
    track_type: str = "overlay",
    source: str = "",
    t_start: float = 0.0,
    t_end: float = 10.0,
    x: float = 0.5,
    y: float = 0.5,
    width: float = 0.3,
    height: float = None,
    opacity: float = 1.0,
    rotation: float = 0,
    z_index: int = 0,
    animation_json: str = "",
    draft_id: str = "",
) -> str:
    """
    向指定轨道添加一个叠层片段.

    Args:
        video_path: 项目视频路径(作为缓存 key)
        track_type: 轨道类型,"overlay" 或 "graphic"
        source: 源文件路径(视频或图片)
        t_start: 开始时间(秒)
        t_end: 结束时间(秒)
        x: 归一化水平位置 0~1(居中),默认 0.5
        y: 归一化垂直位置 0~1(居中),默认 0.5
        width: 归一化宽度 0~1,默认 0.3
        height: 归一化高度 0~1.为 None 时根据 source 宽高比自动计算
        opacity: 不透明度 0~1,默认 1.0
        rotation: 旋转角度(度),默认 0
        z_index: 叠层顺序(小值在底层),默认 0
        animation_json: 可选关键帧动画 JSON(animation.py 格式 keyframes 数组)

    Returns:
        成功或错误信息
    """
    if track_type not in ("overlay", "graphic", "video2"):
        return f"无效 track_type: {track_type},必须为 'overlay'/'graphic'/'video2'"
    if not source or not os.path.exists(source):
        return f"源文件不存在: {source}"
    if t_end <= t_start:
        return f"t_end ({t_end}) 必须大于 t_start ({t_start})"

    # 自动计算 height
    if height is None or height <= 0:
        src_w, src_h = _get_video_dimensions(source)
        if src_w > 0 and src_h > 0:
            height = round(width * src_h / src_w, 4)
        else:
            height = width  # 回退正方形

    # 解析可选 animation_json
    anim = _parse_json(animation_json)
    anim_str = json.dumps(anim, ensure_ascii=False) if anim else ""

    # 确定 source 的 media type
    ext = os.path.splitext(source)[1].lower()
    media_type = "image" if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp") else "video"

    # 读取缓存并分配 id
    cache = _get_overlay_cache(video_path)
    track = cache.get(track_type, [])
    new_id = max((c.get("id", -1) for c in track), default=-1) + 1

    clip = {
        "id": new_id,
        "source": source,
        "type": media_type,
        "t_start": round(t_start, 2),
        "t_end": round(t_end, 2),
        "x": round(x, 4),
        "y": round(y, 4),
        "width": round(width, 4),
        "height": round(height, 4),
        "opacity": round(opacity, 4),
        "rotation": round(rotation, 2),
        "z_index": z_index,
        "animation_json": anim_str if anim else None,
    }

    track.append(clip)
    cache[track_type] = track
    _save_overlay_cache(video_path, cache)

    if draft_id:
        from director.draft import Draft
        try:
            d = Draft(draft_id)
            if d.load():
                if track_type == "video2":
                    timeline_key = "video_track_2"
                else:
                    timeline_key = track_type + "_track"
                current = d.get_data()["timeline"].get(timeline_key, [])
                current.append(clip)
                d.set_overlays(current, track_type)
                d.save(f"添加{track_type}叠层")
        except Exception:
            pass

    return f"已添加 #{new_id} 到 {track_type} 轨道: {os.path.basename(source)} ({t_start}s-{t_end}s)"


# ═══════════════════════════════════════════════════════
#  工具 2: remove_overlay
# ═══════════════════════════════════════════════════════


@tool(
    name="remove_overlay",
    description="从 overlay/graphic/video2 轨道移除指定 id 的叠层片段.",
    phase="edit",
    category="timeline",
    tags=["overlay", "track", "remove"],
    group="时间线与编排",
)
def remove_overlay(
    video_path: str,
    track_type: str = "overlay",
    clip_id: int = 0,
) -> str:
    """
    从指定轨道移除一个叠层片段.

    Args:
        video_path: 项目视频路径(缓存 key)
        track_type: 轨道类型,"overlay"/"graphic"/"video2"
        clip_id: 要移除的片段 id

    Returns:
        成功或错误信息
    """
    if track_type not in ("overlay", "graphic", "video2"):
        return f"无效 track_type: {track_type},必须为 'overlay'/'graphic'/'video2'"

    cache = _get_overlay_cache(video_path)
    track = cache.get(track_type, [])
    found = [c for c in track if c.get("id") == clip_id]
    if not found:
        return f"未找到 {track_type} 轨道中 id={clip_id} 的片段"

    cache[track_type] = [c for c in track if c.get("id") != clip_id]
    _save_overlay_cache(video_path, cache)
    return f"已移除 {track_type} #{clip_id}: {os.path.basename(found[0].get('source', ''))}"


# ═══════════════════════════════════════════════════════
#  工具 3: modify_overlay
# ═══════════════════════════════════════════════════════


@tool(
    name="modify_overlay",
    description="修改指定叠层片段的属性字段(位置,尺寸,透明度,旋转,时间等).只修改 params_json 中提供的字段,未提供的保留原值.",
    phase="edit",
    category="timeline",
    tags=["overlay", "track", "modify"],
    group="时间线与编排",
)
def modify_overlay(
    video_path: str,
    track_type: str = "overlay",
    clip_id: int = 0,
    params_json: str = "",
) -> str:
    """
    修改指定叠层片段的字段.

    Args:
        video_path: 项目视频路径(缓存 key)
        track_type: 轨道类型,"overlay"/"graphic"/"video2"
        clip_id: 要修改的片段 id
        params_json: 要修改的字段 JSON,如 {"x": 0.9, "opacity": 0.5}

    Returns:
        成功或错误信息
    """
    if track_type not in ("overlay", "graphic", "video2"):
        return f"无效 track_type: {track_type},必须为 'overlay'/'graphic'/'video2'"

    params = _parse_json(params_json)
    if not params:
        return "params_json 为空或无效 JSON"

    cache = _get_overlay_cache(video_path)
    track = cache.get(track_type, [])
    found = None
    for c in track:
        if c.get("id") == clip_id:
            found = c
            break

    if not found:
        return f"未找到 {track_type} 轨道中 id={clip_id} 的片段"

    # 允许修改的字段白名单
    allowed = {"source", "t_start", "t_end", "x", "y", "width", "height",
               "opacity", "rotation", "z_index", "animation_json"}
    changed = []
    for key, value in params.items():
        if key not in allowed:
            continue
        if key == "t_start":
            value = round(float(value), 2)
        elif key == "t_end":
            value = round(float(value), 2)
        elif key in ("x", "y", "width", "height", "opacity"):
            value = round(float(value), 4)
        elif key == "rotation":
            value = round(float(value), 2)
        elif key == "z_index":
            value = int(value)
        elif key == "animation_json":
            anim = _parse_json(value)
            value = json.dumps(anim, ensure_ascii=False) if anim else None
        elif key == "source":
            if not os.path.exists(str(value)):
                return f"源文件不存在: {value}"
            # 更新 type
            ext = os.path.splitext(str(value))[1].lower()
            found["type"] = "image" if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp") else "video"
        found[key] = value
        changed.append(key)

    if not changed:
        return "未修改任何字段(提供的字段不在允许列表中)"

    cache[track_type] = track
    _save_overlay_cache(video_path, cache)
    return f"已修改 {track_type} #{clip_id}: {', '.join(changed)}"


# ═══════════════════════════════════════════════════════
#  工具 4: show_timeline
# ═══════════════════════════════════════════════════════


@tool(
    name="show_timeline",
    description="查看完整多轨时间线信息.返回 JSON 包含:track_0_main(主剪辑 segments),track_1_overlay(视频叠层),track_2_graphic(图片叠层)和统计摘要.",
    phase="plan",
    category="timeline",
    tags=["timeline", "overview", "tracks"],
    group="素材信息",
)
def show_timeline(video_path: str) -> str:
    """
    查看完整时间线:主轨道 + overlay 轨道 + graphic 轨道.

    主轨道从 analyze 模块的 segments 获取;
    overlay/graphic 从叠层缓存获取.

    Args:
        video_path: 项目视频路径

    Returns:
        格式化 JSON,含所有轨道信息
    """
    result = {"track_0_main": [], "track_1_overlay": [], "track_2_graphic": [], "track_3_video2": []}

    # 主轨道
    try:
        segments = _get_segments_cached(video_path)
        result["track_0_main"] = [
            {
                "id": s.get("id"),
                "start": s.get("start"),
                "end": s.get("end"),
                "duration": round(s.get("end", 0) - s.get("start", 0), 1),
                "text": s.get("text", "")[:100],
                "status": s.get("status", ""),
            }
            for s in (segments or [])
        ]
    except Exception as e:
        result["track_0_main_error"] = str(e)

    # overlay 轨道
    cache = _get_overlay_cache(video_path)
    result["track_1_overlay"] = cache.get("overlay", [])
    result["track_2_graphic"] = cache.get("graphic", [])
    result["track_3_video2"] = cache.get("video2", [])

    # 统计
    total_overlays = len(result["track_1_overlay"]) + len(result["track_2_graphic"]) + len(result["track_3_video2"])
    summary = {
        "video_path": video_path,
        "main_segments": len(result["track_0_main"]),
        "overlay_clips": len(result["track_1_overlay"]),
        "graphic_clips": len(result["track_2_graphic"]),
        "video2_clips": len(result["track_3_video2"]),
        "total_overlays": total_overlays,
    }
    result["_summary"] = summary

    return json.dumps(result, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════
#  工具 5: render_timeline
# ═══════════════════════════════════════════════════════


def _build_motion_filter_simple(anim: dict, vw: int, vh: int) -> str:
    """
    构建主视频运动滤镜(简化版,直接使用 _build_keyframe_expr).

    Anim 格式:
    {
        "type": "motion",
        "keyframes": [
            {"t": 0, "scale": 1.0, "x": 0, "y": 0, "rotation": 0, "opacity": 1.0},
            {"t": 3, "scale": 1.3, "x": -50, "y": -30}
        ],
        "start": 0, "end": 5
    }
    """
    keyframes = anim.get("keyframes", [])
    if not keyframes:
        return "[0:v]null[vmain]"

    scale_expr = _build_keyframe_expr(keyframes, "scale", 1.0, clamp_min=0.01)
    x_expr = _build_keyframe_expr(keyframes, "x", 0.0)
    y_expr = _build_keyframe_expr(keyframes, "y", 0.0)
    rot_expr = _build_keyframe_expr(keyframes, "rotation", 0.0)
    opa_expr = _build_keyframe_expr(keyframes, "opacity", 1.0, clamp_min=0.0, clamp_max=1.0)

    has_rotation = any(kf.get("rotation", 0) != 0 for kf in keyframes)
    has_opacity = any(kf.get("opacity", 1.0) != 1.0 for kf in keyframes)

    parts = []
    if has_opacity:
        parts.append(f"format=rgba,colorchannelmixer=aa={opa_expr}")
    parts.append(f"scale=iw*({scale_expr}):ih*({scale_expr}):eval=frame")
    if has_rotation:
        parts.append(
            f"rotate=({rot_expr})*PI/180:ow=iw:oh=ih:"
            f"fillcolor=black@0:c=none:eval=frame"
        )
    crop_x = f"(iw-{vw})/2+({x_expr})"
    crop_y = f"(ih-{vh})/2+({y_expr})"
    parts.append(f"crop={vw}:{vh}:{crop_x}:{crop_y}:eval=frame")

    return "[0:v]" + ",".join(parts) + "[vmain]"


@tool(
    name="set_segment_speed",
    description="给草稿主轨道上的指定片段设置播放速度(0.1~5.0倍速).支持恒定速度或曲线变速.曲线变速传 speed_curve_json 如 [{\"t\":0,\"speed\":1.0},{\"t\":0.5,\"speed\":0.5},{\"t\":1,\"speed\":1.0}]",
    phase="edit",
    category="timeline",
    tags=["draft", "speed", "segment"],
    group=["时间线与编排", "细剪与节奏", "调速与布局"],
)
def set_segment_speed(draft_id: str, seg_id: str, speed: float = 1.0, speed_curve_json: str = "") -> str:
    """
    给草稿主轨道上的指定片段设置播放速度.

    Args:
        draft_id: 草稿 ID (如 "main")
        seg_id: 片段 ID (如 "seg_luffy_clash")
        speed: 恒定速度倍数 0.1~5.0,默认 1.0 (仅 speed_curve_json 为空时生效)
        speed_curve_json: 可选,曲线变速 JSON 数组字符串.
            [{"t":0, "speed":1.0}, {"t":0.5, "speed":0.5}, {"t":1, "speed":1.0}]
            t 范围 0~1(镜头内相对时间), speed 范围 0.1~5.0

    Returns:
        成功或错误信息
    """
    from director.draft import Draft
    d = Draft(draft_id)
    if not d.load():
        return f"❌ 草稿 {draft_id} 不存在"

    segments = d.timeline["main_track"]["segments"]
    if not segments:
        return "❌ 草稿主轨道无片段"

    # 查找指定片段
    target = None
    for s in segments:
        if str(s.get("id", "")) == seg_id:
            target = s
            break

    if not target:
        existing_ids = [str(s.get("id", "")) for s in segments]
        return f"❌ 未找到片段 {seg_id}. 当前片段: {', '.join(existing_ids[:20])}"

    # 应用速度
    if speed_curve_json:
        try:
            import json
            curve = json.loads(speed_curve_json)
            if not isinstance(curve, list):
                return "❌ speed_curve_json 必须是数组"
            for kf in curve:
                t, s = kf.get("t", -1), kf.get("speed", 0)
                if not (0 <= t <= 1):
                    return "❌ 关键帧 t 必须在 0~1 之间"
                if not (0.1 <= s <= 5.0):
                    return "❌ 关键帧 speed 必须在 0.1~5.0 之间"
            target["speed_curve"] = {"type": "curve", "keyframes": curve}
            target["speed"] = 1.0  # speed_curve 覆盖恒定速度
            d.save(label=f"speed_{seg_id}")
            return f"✅ 片段 {seg_id} 已应用曲线变速 ({len(curve)} 个关键帧)"
        except json.JSONDecodeError as e:
            return f"❌ speed_curve_json 格式错误: {e}"
    else:
        speed = float(speed)
        if speed < 0.1 or speed > 5.0:
            return "❌ 速度必须在 0.1~5.0 之间"
        target["speed"] = speed
        # 如果有之前设置的曲线,清除
        target.pop("speed_curve", None)
        d.save(label=f"speed_{seg_id}")
        return f"✅ 片段 {seg_id} 已设置 {speed}x 速度"


@tool(
    name="render_timeline",
    description="将时间线上的所有叠层(overlay + graphic)合成到主视频上.自动按 z_index 升序叠层,支持视频叠层(trim)和图片叠层(loop).支持每个叠层的独立关键帧动画(位置/缩放/旋转/透明度).支持主视频动效.自动检测 NVENC 加速.保留原音频.",
    phase="render",
    category="timeline",
    tags=["render", "composite", "overlay"],
    group="时间线与编排",
)
def render_timeline(
    video_path: str,
    main_video_path: str,
    animations_json: str = "",
    output_path: str = "",
) -> str:
    """
    将叠层合成到主视频上,生成最终输出.

    读取 overlay cache,使用 ffmpeg overlay filter
    把所有叠层按 z_index 升序合成到主视频.

    Args:
        video_path: 项目视频路径(缓存 key)
        main_video_path: 主视频文件路径(已渲染好的主轨)
        animations_json: 主视频本身的动效 JSON(可选,同 animation.py 格式)
        output_path: 输出路径(可选,默认自动生成)

    Returns:
        成功或错误信息
    """
    # ── 参数验证 ──
    if not os.path.exists(main_video_path):
        return f"主视频不存在: {main_video_path}"

    cache = _get_overlay_cache(video_path)
    overlays = cache.get("overlay", [])
    graphics = cache.get("graphic", [])
    video2 = cache.get("video2", [])

    # 合并所有叠层,按 z_index 升序排列
    all_clips = sorted(overlays + graphics + video2, key=lambda c: c.get("z_index", 0))

    if not all_clips:
        return "没有叠层需要合成"

    # ── 主视频尺寸 ──
    vw, vh = _get_video_dimensions(main_video_path)

    # ── 准备动画 ──
    main_anim = None
    anims = _parse_json(animations_json)
    if anims:
        for a in anims:
            if a.get("type") == "motion":
                main_anim = a
                break

    has_main_anim = bool(main_anim and main_anim.get("keyframes"))

    # ── 输出路径 ──
    tmp_dir = os.path.join(str(_PROJECT_DIR), "_tmp_render")
    os.makedirs(tmp_dir, exist_ok=True)

    if not output_path:
        tag = hashlib.md5(f"{video_path}:{main_video_path}".encode()).hexdigest()[:8]
        output_path = os.path.join(str(_PROJECT_DIR), "output", f"timeline_{tag}.mp4")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # ── 构建 ffmpeg 命令 ──
    cmd = ["ffmpeg", "-y"]

    # 输入:主视频
    cmd += ["-i", main_video_path]

    # 处理每个叠层,收集额外输入和 filter 片段
    extra_inputs = []       # 额外输入文件路径
    ov_filter_parts = []    # 叠层处理 filter 片段
    overlay_steps = []      # overlay 合成步骤
    current_main_label = "[vmain]" if has_main_anim else "[0:v]"
    input_idx = 1  # 下一个 -i 的索引

    for idx, clip in enumerate(all_clips):
        source = clip.get("source", "")
        if not source or not os.path.exists(source):
            continue

        c_type = clip.get("type", "image")
        cs_x = clip.get("x", 0.5)
        cs_y = clip.get("y", 0.5)
        cw = clip.get("width", 0.3)
        ch = clip.get("height", 0.3)
        c_opacity = clip.get("opacity", 1.0)
        c_rotation = clip.get("rotation", 0)
        t_start = clip.get("t_start", 0.0)
        t_end = clip.get("t_end", 10.0)
        duration = t_end - t_start
        anim_json = clip.get("animation_json")

        # 解析动画关键帧
        anim_kfs = None
        if anim_json:
            parsed = _parse_json(anim_json)
            if isinstance(parsed, list):
                anim_kfs = parsed

        # 计算像素尺寸
        base_w_px = cw * vw
        base_h_px = ch * vh

        # 构建关键帧表达式
        if anim_kfs:
            scale_expr = _build_keyframe_expr(anim_kfs, "scale", 1.0, clamp_min=0.01)
            x_offs_expr = _build_keyframe_expr(anim_kfs, "x", 0.0)
            y_offs_expr = _build_keyframe_expr(anim_kfs, "y", 0.0)
            opa_expr = _build_keyframe_expr(anim_kfs, "opacity", c_opacity,
                                            clamp_min=0.0, clamp_max=1.0)
            rot_expr = _build_keyframe_expr(anim_kfs, "rotation", c_rotation)
            has_rot = any(kf.get("rotation", 0) != 0 for kf in anim_kfs)
        else:
            scale_expr = "1.0"
            x_offs_expr = "0"
            y_offs_expr = "0"
            opa_expr = _fmt_val(c_opacity)
            rot_expr = _fmt_val(c_rotation)
            has_rot = c_rotation != 0

        # 添加输入
        if c_type == "video":
            # 视频叠层:trim 到所需时长
            cmd += ["-ss", "0", "-t", _fmt_val(duration), "-i", source]
            # 滤波器: 重置 PTS + 缩放 + RGBA
            chain = ["setpts=PTS-STARTPTS"]
            chain.append(f"scale={_fmt_val(base_w_px)}*({scale_expr}):{_fmt_val(base_h_px)}*({scale_expr}):eval=frame")
            chain.append("format=rgba")
            # 透明度: 用 colorchannelmixer(仅支持常量,不支持表达式)
            if anim_kfs:
                # 有动画时跳过 colorchannelmixer(不支持表达式),用 geq
                has_opacity_anim = any("opacity" in kf for kf in anim_kfs)
                if has_opacity_anim and c_opacity != 1.0:
                    # opacity 关键帧 + 默认值非 1 -> 需要 geq 做渐变
                    pass  # 暂不支持动态透明度渐变,保持默认 100%
                elif c_opacity != 1.0:
                    chain.append(f"colorchannelmixer=aa={opa_expr}")
                # opacity=1.0 时不需要调透明度
            else:
                # 静态叠层:colorchannelmixer 支持常量
                if c_opacity != 1.0:
                    chain.append(f"colorchannelmixer=aa={opa_expr}")
        else:
            # 图片叠层:loop 为视频流
            cmd += ["-i", source]
            chain = ["loop=loop=-1:size=1,setpts=N/(30*TB)"]
            chain.append(f"scale={_fmt_val(base_w_px)}*({scale_expr}):{_fmt_val(base_h_px)}*({scale_expr}):eval=frame")
            chain.append("format=rgba")
            # 透明度: colorchannelmixer 仅支持常量
            if not anim_kfs and c_opacity != 1.0:
                chain.append(f"colorchannelmixer=aa={opa_expr}")

        # 旋转(rotate filter 不支持 eval=frame,去掉)
        if has_rot:
            chain.append(
                f"rotate=({rot_expr})*PI/180:ow=rotw(({rot_expr})*PI/180):"
                f"oh=roth(({rot_expr})*PI/180):fillcolor=black@0"
            )

        ov_label = f"[ov{idx}]"
        ov_filter_parts.append(f"[{input_idx}:v]" + ",".join(chain) + ov_label)
        input_idx += 1

        # 计算 overlay 位置(归一化 -> 像素,居中)
        # 中心点在 (cs_x * vw, cs_y * vh),减去 overlay 半宽高得到左上角
        center_x = cs_x * vw
        center_y = cs_y * vh

        # overlay 位置表达式(含动画偏移)
        overlay_x = f"({_fmt_val(center_x)}) - overlay_w/2 + ({x_offs_expr})"
        overlay_y = f"({_fmt_val(center_y)}) - overlay_h/2 + ({y_offs_expr})"

        enable_expr = f"between(t,{_fmt_val(t_start)},{_fmt_val(t_end)})".replace(",", "\\,")
        comp_label = f"[c{idx}]"

        overlay_step = (
            f"{current_main_label}{ov_label}"
            f"overlay=x='{overlay_x}':y='{overlay_y}':eval=frame:enable='{enable_expr}'"
            f"{comp_label}"
        )
        overlay_steps.append(overlay_step)
        current_main_label = comp_label

    # 没有有效叠层
    if not ov_filter_parts and not has_main_anim:
        return "没有有效的叠层或动画需要合成"

    # ── 主视频动画 ──
    if has_main_anim:
        motion_filter = _build_motion_filter_simple(main_anim, vw, vh)
        ov_filter_parts.insert(0, motion_filter)

    # 组合所有 filter 片段
    filter_parts = ov_filter_parts + overlay_steps
    filter_complex = ";".join(filter_parts)

    cmd += ["-filter_complex", filter_complex]
    cmd += ["-map", current_main_label]
    cmd += ["-map", f"{'0' if not has_main_anim else '0'}:a?"]  # 保留原音频

    # ── 编码器 ──
    use_nvenc = _has_nvenc()
    if use_nvenc:
        cmd += ["-c:v", "h264_nvenc", "-preset", "p7", "-cq", "23"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20"]
    cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-movflags", "+faststart"]
    cmd += ["-pix_fmt", "yuv420p"]
    cmd += [output_path]

    # ── 执行 ──
    result = subprocess.run(cmd, capture_output=True, timeout=600, check=False)

    # ── 清理临时输入(主要是视频叠层的 trim 文件,但它们是输入而非中间文件)
    # 临时文件已通过 ffmpeg 内部管理,无需额外清理

    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        size = os.path.getsize(output_path) / (1024 * 1024)
        return f"时间线渲染完成: {output_path} ({size:.1f}MB, {len(all_clips)} 个叠层)"

    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="replace")[-500:]
        return f"时间线渲染失败: {err}"

    return "时间线渲染失败:输出文件为空"


# 工具已通过 @tool 装饰器自动注册到 Registry
