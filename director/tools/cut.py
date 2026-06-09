"""
裁剪工具 — 实际摘取视频片段
==============================

AI 分析素材后,立即切割有用片段到物理文件.
不同于 mark_keep/mark_discard(只标记不切割),这里的工具会:
  1. 用 ffmpeg 提取时间范围
  2. 保存到 drafts/<id>/segments/
  3. 注册到管线状态
"""
import os, sys, subprocess, json, hashlib
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from director.registry import tool

PROJECT_DIR = Path(__file__).parent.parent.parent


def _safe_dirname(filepath: str) -> str:
    """从文件名生成不含中文/空格的目录名."""
    name = os.path.splitext(os.path.basename(filepath))[0]
    # 纯 ASCII 直接返回
    if name.isascii() and " " not in name:
        return name
    # 非 ASCII -> 用源文件路径的 MD5 前 8 位
    h = hashlib.md5(filepath.encode("utf-8", errors="replace")).hexdigest()[:8]
    return f"src_{h}"


def _find_draft_dir() -> str:
    """查找当前活跃的管线工作目录。

    只通过环境变量查找，不扫描、不猜测。
    所有子线程继承进程级环境变量，不存在"子线程找不到"的问题。
    """
    from director.workspace import get_active_project_dir
    return get_active_project_dir()


def _load_state(work_dir: str):
    """加载管线状态(延迟导入避免循环)"""
    from director.pipeline_state import PipelineState
    return PipelineState(work_dir)


def _extract_segment(src: str, start: float, end: float, out: str) -> bool:
    """
    用 ffmpeg 提取视频片段.

    使用 -ss 输入 seeking(PTS 重置为 0),不做二次 trim 滤镜.
    这是 FACT.md 铁律:ffmpeg `-ss` 输入 seeking 后 PTS 重置为 0,
    不能同时加 `trim=start={t0}` 滤镜.
    """
    src = os.path.abspath(src)
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)

    # 如果文件已存在,跳过
    if os.path.exists(out) and os.path.getsize(out) > 1000:
        return True

    duration = end - start

    def _stderr_tail(stderr: str, n: int = 500) -> str:
        """取 stderr 尾部,跳过 ffmpeg 版本 banner."""
        lines = stderr.strip().split("\n")
        # 跳过前几行(banner + 空行),保留最后 n 行
        tail = [l for l in lines if l.strip() and not l.startswith("  ")]
        return "\n".join(tail[-max(1, n // 80):])

    # 三种策略逐级回退:copy -> libx264 -> mpeg4(兜底)
    strategies = [
        {
            "label": "copy",
            "cmd": ["ffmpeg", "-y", "-ss", str(start), "-i", src,
                    "-t", str(duration), "-c", "copy",
                    "-avoid_negative_ts", "make_zero", out],
        },
        {
            "label": "libx264",
            "cmd": ["ffmpeg", "-y", "-ss", str(start), "-i", src,
                    "-t", str(duration), "-c:v", "libx264", "-preset", "fast",
                    "-c:a", "aac", "-avoid_negative_ts", "make_zero", out],
        },
        {
            "label": "mpeg4",
            "cmd": ["ffmpeg", "-y", "-ss", str(start), "-i", src,
                    "-t", str(duration), "-c:v", "mpeg4", "-q:v", "5",
                    "-c:a", "aac", out],
        },
    ]

    for strat in strategies:
        try:
            result = subprocess.run(
                strat["cmd"], capture_output=True, text=True, timeout=180,
                encoding="utf-8", errors="replace",
            )
            if result.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 1000:
                return True
            # 失败 -> 记日志,尝试下一个策略
            tail = _stderr_tail(result.stderr)
            print(f"[cut] {strat['label']} 失败: {tail}")
        except subprocess.TimeoutExpired:
            print(f"[cut] {strat['label']} 超时 (180s)")
        except Exception as e:
            print(f"[cut] {strat['label']} 异常: {e}")

    return False


def _get_duration(filepath: str) -> float:
    """获取视频时长(秒)"""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        filepath,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                                encoding="utf-8", errors="replace")
        return float(result.stdout.strip())
    except Exception:
        return 0


def _check_mixed_resolution(file_paths: list[str]) -> tuple:
    """检查文件列表是否有混合分辨率.全部一致返回空tuple,不一致返回(目标宽,目标高)."""
    resolutions = {}
    for fp in file_paths:
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "quiet",
                 "-select_streams", "v:0",
                 "-show_entries", "stream=width,height",
                 "-of", "csv=p=0", fp],
                capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="replace",
            )
            parts = r.stdout.strip().split(",")
            if len(parts) == 2:
                res = (int(parts[0]), int(parts[1]))
                resolutions[res] = resolutions.get(res, 0) + 1
        except Exception:
            pass

    if len(resolutions) <= 1:
        return ()  # 一致或无法检测,无需缩放

    # 取最多片段使用的分辨率作为目标
    target = max(resolutions, key=resolutions.get)
    return target


# ─── 工具定义 ──────────────────────────────────────────────

def _write_cut_to_draft(draft_id: str, seg_id: str, source: str,
                         start: float, end: float, out_path: str,
                         duration: float, description: str):
    """将裁切片段的元数据原子写入 Draft 主轨道（线程安全）。"""
    try:
        from director.draft import Draft
        seg_data = {
            "id": seg_id,
            "source_path": os.path.abspath(out_path),
            "start": 0,        # 裁切文件本身从 0 开始
            "end": duration,   # 播放到文件结束
            "duration": duration,
            "description": description,
            "path": os.path.abspath(out_path),
            "original_source": os.path.abspath(source),
            "original_start": round(start, 2),
            "original_end": round(end, 2),
        }
        draft = Draft(draft_id)
        draft.append_cut_segment(seg_data)
    except Exception as e:
        print(f"[cut] 写草稿失败: {e}")


@tool(
    name="cut_segment",
    description="""从素材中裁切一个片段到独立文件.
调用此工具后片段立即保存到 work_dir/segments/,并可注册到管线状态和草稿轨道.

参数:
    source: **源视频的完整文件路径**(如 C:/path/to/video.mp4),不是 seg_id!
             传 seg_id 会导致“文件不存在”错误.
    start: 起始时间(秒)
    end: 结束时间(秒)
    description: 这个片段的内容描述(给后续阶段看)
    seg_id: 可选片段ID,不传自动生成
    draft_id: 可选草稿ID.传了则同时写入草稿的主轨道,供后续阶段直接使用

返回: 裁切结果(包含片段ID,文件路径,时长)

何时用:
    - 看素材时,发现有价值的段落 -> 立即调用此工具裁出来
    - 不要在 mark_keep 之后忘记裁切.裁切 = 确认保留.

何时千万别用:
    - 不确定要不要保留 -> 先用其他方式预览,确定后再裁
    - 已经裁过的片段不要重复裁""",
    phase="analyze",
    category="timeline",
    tags=["cut", "segment", "extract"],
    group=["裁切与提取", "细剪与节奏"],
)
def cut_segment(source: str, start: float, end: float,
                description: str = "", seg_id: str = "",
                draft_id: str = "") -> str:
    """裁切片段到文件."""
    work_dir = _find_draft_dir()
    state = _load_state(work_dir)

    # 生成段 ID
    if not seg_id:
        existing = len(state.segments)
        seg_id = f"seg_{existing:03d}"

    # 检查 end 不超出视频实际时长
    source_duration = _get_duration(source)
    if source_duration > 0 and end > source_duration:
        end = source_duration

    # 输出路径(避免中文路径导致 ffmpeg 失败)
    seg_dir = os.path.join(work_dir, "segments", _safe_dirname(source))
    out_path = os.path.join(seg_dir, f"{seg_id}.mp4")

    # 执行裁切
    if not _extract_segment(source, start, end, out_path):
        return f"❌ 裁切失败: {source} {start:.1f}s->{end:.1f}s"

    # 获取实际时长
    duration = _get_duration(out_path) or (end - start)

    # 注册到管线状态(保持 list_segments 等工具仍可用)
    seg = state.add_segment(
        seg_id=seg_id,
        source=source,
        start=start,
        end=end,
        path=out_path,
        duration=duration,
        description=description,
    )
    state.save()

    # 如果有 draft_id,直接写入草稿的主轨道
    if draft_id:
        _write_cut_to_draft(draft_id, seg_id, source, start, end,
                            out_path, duration, description)

    return (
        f"✅ 已裁切 [{seg_id}] {description or '未命名'}\n"
        f"  来源: {os.path.basename(source)} {start:.1f}s->{end:.1f}s\n"
        f"  时长: {duration:.1f}s\n"
        f"  路径: {out_path}\n"
        f"  当前共 {len(state.keep_segments())} 个片段"
        f"{'  -> 已写入草稿' if draft_id else ''}"
    )


@tool(
    name="discard_segment",
    description="""弃用已裁切的片段(不删除文件,只从使用列表中移除).

参数:
    seg_id: 片段ID
    draft_id: 可选草稿ID.传了则同时从草稿主轨道移除

何时用:
    - 裁切后发现某段不合适
    - 细筛阶段决定去掉某段""",
    phase="analyze",
    category="timeline",
    tags=["cut", "discard", "remove"],
    group="裁切与提取",
)
def discard_segment(seg_id: str, draft_id: str = "") -> str:
    """弃用片段."""
    work_dir = _find_draft_dir()
    state = _load_state(work_dir)

    seg = state.get_segment(seg_id)
    if not seg:
        return f"❌ 片段 {seg_id} 不存在"

    state.discard_segment(seg_id)
    state.save()

    kept = state.keep_segments()

    # 如果有 draft_id,也从 Draft 主轨道移除
    if draft_id:
        try:
            from director.draft import Draft
            d = Draft(draft_id)
            if d.load():
                existing = d.timeline["main_track"]["segments"]
                d.timeline["main_track"]["segments"] = [
                    s for s in existing if s.get("id") != seg_id
                ]
                d.save(label=f"discard_{seg_id}")
        except Exception:
            pass

    return f"❌ 已弃用 [{seg_id}](文件保留)\n当前保留: {len(kept)} 个片段"


def _concat_to_preview(draft_id: str, output_path: str) -> tuple[bool, str]:
    """将草稿主轨道的所有片段拼接为预览视频.

    Returns:
        (成功?, 路径或错误信息)
    """
    from director.draft import Draft
    d = Draft(draft_id)
    if not d.load():
        return False, f"草稿 {draft_id} 不存在"
    segments = d.timeline["main_track"]["segments"]
    if not segments:
        return False, "草稿主轨道无片段"

    # 筛选有效的片段文件
    valid = []
    for s in segments:
        p = s.get("path", s.get("source_path", ""))
        if p and os.path.isfile(p):
            valid.append(p)
    if not valid:
        return False, "无有效片段文件"

    # 检查各片段分辨率是否一致
    need_scale = _check_mixed_resolution(valid)

    if need_scale:
        # 分辨率不一致:用 concat filter + scale 统一
        inputs = []
        filter_inputs = []
        for i, vp in enumerate(valid):
            inputs.extend(["-i", vp])
            w, h = need_scale
            filter_inputs.append(
                f"[{i}:v]scale={w}:{h}:flags=bilinear:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black[v{i}];"
                f"[{i}:a]anull[a{i}];"
                f"[v{i}][a{i}]"
            )
        filter_str = "".join(filter_inputs)
        filter_str += f"concat=n={len(valid)}:v=1:a=1[outv][outa]"
        cmd = [
            "ffmpeg", "-y",
        ] + inputs + [
            "-filter_complex", filter_str,
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-c:a", "aac", "-b:a", "128k",
            output_path,
        ]
    else:
        # 分辨率一致:直接 -c copy(毫秒级)
        list_path = output_path + ".concat.txt"
        with open(list_path, "w", encoding="utf-8") as f:
            for vp in valid:
                f.write(f"file '{vp.replace(chr(39), chr(39)*2)}'\n")
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", list_path,
            "-c", "copy",
            output_path,
        ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=300, encoding="utf-8", errors="replace")
        if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            try:
                os.remove(output_path + ".concat.txt")
            except Exception:
                pass
            return True, output_path
        return False, f"ffmpeg 拼接失败: {result.stderr[-300:]}"
    except subprocess.TimeoutExpired:
        return False, "ffmpeg 拼接超时 (300s)"
    except Exception as e:
        return False, f"拼接异常: {e}"


def _build_draft_timeline_json(draft_id: str) -> dict | None:
    """将 Draft 主轨道 + 叠层轨道转为 player.html 需要的 timeline JSON 格式."""
    from director.draft import Draft
    d = Draft(draft_id)
    if not d.load():
        return None
    segments = d.timeline["main_track"]["segments"]
    if not segments:
        return None

    cumulative = 0.0
    shots = []
    for s in segments:
        dur = s.get("duration", 0) or 0
        sp = s.get("path", s.get("source_path", ""))
        shots.append({
            "shot_id": str(s.get("id", "")),
            "source_video": sp,
            "time_range": [0, dur],
            "duration": dur,
            "speed": 1.0,
            "description": s.get("description", ""),
            "_cumulativeStart": cumulative,
        })
        cumulative += dur

    # ── 叠层轨道(PiP 画中画)─
    parallel_clips = []
    for c in d.timeline.get("overlay_track", []):
        src = c.get("source_path", "")
        dur = c.get("duration", 0) or 0
        w = c.get("width", 0.35)
        h = c.get("height", 0.35)
        parallel_clips.append({
            "clip_id": c.get("id", 0),
            "source_video": src,
            "time_range": [0, dur],
            "start_time": c.get("start_time", 0),
            "duration": dur,
            "position": {"x": c.get("x", 0.6), "y": c.get("y", 0.6)},
            "scale": max(w, h),
            "opacity": c.get("opacity", 1.0),
            "description": "overlay",
        })

    # ── 第二视频轨(video_track_2 -> 全屏叠层)─
    for c in d.timeline.get("video_track_2", []):
        src = c.get("source_path", "")
        dur = c.get("duration", 0) or 0
        parallel_clips.append({
            "clip_id": c.get("id", 0),
            "source_video": src,
            "time_range": [0, dur],
            "start_time": c.get("start_time", 0),
            "duration": dur,
            "position": {"x": 0, "y": 0},
            "scale": 1.0,
            "opacity": c.get("opacity", 1.0),
            "description": "video_track_2",
        })

    return {
        "content_type": "auto",
        "title": "",
        "total_duration": cumulative,
        "shots": shots,
        "parallel_clips": parallel_clips,
        "overlays": [],
    }


def _render_via_browser(draft_id: str, output_path: str) -> tuple[bool, str]:
    """用 Playwright 浏览器渲染 Draft 预览视频.

    流程:启动预览服务器 -> 设置 Draft 时间线 -> Playwright 录屏 -> 保存文件.
    """
    timeline = _build_draft_timeline_json(draft_id)
    if not timeline:
        return False, "草稿无片段或不存在"
    total_dur = timeline["total_duration"]
    if total_dur <= 0:
        return False, "草稿总时长为 0"

    # 启动预览服务器
    try:
        from preview import PreviewServer
        server = PreviewServer(port=0)
        server.set_timeline(timeline)

        # 设置媒体搜索路径(含 segments 目录 + 工作目录)
        work_dir = _find_draft_dir()
        media_roots = [
            os.path.join(work_dir, "segments"),
            work_dir,
        ]
        server.set_media_roots(media_roots)
        actual_port = server.start()
        server_url = f"http://127.0.0.1:{actual_port}"
    except Exception as e:
        return False, f"预览服务器启动失败: {e}"

    # 用 Playwright 捕获
    try:
        from preview.capture import capture_preview_sync
        b64 = capture_preview_sync(
            server_url=server_url,
            duration_seconds=total_dur,
            width=480,
            height=270,
            timeout=int(total_dur * 1.5 + 30),
        )
    except Exception as e:
        return False, f"Playwright 捕获失败: {e}"
    finally:
        try:
            server.stop()
        except Exception:
            pass

    if not b64:
        return False, "Playwright 捕获返回空"

    # base64 -> 文件
    try:
        import base64
        raw = base64.b64decode(b64)
        with open(output_path, "wb") as f:
            f.write(raw)
        if os.path.getsize(output_path) > 1000:
            return True, output_path
        return False, "捕获文件太小"
    except Exception as e:
        return False, f"base64 解码失败: {e}"


@tool(
    name="render_draft_preview",
    description="""用浏览器渲染草稿预览视频.

AI 首先要调此工具生成预览,然后用 watch_video() 看效果,
再根据效果用 discard_segment() 剔除片段.
浏览器渲染比 ffmpeg 直接拼接更准确,可以看到真正的画面合成效果.

参数:
    draft_id: 草稿 ID
    preview_name: 预览文件名(可选,默认 auto_preview.webm)

返回: 预览视频路径""",
    phase="analyze",
    category="timeline",
    tags=["draft", "preview", "browser"],
    group="预览与质检",
)
def render_draft_preview(draft_id: str, preview_name: str = "") -> str:
    """用浏览器渲染草稿预览视频."""
    work_dir = _find_draft_dir()
    preview_dir = os.path.join(work_dir, "previews")
    os.makedirs(preview_dir, exist_ok=True)

    filename = preview_name or f"preview_{draft_id[:8]}.webm"
    if not filename.endswith((".webm", ".mp4")):
        filename += ".webm"
    output_path = os.path.join(preview_dir, filename)

    # 先试浏览器渲染
    success, result = _render_via_browser(draft_id, output_path)
    if not success:
        # 浏览器渲染失败 -> fallback 到 ffmpeg concat
        print(f"[render_draft_preview] 浏览器渲染失败: {result},回退到 ffmpeg")
        success2, result2 = _concat_to_preview(draft_id, output_path.replace(".webm", ".mp4"))
        if not success2:
            return f"❌ 预览生成失败(浏览器: {result};ffmpeg: {result2})"
        result = result2

    return (
        f"✅ 预览视频已生成\n"
        f"  路径: {result}\n"
        f"  大小: {os.path.getsize(result) / 1024 / 1024:.1f}MB\n"
        f"  用 watch_video('{result}') 查看效果"
    )


@tool(
    name="reorder_draft_segments",
    description="""重新排列草稿主轨道片段的播放顺序.

AI 先用 render_draft_preview 看当前顺序,再用此工具调整.
调整后可以再次 render_draft_preview + watch_video 确认效果.

参数:
    draft_id: 草稿 ID
    new_order: 新的片段 ID 顺序列表,如 ["seg_000", "seg_002", "seg_001"]

返回: 重排结果""",
    phase="plan",
    category="timeline",
    tags=["draft", "arrange", "reorder"],
    group=["裁切与提取", "细剪与节奏", "时间线与编排"],
)
def reorder_draft_segments(draft_id: str, new_order: list[str]) -> str:
    """重新排列草稿主轨道片段."""
    import json
    if isinstance(new_order, str):
        new_order = json.loads(new_order)

    from director.draft import Draft
    d = Draft(draft_id)
    if not d.load():
        return f"❌ 草稿 {draft_id} 不存在"

    segments = d.timeline["main_track"]["segments"]
    if not segments:
        return "❌ 草稿主轨道无片段"

    # 构建 id -> segment 映射
    id_map = {}
    for s in segments:
        sid = str(s.get("id", ""))
        id_map[sid] = s

    # 按新顺序重建
    reordered = []
    seen = set()
    for sid in new_order:
        if sid in id_map and sid not in seen:
            reordered.append(id_map[sid])
            seen.add(sid)

    # 追加未指定的片段
    for s in segments:
        sid = str(s.get("id", ""))
        if sid not in seen:
            reordered.append(s)

    if len(reordered) != len(segments):
        return "❌ 重排失败:片段数量不一致"

    d.timeline["main_track"]["segments"] = reordered
    d.save(label="reorder")

    return (
        f"✅ 已重排顺序({len(segments)} 个片段)\n"
        f"  新顺序: {' -> '.join(new_order)}\n"
        f"  调 render_draft_preview(draft_id=\"{draft_id}\") 看效果"
    )


@tool(
    name="list_segments",
    description="""列出当前所有已裁切片段及其状态.

返回: 片段列表(ID,来源,时间范围,时长,描述,状态)

何时用:
    - 细筛前查看有哪些片段
    - 编排前了解可用素材
    - 任何需要了解当前素材库的时候""",
    phase="analyze",
    category="timeline",
    tags=["cut", "list", "status"],
    group="素材信息",
)
def list_segments() -> str:
    """列出所有片段."""
    work_dir = _find_draft_dir()
    state = _load_state(work_dir)
    all_segs = state._data.get("segments", [])

    if not all_segs:
        return "(暂无已裁切片段)"

    kept = [s for s in all_segs if s.get("status") != "discard"]
    disc = [s for s in all_segs if s.get("status") == "discard"]

    lines = [f"共 {len(all_segs)} 个片段(保留 {len(kept)} 个, 弃用 {len(disc)} 个)"]
    lines.append("")

    if kept:
        lines.append("## 保留片段")
        for s in kept:
            desc = f" — {s.get('description', '')}" if s.get("description") else ""
            lines.append(
                f"  ✅ [{s['id']}] {s['source']} {s['start']:.0f}s->{s['end']:.0f}s "
                f"({s['duration']:.0f}s){desc}"
            )

    if disc:
        lines.append("\n## 弃用片段")
        for s in disc:
            lines.append(f"  ❌ [{s['id']}] {s['source']} ({s['duration']:.0f}s)")

    return "\n".join(lines)


@tool(
    name="get_pipeline_status",
    description="""查看当前管线的完整状态.

返回: 当前阶段,片段统计,编排状态,编辑进度

何时用:
    - 阶段切换时确认产物
    - 不确定当前进度时""",
    phase="all",
    category="timeline",
    tags=["pipeline", "status"],
    group="素材信息",
)
def get_pipeline_status() -> str:
    """查看管线状态."""
    work_dir = _find_draft_dir()
    state = _load_state(work_dir)
    return state.summary()


@tool(
    name="render_arrangement_preview",
    description="""渲染编排阶段预览视频(含 BGM 音频).

编排阶段先用 search_music() 选好 BGM,再用此工具渲染带有 BGM 的预览版本,
然后 watch_video() 观看实际效果,判断 BGM 和画面的匹配度.

参数:
    draft_id: 草稿 ID
    bgm_path: BGM 文件路径(可选,不传则只输出片段原始音频)

返回: 预览视频路径与说明""",
    phase="plan",
    category="preview",
    tags=["draft", "preview", "bgm", "arrangement"],
    group=["预览与质检", "时间线与编排"],
)
def render_arrangement_preview(draft_id: str, bgm_path: str = "") -> str:
    """用 ffmpeg 拼接草稿片段 + 可选混合 BGM,生成带音频的编排预览."""
    import subprocess, os

    work_dir = _find_draft_dir()
    preview_dir = os.path.join(work_dir, "previews")
    os.makedirs(preview_dir, exist_ok=True)

    concat_path = os.path.join(preview_dir, f"arr_concat_{draft_id[:8]}.mp4")

    # 第一遍:拼接片段(保留原始音频)
    success, result = _concat_to_preview(draft_id, concat_path)
    if not success:
        return f"❌ 拼接失败: {result}"

    if not bgm_path:
        return (
            f"✅ 编排预览已生成(原始音频)\n"
            f"  路径: {result}\n"
            f"  用 watch_video('{result}') 查看效果"
        )

    # 第二遍:混入 BGM
    if not os.path.isfile(bgm_path):
        return f"❌ BGM 文件不存在: {bgm_path}"

    output_path = os.path.join(preview_dir, f"arr_bgm_{draft_id[:8]}.mp4")
    cmd = [
        "ffmpeg", "-y",
        "-i", result,
        "-i", bgm_path,
        "-filter_complex",
        "[1:a]volume=0.25[a_bgm];[0:a][a_bgm]amix=inputs=2:duration=first[a_out]",
        "-map", "0:v",
        "-map", "[a_out]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-shortest",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True,
                       timeout=120, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return "❌ BGM 混合超时 (120s)"
    except subprocess.CalledProcessError as e:
        return f"❌ BGM 混合失败: {e.stderr[-200:]}"
    except Exception as e:
        return f"❌ BGM 混合异常: {e}"

    if not os.path.exists(output_path) or os.path.getsize(output_path) < 1000:
        return "❌ BGM 混合输出文件异常"

    return (
        f"✅ 编排预览已生成(含 BGM)\n"
        f"  路径: {output_path}\n"
        f"  用 watch_video('{output_path}') 观看实际效果"
    )
