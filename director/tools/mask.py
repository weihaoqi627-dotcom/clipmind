"""
蒙版工具 — 裁剪和遮罩阶段
========================
AI 通过调用这些工具来应用视频蒙版(裁剪形状),转场效果.
"""
import json, os, subprocess, re, hashlib, base64
from pathlib import Path
from typing import Optional

from director.registry import tool
from director.config import OUTPUT_DIR as _OUTPUT_DIR

_PROJECT_DIR = Path(__file__).parent.parent.parent

# --- 内置蒙版 ---

_INTERNAL_MASKS = {
    "线性": "从左到右渐变透明",
    "镜面": "中间清晰,边缘渐变透明(类似镜面反射)",
    "圆形": "圆形区域内清晰,外部透明",
    "矩形": "矩形区域内清晰,外部透明",
    "爱心": "爱心形状区域内清晰",
    "星形": "星形形状区域内清晰",
}


@tool(
    name="list_masks",
    description="列出所有可用的视频蒙版/裁剪形状",
    phase="plan",
    category="mask",
    tags=["mask", "list", "shape"],
    group="遮罩与稳定",
)
def list_masks() -> str:
    """列出所有可用的视频蒙版/裁剪形状.

    Returns:
        JSON 格式的蒙版列表
    """
    masks = []
    for name, desc in _INTERNAL_MASKS.items():
        masks.append({
            "name": name,
            "description": desc,
            "type": "内置",
        })
    return json.dumps(masks, ensure_ascii=False, indent=2)


@tool(
    name="apply_mask",
    description="[暂缓使用] 对视频应用蒙版效果(如线性,圆形,爱心等).基于 ffmpeg geq 逐像素运算,CPU 极端缓慢,仅适合极短视频或待未来自研渲染引擎实装后再用.",
    phase="edit",
    category="mask",
    tags=["mask", "apply", "shape"],
    group="遮罩与稳定",
)
def apply_mask(
    video_path: str,
    mask_name: str,
    blur_radius: float = 0.0,
    invert: bool = False,
    output_path: str = "",
) -> str:
    """
    对视频应用蒙版/裁剪效果.

    Args:
        video_path: 输入视频路径
        mask_name: 蒙版名称(从 list_masks 获取)
        blur_radius: 边缘模糊半径(像素,0=不模糊,默认0)
        invert: 是否反转蒙版(默认False)
        output_path: 输出路径(可选)

    Returns:
        结果信息
    """
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"

    if mask_name not in _INTERNAL_MASKS:
        return f"未知蒙版: {mask_name}.可用: {', '.join(_INTERNAL_MASKS.keys())}"

    import hashlib
    if not output_path:
        hash_s = hashlib.md5(video_path.encode()).hexdigest()[:8]
        output_path = os.path.join(str(_OUTPUT_DIR), f"mask_{mask_name}_{hash_s}.mp4")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # 获取视频尺寸(用 ffmpeg -i 替代损坏的 ffprobe)
    w, h = _get_video_dimensions(video_path)
    if not w or not h:
        return "无法获取视频尺寸"

    # 根据蒙版类型构建 ffmpeg filter
    filter_str = _build_mask_filter(mask_name, w, h, blur_radius, invert)
    if not filter_str:
        return f"蒙版 {mask_name} 暂不支持"

    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-filter_complex", filter_str,
        "-map", "[out]", "-c:v", "libx264", "-crf", "20",
        "-c:a", "copy", "-movflags", "+faststart", output_path,
    ]
    subprocess.run(cmd, capture_output=True, timeout=600, check=False)

    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        size = os.path.getsize(output_path) / (1024 * 1024)
        invert_str = "(已反转)" if invert else ""
        return f"[OK] 已应用「{mask_name}」蒙版{invert_str}: {output_path} ({size:.1f}MB)"
    return "[FAIL] 蒙版应用失败"


def _build_mask_filter(mask_name: str, w: int, h: int,
                       blur_radius: float = 0.0, invert: bool = False) -> str:
    """构建 ffmpeg filter_complex:RGB multiply 合成

    format=rgb24 -> split -> geq(R=G=B=遮罩值) -> blend=multiply
    255(白)=原画不变, 0(黑)=全黑, 中间值=半透明
    """
    cx, cy = w // 2, h // 2
    inv = "255-" if invert else ""
    sn = min(w, h)

    # 核心表达式(仅表达式内容,不含 geq 命令和格式参数)
    exprs = {
        "线性": f"{inv}255*(Y/{h})",
        "镜面": f"{inv}255*(1-min(abs(X-{cx}),abs(Y-{cy}))/({sn}/2))",
        "圆形": f"{inv}if(gt(pow(X-{cx},2)+pow(Y-{cy},2),pow({sn}/2*0.4,2)),0,255)",
        "矩形": f"{inv}if(lt(abs(X-{cx}),{w//3})*lt(abs(Y-{cy}),{h//3}),255,0)",
        "爱心": _build_heart_expr(cx, cy, inv),
        "星形": _build_star_expr(cx, cy, inv),
    }

    expr = exprs.get(mask_name)
    if not expr:
        return ""

    blur_str = f",gblur=sigma={blur_radius}" if blur_radius > 0 else ""
    return (
        f"[0:v]format=rgb24,split=2[orig][mask_in];"
        f"[mask_in]geq=r='{expr}':g='{expr}':b='{expr}'{blur_str}[mask_rgb];"
        f"[orig][mask_rgb]blend=all_mode='multiply':all_opacity=1[out]"
    )


def _build_heart_expr(cx: int, cy: int, inv: str) -> str:
    """爱心形状蒙版表达式"""
    return (
        f"{inv}if(lt(pow(X-{cx},2)+pow(Y-{cy}+abs(X-{cx})/3,2),"
        f"pow({min(cx,cy)}/2*0.3,2)),255,0)"
    )


def _build_star_expr(cx: int, cy: int, inv: str) -> str:
    """五角星形状蒙版表达式(简化为菱形叠加)"""
    return (
        f"{inv}if(lt(abs(X-{cx})+abs(Y-{cy}),{min(cx,cy)}/2*0.5),255,0)"
    )


@tool(
    name="crop_video",
    description="裁剪视频画面(切掉边缘不需要的部分).支持草稿模式(提供 draft_id 自动写入草稿).",
    phase="edit",
    category="mask",
    tags=["crop", "trim", "cut"],
    group="画面与场景",
)
def crop_video(
    video_path: str,
    x: int = 0,
    y: int = 0,
    width: int = 0,
    height: int = 0,
    output_path: str = "",
    draft_id: str = "",
    clip_id: int = 0,
) -> str:
    """
    裁剪视频画面.

    Args:
        video_path: 输入视频路径
        x: 起始X坐标(默认0)
        y: 起始Y坐标(默认0)
        width: 裁剪宽度(默认0=视频原始宽度)
        height: 裁剪高度(默认0=视频原始高度)
        output_path: 输出路径(可选)
        draft_id: 草稿 ID(可选),提供后自动写入草稿
        clip_id: 片段 ID(配合 draft_id 使用),默认 0

    Returns:
        结果信息
    """
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"

    # 获取视频尺寸(用 ffmpeg -i 替代损坏的 ffprobe)
    vw, vh = _get_video_dimensions(video_path)
    if not vw or not vh:
        return "无法获取视频尺寸"

    cw = width if width > 0 else vw
    ch = height if height > 0 else vh

    import hashlib
    if not output_path:
        hash_s = hashlib.md5(video_path.encode()).hexdigest()[:8]
        output_path = os.path.join(str(_OUTPUT_DIR), f"crop_{hash_s}.mp4")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", f"crop={cw}:{ch}:{x}:{y}",
        "-c:v", "libx264", "-crf", "20",
        "-c:a", "copy", "-movflags", "+faststart", output_path,
    ]
    subprocess.run(cmd, capture_output=True, timeout=600, check=False)

    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        size = os.path.getsize(output_path) / (1024 * 1024)
        # ── 写草稿 ──
        draft_msg = ""
        if draft_id:
            from director.draft import _write_to_draft
            draft_msg = _write_to_draft(
                draft_id, clip_id, "crop",
                {"x": x, "y": y, "w": cw, "h": ch},
                label="裁剪完成",
            )
        return f"[OK] 裁剪完成 ({cw}x{ch}): {output_path} ({size:.1f}MB)"
    return "[FAIL] 裁剪失败"


def _get_video_dimensions(video_path: str):
    """用 ffmpeg -i 获取视频分辨率(替代损坏的 ffprobe)"""
    import re
    r = subprocess.run(["ffmpeg", "-i", video_path],
                       capture_output=True, timeout=30)
    output = (r.stdout + r.stderr).decode("utf-8", errors="replace")
    m = re.search(r",\s*(\d{3,})x(\d{3,})", output)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


# ─── 色度抠像辅助函数 ───────────────────────────────────────


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


def _has_nvenc() -> bool:
    """检测 NVENC 编码器是否可用"""
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, timeout=10,
        )
        return "h264_nvenc" in (r.stdout + r.stderr).decode("utf-8", errors="replace")
    except Exception:
        return False


def _cleanup_tmp(*paths: str):
    """清理临时文件"""
    for p in paths:
        if not p:
            continue
        try:
            if os.path.isfile(p):
                os.remove(p)
        except Exception:
            pass


def _ensure_output_dir():
    """确保输出目录存在"""
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _build_colorkey_filter(color: str, similarity: float, blend: float) -> str:
    """构建 colorkey filter 字符串"""
    return f"colorkey=color={color}:similarity={similarity}:blend={blend}"


# ─── 色度抠像工具函数 ────────────────────────────────────────


@tool(
    name="apply_chroma_key",
    description="对视频进行色度抠像(绿幕/蓝幕抠图),可选替换背景.支持草稿模式(提供 draft_id 自动写入草稿).支持绿幕(green),蓝幕(blue)或自定义颜色",
    phase="edit",
    category="mask",
    tags=["chroma_key", "greenscreen", "keying"],
    group="遮罩与稳定",
)
def apply_chroma_key(
    video_path: str,
    color: str = "green",
    similarity: float = 0.3,
    blend: float = 0.1,
    background_path: str = "",
    output_path: str = "",
    draft_id: str = "",
    clip_id: int = 0,
) -> str:
    """
    对视频进行色度抠像(绿幕/蓝幕抠图),可选替换背景.

    Args:
        video_path: 源视频路径(带绿幕/蓝幕的视频)
        color: 抠像颜色 "green"(绿幕),"blue"(蓝幕) 或自定义 "#00ff00",默认 "green"
        similarity: 颜色相似度 0~1,越大抠得越多,默认 0.3
        blend: 边缘混合程度 0~1,默认 0.1
        background_path: 可选,替换背景的视频/图片路径
        output_path: 输出路径(可选,自动生成)

    Returns:
        结果信息(含输出路径和文件大小)
    """
    # ── 校验输入 ──
    if not os.path.exists(video_path):
        return f"源文件不存在: {video_path}"

    if background_path and not os.path.exists(background_path):
        return f"背景文件不存在: {background_path}"

    # ── 确定编码器 ──
    nvenc_ok = _has_nvenc()
    vcodec = "h264_nvenc" if nvenc_ok else "libx264"
    vparams = ["-qp", "18", "-preset", "p4"] if nvenc_ok else ["-crf", "18", "-preset", "medium"]

    # ── 获取视频尺寸 ──
    vw, vh = _get_video_dimensions(video_path)

    # ── 输出路径 ──
    _ensure_output_dir()
    raw = hashlib.md5(f"{video_path}{color}{similarity}{blend}{background_path}".encode()).hexdigest()[:10]

    has_background = bool(background_path and os.path.exists(background_path))

    if not output_path:
        if has_background:
            output_path = str(_OUTPUT_DIR / f"chroma_{raw}.mp4")
        else:
            output_path = str(_OUTPUT_DIR / f"chroma_{raw}.mov")

    # ── 构建 filter_complex ──
    ck_filter = _build_colorkey_filter(color, similarity, blend)

    if has_background:
        # 有背景:前景抠像 + 背景缩放 + overlay
        filter_complex = (
            f"[0:v]format=rgba,{ck_filter}[fg]; "
            f"[1:v]scale={vw}:{vh}[bg]; "
            f"[bg][fg]overlay=format=auto[vout]"
        )
        input_args = ["-i", video_path, "-i", background_path]
    else:
        # 无背景:只输出透明背景 RGBA
        filter_complex = f"[0:v]format=rgba,{ck_filter}[vout]"
        input_args = ["-i", video_path]

    # ── 音频处理 ──
    has_audio = False
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", video_path],
            capture_output=True, timeout=30,
        )
        has_audio = bool(re.search(r"Stream.*Audio", (r.stdout + r.stderr).decode("utf-8", errors="replace")))
    except Exception:
        pass

    # ── 构建命令 ──
    cmd = ["ffmpeg", "-y", "-hide_banner"]
    cmd.extend(input_args)
    cmd.extend(["-filter_complex", filter_complex])
    cmd.extend(["-map", "[vout]"])

    if has_background:
        cmd.extend(["-c:v", vcodec])
        cmd.extend(vparams)
        if has_audio:
            cmd.extend(["-map", "0:a?"])
            cmd.extend(["-c:a", "copy"])
    else:
        cmd.extend(["-c:v", "png"])
        if has_audio:
            cmd.extend(["-map", "0:a?"])
            cmd.extend(["-c:a", "copy"])

    cmd.extend(["-progress", "pipe:1"])
    cmd.append(output_path)

    # ── 执行 ──
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=3600)
        if r.returncode != 0:
            err = r.stderr.decode("utf-8", errors="replace")[-500:]
            return f"抠像失败: {err}"
    except subprocess.TimeoutExpired:
        return "抠像超时(>1小时)"
    except Exception as e:
        return f"抠像异常: {e}"

    # ── 结果 ──
    if os.path.exists(output_path):
        size_mb = os.path.getsize(output_path) / (1024 * 1024)

        # ── 写草稿 ──
        if draft_id:
            from director.draft import _write_to_draft
            _write_to_draft(
                draft_id, clip_id, "chromakey",
                {"color": color, "similarity": similarity, "blend": blend,
                 "background_path": background_path},
                label="抠像完成",
            )

        info = (
            f"抠像完成\n"
            f"输出: {output_path}\n"
            f"大小: {size_mb:.1f} MB\n"
            f"颜色: {color}, 相似度: {similarity}, 混合: {blend}\n"
            f"编码: {'NVENC' if nvenc_ok and has_background else vcodec}\n"
            f"背景: {'有' if has_background else '无(透明通道)'}"
        )
        return info
    else:
        return "抠像失败:输出文件未生成"


@tool(
    name="preview_chroma_key",
    description="在指定时间点预览色度抠像效果,返回 data:image/png;base64 图片供评估",
    phase="edit",
    category="mask",
    tags=["chroma_key", "preview", "keying"],
    group="遮罩与稳定",
)
def preview_chroma_key(
    video_path: str,
    color: str = "green",
    similarity: float = 0.3,
    blend: float = 0.1,
    background_path: str = "",
    time_pos: float = 1.0,
) -> str:
    """
    在指定时间点预览色度抠像效果,返回 base64 编码的 PNG 图片.

    Args:
        video_path: 源视频路径
        color: 抠像颜色 "green","blue" 或自定义 "#00ff00"
        similarity: 颜色相似度 0~1
        blend: 边缘混合程度 0~1
        background_path: 可选背景路径
        time_pos: 预览时间点(秒),默认 1.0

    Returns:
        data:image/png;base64,... 供 VL 模型评估
    """
    if not os.path.exists(video_path):
        return f"源文件不存在: {video_path}"

    if background_path and not os.path.exists(background_path):
        return f"背景文件不存在: {background_path}"

    vw, vh = _get_video_dimensions(video_path)
    ck_filter = _build_colorkey_filter(color, similarity, blend)

    has_background = bool(background_path and os.path.exists(background_path))

    if has_background:
        filter_complex = (
            f"[0:v]format=rgba,{ck_filter}[fg]; "
            f"[1:v]scale={vw}:{vh}[bg]; "
            f"[bg][fg]overlay=format=auto[vout]"
        )
        input_args = ["-i", video_path, "-i", background_path]
    else:
        filter_complex = f"[0:v]format=rgba,{ck_filter}[vout]"
        input_args = ["-i", video_path]

    cmd = ["ffmpeg", "-y", "-hide_banner"]
    cmd.extend(["-ss", str(time_pos)])
    cmd.extend(input_args)
    cmd.extend(["-filter_complex", filter_complex])
    cmd.extend(["-map", "[vout]"])
    cmd.extend(["-vframes", "1"])
    cmd.extend(["-f", "image2pipe", "-vcodec", "png"])
    cmd.append("-")

    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60)
        if r.returncode != 0 or len(r.stdout) == 0:
            err = r.stderr.decode("utf-8", errors="replace")[-300:]
            return f"预览失败: {err}"
        b64 = base64.b64encode(r.stdout).decode("utf-8")
        return f"data:image/png;base64,{b64}"
    except subprocess.TimeoutExpired:
        return "预览超时(>60秒)"
    except Exception as e:
        return f"预览异常: {e}"


# ─── 遮罩生成工具(Phase 2) ─────────────────────────────


@tool(
    name="create_color_mask",
    description="基于颜色生成遮罩视频.白色=匹配颜色的像素,黑色=不匹配.生成的遮罩可直接用于调色工具的 mask_path 参数,实现区域调色",
    phase="edit",
    category="mask",
    tags=["mask", "color", "key"],
    group="遮罩与稳定",
)
def create_color_mask(
    video_path: str,
    target_color: str = "#00FF00",
    similarity: float = 0.3,
    blend: float = 0.1,
    invert: bool = False,
    blur_radius: float = 0.0,
    output_path: str = "",
) -> str:
    """
    基于颜色生成遮罩视频.白色区域=匹配目标颜色的像素,黑色区域=不匹配.
    生成的遮罩可直接用于调色工具的 mask_path 参数,实现区域调色.

    Args:
        video_path: 输入视频路径
        target_color: 目标颜色,#RRGGBB 格式(如 #4488FF)或颜色名 green/blue/red 等
        similarity: 颜色相似度 0~1,越大匹配范围越宽,默认 0.3
        blend: 边缘混合程度 0~1,默认 0.1
        invert: 是否反转(True=选中非目标颜色区域)
        blur_radius: 边缘模糊半径(像素,0=不模糊),默认 0
        output_path: 输出遮罩路径(可选)

    Returns:
        结果信息
    """
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"

    if not output_path:
        raw = hashlib.md5(f"{video_path}color{target_color}{similarity}{blend}{invert}{blur_radius}".encode()).hexdigest()[:10]
        output_path = str(_OUTPUT_DIR / f"mask_color_{raw}.mp4")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # 规范化颜色格式:去掉 0x 前缀,保证 # 开头
    color_arg = target_color
    if color_arg.startswith("0x"):
        color_arg = "#" + color_arg[2:]
    if color_arg.startswith("#") and len(color_arg) == 7:
        pass  # 标准 #RRGGBB 格式
    elif len(color_arg) == 6 and all(c in "0123456789abcdefABCDEF" for c in color_arg):
        color_arg = "#" + color_arg

    # colorkey: 匹配区域 alpha=0 -> alphaextract 得黑(0) -> negate 得白(255)
    # invert=True: 跳过 negate
    negate_str = "" if invert else ",negate"
    blur_filter = f",gblur=sigma={blur_radius}" if blur_radius > 0 else ""

    filter_str = (
        f"[0:v]format=rgba,"
        f"colorkey=color={color_arg}:similarity={similarity}:blend={blend},"
        f"alphaextract,format=gray{negate_str}{blur_filter}[out]"
    )

    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-filter_complex", filter_str,
        "-map", "[out]",
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-an", output_path,
    ]
    subprocess.run(cmd, capture_output=True, timeout=300, check=False)

    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        size = os.path.getsize(output_path) / (1024 * 1024)
        inv_str = "(反转)" if invert else ""
        return f"[OK] 颜色遮罩生成完成{inv_str}: {output_path} ({size:.1f}MB)"
    return "[FAIL] 颜色遮罩生成失败"


@tool(
    name="create_geometry_mask",
    description="生成几何形状遮罩,类似 DaVinci Resolve 的 Power Window.白色=形状内部,黑色=外部.支持圆形/矩形/椭圆,带羽化.生成的遮罩可直接用于调色工具的 mask_path 参数",
    phase="edit",
    category="mask",
    tags=["mask", "geometry", "shape"],
    group="遮罩与稳定",
)
def create_geometry_mask(
    video_path: str,
    shape: str = "circle",
    center_x: int = -1,
    center_y: int = -1,
    radius: int = 0,
    width: int = 0,
    height: int = 0,
    feather: float = 0.0,
    invert: bool = False,
    output_path: str = "",
) -> str:
    """
    生成几何形状遮罩(类似 DaVinci Resolve 的 Power Window).
    白色区域=形状内部,黑色区域=形状外部.
    生成的遮罩可直接用于调色工具的 mask_path 参数.

    Args:
        video_path: 参考视频路径(用于获取分辨率,不影响遮罩内容)
        shape: 形状类型: "circle"(圆形) / "rectangle"(矩形) / "ellipse"(椭圆)
        center_x: 中心X坐标(-1=自动居中)
        center_y: 中心Y坐标(-1=自动居中)
        radius: 圆形半径/椭圆X轴半径(像素,0=自动为短边的 1/4)
        width: 矩形或椭圆宽度(像素,0=自动为视频宽度 1/2)
        height: 矩形或椭圆高度(像素,0=自动为视频高度 1/2)
        feather: 边缘羽化半径(像素,0=硬边),默认 0
        invert: 是否反转遮罩
        output_path: 输出遮罩路径(可选)

    Returns:
        结果信息
    """
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"

    vw, vh = _get_video_dimensions(video_path)
    if not vw or not vh:
        return "无法获取视频尺寸"

    cx = vw // 2 if center_x <= 0 else center_x
    cy = vh // 2 if center_y <= 0 else center_y
    short_side = min(vw, vh)

    if not output_path:
        raw = hashlib.md5(f"{video_path}{shape}{cx}{cy}{feather}{invert}".encode()).hexdigest()[:10]
        output_path = str(_OUTPUT_DIR / f"mask_{shape}_{raw}.mp4")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # 用 geq 生成硬边形状,可选 gblur 做羽化
    if shape == "circle":
        r = short_side // 4 if radius <= 0 else radius
        geq_expr = f"if(gt(pow(X-{cx},2)+pow(Y-{cy},2), pow({r},2)), 0, 255)"
    elif shape == "rectangle":
        rw = vw // 2 if width <= 0 else width
        rh = vh // 2 if height <= 0 else height
        geq_expr = f"if(lt(abs(X-{cx}),{rw//2})*lt(abs(Y-{cy}),{rh//2}), 255, 0)"
    elif shape == "ellipse":
        rx = short_side // 4 if radius <= 0 else radius
        ry = rx * vh // vw if height <= 0 else height
        geq_expr = f"if(lt(pow((X-{cx})/{rx},2)+pow((Y-{cy})/{ry},2), 1), 255, 0)"
    else:
        return f"不支持的形状: {shape},可选: circle, rectangle, ellipse"

    # 构建 filter
    filter_parts = [f"geq=r='{geq_expr}':g='{geq_expr}':b='{geq_expr}'"]
    if feather > 0:
        filter_parts.append(f"gblur=sigma={feather}")
    if invert:
        filter_parts.append("geq=r='255-X':g='255-X':b='255-X'")
    filter_str = ",".join(filter_parts)

    # 输入:用 color 源生成纯黑画面 + 遮罩 overlay,避免需要实际视频输入
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=black:s={vw}x{vh}:d=1",
        "-filter_complex",
        f"[0:v]{filter_str}[out]",
        "-map", "[out]",
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-an", output_path,
    ]
    subprocess.run(cmd, capture_output=True, timeout=120, check=False)

    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        size = os.path.getsize(output_path) / (1024 * 1024)
        inv_str = "(反转)" if invert else ""
        feather_str = f",羽化={feather}px" if feather > 0 else ""
        return f"[OK] 几何遮罩 [{shape}]{inv_str}{feather_str}: {output_path} ({size:.1f}MB)"
    return "[FAIL] 几何遮罩生成失败"


@tool(
    name="create_luminance_mask",
    description="基于亮度生成遮罩.白色=在指定亮度范围内的像素,黑色=范围外.生成的遮罩可直接用于调色工具的 mask_path 参数",
    phase="edit",
    category="mask",
    tags=["mask", "luminance", "brightness"],
    group="遮罩与稳定",
)
def create_luminance_mask(
    video_path: str,
    threshold: float = 0.5,
    tolerance: float = 0.3,
    invert: bool = False,
    blur_radius: float = 0.0,
    output_path: str = "",
) -> str:
    """
    基于亮度生成遮罩.白色区域=在指定亮度范围内的像素.
    生成的遮罩可直接用于调色工具的 mask_path 参数.

    Args:
        video_path: 输入视频路径
        threshold: 亮度目标值 0~1(0.5=中间灰),默认 0.5
        tolerance: 容差 0~1,亮度在 [threshold-tolerance, threshold+tolerance]
                   范围内的像素被选中,默认 0.3
        invert: 是否反转遮罩
        blur_radius: 边缘模糊半径(像素,0=不模糊),默认 0
        output_path: 输出遮罩路径(可选)

    Returns:
        结果信息
    """
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"

    if not output_path:
        raw = hashlib.md5(f"{video_path}lum{threshold}{tolerance}{invert}{blur_radius}".encode()).hexdigest()[:10]
        output_path = str(_OUTPUT_DIR / f"mask_lum_{raw}.mp4")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # lumakey 输出:匹配区域 alpha=0(透明),不匹配区域 alpha=255
    # -> alphaextract 得 黑=匹配, 白=不匹配
    # -> negate 得 白=匹配, 黑=不匹配
    negate_need = "" if invert else ",negate"
    blur_filter = f",gblur=sigma={blur_radius}" if blur_radius > 0 else ""

    filter_str = (
        f"[0:v]format=yuva420p,"
        f"lumakey=threshold={threshold}:tolerance={tolerance},"
        f"alphaextract,format=gray{negate_need}{blur_filter}[out]"
    )

    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-filter_complex", filter_str,
        "-map", "[out]",
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-an", output_path,
    ]
    subprocess.run(cmd, capture_output=True, timeout=300, check=False)

    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        size = os.path.getsize(output_path) / (1024 * 1024)
        inv_str = "(反转)" if invert else ""
        return (f"[OK] 亮度遮罩生成完成{inv_str} "
                f"(threshold={threshold}, tolerance={tolerance}): "
                f"{output_path} ({size:.1f}MB)")
    return "[FAIL] 亮度遮罩生成失败"


# 工具已通过 @tool 装饰器自动注册到 Registry
