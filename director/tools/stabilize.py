"""
视频防抖工具 — 稳定化处理
========================
提供两种防抖方案:
  - vidstab(两阶段分析+变换,更精确)
  - deshake(单通道快速处理)

所有工具返回字符串,方便 AI 阅读理解.
"""
import json, os, base64, subprocess, re, math
from pathlib import Path

from director.registry import tool

_PROJECT_DIR = Path(__file__).parent.parent.parent


# ═══════════════════════════════════════════════════════════
#  主函数:视频防抖
# ═══════════════════════════════════════════════════════════

@tool(
    name="stabilize_video",
    description="视频防抖.支持 vidstab(两阶段高精度,默认)和 deshake(单通道快速).vidstab 两遍分析:vidstabdetect + vidstabtransform.crop 参数控制黑边处理:keep(保持原尺寸微放),fill(自动填充),black(保留黑边).smoothing 控制平滑强度(3-60,默认10),越高越平滑但可能引入延迟.",
    phase="edit",
    category="stabilize",
    tags=["stabilize", "vidstab", "deshake"],
    group="遮罩与稳定",
)
def stabilize_video(
    video_path: str,
    method: str = "vidstab",
    smoothing: int = 10,
    crop: str = "keep",
    output_path: str = "",
    draft_id: str = "",
    clip_id: int = 0,
) -> str:
    """
    视频防抖.支持两种方法:
      - vidstab(默认):两阶段 vidstabdetect + vidstabtransform,精度高
      - deshake:单通道 deshake filter,速度快

    Args:
        video_path: 输入视频路径
        method: "vidstab"(精度高)或 "deshake"(速度快)
        smoothing: vidstab 平滑帧数,范围 5-30,默认 10
        crop: "keep"(保持原尺寸,轻微放大去黑边),"fill"(自动填充),"black"(显示黑边)
        output_path: 输出路径(可选)

    Returns:
        结果信息字符串
    """
    # ── 输入验证 ──
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"

    valid_methods = {"vidstab", "deshake"}
    if method not in valid_methods:
        return f"不支持的防抖方法: {method},可选: {', '.join(valid_methods)}"

    # smoothing 范围检查
    smoothing = max(3, min(60, smoothing))
    if smoothing > 30:
        pass  # 高 smoothing 可能引入延迟,让 AI 自行决定

    valid_crops = {"keep", "fill", "black"}
    if crop not in valid_crops:
        crop = "keep"

    # ── 输出路径 ──
    if not output_path:
        base, ext = os.path.splitext(video_path)
        tag = "stabilized"
        if method == "deshake":
            tag = "deshake"
        output_path = f"{base}_{tag}{ext}"
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # ── 临时目录(不能用含冒号的路径,因为 ffmpeg filter 的 `:` 是选项分隔符)──
    tmp_dir = os.path.join(_PROJECT_DIR, "_tmp_render").replace("\\", "/")
    os.makedirs(tmp_dir, exist_ok=True)
    transforms_file_path = "transforms.trf"  # 只用文件名,配合 cwd=tmp_dir 使用

    # ── NVENC 检测 ──
    nvenc_ok = _check_nvenc()
    vcodec = "h264_nvenc" if nvenc_ok else "libx264"
    vparams = ["-qp", "18", "-preset", "p4"] if nvenc_ok else ["-crf", "18", "-preset", "medium"]

    # ════════════════════════════════════════════════════
    #  方法 1: vidstab(两阶段)
    # ════════════════════════════════════════════════════
    if method == "vidstab":
        # ── 第一阶段:运动分析 ──
        # shakiness: 1-10, 越高越激进(对运动越敏感)
        # accuracy: 1-15, 越高越精确但越慢
        # step: 像素步长,越小越平滑但越慢
        detect_cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", f"vidstabdetect=shakiness=5:accuracy=15:result={transforms_file_path}",
            "-f", "null",
            "-",
        ]
        detect_result = subprocess.run(
            detect_cmd, capture_output=True, timeout=600, check=False,
            cwd=tmp_dir,
        )
        if detect_result.returncode != 0:
            err = detect_result.stderr.decode("utf-8", errors="replace")[-300:]
            return f"vidstabdetect 分析失败: {err}"

        if not os.path.exists(os.path.join(tmp_dir, transforms_file_path)):
            return "vidstabdetect 未生成变换文件"

        # ── 第二阶段:应用稳定化变换 ──
        # 构建 vidstabtransform 参数
        transform_opts = [
            f"vidstabtransform=input={transforms_file_path}",
            f"smoothing={smoothing}",
            "interpol=linear",
        ]

        # 根据 crop 模式调整
        if crop == "keep":
            # 保持原始尺寸,略微放大(1.05x)来隐藏边框
            transform_opts.append("crop=keep")
            transform_opts.append("zoom=1.05")
        elif crop == "fill":
            # 自动填充:vidstabtransform 不支持 crop=fill,改用 keep+optzoom 实现
            transform_opts.append("crop=keep")
            transform_opts.append("optzoom=1")
            transform_opts.append("zoomspeed=0.1")
        else:  # "black"
            # 保留黑边,不做缩放
            transform_opts.append("crop=black")

        vf_str = ":".join(transform_opts)

        transform_cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", vf_str,
            "-c:v", vcodec, *vparams,
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            output_path,
        ]
        transform_result = subprocess.run(
            transform_cmd, capture_output=True, timeout=900, check=False,
            cwd=tmp_dir,
        )

        # ── 清理 transforms.trf ──
        _cleanup_tmp([os.path.join(tmp_dir, transforms_file_path)])

        if transform_result.returncode != 0 and (
            not os.path.exists(output_path) or os.path.getsize(output_path) == 0
        ):
            err = transform_result.stderr.decode("utf-8", errors="replace")[-300:]
            return f"vidstabtransform 应用失败: {err}"

    # ════════════════════════════════════════════════════
    #  方法 2: deshake(单通道快速处理)
    # ════════════════════════════════════════════════════
    else:  # method == "deshake"
        deshake_opts = [
            "rx=64",        # 最大水平位移(像素)
            "ry=64",        # 最大垂直位移(像素)
            "edge=blank",   # 边缘处理:保留黑边
            "blocksize=32", # 运动估计块大小
            "contrast=1.0", # 边缘对比度阈值
        ]
        vf_str = "deshake=" + ":".join(deshake_opts)

        # 如果 crop=keep,放大 1.05x 隐藏黑边
        if crop == "keep":
            vf_str = f"{vf_str},scale=iw*1.05:ih*1.05:flags=lanczos,crop=iw/1.05:ih/1.05"

        deshake_cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", vf_str,
            "-c:v", vcodec, *vparams,
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            output_path,
        ]
        deshake_result = subprocess.run(
            deshake_cmd, capture_output=True, timeout=900, check=False
        )

        if deshake_result.returncode != 0 and (
            not os.path.exists(output_path) or os.path.getsize(output_path) == 0
        ):
            err = deshake_result.stderr.decode("utf-8", errors="replace")[-300:]
            return f"deshake 处理失败: {err}"

    # ── 验证输出 ──
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        return "防抖处理失败:输出文件为空"

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    if draft_id:
        from director.draft import _write_to_draft
        _write_to_draft(draft_id, clip_id, "stabilize", {"method": method, "shakiness": smoothing}, label="稳定完成")
    return (
        f"✅ 防抖完成 (方法: {method}, 平滑: {smoothing}, 裁剪: {crop})\n"
        f"   输出: {output_path} ({size_mb:.1f}MB)"
    )


# ═══════════════════════════════════════════════════════════
#  预览:对比原片 vs 防抖效果
# ═══════════════════════════════════════════════════════════

@tool(
    name="preview_stabilization",
    description="在指定时间点预览防抖效果.返回 base64 PNG 并排对比图(原始 | 防抖),可直接输入给 VL 模型评估防抖效果.",
    phase="edit",
    category="stabilize",
    tags=["stabilize", "preview", "compare"],
    group="遮罩与稳定",
)
def preview_stabilization(
    video_path: str,
    method: str = "vidstab",
    time_pos: float = 1.0,
) -> str:
    """
    在指定时间点预览防抖效果(原片 vs 防抖并排对比).
    返回 base64 PNG,可直接输入给 VL 模型评估.

    Args:
        video_path: 输入视频路径
        method: "vidstab" 或 "deshake"
        time_pos: 采样时间点(秒),默认 1.0

    Returns:
        base64 PNG 图像数据,或错误信息
    """
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"

    tmp_dir = _PROJECT_DIR / "_tmp_render"
    os.makedirs(tmp_dir, exist_ok=True)

    # 提取原始帧
    orig_frame = str(tmp_dir / "stabilize_orig_frame.png")
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

    # 应用防抖并提取同位置帧
    transforms_file_path = "transforms_preview.trf"  # 只用文件名,避免冒号/中文导致 filter 解析问题
    stab_frame = os.path.join(str(tmp_dir), "stabilize_stab_frame.png")

    if method == "vidstab":
        # 第一阶段:分析
        detect_cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", f"vidstabdetect=shakiness=5:accuracy=15:result={transforms_file_path}",
            "-f", "null", "-",
        ]
        subprocess.run(detect_cmd, capture_output=True, timeout=600, check=False, cwd=str(tmp_dir))

        # 第二阶段:提取防抖后的帧
        vf_str = (
            f"vidstabtransform=input={transforms_file_path}:"
            f"smoothing=10:interpol=linear:crop=keep:zoom=1.05"
        )
        extract_cmd2 = [
            "ffmpeg", "-y",
            "-ss", str(time_pos),
            "-i", video_path,
            "-vf", vf_str,
            "-vframes", "1",
            "-q:v", "2",
            stab_frame,
        ]
        subprocess.run(extract_cmd2, capture_output=True, timeout=120, check=False, cwd=str(tmp_dir))

        # 清理
        _cleanup_tmp([os.path.join(str(tmp_dir), transforms_file_path)])

    else:  # deshake
        vf_str = "deshake=rx=64:ry=64:edge=blank:blocksize=32:contrast=1.0"
        extract_cmd2 = [
            "ffmpeg", "-y",
            "-ss", str(time_pos),
            "-i", video_path,
            "-vf", vf_str,
            "-vframes", "1",
            "-q:v", "2",
            stab_frame,
        ]
        subprocess.run(extract_cmd2, capture_output=True, timeout=120, check=False)

    if not os.path.exists(stab_frame):
        return "防抖帧提取失败"

    # 合成并排图(原始 | 防抖),用 hstack 或 overlay
    # 先获取两帧的尺寸,确保高度一致
    w1, h1 = _get_video_dimensions(orig_frame)
    w2, h2 = _get_video_dimensions(stab_frame)
    if not w1 or not h1:
        w1, h1 = 1920, 1080
    if not w2 or not h2:
        w2, h2 = w1, h1

    composite = str(tmp_dir / "stabilize_preview_composite.png")

    if w1 == w2 and h1 == h2:
        # 尺寸一致,用 hstack 左右并排
        composite_cmd = [
            "ffmpeg", "-y",
            "-i", orig_frame,
            "-i", stab_frame,
            "-filter_complex", "[0:v][1:v]hstack=inputs=2[v]",
            "-map", "[v]",
            "-frames:v", "1",
            composite,
        ]
    else:
        # 尺寸不一致,先缩放第二帧再并排
        composite_cmd = [
            "ffmpeg", "-y",
            "-i", orig_frame,
            "-i", stab_frame,
            "-filter_complex",
            f"[1:v]scale={w1}:{h1}:flags=lanczos[s];[0:v][s]hstack=inputs=2[v]",
            "-map", "[v]",
            "-frames:v", "1",
            composite,
        ]

    subprocess.run(composite_cmd, capture_output=True, timeout=30, check=False)

    # 优先用 composite,失败则 fallback 到拼接
    if not os.path.exists(composite) or os.path.getsize(composite) == 0:
        # fallback: 用原帧和防抖帧分别编码
        pass

    # 读取并转为 base64
    try:
        with open(composite, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
    except Exception as e:
        b64 = ""

    # 清理临时文件
    _cleanup_tmp([orig_frame, stab_frame, composite])

    if b64:
        return b64

    # fallback: 尝试读取单个帧
    try:
        with open(orig_frame, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        _cleanup_tmp([orig_frame, stab_frame, composite])
        return b64
    except:
        pass

    return "生成预览图像失败"


# ═══════════════════════════════════════════════════════════
#  抖动程度评估
# ═══════════════════════════════════════════════════════════

@tool(
    name="estimate_shakiness",
    description="快速分析视频抖动程度.运行 vidstabdetect 第一遍分析,解析变换数据返回总运动幅度,平均/最大帧间位移,抖动等级和处理建议.",
    phase="analyze",
    category="stabilize",
    tags=["analyze", "shakiness", "detect"],
    group="遮罩与稳定",
)
def estimate_shakiness(video_path: str) -> str:
    """
    快速分析视频抖动程度.
    通过帧间对比(PSNR)估算抖动等级,无需解析 TRF 文件.

    Returns:
        JSON 字符串,包含:
        {
            "avg_psnr": float,           # 平均帧间 PSNR(越低越抖)
            "psnr_variance": float,       # PSNR 方差(越高表示抖动越不均匀)
            "shakiness_level": str,       # "稳定" / "轻微抖动" / "中度抖动" / "严重抖动"
            "frames_analyzed": int,       # 分析帧数
            "recommendation": str         # 处理建议
        }
    """
    if not os.path.exists(video_path):
        return json.dumps({
            "error": f"文件不存在: {video_path}",
            "shakiness_level": "未知",
        }, ensure_ascii=False)

    tmp_dir = os.path.join(str(_PROJECT_DIR), "_tmp_render")
    os.makedirs(tmp_dir, exist_ok=True)

    # 用 ffmpeg 逐帧对比 PSNR:高 PSNR = 帧间差异小 = 稳定
    psnr_cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-filter_complex",
        "[0:v]fps=10,split=2[s1][s2];"
        "[s1]framestep=2,setpts=PTS*2[t1];"
        "[s2]setpts=PTS[t2];"
        "[t1][t2]psnr=stats_file=shake_psnr.log",
        "-an",
        "-f", "null",
        "-",
    ]
    psnr_result = subprocess.run(
        psnr_cmd, capture_output=True, timeout=600, check=False,
        cwd=tmp_dir,
    )

    if psnr_result.returncode != 0:
        err = psnr_result.stderr.decode("utf-8", errors="replace")[-200:]
        return json.dumps({
            "error": f"帧间分析失败: {err}",
            "shakiness_level": "未知",
        }, ensure_ascii=False)

    # ── 解析 PSNR log ──
    psnr_log_path = os.path.join(tmp_dir, "shake_psnr.log")
    if not os.path.exists(psnr_log_path):
        return json.dumps({
            "error": "未生成 PSNR 数据",
            "shakiness_level": "未知",
        }, ensure_ascii=False)

    psnr_values = []
    try:
        with open(psnr_log_path, "r", encoding="utf-8") as f:
            for line in f:
                m = re.search(r"psnr_y:([\d.inf]+)", line)
                if m and m.group(1) != "inf":
                    psnr_values.append(float(m.group(1)))
    except Exception as e:
        _cleanup_tmp([psnr_log_path])
        return json.dumps({
            "error": f"解析 PSNR 失败: {e}",
            "shakiness_level": "未知",
        }, ensure_ascii=False)

    _cleanup_tmp([psnr_log_path])

    if len(psnr_values) < 2:
        return json.dumps({
            "error": "帧数不足,无法分析",
            "shakiness_level": "未知",
            "frames_analyzed": len(psnr_values),
        }, ensure_ascii=False)

    # ── 计算抖动指标 ──
    avg_psnr = sum(psnr_values) / len(psnr_values)
    variance = sum((p - avg_psnr) ** 2 for p in psnr_values) / len(psnr_values)

    # 判断抖动等级(基于平均 PSNR,单位 dB)
    # 经验阈值(30fps 降采样到 10fps 后的帧间对比):
    #   > 40dB: 几乎无变化,很稳定
    #   30-40dB: 轻微运动
    #   20-30dB: 明显运动/抖动
    #   < 20dB: 剧烈抖动/场景切换
    if avg_psnr > 40:
        level = "稳定"
        recommendation = "视频画面稳定,无需防抖处理"
    elif avg_psnr > 30:
        level = "轻微抖动"
        recommendation = "建议使用 deshake 快速防抖,或 vidstab 低平滑度(5-10)"
    elif avg_psnr > 20:
        level = "中度抖动"
        recommendation = "建议使用 vidstab 防抖,平滑度 10-15"
    else:
        level = "严重抖动"
        recommendation = "强烈建议使用 vidstab 防抖,平滑度 15-25"

    result = {
        "avg_psnr": round(avg_psnr, 2),
        "psnr_variance": round(variance, 2),
        "shakiness_level": level,
        "frames_analyzed": len(psnr_values),
        "recommendation": recommendation,
    }

    return json.dumps(result, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════════

def _check_nvenc() -> bool:
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


def _detect_fps(video_path: str) -> float:
    """检测视频帧率"""
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", video_path],
            capture_output=True, timeout=15, check=False,
        )
        out = (r.stdout + r.stderr).decode("utf-8", errors="replace")
        fps_m = re.search(r"(\d+(?:\.\d+)?)\s*(?:fps|tb\(r\))", out)
        if fps_m:
            fps = float(fps_m.group(1))
            if 10 <= fps <= 120:
                return round(fps, 1)
        fps_m2 = re.search(r"(\d+)/(\d+)\s*tb", out)
        if fps_m2:
            fps = float(fps_m2.group(1)) / float(fps_m2.group(2))
            if 10 <= fps <= 120:
                return round(fps, 1)
    except Exception:
        pass
    return 30.0


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
            except:
                pass


# 工具已通过 @tool 装饰器自动注册到 Registry
