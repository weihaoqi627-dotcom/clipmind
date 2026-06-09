"""
视频降噪工具 — 使用 ffmpeg 内置滤镜
==============================
提供两种降噪方案:
  - nlmeans(非局部均值降噪,高质量但慢)
  - hqdn3d(快速高通降噪,适合轻度噪声)

零额外依赖,仅需 ffmpeg.
"""
import json, os, base64, subprocess, re
from pathlib import Path

from director.registry import tool

_PROJECT_DIR = Path(__file__).parent.parent.parent


# ═══════════════════════════════════════════════════════════
#  主函数:视频降噪
# ═══════════════════════════════════════════════════════════

@tool(
    name="denoise_video",
    description="视频降噪处理.支持 nlmeans(非局部均值降噪,高质量但慢)和 hqdn3d(快速高通降噪,适合轻度噪声).自动检测 NVENC 编码器加速输出.保留原音频.",
    phase="edit",
    category="denoise",
    tags=["denoise", "nlmeans", "hqdn3d"],
    group="降噪",
)
def denoise_video(
    video_path: str,
    method: str = "nlmeans",
    strength: float = 1.0,
    temporal_strength: int = 2,
    output_path: str = "",
    draft_id: str = "",
    clip_id: int = 0,
) -> str:
    """
    视频降噪处理.支持 nlmeans(高质量)或 hqdn3d(快速).

    Args:
        video_path: 输入视频路径
        method: "nlmeans"(高质量,默认)或 "hqdn3d"(快速)
        strength: 降噪强度,范围 0~5,默认 1.0
        temporal_strength: 时域降噪强度,范围 0~10,默认 2(仅 hqdn3d 使用)
        output_path: 输出路径(可选,默认在源文件旁生成)

    Returns:
        结果信息字符串
    """
    # ── 输入验证 ──
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"

    valid_methods = {"nlmeans", "hqdn3d"}
    if method not in valid_methods:
        return f"不支持的降噪方法: {method},可选: {', '.join(valid_methods)}"

    strength = max(0.0, min(5.0, strength))
    temporal_strength = max(0, min(10, temporal_strength))

    # ── 输出路径 ──
    if not output_path:
        base, ext = os.path.splitext(video_path)
        output_path = f"{base}_denoised{ext}"
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # ── NVENC 检测 ──
    nvenc_ok = _has_nvenc()
    vcodec = "h264_nvenc" if nvenc_ok else "libx264"
    vparams = ["-qp", "18", "-preset", "p4"] if nvenc_ok else ["-crf", "18", "-preset", "medium"]

    # ── 构建 filter 字符串 ──
    if method == "nlmeans":
        rc = int(strength * 6)
        s = int(strength * 4)
        vf_str = f"nlmeans=p=7:rc={rc}:s={s}"
    else:  # hqdn3d
        luma_spatial = strength * 3
        chroma_spatial = strength * 2
        luma_tmp = temporal_strength * 2
        vf_str = f"hqdn3d=luma_spatial={luma_spatial:.1f}:chroma_spatial={chroma_spatial:.1f}:luma_tmp={luma_tmp}"

    # ── 音频处理 ──
    has_audio = _detect_audio_stream(video_path)

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", vf_str,
        "-c:v", vcodec, *vparams,
    ]

    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-an"]

    cmd += ["-movflags", "+faststart", output_path]

    result = subprocess.run(cmd, capture_output=True, timeout=3600, check=False)

    if result.returncode != 0 and (
        not os.path.exists(output_path) or os.path.getsize(output_path) == 0
    ):
        err = result.stderr.decode("utf-8", errors="replace")[-300:]
        return f"降噪处理失败: {err}"

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        return "降噪处理失败:输出文件为空"

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    method_label = "NLMeans" if method == "nlmeans" else "HQDN3D"

    if draft_id:
        from director.draft import _write_to_draft
        _write_to_draft(draft_id, clip_id, "denoise", {"type": method, "strength": strength}, label="降噪完成")

    return (
        f"降噪完成 (方法: {method_label}, 强度: {strength}, 时域强度: {temporal_strength})\n"
        f"输出: {output_path} ({size_mb:.1f}MB)"
    )


# ═══════════════════════════════════════════════════════════
#  预览:原图 vs 降噪 并排对比
# ═══════════════════════════════════════════════════════════

@tool(
    name="compare_denoise",
    description="在指定时间点截取原图与降噪后的并排对比图.返回 base64 PNG 图像(data:image/png;base64,...),可直接输入给 VL 模型评估降噪效果.",
    phase="edit",
    category="denoise",
    tags=["denoise", "preview", "compare"],
    group="降噪",
)
def compare_denoise(
    video_path: str,
    method: str = "nlmeans",
    strength: float = 1.0,
    time_pos: float = 1.0,
) -> str:
    """
    在指定时间点截图,原图 vs 降噪后并排对比.
    返回 data:image/png;base64,... 供 VL 模型评估.

    Args:
        video_path: 输入视频路径
        method: "nlmeans"(默认)或 "hqdn3d"
        strength: 降噪强度,范围 0~5,默认 1.0
        time_pos: 采样时间点(秒),默认 1.0

    Returns:
        base64 PNG 图像数据,或错误信息
    """
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"

    tmp_dir = _PROJECT_DIR / "_tmp_render"
    os.makedirs(tmp_dir, exist_ok=True)

    # 提取原始帧
    orig_frame = str(tmp_dir / "denoise_orig_frame.png")
    extract_cmd = [
        "ffmpeg", "-y",
        "-ss", str(time_pos),
        "-i", video_path,
        "-vframes", "1",
        "-q:v", "2",
        orig_frame,
    ]
    r1 = subprocess.run(extract_cmd, capture_output=True, timeout=30, check=False)
    if r1.returncode != 0 or not os.path.exists(orig_frame):
        return "提取原始帧失败"

    # 提取降噪后的帧
    strength = max(0.0, min(5.0, strength))
    if method == "nlmeans":
        rc = int(strength * 6)
        s = int(strength * 4)
        vf_str = f"nlmeans=p=7:rc={rc}:s={s}"
    else:
        ls = strength * 3
        cs = strength * 2
        vf_str = f"hqdn3d=luma_spatial={ls:.1f}:chroma_spatial={cs:.1f}:luma_tmp=4"

    denoised_frame = str(tmp_dir / "denoise_denoised_frame.png")
    extract_cmd2 = [
        "ffmpeg", "-y",
        "-ss", str(time_pos),
        "-i", video_path,
        "-vf", vf_str,
        "-vframes", "1",
        "-q:v", "2",
        denoised_frame,
    ]
    r2 = subprocess.run(extract_cmd2, capture_output=True, timeout=120, check=False)
    if r2.returncode != 0 or not os.path.exists(denoised_frame):
        return "降噪帧提取失败"

    # 获取尺寸
    w1, h1 = _get_video_dimensions(orig_frame)
    w2, h2 = _get_video_dimensions(denoised_frame)
    if not w1 or not h1:
        w1, h1 = 1920, 1080
    if not w2 or not h2:
        w2, h2 = w1, h1

    composite = str(tmp_dir / "denoise_preview_composite.png")

    if w1 == w2 and h1 == h2:
        composite_cmd = [
            "ffmpeg", "-y",
            "-i", orig_frame,
            "-i", denoised_frame,
            "-filter_complex", "[0:v][1:v]hstack=inputs=2[v]",
            "-map", "[v]",
            "-frames:v", "1",
            composite,
        ]
    else:
        composite_cmd = [
            "ffmpeg", "-y",
            "-i", orig_frame,
            "-i", denoised_frame,
            "-filter_complex",
            f"[1:v]scale={w1}:{h1}:flags=lanczos[s];[0:v][s]hstack=inputs=2[v]",
            "-map", "[v]",
            "-frames:v", "1",
            composite,
        ]

    subprocess.run(composite_cmd, capture_output=True, timeout=30, check=False)

    # 读取合成图
    if os.path.exists(composite) and os.path.getsize(composite) > 0:
        try:
            with open(composite, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            _cleanup_tmp([orig_frame, denoised_frame, composite])
            return b64
        except Exception:
            pass

    # fallback: 返回原帧
    try:
        with open(orig_frame, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        _cleanup_tmp([orig_frame, denoised_frame, composite])
        return b64
    except Exception:
        _cleanup_tmp([orig_frame, denoised_frame, composite])
        return "生成对比图像失败"


# ═══════════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════════

def _has_nvenc() -> bool:
    """检查 NVENC 编码器是否可用"""
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True, timeout=10
    )
    return "h264_nvenc" in (r.stdout + r.stderr).decode("utf-8", errors="replace")


def _parse_json(data) -> any:
    """安全解析 JSON 字符串或直接返回已解析对象"""
    if not data:
        return None
    if isinstance(data, str):
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return None
    return data


def _get_video_dimensions(video_path: str) -> tuple:
    """获取视频/图像分辨率 (width, height)"""
    r = subprocess.run(
        ["ffmpeg", "-i", video_path],
        capture_output=True, timeout=30
    )
    output = (r.stdout + r.stderr).decode("utf-8", errors="replace")
    m = re.search(r",\s*(\d{3,})x(\d{3,})", output)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def _cleanup_tmp(files: list):
    """清理临时文件列表"""
    for f in files:
        if isinstance(f, str) and os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass


def _detect_audio_stream(video_path: str) -> bool:
    """用 ffmpeg -i 检测视频是否有音频流(兼容 Windows ffprobe 7.1 bug)"""
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", video_path],
            capture_output=True, timeout=30,
        )
        out = (r.stdout + r.stderr).decode("utf-8", errors="replace")
        return re.search(r"Stream.*Audio", out) is not None
    except Exception:
        return False


# 工具已通过 @tool 装饰器自动注册到 Registry
