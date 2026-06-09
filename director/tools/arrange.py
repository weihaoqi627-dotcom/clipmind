"""
排版工具 — 镜头编排阶段
========================
AI 通过反复调用这些工具来编排镜头顺序,预览并修改.
"""
import json, os, subprocess, copy
from pathlib import Path

from director.registry import tool

_PROJECT_DIR = Path(__file__).parent.parent.parent

# ─── 状态 ──────────────────────────────────────────────────
# arrangement 格式: [{"id": 0, "source_clip_id": 0, "start": 0, "end": 30, ...}, ...]

_arrangements: dict = {}  # session_key -> list[dict]
_arrangement_versions: dict = {}  # video_path -> current_version (int)

def _get_key(video_path: str, ver: int = 0) -> str:
    return f"{video_path}_v{ver}"

def _next_version(video_path: str) -> int:
    """获取下一个可用版本号并递增计数器"""
    cur = _arrangement_versions.get(video_path, 0) + 1
    _arrangement_versions[video_path] = cur
    return cur

def _get_latest_key(video_path: str) -> str:
    """获取指定视频的最新版本 key"""
    ver = _arrangement_versions.get(video_path, 0)
    if ver == 0:
        return _get_key(video_path, 1)  # 回退到 v1
    return _get_key(video_path, ver)


@tool(
    name="new_arrangement",
    description="基于保留的片段创建新排版",
    phase="plan",
    category="arrange",
    tags=["arrange", "timeline", "sequence"],
    group="时间线与编排",
)
def new_arrangement(video_path: str, keep_clips_json: str) -> str:
    """
    基于保留的片段创建新排版.

    Args:
        video_path: 视频路径
        keep_clips_json: 可以是:
            - [{"id": 0, "start": 0, "end": 30}, ...] 完整对象
            - [0, 1, 2] 简单ID列表(自动从缓存查找)
            从 show_current_clips 获取
    """
    clips = json.loads(keep_clips_json) if isinstance(keep_clips_json, str) else keep_clips_json
    if not clips:
        return "无有效片段"

    # 如果是简单ID列表 [0, 1, 2],从缓存自动查找
    if isinstance(clips[0], (int, float)):
        from director.tools.analyze import _get_segments_cached
        segments = _get_segments_cached(video_path)
        id_map = {s["id"]: s for s in segments}
        resolved = []
        for cid in clips:
            seg = id_map.get(int(cid))
            if seg:
                resolved.append(seg)
        clips = resolved
        if not clips:
            return "无法从缓存找到这些片段ID"

    arr = []
    for clip in clips:
        dur = clip.get("end", 30) - clip.get("start", 0)
        arr.append({
            "id": len(arr),
            "source_clip_id": clip.get("id", 0),
            "start": clip.get("start", 0),
            "end": clip.get("end", 30),
            "duration": round(dur, 1),
            "_original_duration": round(dur, 1),
        })
    ver = _next_version(video_path)
    key = _get_key(video_path, ver)
    _arrangements[key] = arr
    return f"已创建排版 (版本 {ver}): {len(arr)} 个镜头\n" + _format_arrangement(arr)


@tool(
    name="move_shot",
    description="移动一个镜头的位置(上移或下移),重新排序素材",
    phase="plan",
    category="arrange",
    tags=["arrange", "reorder", "shot"],
    group=["细剪与节奏", "时间线与编排"],
)
def move_shot(video_path: str, shot_id: int, direction: str) -> str:
    """
    移动一个镜头的位置(上移/下移).

    Args:
        video_path: 视频路径
        shot_id: 镜头ID
        direction: "up" 或 "down"
    """
    key = _get_latest_key(video_path)
    arr = _arrangements.get(key, [])
    if not arr:
        return "(暂无排版数据,先调用 new_arrangement)"
    idx = next((i for i, s in enumerate(arr) if s["id"] == shot_id), -1)
    if idx < 0:
        return f"无效 shot_id: {shot_id}"
    if direction == "up" and idx > 0:
        arr[idx], arr[idx-1] = arr[idx-1], arr[idx]
    elif direction == "down" and idx < len(arr) - 1:
        arr[idx], arr[idx+1] = arr[idx+1], arr[idx]
    else:
        return f"无法移动(已在{'最' + direction}边)"
    # 重新编号
    for i, s in enumerate(arr):
        s["id"] = i
    return f"✅ 镜头 {shot_id} 已移{'上' if direction == 'up' else '下'}\n" + _format_arrangement(arr)


@tool(
    name="preview_arrangement",
    description="预览当前排序的视觉效果,返回一帧预览图供评估",
    phase="plan",
    category="arrange",
    tags=["arrange", "preview", "sequence"],
    group="时间线与编排",
)
def preview_arrangement(video_path: str) -> str:
    """预览当前排版的视觉效果(渲染低分辨率预览)."""
    key = _get_latest_key(video_path)
    arr = _arrangements.get(key, [])
    if not arr:
        return "(暂无排版数据)"
    # 快速拼接预览
    tmp_dir = os.path.join(_PROJECT_DIR, "_tmp_preview")
    os.makedirs(tmp_dir, exist_ok=True)
    concat_file = os.path.join(tmp_dir, "concat.txt")
    segments = []
    for shot in arr:
        seg_path = os.path.join(tmp_dir, f"seg_{shot['id']}.mp4")
        cmd = [
            "ffmpeg", "-y", "-ss", str(shot["start"]), "-i", video_path,
            "-t", str(shot["duration"]),
            "-vf", "scale=-2:720,fps=24",
            "-c:v", "libx264", "-crf", "28", seg_path,
        ]
        subprocess.run(cmd, capture_output=True, timeout=120, check=False)
        if os.path.exists(seg_path):
            segments.append(f"file '{seg_path}'")
    if not segments:
        return "预览生成失败"
    with open(concat_file, "w", encoding="utf-8") as f:
        f.write("\n".join(segments))
    out_path = os.path.join(tmp_dir, "preview.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", concat_file, "-c", "copy", out_path,
    ], capture_output=True, timeout=120, check=False)
    if os.path.exists(out_path):
        size = os.path.getsize(out_path) / (1024 * 1024)
        return f"预览已生成 ({size:.1f}MB)\n路径: {out_path}"
    return "预览生成失败"


@tool(
    name="show_arrangement",
    description="查看当前镜头的排版顺序和状态列表",
    phase="plan",
    category="arrange",
    tags=["arrange", "view", "sequence"],
    group="时间线与编排",
)
def show_arrangement(video_path: str, version: int = 0) -> str:
    """查看当前排版.version=0 使用最新版本."""
    if version == 0:
        key = _get_latest_key(video_path)
    else:
        key = _get_key(video_path, version)
    arr = _arrangements.get(key, [])
    if not arr:
        return "(暂无排版数据)"
    return _format_arrangement(arr)


def _format_arrangement(arr: list) -> str:
    """格式化排版为可读字符串"""
    lines = [f"排版 (共 {len(arr)} 个镜头, 总时长 {sum(s['duration'] for s in arr):.0f}s):"]
    for s in arr:
        extras = []
        if s.get("speed_ramp"):
            extras.append(f"变速:{s['speed_ramp']}")
        if s.get("layout"):
            extras.append(f"布局:{s['layout']}")
        info = f"  [{s['id']}] 源片段 {s['source_clip_id']} | "
        info += f"{s['start']:.0f}s-{s['end']:.0f}s | 时长 {s['duration']:.0f}s"
        if extras:
            info += " | " + ", ".join(extras)
        lines.append(info)
    return "\n".join(lines)


def _parse_json(data) -> list:
    """将JSON字符串解析为列表"""
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, str):
        try:
            return json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return []
    return []


@tool(
    name="apply_speed_ramp",
    description="给某个镜头应用曲线变速(0.1~5倍速),支持恒定速度或时间关键帧曲线",
    phase="edit",
    category="arrange",
    tags=["speed", "ramp", "time_remap"],
    group=["细剪与节奏", "调速与布局"],
)
def apply_speed_ramp(video_path: str, shot_id: int, speed_curve_json: str) -> str:
    """
    给某个镜头应用曲线变速.

    Args:
        video_path: 视频路径
        shot_id: 镜头ID(从排版中获取)
        speed_curve_json: 速度曲线JSON.
            恒定速度: 直接传速度值,如 "2.0" 表示2倍速
            变速曲线: [{"t": 0, "speed": 1.0}, {"t": 0.3, "speed": 2.0}, {"t": 0.7, "speed": 0.5}, {"t": 1, "speed": 1.0}]
            t的范围0~1表示镜头内的相对时间位置,speed是速度倍数(0.1~5.0)
    """
    key = _get_key(video_path, 1)
    arr = _arrangements.get(key, [])
    shot = next((s for s in arr if s["id"] == shot_id), None)
    if not shot:
        return f"无效 shot_id: {shot_id}(当前镜头ID: {[s['id'] for s in arr]})"

    try:
        curve = json.loads(speed_curve_json) if isinstance(speed_curve_json, str) else speed_curve_json
    except json.JSONDecodeError:
        return "speed_curve_json 格式错误"

    # 验证曲线
    if isinstance(curve, (int, float)):
        # 恒定速度
        speed = float(curve)
        if speed < 0.1 or speed > 5.0:
            return "速度必须在 0.1~5.0 之间"
        shot["speed_ramp"] = {"type": "constant", "speed": speed}
        shot["duration"] = round(shot.get("_original_duration", shot["duration"]) / speed, 1)
        return f"✅ 镜头 {shot_id} 已应用 {speed}x 恒定速度\n" + _format_arrangement(arr)

    if isinstance(curve, list) and all(isinstance(k, dict) for k in curve):
        # 验证每个关键帧
        for kf in curve:
            t, s = kf.get("t", -1), kf.get("speed", 0)
            if not (0 <= t <= 1):
                return "变速关键帧的 t 必须在 0~1 之间"
            if not (0.1 <= s <= 5.0):
                return "变速关键帧的 speed 必须在 0.1~5.0 之间"
        # 保存原始时长
        if "_original_duration" not in shot:
            shot["_original_duration"] = shot["duration"]
        # 计算变速后的平均时长
        avg_speed = sum(k["speed"] for k in curve) / len(curve)
        shot["speed_ramp"] = {"type": "curve", "keyframes": curve}
        shot["duration"] = round(shot["_original_duration"] / avg_speed, 1)
        return f"✅ 镜头 {shot_id} 已应用曲线变速 ({len(curve)} 个关键帧)\n" + _format_arrangement(arr)

    return "speed_curve_json 格式错误:应传数字(恒定速度)或关键帧数组"


@tool(
    name="remove_speed_ramp",
    description="移除某个镜头的曲线变速,恢复原始速度",
    phase="edit",
    category="arrange",
    tags=["speed", "ramp", "reset"],
    group=["细剪与节奏", "调速与布局"],
)
def remove_speed_ramp(video_path: str, shot_id: int) -> str:
    """
    移除某个镜头的曲线变速,恢复原始速度.

    Args:
        video_path: 视频路径
        shot_id: 镜头ID
    """
    key = _get_key(video_path, 1)
    arr = _arrangements.get(key, [])
    shot = next((s for s in arr if s["id"] == shot_id), None)
    if not shot:
        return f"无效 shot_id: {shot_id}"
    if "speed_ramp" not in shot:
        return f"镜头 {shot_id} 没有应用变速"
    del shot["speed_ramp"]
    if "_original_duration" in shot:
        shot["duration"] = shot["_original_duration"]
        del shot["_original_duration"]
    return f"✅ 镜头 {shot_id} 已恢复原始速度\n" + _format_arrangement(arr)


@tool(
    name="apply_layout",
    description="对多个镜头应用分屏/画中画布局(side_by_side左右分屏,pip画中画,grid_2x2网格,grid_3x1上下三分,grid_1x3左右三分,stack上下分屏)",
    phase="edit",
    category="arrange",
    tags=["layout", "split_screen", "pip"],
    group="调速与布局",
)
def apply_layout(video_path: str, shot_ids_json: str, layout_type: str,
                 pip_position: str = "br", pip_size_ratio: float = 0.25) -> str:
    """
    对多个镜头应用分屏/画中画布局.

    Args:
        video_path: 视频路径
        shot_ids_json: 要组合的镜头ID列表,如 "[0, 1, 2, 3]"
        layout_type: 布局类型
            - "side_by_side": 左右分屏(2个镜头)
            - "pip": 画中画(第1个主画面,后面的画中画)
            - "grid_2x2": 2×2网格(4个镜头)
            - "grid_3x1": 上中下三分(3个镜头)
            - "grid_1x3": 左中右三分(3个镜头)
            - "stack": 上下分屏(2个镜头)
        pip_position: 画中画位置(仅pip有效),tl=左上 tr=右上 bl=左下 br=右下
        pip_size_ratio: 画中画大小比例(仅pip有效),0.1~0.5,默认0.25
    """
    key = _get_key(video_path, 1)
    arr = _arrangements.get(key, [])

    shot_ids = json.loads(shot_ids_json) if isinstance(shot_ids_json, str) else shot_ids_json
    if not isinstance(shot_ids, list) or len(shot_ids) < 2:
        return "至少需要2个镜头才能布局"

    # 验证所有shot_id存在
    shots = [s for s in arr if s["id"] in shot_ids]
    if len(shots) != len(shot_ids):
        missing = set(shot_ids) - {s["id"] for s in shots}
        return f"未找到镜头: {missing}"

    # 验证布局类型
    valid_layouts = {
        "side_by_side": 2,
        "pip": 0,  # 任意数量
        "grid_2x2": 4,
        "grid_3x1": 3,
        "grid_1x3": 3,
        "stack": 2,
    }
    if layout_type not in valid_layouts:
        return f"无效布局类型: {layout_type}(可选: {list(valid_layouts.keys())})"

    required_count = valid_layouts[layout_type]
    if required_count > 0 and len(shot_ids) != required_count:
        return f"布局 {layout_type} 需要恰好 {required_count} 个镜头,传了 {len(shot_ids)} 个"

    # 验证pip参数
    if layout_type == "pip":
        if pip_position not in ("tl", "tr", "bl", "br"):
            return "pip_position 必须是 tl/tr/bl/br"
        if not (0.1 <= pip_size_ratio <= 0.5):
            return "pip_size_ratio 必须在 0.1~0.5 之间"

    # 给每个shot打上layout标签
    # 主镜头取最短时长,画中画镜头取主镜头等长(从尾部截)
    min_dur = min(s["duration"] for s in shots)
    for i, sid in enumerate(shot_ids):
        shot = next(s for s in shots if s["id"] == sid)
        shot["layout"] = {
            "type": layout_type,
            "index": i,
            "total": len(shot_ids),
        }
        if layout_type == "pip":
            shot["layout"]["pip_position"] = pip_position if i > 0 else "full"
            shot["layout"]["pip_size_ratio"] = pip_size_ratio if i > 0 else 1.0
        # 统一时长
        if shot["duration"] > min_dur:
            shot["duration"] = min_dur

    descs = {
        "side_by_side": "左右分屏",
        "pip": "画中画",
        "grid_2x2": "2×2网格",
        "grid_3x1": "上下三分",
        "grid_1x3": "左右三分",
        "stack": "上下分屏",
    }
    return f"✅ 已应用{descs.get(layout_type, layout_type)}布局({len(shot_ids)}个镜头)\n" + _format_arrangement(arr)


@tool(
    name="remove_layout",
    description="移除镜头的分屏/画中画布局",
    phase="edit",
    category="arrange",
    tags=["layout", "remove", "reset"],
    group="调速与布局",
)
def remove_layout(video_path: str, shot_ids_json: str) -> str:
    """
    移除镜头的分屏/画中画布局,恢复单个画面.

    Args:
        video_path: 视频路径
        shot_ids_json: 镜头ID列表,如 "[0, 1]"
    """
    key = _get_key(video_path, 1)
    arr = _arrangements.get(key, [])
    shot_ids = json.loads(shot_ids_json) if isinstance(shot_ids_json, str) else shot_ids_json
    if not isinstance(shot_ids, list):
        return "shot_ids_json 应为数组"

    removed = 0
    for s in arr:
        if s["id"] in shot_ids and "layout" in s:
            del s["layout"]
            removed += 1

    if removed == 0:
        return "所选镜头没有布局"
    return f"✅ 已移除 {removed} 个镜头的布局\n" + _format_arrangement(arr)


@tool(
    name="apply_arrangement",
    description="应用排版 — 将排版结果写入草稿",
    phase="all",
    category="arrange",
    tags=["arrange", "apply", "draft"],
    group="时间线与编排",
)
def apply_arrangement(arrangement_json: str, transitions_json: str = "[]", video_path: str = "", draft_id: str = "") -> str:
    """
    应用排版 — 将排版结果写入草稿.

    Args:
        arrangement_json: 排版数据JSON
        transitions_json: 转场数据JSON(可选)
        video_path: 视频路径(可选)
        draft_id: 草稿ID(可选),提供后将排版结果写入草稿
    """
    arr = _parse_json(arrangement_json) if isinstance(arrangement_json, str) else arrangement_json
    if not arr:
        return "排版数据为空"

    ver = _next_version(video_path)
    key = _get_key(video_path, ver)
    _arrangements[key] = arr
    result = f"已应用排版 (版本 {ver}): {len(arr)} 个镜头\n" + _format_arrangement(arr)

    if draft_id:
        from director.draft import Draft
        try:
            d = Draft(draft_id)
            if d.load():
                segs = _parse_json(arrangement_json) or []
                draft_segments = []
                for i, s in enumerate(segs):
                    draft_segments.append({
                        "id": s.get("id", i),
                        "source_path": s.get("source_path", video_path),
                        "start": s.get("start", 0),
                        "end": s.get("end", 30),
                        "duration": round(s.get("end", 30) - s.get("start", 0), 1),
                        "speed": 1.0,
                        "filters": {"crop": None, "chromakey": None, "color_grading": None, "color_preset": None, "denoise": None, "stabilize": None, "animation": None},
                        "text": s.get("text", ""),
                        "status": s.get("status", "keep"),
                    })
                d.set_segments(draft_segments)
                if transitions_json:
                    trans = _parse_json(transitions_json) or []
                    if trans:
                        d.set_transitions(trans)
                d.save("排版完成")
        except Exception:
            pass

    return result


# 工具已通过 @tool 装饰器自动注册到 Registry
