"""
成品审查工具
=============
Director 完成渲染后,用 VL 模型看一遍成品画面质量.
不检查创意层面(那是用户的事),只检查技术质量:
- 画面跳帧/黑屏
- 转场卡顿
- 字幕错位/不同步
- 音频不同步
- 色彩异常
"""
import json, os, subprocess, tempfile, base64
from pathlib import Path

_PROJECT_DIR = Path(__file__).parent.parent.parent


def _get_api_key() -> str:
    return os.environ.get("DASHSCOPE_API_KEY", "")


def _make_preview_clip(video_path: str) -> str:
    """
    用 ffmpeg 对输出视频做降质,生成 VL 可用的预览片段.
    限制:360p,2fps,crf 35,静音.大幅减少体积.
    返回临时文件路径.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.close()

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", "scale=640:360,fps=2",
        "-c:v", "libx264",
        "-crf", "35",
        "-preset", "ultrafast",
        "-an",  # 静音
        "-movflags", "+faststart",
        tmp.name,
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=120, check=True)
        return tmp.name
    except Exception:
        # 回退:直接读原文件(小视频可能不大)
        return video_path


def _call_vl_review(video_path: str, task: str, tool_issues: list) -> dict:
    """发送输出视频到 VL 模型做质量审查"""
    api_key = _get_api_key()
    if not api_key:
        return {"error": "API Key 未配置"}

    # 读视频文件并编码
    try:
        with open(video_path, "rb") as f:
            video_bytes = f.read()
    except Exception as e:
        return {"error": f"无法读取视频: {e}"}

    if len(video_bytes) > 15 * 1024 * 1024:  # 15MB 限制
        return {"error": f"视频过大 ({len(video_bytes) / 1024 / 1024:.1f}MB),无法发送 VL 审查"}

    b64 = base64.b64encode(video_bytes).decode("utf-8")

    # 构建审查 prompt
    issues_text = ""
    if tool_issues:
        issues_lines = [f"- {i['tool']}: {i['error'][:120]}" for i in tool_issues[:10]]
        issues_text = "\n编辑过程中以下工具报错:\n" + "\n".join(issues_lines)

    prompt = f"""你是视频质量审查员.请审查这段视频的技术质量.

原始任务要求: {task[:500]}
{issues_text}

请从以下角度审查:
1. 画面质量:是否有跳帧,黑屏,花屏,色彩异常
2. 转场质量:转场是否卡顿,不到位
3. 字幕质量:字幕是否错位,不同步
4. 音频质量:如果有音频,是否有不同步

按 JSON 格式输出(只输出 JSON,不要其他内容):
{{
  "overall": "good/ok/poor",
  "issues": [
    {{"type": "video/audio/subtitle/transition", "severity": "high/medium/low", "description": "具体问题"}}
  ],
  "summary": "一句话总结"
}}"""

    try:
        from openai import OpenAI
        import httpx
        client = OpenAI(
            api_key=api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            max_retries=1,
            timeout=httpx.Timeout(180.0),
        )

        resp = client.chat.completions.create(
            model=os.environ.get("LLM_MODEL", "qwen3.6-plus"),
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "video_url",
                        "video_url": {"url": f"data:video/mp4;base64,{b64}"},
                        "fps": 2,
                    },
                ]
            }],
            max_tokens=4096,
        )
        content = resp.choices[0].message.content or ""

        # 提取 JSON
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
        return json.loads(content)

    except json.JSONDecodeError:
        return {"error": "VL 审查结果解析失败", "raw": content[:500]}
    except Exception as e:
        return {"error": f"VL 审查失败: {type(e).__name__}: {str(e)}"}


def review_output(video_path: str, task: str, tool_issues: list = None) -> dict:
    """
    审查渲染完成的输出视频.

    Args:
        video_path: 输出视频路径
        task: 原始任务描述
        tool_issues: 编辑过程中工具报错列表

    Returns:
        {
            "overall": "good" | "ok" | "poor" | "error",
            "issues": [...],
            "tool_issues": [...],
            "summary": "...",
        }
    """
    if not video_path or not os.path.exists(video_path):
        return {"overall": "error", "issues": [], "tool_issues": tool_issues or [],
                "summary": f"输出文件不存在: {video_path}"}

    tool_issues = tool_issues or []

    # Step 1: 降质生成预览
    preview_path = _make_preview_clip(video_path)
    need_cleanup = preview_path != video_path

    # Step 2: VL 审查
    try:
        result = _call_vl_review(preview_path, task, tool_issues)
    finally:
        if need_cleanup and os.path.exists(preview_path):
            try:
                os.unlink(preview_path)
            except Exception:
                pass

    # Step 3: 合并工具问题
    if isinstance(result, dict):
        result["tool_issues"] = tool_issues
        if "overall" not in result:
            result["overall"] = "ok"
        if "issues" not in result:
            result["issues"] = []
    else:
        result = {"overall": "error", "issues": [], "tool_issues": tool_issues,
                  "summary": str(result)}

    return result
