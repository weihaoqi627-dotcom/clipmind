"""
预览检查工具
=============
取代"调参数 -> 渲染 -> 看效果 -> 再调"的旧模式.

check_edit:AI 改完某个参数后,用它检查自己改得对不对.
AI 自己决定要看哪段时间——刚调了哪个镜头就看那个镜头.
不走完整渲染管线,轻量检查,不满意就再调再查.

工作流程:
  edit_tool(...)   ->  AI 调参数
  check_edit(...)  ->  AI 看效果,VL 模型告诉它对不对
  edit_tool(...)   ->  不满意再调
  check_edit(...)  ->  再看
  render_final     ->  满意了,最终导出

实现分层:
  顶层:check_edit(Director 可调用的工具函数)
  中层:Canvas 录制(Electron 预览就绪后自动接入,最快)
  底层:FFmpeg 快速提取(回退方案)
"""
import json, os, base64
from pathlib import Path
from typing import Optional

from director.registry import tool

_PROJECT_DIR = Path(__file__).parent.parent.parent


# ═══════════════════════════════════════════════════════════
#  后端注入
# ═══════════════════════════════════════════════════════════
# UI(Canvas 预览)就绪后,director_runner 会注入真正的后端.
# 在此之前所有调用走回退方案.

_preview_clip_backend: Optional[callable] = None


def set_preview_backend(fn: callable):
    """
    注入 Canvas 录制后端.
    fn(start_time, end_time) -> bytes | None
    bytes: WebM 视频数据
    None: 不可用,走回退方案
    """
    global _preview_clip_backend
    _preview_clip_backend = fn


def _get_backend():
    return _preview_clip_backend


# ═══════════════════════════════════════════════════════════
#  check_edit 工具
# ═══════════════════════════════════════════════════════════

@tool(
    name="check_edit",
    description=(
        "检查你自己刚改的剪辑效果."
        "每调完一个参数(调色/转场/字幕/动效/BGM)后调用它,"
        "VL模型会看画面告诉你效果好不好."
        "不需要渲染整个视频,只看你指定的那一段."
        "不满意就再调再检查,满意了再渲染."
    ),
    phase="edit",
    category="preview",
    tags=["check", "preview", "review"],
    group="预览与质检",
)
def check_edit(
    video_path: str,
    question: str = "",
    start_time: Optional[float] = None,
    end_time: Optional[float] = None,
) -> str:
    """
    检查你刚改完的剪辑效果.

    Args:
        video_path: 源视频路径(必填)
        question: 你想确认什么(选填).
            比如 "转场衔接自然吗" "调色后画面偏暗吗" "字幕对齐了吗"
            不填的话 VL 模型会自己判断有什么问题.
        start_time: 开始时间秒(选填).不填则自动选择.
        end_time: 结束时间秒(选填).不填则自动选择.

    Returns:
        分析结果
    """
    # ── 参数校验 ──
    if not os.path.exists(video_path):
        return f"[错误] 文件不存在: {video_path}"

    # 确定时间范围
    resolved_start, resolved_end, duration = _resolve_time_range(
        video_path, start_time, end_time
    )

    if duration <= 0:
        return f"[错误] 无效时间范围: {resolved_start:.1f}-{resolved_end:.1f}s"

    # ── 第1优先:Canvas 预览后端(Electron 就绪后)──
    backend = _get_backend()
    if backend is not None:
        try:
            video_bytes = backend(video_path, resolved_start, resolved_end)
            if video_bytes and len(video_bytes) > 100:
                return _call_vl_analysis(
                    video_bytes, resolved_start, resolved_end,
                    question, source="canvas"
                )
        except Exception:
            pass  # 静默回退

    # ── 第2优先:screen_clip 分析(已有片段缓存时)──
    from director.tools.analyze import _get_segments_cached
    segments = _get_segments_cached(video_path)
    if segments:
        covering = [
            s for s in segments
            if s["start"] < resolved_end and s["end"] > resolved_start
        ]
        if covering:
            from director.tools.analyze import screen_clip as sc
            results = []
            for clip in covering:
                r = sc(video_path, clip["id"], duration)
                results.append(f"[片段 {clip['id']} {clip['start']:.0f}s-{clip['end']:.0f}s]\n{r}")
            return "\n---\n".join(results)

    # ── 第3优先:FFmpeg 快速提取(兜底)──
    return _extract_and_analyze(video_path, resolved_start, resolved_end, question)


def _resolve_time_range(
    video_path: str,
    start: Optional[float],
    end: Optional[float],
) -> tuple[float, float, float]:
    """确定实际要检查的时间范围——AI 说了算,不设硬限制"""
    # 防御:LLM 可能传字符串而非 float
    if start is not None:
        start = float(start)
    if end is not None:
        end = float(end)
    total = _get_duration(video_path)

    # 都填了 -> 完全尊重 AI 的选择
    if start is not None and end is not None:
        if end <= start:
            return start, start + 1, 1  # 防 0 或负数
        return start, end, end - start

    # 只填了一个 -> 补到自然结尾或开头
    if start is not None:
        end = total if total > start else start + 30
        return start, end, end - start
    if end is not None:
        start = 0.0
        return start, end, end - start

    # 都没填 -> 取第一个片段
    if total <= 0:
        return 0.0, 30.0, 30.0
    segments = _get_segments(video_path)
    if segments:
        s = segments[0]
        return s["start"], s["end"], s["end"] - s["start"]
    return 0.0, total, total


def _get_duration(video_path: str) -> float:
    """用 ffprobe 获取视频时长"""
    import subprocess, re
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration", "-of", "csv=p=0", video_path],
            capture_output=True, timeout=15, text=True
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def _get_segments(video_path: str) -> list:
    """获取片段列表(带缓存)"""
    from director.tools.analyze import _get_segments_cached
    try:
        return _get_segments_cached(video_path)
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════
#  VL 分析调用
# ═══════════════════════════════════════════════════════════

def _get_api_key() -> str:
    """获取 API key"""
    key = os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    try:
        from director.config import get_api_key as _cfg_get_key
        return _cfg_get_key()
    except Exception:
        return ""


def _call_vl_analysis(
    video_bytes: bytes,
    start_time: float,
    end_time: float,
    question: str,
    source: str = "canvas",
) -> str:
    """发送视频片段到 VL 模型,返回分析结果"""
    api_key = _get_api_key()
    if not api_key:
        return "[错误] API Key 未配置"

    b64 = base64.b64encode(video_bytes).decode("utf-8")

    from openai import OpenAI
    import httpx
    client = OpenAI(
        api_key=api_key,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        max_retries=1,
        timeout=httpx.Timeout(180.0),
    )

    prompt = "分析这段视频的剪辑效果."
    if question:
        prompt += f"重点关注:{question}."
    prompt += (
        "\n按以下格式输出:\n"
        "效果评价: 好/一般/有问题\n"
        "具体问题: 描述发现的问题(没有就说无)\n"
        "建议调整: 建议做什么(没有就说无需调整)"
    )

    try:
        resp = client.chat.completions.create(
            model="qwen3.6-plus",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "video_url",
                        "video_url": {"url": f"data:video/webm;base64,{b64}"},
                        "fps": 2,
                    },
                ]
            }],
            max_tokens=20000,
        )
        analysis = resp.choices[0].message.content or "(无分析结果)"
    except Exception as e:
        analysis = f"VL分析失败: {e}"

    result = (
        f"检查范围: {start_time:.1f}s - {end_time:.1f}s\n"
        f"来源: {source}\n"
        f"分析结果:\n{analysis}"
    )
    return result


# ═══════════════════════════════════════════════════════════
#  FFmpeg 快速提取(兜底方案)
# ═══════════════════════════════════════════════════════════

def _extract_and_analyze(
    video_path: str,
    start_time: float,
    end_time: float,
    question: str,
) -> str:
    """FFmpeg 快速提取指定范围,crf 35 + ultrafast,不做截断"""
    import subprocess, tempfile

    duration = end_time - start_time
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".mp4")
    os.close(tmp_fd)

    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-ss", str(start_time), "-i", video_path,
            "-t", str(duration),
            "-vf", "scale=-2:480",
            "-c:v", "libx264", "-crf", "35", "-preset", "ultrafast",
            "-an",
            tmp_path,
        ], capture_output=True, timeout=300, check=False)

        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            return f"无法提取 {start_time:.1f}s-{end_time:.1f}s 的画面"

        with open(tmp_path, "rb") as f:
            video_bytes = f.read()

        return _call_vl_analysis(
            video_bytes, start_time, end_time, question,
            source="ffmpeg_fallback",
        )
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
