"""
调色工具 — 色彩调整和滤镜阶段
===========================
AI 通过调用这些工具来应用色彩调整,滤镜效果.
"""
import json, os, subprocess, base64, hashlib, re
from pathlib import Path
from typing import Optional

from director.registry import tool

_PROJECT_DIR = Path(__file__).parent.parent.parent

# ─── 内置色彩预设 ──────────────────────────────────────────

# ffmpeg eq 滤镜参数预设
_COLOR_PRESETS = {
    "提亮": {
        "brightness": 0.15,
        "contrast": 1.05,
        "saturation": 1.0,
        "description": "整体提亮,轻微增加对比度",
    },
    "暖色调": {
        "brightness": 0.0,
        "contrast": 1.0,
        "saturation": 1.2,
        "colorbalance_rh": 0.1,
        "colorbalance_gh": -0.05,
        "colorbalance_bh": -0.1,
        "description": "增加红色,减少蓝色,营造温暖氛围",
    },
    "冷色调": {
        "brightness": 0.0,
        "contrast": 1.0,
        "saturation": 1.1,
        "colorbalance_rh": -0.1,
        "colorbalance_gh": 0.0,
        "colorbalance_bh": 0.15,
        "description": "增加蓝色,减少红色,营造冷峻氛围",
    },
    "胶片感": {
        "brightness": -0.05,
        "contrast": 1.15,
        "saturation": 0.85,
        "colorbalance_shadows": "0.1:0.05:-0.05",
        "colorbalance_midtones": "0.05:0.02:-0.02",
        "description": "低饱和度,高对比度,暗部偏暖的胶片质感",
    },
    "日系清新": {
        "brightness": 0.1,
        "contrast": 0.95,
        "saturation": 1.1,
        "colorbalance_shadows": "0.0:0.0:0.05",
        "colorbalance_highlights": "0.0:0.02:0.05",
        "description": "偏亮,低对比度,高饱和度,轻微蓝色倾向",
    },
    "黑白": {
        "hue": 0,
        "saturation": 0,
        "contrast": 1.1,
        "brightness": 0.0,
        "description": "去色转为黑白",
    },
    "复古": {
        "brightness": -0.05,
        "contrast": 1.1,
        "saturation": 0.7,
        "colorbalance_shadows": "0.15:0.05:-0.1",
        "colorbalance_midtones": "0.1:0.02:-0.05",
        "colorbalance_highlights": "0.05:0.0:-0.02",
        "description": "暗部偏橙黄,整体偏暖的复古风格",
    },
    "高对比": {
        "brightness": 0.0,
        "contrast": 1.3,
        "saturation": 1.1,
        "description": "高对比度,画面锐利清晰",
    },
    "暗调电影": {
        "brightness": -0.1,
        "contrast": 1.2,
        "saturation": 0.9,
        "gamma": 0.9,
        "colorbalance_shadows": "0.05:0.05:-0.1",
        "colorbalance_midtones": "0.0:0.0:-0.05",
        "description": "整体偏暗,高对比,暗部偏橙,亮部冷调的电影质感",
    },
    "鲜艳": {
        "brightness": 0.0,
        "contrast": 1.1,
        "saturation": 1.5,
        "description": "高饱和度,色彩鲜艳夺目",
    },
}

_LUT_EFFECTS_DIR = _PROJECT_DIR / "color_luts"


# ─── FFmpeg curves filter  控制点解析 ──────────────

def _safe_float(v, default=-1.0) -> float:
    """安全转 float,兼容 None/字符串/其他类型"""
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def _validate_curve_points(curve_str: str) -> bool:
    """验证曲线控制点格式: '0/0 0.25/0.35 0.5/0.55 1/1'"""
    if not curve_str:
        return False
    parts = curve_str.strip().split()
    if len(parts) < 2:
        return False
    for p in parts:
        if "/" not in p:
            return False
        try:
            x_str, y_str = p.split("/", 1)
            x, y = float(x_str), float(y_str)
            if not (0 <= x <= 1 and 0 <= y <= 1):
                return False
        except ValueError:
            return False
    # 必须从 0 开始到 1 结束
    first_x = float(parts[0].split("/")[0])
    last_x = float(parts[-1].split("/")[0])
    if first_x != 0 or last_x != 1:
        return False
    return True


def _build_curves_filter(master: str = "", red: str = "", green: str = "", blue: str = "") -> str:
    """构建 ffmpeg curves filter 字符串"""
    parts = []
    if master:
        parts.append(f"master='{master}'")
    if red:
        parts.append(f"r='{red}'")
    if green:
        parts.append(f"g='{green}'")
    if blue:
        parts.append(f"b='{blue}'")
    if not parts:
        return ""
    return f"curves={':'.join(parts)}"


def _build_colorbalance_filter(shadows: str = "", midtones: str = "", highlights: str = "") -> str:
    """
    构建 ffmpeg colorbalance filter 字符串.

    参数格式: "r:g:b",每个值范围 -1.0 ~ 1.0
    例如: "0.1:-0.05:-0.1" 表示 r+0.1, g-0.05, b-0.1
    """
    parts = []
    zone_mapping = {"shadows": "s", "midtones": "m", "highlights": "h"}
    for zone_name, zone_args in [("shadows", shadows), ("midtones", midtones), ("highlights", highlights)]:
        if not zone_args:
            continue
        try:
            r, g, b = [float(v.strip()) for v in zone_args.split(":")]
        except ValueError:
            continue
        suffix = zone_mapping[zone_name]
        parts.append(f"r{suffix}={r}")
        parts.append(f"g{suffix}={g}")
        parts.append(f"b{suffix}={b}")
    if not parts:
        return ""
    return f"colorbalance={':'.join(parts)}"


# ─── 遮罩合成辅助 ─────────────────────────────────

def _apply_mask_postprocess(
    video_path: str,
    graded_path: str,
    mask_path: str,
    output_path: str,
) -> str:
    """
    如果提供了 mask_path,用 overlay + alphamerge 将调色结果区域合成.
    mask 白色区域显示调色后,黑色区域保持原画.

    注意:使用 overlay + alphamerge 而非 maskedmerge,因为 maskedmerge 在
    yuv420p 下对 chroma subsampling 处理有 bug(https://trac.ffmpeg.org/ticket/10666),
    导致 mask=0 时 base 仍被 overlay 的 chroma 污染.
    overlay + alphamerge 在 RGBA 空间操作,像素级精确.
    """
    if not mask_path or not os.path.exists(mask_path):
        if graded_path != output_path:
            import shutil
            shutil.copy2(graded_path, output_path)
        return output_path

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,       # 0:v — 原画(base)
        "-i", graded_path,      # 1:v — 调色后(overlay)
        "-i", mask_path,        # 2:v — 遮罩
        "-filter_complex",
        "[2:v]format=gray,setparams=range=jpeg[mask_norm];"
        "[1:v]format=rgba[mask_base];"
        "[mask_base][mask_norm]alphamerge[graded_alpha];"
        "[0:v]format=rgba[base_rgba];"
        "[base_rgba][graded_alpha]overlay=format=auto:alpha=1[out]",
        "-map", "[out]",
        "-map", "0:a?",         # 保持原音频
        "-c:v", "libx264", "-crf", "10", "-preset", "slow",
        "-c:a", "copy",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=300)
    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        return output_path
    return graded_path  # 合并失败,降级返回纯调色结果


# ─── 高级调色工具 ──────────────────────────────────


@tool(
    name="apply_color_curves",
    description=(
        "RGB/亮度曲线调色.给 master(亮度)和 R/G/B 通道分别设置控制点,"
        "格式 \"0/0 0.25/0.35 0.5/0.5 1/1\",每对 x/y 范围 0~1."
        "支持草稿模式(提供 draft_id 自动写入草稿)."
    ),
    phase="edit",
    category="color",
    tags=["color"],
)
def apply_color_curves(
    video_path: str,
    master_curve: str = "",
    red_curve: str = "",
    green_curve: str = "",
    blue_curve: str = "",
    intensity: float = 1.0,
    mask_path: str = "",
    output_path: str = "",
    draft_id: str = "",
    clip_id: int = 0,
) -> str:
    """
    通过 RGB 曲线做精确调色.支持 master(亮度)和 R/G/B 独立通道曲线.

    Args:
        video_path: 输入视频路径
        master_curve: 亮度曲线,格式 "0/0 0.25/0.35 0.5/0.55 0.75/0.8 1/1"
                      每对 x/y 控制点,范围 0~1
        red_curve: 红色通道曲线
        green_curve: 绿色通道曲线
        blue_curve: 蓝色通道曲线
        intensity: 混合强度 0.0-1.0(1.0=完全应用)
        mask_path: 可选,遮罩视频路径.提供后调色只作用在遮罩白色区域
        output_path: 输出路径(可选)

    Returns:
        结果信息

    Examples:
        - S曲线增对比度: master_curve="0/0 0.25/0.3 0.5/0.5 0.75/0.7 1/1"
        - 提亮暗部: master_curve="0/0.1 0.25/0.3 0.5/0.5 0.75/0.7 1/1"
        - 蓝通道压暗 + 红通道提亮:
          red_curve="0/0 0.5/0.55 1/1"
          blue_curve="0/0 0.5/0.45 1/1"
    """
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"

    # 验证至少有一个曲线参数
    curves_data = {"master": master_curve, "r": red_curve, "g": green_curve, "b": blue_curve}
    valid_curves = {k: v for k, v in curves_data.items() if v and _validate_curve_points(v)}
    if not valid_curves:
        return ("曲线参数无效.格式必须是 \"0/0 0.25/0.35 ... 1/1\","
                "每对 x/y 都在 0~1 范围,首点 0/0 末点 1/1.")

    filter_str = _build_curves_filter(
        master=curves_data["master"],
        red=curves_data["r"],
        green=curves_data["g"],
        blue=curves_data["b"],
    )
    if not filter_str:
        return "无法构建曲线滤镜"

    import hashlib
    if not output_path:
        hash_s = hashlib.md5(video_path.encode()).hexdigest()[:8]
        output_path = os.path.join(_PROJECT_DIR, "output", f"curves_{hash_s}.mp4")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    if mask_path and os.path.exists(mask_path):
        # 一次编码:调色 + 遮罩合并
        # 注意:必须用 format=rgba 确保 alphamerge + overlay 在 RGB 空间精确工作
        cmd = ["ffmpeg", "-y", "-i", video_path, "-i", mask_path]
        if intensity < 1.0:
            base_filter = _build_curves_filter(
                master=curves_data["master"],
                red=curves_data["r"],
                green=curves_data["g"],
                blue=curves_data["b"],
            )
            cmd += ["-filter_complex",
                    f"[0:v]split=3[base_yuv][blend_orig][filter_src];"
                    f"[filter_src]{base_filter}[grade_in];"
                    f"[blend_orig][grade_in]blend=all_mode='overlay':all_opacity={intensity}[graded_yuv];"
                    f"[graded_yuv]format=rgba[graded];"
                    f"[1:v]format=gray,setparams=range=jpeg[mask_norm];"
                    f"[graded][mask_norm]alphamerge[graded_alpha];"
                    f"[base_yuv]format=rgba[base];"
                    f"[base][graded_alpha]overlay=format=auto:alpha=1[out]"]
        else:
            cmd += ["-filter_complex",
                    f"[0:v]split=2[base_yuv][filter_src];"
                    f"[filter_src]{filter_str}[graded_yuv];"
                    f"[graded_yuv]format=rgba[graded];"
                    f"[1:v]format=gray,setparams=range=jpeg[mask_norm];"
                    f"[graded][mask_norm]alphamerge[graded_alpha];"
                    f"[base_yuv]format=rgba[base];"
                    f"[base][graded_alpha]overlay=format=auto:alpha=1[out]"]
        cmd += ["-map", "[out]", "-map", "0:a?"]
    else:
        cmd = ["ffmpeg", "-y", "-i", video_path]
        if intensity < 1.0:
            cmd += ["-filter_complex",
                    f"[0:v]{filter_str}[filtered];"
                    f"[0:v][filtered]blend=all_mode='overlay':all_opacity={intensity}[out]",
                    "-map", "[out]", "-map", "0:a?"]
        else:
            cmd += ["-vf", filter_str]
    cmd += ["-c:v", "libx264", "-crf", "20", "-c:a", "copy", "-movflags", "+faststart", output_path]
    result = subprocess.run(cmd, capture_output=True, timeout=300, check=False)

    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        size = os.path.getsize(output_path) / (1024 * 1024)

        # ── 写草稿 ──
        if draft_id:
            from director.draft import _write_to_draft
            _write_to_draft(
                draft_id, clip_id, "color_grading",
                {"master_curve": master_curve, "red_curve": red_curve,
                 "green_curve": green_curve, "blue_curve": blue_curve,
                 "intensity": intensity},
                label="调色完成",
            )

        curve_desc = ", ".join(f"{k}" for k in valid_curves)
        return f"✅ 曲线调色完成 ({curve_desc}): {output_path} ({size:.1f}MB)"
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="replace")[-300:]
        return f"❌ 曲线调色失败: {err}"
    return "❌ 曲线调色失败"


@tool(
    name="apply_color_wheels",
    description="三向色轮调色——分别调整阴影/中间调/高光的 RGB 偏移,类似 DaVinci Resolve 的 Lift/Gamma/Gain.参数格式 \"r:g:b\",范围 -1.0~1.0(正数增加该颜色)",
    phase="edit",
    category="color",
    tags=["color"],
)
def apply_color_wheels(
    video_path: str,
    shadows_rgb: str = "",
    midtones_rgb: str = "",
    highlights_rgb: str = "",
    intensity: float = 1.0,
    mask_path: str = "",
    output_path: str = "",
) -> str:
    """
    三向色轮调色——分别调整阴影,中间调,高光的红/绿/蓝偏移.
    类似 DaVinci Resolve 的 Lift/Gamma/Gain.

    Args:
        video_path: 输入视频路径
        shadows_rgb: 阴影偏移,格式 "r:g:b",范围 -1.0 ~ 1.0
                     正数增加该颜色,负数减少
        midtones_rgb: 中间调偏移
        highlights_rgb: 高光偏移
        intensity: 混合强度 0.0-1.0
        mask_path: 可选,遮罩视频路径.提供后调色只作用在遮罩白色区域
        output_path: 输出路径(可选)

    Returns:
        结果信息

    Examples:
        - 暗部加暖: shadows_rgb="0.1:0.03:-0.08"
        - 高光加冷: highlights_rgb="-0.05:0.02:0.1"
        - 胶片青橙: shadows_rgb="0.15:0.0:-0.08", highlights_rgb="-0.05:0.02:0.12"
    """
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"

    # 验证至少有一个有效色轮参数
    has_valid = any(
        v and len(v.split(":")) == 3
        for v in [shadows_rgb, midtones_rgb, highlights_rgb]
    )
    if not has_valid:
        return "至少需要提供一个色轮参数,格式 \"r:g:b\"(如 \"0.1:-0.05:-0.1\")"

    filter_str = _build_colorbalance_filter(
        shadows=shadows_rgb,
        midtones=midtones_rgb,
        highlights=highlights_rgb,
    )
    if not filter_str:
        return "无法构建色轮滤镜"

    import hashlib
    if not output_path:
        hash_s = hashlib.md5(video_path.encode()).hexdigest()[:8]
        output_path = os.path.join(_PROJECT_DIR, "output", f"wheels_{hash_s}.mp4")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    if mask_path and os.path.exists(mask_path):
        cmd = ["ffmpeg", "-y", "-i", video_path, "-i", mask_path]
        if intensity < 1.0:
            base_filter = _build_colorbalance_filter(
                shadows=shadows_rgb, midtones=midtones_rgb, highlights=highlights_rgb,
            )
            cmd += ["-filter_complex",
                    f"[0:v]split=3[base_yuv][blend_orig][filter_src];"
                    f"[filter_src]{base_filter}[grade_in];"
                    f"[blend_orig][grade_in]blend=all_mode='overlay':all_opacity={intensity}[graded_yuv];"
                    f"[graded_yuv]format=rgba[graded];"
                    f"[1:v]format=gray,setparams=range=jpeg[mask_norm];"
                    f"[graded][mask_norm]alphamerge[graded_alpha];"
                    f"[base_yuv]format=rgba[base];"
                    f"[base][graded_alpha]overlay=format=auto:alpha=1[out]"]
        else:
            cmd += ["-filter_complex",
                    f"[0:v]split=2[base_yuv][filter_src];"
                    f"[filter_src]{filter_str}[graded_yuv];"
                    f"[graded_yuv]format=rgba[graded];"
                    f"[1:v]format=gray,setparams=range=jpeg[mask_norm];"
                    f"[graded][mask_norm]alphamerge[graded_alpha];"
                    f"[base_yuv]format=rgba[base];"
                    f"[base][graded_alpha]overlay=format=auto:alpha=1[out]"]
        cmd += ["-map", "[out]", "-map", "0:a?"]
    else:
        cmd = ["ffmpeg", "-y", "-i", video_path]
        if intensity < 1.0:
            cmd += ["-filter_complex",
                    f"[0:v]{filter_str}[filtered];"
                    f"[0:v][filtered]blend=all_mode='overlay':all_opacity={intensity}[out]",
                    "-map", "[out]", "-map", "0:a?"]
        else:
            cmd += ["-vf", filter_str]
    cmd += ["-c:v", "libx264", "-crf", "20", "-c:a", "copy", "-movflags", "+faststart", output_path]
    result = subprocess.run(cmd, capture_output=True, timeout=300, check=False)

    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        size = os.path.getsize(output_path) / (1024 * 1024)
        zones = [f"{k.split('_')[0]}({v})" for k, v in
                 [("shadows", shadows_rgb), ("midtones", midtones_rgb), ("highlights", highlights_rgb)]
                 if v]
        return f"✅ 色轮调色完成 ({', '.join(zones)}): {output_path} ({size:.1f}MB)"
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="replace")[-300:]
        return f"❌ 色轮调色失败: {err}"
    return "❌ 色轮调色失败"


@tool(
    name="apply_color_levels",
    description="色阶调整——黑点/白点裁切,Gamma 中间调,暗部偏移.black_point=黑点裁切(0~1),white_point=白点裁切(0~1),gamma_r/g/b=通道gamma(0.5~2.0),shadow_=暗部偏移(-1~1)",
    phase="edit",
    category="color",
    tags=["color"],
)
def apply_color_levels(
    video_path: str,
    black_point: float = -1.0,
    white_point: float = -1.0,
    gamma_r: float = -1.0,
    gamma_g: float = -1.0,
    gamma_b: float = -1.0,
    shadow_red: float = -1.0,
    shadow_green: float = -1.0,
    shadow_blue: float = -1.0,
    intensity: float = 1.0,
    mask_path: str = "",
    output_path: str = "",
) -> str:
    """
    色阶调整——黑点/白点(输入级)+ Gamma(中间调)+ 阴影偏移.

    Args:
        video_path: 输入视频路径
        black_point: 黑点裁切 0.0~1.0(0=不裁切),默认 -1=不启用
        white_point: 白点裁切 0.0~1.0(1=不裁切),默认 -1=不启用
        gamma_r: 红色通道 gamma(通常 0.5~2.0,1=不变),默认 -1=不启用
        gamma_g: 绿色通道 gamma
        gamma_b: 蓝色通道 gamma
        shadow_red: 暗部红色偏移 -1.0~1.0
        shadow_green: 暗部绿色偏移
        shadow_blue: 暗部蓝色偏移
        intensity: 混合强度 0.0-1.0
        mask_path: 可选,遮罩视频路径.提供后调色只作用在遮罩白色区域
        output_path: 输出路径(可选)

    Returns:
        结果信息

    Examples:
        - 标准对比度调整: black_point=0.05, white_point=0.95
        - 暗部加绿: shadow_green=0.1
        - 冷暖调: gamma_r=1.1, gamma_b=0.9
    """
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"

    filters = []

    # colorlevels: 黑/白点(防御:JSON 传入可能是字符串)
    colorlevels_parts = []
    _bp = _safe_float(black_point)
    _wp = _safe_float(white_point)
    if _bp >= 0:
        colorlevels_parts.append(f"rimin={_bp}")
    if _wp >= 0:
        colorlevels_parts.append(f"rimax={_wp}")
    if colorlevels_parts:
        filters.append(f"colorlevels={':'.join(colorlevels_parts)}")

    # eq: gamma per channel
    eq_parts = []
    shadow_parts = []
    if gamma_r >= 0:
        eq_parts.append(f"gamma_r={gamma_r}")
    if gamma_g >= 0:
        eq_parts.append(f"gamma_g={gamma_g}")
    if gamma_b >= 0:
        eq_parts.append(f"gamma_b={gamma_b}")

    # colorbalance: 暗部偏移(通过 shadows 通道)
    if shadow_red >= 0 or shadow_green >= 0 or shadow_blue >= 0:
        sr = shadow_red if shadow_red >= 0 else 0
        sg = shadow_green if shadow_green >= 0 else 0
        sb = shadow_blue if shadow_blue >= 0 else 0
        filters.append(f"colorbalance=rs={sr}:gs={sg}:bs={sb}")

    if eq_parts:
        filters.append(f"eq={':'.join(eq_parts)}")

    if not filters:
        return "未提供有效参数.至少设置 black_point,white_point,gamma,shadow 之一."

    filter_str = ",".join(filters)

    import hashlib
    if not output_path:
        hash_s = hashlib.md5(video_path.encode()).hexdigest()[:8]
        output_path = os.path.join(_PROJECT_DIR, "output", f"levels_{hash_s}.mp4")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    if intensity < 1.0:
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-filter_complex",
            f"[0:v]{filter_str}[filtered];"
            f"[0:v][filtered]blend=all_mode='overlay':all_opacity={intensity}[out]",
            "-map", "[out]", "-map", "0:a?",
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", filter_str,
        ]
    cmd += ["-c:v", "libx264", "-crf", "20", "-c:a", "copy", "-movflags", "+faststart", output_path]
    result = subprocess.run(cmd, capture_output=True, timeout=300, check=False)

    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        if mask_path:
            final_out = output_path.replace(".mp4", "_masked.mp4")
            _apply_mask_postprocess(video_path, output_path, mask_path, final_out)
            if os.path.exists(final_out):
                output_path = final_out
        size = os.path.getsize(output_path) / (1024 * 1024)
        applied = []
        if _safe_float(black_point) >= 0: applied.append(f"黑点={black_point}")
        if _safe_float(white_point) >= 0: applied.append(f"白点={white_point}")
        if gamma_r >= 0: applied.append(f"γR={gamma_r}")
        if gamma_g >= 0: applied.append(f"γG={gamma_g}")
        if gamma_b >= 0: applied.append(f"γB={gamma_b}")
        if shadow_red >= 0: applied.append(f"暗部R={shadow_red}")
        return f"✅ 色阶调整完成 ({', '.join(applied)}): {output_path} ({size:.1f}MB)"
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="replace")[-300:]
        return f"❌ 色阶调整失败: {err}"
    return "❌ 色阶调整失败"


@tool(
    name="preview_color_advanced",
    description="预览高级调色效果(曲线/色轮/色阶),返回一帧 base64 图片供 AI 评审",
    phase="analyze",
    category="color",
    tags=["color"],
)
def preview_color_advanced(
    video_path: str,
    curves_master: str = "",
    curves_red: str = "",
    curves_green: str = "",
    curves_blue: str = "",
    shadows_rgb: str = "",
    midtones_rgb: str = "",
    highlights_rgb: str = "",
    black_point: float = -1.0,
    white_point: float = -1.0,
    time_pos: float = 1.0,
) -> str:
    """
    预览高级调色效果(返回一帧图片的 base64 供 VL 评审).

    参数含义同 apply_color_curves / apply_color_wheels / apply_color_levels.
    """
    if not os.path.exists(video_path):
        return "文件不存在"

    grade = {}
    if curves_master: grade["curves_master"] = curves_master
    if curves_red: grade["curves_r"] = curves_red
    if curves_green: grade["curves_g"] = curves_green
    if curves_blue: grade["curves_b"] = curves_blue
    if shadows_rgb: grade["shadows_rgb"] = shadows_rgb
    if midtones_rgb: grade["midtones_rgb"] = midtones_rgb
    if highlights_rgb: grade["highlights_rgb"] = highlights_rgb
    if _safe_float(black_point) >= 0: grade["black_point"] = black_point
    if _safe_float(white_point) >= 0: grade["white_point"] = white_point

    filter_str = _build_grade_filter(grade)
    if not filter_str:
        return "未提供有效调色参数"

    import hashlib
    hash_s = hashlib.md5(video_path.encode()).hexdigest()[:8]
    tmp = os.path.join(_PROJECT_DIR, "_tmp_color", f"preview_adv_{hash_s}.png")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)

    cmd = [
        "ffmpeg", "-y", "-ss", str(time_pos), "-i", video_path,
        "-vf", filter_str,
        "-vframes", "1", tmp,
    ]
    subprocess.run(cmd, capture_output=True, timeout=30, check=False)

    if not os.path.exists(tmp):
        return "预览生成失败"

    with open(tmp, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    try:
        os.remove(tmp)
    except:
        pass

    return f"data:image/png;base64,{b64}"


def _build_grade_filter(grade: dict) -> str:
    """
    规范调色滤镜构建 — 从单一 grade dict 构建完整 filter 链.
    同时被 colors.py 的独立工具和 render.py 的 Step 1 使用.

    支持的字段:
        curves_master, curves_r, curves_g, curves_b: 曲线控制点 "0/0 0.25/0.3 ... 1/1"
        shadows_rgb, midtones_rgb, highlights_rgb: 色轮 "r:g:b" 范围 -1~1
        black_point, white_point: 色阶裁切 0~1
        brightness, contrast, saturation: eq 滤镜参数
        gamma_r, gamma_g, gamma_b: 伽马校正 0.5~2.0
    """
    parts = []

    # curves: master/R/G/B
    curves_parts = []
    for key, label in [("curves_master", "master"), ("curves_r", "r"),
                        ("curves_g", "g"), ("curves_b", "b")]:
        val = grade.get(key, "")
        if _validate_curve_points(val):
            curves_parts.append(f"{label}='{val}'")
    if curves_parts:
        parts.append(f"curves={':'.join(curves_parts)}")

    # colorbalance: shadows/midtones/highlights
    cb_parts = []
    zone_map = {"shadows_rgb": "s", "midtones_rgb": "m", "highlights_rgb": "h"}
    for key, suffix in zone_map.items():
        val = grade.get(key, "")
        if val and ":" in val:
            try:
                r, g, b = [float(v) for v in val.split(":")]
                cb_parts.append(f"r{suffix}={r}")
                cb_parts.append(f"g{suffix}={g}")
                cb_parts.append(f"b{suffix}={b}")
            except ValueError:
                pass
    if cb_parts:
        parts.append(f"colorbalance={':'.join(cb_parts)}")

    # colorlevels: black/white point
    # 注意:JSON 传入的值可能是字符串,必须转 float
    cl_parts = []
    try:
        bp = float(grade.get("black_point", -1))
    except (ValueError, TypeError):
        bp = -1.0
    try:
        wp = float(grade.get("white_point", -1))
    except (ValueError, TypeError):
        wp = -1.0
    if bp >= 0:
        cl_parts.append(f"rimin={bp}")
    if wp >= 0:
        cl_parts.append(f"rimax={wp}")
    if cl_parts:
        parts.append(f"colorlevels={':'.join(cl_parts)}")

    # eq: brightness/contrast/saturation/gamma per channel
    eq_parts = []
    for key in ("brightness", "contrast", "saturation"):
        if key in grade:
            eq_parts.append(f"{key}={grade[key]}")
    for key, label in [("gamma_r", "gamma_r"), ("gamma_g", "gamma_g"), ("gamma_b", "gamma_b")]:
        if key in grade:
            eq_parts.append(f"{label}={grade[key]}")
    if eq_parts:
        parts.append(f"eq={':'.join(eq_parts)}")

    return ",".join(parts)


# ─── HSL 二级调色 ──────────────────────────────────


@tool(
    name="apply_hsl_secondary",
    description=(
        "HSL 二级调色——选中视频中特定颜色范围做局部调色."
        "类似 DaVinci Resolve 的 Color Qualifier."
        "通过 target_color(0xRRGGBB) 指定目标颜色,"
        "similarity 控制选中范围,"
        "adjustments_json 指定选区内的调色参数"
    ),
    phase="edit",
    category="color",
    tags=["color"],
)
def apply_hsl_secondary(
    video_path: str,
    target_color: str = "",
    similarity: float = 0.2,
    adjustments_json: str = "",
    feather: float = 0.0,
    invert_mask: bool = False,
    mask_path: str = "",
    output_path: str = "",
) -> str:
    """
    HSL 二级调色 — 选中特定颜色范围做局部调色.
    类似于 DaVinci Resolve 的 Color Qualifier / HSL 选色.

    Args:
        video_path: 输入视频路径
        target_color: 目标颜色,十六进制格式 "0xRRGGBB" 如 "0xFF6600"
                      可以通过 preview_color_advanced 截图让 VL 分析目标颜色
        similarity: 颜色相似度 0.0~1.0(0=精确匹配,1=全选)
        adjustments_json: 对选中区域应用的调色参数 JSON
                          {"curves_master": "...", "shadows_rgb": "...", "saturation": 1.3}
        feather: 边缘羽化 0.0~1.0
        invert_mask: 是否反转选区(True=选中区域保持不变,调色应用到选区外)
        mask_path: 可选,外部遮罩视频路径.与HSL内部遮罩叠加使用
        output_path: 输出路径(可选)

    Returns:
        结果信息

    Examples:
        - 把蓝色天空改成冷暖调:
          target_color="0x4488CC", similarity=0.3,
          adjustments_json='{"shadows_rgb":"0.1:0.05:-0.1"}'
        - 单独提亮面部肤色:
          target_color="0xFFCCAA", similarity=0.25,
          adjustments_json='{"curves_master":"0/0.1 0.5/0.5 1/1"}'
    """
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"
    if not target_color:
        return "请指定目标颜色 target_color,格式 \"0xRRGGBB\""
    if not target_color.startswith("0x"):
        target_color = "0x" + target_color.lstrip("#")

    adjustments = _parse_json(adjustments_json) or {}
    adj_filter = _build_grade_filter(adjustments)
    if not adj_filter:
        return "请提供有效的调色参数(adjustments_json)"

    import hashlib
    if not output_path:
        hash_s = hashlib.md5(video_path.encode()).hexdigest()[:8]
        tag = hashlib.md5(target_color.encode()).hexdigest()[:4]
        output_path = os.path.join(_PROJECT_DIR, "output", f"hsl_{tag}_{hash_s}.mp4")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # filter_complex: split=3 -> 原版 / 调色版 / 遮罩
    # 遮罩链: format=rgba(确保alpha支持) -> colorkey -> alphaextract -> (可选 negate)
    mask_chain = (
        f"format=rgba,colorkey={target_color}:{similarity}:{feather},"
        f"alphaextract"
    )
    if not invert_mask:
        mask_chain += ",negate"
    # 不反转: mask 白色=目标颜色区域 -> 调色只作用于目标区域
    # 反转:   mask 白色=非目标区域 -> 调色作用于背景

    filter_graph = (
        f"[0:v]split=3[orig_yuv][grade_in][mask_in];"
        f"[grade_in]{adj_filter}[graded_yuv];"
        f"[graded_yuv]format=rgba[graded];"
        f"[mask_in]{mask_chain}[mask];"
        f"[graded][mask]alphamerge[graded_alpha];"
        f"[orig_yuv]format=rgba[orig];"
        f"[orig][graded_alpha]overlay=format=auto:alpha=1[out]"
    )

    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-filter_complex", filter_graph,
        "-map", "[out]",
        "-c:v", "libx264", "-crf", "18", "-preset", "slow",
        "-c:a", "copy", "-movflags", "+faststart", output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, timeout=600, check=False)

    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        if mask_path:
            final_out = output_path.replace(".mp4", "_masked.mp4")
            _apply_mask_postprocess(video_path, output_path, mask_path, final_out)
            if os.path.exists(final_out):
                output_path = final_out
        size = os.path.getsize(output_path) / (1024 * 1024)
        desc = f"HSL选色 {target_color} (相似度={similarity})"
        return f"✅ 二级调色完成 {desc}: {output_path} ({size:.1f}MB)"

    err = result.stderr.decode("utf-8", errors="replace")[-300:]
    return f"❌ HSL 二级调色失败: {err}"


def _parse_json(data) -> any:
    """安全解析 JSON 字符串"""
    if not data:
        return None
    if isinstance(data, str):
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return None
    return data


def _fix_title(title: str) -> str:
    """尝试修复从数据库读出的乱码标题"""
    if not title:
        return title
    try:
        # 尝试 latin-1 -> utf-8 修复
        fixed = title.encode("latin-1").decode("utf-8")
        if fixed and len(fixed) > 1:
            return fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return title


# ─── 工具函数 ──────────────────────────────────────────────


@tool(
    name="list_color_presets",
    description="列出所有可用的色彩预设(如提亮,暖色调,胶片感,黑白等)",
    phase="analyze",
    category="color",
    tags=["color"],
)
def list_color_presets() -> str:
    """列出所有可用的色彩预设,每个预设包含效果描述.

    Returns:
        JSON 格式的色彩预设列表
    """
    presets = []
    for name, params in _COLOR_PRESETS.items():
        presets.append({
            "name": name,
            "description": params.get("description", ""),
        })
    return json.dumps(presets, ensure_ascii=False, indent=2)


@tool(
    name="apply_color_preset",
    description="对视频应用色彩预设(调色).支持草稿模式(提供 draft_id 自动写入草稿).",
    phase="edit",
    category="color",
    tags=["color"],
)
def apply_color_preset(
    video_path: str,
    preset_name: str,
    intensity: float = 1.0,
    start_time: float = 0.0,
    duration: float = 0.0,
    mask_path: str = "",
    output_path: str = "",
    draft_id: str = "",
    clip_id: int = 0,
) -> str:
    """
    对视频应用色彩预设.

    Args:
        video_path: 输入视频路径
        preset_name: 色彩预设名称(从 list_color_presets 获取)
        intensity: 强度 0.0-1.0(默认1.0),通过 alpha 混合控制
        start_time: 起始时间(秒),默认从开头
        duration: 持续时间(秒),0=直到结束
        mask_path: 可选,遮罩视频路径.提供后调色只作用在遮罩白色区域,黑色区域保持原画面
        output_path: 输出路径(可选)

    Returns:
        结果信息
    """
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"

    preset = _COLOR_PRESETS.get(preset_name)
    if not preset:
        names = ", ".join(_COLOR_PRESETS.keys())
        return f"未知预设: {preset_name}.可用: {names}"

    import hashlib
    if not output_path:
        hash_s = hashlib.md5(video_path.encode()).hexdigest()[:8]
        output_path = os.path.join(_PROJECT_DIR, "output", f"color_{preset_name}_{hash_s}.mp4")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # 构建 ffmpeg filter 链
    filters = _build_color_filter(preset)
    if not filters:
        return "无法构建色彩滤镜"

    # 截取子视频加上调色再混合
    last_result = None
    has_subclip = start_time > 0 or duration > 0
    if has_subclip:
        sub_path = output_path.replace(".mp4", "_sub.mp4")
        cmd = ["ffmpeg", "-y", "-ss", str(start_time), "-i", video_path]
        if duration > 0:
            cmd += ["-t", str(duration)]
        cmd += ["-vf", filters, "-c:v", "libx264", "-crf", "20", "-an", sub_path]
        subprocess.run(cmd, capture_output=True, timeout=300, check=False)
        if not os.path.exists(sub_path):
            return "子视频处理失败"

        # 如果 intensity < 1.0,混合原视频和调色后的视频
        if intensity < 1.0:
            overlay_path = output_path.replace(".mp4", "_overlay.mp4")
            mix_cmd = [
                "ffmpeg", "-y",
                "-ss", str(start_time), "-i", video_path,
                "-i", sub_path,
                "-filter_complex",
                f"[0:v]trim=0:{duration if duration>0 else '999'},setpts=PTS-STARTPTS[orig];"
                f"[1:v]setpts=PTS-STARTPTS[color];"
                f"[orig][color]blend=all_mode='overlay':all_opacity={intensity}[out]",
                "-map", "[out]", "-map", "0:a?", "-c:v", "libx264", "-crf", "20",
                "-c:a", "copy", "-movflags", "+faststart", overlay_path,
            ]
            last_result = subprocess.run(mix_cmd, capture_output=True, timeout=300, check=False)
            if os.path.exists(overlay_path):
                output_path = overlay_path
        else:
            replace_cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-ss", str(start_time), "-i", sub_path,
                "-filter_complex",
                f"[0:v]trim=0:{start_time}[pre];"
                f"[1:v]setpts=PTS-STARTPTS[color];"
                f"[0:v]trim={start_time + (duration if duration>0 else 999)}:999[post];"
                f"[pre][color][post]concat=n=3[out]",
                "-map", "[out]", "-map", "0:a?", "-c:v", "libx264", "-crf", "20",
                "-c:a", "copy", "-movflags", "+faststart", output_path,
            ]
            last_result = subprocess.run(replace_cmd, capture_output=True, timeout=300, check=False)

        try: os.remove(sub_path)
        except OSError: pass
    else:
        # 直接对整个视频应用
        if mask_path and os.path.exists(mask_path):
            # 单次编码:调色 + 遮罩合并一次完成
            # 注意:遮罩转 full range(jpeg),因为 H.264 的 limited range 白=235≠255
            cmd = ["ffmpeg", "-y", "-i", video_path, "-i", mask_path]
            if intensity < 1.0:
                cmd += ["-filter_complex",
                        f"[0:v]split=3[base_yuv][blend_orig][filter_src];"
                        f"[filter_src]{filters}[grade_in];"
                        f"[blend_orig][grade_in]blend=all_mode='overlay':all_opacity={intensity}[graded_yuv];"
                        f"[graded_yuv]format=rgba[graded];"
                        f"[1:v]format=gray,setparams=range=jpeg[mask_norm];"
                        f"[graded][mask_norm]alphamerge[graded_alpha];"
                        f"[base_yuv]format=rgba[base];"
                        f"[base][graded_alpha]overlay=format=auto:alpha=1[out]"]
            else:
                cmd += ["-filter_complex",
                        f"[0:v]split=2[base_yuv][filter_src];"
                        f"[filter_src]{filters}[graded_yuv];"
                        f"[graded_yuv]format=rgba[graded];"
                        f"[1:v]format=gray,setparams=range=jpeg[mask_norm];"
                        f"[graded][mask_norm]alphamerge[graded_alpha];"
                        f"[base_yuv]format=rgba[base];"
                        f"[base][graded_alpha]overlay=format=auto:alpha=1[out]"]
            cmd += ["-map", "[out]", "-map", "0:a?"]
        else:
            cmd = ["ffmpeg", "-y", "-i", video_path]
            if intensity < 1.0:
                cmd += ["-filter_complex",
                        f"[0:v]{filters}[filtered];"
                        f"[0:v][filtered]blend=all_mode='overlay':all_opacity={intensity}[out]",
                        "-map", "[out]", "-map", "0:a?"]
            else:
                cmd += ["-vf", filters]
        cmd += ["-c:v", "libx264", "-crf", "20", "-c:a", "copy", "-movflags", "+faststart", output_path]
        last_result = subprocess.run(cmd, capture_output=True, timeout=300, check=False)

    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        size = os.path.getsize(output_path) / (1024 * 1024)

        # ── 写草稿(color_preset 用 color_preset 字段而非 color_grading)──
        if draft_id:
            from director.draft import _write_to_draft
            _write_to_draft(
                draft_id, clip_id, "color_preset",
                {"preset_name": preset_name, "intensity": intensity},
                label="调色完成",
            )

        return f"✅ 已应用「{preset_name}」调色 (强度={intensity}): {output_path} ({size:.1f}MB)"
    if last_result and last_result.returncode != 0:
        err = last_result.stderr.decode("utf-8", errors="replace")[-300:]
        return f"❌ 调色失败: {err}"
    return f"❌ 调色失败: 输出文件不存在或为空"


def _build_color_filter(preset: dict) -> str:
    """从预设构建 ffmpeg filter 字符串"""
    filters = []

    # eq 滤镜
    eq_parts = []
    for param in ("brightness", "contrast", "saturation", "gamma"):
        if param in preset:
            eq_parts.append(f"{param}={preset[param]}")
    if preset.get("hue") is not None:
        eq_parts.append(f"saturation=0")  # 黑白时用 eq 控制
    if eq_parts:
        filters.append(f"eq={':'.join(eq_parts)}")

    # colorbalance 滤镜
    # ffmpeg 参数名: shadows->rs/gs/bs, midtones->rm/gm/bm, highlights->rh/gh/bh
    cb_parts = []
    zone_to_chars = {"shadows": "s", "midtones": "m", "highlights": "h"}
    for zone, suffix in zone_to_chars.items():
        key = f"colorbalance_{zone}"
        if key in preset:
            val = preset[key]
            if isinstance(val, str) and ":" in val:
                # "0.0:0.0:0.05" -> rs=0.0:gs=0.0:bs=0.05
                parts = val.split(":")
                for i, ch in enumerate(["r", "g", "b"]):
                    if i < len(parts):
                        cb_parts.append(f"{ch}{suffix}={parts[i]}")
            else:
                cb_parts.append(f"{zone}={val}")
    for channel in ("rh", "gh", "bh"):
        key = f"colorbalance_{channel}"
        if key in preset:
            cb_parts.append(f"{channel}={preset[key]}")
    if cb_parts:
        filters.append(f"colorbalance={':'.join(cb_parts)}")

    # hue 滤镜 (转黑白)
    if preset.get("hue") is not None and preset.get("saturation", 1.0) == 0:
        filters.append("hue=s=0")

    return ",".join(filters) if filters else ""



@tool(
    name="preview_color",
    description="预览预设色彩效果(返回一帧 base64 图片供 AI 评审)",
    phase="analyze",
    category="color",
    tags=["color"],
)
def preview_color(
    video_path: str,
    preset_name: str,
    time_pos: float = 1.0,
) -> str:
    """
    预览色彩效果(生成一帧画面让 VL 评价).

    Args:
        video_path: 视频路径
        preset_name: 色彩预设名称
        time_pos: 预览时间点(秒)

    Returns:
        base64 编码的预览帧(供 VL 评审)
    """
    preset = _COLOR_PRESETS.get(preset_name)
    if not preset:
        return f"未知预设: {preset_name}"

    filters = _build_color_filter(preset)
    if not filters:
        return "无法构建色彩滤镜"

    import base64, tempfile
    tmp = os.path.join(_PROJECT_DIR, "_tmp_color", f"preview_{preset_name}.png")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)

    cmd = [
        "ffmpeg", "-y", "-ss", str(time_pos), "-i", video_path,
        "-vf", filters,
        "-vframes", "1", tmp,
    ]
    subprocess.run(cmd, capture_output=True, timeout=30, check=False)

    if not os.path.exists(tmp):
        return "预览生成失败"

    with open(tmp, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    try: os.remove(tmp)
    except OSError: pass

    return f"data:image/png;base64,{b64}"


@tool(
    name="list_lut_effects",
    description="列出已下载的 LUT 调色效果包",
    phase="analyze",
    category="color",
    tags=["color"],
)
def list_lut_effects() -> str:
    """列出已下载的 LUT 调色效果包.

    Returns:
        JSON 格式的 LUT 效果列表
    """
    if not _LUT_EFFECTS_DIR.exists():
        return "[]"
    effects = []
    for f in _LUT_EFFECTS_DIR.iterdir():
        if f.is_dir():
            meta_path = f / "metadata.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    effects.append(meta)
                except:
                    pass
            else:
                effects.append({"name": f.name, "path": str(f)})
    return json.dumps(effects, ensure_ascii=False, indent=2) if effects else "(无 LUT 效果)"


@tool(
    name="apply_lut",
    description="应用 LUT 3D Look-Up Table 调色效果.可选 mask_path 实现区域 LUT 调色",
    phase="edit",
    category="color",
    tags=["color"],
)
def apply_lut(
    video_path: str,
    lut_name: str,
    intensity: float = 1.0,
    mask_path: str = "",
    output_path: str = "",
) -> str:
    """
    应用 LUT (3D Look-Up Table) 调色效果.

    Args:
        video_path: 输入视频路径
        lut_name: LUT 文件名称(从 list_lut_effects 获取)
        intensity: 强度 0.0-1.0(默认1.0)
        mask_path: 可选,遮罩视频路径.提供后调色只作用在遮罩白色区域
        output_path: 输出路径(可选,默认自动生成到 output/)

    Returns:
        结果信息
    """
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"

    # 查找 LUT 文件
    lut_path = None
    if _LUT_EFFECTS_DIR.exists():
        for f in _LUT_EFFECTS_DIR.rglob("*"):
            if f.suffix.lower() in (".cube", ".png", ".bin") and lut_name in f.name:
                lut_path = f
                break

    if not lut_path or not lut_path.exists():
        return f"未找到 LUT: {lut_name}"

    import hashlib
    if not output_path:
        output_path = os.path.join(_PROJECT_DIR, "output",
                                   f"lut_{lut_name}_{hashlib.md5(video_path.encode()).hexdigest()[:8]}.mp4")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # 构建 lut3d filter
    # Windows 注意事项:
    # 1. filter 字符串中单引号 '...' 不被识别为引号
    # 2. 绝对路径 C:/Users/... 中的 C: 会被当成选项名
    # 3. 必须用相对路径 + 正斜杠
    lut_rel = os.path.relpath(lut_path, _PROJECT_DIR)
    lut_path_str = lut_rel.replace("\\", "/")
    ext = lut_path.suffix.lower()
    if ext in (".cube", ".png"):
        lut_filter = f"lut3d=file={lut_path_str}:interp=trilinear"
    else:
        return f"不支持的 LUT 格式: {ext}"

    has_mask = bool(mask_path and os.path.exists(mask_path))

    if has_mask:
        # 单次编码 + overlay+alphamerge 遮罩合成(RGB空间,避免yuv chroma bug)
        if intensity < 1.0:
            cmd = ["ffmpeg", "-y", "-i", video_path, "-i", mask_path]
            filter_complex = (
                f"[0:v]{lut_filter},format=rgba[graded];"
                f"[0:v]format=rgba[lut_base];"
                f"[lut_base][graded]blend=all_mode='overlay':all_opacity={intensity}[graded2];"
                f"[1:v]format=gray,setparams=range=jpeg[mask_norm];"
                f"[graded2][mask_norm]alphamerge[graded_alpha];"
                f"[lut_base][graded_alpha]overlay=format=auto:alpha=1[out]"
            )
            cmd += ["-filter_complex", filter_complex]
        else:
            cmd = ["ffmpeg", "-y", "-i", video_path, "-i", mask_path]
            filter_complex = (
                f"[0:v]{lut_filter},format=rgba[graded];"
                f"[0:v]format=rgba[base];"
                f"[1:v]format=gray,setparams=range=jpeg[mask_norm];"
                f"[graded][mask_norm]alphamerge[graded_alpha];"
                f"[base][graded_alpha]overlay=format=auto:alpha=1[out]"
            )
            cmd += ["-filter_complex", filter_complex]
        cmd += ["-map", "[out]", "-map", "0:a?",
                "-c:v", "libx264", "-crf", "20", "-preset", "slow",
                "-c:a", "copy", output_path]
    else:
        # 无遮罩:保持原有逻辑
        if intensity < 1.0:
            filter_str = f"{lut_filter},split[color][out];[0:v]split=2[orig][tmp];[orig][color]blend=all_mode='overlay':all_opacity={intensity}[out]"
        else:
            filter_str = lut_filter
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", filter_str,
            "-c:v", "libx264", "-crf", "20",
            "-c:a", "copy", "-movflags", "+faststart", output_path,
        ]

    subprocess.run(cmd, capture_output=True, timeout=300, check=False)

    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        size = os.path.getsize(output_path) / (1024 * 1024)
        mask_info = f"(遮罩区域)" if has_mask else ""
        return f"✅ LUT 调色完成{mask_info}: {output_path} ({size:.1f}MB)"
    return "❌ LUT 调色失败"


# ─── 多镜头色彩匹配 ────────────────────────────

def _extract_frame_rgb(video_path: str, time_pos: float = 1.0):
    """提取视频某一帧,返回该帧的平均 R/G/B 值(0~255)和亮度 Y.

    内部使用 ffmpeg 提取 PNG + PIL 读取分析.
    若 PIL 不可用,回退到 ffmpeg signalstats 解析.
    """
    tmp_dir = _PROJECT_DIR / "_tmp_color"
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_png = os.path.join(tmp_dir, f"match_{hashlib.md5(f'{video_path}:{time_pos}'.encode()).hexdigest()[:12]}.png")

    subprocess.run([
        "ffmpeg", "-y", "-ss", str(time_pos), "-i", video_path,
        "-vframes", "1", tmp_png,
    ], capture_output=True, timeout=30, check=False)

    if not os.path.exists(tmp_png):
        return None

    try:
        from PIL import Image
        import numpy as np
        img = Image.open(tmp_png).convert("RGB")
        arr = np.array(img, dtype=np.float64)
        h, w, _ = arr.shape
        mean_r = float(arr[:,:,0].mean())
        mean_g = float(arr[:,:,1].mean())
        mean_b = float(arr[:,:,2].mean())
        # 亮度近似: Y = 0.299R + 0.587G + 0.114B
        mean_y = 0.299 * mean_r + 0.587 * mean_g + 0.114 * mean_b
        os.remove(tmp_png)
        return {"R": mean_r, "G": mean_g, "B": mean_b, "Y": mean_y}
    except ImportError:
        # PIL 不可用,用 ffmpeg signalstats 回退
        pass

    # 回退:用 ffmpeg signalstats 解析
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(time_pos), "-i", video_path,
             "-vf", "signalstats", "-frames:v", "1", "-f", "null", "-"],
            capture_output=True, timeout=30, check=False,
        )
        out = r.stderr.decode("utf-8", errors="replace")
        # signalstats 输出: Y=128 U=128 V=128
        y_m = re.search(r"Y=(\d+)", out)
        u_m = re.search(r"U=(\d+)", out)
        v_m = re.search(r"V=(\d+)", out)
        if y_m:
            y_val = float(y_m.group(1))
            # U/V 是色差,不是直接 R/G/B
            os.remove(tmp_png)
            return {"Y": y_val * 255/128, "U": float(u_m.group(1)) if u_m else 128,
                    "V": float(v_m.group(1)) if v_m else 128}
    except Exception:
        pass

    try: os.remove(tmp_png)
    except OSError: pass
    return None


@tool(
    name="match_color",
    description=(
        "多镜头色彩匹配——分析所有片段的平均色彩,自动计算调色参数"
        "使每个片段的色调与参考片段一致."
        "输出 grade_json 可直接传入 render_final 的 grade_json 参数,"
        "让最终输出的所有镜头色彩统一."
        "使用场景:编排完成后发现各镜头色温差太大需要统一."
    ),
    phase="all",
    category="color",
    tags=["color"],
)
def match_color(
    video_path: str,
    segments_json: str,
    reference_id: int = 0,
    match_properties: str = "all",
    strength: float = 0.8,
) -> str:
    """
    多镜头色彩匹配——使所有镜头的色调与参考镜头一致.

    原理:提取每个镜头的中间帧,分析平均 R/G/B 值,然后
    为每个非参考镜头计算 colorbalance + eq 校正参数,
    使其亮度/色温向参考镜头靠拢.

    Args:
        video_path: 源视频路径
        segments_json: 片段列表 [{"id": 0, "start":0, "end":30}, ...]
                       id 必须是整数且与参考一致
        reference_id: 参考片段的 id(其他片段会匹配到这个片段的色调)
        match_properties: 匹配属性:
                          "all"=亮度+色温,
                          "luma"=仅亮度,
                          "color"=仅色温
        strength: 匹配强度 0.0~1.0(1.0=完全匹配,0.5=半匹配)

    Returns:
        JSON 字符串,包含 grade_json(可直接传入 render_final 的 grade_json 参数)
        和匹配信息
    """
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"

    segments = _parse_json(segments_json)
    if not segments:
        return "无效的片段数据"

    # 构建 id -> segment 映射
    seg_map = {}
    for s in segments:
        sid = s.get("id")
        if sid is None:
            return "片段缺少 id 字段"
        seg_map[int(sid)] = s

    if int(reference_id) not in seg_map:
        return f"参考片段 id={reference_id} 不在片段列表中"

    match_luma = match_properties in ("all", "luma")
    match_color_temp = match_properties in ("all", "color")

    # 1. 提取参考片段的中间帧统计
    ref_seg = seg_map[int(reference_id)]
    ref_mid = ref_seg.get("start", 0) + ref_seg.get("duration",
                         ref_seg.get("end", 30) - ref_seg.get("start", 0)) / 2
    ref_stats = _extract_frame_rgb(video_path, ref_mid)
    if not ref_stats:
        return "无法提取参考帧画面(可能 ffmpeg 或 PIL 不可用)"
    ref_y = ref_stats.get("Y", 128)
    ref_r = ref_stats.get("R", ref_y)
    ref_g = ref_stats.get("G", ref_y)
    ref_b = ref_stats.get("B", ref_y)

    # 2. 为每个非参考片段计算校正参数
    grades = {}
    match_info = []

    for s in segments:
        sid = int(s.get("id"))
        if sid == int(reference_id):
            match_info.append({"id": sid, "action": "reference", "note": "参考镜头,不做校正"})
            continue

        mid = s.get("start", 0) + s.get("duration",
              s.get("end", 30) - s.get("start", 0)) / 2
        stats = _extract_frame_rgb(video_path, mid)
        if not stats:
            match_info.append({"id": sid, "action": "skipped", "reason": "无法提取帧"})
            continue

        tgt_y = stats.get("Y", 128)
        tgt_r = stats.get("R", tgt_y)
        tgt_g = stats.get("G", tgt_y)
        tgt_b = stats.get("B", tgt_y)

        grade = {}

        if match_luma:
            # 亮度差 (0~255 scale) -> brightness 参数(-1~1 scale)
            # 参考亮度 - 目标亮度,正值表示目标需要提亮
            y_diff = (ref_y - tgt_y) / 255.0 * strength
            # 钳制到合理范围
            y_diff = max(-0.3, min(0.3, y_diff))
            if abs(y_diff) > 0.01:
                grade["brightness"] = round(y_diff, 4)

        if match_color_temp:
            # 色温校正:通过 colorbalance 调整 R/B 通道
            # R 通道差
            r_ratio = ref_r / max(ref_y, 1)  # 参考的红比例
            t_r_ratio = tgt_r / max(tgt_y, 1)  # 目标的红比例
            r_diff = (r_ratio - t_r_ratio) * 0.3 * strength
            r_diff = max(-0.2, min(0.2, r_diff))

            # B 通道差(蓝色变化=色温变化)
            b_ratio = ref_b / max(ref_y, 1)
            t_b_ratio = tgt_b / max(tgt_y, 1)
            b_diff = (b_ratio - t_b_ratio) * 0.3 * strength
            b_diff = max(-0.2, min(0.2, b_diff))

            # G 通道差(绿色=中间参考)
            g_ratio = ref_g / max(ref_y, 1)
            t_g_ratio = tgt_g / max(tgt_y, 1)
            g_diff = (g_ratio - t_g_ratio) * 0.15 * strength
            g_diff = max(-0.1, min(0.1, g_diff))

            # 只对中间调做色温偏移(不破坏阴影/高光特性)
            r_adj = round(r_diff, 4)
            g_adj = round(g_diff, 4)
            b_adj = round(b_diff, 4)
            if any(abs(v) >= 0.005 for v in (r_adj, g_adj, b_adj)):
                grade["midtones_rgb"] = f"{r_adj}:{g_adj}:{b_adj}"

        if not grade:
            match_info.append({"id": sid, "action": "skipped", "reason": "色差过小,无需校正"})
            continue

        grades[str(sid)] = grade
        diff_r = round(tgt_r - ref_r, 1)
        diff_g = round(tgt_g - ref_g, 1)
        diff_b = round(tgt_b - ref_b, 1)
        match_info.append({
            "id": sid,
            "action": "matched",
            "rgb_diff": f"R{diff_r:+.0f} G{diff_g:+.0f} B{diff_b:+.0f}",
            "applied": grade,
        })

    result = {
        "grade_json": grades,
        "match_info": match_info,
        "reference_id": reference_id,
        "total_segments": len(segments),
        "matched_count": sum(1 for m in match_info if m.get("action") == "matched"),
    }

    info_lines = [f"参考镜头: id={reference_id}"]
    for m in match_info:
        if m.get("action") == "reference":
            info_lines.append(f"  id={m['id']} -> 参考镜头")
        elif m.get("action") == "matched":
            info_lines.append(f"  id={m['id']} -> 已匹配 ({m['rgb_diff']})")
        else:
            info_lines.append(f"  id={m['id']} -> 跳过 ({m.get('reason','')})")

    grade_json_str = json.dumps(grades, ensure_ascii=False)
    return (
        f"✅ 色彩匹配完成\n"
        + "\n".join(info_lines)
        + f"\n\n匹配的 grade_json(可直接用于 render_final):\n{grade_json_str}"
    )

"""
调色 Agent — 专属调色决策 Agent
====================================
主 Agent 调这个工具来委托调色决策.

流程:
1. 提取每个镜头的代表帧
2. 定量分析(亮度,RGB 平衡,曝光分布)
3. VL 定性分析(曝光感,色温,肤色,风格建议)
4. 综合生成调色参数(逐镜头 + 跨镜头匹配)
5. 输出 grade_json 给主 Agent 用于 render_final

维护人: CherryClaw
"""
import json, os, subprocess, base64, hashlib, re, time
from pathlib import Path

_PROJECT_DIR = Path(__file__).parent.parent.parent

# ─── VL 调色分析系统提示 ──────────────────────────────────

_COLOR_SYSTEM_PROMPT = """你是 ClipMind 的调色专家.你的工作是通过画面分析给出精准的调色建议.

调色分析输出格式(严格 JSON):
{
    "shots": [
        {
            "shot_index": 0,
            "exposure": "欠曝/过曝/正常/lv",  // lv=level, 0-10
            "exposure_score": 5,   // 0=全黑 10=全白, 5=正常
            "brightness_correction": -0.15,  // brightness 调整值
            "contrast_rating": "低/适中/高",
            "contrast_correction": 0.0,  // contrast 调整
            "saturation_rating": "低/适中/高",
            "saturation_correction": 0.0,  // saturation 调整
            "color_temp": "暖/冷/中性",
            "color_temp_correction": "0,0,0",  // R,G,B 色温修正
            "color_cast": "无/偏绿/偏红/偏蓝/偏黄",
            "color_cast_correction": "0,0,0",  // 去色偏
            "skin_tone": "正常/偏黄/偏红/偏绿",
            "skin_tone_note": "",
            "mood": "温馨/冷峻/明亮/沉稳/自然/复古/清新",
            "suggested_preset": "暖色调/冷色调/日系清新/胶片感/提亮/无",
            "grade_params": {
                "brightness": 0.0,
                "contrast": 1.0,
                "saturation": 1.0,
                "shadows_rgb": "",
                "midtones_rgb": "",
                "highlights_rgb": ""
            },
            "notes": "整体正常,轻微提亮肤色即可"
        }
    ],
    "global_notes": "所有镜头白平衡一致,无需跨镜匹配.shot2略暗需单独提亮."
}

规则:
1. exposure: 5=完美, 0-3=严重欠曝, 7-10=过曝
2. brightness: 正值提亮, 负值压暗
3. color_temp_correction: 格式 "R,G,B" 正负数均可
4. suggested_preset: 从"暖色调/冷色调/日系清新/胶片感/提亮/复古/无"中选
5. grade_params 中的空字符串="" 表示不调整该项
6. 如果画面有人物,50% 以上的注意力放在肤色上
"""

# ─── VL 调色评估系统提示 ──────────────────────────────────

_COLOR_EVAL_PROMPT = """你是 ClipMind 的调色质检员.
给你一张调色前和调色后的画面对比,评估调色效果.

评估标准:
1. 曝光是否准确(不过曝不欠曝)
2. 白平衡是否自然
3. 肤色是否健康自然
4. 整体色彩是否协调

输出 JSON:
{
    "exposure_improved": true/false,
    "white_balance_improved": true/false,
    "skin_tone_improved": true/false,
    "overall_score": 0-10,
    "issues": ["问题1", "问题2"],
    "accept": true/false
}

accept=true 表示调色达标可以接受.
accept=false 需要继续调整.
"""


# ═══════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════


def _load_config() -> dict:
    """读取 API KEY 配置(优先环境变量,回退 config 模块)"""
    api_key = os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        try:
            from director.config import get_api_key as _cfg_get_key
            api_key = _cfg_get_key()
        except Exception:
            api_key = ""
    base_url = os.environ.get("LLM_BASE_URL",
                               "https://dashscope.aliyuncs.com/compatible-mode/v1")
    vl_model = os.environ.get("VL_MODEL", "qwen-vl-max")
    return {"api_key": api_key, "base_url": base_url, "vl_model": vl_model}


def _extract_frame_base64(video_path: str, time_pos: float) -> str:
    """
    从视频指定时间点提取一帧,返回 data:image/png;base64,....
    失败返回空字符串.
    """
    tmp_dir = os.path.join(_PROJECT_DIR, "_tmp_render")
    os.makedirs(tmp_dir, exist_ok=True)
    tag = hashlib.md5(f"{video_path}:{time_pos}".encode()).hexdigest()[:12]
    tmp_png = os.path.join(tmp_dir, f"color_frame_{tag}.png")

    r = subprocess.run([
        "ffmpeg", "-y", "-ss", str(time_pos), "-i", video_path,
        "-vframes", "1",
        "-vf", "scale=-2:460",  # 460p 足够 VL 分析
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


def _call_vl(messages: list, cfg: dict, temperature: float = 0.3) -> str:
    """
    调用 Qwen VL 模型.
    messages 格式: OpenAI 兼容的多模态格式.
    """
    from openai import OpenAI
    import httpx

    client = OpenAI(
        api_key=cfg["api_key"],
        base_url=cfg["base_url"],
        max_retries=2,
        timeout=httpx.Timeout(120.0),
    )

    try:
        resp = client.chat.completions.create(
            model=cfg["vl_model"],
            messages=messages,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content or ""
    except Exception as e:
        return ""


def _extract_color_metrics(video_path: str, time_pos: float) -> dict:
    """
    定量色彩分析 — 从帧数据计算曝光/RGB指标.
    不依赖 VL,纯计算.

    返回:
    {
        "y_mean": 0-255,        # 亮度均值
        "r_mean": 0-255,
        "g_mean": 0-255,
        "b_mean": 0-255,
        "exposure_score": 0-10, # 0=全黑 10=全白
        "rg_diff": float,       # R-G 差值, 正数偏暖
        "bg_diff": float,       # B-G 差值, 正数偏蓝
        "shadow_avg": 0-255,    # 暗部均值 (Y<64)
        "highlight_avg": 0-255  # 亮部均值 (Y>192)
    }
    """
    # 提取帧
    tmp_dir = os.path.join(_PROJECT_DIR, "_tmp_render")
    os.makedirs(tmp_dir, exist_ok=True)
    tag = hashlib.md5(f"{video_path}:{time_pos}".encode()).hexdigest()[:12]
    tmp_png = os.path.join(tmp_dir, f"metrics_{tag}.png")

    subprocess.run([
        "ffmpeg", "-y", "-ss", str(time_pos), "-i", video_path,
        "-vframes", "1", tmp_png,
    ], capture_output=True, timeout=30, check=False)

    if not os.path.exists(tmp_png):
        return {"error": "frame extraction failed"}

    try:
        from PIL import Image
        import numpy as np
        img = Image.open(tmp_png).convert("RGB")
        arr = np.array(img, dtype=np.float64)

        r = arr[:,:,0]; g = arr[:,:,1]; b = arr[:,:,2]
        y = 0.299 * r + 0.587 * g + 0.114 * b

        y_mean = float(y.mean())
        r_mean = float(r.mean())
        g_mean = float(g.mean())
        b_mean = float(b.mean())

        # 曝光评分 0-10
        exposure_score = (y_mean / 255.0) * 10.0

        # 色偏:R-G, B-G 差值
        rg_diff = r_mean - g_mean
        bg_diff = b_mean - g_mean

        # 暗部/亮部均值
        shadow_mask = y < 64
        highlight_mask = y > 192
        shadow_avg = float(y[shadow_mask].mean()) if shadow_mask.any() else 0.0
        highlight_avg = float(y[highlight_mask].mean()) if highlight_mask.any() else 255.0

        os.remove(tmp_png) if os.path.exists(tmp_png) else None

        return {
            "y_mean": round(y_mean, 1),
            "r_mean": round(r_mean, 1),
            "g_mean": round(g_mean, 1),
            "b_mean": round(b_mean, 1),
            "exposure_score": round(exposure_score, 1),
            "rg_diff": round(rg_diff, 1),
            "bg_diff": round(bg_diff, 1),
            "shadow_avg": round(shadow_avg, 1),
            "highlight_avg": round(highlight_avg, 1),
        }

    except ImportError:
        os.remove(tmp_png) if os.path.exists(tmp_png) else None
        return {"error": "PIL/numpy not available"}
    except Exception as e:
        try:
            os.remove(tmp_png)
        except Exception:
            pass
        return {"error": str(e)}


def _metrics_to_corrections(metrics: dict) -> dict:
    """
    根据定量指标生成调色建议(无需 VL).
    作为 VL 分析的补充和兜底.
    """
    corrections = {
        "brightness": 0.0,
        "contrast": 1.0,
        "saturation": 1.0,
        "midtones_rgb": "",
        "notes": "",
    }

    if "error" in metrics:
        return corrections

    y_mean = metrics.get("y_mean", 128)
    rg = metrics.get("rg_diff", 0)
    bg = metrics.get("bg_diff", 0)
    exp = metrics.get("exposure_score", 5)
    shadow = metrics.get("shadow_avg", 0)
    highlight = metrics.get("highlight_avg", 255)

    # 曝光修正
    if exp < 3.0:
        # 严重欠曝
        corrections["brightness"] = round((3.5 - exp) * 0.06, 3)
        corrections["brightness"] = min(corrections["brightness"], 0.35)
        corrections["notes"] += f"严重欠曝(exp={exp}),提亮{corrections['brightness']}."
    elif exp < 4.5:
        # 轻微欠曝
        corrections["brightness"] = round((4.5 - exp) * 0.04, 3)
        corrections["notes"] += f"轻微欠曝,提亮{corrections['brightness']}."
    elif exp > 7.0:
        # 过曝
        corrections["brightness"] = round((7.0 - exp) * 0.05, 3)
        corrections["brightness"] = max(corrections["brightness"], -0.3)
        corrections["notes"] += f"过曝(exp={exp}),压暗{corrections['brightness']}."

    # 对比度判断
    dr = highlight - shadow
    if dr < 80:
        corrections["contrast"] = 1.1
        corrections["notes"] += "对比度偏低,增加10%."
    elif dr > 200:
        corrections["contrast"] = 0.95
        corrections["notes"] += "对比度较高,降低5%."

    # 色温修正(根据 R-G, B-G 差值)
    if abs(rg) > 8 or abs(bg) > 8:
        # 有可见色偏
        r_corr = round(-rg * 0.003, 4)
        b_corr = round(-bg * 0.003, 4)
        corrections["midtones_rgb"] = f"{r_corr}:0:{b_corr}"
        if rg > 8:
            corrections["notes"] += f"偏暖(R-G={rg:.0f}),降暖{r_corr}."
        elif rg < -8:
            corrections["notes"] += f"偏冷(R-G={rg:.0f}),增暖{r_corr}."
        if bg > 8:
            corrections["notes"] += f"偏蓝(B-G={bg:.0f}),降蓝{b_corr}."
        elif bg < -8:
            corrections["notes"] += f"偏黄(B-G={bg:.0f}),增蓝{b_corr}."

    if not corrections["notes"]:
        corrections["notes"] = "曝光和色温正常,无需大幅调整."

    return corrections


# ═══════════════════════════════════════════════════════
#  主工具:run_color_grading
# ═══════════════════════════════════════════════════════


@tool(
    name="run_color_grading",
    description=(
        "【调色专用 Agent】分析素材并生成逐镜头调色方案."
        "定量分析亮度/曝光/色偏指标 + 可选 VL 感知分析曝光感/色温/肤色/氛围."
        "自动做跨镜头色彩匹配.返回 grade_json 直接用于 render_final."
        "主 Agent 只需要在编排完成后调一次这个工具,拿结果去渲染."
    ),
    phase="analyze",
    category="color",
    tags=["color"],
)
def run_color_grading(
    video_path: str,
    arrangement_json: str,
    style: str = "auto",
    use_vl: bool = True,
) -> str:
    """
    调色 Agent — 分析素材并生成精准调色方案.

    流程:
    1. 提取每个镜头的代表帧
    2. 定量色彩分析(曝光/RGB/色偏指标)
    3. 可选:VL 模型定性分析(感知色彩/肤色/氛围)
    4. 综合生成 grade_json(兼容 render_final 格式)
    5. 跨镜头色彩匹配(reduce shot-to-shot variation)

    Args:
        video_path: 源视频路径
        arrangement_json: 镜头编排 JSON
            [{"start": 0, "end": 30, "duration": 30}, ...]
        style: 风格偏好
            "auto" - 自动分析决定
            "warm" - 暖色调
            "cool" - 冷色调
            "bright" - 明亮
            "film" - 胶片感
            "fresh" - 日系清新
            "vintage" - 复古
        use_vl: 是否使用 VL 模型辅助分析(默认 true)

    Returns:
        grade_json 字符串(直接用于 render_final 的 grade_json 参数)
    """
    # ── 参数验证 ──
    if not os.path.exists(video_path):
        return json.dumps({"error": f"文件不存在: {video_path}"})

    arrangement = _parse_json(arrangement_json)
    if not arrangement:
        return json.dumps({"error": "空编排"})
    if isinstance(arrangement[0], (int, float)):
        from director.tools.analyze import _get_segments_cached
        segs = _get_segments_cached(video_path)
        id_map = {s["id"]: s for s in segs}
        resolved = []
        for cid in arrangement:
            seg = id_map.get(int(cid))
            if seg:
                resolved.append(seg)
        arrangement = resolved
    if not arrangement:
        return json.dumps({"error": "无可分析的镜头"})

    # ── 风格映射 ──
    style_presets = {
        "warm": "暖色调",
        "cool": "冷色调",
        "bright": "提亮",
        "film": "胶片感",
        "fresh": "日系清新",
        "vintage": "复古",
    }

    # ── 配置 ──
    cfg = _load_config()
    if not cfg.get("api_key"):
        use_vl = False  # 没有 API key 就不调 VL

    # ── 逐镜头分析 ──
    grades = {}
    shot_metrics_list = []
    shot_vl_list = []

    for i, shot in enumerate(arrangement):
        start = shot.get("start", 0)
        end = shot.get("end", start + shot.get("duration", 30))
        mid_point = (start + end) / 2

        # 定量分析
        metrics = _extract_color_metrics(video_path, mid_point)
        shot_metrics_list.append(metrics)
        corrections = _metrics_to_corrections(metrics)

        # VL 定性分析
        vl_result = None
        if use_vl:
            frame_b64 = _extract_frame_base64(video_path, mid_point)
            if frame_b64:
                vl_result = _vl_analyze_frame(frame_b64, cfg)
                shot_vl_list.append(vl_result)
                # VL 分析结果覆盖定量分析
                if vl_result and "grade_params" in vl_result:
                    vp = vl_result["grade_params"]
                    if vp.get("brightness") != 0:
                        corrections["brightness"] = vp["brightness"]
                    if vp.get("contrast") != 1.0:
                        corrections["contrast"] = vp["contrast"]
                    if vp.get("saturation") != 1.0:
                        corrections["saturation"] = vp["saturation"]
                    if vp.get("midtones_rgb"):
                        corrections["midtones_rgb"] = vp["midtones_rgb"]

                    # VL 备注
                    if vl_result.get("notes"):
                        corrections["notes"] = vl_result["notes"]
        else:
            shot_vl_list.append(None)

        # 构建 grade 参数
        grade = {}
        if corrections["brightness"] != 0:
            # 用 eq 滤镜提亮度
            grade["brightness"] = corrections["brightness"]
        if corrections["contrast"] != 1.0:
            grade["contrast"] = corrections["contrast"]
        if corrections["saturation"] != 1.0:
            grade["saturation"] = corrections["saturation"]
        if corrections["midtones_rgb"]:
            grade["midtones_rgb"] = corrections["midtones_rgb"]

        # 风格预设覆盖
        if style in style_presets:
            preset_name = style_presets[style]
            preset = _COLOR_PRESETS.get(preset_name, {})
            if preset:
                for k, v in preset.items():
                    if k != "description":
                        grade[k] = v

        grades[str(i)] = grade

    # ── 跨镜头色彩匹配 ──
    # 如果 >= 2 个镜头且都有 colorbalance 参数,做跨镜匹配
    matched = False
    if len(arrangement) >= 2:
        # 用 match_color 做跨镜头匹配
        try:
            mc_result = match_color(video_path, arrangement_json, reference_id=0)
            mc_data = _parse_json(mc_result)
            if mc_data and isinstance(mc_data, dict):
                for shot_id, mc_grade in mc_data.items():
                    if shot_id not in grades:
                        grades[shot_id] = {}
                    # 只取 colorbalance 相关参数,不覆盖 brightness/contrast
                    for k in ["shadows_rgb", "midtones_rgb", "highlights_rgb"]:
                        if k in mc_grade and mc_grade[k]:
                            grades[shot_id][k] = mc_grade[k]
                    matched = True
        except Exception:
            pass

    # ── 构建结果 ──
    result = {
        "grade_json": grades,
        "shot_count": len(arrangement),
    }

    # VL 分析摘要
    vl_summaries = []
    for i, vl in enumerate(shot_vl_list):
        if vl:
            vl_summaries.append({
                "shot": i,
                "exposure": vl.get("exposure", ""),
                "color_temp": vl.get("color_temp", ""),
                "mood": vl.get("mood", ""),
                "skin_tone": vl.get("skin_tone", ""),
            })
    if vl_summaries:
        result["vl_analysis"] = vl_summaries

    # 备注
    notes = []
    for i, c in enumerate([_metrics_to_corrections(m) for m in shot_metrics_list]):
        if c.get("notes"):
            notes.append(f"shot{i}: {c['notes']}")
    if notes:
        result["correction_notes"] = notes

    # 跨镜匹配状态
    if matched:
        result["cross_shot_matched"] = True

    return json.dumps(result, ensure_ascii=False, indent=2)


def _vl_analyze_frame(frame_b64: str, cfg: dict) -> dict:
    """
    调 VL 分析单帧画面色彩.
    返回调色建议 dict.
    """
    if not frame_b64:
        return None

    messages = [
        {
            "role": "system",
            "content": _COLOR_SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": frame_b64},
                },
                {
                    "type": "text",
                    "text": "分析这个画面的曝光,白平衡,色彩,肤色(如果有人物)."
                            "输出完整的调色建议 JSON.",
                },
            ],
        },
    ]

    content = _call_vl(messages, cfg)
    if not content:
        return None

    return _parse_json(content)


# ═══════════════════════════════════════════════════════
#  辅助工具:color_analyze
# ═══════════════════════════════════════════════════════


@tool(
    name="color_analyze",
    description=(
        "【独立色彩分析】分析单帧画面的色彩指标."
        "定量返回亮度/RGB/色偏数据,可选 VL 感知(曝光感,色温,肤色,氛围)."
        "AI 可用此工具预览分析结果后再决定调色方案."
    ),
    phase="all",
    category="color",
    tags=["color"],
)
def color_analyze(
    video_path: str,
    time_pos: float = 1.0,
    use_vl: bool = True,
) -> str:
    """
    单帧色彩分析 — 定量 + 可选的 VL 定性分析.

    返回分析 JSON:
    - metrics: 曝光/RGB/色偏 定量指标
    - corrections: 基于指标的调色建议
    - vl_analysis: VL 感知分析(开启 use_vl 时)

    Args:
        video_path: 视频路径
        time_pos: 分析时间点(秒)
        use_vl: 是否使用 VL 感知分析

    Returns:
        分析 JSON 字符串
    """
    if not os.path.exists(video_path):
        return json.dumps({"error": f"文件不存在: {video_path}"})

    # 定量分析
    metrics = _extract_color_metrics(video_path, time_pos)
    corrections = _metrics_to_corrections(metrics)

    result = {
        "metrics": metrics,
        "corrections": corrections,
    }

    # VL 分析
    if use_vl:
        cfg = _load_config()
        if cfg.get("api_key"):
            frame_b64 = _extract_frame_base64(video_path, time_pos)
            if frame_b64:
                vl_result = _vl_analyze_frame(frame_b64, cfg)
                if vl_result:
                    result["vl_analysis"] = vl_result

    return json.dumps(result, ensure_ascii=False, indent=2)


# 工具已通过 @tool 装饰器自动注册到 Registry
