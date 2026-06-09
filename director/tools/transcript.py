"""
转录编辑工具 — 文字剪辑 & 切气口
==================================
基于 AI 转录(get_asr_transcript)的结构化数据,
对语音类素材进行高效剪辑.

核心能力:
  1. crop_silence — 自动切除句间长间隔(死气/气口)
  2. search_transcript — 关键词搜索定位时间段
  3. delete_sentences — 按句子删除片段
  4. rebuild_timeline_from_transcript — 从转录重建时间轴片段

设计原则:
  - 所有操作基于 draft.transcript 的缓存数据,不重复调 AI
  - 切分操作调用现有 timeline 工具,不重复造轮子
  - 转录数据 = 时间轴的文字视图,操作转录 = 操作时间轴
"""
import json
from pathlib import Path

from director.registry import tool

_PROJECT_DIR = Path(__file__).parent.parent.parent


def _parse_json(data) -> any:
    """安全解析 JSON"""
    if data is None or data == "":
        return None
    if isinstance(data, str):
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return None
    return data


def _crop_silence_ffmpeg(draft, source_videos: list, draft_id: str, min_gap_s: float) -> str:
    """用 ffmpeg silenceremove 检测静音并切除(无转录数据时的 fallback).

    扫描视频的音频轨,检测所有超过 min_gap_s 的静音段,
    将非静音段作为独立片段写入草稿.
    """
    import subprocess, re, os

    video = source_videos[0]
    if not os.path.exists(video):
        return f"源文件不存在: {video}"

    # 获取视频时长(ffprobe 不可用时用 ffmpeg 回退)
    total_dur = 0
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", video],
            capture_output=True, text=True, timeout=15
        )
        # ffmpeg 输出时长信息在 stderr: "Duration: 00:01:21.39, ..."
        m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", r.stderr)
        if m:
            h, m_min, s = float(m.group(1)), float(m.group(2)), float(m.group(3))
            total_dur = h * 3600 + m_min * 60 + s
    except Exception:
        total_dur = 0

    if total_dur <= 0:
        return f"无法获取视频时长"

    # 用 ffmpeg silencedetect 找出所有静音段
    # noise_dB: 噪声阈值,-50dB 适合大多数口播
    cmd = [
        "ffmpeg", "-i", video,
        "-af", f"silencedetect=noise=-50dB:d={min_gap_s}",
        "-f", "null", "-"
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=max(30, int(total_dur * 2)))
        output = r.stderr + r.stdout
    except subprocess.TimeoutExpired:
        return "ffmpeg 静音检测超时"
    except Exception as e:
        return f"ffmpeg 静音检测失败: {e}"

    # 解析 silencedetect 输出,找出所有静音段
    # silence_start: 1.96  /  silence_end: 3.96 | silence_duration: 2
    silence_starts = [float(m) for m in re.findall(r"silence_start:\s*([\d.]+)", output)]
    silence_ends = [float(m) for m in re.findall(r"silence_end:\s*([\d.]+)", output)]
    silence_durations = [float(m) for m in re.findall(r"silence_duration:\s*([\d.]+)", output)]

    if not silence_starts:
        return f"未检测到超过 {min_gap_s}s 的静音段,无需切气口"

    # 用静音段切出非静音片段
    cuts = []
    prev_end = 0.0
    total_gap = 0
    for i in range(len(silence_starts)):
        s_start = silence_starts[i]
        # 静音前的语音段
        if s_start > prev_end + 0.3:  # 至少 0.3s 的语音才算有效
            cuts.append({"start": round(prev_end, 1), "end": round(s_start, 1),
                         "duration": round(s_start - prev_end, 1)})
        # 跳过静音
        if i < len(silence_ends):
            gap_dur = silence_ends[i] - s_start
            total_gap += gap_dur
            prev_end = silence_ends[i]
        else:
            prev_end = s_start + min_gap_s

    # 最后一段
    if total_dur - prev_end > 0.5:
        cuts.append({"start": round(prev_end, 1), "end": round(total_dur, 1),
                     "duration": round(total_dur - prev_end, 1)})

    if not cuts:
        return f"静音切除后未保留有效片段"

    # 重建草稿片段
    new_segments = []
    for i, c in enumerate(cuts):
        new_segments.append({
            "id": i,
            "source_path": video,
            "start": c["start"],
            "end": c["end"],
            "duration": c["duration"],
            "speed": 1.0,
            "status": "keep",
        })

    draft.set_segments(new_segments)
    draft.save(f"ffmpeg 切气口 (gap > {min_gap_s}s)")

    total_new = sum(c["duration"] for c in cuts)
    lines = [
        f"✅ ffmpeg 切气口完成 (间隔 > {min_gap_s}s)",
        f"  检测到 {len(silence_starts)} 处静音,切除 {total_gap:.0f}s",
        f"  剩余 {len(cuts)} 个有效片段,共 {total_new:.0f}s",
    ]
    return "\n".join(lines)


@tool(
    name="crop_silence",
    description=(
        "自动切除语音片段之间的长间隔(死气/气口/空白段)."
        "扫描草稿中的转录时间戳,句间间隔超过 min_gap_s 的段落被自动切掉."
        "典型用法:60 分钟访谈 -> crop_silence(min_gap_s=1.5) -> 自动缩到 35 分钟左右."
    ),
    phase="edit",
    category="transcript",
    tags=["silence", "transcript", "crop", "speech"],
    group="语音与转写",
)
def crop_silence(draft_id: str = "", min_gap_s: float = 1.0) -> str:
    """
    自动切除语音片段之间的长间隔.

    工作原理:
      1. 读取草稿中的转录数据
      2. 扫描相邻句子的时间间隔
      3. 间隔 > min_gap_s 的视为"死气",切除
      4. 间隔 <= min_gap_s 的视为自然停顿,保留
      5. 根据保留下来的语音块重建时间轴片段

    Args:
        draft_id: 草稿 ID(留空则自动从 PipelineState 读取或创建)
        min_gap_s: 最小间隔阈值(秒),默认 1.0.
                   大于此值的间隔被切掉,小于等于此值的保留.

    Returns:
        切气口结果摘要
    """
    from director.draft import Draft
    from director.tools.cut import _find_draft_dir
    from director.pipeline_state import PipelineState

    # 确定 draft_id: 参数优先 -> PipelineState.draft_id -> "main"
    if not draft_id:
        try:
            work_dir = _find_draft_dir()
            state = PipelineState(work_dir)
            draft_id = state.draft_id or "main"
        except Exception:
            draft_id = "main"

    d = Draft(draft_id)
    if not d.load():
        # Draft 不存在 -> 尝试从 PipelineState 的 segments 自动建稿
        try:
            work_dir = _find_draft_dir()
            state = PipelineState(work_dir)
            segs = state.keep_segments()
            if segs:
                d._ensure_loaded()
                d._data["source_videos"] = list(set(
                    s.get("source_path", s.get("source", "")) for s in segs
                ))
                for seg in segs:
                    d.timeline["main_track"]["segments"].append({
                        "id": seg.get("id", ""),
                        "source_path": seg.get("path", ""),
                        "start": 0,
                        "end": seg.get("duration", 0),
                        "duration": seg.get("duration", 0),
                        "description": seg.get("description", ""),
                        "path": seg.get("path", ""),
                        "original_source": seg.get("source_path", ""),
                        "original_start": seg.get("start", 0),
                        "original_end": seg.get("end", 0),
                    })
                d.save(label="crop_silence 自动建稿")
                state.draft_id = draft_id
                state.save()
            else:
                return f"草稿 {draft_id} 不存在且 PipelineState 也无片段"
        except Exception as e:
            return f"草稿 {draft_id} 不存在且自动建稿失败: {e}"

    transcript = d.get_transcript()
    if not transcript:
        # 没有转录数据时,用 ffmpeg silenceremove 直接检测静音
        try:
            source_videos = d._data.get("source_videos", [])
            if source_videos:
                video_path = source_videos[0]
                return _crop_silence_ffmpeg(d, source_videos, draft_id, min_gap_s)
        except Exception as e:
            pass
        return "草稿中没有转录数据.请先调用 get_asr_transcript(video_path, draft_id=...)"

    segments = transcript.get("segments", [])
    if not segments or len(segments) < 2:
        return f"转录只有 {len(segments)} 个句子,无需切气口"

    # ── 1. 将句子按间隔合并为语音块 ──
    # 相邻句子的间隔 <= min_gap_s -> 同一语音块
    # 否则 -> 新语音块
    blocks = []  # [(block_start, block_end, [sentence_indices])]
    current_block_start = segments[0]["start"]
    current_block_end = segments[0]["end"]
    current_indices = [0]

    gaps_found = []  # 记录被切的间隔

    for i in range(1, len(segments)):
        prev = segments[i - 1]
        curr = segments[i]
        gap = curr["start"] - prev["end"]

        if gap > min_gap_s:
            # 间隔过大 -> 结束当前块,开始新块
            blocks.append((current_block_start, current_block_end, current_indices))
            gaps_found.append({
                "between_sentences": f"[{i-1}]->[{i}]",
                "gap_duration": round(gap, 1),
                "from": round(prev["end"], 1),
                "to": round(curr["start"], 1),
                "before_text": prev["text"][:30],
                "after_text": curr["text"][:30],
            })
            current_block_start = curr["start"]
            current_block_end = curr["end"]
            current_indices = [i]
        else:
            # 自然停顿 -> 合并
            current_block_end = curr["end"]
            current_indices.append(i)

    # 最后一个块
    blocks.append((current_block_start, current_block_end, current_indices))

    # ── 2. 检查最后一个句子之后的尾部静音 ──
    last_sentence_end = segments[-1]["end"]
    tail_gap = 0
    # 如果有视频总时长,可以检测尾部静音
    source_video = transcript.get("source_video", "")
    if source_video:
        from director.tools.analyze import _get_video_duration
        total_dur = _get_video_duration(source_video)
        if total_dur > 0 and total_dur - last_sentence_end > min_gap_s:
            tail_gap = round(total_dur - last_sentence_end, 1)

    # ── 3. 重建主轨道片段 ──
    source_paths = d.get_data().get("source_videos", [])
    source_path = source_paths[0] if source_paths else source_video

    new_segments = []
    for idx, (start, end, indices) in enumerate(blocks):
        # 保留原文(合并块内所有句子)
        texts = [segments[j]["text"] for j in indices]
        combined_text = " ".join(texts)[:200]

        new_segments.append({
            "id": idx,
            "source_path": source_path,
            "start": round(start, 1),
            "end": round(end, 1),
            "duration": round(end - start, 1),
            "speed": 1.0,
            "filters": {
                "crop": None,
                "chromakey": None,
                "color_grading": None,
                "color_preset": None,
                "denoise": None,
                "stabilize": None,
                "animation": None,
            },
            "text": combined_text,
            "status": "keep",
        })

    d.set_segments(new_segments)
    # 同步字幕:只保留未被切除的句子
    kept_indices = {j for b in blocks for j in b[2]}
    kept_subtitles = [
        {"start": s["start"], "end": s["end"], "text": s["text"]}
        for s in segments if s["index"] in kept_indices
    ]
    d.set_subtitles(kept_subtitles)
    d.save(f"切气口 (gap > {min_gap_s}s)")

    # ── 4. 统计 ──
    total_original = sum(s["end"] - s["start"] for s in segments)
    total_new = sum(b[1] - b[0] for b in blocks)
    total_gap = sum(g["gap_duration"] for g in gaps_found)

    result = [
        f"✅ 切气口完成 (间隔 > {min_gap_s}s)",
        f"  原始语音总长: {total_original:.0f}s",
        f"  切除死气: {total_gap:.0f}s ({len(gaps_found)} 处)",
    ]
    if tail_gap > 0:
        result.append(f"  尾部静音: {tail_gap:.0f}s")
    result.append(f"  剩余有效: {total_new:.0f}s ({len(blocks)} 个片段)")

    if gaps_found:
        result.append(f"\n  切除详情:")
        for g in gaps_found[:10]:
            result.append(
                f"    {g['from']:.1f}s -> {g['to']:.1f}s "
                f"(间隔 {g['gap_duration']:.1f}s)"
            )
        if len(gaps_found) > 10:
            result.append(f"    ... 共 {len(gaps_found)} 处")

    return "\n".join(result)


@tool(
    name="search_transcript",
    description=(
        "在草稿转录文本中搜索关键词,返回匹配句子的时间位置."
        "Agent 用此工具快速定位语音内容中的关键信息,无需逐帧看视频."
        "返回匹配句子的索引,文本,起止时间."
    ),
    phase="analyze",
    category="transcript",
    tags=["search", "transcript", "keyword"],
    group="语音与转写",
)
def search_transcript(draft_id: str, keyword: str) -> str:
    """
    在转录中搜索关键词.

    Args:
        draft_id: 草稿 ID
        keyword: 搜索关键词(支持中文/英文)

    Returns:
        匹配结果(句子索引,文本片段,时间位置)
    """
    from director.draft import Draft

    if not keyword or not keyword.strip():
        return "请提供搜索关键词"

    d = Draft(draft_id)
    if not d.load():
        return f"草稿 {draft_id} 不存在"

    transcript = d.get_transcript()
    if not transcript:
        return "草稿中没有转录数据.请先调用 get_asr_transcript"

    segments = transcript.get("segments", [])
    if not segments:
        return "转录为空"

    kw = keyword.strip().lower()
    matches = []
    for seg in segments:
        text = seg.get("text", "")
        if kw in text.lower():
            # 高亮关键词位置
            idx = text.lower().find(kw)
            start_ctx = max(0, idx - 10)
            end_ctx = min(len(text), idx + len(kw) + 10)
            ctx = text[start_ctx:end_ctx]
            if start_ctx > 0:
                ctx = "..." + ctx
            if end_ctx < len(text):
                ctx = ctx + "..."

            matches.append({
                "index": seg["index"],
                "start": seg["start"],
                "end": seg["end"],
                "duration": round(seg["end"] - seg["start"], 1),
                "text": seg["text"],
                "context": ctx,
            })

    if not matches:
        return f"未找到包含「{keyword}」的句子"

    # 按 index 排序
    matches.sort(key=lambda m: m["index"])

    lines = [f"🔍 找到 {len(matches)} 处「{keyword}」:"]
    for m in matches:
        lines.append(
            f"  [{m['index']}] {m['start']:.1f}s-{m['end']:.1f}s ({m['duration']:.1f}s)"
        )
        lines.append(f"      \"{m['text'][:80]}\"")

    return "\n".join(lines)


@tool(
    name="delete_sentences",
    description=(
        "按转录句子索引删除对应的时间轴片段."
        "传入 sentence_indices 列表(从 search_transcript 获取的 index),"
        "删除这些句子对应的视频片段."
        "内部调用现有的时间轴操作工具完成实际切割."
    ),
    phase="edit",
    category="transcript",
    tags=["delete", "transcript", "edit"],
    group="语音与转写",
)
def delete_sentences(draft_id: str, sentence_indices_json: str) -> str:
    """
    删除指定转录句子对应的时间轴片段.

    Args:
        draft_id: 草稿 ID
        sentence_indices_json: 要删除的句子索引 JSON 数组,如 "[0, 3, 7]"

    Returns:
        删除结果摘要
    """
    from director.draft import Draft

    indices = _parse_json(sentence_indices_json)
    if not indices or not isinstance(indices, list):
        return "sentence_indices_json 格式错误,需要 JSON 数组如 [0, 3, 7]"

    d = Draft(draft_id)
    if not d.load():
        return f"草稿 {draft_id} 不存在"

    transcript = d.get_transcript()
    if not transcript:
        return "草稿中没有转录数据"

    segments = transcript.get("segments", [])
    idx_to_seg = {s["index"]: s for s in segments}

    # 找出要删除的时间范围
    deleted = []
    for idx in sorted(indices):
        seg = idx_to_seg.get(idx)
        if seg:
            deleted.append(seg)

    if not deleted:
        return "没有找到对应的句子"

    # 找出不被删除的句子 -> 保留的时间范围
    delete_indices = set(indices)
    kept_segments = [s for s in segments if s["index"] not in delete_indices]

    # 用保留的句子重建时间轴
    source_paths = d.get_data().get("source_videos", [])
    source_path = source_paths[0] if source_paths else transcript.get("source_video", "")

    new_segments = []
    new_id = 0
    for seg in kept_segments:
        new_segments.append({
            "id": new_id,
            "source_path": source_path,
            "start": seg["start"],
            "end": seg["end"],
            "duration": round(seg["end"] - seg["start"], 1),
            "speed": 1.0,
            "filters": {
                "crop": None,
                "chromakey": None,
                "color_grading": None,
                "color_preset": None,
                "denoise": None,
                "stabilize": None,
                "animation": None,
            },
            "text": seg["text"],
            "status": "keep",
        })
        new_id += 1

    d.set_segments(new_segments)
    # 同步字幕
    kept_subtitles = [
        {"start": s["start"], "end": s["end"], "text": s["text"]}
        for s in kept_segments
    ]
    d.set_subtitles(kept_subtitles)
    d.save(f"删除 {len(deleted)} 句")

    total_deleted = sum(s["end"] - s["start"] for s in deleted)
    lines = [
        f"✅ 已删除 {len(deleted)} 个句子 (共 {total_deleted:.1f}s):",
    ]
    for s in deleted[:10]:
        lines.append(f"  [{s['index']}] {s['start']:.1f}s-{s['end']:.1f}s: {s['text'][:50]}")
    if len(deleted) > 10:
        lines.append(f"  ... 共 {len(deleted)} 句")
    lines.append(f"\n  保留 {len(kept_segments)} 句,已更新草稿")

    return "\n".join(lines)


@tool(
    name="show_transcript",
    description=(
        "查看草稿转录文本概览.返回前 N 句的索引,文本,时间范围,"
        "以及总句数,总时长等统计信息.Agent 用此工具快速了解语音内容全貌."
    ),
    phase="analyze",
    category="transcript",
    tags=["transcript", "view", "overview"],
    group="语音与转写",
)
def show_transcript(draft_id: str, offset: int = 0, limit: int = 20) -> str:
    """
    查看转录概览.

    Args:
        draft_id: 草稿 ID
        offset: 起始句子索引
        limit: 返回句子数

    Returns:
        转录概览文本
    """
    from director.draft import Draft

    d = Draft(draft_id)
    if not d.load():
        return f"草稿 {draft_id} 不存在"

    transcript = d.get_transcript()
    if not transcript:
        return "草稿中没有转录数据"

    segments = transcript.get("segments", [])
    if not segments:
        return "转录为空"

    total_dur = sum(s["end"] - s["start"] for s in segments)

    lines = [
        f"📝 转录概览: {len(segments)} 句, 总语音 {total_dur:.0f}s",
        f"  源素材: {transcript.get('source_video', '未知')}\n",
    ]

    show = segments[offset:offset + limit]
    for s in show:
        dur = s["end"] - s["start"]
        lines.append(
            f"  [{s['index']:3d}] {s['start']:6.1f}s -> {s['end']:6.1f}s "
            f"({dur:.1f}s)  {s['text']}"
        )

    if offset + limit < len(segments):
        lines.append(f"\n  ... 还有 {len(segments) - offset - limit} 句."
                     f"用 offset={offset + limit} 继续查看")

    return "\n".join(lines)
