"""
动画工具 — 关键帧动画系统
=========================
为视频应用关键帧驱动的动画(Ken Burns 缩放,位移,旋转,透明度),
同时支持叠加图层(图片/文字渲染图)的关键帧动画.

主视频通过 scale + crop 实现缩放和平移,
叠加图层通过独立的 filter chain 实现缩放 + 旋转 + 透明度 + 位置.
"""
import json, os, subprocess, re, base64, hashlib, shutil
from pathlib import Path

from director.registry import tool

_PROJECT_DIR = Path(__file__).parent.parent.parent

# ═══════════════════════════════════════════════════════
#  动画模板
# ═══════════════════════════════════════════════════════

_ANIMATION_TEMPLATES = {
    "slide_in_left": {
        "description": "元素从左侧滑入画面",
        "keyframes": [
            {"t": 0.0, "x": -500, "y": 0, "scale": 1.0, "opacity": 0.0, "rotation": 0},
            {"t": 0.6, "x": 0,   "y": 0, "scale": 1.0, "opacity": 1.0, "rotation": 0},
        ],
    },
    "slide_in_right": {
        "description": "元素从右侧滑入画面",
        "keyframes": [
            {"t": 0.0, "x": 500, "y": 0, "scale": 1.0, "opacity": 0.0, "rotation": 0},
            {"t": 0.6, "x": 0,   "y": 0, "scale": 1.0, "opacity": 1.0, "rotation": 0},
        ],
    },
    "zoom_in_slow": {
        "description": "缓慢推近(Ken Burns 风格慢速变焦)",
        "keyframes": [
            {"t": 0.0, "x": 0, "y": 0, "scale": 1.0,  "opacity": 1.0, "rotation": 0},
            {"t": 5.0, "x": 0, "y": 0, "scale": 1.15, "opacity": 1.0, "rotation": 0},
        ],
    },
    "zoom_in_fast": {
        "description": "快速推近",
        "keyframes": [
            {"t": 0.0, "x": 0, "y": 0, "scale": 1.0, "opacity": 1.0, "rotation": 0},
            {"t": 1.0, "x": 0, "y": 0, "scale": 1.4, "opacity": 1.0, "rotation": 0},
        ],
    },
    "fade_in": {
        "description": "透明度淡入",
        "keyframes": [
            {"t": 0.0, "x": 0, "y": 0, "scale": 1.0, "opacity": 0.0, "rotation": 0},
            {"t": 1.0, "x": 0, "y": 0, "scale": 1.0, "opacity": 1.0, "rotation": 0},
        ],
    },
    "pop_in": {
        "description": "弹入效果——从 0 快速放大到 1.1 再回弹到 1.0",
        "keyframes": [
            {"t": 0.0, "x": 0, "y": 0, "scale": 0.0, "opacity": 0.0, "rotation": 0},
            {"t": 0.3, "x": 0, "y": 0, "scale": 1.1, "opacity": 1.0, "rotation": 0},
            {"t": 0.5, "x": 0, "y": 0, "scale": 1.0, "opacity": 1.0, "rotation": 0},
        ],
    },
    "rotate_in": {
        "description": "旋转进入——从 0 度旋转到 0 度,伴随透明度/缩放变化",
        "keyframes": [
            {"t": 0.0, "x": 0, "y": 0, "scale": 0.5, "opacity": 0.0, "rotation": -720},
            {"t": 1.0, "x": 0, "y": 0, "scale": 1.0, "opacity": 1.0, "rotation": 0},
        ],
    },
    "bounce": {
        "description": "弹跳效果——先向下再回弹到原位",
        "keyframes": [
            {"t": 0.0, "x": 0, "y": -100, "scale": 1.0, "opacity": 1.0, "rotation": 0},
            {"t": 0.4, "x": 0, "y": 0,    "scale": 1.0, "opacity": 1.0, "rotation": 0},
            {"t": 0.5, "x": 0, "y": -30,  "scale": 1.0, "opacity": 1.0, "rotation": 0},
            {"t": 0.6, "x": 0, "y": 0,    "scale": 1.0, "opacity": 1.0, "rotation": 0},
        ],
    },
}

# ═══════════════════════════════════════════════════════
#  核心引擎:关键帧表达式构建
# ═══════════════════════════════════════════════════════


def _build_keyframe_expr(
    keyframes: list,
    prop: str,
    default_value: float = 0.0,
    clamp_min: float = None,
    clamp_max: float = None,
) -> str:
    """
    从关键帧数据构建 ffmpeg 线性插值表达式.

    keyframes 格式:[{"t": 0.0, "x": 0}, {"t": 1.0, "x": 100}, ...]
    返回类似:
        if(lt(t,0),0,if(between(t,0,1),0+(100-0)*(t-0)/(1-0),if(between(t,1,3),100+(50-100)*(t-1)/(3-1),50)))

    参数:
        keyframes: 关键帧列表
        prop: 属性名,如 "x","y","scale","opacity","rotation"
        default_value: 默认值(t < 首帧时使用)
        clamp_min: 最小值钳制(可选)
        clamp_max: 最大值钳制(可选)
    """
    if not keyframes:
        # 无关键帧,返回常数表达式
        val = _fmt_val(default_value)
        if clamp_min is not None:
            val = f"max({val},{clamp_min})"
        if clamp_max is not None:
            val = f"min({val},{clamp_max})"
        return val

    # 按时间排序
    sorted_kfs = sorted(keyframes, key=lambda k: k["t"])
    first_t = sorted_kfs[0]["t"]
    first_v = sorted_kfs[0].get(prop, default_value)

    # 构建嵌套 if 表达式
    # 结构: if(lt(t,t0),v0, if(between(t,t0,t1),lerp0, if(between(t,t1,t2),lerp1, ..., v_last)))
    parts = []

    # t < 首帧时间 -> 使用首帧值
    parts.append(f"if(lt(t,{_fmt_val(first_t)}),{_fmt_val(first_v)},")

    # 逐段线性插值
    for i in range(len(sorted_kfs) - 1):
        kf0 = sorted_kfs[i]
        kf1 = sorted_kfs[i + 1]
        t0 = kf0["t"]
        t1 = kf1["t"]
        v0 = kf0.get(prop, default_value)
        v1 = kf1.get(prop, default_value)

        # 避免除以零
        if abs(t1 - t0) < 0.0001:
            # 两帧在同一时间点,不插值,用后一帧的值
            parts.append(f"if(between(t,{_fmt_val(t0)},{_fmt_val(t1)}),{_fmt_val(v1)},")
        else:
            # lerp: v0 + (v1 - v0) * (t - t0) / (t1 - t0)
            lerp = f"{_fmt_val(v0)}+({_fmt_val(v1)}-{_fmt_val(v0)})*(t-{_fmt_val(t0)})/({_fmt_val(t1)}-{_fmt_val(t0)})"
            parts.append(f"if(between(t,{_fmt_val(t0)},{_fmt_val(t1)}),{lerp},")

    # t > 末帧时间 -> 使用末帧值
    last_v = sorted_kfs[-1].get(prop, default_value)
    parts.append(_fmt_val(last_v))

    # 闭合所有 if 括号
    expr = "".join(parts) + ")" * len(sorted_kfs)

    # 可选钳制(用 if 而非 min/max,避免嵌套逗号歧义)
    if clamp_min is not None:
        expr = f"if(lt({expr},{clamp_min}),{clamp_min},{expr})"
    if clamp_max is not None:
        expr = f"if(gt({expr},{clamp_max}),{clamp_max},{expr})"

    # 转义表达式中的逗号,防止被 ffmpeg 滤镜解析器误判为滤镜分隔符
    # 如 if(lt(t,0),1,1.3) -> if(lt(t\\,0)\\,1\\,1.3)
    expr = expr.replace(",", "\\,")

    return expr


def _fmt_val(v: float) -> str:
    """将数值格式化为 ffmpeg 友好的字符串,避免科学计数法"""
    if isinstance(v, float):
        # 整数用整数输出
        if v == int(v) and abs(v) < 1e12:
            return str(int(v))
        return f"{v:.6f}".rstrip("0").rstrip(".")
    return str(v)


# ═══════════════════════════════════════════════════════
#  核心引擎:主视频运动滤镜
# ═══════════════════════════════════════════════════════


def _build_motion_filter(anim: dict, vw: int, vh: int) -> str:
    """
    构建主视频运动动画的 filter chain(scale + crop 实现推拉摇移).

    Anim 格式:
    {
        "type": "motion",
        "keyframes": [
            {"t": 0, "scale": 1.0, "x": 0, "y": 0, "rotation": 0, "opacity": 1.0},
            {"t": 3, "scale": 1.3, "x": -50, "y": -30}
        ],
        "start": 0,
        "end": 5
    }

    返回: filter chain 字符串,如
        "format=rgba,colorchannelmixer=aa=1.0,scale=iw*1.0:ih*1.0:...:eval=frame,
         rotate=...:ow=iw:oh=ih:...,crop=vw:vh:(iw-vw)/2+0:(ih-vh)/2+0:eval=frame"

    输出标签固定为 [vmain].
    """
    keyframes = anim.get("keyframes", [])
    if not keyframes:
        # 无动画,直通
        return "[0:v]null[vmain]"

    # 构建各属性的插值表达式
    scale_expr = _build_keyframe_expr(keyframes, "scale", 1.0, clamp_min=0.01)
    x_expr = _build_keyframe_expr(keyframes, "x", 0.0)
    y_expr = _build_keyframe_expr(keyframes, "y", 0.0)
    rot_expr = _build_keyframe_expr(keyframes, "rotation", 0.0)
    opa_expr = _build_keyframe_expr(keyframes, "opacity", 1.0, clamp_min=0.0, clamp_max=1.0)

    has_rotation = any(kf.get("rotation", 0) != 0 for kf in keyframes)
    has_opacity = any(kf.get("opacity", 1.0) != 1.0 for kf in keyframes)

    parts = []

    # 1. 透明度(若有变化)
    if has_opacity:
        parts.append(f"format=rgba,geq=r='X':g='X':b='X':a='{opa_expr}':eval=frame")

    # 2. 缩放(eval=frame 逐帧评估表达式)
    parts.append(f"scale=iw*({scale_expr}):ih*({scale_expr}):eval=frame")

    # 3. 旋转(保持画幅不变,用 ow=iw:oh=ih 固定输出尺寸)
    if has_rotation:
        parts.append(
            f"rotate=({rot_expr})*PI/180:ow=iw:oh=ih:"
            f"fillcolor=black@0:c=none"
        )

    # 4. 裁切平移:从缩放后的画面中裁出原始尺寸 + 偏移
    #    iw/ih 在 crop filter 中是缩放+旋转后的画面尺寸
    #    偏移量 = (缩放后尺寸 - 原始尺寸)/2 + 目标偏移
    crop_x = f"(iw-{vw})/2+({x_expr})"
    crop_y = f"(ih-{vh})/2+({y_expr})"
    # crop 滤镜不支��� eval=frame,表达式默认逐帧评估
    parts.append(f"crop={vw}:{vh}:{crop_x}:{crop_y}")

    return "[0:v]" + ",".join(parts) + "[vmain]"


# ═══════════════════════════════════════════════════════
#  核心引擎:叠加图层滤镜
# ═══════════════════════════════════════════════════════


def _build_overlay_filters(
    overlays: list, vw: int, vh: int, has_main_anim: bool
) -> tuple:
    """
    构建叠加图层的 filter graph 片段.

    每个 overlay 格式:
    {
        "source": "/path/to/image.png",
        "start": 0, "end": 10,
        "width": 200, "height": 200,
        "keyframes": [
            {"t": 0, "x": -200, "y": 500, "scale": 1.0, "opacity": 1.0, "rotation": 0},
            {"t": 2, "x": 100, "y": 500}
        ]
    }

    返回:
        (
            filter_parts: list[str],      # filter_complex 片段列表,用 ";" 连接
            final_video_label: str,        # 最终合成视频的标签
            extra_input_paths: list[str],  # 额外输入文件路径列表(叠加图片)
        )
    """
    if not overlays:
        main_label = "[vmain]" if has_main_anim else "[0:v]"
        return [], main_label, []

    filter_parts = []
    extra_inputs = []
    # 当前合成基础标签:如果主视频有运动动画则用 [vmain],否则 [0:v]
    current_main_label = "[vmain]" if has_main_anim else "[0:v]"
    ov_input_index = 0  # 叠加图在 ffmpeg -i 列表中的索引

    for idx, ov in enumerate(overlays):
        source = ov.get("source", "")
        if not source or not os.path.exists(source):
            # 跳过不存在的文件
            continue

        extra_inputs.append(source)
        ov_input_index += 1  # 第一个叠加图是 -i 1,依次递增
        ffmpeg_input_idx = ov_input_index  # -i 编号

        w = ov.get("width", 100)
        h = ov.get("height", 100)
        start_t = ov.get("start", 0.0)
        end_t = ov.get("end", 10.0)
        kfs = ov.get("keyframes", [])

        if not kfs:
            # 无关键帧:使用静态默认值
            scale_expr = "1.0"
            x_expr = "0"
            y_expr = "0"
            rot_expr = "0"
            opa_expr = "1.0"
            has_rot = False
        else:
            # 补全首尾关键帧以确保在 start/end 外的透明度为 0
            enriched_kfs = _enrich_overlay_keyframes(kfs, start_t, end_t)

            scale_expr = _build_keyframe_expr(enriched_kfs, "scale", 1.0, clamp_min=0.01)
            x_expr = _build_keyframe_expr(enriched_kfs, "x", 0.0)
            y_expr = _build_keyframe_expr(enriched_kfs, "y", 0.0)
            rot_expr = _build_keyframe_expr(enriched_kfs, "rotation", 0.0)
            opa_expr = _build_keyframe_expr(enriched_kfs, "opacity", 1.0,
                                             clamp_min=0.0, clamp_max=1.0)
            has_rot = any(kf.get("rotation", 0) != 0 for kf in enriched_kfs)

        # ---- 构建叠加图的 filter chain ----
        ov_label = f"[ov{idx}]"
        chain = []

        # 将单帧图片循环为视频流 (假设帧率 30)
        chain.append("loop=loop=-1:size=1,setpts=N/(30*TB)")

        # 缩放:width * scale, height * scale
        chain.append(f"scale={_fmt_val(w)}*({scale_expr}):{_fmt_val(h)}*({scale_expr}):eval=frame")

        # 转换为 RGBA 以支持透明度
        chain.append("format=rgba")

        # 透明度
        chain.append(f"colorchannelmixer=aa={opa_expr}")

        # 旋转
        if has_rot:
            chain.append(
                f"rotate=({rot_expr})*PI/180:ow=rotw(({rot_expr})*PI/180):"
                f"oh=roth(({rot_expr})*PI/180):fillcolor=none@0:c=none:eval=frame"
            )

        filter_parts.append(f"[{ffmpeg_input_idx}:v]" + ",".join(chain) + ov_label)

        # ---- 叠加到主视频 ----
        comp_label = f"[comp{idx}]"
        # enable 参数控制可见时间窗口
        enable_expr = f"between(t,{_fmt_val(start_t)},{_fmt_val(end_t)})"
        overlay_str = (
            f"{current_main_label}{ov_label}"
            f"overlay=x='{x_expr}':y='{y_expr}':eval=frame:enable='{enable_expr}'"
            f"{comp_label}"
        )
        filter_parts.append(overlay_str)

        # 更新当前主视频标签
        current_main_label = comp_label

    return filter_parts, current_main_label, extra_inputs


def _enrich_overlay_keyframes(kfs: list, start_t: float, end_t: float) -> list:
    """
    补全叠加图的关键帧,确保 start 前和 end 后透明度为 0.
    如果已有关键帧覆盖这些时间边界,则不重复添加.
    """
    if not kfs:
        return kfs

    enriched = list(kfs)
    sorted_kfs = sorted(enriched, key=lambda k: k["t"])

    # 确保 start 处有关键帧
    # 如果首帧时间 > start,在 start 处插入一个不可见帧
    first_t = sorted_kfs[0]["t"]
    if first_t > start_t + 0.01:
        enriched.append({"t": start_t, "opacity": 0.0})
    elif first_t >= start_t - 0.01:
        # 首帧已经在 start 附近,确保其 opacity <= 0.01
        if sorted_kfs[0].get("opacity", 1.0) > 0.01:
            # 保持首帧值,但添加一个 start 前的 opacity=0 帧
            enriched.append({"t": start_t - 0.01, "opacity": 0.0,
                            "x": sorted_kfs[0].get("x", 0),
                            "y": sorted_kfs[0].get("y", 0),
                            "scale": sorted_kfs[0].get("scale", 1.0),
                            "rotation": sorted_kfs[0].get("rotation", 0)})

    # 确保 end 处有关键帧
    last_t = sorted_kfs[-1]["t"]
    if last_t < end_t - 0.01:
        # 在 end 处插入最后一帧的副本(保持可见到 end)
        last_kf = sorted_kfs[-1].copy()
        last_kf["t"] = end_t
        enriched.append(last_kf)
        # 在 end 后插入不可见帧
        enriched.append({"t": end_t + 0.01, "opacity": 0.0})

    return enriched


# ═══════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════


def _parse_json(data):
    """安全解析 JSON 字符串"""
    if not data:
        return None
    if isinstance(data, str):
        try:
            return json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return None
    return data


def _get_video_dimensions(video_path: str) -> tuple:
    """用 ffmpeg -i 获取视频宽高(兼容 Windows ffprobe 7.1 的 -show_entries bug)"""
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", video_path],
            capture_output=True, timeout=30,
        )
        m = re.search(r",\s*(\d{3,})x(\d{3,})", (r.stdout + r.stderr).decode("utf-8", errors="replace"))
        if m:
            return int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    return 1920, 1080


def _has_nvenc() -> bool:
    """检测系统是否支持 NVENC 编码"""
    try:
        r = subprocess.run(
            ["ffmpeg", "-encoders"],
            capture_output=True, timeout=10, check=False,
        )
        return "h264_nvenc" in r.stdout.decode("utf-8", errors="replace")
    except Exception:
        return False


def _cleanup_tmp(*paths):
    """安全清理临时文件"""
    for p in paths:
        if not p:
            continue
        try:
            if os.path.isfile(p):
                os.remove(p)
            elif os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
        except Exception:
            pass


def _is_default_animation(keyframes: list) -> bool:
    """
    检查关键帧是否都是默认值(无实际动画效果).
    默认值: scale=1.0, x=0, y=0, rotation=0, opacity=1.0
    """
    props = ["scale", "x", "y", "rotation", "opacity"]
    defaults = [1.0, 0.0, 0.0, 0.0, 1.0]
    for kf in keyframes:
        for prop, default in zip(props, defaults):
            val = kf.get(prop, default)
            if abs(val - default) > 0.001:
                return False
    return True


def _animations_have_effect(animations_json) -> bool:
    """检查 animations_json 中是否有任何非默认动画"""
    anims = _parse_json(animations_json)
    if not anims:
        return False
    for anim in anims:
        kfs = anim.get("keyframes", [])
        if kfs and not _is_default_animation(kfs):
            return True
    return False


def _get_motion_anim(animations_json) -> dict:
    """
    从 animations_json 中提取 type=motion 的动画配置.
    返回第一个匹配的动画 dict,若无则返回空 dict.
    """
    anims = _parse_json(animations_json)
    if not anims:
        return {}
    for anim in anims:
        if anim.get("type") == "motion":
            return anim
    return {}


def _get_overlays(overlays_json) -> list:
    """解析 overlays_json 为列表"""
    ovs = _parse_json(overlays_json)
    return ovs if ovs else []


# ═══════════════════════════════════════════════════════
#  AI 工具:apply_animation
# ═══════════════════════════════════════════════════════


@tool(
    name="apply_animation",
    description="对视频应用关键帧动画——支持主视频运动(Ken Burns 缩放/平移/旋转/透明度)和叠加图层(图片)的独立关键帧动画.一次调用可同时处理主视频动画 + 多个叠加层.",
    phase="edit",
    category="animation",
    tags=["keyframe", "motion", "ken_burns"],
    group="花字与动画(效果层)",
)
def apply_animation(
    video_path: str,
    animations_json: str,
    overlays_json: str = "",
    output_path: str = "",
    draft_id: str = "",
    clip_id: int = 0,
) -> str:
    """
    对主视频应用关键帧运动动画 + 叠加图层关键帧动画.

    支持:
    - 主视频 Ken Burns 风格缩放/平移/旋转/透明度
    - 叠加层(图片)的位置/缩放/旋转/透明度动画
    - 自动检测 NVENC 加速

    Args:
        video_path: 输入视频路径
        animations_json: 主视频动画 JSON
            [{
                "type": "motion",
                "keyframes": [
                    {"t": 0, "scale": 1.0, "x": 0, "y": 0, "rotation": 0, "opacity": 1.0},
                    {"t": 3, "scale": 1.3, "x": -50, "y": -30}
                ],
                "start": 0, "end": 5
            }]
        overlays_json: 叠加层 JSON
            [{
                "source": "/path/to/image.png",
                "start": 0, "end": 10,
                "width": 200, "height": 200,
                "keyframes": [{"t":0,"x":-200,"y":500,"scale":1.0,"opacity":1.0,"rotation":0}]
            }]
        output_path: 输出路径(可选,默认自动生成)

    Returns:
        结果信息
    """
    # ── 参数验证 ──
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"

    motion_anim = _get_motion_anim(animations_json)
    overlays = _get_overlays(overlays_json)
    has_motion = bool(motion_anim.get("keyframes")) and not _is_default_animation(
        motion_anim.get("keyframes", [])
    )
    has_overlays = bool(overlays)

    # 检查是否有任何动画效果
    if not has_motion and not has_overlays:
        return "未检测到有效的动画关键帧(所有值均为默认值),跳过渲染"

    # ── 获取视频信息 ──
    vw, vh = _get_video_dimensions(video_path)

    # ── 生成输出路径 ──
    if not output_path:
        tag = hashlib.md5(video_path.encode()).hexdigest()[:8]
        output_path = os.path.join(_PROJECT_DIR, "output", f"anim_{tag}.mp4")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tmp_dir = os.path.join(_PROJECT_DIR, "_tmp_render")
    os.makedirs(tmp_dir, exist_ok=True)

    # ── 构建 filter_complex 图 ──
    filter_parts = []
    final_label = "[vmain]" if has_motion else "[0:v]"

    # 主视频运动滤镜
    if has_motion:
        motion_filter = _build_motion_filter(motion_anim, vw, vh)
        filter_parts.append(motion_filter)

    # 叠加图层
    overlay_filter_parts, final_label, extra_inputs = _build_overlay_filters(
        overlays, vw, vh, has_motion
    )
    filter_parts.extend(overlay_filter_parts)

    # 没有叠加层也没有主视频动画 — 但前面已经检查过了
    if not filter_parts:
        return "无法构建动画滤镜图"

    filter_complex = ";".join(filter_parts)

    # ── 构建 ffmpeg 命令 ──
    cmd = ["ffmpeg", "-y"]

    # 输入文件
    cmd += ["-i", video_path]
    for inp in extra_inputs:
        cmd += ["-i", inp]

    cmd += ["-filter_complex", filter_complex]
    cmd += ["-map", final_label]
    cmd += ["-map", "0:a?"]  # 保留原音频(如果有)

    # 编码器选择
    use_nvenc = _has_nvenc()
    if use_nvenc:
        cmd += ["-c:v", "h264_nvenc", "-preset", "p7", "-cq", "23"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20"]

    cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-movflags", "+faststart"]
    cmd += ["-pix_fmt", "yuv420p"]
    cmd += [output_path]

    # ── 执行 ──
    result = subprocess.run(cmd, capture_output=True, timeout=600, check=False)

    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        size = os.path.getsize(output_path) / (1024 * 1024)
        desc_parts = []
        if has_motion:
            desc_parts.append("主视频动画")
        if has_overlays:
            desc_parts.append(f"{len(overlays)} 个叠加层")
        desc = "+".join(desc_parts)
        if draft_id:
            from director.draft import _write_to_draft
            _write_to_draft(draft_id, clip_id, "animation", {"type": "motion", "animations_json": animations_json}, label="动画完成")
        return f"动画渲染完成 ({desc}): {output_path} ({size:.1f}MB)"

    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="replace")[-500:]
        return f"动画渲染失败: {err}"

    return "动画渲染失败:输出文件为空"


# ═══════════════════════════════════════════════════════
#  AI 工具:preview_animation_frame
# ═══════════════════════════════════════════════════════


@tool(
    name="preview_animation_frame",
    description="在指定时间点预览动画效果,返回一帧 base64 PNG 图片.AI 可以用它来评估动画效果是否满意.",
    phase="edit",
    category="animation",
    tags=["preview", "keyframe", "frame"],
    group="花字与动画(效果层)",
)
def preview_animation_frame(
    video_path: str,
    animations_json: str,
    time_pos: float = 1.0,
) -> str:
    """
    在指定时间点预览动画效果,返回 base64 PNG 供 VL 评审.

    Args:
        video_path: 输入视频路径
        animations_json: 动画 JSON(同 apply_animation)
        time_pos: 预览时间点(秒),默认 1.0

    Returns:
        data:image/png;base64,... 格式的图片数据
    """
    if not os.path.exists(video_path):
        return "文件不存在"

    motion_anim = _get_motion_anim(animations_json)
    has_motion = bool(motion_anim.get("keyframes")) and not _is_default_animation(
        motion_anim.get("keyframes", [])
    )

    vw, vh = _get_video_dimensions(video_path)
    tmp_dir = os.path.join(_PROJECT_DIR, "_tmp_render")
    os.makedirs(tmp_dir, exist_ok=True)
    tag = hashlib.md5(f"{video_path}:{time_pos}:{animations_json}".encode()).hexdigest()[:12]
    tmp_png = os.path.join(tmp_dir, f"anim_preview_{tag}.png")

    filter_parts = []

    if has_motion:
        motion_filter = _build_motion_filter(motion_anim, vw, vh)
        filter_parts.append(motion_filter)
        final_label = "[vmain]"
    else:
        final_label = "[0:v]"

    if not filter_parts:
        # 无动画,直接截图
        cmd = [
            "ffmpeg", "-y", "-ss", str(time_pos), "-i", video_path,
            "-vframes", "1", tmp_png,
        ]
    else:
        filter_complex = ";".join(filter_parts)
        cmd = [
            "ffmpeg", "-y", "-ss", str(time_pos), "-i", video_path,
            "-filter_complex", filter_complex,
            "-map", final_label,
            "-vframes", "1", tmp_png,
        ]

    subprocess.run(cmd, capture_output=True, timeout=30, check=False)

    if not os.path.exists(tmp_png) or os.path.getsize(tmp_png) == 0:
        return "预览帧生成失败"

    with open(tmp_png, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    try:
        os.remove(tmp_png)
    except Exception:
        pass

    return f"data:image/png;base64,{b64}"


# ═══════════════════════════════════════════════════════
#  AI 工具:list_animation_templates
# ═══════════════════════════════════════════════════════


@tool(
    name="list_animation_templates",
    description="列出预定义的动画模板(slide_in_left/right, zoom_in_slow/fast, fade_in, pop_in, rotate_in, bounce).AI 可根据场景选择合适的模板,直接嵌入元素动画.",
    phase="plan",
    category="animation",
    tags=["template", "list", "keyframe"],
    group="花字与动画(效果层)",
)
def list_animation_templates() -> str:
    """
    列出预定义的动画模板,每个模板包含关键帧数据和用途说明.
    适用于快速为元素应用常见动画效果.

    Returns:
        JSON 格式的动画模板列表
    """
    result = []
    for name, tmpl in _ANIMATION_TEMPLATES.items():
        result.append({
            "name": name,
            "description": tmpl["description"],
            "keyframes": tmpl["keyframes"],
        })
    return json.dumps(result, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════
#  AI 工具:apply_slow_motion
# ═══════════════════════════════════════════════════════


@tool(
    name="apply_slow_motion",
    description="光流法慢动作——用运动插值补偿实现流畅慢放.支持 optical_flow(光流插值,流畅推荐) 和 frame_repeat(帧重复,快速) 两种方法.速度因子: 0.25=4倍慢放, 0.5=2倍慢放, 0.75=1.33倍慢放.光流法自动检测帧率并插值到目标帧率,流畅度远超普通帧重复.",
    phase="all",
    category="animation",
    tags=["slow_motion", "optical_flow", "speed"],
    group=["细剪与节奏", "调速与布局"],
)
def apply_slow_motion(
    video_path: str,
    speed_factor: float = 0.5,
    method: str = "optical_flow",
    output_path: str = "",
) -> str:
    """
    光流法慢动作 — 用运动插值补偿实现流畅慢放.

    传统慢动作只是复制帧(帧重复),会导致画面卡顿.
    光流法(minterpolate)在每两帧之间计算物体的运动方向,
    生成中间帧,实现真正的流畅慢动作.

    Args:
        video_path: 输入视频路径
        speed_factor: 速度因子
            0.25 = 4 倍慢放(极慢)
            0.5  = 2 倍慢放
            0.75 = 1.33 倍慢放(微慢)
        method: 慢放方法
            "optical_flow" - 光流插值(流畅,推荐)
            "frame_repeat" - 帧重复(快速,有卡顿感)
        output_path: 输出路径(可选,默认自动生成)

    Returns:
        结果信息
    """
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"

    # 参数校验
    speed_factor = max(0.1, min(0.95, speed_factor))
    valid_methods = {"optical_flow", "frame_repeat"}
    if method not in valid_methods:
        return f"不支持的慢放方法: {method},可选: {', '.join(sorted(valid_methods))}"

    # 检测输入帧率
    fps = _detect_fps(video_path)
    vw, vh = _get_video_dimensions(video_path)

    # 输出路径
    if not output_path:
        tag = hashlib.md5(video_path.encode()).hexdigest()[:8]
        output_path = os.path.join(_PROJECT_DIR, "output", f"slowmo_{tag}.mp4")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # 速度因子转慢放倍数
    playback_speed = speed_factor  # 0.5 = 半速播放
    time_stretch = 1.0 / playback_speed  # 2.0 = 时长翻倍

    if method == "optical_flow":
        # ── 光流法慢动作 ──
        # 1. 用 minterpolate 将帧率提高到 target_fps = fps / speed_factor
        #    mi_mode=mci: 运动补偿插值
        #    mc_mode=aobmc: 自适应重叠块运动补偿
        #    me_mode=bidir: 双向运动估计
        #    vsbmc=1: 可变尺寸块运动补偿
        # 2. 用 setpts 拉长播放时间
        # 3. 音频用 atempo 做时间伸缩

        target_fps = round(fps / speed_factor)
        # 限制最大目标帧率,防止内存爆炸
        target_fps = min(target_fps, 240)

        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-filter_complex",
            f"[0:v]minterpolate=fps={target_fps}:mi_mode=mci:"
            f"mc_mode=aobmc:me_mode=bidir:vsbmc=1,"
            f"setpts={time_stretch}*PTS[vout];"
            f"[0:a]atempo={playback_speed}[aout]",
            "-map", "[vout]",
            "-map", "[aout]",
            "-c:v", "libx264", "-crf", "18", "-preset", "slow",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            output_path,
        ]
    else:
        # ── 帧重复慢动作(简单方法)──
        # 直接用 setpts 拉长时间,ffmpeg 自动重复帧
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-filter_complex",
            f"[0:v]setpts={time_stretch}*PTS[vout];"
            f"[0:a]atempo={playback_speed}[aout]",
            "-map", "[vout]",
            "-map", "[aout]",
            "-c:v", "libx264", "-crf", "18", "-preset", "medium",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            output_path,
        ]

    # 尝试 NVENC 加速
    nvenc = _has_nvenc()
    if method == "optical_flow" and nvenc:
        # NVENC 在光流法下可能内存不足,只在 frame_repeat 时用
        pass  # 光流法用 libx264 更稳定

    result = subprocess.run(cmd, capture_output=True, timeout=900, check=False)

    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        size = os.path.getsize(output_path) / (1024 * 1024)
        orig_dur = _get_video_duration(video_path)
        new_dur = orig_dur / speed_factor if orig_dur else 0
        method_name = "光流插值" if method == "optical_flow" else "帧重复"
        return (
            f"慢动作完成 ({method_name})\n"
            f"  速度: {speed_factor}x ({int(1/speed_factor)}倍慢放)\n"
            f"  时长: {orig_dur:.1f}s -> {new_dur:.1f}s\n"
            f"  原始帧率: {fps}fps\n"
            f"  输出: {output_path} ({size:.1f}MB)"
        )

    err = result.stderr.decode("utf-8", errors="replace")[-500:]
    return f"慢动作渲染失败: {err}"


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
    except Exception:
        pass
    return 30.0


def _get_video_duration(video_path: str) -> float:
    """检测视频时长(秒)"""
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", video_path],
            capture_output=True, timeout=15, check=False,
        )
        out = (r.stdout + r.stderr).decode("utf-8", errors="replace")
        m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", out)
        if m:
            h, m_, s = float(m.group(1)), float(m.group(2)), float(m.group(3))
            return h * 3600 + m_ * 60 + s
    except Exception:
        pass
    return 0.0


# 工具已通过 @tool 装饰器自动注册到 Registry
