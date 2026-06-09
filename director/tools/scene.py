"""
场景检测工具 — 镜头切分
========================
使用 ffmpeg scene filter 实现镜头切点检测和视频分割.
零额外依赖,全部基于 ffmpeg/ffprobe.

工具函数:
  - detect_scenes: 检测视频的所有镜头切换点,返回 JSON
  - split_at_scenes: 检测场景切点并分割为独立片段
"""
import json, os, subprocess, re, hashlib
from pathlib import Path
from director.registry import tool

_PROJECT_DIR = Path(__file__).parent.parent.parent


# ═══════════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════════

def _is_valid_mp4(file_path: str) -> bool:
    """快速检查 MP4 文件是否有效（含 moov atom）"""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-i", file_path],
            capture_output=True, timeout=30
        )
        return r.returncode == 0
    except Exception:
        return False


def _parse_json(data: str):
    """尝试解析 JSON 字符串,失败返回 None"""
    try:
        return json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return None


def _get_video_info(video_path: str) -> dict:
    """
    用 ffmpeg -i 获取视频时长和帧率(兼容没有真 ffprobe 的环境).
    时序(改修2026-05-25):ffprobe.exe 可能是 ffmpeg 副本,改用 ffmpeg -i 解析.

    Returns:
        {"duration": float, "fps": float, "width": int, "height": int}
        失败时返回空 dict
    """
    info = {}
    if not os.path.exists(video_path):
        return info

    try:
        r = subprocess.run(
            ["ffmpeg", "-i", video_path],
            capture_output=True, timeout=30, check=False,
        )
        output = (r.stdout + r.stderr).decode("utf-8", errors="replace")

        # 时长: "Duration: 00:01:21.39"
        dur_m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", output)
        if dur_m:
            h, m, s = int(dur_m.group(1)), int(dur_m.group(2)), float(dur_m.group(3))
            info["duration"] = h * 3600 + m * 60 + s

        # 从视频流解析分辨率,帧率
        for line in output.split("\n"):
            if "Stream #" in line and "Video:" in line:
                # 分辨率: "1920x3414"
                res_m = re.search(r",\s*(\d{3,})x(\d{3,})", line)
                if res_m:
                    info["width"] = int(res_m.group(1))
                    info["height"] = int(res_m.group(2))
                # 帧率: "30 fps" 或 "30000/1001 fps"
                fps_m = re.search(r"(\d+(?:\.\d+)?)\s*(?:fps|tbr)", line)
                if fps_m:
                    info["fps"] = float(fps_m.group(1))
                # 宽高比 SAR/DAR 已包含在分辨率不算
                break

    except Exception:
        pass

    return info


def _cleanup_tmp(*paths):
    """清理临时文件"""
    for p in paths:
        if not p:
            continue
        try:
            if os.path.isfile(p):
                os.remove(p)
            elif os.path.isdir(p):
                import shutil
                shutil.rmtree(p, ignore_errors=True)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════
#  主工具函数
# ═══════════════════════════════════════════════════════════

@tool(
    name="detect_scenes",
    description=(
        "检测视频的所有镜头切换点."
        "用 ffmpeg scene filter 分析画面变化返回场景列表."
        "每个场景包含起止时间,时长和帧编号."
        "不分割视频,只返回检测结果.需要分割请用 split_at_scenes."
        "不适用于静态画面视频(可能把画面闪烁误报为切点)."
    ),
    phase="analyze",
    category="scene",
    tags=["scene", "detection"],
    group="画面与场景",
)
def detect_scenes(
    video_path: str,
    threshold: float = 0.3,
    min_scene_duration: float = 1.0,
    seek_start: float = 0.0,
    duration: float = 0.0,
) -> str:
    """
    检测视频的所有镜头切换点.

    Args:
        video_path: 视频文件路径
        threshold: 场景变化敏感度 0~1
            - 0.3: 普通(默认),适合大多数视频
            - 0.2: 敏感,检测更多切点
            - 0.4: 保守,只检测明显切点
        min_scene_duration: 最小镜头时长(秒),低于此的切点被合并,默认 1.0
        seek_start: 跳转到指定时间开始检测(秒),0=从头开始
        duration: 只检测指定时长(秒),0=检测到结尾

    Returns:
        JSON 字符串:
        {
            "scenes": [...],
            "total_scenes": N,
            "threshold": 0.3,
            "total_duration": 60.0,
            "fps": 30.0
        }
    """
    # ── 输入验证 ──
    if not os.path.exists(video_path):
        return json.dumps({
            "error": f"文件不存在: {video_path}",
            "scenes": [],
            "total_scenes": 0,
        }, ensure_ascii=False)

    # 阈值范围限制
    threshold = max(0.01, min(1.0, threshold))
    min_scene_duration = max(0.1, min_scene_duration)

    # ── 获取视频信息 ──
    info = _get_video_info(video_path)
    total_duration = info.get("duration", 0.0)
    fps = info.get("fps", 30.0)
    if total_duration <= 0:
        return json.dumps({
            "error": f"无法获取视频时长: {video_path}",
            "scenes": [],
            "total_scenes": 0,
        }, ensure_ascii=False)

    # 窗口模式(duration>0): total_duration 改为窗口时长
    if duration > 0:
        total_duration = duration

    # ── 运行 ffmpeg scene 检测 ──
    # select='gt(scene,threshold)' 滤镜检测画面变化
    # showinfo 滤镜输出每个选中帧的 pts_time
    # -vsync vfr 变帧率模式,只保留选中帧
    # seek_start/duration 支持窗口检测(只解码指定区间,不解全集)
    cmd = ["ffmpeg"]
    if seek_start > 0:
        cmd += ["-ss", str(seek_start)]
    cmd += ["-i", video_path]
    if duration > 0:
        cmd += ["-t", str(duration)]
    cmd += [
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-vsync", "vfr",
        "-f", "null",
        "-",
    ]

    try:
        r = subprocess.run(cmd, capture_output=True, timeout=3600, check=False)
    except Exception as e:
        return json.dumps({
            "error": f"ffmpeg 执行失败: {e}",
            "scenes": [],
            "total_scenes": 0,
        }, ensure_ascii=False)

    output = (r.stdout + r.stderr).decode("utf-8", errors="replace")

    # ── 从 stderr 提取切点时间 ──
    # showinfo 输出格式: pts_time:XX.X
    # 注意: -ss seek_start 放在 -i 之前时,PTS 从 0 重新计时
    # 所以 pts_time 是相对 seek_start 的偏移,需要加上 seek_start
    cut_times = []
    for m in re.finditer(r"pts_time:(\d+\.?\d*)", output):
        try:
            t = float(m.group(1))
            if seek_start > 0:
                t += seek_start
            if t > seek_start:  # 排除起始点 0(+seek_start)
                cut_times.append(t)
        except ValueError:
            continue

    # ── 构建场景列表 ──
    # 切点时间=新场景的开始,上一个场景到此结束
    # 合并过短片段
    scenes_raw = []
    prev_time = 0.0

    if not cut_times:
        # 没有检测到切点,整个视频作为一个场景
        scenes_raw.append({"start": 0.0, "end": total_duration})

    for ct in cut_times:
        if ct <= prev_time:
            continue
        scenes_raw.append({"start": prev_time, "end": ct})
        prev_time = ct

    # 最后一个片段
    if prev_time < total_duration:
        scenes_raw.append({"start": prev_time, "end": total_duration})

    # ── 合并过短片段 ──
    if min_scene_duration > 0 and len(scenes_raw) > 1:
        scenes_merged = []
        i = 0
        while i < len(scenes_raw):
            seg = scenes_raw[i]
            dur = seg["end"] - seg["start"]

            if dur < min_scene_duration and i + 1 < len(scenes_raw):
                # 合并到下一个片段
                next_seg = scenes_raw[i + 1]
                scenes_raw[i + 1]["start"] = seg["start"]
            else:
                scenes_merged.append(seg)
            i += 1

        # 重新检查合并后是否还有过短的(合并后第一个或最后一个可能仍过短)
        # 但不要太激进,至少保留 1 个场景
        if scenes_merged:
            scenes_raw = scenes_merged

    # ── 有时第一个场景太短(阈值敏感导致一开始就切),合并到后续 ──
    if len(scenes_raw) >= 2:
        first_dur = scenes_raw[0]["end"] - scenes_raw[0]["start"]
        if first_dur < min_scene_duration:
            scenes_raw[1]["start"] = scenes_raw[0]["start"]
            scenes_raw.pop(0)

    # ── 有时最后一个场景太短,合并到前一个 ──
    if len(scenes_raw) >= 2:
        last_dur = scenes_raw[-1]["end"] - scenes_raw[-1]["start"]
        if last_dur < min_scene_duration:
            scenes_raw[-2]["end"] = scenes_raw[-1]["end"]
            scenes_raw.pop(-1)

    # ── 构建最终输出 ──
    scenes_out = []
    for i, seg in enumerate(scenes_raw):
        start = seg["start"]
        end = seg["end"]
        duration = end - start
        start_frame = round(start * fps)
        end_frame = round(end * fps)
        scenes_out.append({
            "index": i,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(duration, 3),
            "start_frame": start_frame,
            "end_frame": end_frame,
        })

    result = {
        "scenes": scenes_out,
        "total_scenes": len(scenes_out),
        "threshold": threshold,
        "total_duration": round(total_duration, 3),
        "fps": fps,
    }

    return json.dumps(result, ensure_ascii=False, indent=2)


@tool(
    name="split_at_scenes",
    description=(
        "检测场景切点并分割为独立文件."
        "先用 scene filter 检测切点,再用 ffmpeg -c copy 无重编码快速分割."
        "输出到指定目录或自动创建 output/scenes_{hash}/ 目录."
        "同时生成 segments.json 文件(可直接用于 arrangement)."
        "不检测画面内容(人/物/文字等),只按画面切换分割."
        "分割后会在素材目录下创建文件,注意清理."
    ),
    phase="analyze",
    category="scene",
    tags=["scene", "split", "segmentation"],
    group="画面与场景",
)
def split_at_scenes(
    video_path: str,
    threshold: float = 0.3,
    min_scene_duration: float = 1.0,
    output_dir: str = "",
) -> str:
    """
    检测场景切点并分割为独立片段.

    每个片段用 ffmpeg -ss -t -c copy 快速分割(无重编码).
    输出到 output_dir 或自动创建 output/scenes_{hash}/.
    同时写入 segments JSON 文件到输出目录(可直接用于 new_arrangement).

    Args:
        video_path: 视频文件路径
        threshold: 场景变化敏感度 0~1
        min_scene_duration: 最小镜头时长(秒)
        output_dir: 输出目录(可选,不指定则自动创建)

    Returns:
        JSON 字符串:
        {
            "segments": [
                {"id": 0, "start": 0.0, "end": 5.2, "duration": 5.2,
                 "path": "output/scenes_xxx/scene_000.mp4"},
                ...
            ],
            "total": 5,
            "output_dir": "output/scenes_xxx/"
        }
    """
    # ── 输入验证 ──
    if not os.path.exists(video_path):
        return json.dumps({
            "error": f"文件不存在: {video_path}",
            "segments": [],
            "total": 0,
        }, ensure_ascii=False)

    threshold = max(0.01, min(1.0, threshold))
    min_scene_duration = max(0.1, min_scene_duration)

    # ── 先检测场景 ──
    scenes_json = detect_scenes(video_path, threshold, min_scene_duration)
    scenes_data = _parse_json(scenes_json)

    if not scenes_data or "error" in scenes_data:
        return json.dumps({
            "error": scenes_data.get("error", "场景检测失败") if scenes_data else "场景检测失败",
            "segments": [],
            "total": 0,
        }, ensure_ascii=False)

    scenes = scenes_data.get("scenes", [])
    if not scenes:
        return json.dumps({
            "error": "未检测到任何场景",
            "segments": [],
            "total": 0,
        }, ensure_ascii=False)

    # ── 确定输出目录 ──
    if not output_dir:
        # 根据视频路径生成 hash
        video_hash = hashlib.md5(video_path.encode("utf-8")).hexdigest()[:8]
        output_dir = str(_PROJECT_DIR / "output" / f"scenes_{video_hash}")

    os.makedirs(output_dir, exist_ok=True)

    # ── 分割片段 ──
    segments = []
    video_ext = os.path.splitext(video_path)[1] or ".mp4"

    for scene in scenes:
        idx = scene["index"]
        start = scene["start"]
        end = scene["end"]
        duration = scene["duration"]

        out_name = f"scene_{idx:03d}{video_ext}"
        out_path = os.path.join(output_dir, out_name)

        # ffmpeg -ss -to -c copy 快速分割
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{start:.3f}",
            "-i", video_path,
            "-to", f"{duration:.3f}",
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            out_path,
        ]

        try:
            subprocess.run(cmd, capture_output=True, timeout=3600, check=False)
        except Exception as e:
            # 记录失败但继续
            segments.append({
                "id": idx,
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(duration, 3),
                "path": out_path,
                "error": str(e),
            })
            continue

        segments.append({
            "id": idx,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(duration, 3),
            "path": out_path,
        })

    # ── 写入 segments JSON ──
    result = {
        "segments": segments,
        "total": len(segments),
        "output_dir": output_dir + "/",
        "source": video_path,
        "threshold": threshold,
    }

    json_path = os.path.join(output_dir, "segments.json")
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=True, indent=2)
    except Exception as e:
        result["json_write_error"] = str(e)

    return json.dumps(result, ensure_ascii=False, indent=2)



@tool(
    name="split_by_scenes",
    description=(
        "将视频按固定间隔切分为原子片段(元数据切分,不写文件)."
        "≤3分钟的视频不切;>3分钟的按10分钟间隔切分."
        "不做场景检测,只计算切分点并注册到管线状态."
        "compress_segments 从原始素材提取+压缩一步到位."
    ),
    phase="analyze",
    category="scene",
    tags=["scene", "split", "preset"],
    group="画面与场景",
)
def split_by_scenes(
    video_paths_json: str = "",
    min_before_split: float = 180.0,
    split_window_start: float = 600.0,
    split_window_end: float = 300.0,
    threshold: float = 0.3,
) -> str:
    """
    将视频按固定间隔切分为原子片段(元数据切分,不写文件).

    只计算切分点并注册到管线状态,不执行ffmpeg.
    compress_segments 负责从原始素材提取+压缩.

    切分策略:
    - ≤3分钟: 不切,整段作为1个片段
    - >3分钟: 按 MAX_CHUNK(默认5分钟)固定间隔切分

    Args:
        video_paths_json: 视频路径列表(JSON 数组字符串,为空则自动从管线状态读取)
        min_before_split: 最短触发切分的时长(秒),默认 180(3分钟)
        split_window_start: 未使用(保留参数兼容)
        split_window_end: 未使用(保留参数兼容)
        threshold: 未使用(保留参数兼容)

    Returns:
        JSON: {segments: [...], total: N}
    """
    import json as _json
    from director.pipeline_state import PipelineState
    from director.tools.cut import _find_draft_dir

    work_dir = _find_draft_dir()
    state = PipelineState(work_dir)

    # 解析视频路径
    video_paths = []
    if video_paths_json:
        try:
            video_paths = _json.loads(video_paths_json)
        except _json.JSONDecodeError:
            return _json.dumps({"error": f"video_paths_json 格式错误: {video_paths_json[:100]}", "segments": [], "total": 0})
    else:
        video_paths = getattr(state, 'video_paths', []) or []

    if not video_paths:
        return _json.dumps({"error": "无视频路径", "segments": [], "total": 0})

    # 固定间隔
    MAX_CHUNK = 300  # 每段最长5分钟
    TAIL_MERGE = 150  # 最后一段不超过此秒数则合并到前一段(2.5分钟)

    def _dur(path: str) -> float:
        if not path or not os.path.exists(path):
            return 0.0
        try:
            r = subprocess.run(["ffmpeg", "-i", path], capture_output=True, timeout=30)
            m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", (r.stdout + r.stderr).decode("utf-8", errors="replace"))
            if m:
                h, m, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
                return h * 3600 + m * 60 + s
        except:
            pass
        return 0.0

    all_segments = []

    # 幂等检查:如果 video_paths 中的文件已有切分片段,直接返回已有的
    existing = state.segments
    if existing and video_paths:
        existing_paths = set()
        for s in existing:
            sp = os.path.normpath(s.get("source_path", s.get("source", "")))
            if sp:
                existing_paths.add(sp)
        requested = set(os.path.normpath(p) for p in video_paths if p)
        if requested and requested.issubset(existing_paths):
            for s in existing:
                all_segments.append({
                    "id": s["id"], "source": s.get("source", ""),
                    "start": s.get("start", 0), "end": s.get("end", 0),
                    "path": s.get("path", ""), "duration": s.get("duration", 0),
                })
            return _json.dumps({
                "segments": all_segments,
                "total": len(all_segments),
                "draft_id": "",
                "cached": True,
            }, ensure_ascii=False, indent=2)

    for vp in video_paths:
        if not os.path.exists(vp):
            continue
        total_dur = _dur(vp)
        if total_dur <= 0:
            continue

        base = os.path.splitext(os.path.basename(vp))[0]

        # 计算切分点
        cut_points = [0.0]
        if total_dur <= min_before_split:
            cut_points.append(total_dur)
        else:
            pos = MAX_CHUNK
            while pos < total_dur:
                remaining = total_dur - pos
                if remaining <= TAIL_MERGE:
                    break  # 剩余≤5分钟就合并到前一段,不再切
                cut_points.append(pos)
                pos += MAX_CHUNK
            cut_points.append(total_dur)

        for i in range(len(cut_points) - 1):
            start_t = cut_points[i]
            end_t = cut_points[i + 1]
            dur = end_t - start_t
            if dur <= 0:
                continue

            seg_id = f"seg_{len(all_segments):03d}"
            # 虚拟片段 — 不写文件,compress_segments 从 source_path 提取+压缩
            seg = state.add_segment(
                seg_id=seg_id, source=vp, start=start_t, end=end_t,
                path="", duration=dur,
                description=f"{base} 分段{i+1}({start_t:.0f}s-{end_t:.0f}s)",
            )
            # add_segment 已设置 source_path = os.path.abspath(source)
            all_segments.append({
                "id": seg_id, "source": vp, "start": start_t, "end": end_t,
                "path": "", "duration": dur,
            })

    state.save()

    # 写入 _index/
    try:
        from director.memory_store import save_delegate_entities
        save_delegate_entities(work_dir, "split_by_scenes: 切分原子片段",
            [{"tool": "split_by_scenes", "result": _json.dumps({"total": len(all_segments), "segments": all_segments}, ensure_ascii=False)}],
            "系统")
    except Exception:
        pass

    return _json.dumps({
        "segments": all_segments,
        "total": len(all_segments),
        "draft_id": "",
    }, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════
#  辅助:压缩后建稿
# ═══════════════════════════════════════════════════════════

def _ensure_draft_after_compress(state, all_segments: list, work_dir: str):
    """压缩完成后创建 Draft("main")"""
    try:
        from director.draft import Draft
        draft = Draft("main")
        draft._ensure_loaded()
        draft._data["source_videos"] = list(set(
            s.get("source_path", s.get("source", "")) for s in all_segments
        ))
        draft.timeline["main_track"]["segments"] = []
        for seg in all_segments:
            file_path = seg.get("path") or seg.get("compressed_path", "")
            if not file_path:
                continue
            draft_item = {
                "id": seg["id"],
                "source_path": file_path,
                "start": 0,
                "end": seg.get("duration", 0),
                "duration": seg.get("duration", 0),
                "description": seg.get("description", ""),
                "path": file_path,
                "original_source": seg.get("source", ""),
                "original_start": seg.get("start", 0),
                "original_end": seg.get("end", 0),
            }
            draft.timeline["main_track"]["segments"].append(draft_item)
        draft.save(label="compress_segments 建稿")
        state.draft_id = "main"
        state.save()
    except Exception as e:
        import traceback
        print(f"compress_segments 建稿失败(不影响压缩结果): {e}\n{traceback.format_exc()}")


# ═══════════════════════════════════════════════════════════
#  compress_segments — 标准压缩步骤(所有视频分析前必须先压缩)
# ═══════════════════════════════════════════════════════════

@tool(
    name="compress_segments",
    description=(
        "将已切分的所有视频片段统一压缩到720p CRF28，供AI视觉分析使用。"
        "压缩是视觉分析前的标准预处理步骤——所有用户的视频在分析前都必须经过此步骤。"
        "压缩后的片段存到 segments_compressed/ 目录，原始片段保持不变（最终渲染用原始片段）。"
        "效果：降分辨率 + 有损压缩 → 大幅减小文件体积 → 加速OSS上传和AI推理。"
        "并行路数由硬件画像动态决定(上限3路),无需手动设置。"
    ),
    phase="analyze",
    category="compress",
    tags=["compress", "preprocess", "video", "standard"],
    group="画面与场景",
)
def compress_segments() -> str:
    """
    压缩所有已切分的视频片段为统一格式(720p CRF32 ultrafast)，供VL分析使用。
    自动检测硬件编码器(NVENC/AMF/QSV)，有则用硬件编码加速。
    单路顺序压缩（默认1路，可设 CLIPMIND_COMPRESS_WORKERS 调高并行数）。
    超时: 1800s（可设 CLIPMIND_COMPRESS_TIMEOUT）。

    压缩后的片段包含压缩后的音频(AAC 128k)，供全流程使用。
    如果原片段无音频则仅输出视频轨道。

    Returns:
        JSON: {total, compressed, skipped, failed, output_dir, ...}
    """
    import json as _json
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from director.pipeline_state import PipelineState
    from director.tools.cut import _find_draft_dir

    work_dir = _find_draft_dir()
    state = PipelineState(work_dir)
    all_segments = state.segments

    if not all_segments:
        return _json.dumps({
            "error": "无片段可压缩。请先运行 split_by_scenes 切分素材。",
            "total": 0, "compressed": 0,
        }, ensure_ascii=False)

    compressed_dir = os.path.join(work_dir, "segments_compressed")
    os.makedirs(compressed_dir, exist_ok=True)

    # ── 从硬件画像读取最优配置 ────────────────────────────
    from director.hardware_profile import get_pipeline_config, run_benchmark
    _pipe_cfg = get_pipeline_config()
    _compress_cfg = _pipe_cfg.get("compress", {})
    _HW_ENCODER = _compress_cfg.get("encoder")  # None = 纯CPU
    _MAX_DIM = _compress_cfg.get("max_dim", 1280)
    _MAX_WORKERS = _compress_cfg.get("workers", 1)
    _COMPRESS_TIMEOUT = _compress_cfg.get("timeout", 1800)
    _COMPRESS_CQ = _compress_cfg.get("cq_or_crf", 32)
    _COMPRESS_PRESET = _compress_cfg.get("preset", "ultrafast")
    # 环境变量可覆盖
    _MAX_WORKERS = int(os.environ.get("CLIPMIND_COMPRESS_WORKERS", str(_MAX_WORKERS)))
    _COMPRESS_TIMEOUT = int(os.environ.get("CLIPMIND_COMPRESS_TIMEOUT", str(_COMPRESS_TIMEOUT)))
    _HW_ENCODER = os.environ.get("CLIPMIND_HW_ENCODER", _HW_ENCODER)

    total = len(all_segments)
    skipped_count = 0
    # 先筛选出需要压缩的任务
    compress_tasks = []

    for seg in all_segments:
        seg_id = seg.get("id", "?")
        src_path = seg.get("path", "")
        source_path = seg.get("source_path", "")
        seg_start = seg.get("start", 0)
        seg_end = seg.get("end", 0)
        seg_dur = seg_end - seg_start

        # 已存在压缩版本 → 跳过（校验 MP4 有效性）
        existing = seg.get("compressed_path", "")
        if existing and os.path.exists(existing):
            if _is_valid_mp4(existing):
                skipped_count += 1
                continue
            else:
                print(f"compress_segments: 压缩文件损坏(缺少moov atom)，重新压缩: {existing}")
                try:
                    os.remove(existing)
                except Exception as _re:
                    print(f"compress_segments: 删除损坏文件失败: {_re}")

        out_path = os.path.join(compressed_dir, f"{seg_id}.mp4")
        if os.path.exists(out_path) and os.path.getsize(out_path) > 1000 and _is_valid_mp4(out_path):
            # 文件已存在且有效(上次压缩遗留) → 补上 compressed_path
            for s in state._data.get("segments", []):
                if s.get("id") == seg_id:
                    s["compressed_path"] = out_path
                    if not s.get("path"):
                        s["path"] = out_path
                    break
            state.save()
            skipped_count += 1
            continue
        elif os.path.exists(out_path) and not _is_valid_mp4(out_path):
            print(f"compress_segments: 遗留压缩文件损坏(缺少moov atom)，重新压缩: {out_path}")
            try:
                os.remove(out_path)
            except Exception as _re:
                print(f"compress_segments: 删除损坏文件失败: {_re}")

        # 判断输入来源
        use_source = False
        src_for_info = src_path
        if not src_path or not os.path.exists(src_path):
            if source_path and os.path.exists(source_path) and seg_dur > 0:
                use_source = True
                src_for_info = source_path
            else:
                skipped_count += 1
                continue

        # 获取分辨率
        info = _get_video_info(src_for_info)
        w = info.get("width", 0)
        h = info.get("height", 0)
        if w <= 0 or h <= 0:
            print(f"compress_segments: 无法解析分辨率 {seg_id}")
            skipped_count += 1
            continue

        # 计算缩放
        longest = max(w, h)
        vf = None
        if longest > _MAX_DIM:
            scale_val = _MAX_DIM / longest
            new_w = int(w * scale_val)
            new_h = int(h * scale_val)
            new_w = new_w if new_w % 2 == 0 else new_w + 1
            new_h = new_h if new_h % 2 == 0 else new_h + 1
            vf = f"scale={new_w}:{new_h}"

        compress_tasks.append({
            "seg_id": seg_id,
            "use_source": use_source,
            "src_path": src_path,
            "source_path": source_path,
            "seg_start": seg_start,
            "seg_dur": seg_dur,
            "out_path": out_path,
            "vf": vf,
        })

    if not compress_tasks:
        return _json.dumps({
            "total": total, "compressed": 0,
            "skipped": skipped_count, "failed": 0,
            "message": "所有片段已压缩，无需处理",
            "output_dir": compressed_dir,
        }, ensure_ascii=False)

    def _compress_one(task: dict) -> dict:
        """压缩单个片段（供线程池调用）"""
        seg_id = task["seg_id"]
        out_path = task["out_path"]
        try:
            if task["use_source"]:
                cmd = [
                    "ffmpeg", "-y", "-ss", str(task["seg_start"]),
                    "-i", task["source_path"],
                    "-t", str(task["seg_dur"]),
                ]
            else:
                cmd = ["ffmpeg", "-y", "-i", task["src_path"]]

            if task["vf"]:
                cmd += ["-vf", task["vf"]]

            # 编码器选择: 硬件 > CPU ultrafast
            # VL 分析不要求高质量,所以全部用最快参数
            if _HW_ENCODER:
                cmd += ["-c:v", _HW_ENCODER]
                if _HW_ENCODER == "h264_nvenc":
                    cmd += ["-preset", "p1", "-cq", str(_COMPRESS_CQ)]
                elif _HW_ENCODER == "h264_amf":
                    cmd += ["-quality", "speed", "-qp_i", str(_COMPRESS_CQ), "-qp_p", str(_COMPRESS_CQ)]
                elif "qsv" in _HW_ENCODER:
                    cmd += ["-preset", "veryfast", "-global_quality", str(_COMPRESS_CQ)]
            else:
                cmd += ["-c:v", "libx264", "-preset", _COMPRESS_PRESET, "-crf", str(_COMPRESS_CQ)]

            cmd += ["-c:a", "aac", "-b:a", "128k", out_path]

            subprocess.run(cmd, capture_output=True, timeout=_COMPRESS_TIMEOUT, check=True)

            if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
                return {"seg_id": seg_id, "out_path": out_path, "success": True}
            else:
                return {"seg_id": seg_id, "success": False,
                        "error": "压缩输出为空或太小"}
        except subprocess.TimeoutExpired:
            return {"seg_id": seg_id, "success": False, "error": f"超时(>{_COMPRESS_TIMEOUT}s)"}
        except Exception as e:
            return {"seg_id": seg_id, "success": False,
                    "error": f"{type(e).__name__}: {str(e)[:100]}"}

    # 并行压缩
    compressed_count = 0
    failed_count = 0
    failed_details = []

    # 【调试】保存压缩前的数据快照
    _before_ids = [s.get("id", "?") for s in state._data.get("segments", [])]
    print(f"【调试】compress 开始: {len(_before_ids)} 个片段, IDs={_before_ids}")

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {pool.submit(_compress_one, t): t["seg_id"] for t in compress_tasks}
        for fut in as_completed(futures):
            result = fut.result()
            if result["success"]:
                # 在管线状态中注册 compressed_path
                segs_list = state._data.get("segments", [])
                found = False
                _before_len = len(segs_list)
                for s in segs_list:
                    if s.get("id") == result["seg_id"]:
                        s["compressed_path"] = result["out_path"]
                        if not s.get("path"):
                            s["path"] = result["out_path"]
                        found = True
                        break
                state.save()
                compressed_count += 1
                # 【调试】验证保存是否成功
                if found:
                    import json as _sj
                    try:
                        with open(state.state_path, encoding="utf-8") as _sf:
                            _saved = _sj.load(_sf)
                        _saved_cp = None
                        for _ss in _saved.get("segments", []):
                            if _ss.get("id") == result["seg_id"]:
                                _saved_cp = _ss.get("compressed_path", "")
                                break
                        print(f"【调试】压缩完成 {result['seg_id']} -> compressed_path={result['out_path']} (保存验证: {'OK' if _saved_cp else '缺失!'})")
                    except Exception as _se:
                        print(f"【调试】保存验证失败: {_se}")
                else:
                    print(f"【调试】严重: 压缩完成 {result['seg_id']} 但 state 中找不到该片段! segments列表长度={_before_len}")
            else:
                failed_count += 1
                failed_details.append(
                    f"{result['seg_id']}: {result.get('error', '未知错误')}")

    # 压缩完成后创建 Draft("main")
    state.draft_id = "main"
    state.save()
    _ensure_draft_after_compress(state, all_segments, work_dir)

    result_report = {
        "total": total,
        "compressed": compressed_count,
        "skipped": skipped_count,
        "failed": failed_count,
        "output_dir": compressed_dir,
    }
    if failed_details:
        result_report["failed_details"] = failed_details

    # 汇总日志
    in_size = 0
    out_size = 0
    for seg in all_segments:
        p = seg.get("path", "")
        cp = seg.get("compressed_path", "")
        if p and os.path.exists(p):
            in_size += os.path.getsize(p)
        if cp and os.path.exists(cp):
            out_size += os.path.getsize(cp)
    if in_size > 0 and out_size > 0:
        result_report["total_input_mb"] = round(in_size / 1024 / 1024, 1)
        result_report["total_output_mb"] = round(out_size / 1024 / 1024, 1)
        result_report["compression_ratio"] = f"{out_size/in_size*100:.0f}%"

    return _json.dumps(result_report, ensure_ascii=False, indent=2)


# 工具已通过 @tool 装饰器自动注册到 Registry
# 不再需要手动定义 TOOLS = [...]
