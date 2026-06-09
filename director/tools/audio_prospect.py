"""
语音勘探工具 — AI 先"听"素材，不等压缩完成
======================================
不等画面压缩完成,直接用原始素材的音频轨道采样听内容.

回答三个问题:
  1. 这段素材说了什么/有什么声音？
  2. 有没有价值？
  3. 下一步建议怎么做？

与 prospect_material 的区别:
  - prospect_material: 看画面(需要等压缩完)
  - audio_prospect: 听声音(原始文件即可,立即可用)
"""
import os, json, subprocess, hashlib, base64
from director.registry import tool
from director.logging_config import get_logger

log = get_logger("director.tools.audio_prospect")

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


def _extract_audio_sample(video_path: str, start_time: float, duration: float = 10.0) -> str:
    """从视频中提取一小段音频样本(用于语音分析)"""
    sample_dir = os.path.join(_PROJECT_DIR, "workspace", "audio_samples")
    os.makedirs(sample_dir, exist_ok=True)

    base = hashlib.md5(f"{video_path}_{start_time}".encode()).hexdigest()[:12]
    out_path = os.path.join(sample_dir, f"audio_sample_{base}.wav")

    if os.path.exists(out_path) and os.path.getsize(out_path) > 1024:
        return out_path  # 已缓存

    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(start_time), "-i", video_path,
             "-t", str(duration),
             "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
             out_path],
            capture_output=True, timeout=60,
        )
        if os.path.exists(out_path) and os.path.getsize(out_path) > 1024:
            return out_path
    except Exception as e:
        log.warning("提取音频样本失败 %s @%.1f: %s", video_path, start_time, e)

    return ""


def _call_audio_analysis(audio_paths: list[str], duration_total: float) -> dict:
    """把音频样本发给配置的语音模型分析内容（用 get_model_for_role("audio") 获取模型名）"""
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        try:
            from director.config import get_api_key
            api_key = get_api_key()
        except Exception:
            pass
    if not api_key:
        return {"error": "API Key 未配置"}

    if not audio_paths:
        return {"error": "没有可用的音频样本"}

    from director.config import get_model_for_role
    audio_model = get_model_for_role("audio")

    import requests
    _base = os.environ.get("DASHSCOPE_API_BASE", "https://dashscope.aliyuncs.com")
    api_url = f"{_base}/api/v1/services/aigc/multimodal-generation/generation"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    # 构造多段音频内容
    content_parts = []
    for i, ap in enumerate(audio_paths):
        if not ap or not os.path.exists(ap):
            continue
        with open(ap, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        labels = ["开头部分", "中间部分", "结尾部分"]
        label = labels[i] if i < len(labels) else f"第{i+1}段"
        content_parts.append({"audio": f"data:audio/wav;base64,{b64}"})
        content_parts.append({"text": f"（这是{label}的音频，约10秒）"})

    if not content_parts:
        return {"error": "音频样本读取失败"}

    prompt = (
        "你是 ClipMind 的语音勘探员。给你三段这段视频的音频样本（开头/中间/结尾），请快速判断：\n"
        "1. 音频类型(单选)：纯语音/语音+背景音/纯音乐/纯音效/无声/混合\n"
        "2. 如果有人说话，说的是什么内容？(一句话概括)\n"
        "3. 说话者的语气/情绪是什么样的？(平静/激动/严肃/幽默/悲伤/愤怒/其他)\n"
        "4. 背景音类型：有BGM吗？有环境音吗？有特殊音效吗？\n"
        "5. 这个素材的音频有没有剪辑价值？(高/中/低) 为什么？\n"
        "6. 如果三段样本的音频差异很大，说明是不同场景混剪\n"
        f"素材总时长: {duration_total:.0f}秒\n"
        "用中文回答，简洁。"
    )

    messages = [{
        "role": "user",
        "content": [{"text": prompt}] + content_parts,
    }]

    try:
        payload = {
            "model": audio_model,
            "input": {"messages": messages},
            "parameters": {"max_tokens": 500},
        }
        resp = requests.post(api_url, headers=headers, json=payload, timeout=120)
        data = resp.json()

        # 检查 DashScope 错误响应
        if "code" in data and "message" in data:
            return {"error": f"语音勘探不可用({data['code']}): {data['message'][:200]}"}

        # 解析正常响应
        choices = data.get("output", {}).get("choices", [])
        text = ""
        if choices:
            content_list = choices[0].get("message", {}).get("content", [])
            for c in content_list:
                if "text" in c:
                    text += c["text"]
        if not text:
            return {"error": "语音勘探无返回内容"}
        return {"report": text, "samples_used": len(audio_paths)}
    except Exception as e:
        log.warning("语音勘探API调用失败: %s", e)
        return {"error": f"语音勘探失败: {e}"}


@tool(
    name="audio_prospect",
    description="""语音勘探素材——不等压缩完成,直接用原始素材听声音判断类型和价值.

什么时候用:
  - 刚拿到素材,想快速知道内容
  - 素材正在压缩中,不想等
  - 想知道素材有没有语音、说了什么、情绪如何

什么时候别用:
  - 已经知道素材内容(无须再探)
  - 纯静音素材(探了也没用)
  - 已经做完 prospect_material(画面勘探更全面)""",
    phase="all",
    category="material",
    tags=["prospect", "audio", "material", "sample"],
    group="素材勘探",
)
def audio_prospect(video_path: str) -> str:
    """语音勘探素材——不等压缩,直接用原始音频采样判断.

    Args:
        video_path: 素材文件的绝对路径(原始文件即可,不需要等压缩)

    Returns:
        语音勘探报告(JSON)
    """
    if not os.path.exists(video_path):
        return json.dumps({"error": f"文件不存在: {video_path}"}, ensure_ascii=False)

    filename = os.path.basename(video_path)
    duration = _get_duration(video_path)

    if duration <= 0:
        return json.dumps({"error": "无法获取视频时长"}, ensure_ascii=False)

    # 极短视频(<10秒),只采一个点
    if duration < 10:
        sample_points = [max(1, duration * 0.5)]
    else:
        # 采样3个点(开头5%, 50%, 结尾-5%)
        sample_points = [
            max(2, duration * 0.05),
            duration * 0.5,
            min(duration - 5, duration * 0.95),
        ]

    sample_paths = []
    for pt in sample_points:
        sp = _extract_audio_sample(video_path, pt, duration=10.0)
        if sp:
            sample_paths.append(sp)

    if not sample_paths:
        return json.dumps({
            "filename": filename,
            "duration": round(duration, 1),
            "error": "音频样本提取失败",
        }, ensure_ascii=False)

    analysis = _call_audio_analysis(sample_paths, duration)

    result = {
        "filename": filename,
        "duration": round(duration, 1),
        **analysis,
    }

    # 清理临时文件
    for sp in sample_paths:
        try:
            os.remove(sp)
        except Exception:
            pass

    return json.dumps(result, ensure_ascii=False, indent=2)
