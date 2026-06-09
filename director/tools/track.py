"""
物体跟踪工具 — 追踪画面中的特定物体
===================================
基于 OpenCV 内置 tracker(Nano/DaSiamRPN/ViT),在视频中追踪用户指定的
物体区域,支持全帧轨迹追踪和追踪裁剪.

适用场景:需要跟随画面中特定物体移动的视频.

工具函数:
  - track_object: 从指定帧选区域,全视频追踪,返回运动轨迹
  - preview_track_frame: 预览跟踪结果(base64 PNG)
  - crop_to_track: 以追踪物体为中心自动裁剪视频
"""
import json, base64, os, subprocess, re, hashlib, tempfile
from pathlib import Path

from director.registry import tool

_PROJECT_DIR = Path(__file__).parent.parent.parent

AVAILABLE_TRACKERS = ["nano", "dasiamrpn", "vit", "mil"]

# Auto-detected tracker name
_TRACKER_NAME = None


def _get_tracker_name():
    """Auto-detect the best available tracker."""
    global _TRACKER_NAME
    if _TRACKER_NAME:
        return _TRACKER_NAME

    import cv2
    # 按优先级检测
    candidates = [
        ("nano", "TrackerNano"),
        ("dasiamrpn", "TrackerDaSiamRPN"),
        ("vit", "TrackerVit"),
        ("mil", "TrackerMIL"),
        ("csrt", "TrackerCSRT"),
    ]
    for name, cv_name in candidates:
        # Check for create function or class
        if hasattr(cv2, f"{cv_name}_create") or hasattr(cv2, cv_name):
            _TRACKER_NAME = name
            return name
    # Fallback
    _TRACKER_NAME = "mil"
    return "mil"


def _create_tracker(tracker_type: str = ""):
    """创建 OpenCV 跟踪器,自动探测最佳可用 tracker"""
    import cv2

    if not tracker_type:
        tracker_type = _get_tracker_name()

    # 新版 OpenCV 4.13+ 使用 TrackerXxx_create()
    # 旧版 OpenCV 4.5 使用 legacy 命名空间
    create_funcs = {
        "nano": ["TrackerNano_create"],
        "dasiamrpn": ["TrackerDaSiamRPN_create"],
        "vit": ["TrackerVit_create"],
        "mil": ["TrackerMIL_create", "legacy_TrackerMIL"],
        "csrt": ["TrackerCSRT_create", "legacy_TrackerCSRT"],
        "kcf": ["TrackerKCF_create", "legacy_TrackerKCF"],
    }

    names = create_funcs.get(tracker_type, create_funcs.get(_get_tracker_name(), ["TrackerNano_create"]))

    for n in names:
        try:
            fn = getattr(cv2, n, None)
            if fn:
                return fn()
        except (AttributeError, TypeError, cv2.error):
            continue

    raise RuntimeError(f"No tracking API available (tried: {names})")


# ═══════════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════════

def _parse_json(data):
    """安全解析 JSON 字符串或列表"""
    if isinstance(data, (list, dict)):
        return data
    if not data:
        return None
    try:
        return json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return None


def _get_video_dimensions(video_path: str) -> tuple:
    """用 ffmpeg -i 获取视频分辨率"""
    r = subprocess.run(["ffmpeg", "-i", video_path], capture_output=True, timeout=30)
    output = (r.stdout + r.stderr).decode("utf-8", errors="replace")
    m = re.search(r",\s*(\d{3,})x(\d{3,})", output)
    if m:
        return int(m.group(1)), int(m.group(2))

    # 回退用 OpenCV
    import cv2
    cap = cv2.VideoCapture(video_path)
    if cap.isOpened():
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        if w > 0 and h > 0:
            return w, h
    return None, None


def _parse_bbox(bbox, vw: int = 0, vh: int = 0, from_normalized: bool = True) -> tuple:
    """
    解析 bbox 为像素坐标 (x, y, w, h).

    Args:
        bbox: dict/JSON/list,可以是像素坐标或归一化坐标 [0,1]
        vw, vh: 视频尺寸(归一化时需要)
        from_normalized: True=bbox 是归一化坐标 [0,1],False=像素坐标
    """
    data = _parse_json(bbox)
    if data is None:
        raise ValueError(f"无法解析 bbox: {bbox}")

    if isinstance(data, dict):
        x = float(data.get("x", 0))
        y = float(data.get("y", 0))
        w = float(data.get("w", 0.1))
        h = float(data.get("h", 0.1))
    elif isinstance(data, (list, tuple)):
        x, y, w, h = float(data[0]), float(data[1]), float(data[2]), float(data[3])
    else:
        raise ValueError(f"不支持的 bbox 格式: {type(data)}")

    if from_normalized and vw > 0 and vh > 0:
        x, y, w, h = int(x * vw), int(y * vh), int(w * vw), int(h * vh)

    # Clamp to video bounds
    if vw > 0:
        x = max(0, min(int(x), vw - 1))
        w = max(2, min(int(w), vw - x))
    if vh > 0:
        y = max(0, min(int(y), vh - 1))
        h = max(2, min(int(h), vh - y))

    return int(x), int(y), int(w), int(h)


def _cleanup_tmp(*paths):
    for p in paths:
        if not p:
            continue
        try:
            if os.path.isfile(p):
                os.remove(p)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════
#  工具 1: track_object
# ═══════════════════════════════════════════════════════════

@tool(
    name="track_object",
    description="在视频中追踪指定区域的物体,返回全视频运动轨迹和运动分析(范围,位移,是否静态).VL 模型可用此工具分析产品/人物/文字的运动模式",
    phase="analyze",
    category="track",
    tags=["track", "object", "motion"],
    group="画面与场景",
)
def track_object(
    video_path: str,
    bbox_json,
    init_time: float = 0.0,
    tracker_type: str = "",
    keyframe_interval: int = 0,
    max_frames: int = 0,
    draft_id: str = "",
    clip_id: int = 0,
) -> str:
    """
    在视频中追踪指定区域的物体.

    从 init_time 位置的帧初始化 tracker,然后逐帧追踪直到视频结束
    (或 max_frames 帧后停止).返回轨迹和运动分析.

    Args:
        video_path: 视频文件路径
        bbox_json: 初始包围框,支持两种格式:
                   - 归一化 (0~1): [x, y, w, h] 或 {"x":..., "y":..., "w":..., "h":...}
                   - 像素: 同上,通过 from_normalized=False
        init_time: 初始化追踪的起始时间(秒),默认 0.0
        tracker_type: "nano"/"dasiamrpn"/"vit"/"mil"/"csrt"/"kcf",空串=自动选择最佳
        keyframe_interval: 关键帧采样间隔(帧数),0=记录每帧,默认 0
        max_frames: 最大追踪帧数,0=不限
        draft_id: 草稿 ID
        clip_id: 素材索引

    Returns:
        JSON 格式的追踪结果
    """
    import cv2

    if not os.path.exists(video_path):
        return json.dumps({"error": f"文件不存在: {video_path}"}, ensure_ascii=False)

    # 默认 tracker
    if not tracker_type:
        tracker_type = _get_tracker_name()

    w, h = _get_video_dimensions(video_path)
    if not w or not h:
        return json.dumps({"error": "无法获取视频尺寸"}, ensure_ascii=False)

    # 解析 bbox(归一化坐标)
    try:
        bx, by, bw, bh = _parse_bbox(bbox_json, w, h, from_normalized=True)
    except Exception as e:
        return json.dumps({"error": f"bbox 解析失败: {e}"}, ensure_ascii=False)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return json.dumps({"error": "无法打开视频文件"}, ensure_ascii=False)

    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        fps = 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # 跳到初始帧
    init_frame_idx = int(init_time * fps)
    init_frame_idx = max(0, min(init_frame_idx, total_frames - 1))
    cap.set(cv2.CAP_PROP_POS_FRAMES, init_frame_idx)

    ret, init_frame = cap.read()
    if not ret:
        cap.release()
        return json.dumps({"error": f"无法读取帧 {init_frame_idx}"}, ensure_ascii=False)

    # 初始化 tracker
    try:
        tracker = _create_tracker(tracker_type)
        tracker.init(init_frame, (bx, by, bw, bh))
    except Exception as e:
        cap.release()
        return json.dumps({"error": f"tracker 初始化失败: {e}"}, ensure_ascii=False)

    # 确定有效间隔
    interval = keyframe_interval if keyframe_interval > 0 else 1
    frames_to_track = min(total_frames - init_frame_idx, max_frames) if max_frames > 0 else (total_frames - init_frame_idx)

    bbox_sequence = []
    center_keyframes = []
    prev_cx, prev_cy = bx + bw / 2, by + bh / 2

    # 记录第一帧
    t = init_frame_idx / fps
    bbox_sequence.append({
        "frame": init_frame_idx, "t": round(t, 4),
        "x": round(bx / w, 6), "y": round(by / h, 6),
        "w": round(bw / w, 6), "h": round(bh / h, 6),
    })
    center_keyframes.append({
        "t": round(t, 4),
        "x": round((bx + bw / 2) / w, 6),
        "y": round((by + bh / 2) / h, 6),
    })

    # 逐帧追踪
    frame_idx = init_frame_idx + 1
    processed = 1
    lost_count = 0

    while cap.isOpened() and processed < frames_to_track:
        ret, frame = cap.read()
        if not ret:
            break

        ok, bbox_out = tracker.update(frame)

        if ok:
            tx, ty, tw, th = [int(v) for v in bbox_out]
            prev_cx, prev_cy = tx + tw / 2, ty + th / 2
            lost_count = 0
        else:
            lost_count += 1
            tx, ty, tw, th = int(prev_cx - bw / 2), int(prev_cy - bh / 2), bw, bh

        t = frame_idx / fps
        if processed % interval == 0:
            bbox_sequence.append({
                "frame": frame_idx, "t": round(t, 4),
                "x": round(tx / w, 6), "y": round(ty / h, 6),
                "w": round(tw / w, 6), "h": round(th / h, 6),
                "ok": ok,
            })
            center_keyframes.append({
                "t": round(t, 4),
                "x": round((tx + tw / 2) / w, 6),
                "y": round((ty + th / 2) / h, 6),
            })

        processed += 1
        frame_idx += 1

    cap.release()

    if not bbox_sequence:
        return json.dumps({"error": "未获取任何追踪数据"}, ensure_ascii=False)

    # 运动分析
    centers_x = [(b["x"] + b["w"] / 2) for b in bbox_sequence if b.get("ok", True)]
    centers_y = [(b["y"] + b["h"] / 2) for b in bbox_sequence if b.get("ok", True)]

    motion_range_x = max(centers_x) - min(centers_x) if centers_x else 0
    motion_range_y = max(centers_y) - min(centers_y) if centers_y else 0
    total_disp = 0.0
    for i in range(1, len(centers_x)):
        dx = centers_x[i] - centers_x[i - 1]
        dy = centers_y[i] - centers_y[i - 1]
        total_disp += (dx ** 2 + dy ** 2) ** 0.5

    ok_frames = sum(1 for b in bbox_sequence if b.get("ok", True))
    quality = round(ok_frames / max(len(bbox_sequence), 1), 4)

    result = {
        "tracker": tracker_type,
        "fps": round(fps, 1),
        "frames_tracked": processed,
        "duration_seconds": round(processed / fps, 2),
        "keyframes": len(bbox_sequence),
        "tracking_quality": quality,
        "motion": {
            "range_x_norm": round(motion_range_x, 6),
            "range_y_norm": round(motion_range_y, 6),
            "total_displacement_norm": round(total_disp, 6),
            "is_static": motion_range_x < 0.01 and motion_range_y < 0.01,
        },
        "bbox_sequence": bbox_sequence,
        "center_keyframes": center_keyframes,
        "has_tracking_data": len(bbox_sequence) > 0,
    }

    if draft_id:
        from director.draft import _write_to_draft
        _write_to_draft(draft_id, clip_id, "track", {"method": tracker_type, "quality": quality}, label="物体追踪完成")

    return json.dumps(result, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════
#  工具 2: preview_track_frame
# ═══════════════════════════════════════════════════════════

@tool(
    name="preview_track_frame",
    description="在指定时间点预览物体跟踪结果.画框标记追踪位置,返回 base64 PNG 图片,可直接输入给 VL 模型评估跟踪效果",
    phase="analyze",
    category="track",
    tags=["track", "preview"],
    group="画面与场景",
)
def preview_track_frame(
    video_path: str,
    bbox_json,
    tracker_type: str = "",
    time_pos: float = 1.0,
) -> str:
    """
    在指定时间点预览物体跟踪结果.

    Args:
        video_path: 视频文件路径
        bbox_json: 初始包围框 [x, y, w, h] 归一化 0~1
        tracker_type: 跟踪器类型,空串=自动
        time_pos: 预览时间点(秒),默认 1.0

    Returns:
        data:image/png;base64,... 格式的图片数据
    """
    import cv2

    if not os.path.exists(video_path):
        return json.dumps({"error": f"文件不存在: {video_path}"}, ensure_ascii=False)

    if not tracker_type:
        tracker_type = _get_tracker_name()

    w, h = _get_video_dimensions(video_path)
    if not w or not h:
        return json.dumps({"error": "无法获取视频尺寸"}, ensure_ascii=False)

    try:
        bx, by, bw, bh = _parse_bbox(bbox_json, w, h, from_normalized=True)
    except Exception as e:
        return json.dumps({"error": f"bbox 解析失败: {e}"}, ensure_ascii=False)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return json.dumps({"error": "无法打开视频文件"}, ensure_ascii=False)

    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        fps = 30.0

    ret, frame = cap.read()
    if not ret:
        cap.release()
        return json.dumps({"error": "无法读取第一帧"}, ensure_ascii=False)

    try:
        tracker = _create_tracker(tracker_type)
        tracker.init(frame, (bx, by, bw, bh))
    except Exception as e:
        cap.release()
        return json.dumps({"error": f"tracker 初始化失败: {e}"}, ensure_ascii=False)

    target_frame = int(time_pos * fps)
    current_frame = 0
    tracked_bbox = (bx, by, bw, bh)
    track_ok = True

    while current_frame < target_frame:
        ret, frame = cap.read()
        if not ret:
            break
        current_frame += 1
        track_ok, tracked_bbox = tracker.update(frame)
        if not track_ok:
            break

    cap.release()

    tx, ty, tw, th = [int(v) for v in tracked_bbox]
    cv2.rectangle(frame, (tx, ty), (tx + tw, ty + th), (0, 255, 0), 3)
    cx, cy = tx + tw // 2, ty + th // 2
    cv2.circle(frame, (cx, cy), 6, (0, 255, 0), -1)
    cv2.circle(frame, (cx, cy), 10, (0, 255, 0), 2)

    label = f"Tracker: {tracker_type.upper()} | Time: {time_pos:.1f}s | {'OK' if track_ok else 'LOST'}"
    cv2.putText(frame, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

    ret, buf = cv2.imencode(".png", frame)
    if not ret:
        return json.dumps({"error": "图片编码失败"}, ensure_ascii=False)

    return f"data:image/png;base64,{base64.b64encode(buf).decode('utf-8')}"


# ═══════════════════════════════════════════════════════════
#  工具 3: crop_to_track
# ═══════════════════════════════════════════════════════════

@tool(
    name="crop_to_track",
    description="以追踪物体为中心自动裁剪视频,画面始终跟随被追踪物体.适用于需要跟随画面中特定物体的场景(如运动物体、移动主体)",
    phase="all",
    category="track",
    tags=["track", "crop", "follow"],
    group="画面与场景",
)
def crop_to_track(
    video_path: str,
    bbox_json,
    init_time: float = 0.0,
    target_ratio: str = "9:16",
    padding: float = 0.3,
    tracker_type: str = "",
    output_path: str = "",
    draft_id: str = "",
    clip_id: int = 0,
) -> str:
    """
    以追踪物体为中心自动裁剪视频.

    先追踪物体轨迹,然后根据轨迹动态裁剪,画面始终跟随物体.

    Args:
        video_path: 视频文件路径
        bbox_json: 初始包围框 [x, y, w, h] 归一化 0~1
        init_time: 初始化追踪的起始时间(秒),默认 0.0
        target_ratio: 目标宽高比,支持 9:16/16:9/1:1/4:3/3:4,默认 9:16
        padding: 物体周围边距比例 [0-1],默认 0.3
        tracker_type: 跟踪器类型,空串=自动
        output_path: 输出路径
        draft_id: 草稿 ID
        clip_id: 素材索引

    Returns:
        结果信息
    """
    import cv2

    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"

    if not tracker_type:
        tracker_type = _get_tracker_name()

    # 先追踪
    track_result_str = track_object(
        video_path=video_path,
        bbox_json=bbox_json,
        init_time=init_time,
        tracker_type=tracker_type,
        keyframe_interval=0,
    )
    track_data = json.loads(track_result_str)
    if "error" in track_data:
        return f"追踪失败: {track_data['error']}"

    bbox_seq = track_data.get("bbox_sequence", [])
    if not bbox_seq:
        return "追踪轨迹为空"

    w, h = _get_video_dimensions(video_path)
    if not w or not h:
        return "无法获取视频尺寸"

    fps = track_data.get("fps", 30)

    # 解析目标比例
    ratio_map = {"9:16": (9, 16), "16:9": (16, 9), "1:1": (1, 1), "4:3": (4, 3), "3:4": (3, 4)}
    twr, thr = ratio_map.get(target_ratio, (9, 16))
    target_aspect = twr / thr

    # 计算 crop 尺寸
    crop_h = int(h * 0.65)
    crop_w = int(crop_h * target_aspect)
    if crop_w > w:
        crop_w = w
        crop_h = int(w / target_aspect)

    # 带 padding
    pad_w = int(crop_w * (1.0 + padding * 2))
    pad_h = int(crop_h * (1.0 + padding * 2))
    pad_w = min(pad_w, w - 2)
    pad_h = min(pad_h, h - 2)

    # 为每个 bbox 生成分段裁剪
    tmp_dir = tempfile.gettempdir()
    tag = hashlib.md5(video_path.encode()).hexdigest()[:6]
    seg_files = []

    for i, b in enumerate(bbox_seq):
        # 物体中心
        cx = int((b["x"] + b["w"] / 2) * w)
        cy = int((b["y"] + b["h"] / 2) * h)

        cx_pad = cx - pad_w // 2
        cy_pad = cy - pad_h // 2
        cx_pad = max(0, min(cx_pad, w - pad_w))
        cy_pad = max(0, min(cy_pad, h - pad_h))

        start_t = b["t"]
        end_t = bbox_seq[i + 1]["t"] if i + 1 < len(bbox_seq) else track_data.get("duration_seconds", start_t + 1)
        dur = max(0.1, end_t - start_t)

        seg_path = os.path.join(tmp_dir, f"track_seg_{tag}_{i}.mp4")
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_t),
            "-i", video_path,
            "-t", str(dur),
            "-vf", f"crop={pad_w}:{pad_h}:{cx_pad}:{cy_pad}",
            "-c:v", "libx264", "-crf", "20",
            "-an", seg_path,
        ]
        subprocess.run(cmd, capture_output=True, timeout=120)
        if os.path.exists(seg_path) and os.path.getsize(seg_path) > 0:
            seg_files.append(seg_path)

    if not seg_files:
        _cleanup_tmp(*seg_files)
        return "分段裁剪失败"

    # concat
    concat_list = os.path.join(tmp_dir, f"track_concat_{tag}.txt")
    with open(concat_list, "w", encoding="utf-8") as f:
        for sf in seg_files:
            f.write(f"file '{sf.replace(chr(92), chr(92)+chr(92))}'\n")

    if not output_path:
        hash_out = hashlib.md5(video_path.encode()).hexdigest()[:8]
        output_path = os.path.join(_PROJECT_DIR, "output", f"crop_to_track_{hash_out}.mp4")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # 试带音频
    temp_out = output_path
    cmd_concat = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", concat_list,
        "-i", video_path,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k",
        "-map", "0:v", "-map", "1:a",
        "-shortest", "-movflags", "+faststart",
        temp_out,
    ]
    r = subprocess.run(cmd_concat, capture_output=True, timeout=600)

    if r.returncode != 0 or not os.path.exists(temp_out) or os.path.getsize(temp_out) == 0:
        # 回退:仅视频无音频
        cmd_v = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", concat_list,
            "-c:v", "libx264", "-crf", "20",
            "-an", "-movflags", "+faststart",
            temp_out,
        ]
        subprocess.run(cmd_v, capture_output=True, timeout=300)

    _cleanup_tmp(concat_list, *seg_files)

    if os.path.exists(temp_out) and os.path.getsize(temp_out) > 0:
        size_mb = os.path.getsize(temp_out) / (1024 * 1024)
        quality = track_data.get("tracking_quality", 0)
        motion = track_data.get("motion", {})
        if draft_id:
            from director.draft import _write_to_draft
            _write_to_draft(draft_id, clip_id, "crop", {"mode": "track", "quality": quality}, label="追踪裁剪完成")
        return (
            f"[OK] 追踪裁剪完成({target_ratio}, {tracker_type}, 追踪质量 {quality:.0%}):\n"
            f"  {temp_out} ({size_mb:.1f}MB)\n"
            f"  运动范围: {motion.get('range_x_norm', 0):.1%} x {motion.get('range_y_norm', 0):.1%}"
        )
    return "[FAIL] 追踪裁剪失败"
