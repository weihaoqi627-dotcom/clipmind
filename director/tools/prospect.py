"""
素材勘探工具 — AI 先"瞄一眼"素材，判断类型和价值
=============================================
不是完整分析，是快速采样，回答三个问题:
  1. 这是什么类型的素材？
  2. 有没有价值？
  3. 下一步建议怎么做？
"""
import os, json, subprocess, hashlib, re, time
from director.registry import tool
from director.logging_config import get_logger

log = get_logger("director.tools.prospect")

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _get_duration(video_path: str) -> float:
    """获取视频时长(秒)，用 ffmpeg -i 兼容所有版本"""
    import re
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", video_path],
            capture_output=True, timeout=30, check=False,
        )
        output = (r.stdout + r.stderr).decode("utf-8", errors="replace")
        m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", output)
        if m:
            h, m2, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
            return float(h * 3600 + m2 * 60 + s)
    except Exception:
        pass
    return 0.0


def _extract_sample_clip(video_path: str, start_time: float, duration: float = 3.0) -> str:
    """从视频中提取一小段样本(用于 VL 分析)"""
    sample_dir = os.path.join(_PROJECT_DIR, "workspace", "samples")
    os.makedirs(sample_dir, exist_ok=True)

    base = hashlib.md5(f"{video_path}_{start_time}".encode()).hexdigest()[:12]
    out_path = os.path.join(sample_dir, f"sample_{base}.mp4")

    if os.path.exists(out_path):
        return out_path  # 已缓存

    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(start_time), "-i", video_path,
             "-t", str(duration),
             "-vf", "scale=1280:720",
             "-c:v", "libx264", "-crf", "28",
             "-an",  # 无声样本，只看画面
             out_path],
            capture_output=True, timeout=120,
        )
        if os.path.exists(out_path) and os.path.getsize(out_path) > 1024:
            return out_path
    except Exception as e:
        log.warning("提取样本失败 %s @%.1f: %s", video_path, start_time, e)

    return ""


def _extract_frame_base64(video_path: str, time_pos: float) -> str:
    """从视频指定时间点提取一帧,返回 data:image/png;base64,...."""
    import hashlib, base64
    tmp_dir = os.path.join(_PROJECT_DIR, "_tmp_render")
    os.makedirs(tmp_dir, exist_ok=True)
    tag = hashlib.md5(f"{video_path}:{time_pos}".encode()).hexdigest()[:12]
    tmp_png = os.path.join(tmp_dir, f"prospect_frame_{tag}.png")

    r = subprocess.run([
        "ffmpeg", "-y", "-ss", str(time_pos), "-i", video_path,
        "-vframes", "1",
        "-vf", "scale=-2:460",
        tmp_png,
    ], capture_output=True, timeout=30, check=False)

    if r.returncode != 0 or not os.path.exists(tmp_png):
        return ""

    with open(tmp_png, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    try:
        os.remove(tmp_png)
    except Exception:
        pass

    return f"data:image/png;base64,{b64}"


def _call_vision_for_prospect(sample_paths: list[str], duration_total: float) -> dict:
    """把样本发给 VL 模型，问素材类型"""
    from dashscope import MultiModalConversation

    if not sample_paths:
        return {"error": "无法提取样本"}

    # 构造消息：从每个样本视频取 2 帧
    content = []
    for sp in sample_paths:
        if not sp:
            continue
        try:
            # 从样本视频中取 2 帧（开头和后段）
            for offset in [0.5, 2.0]:
                frame = _extract_frame_base64(sp, offset)
                if frame:
                    content.append({"image": frame})
        except Exception:
            pass

    if not content:
        return {"error": "样本帧提取失败"}

    prompt = (
        "你是素材勘探员。给你几段这个视频的样本片段(开头/中间/结尾)，请快速判断：\n"
        "1. 素材类型(单选)：口播/打斗/风景/教学/日常对话/混剪/其他\n"
        "2. 主要内容是什么？(一句话)\n"
        "3. 画面质量如何？(好/中/差)\n"
        "4. 这个素材有没有值得剪辑的价值？(高/中/低) 为什么？\n"
        "5. 如果有多段样本，它们的场景是否一致？还是不同场景的混剪？\n"
        "6. 建议怎么做？(不切直接听全文/按场景切分再分析/废弃/不确定需完整听)\n"
        f"素材总时长: {duration_total:.0f}秒\n"
        "用中文回答，简洁。"
    )

    messages = [
        {"role": "system", "content": [{"text": "你是 ClipMind 的素材勘探员，快速判断素材类型和价值。"}]},
        {"role": "user", "content": [{"text": prompt}] + content},
    ]

    from director.config import get_model_for_role
    vision_model = get_model_for_role("vision")
    try:
        resp = MultiModalConversation.call(
            model=vision_model,
            messages=messages,
            max_tokens=500,
        )
        text = ""
        if hasattr(resp, "output") and resp.output:
            choices = resp.output.get("choices", [])
            if choices and "message" in choices[0]:
                msg = choices[0]["message"]
                for c in msg.get("content", []):
                    if "text" in c:
                        text += c["text"]
        if not text:
            text = str(resp)
        return {"report": text, "samples_used": len(sample_paths)}
    except Exception as e:
        log.warning("VL 勘探调用失败: %s", e)
        return {"error": f"勘探调用失败: {e}"}


@tool(
    name="prospect_material",
    description="""勘探素材——快速判断素材的类型和价值.

不做完整分析,只采样看几眼,回答:这是什么类型的素材?有没有价值?下一步怎么做?

什么时候用:
  - 刚拿到素材,不了解它是什么内容时
  - 想知道要不要对这个素材做深度分析
  - 素材太多,需要先筛选哪些值得做

什么时候千万别用:
  - 已经知道素材类型(无须再探)
  - 需要完整内容分析(应该用定向分析工具)
  - 很短(<10秒)的素材,直接 watch_video 看完整内容""",
    phase="all",
    category="material",
    tags=["prospect", "analyze", "material", "sample"],
    group="素材勘探",
)
def prospect_material(video_path: str) -> str:
    """勘探一个素材,快速判断类型和价值.

    Args:
        video_path: 素材文件的绝对路径

    Returns:
        勘探报告(JSON)
    """
    if not os.path.exists(video_path):
        return json.dumps({"error": f"文件不存在: {video_path}"}, ensure_ascii=False)

    filename = os.path.basename(video_path)
    duration = _get_duration(video_path)

    if duration <= 0:
        return json.dumps({"error": "无法获取视频时长"}, ensure_ascii=False)

    # 极短视频(<10秒),不需要采样,直接看完整
    if duration < 10:
        from director.tools.watch import watch_video
        result = watch_video(video_path, fps=2.0)
        report = f"【素材 {filename}】短素材({duration:.0f}秒)，已完整看完:\n{result[:500]}"
        return json.dumps({
            "filename": filename,
            "duration": duration,
            "type": "短素材(已完整看完)",
            "report": report,
        }, ensure_ascii=False)

    # 正常素材:采样3个点(开头20%, 中间50%, 结尾80%)
    sample_points = [
        max(5, duration * 0.2),
        duration * 0.5,
        min(duration - 5, duration * 0.8),
    ]

    sample_paths = []
    for pt in sample_points:
        sp = _extract_sample_clip(video_path, pt)
        if sp:
            sample_paths.append(sp)

    if not sample_paths:
        # 采样失败,降级用 watch_video 低帧率版
        from director.tools.watch import watch_video
        result = watch_video(video_path, fps=0.3, custom_prompt="快速浏览这个素材,判断类型和价值。")
        return json.dumps({
            "filename": filename,
            "duration": duration,
            "type": "降级(采样失败,已低帧率看完)",
            "report": result[:800],
        }, ensure_ascii=False)

    vision_result = _call_vision_for_prospect(sample_paths, duration)

    result = {
        "filename": filename,
        "duration": round(duration, 1),
        **vision_result,
    }

    # 清理临时样本文件
    for sp in sample_paths:
        try:
            os.remove(sp)
        except Exception:
            pass

    return json.dumps(result, ensure_ascii=False, indent=2)
