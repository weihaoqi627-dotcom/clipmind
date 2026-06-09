#!/usr/bin/env python3
"""
BMF 渲染 Worker(运行在 WSL2 内)
────────────────────────────────
从 EditingScript JSON 构建 BMF Graph -> 渲染成片 MP4.

用法:
  python3 bmf_worker.py <editing_script.json> <output.mp4>

管线:
  decode -> trim -> speed -> reverse -> freeze -> color -> audio_action
  -> effects -> keyframe -> compositing -> concat+fade -> overlay(HF)
  -> mix_audio -> encode
"""

import json
import os
import sys
import time
import subprocess
from pathlib import Path


# ═══════════════════════════════════════════════════
#  路径转换
# ═══════════════════════════════════════════════════

def win_to_wsl(win_path: str) -> str:
    """C:\\Users\\... -> /mnt/c/Users/..."""
    if win_path.startswith("/mnt/"):
        return win_path
    win_path = win_path.replace("\\", "/")
    if len(win_path) >= 2 and win_path[1] == ":":
        drive = win_path[0].lower()
        return f"/mnt/{drive}{win_path[2:]}"
    return win_path


# ═══════════════════════════════════════════════════
#  转场映射
# ═══════════════════════════════════════════════════

XFADE_MAP = {
    "cut":         None,           # 硬切,不需要 xfade
    "fade":        "fade",
    "dissolve":    "dissolve",
    "fadeblack":   "fadeblack",
    "fadewhite":   "fadewhite",
    "slide_left":  "slideright",   # xfade 命名:左滑=上一个向右滑
    "slide_right": "slideleft",
    "slide_up":    "slidedown",
    "slide_down":  "slideup",
    "wipe_left":   "wiperight",
    "wipe_right":  "wipeleft",
    "wipe_up":     "wipedown",
    "wipe_down":   "wipeup",
    "zoom_in":     "zoomin",
    "pixelize":    "pixelize",
    "circleopen":  "circleopen",
    "circleclose": "circleclose",
    "rectcrop":    "rectcrop",
    "hlslider":    "hlslice",
    "hrslider":    "hrslice",
    "vu_slide":    "vuslice",
    "vd_slide":    "vdslice",
    "radial":      "radial",
}


# ═══════════════════════════════════════════════════
#  混合模式映射(-> FFmpeg blend filter all_mode)
# ═══════════════════════════════════════════════════

BLEND_MODE_MAP = {
    "normal":       "normal",
    "multiply":     "multiply",
    "screen":       "screen",
    "overlay":      "overlay",
    "soft_light":   "softlight",
    "hard_light":   "hardlight",
    "darken":       "darken",
    "lighten":      "lighten",
    "difference":   "difference",
    "color_dodge":  "dodge",
    "color_burn":   "burn",
    "addition":     "addition",
    "subtract":     "subtract",
    "exclusion":    "exclusion",
    "average":      "average",
    "negation":     "negation",
    "phoenix":      "phoenix",
    "reflect":      "reflect",
    "glow":         "glow",
    "xor":          "xor",
    "vividlight":   "vividlight",
    "linearlight":  "linearlight",
    "pinlight":     "pinlight",
    "hardmix":      "hardmix",
}


# ═══════════════════════════════════════════════════
#  BMF 图构建
# ═══════════════════════════════════════════════════

def build_bmf_graph(script: dict, output_path: str):
    """
    完整渲染管线:
      decode -> trim -> speed -> fade in/out -> xfade concat -> overlay HF -> encode
    """
    import bmf

    shots = script.get("shots", [])
    overlays = script.get("overlays", [])
    if not shots:
        raise ValueError("EditingScript 中没有 shots,无法渲染")

    has_gpu = _check_cuda()
    print(f"[BMF Worker] {len(shots)} 镜头, {len(overlays)} 叠层, "
          f"GPU={'on' if has_gpu else 'off'}", file=sys.stderr)

    graph = bmf.graph({"dump_graph": 1 if os.environ.get("BMF_DEBUG") else 0})

    # ── 全局 transitions[] 合并到 per-shot ──
    global_transitions = script.get("transitions", [])
    for gt in global_transitions:
        idx = gt.get("shot_index", -1)
        if 0 <= idx < len(shots):
            if "type" in gt:
                shots[idx]["transition_in"] = gt["type"]
                shots[idx]["transition_out"] = gt["type"]
            if "duration" in gt:
                shots[idx]["transition_duration"] = gt["duration"]

    # ── Step 1: 逐镜头解码 + 预处理 ──
    processed = []
    for i, shot in enumerate(shots):
        source_video = win_to_wsl(shot["source_video"])
        t_start, t_end = shot["time_range"]
        duration = t_end - t_start

        if not os.path.exists(source_video):
            print(f"  [WARN] 源文件不存在: {source_video}", file=sys.stderr)
            continue

        print(f"  [{i+1}/{len(shots)}] {shot['shot_id']}: "
              f"{os.path.basename(source_video)} [{t_start:.1f}s-{t_end:.1f}s] "
              f"({duration:.1f}s)", file=sys.stderr)

        # ── 曲线变速(需优先处理,避免同源双重 decode) ──
        speed_ramp = shot.get("speed_ramp", [])

        if speed_ramp:
            # 曲线变速自行处理 decode + trim + speed + concat
            video, audio, duration = _apply_speed_ramp(
                graph, source_video, t_start, duration, speed_ramp
            )
        else:
            # Step 1a: decode with seek(start_time 跳转到镜头起点)
            decode_opts = {"input_path": source_video}
            if t_start > 0:
                decode_opts["start_time"] = t_start

            stream = graph.decode(decode_opts)
            video = stream['video']
            audio = stream['audio']

            # Step 1b: trim 精确截断(BMF trim(start>0) 有 bug,必须用 start=0 + decode seek)
            if duration < 86400:
                video = video.ff_filter("trim", start=0, duration=duration)
                audio = audio.ff_filter("atrim", start=0, duration=duration)

            # 变速
            speed = shot.get("speed", 1.0)
            if speed != 1.0:
                setpts_expr = f"{1/speed}*PTS"
                video = video.ff_filter('setpts', expr=setpts_expr)
                audio = audio.ff_filter('atempo', tempo=speed)
                duration = duration / speed

            # 反转
            if shot.get("reverse", False):
                video = video.ff_filter('reverse')
                audio = audio.ff_filter('areverse')

            # 定格帧(split + tpad + concat — 支持片中任意位置定格)
            freeze_at = shot.get("freeze_at", 0)
            freeze_dur = shot.get("freeze_duration", 0)
            if freeze_at > 0 and freeze_dur > 0:
                if freeze_at >= duration - 0.05:
                    # 片尾定格:简单 tpad
                    video = video.ff_filter(
                        "tpad",
                        stop_mode="clone",
                        stop_duration=freeze_dur,
                    )
                    duration += freeze_dur
                else:
                    # 片中定格:split -> tpad -> concat
                    # 分为 freeze 前 (0 ~ freeze_at) 和 freeze 后 (freeze_at ~ duration)
                    before_v = video.ff_filter("trim", start=0, duration=freeze_at)
                    after_v = video.ff_filter("trim", start=freeze_at, duration=duration - freeze_at)
                    # tpad 定格在 before_v 结尾
                    before_v = before_v.ff_filter(
                        "tpad",
                        stop_mode="clone",
                        stop_duration=freeze_dur,
                    )
                    # concat before + after
                    video = bmf.concat(before_v, after_v, n=2, v=1, a=0)
                    # 音频也切分
                    after_a = audio.ff_filter("atrim", start=freeze_at, duration=duration - freeze_at)
                    before_a = audio.ff_filter("atrim", start=0, duration=freeze_at)
                    # atrim 后 tpad 对音频无效,asendcmd 暂停音频
                    before_a = before_a.ff_filter(
                        "atrim", start=0, duration=freeze_at
                    )
                    audio = bmf.ff_filter(
                        [before_a, after_a.ff_filter("adelay", delays=str(int(freeze_dur * 1000)))],
                        "amix",
                        inputs=2,
                        duration="longest",
                        normalize=0,
                    )
                    duration += freeze_dur
                    print(f"  [freeze] 片中定格 @{freeze_at:.1f}s, 持续{freeze_dur:.1f}s", file=sys.stderr)

        # 调色(优先结构化 grade,回退 grade_spec 字符串)
        grade = shot.get("grade", {})
        grade_spec = shot.get("grade_spec", "")
        if grade or grade_spec:
            video = _apply_color_grade(video, grade_spec, grade)

        # 音频策略(mute / fade_in / fade_out / crossfade / bgm_only)
        audio_action = shot.get("audio_action", "")
        if audio_action:
            audio = _apply_audio_action(audio, audio_action, duration)

        # 音频 EQ
        audio_eq = shot.get("audio_eq", "")
        if audio_eq:
            audio = _apply_audio_eq(audio, audio_eq)

        # 音频压缩
        audio_compress = shot.get("audio_compress", "")
        if audio_compress:
            audio = _apply_audio_compress(audio, audio_compress)

        # 视觉特效(blur / sharpen / noise / flip 等)
        effects = shot.get("effects", [])
        if effects:
            video = _apply_effects(video, effects)

        # 视频防抖
        stabilize = shot.get("stabilize", False)
        if stabilize:
            video = _apply_stabilize(video, stabilize)

        # 关键帧动画 — 预设(slow_push_in / pan:right 等,兼容旧版)
        keyframe = shot.get("keyframe_animation", "")
        src_size = _probe_resolution(source_video)
        if keyframe:
            video = _apply_keyframe_animation(video, keyframe, duration, source_size=src_size)

        # 关键帧动画 — 精确模式(scale/position/rotation/opacity 逐帧插值)
        keyframes = shot.get("keyframes", [])
        if keyframes:
            video = _apply_keyframes(video, keyframes, duration, source_size=src_size)

        # 裁剪
        crop_spec = shot.get("crop_spec", "")
        if crop_spec:
            video = _apply_crop(video, crop_spec, source_size=src_size)

        # 合成(chroma_key 绿幕抠像 / luma_key 亮度键)
        compositing = shot.get("compositing", "")
        if compositing:
            video = _apply_compositing(video, compositing)

        # 蒙版(椭圆/矩形 + 羽化)
        masks = shot.get("masks", [])
        if masks:
            video = _apply_masks(video, masks)

        # 叠加文字(overlay_text -> drawtext filter)
        overlay_text = shot.get("overlay_text")
        if overlay_text:
            subtitle_anim = shot.get("subtitle_animation", "fadeSlide")
            video = _apply_overlay_text(video, overlay_text, duration, subtitle_anim)

        # 标注(annotation -> drawtext / drawbox)
        annotation_type = shot.get("annotation_type", "")
        if annotation_type:
            ann_x = shot.get("annotation_x", 0.5)
            ann_y = shot.get("annotation_y", 0.4)
            video = _apply_annotation(video, annotation_type, duration, ann_x, ann_y)

        processed.append({
            "video": video,
            "audio": audio,
            "duration": duration,
            "transition_out": shot.get("transition_out", "cut"),
            "transition_in": shot.get("transition_in", "cut"),
            "transition_duration": shot.get("transition_duration", 0.5),
            "j_cut_audio": shot.get("j_cut_audio", 0.0),
            "l_cut_audio": shot.get("l_cut_audio", 0.0),
        })

    if not processed:
        raise RuntimeError("没有成功解码任何镜头")

    # ── Step 2: 带转场的拼接 ──
    if len(processed) == 1:
        final_video = processed[0]["video"]
        final_audio = processed[0]["audio"]
    else:
        final_video, final_audio = _concat_with_transitions(processed)

    # ── Step 2.5: 并行轨(画中画/分屏) ──
    parallel_clips = script.get("parallel_clips", [])
    if parallel_clips:
        canvas_w = script.get("width", 1920)
        canvas_h = script.get("height", 1080)
        final_video, final_audio = _apply_parallel_clips(
            graph, final_video, final_audio, parallel_clips,
            canvas_w=canvas_w, canvas_h=canvas_h,
        )

    # ── Step 3: HyperFrames 叠层 ──
    if overlays:
        canvas_w = script.get("width", 1920)
        canvas_h = script.get("height", 1080)
        final_video, final_audio = _apply_overlays(
            graph, final_video, final_audio, overlays,
            canvas_w=canvas_w, canvas_h=canvas_h,
        )

    # ── Step 3.5: 调整图层(全局特效/调色) ──
    total_dur = sum(p["duration"] for p in processed)
    adjustment_layers = script.get("adjustment_layers", [])
    if adjustment_layers:
        final_video = _apply_adjustment_layers(
            final_video, adjustment_layers, total_dur
        )

    # ── Step 4: 音频混音(旁白 + BGM) ──
    final_audio = _mix_audio(graph, final_audio, total_dur, script)

    # ── Step 5: 编码(GPU 优先) ──
    print(f"[BMF Worker] 编码 -> {output_path}", file=sys.stderr)

    is_long = total_dur > 120  # 超过2分钟

    if has_gpu:
        # NVENC GPU 编码:h264_nvenc, QP 模式
        # p4 = 速度/质量平衡, p1 = 极速(长视频用)
        nvenc_preset = "p1" if is_long else "p4"
        nvenc_qp = 20 if is_long else 18  # QP 18 ≈ CRF 17
        print(f"  [GPU] h264_nvenc preset={nvenc_preset} qp={nvenc_qp}", file=sys.stderr)
        encode_opts = {
            "output_path": output_path,
            "video_params": {
                "codec": "h264_nvenc",
                "preset": nvenc_preset,
                "qp": nvenc_qp,
                "profile": "high",
            },
            "audio_params": {
                "codec": "aac",
                "bit_rate": 256000,
                "sample_rate": 48000,
            },
        }
    else:
        # CPU 回退:libx264, CRF 模式
        cpu_preset = "slow" if not is_long else "medium"
        print(f"  [CPU] h264 preset={cpu_preset} crf=17", file=sys.stderr)
        encode_opts = {
            "output_path": output_path,
            "video_params": {
                "codec": "h264",
                "preset": cpu_preset,
                "crf": 17,
                "profile": "high",
            },
            "audio_params": {
                "codec": "aac",
                "bit_rate": 256000,
                "sample_rate": 48000,
            },
        }

    bmf.encode(final_video, final_audio, encode_opts).run()
    return graph


# ═══════════════════════════════════════════════════
#  转场拼接
# ═══════════════════════════════════════════════════

def _concat_with_transitions(processed: list):
    """
    带 fade 转场 + J/L Cut 的拼接.

    视频: fade out -> fade in -> concat(同前)
    音频: 支持 J-Cut(下一镜头音频提前)和 L-Cut(当前镜头音频延续),
          重叠区域用 amix 混合.
    """
    import bmf

    video_chain = processed[0]["video"]
    audio_chain = processed[0]["audio"]
    video_cumulative = processed[0]["duration"]   # 视频累积时长
    audio_cumulative = processed[0]["duration"]   # 音频累积时长(受 J/L cut 影响)

    for i in range(1, len(processed)):
        prev = processed[i - 1]
        curr = processed[i]
        trans_type = prev.get("transition_out", "cut")
        trans_dur = prev.get("transition_duration", 0.5)

        prev_l_cut = prev.get("l_cut_audio", 0.0)
        curr_j_cut = curr.get("j_cut_audio", 0.0)

        # ── 视频(不受 J/L cut 影响) ──
        if trans_type == "cut" or trans_dur <= 0.05:
            video_chain = bmf.concat(video_chain, curr["video"], n=2, v=1, a=0)
        else:
            fade_out_start = max(0, video_cumulative - trans_dur)
            prev_v = video_chain.ff_filter(
                "fade", t="out", st=fade_out_start, d=trans_dur
            )
            next_v = curr["video"].ff_filter(
                "fade", t="in", st=0, d=trans_dur
            )
            video_chain = bmf.concat(prev_v, next_v, n=2, v=1, a=0)

        video_cumulative += curr["duration"]

        # ── 音频(J/L cut 调整时间线) ──
        has_jl_cut = prev_l_cut > 0.01 or curr_j_cut > 0.01

        if not has_jl_cut:
            # 无 J/L cut:正常 concat/crossfade
            if trans_type == "cut" or trans_dur <= 0.05:
                audio_chain = bmf.concat(audio_chain, curr["audio"], n=2, v=0, a=1)
            else:
                fade_out_start_a = max(0, audio_cumulative - trans_dur)
                prev_a = audio_chain.ff_filter(
                    "afade", t="out", st=fade_out_start_a, d=trans_dur
                )
                next_a = curr["audio"].ff_filter(
                    "afade", t="in", st=0, d=trans_dur
                )
                audio_chain = bmf.concat(prev_a, next_a, n=2, v=0, a=1)
            audio_cumulative += curr["duration"]

        else:
            # J/L cut 存在:音频需重叠混合
            # prev_l_cut: prev audio 延续到 video 结束后
            # curr_j_cut: curr audio 提前到 video 开始前
            crossfade_dur = trans_dur if trans_type != "cut" else 0.25

            # curr audio 延迟量:使其在 video_cumulative - curr_j_cut 时间开始
            # 即 curr audio 的起始时间 = 视频衔接点 - curr_j_cut
            curr_start_time = video_cumulative - curr_j_cut
            curr_delay_sec = curr_start_time  # 从时间线 0 算起的延迟

            curr_a = curr["audio"]
            if curr_delay_sec > 0.01:
                delay_ms = int(curr_delay_sec * 1000)
                curr_a = curr_a.ff_filter("adelay", delays=str(delay_ms))

            # curr audio fade in(fade in 起点相对于 curr_a 的起始点)
            curr_a = curr_a.ff_filter(
                "afade", t="in", st=0, d=crossfade_dur
            )

            # prev audio fade out:从重叠区域开始
            prev_fade_start = max(0, curr_start_time - crossfade_dur)
            if prev_fade_start < audio_cumulative:
                prev_a = audio_chain.ff_filter(
                    "afade", t="out", st=prev_fade_start, d=crossfade_dur
                )
            else:
                prev_a = audio_chain

            # amix: duration=longest 允许 curr audio 延续超出 prev
            audio_chain = bmf.ff_filter(
                [prev_a, curr_a],
                "amix",
                inputs=2,
                duration="longest",
                normalize=0,
            )

            # 更新音频累积时长
            audio_cumulative = video_cumulative + curr["duration"]

    return video_chain, audio_chain


# ═══════════════════════════════════════════════════
#  HyperFrames 叠层
# ═══════════════════════════════════════════════════

def _apply_overlays(graph, video_stream, audio_stream, overlays: list,
                    canvas_w: int = 1920, canvas_h: int = 1080):
    """
    将 HyperFrames 透明 MOV 叠加到主视频轨上.

    每个 overlay 独立 decode -> overlay/blend filter 叠加到主轨 -> 输出新主轨.
    多个 overlay 按时间线顺序逐层叠加.
    支持 blend_mode: normal -> alpha 叠加, 其他 -> blend filter
    """
    import bmf

    print(f"[BMF Worker] 叠加 {len(overlays)} 个叠层...", file=sys.stderr)

    for i, ov in enumerate(overlays):
        mov_path = ov.get("props", {}).get("mov_path", "")
        if not mov_path:
            print(f"  [SKIP] overlay {ov.get('element_id', '?')}: 无 mov_path", file=sys.stderr)
            continue

        mov_wsl = win_to_wsl(mov_path)
        if not os.path.exists(mov_wsl):
            print(f"  [SKIP] overlay MOV 不存在: {mov_wsl}", file=sys.stderr)
            continue

        start_t = ov.get("start_time", 0)
        dur = ov.get("duration", 5)
        ov_type = ov.get("type", "?")
        blend_mode = ov.get("blend_mode", "normal")
        ff_blend = BLEND_MODE_MAP.get(blend_mode, "normal")

        print(f"  [{i+1}/{len(overlays)}] {ov_type} "
              f"@{start_t:.1f}s dur={dur:.1f}s", file=sys.stderr)

        # 解码叠层 MOV
        ov_stream = graph.decode({"input_path": mov_wsl})
        ov_video = ov_stream['video']

        # 位置(默认居中)
        x = ov.get("props", {}).get("x", "(W-w)/2")
        y = ov.get("props", {}).get("y", "(H-h)/2")

        # enable 表达式:只在指定时间窗口显示叠层
        enable_expr = f"between(t,{start_t},{start_t + dur})"

        if blend_mode == "normal":
            # Alpha 叠加(HF MOV 自带 alpha 通道)
            video_stream = bmf.ff_filter(
                [video_stream, ov_video],
                "overlay",
                x=x, y=y,
                enable=enable_expr,
            )
        else:
            # 非 normal 混合模式:pad 到画布大小 -> blend
            # 注:这可能会丢失 alpha 通道的透明信息
            ov_video = ov_video.ff_filter(
                "pad",
                w=canvas_w, h=canvas_h,
                x=x, y=y,
                color="black@0",
            )
            video_stream = bmf.ff_filter(
                [video_stream, ov_video],
                "blend",
                all_mode=ff_blend,
                enable=enable_expr,
            )

    return video_stream, audio_stream


# ═══════════════════════════════════════════════════
#  并行轨(画中画 / 分屏)
# ═══════════════════════════════════════════════════

PIP_POSITIONS = {
    "pip_tl":      {"x": "margin", "y": "margin"},
    "pip_tr":      {"x": "W-w-margin", "y": "margin"},
    "pip_bl":      {"x": "margin", "y": "H-h-margin"},
    "pip_br":      {"x": "W-w-margin", "y": "H-h-margin"},
    "top_left":    {"x": "margin", "y": "margin"},
    "top_right":   {"x": "W-w-margin", "y": "margin"},
    "bottom_left": {"x": "margin", "y": "H-h-margin"},
    "bottom_right":{"x": "W-w-margin", "y": "H-h-margin"},
}


def _apply_parallel_clips(graph, video_chain, audio_chain, parallel_clips: list,
                          canvas_w: int = 1920, canvas_h: int = 1080):
    """
    多轨合成——画中画 / 分屏 / 全屏叠层.

    每个 parallel_clip: decode -> trim -> scale/position -> blend/overlay 到主视频.
    position="full": 全屏叠加,忽略 scale/margin,走 blend filter
    opacity<1.0: 走 blend filter 用 all_opacity 控制透明度
    """
    import bmf

    if not parallel_clips:
        return video_chain, audio_chain

    print(f"[BMF Worker] 多轨合成: {len(parallel_clips)} 条", file=sys.stderr)

    for i, pc in enumerate(parallel_clips):
        src = win_to_wsl(pc.get("source_video", ""))
        if not src or not os.path.exists(src):
            print(f"  [SKIP] 辅轨 {pc.get('clip_id', '?')}: 源文件不存在", file=sys.stderr)
            continue

        tr = pc.get("time_range", [0, 5])
        t_start, t_end = tr[0], tr[1]
        dur = t_end - t_start
        start_time = pc.get("start_time", 0)
        position = pc.get("position", "pip_br")
        scale = pc.get("scale", 0.3)
        margin = pc.get("margin", 20)
        mute = pc.get("mute", True)
        blend_mode = pc.get("blend_mode", "normal")
        opacity = pc.get("opacity", 1.0)
        ff_blend = BLEND_MODE_MAP.get(blend_mode, "normal")

        # 副轨平权新字段
        pip_speed = pc.get("speed", 1.0) or 1.0
        pip_effects = pc.get("effects", []) or []
        pip_grade_spec = pc.get("grade_spec", "")
        pip_trans_in = pc.get("transition_in", "")
        pip_trans_out = pc.get("transition_out", "")
        pip_trans_dur = pc.get("transition_duration", 0.3)

        # 副轨输出时长(setpts 后会变,与主轨逻辑一致)
        trim_start = t_start
        trim_dur = dur
        pip_out_dur = dur
        if pip_speed != 1.0:
            pip_out_dur = dur / pip_speed

        is_full = position == "full"
        use_blend = (not is_full and blend_mode != "normal") or is_full or opacity < 1.0

        detail_parts = [f"@{start_time:.1f}s dur={dur:.1f}s pos={position} scale={scale}"]
        if blend_mode != "normal":
            detail_parts.append(f"blend={blend_mode}")
        if opacity < 1.0:
            detail_parts.append(f"opacity={opacity}")
        if pip_speed != 1.0:
            detail_parts.append(f"speed={pip_speed}x")
        if pip_trans_in:
            detail_parts.append(f"in={pip_trans_in}/{pip_trans_dur}s")
        if pip_trans_out:
            detail_parts.append(f"out={pip_trans_out}/{pip_trans_dur}s")
        if pip_grade_spec:
            detail_parts.append(f"grade={pip_grade_spec}")
        print(f"  [{i+1}/{len(parallel_clips)}] {pc.get('clip_id', '?')} "
              + ", ".join(detail_parts),
              file=sys.stderr)

        # decode + trim(视频)
        pip_stream = graph.decode({
            "input_path": src,
            "start_time": trim_start,
        })
        pip_v = pip_stream['video'].ff_filter("trim", start=0, duration=trim_dur)

        # ── 副轨变速 ──
        if pip_speed != 1.0:
            setpts = 1.0 / pip_speed
            pip_v = pip_v.ff_filter("setpts", expr=f"{setpts}*PTS")

        # ── 副轨转场(fade in/out) ──
        if pip_trans_in == "fade" and pip_trans_dur > 0:
            pip_v = pip_v.ff_filter("fade", t="in", st=0, d=pip_trans_dur)
        elif pip_trans_in == "wipe" and pip_trans_dur > 0:
            # wipe 用 crop + fade 近似(从左到右揭开)
            pip_v = pip_v.ff_filter("fade", t="in", st=0, d=pip_trans_dur)
        if pip_trans_out == "fade" and pip_trans_dur > 0:
            st_out = max(0, pip_out_dur - pip_trans_dur)
            pip_v = pip_v.ff_filter("fade", t="out", st=st_out, d=pip_trans_dur)

        # ── 副轨特效 ──
        for eff in pip_effects:
            if eff == "blur" or eff == "gaussian_blur":
                pip_v = pip_v.ff_filter("boxblur", lr=5, la="lr")
            elif eff == "sharpen":
                pip_v = pip_v.ff_filter("unsharp", luma_msize_x=5, luma_msize_y=5, luma_amount=1.0)
            elif eff == "noise":
                pip_v = pip_v.ff_filter("noise", c0s=8, c1s=8, c2s=8, all_seed=42)

        # ── 副轨调色 ──
        if pip_grade_spec:
            pip_v = _apply_color_grade(pip_v, pip_grade_spec)

        enable_expr = f"between(t,{start_time},{start_time + pip_out_dur})"

        if is_full or use_blend:
            # 走 blend filter 路径
            if is_full:
                # 全屏:缩放到画布尺寸
                pip_v = pip_v.ff_filter("scale", w=canvas_w, h=canvas_h)
                # pad 填满透明区域
                pip_v = pip_v.ff_filter(
                    "pad", w=canvas_w, h=canvas_h,
                    x=0, y=0, color="black@0",
                )
            else:
                # 画中画位置:scale -> pad 到画布
                pip_v = pip_v.ff_filter("scale", w=f"iw*{scale}", h=f"ih*{scale}")
                pos_info = PIP_POSITIONS.get(position, PIP_POSITIONS["pip_br"])
                pad_x = str(pos_info["x"]).replace("margin", str(margin))
                pad_y = str(pos_info["y"]).replace("margin", str(margin))
                pip_v = pip_v.ff_filter(
                    "pad", w=canvas_w, h=canvas_h,
                    x=pad_x, y=pad_y, color="black@0",
                )

            # blend with opacity
            blend_params = {"all_mode": ff_blend, "enable": enable_expr}
            if opacity < 1.0:
                blend_params["all_opacity"] = str(opacity)
            video_chain = bmf.ff_filter(
                [video_chain, pip_v], "blend", **blend_params,
            )
        else:
            # 标准 PiP overlay,normal blend
            pip_v = pip_v.ff_filter("scale", w=f"iw*{scale}", h=f"ih*{scale}")
            pos_info = PIP_POSITIONS.get(position, PIP_POSITIONS["pip_br"])
            x = str(pos_info["x"]).replace("margin", str(margin))
            y = str(pos_info["y"]).replace("margin", str(margin))
            video_chain = bmf.ff_filter(
                [video_chain, pip_v],
                "overlay",
                x=x, y=y,
                enable=enable_expr,
            )

        # 音频处理(变速后副轨音频也要同步)
        if not mute:
            pip_a = pip_stream['audio'].ff_filter("atrim", start=0, duration=trim_dur)
            if pip_speed != 1.0 and pip_a is not None:
                pip_a = pip_a.ff_filter("atempo", tempo=pip_speed)
            if pip_a is not None:
                delay_ms = int(start_time * 1000)
                if delay_ms > 0:
                    pip_a = pip_a.ff_filter("adelay", delays=str(delay_ms))
                audio_chain = bmf.ff_filter(
                    [audio_chain, pip_a],
                    "amix",
                    inputs=2,
                    duration="longest",
                    normalize=0,
                )

    return video_chain, audio_chain


# ═══════════════════════════════════════════════════
#  音频混音(旁白 + BGM)
# ═══════════════════════════════════════════════════

def _collect_sfx_events(script: dict) -> list[dict]:
    """
    从脚本所有镜头中收集 SFX 事件,转成带绝对时间线的渲染指令.

    处理两种 SFX:
      - sfx_hits:     卡点音效(impact/riser),time 是镜头内偏移
      - sfx_transitions: 转场音效(whoosh),time 是镜头内偏移

    返回 list[dict],每项:
      {path, time(绝对秒), duration, volume, category, type}
    """
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from audio.sfx_library import select_sfx

    shots = script.get("shots", [])
    if not shots:
        return []

    events = []
    t = 0.0  # 绝对时间线游标

    for shot in shots:
        tr = shot.get("time_range", [0, 0])
        speed = shot.get("speed", 1.0) or 1.0
        raw_dur = tr[1] - tr[0]
        dur = raw_dur / speed if speed != 1.0 else raw_dur
        freeze_dur = (shot.get("freeze_duration", 0) or 0) if (shot.get("freeze_at", 0) or 0) > 0 else 0

        # ── 卡点 SFX ──
        for h in shot.get("sfx_hits", []) or []:
            if not isinstance(h, dict):
                continue
            offset = h.get("time", 0)
            cat = h.get("category", "impact")
            kw = h.get("keywords", [])
            vol = h.get("volume", 0.6)
            s_dur = h.get("duration", 0.2)

            path = select_sfx(category=cat, keywords=kw)
            if path:
                events.append({
                    "path": path,
                    "time": t + offset,
                    "duration": s_dur,
                    "volume": vol,
                    "category": cat,
                    "type": "hit",
                })

        # ── 转场 SFX ──
        for tr_sfx in shot.get("sfx_transitions", []) or []:
            if not isinstance(tr_sfx, dict):
                continue
            offset = tr_sfx.get("time", dur * 0.5)  # 默认镜头中间
            cat = tr_sfx.get("category", "transition")
            kw = tr_sfx.get("keywords", [])
            vol = tr_sfx.get("volume", 0.4)
            s_dur = tr_sfx.get("duration", 0.3)

            path = select_sfx(category=cat, keywords=kw)
            if path:
                events.append({
                    "path": path,
                    "time": t + offset,
                    "duration": s_dur,
                    "volume": vol,
                    "category": cat,
                    "type": "transition",
                })

        t += dur + freeze_dur

    return events


def _find_speech_segments(script: dict) -> list[tuple[float, float]]:
    """
    从镜头时间线中找出所有保留人声的时间区间.

    返回 [(start_sec, end_sec), ...] 列表,供 BGM 闪避使用.
    判断标准:audio_action 不是 "mute" 或 "bgm_only" 的镜头 = 有人声.
    """
    shots = script.get("shots", [])
    if not shots:
        return []

    segments = []
    t = 0.0
    for shot in shots:
        action = shot.get("audio_action", "")
        tr = shot.get("time_range", [0, 0])
        speed = shot.get("speed", 1.0) or 1.0
        raw_dur = tr[1] - tr[0]
        dur = raw_dur / speed if speed != 1.0 else raw_dur

        # 定格额外时长
        freeze_at = shot.get("freeze_at", 0) or 0
        freeze_dur = shot.get("freeze_duration", 0) or 0
        if freeze_at > 0 and freeze_dur > 0:
            dur += freeze_dur

        # 不是 mute 也不是 bgm_only -> 有人声
        if action not in ("mute", "bgm_only"):
            segments.append((t, t + dur))

        t += dur

    return segments


def _build_ducking_envelope(
    segments: list[tuple[float, float]],
    *,
    max_level: float = 0.7,
    duck_dense: float = 0.2,
    duck_sparse: float = 0.3,
    attack: float = 5.0,
    release: float = 5.0,
    look_behind: float = 30.0,
    dense_gap: float = 10.0,
) -> str:
    """
    构建 FFmpeg volume 表达式,实现智能 BGM 闪避.

    策略:
    1. 检查前后 30s 内有无其他人声,有则不恢复,维持低位
    2. 人声密集(间隔<10s)-> 压到 20%;稀疏 -> 压到 30%
    3. 无对话时最高 70%,不会轰头
    4. 渐入渐出各 5 秒,平滑过渡

    Returns:
        FFmpeg volume 表达式(输出绝对值,不乘 bgm_volume)
    """
    if not segments:
        return str(max_level)

    # ── 第一步:间隔 < look_behind(30s) 的段合并为一个"对话块" ──
    blocks = []       # [(block_start, block_end, duck_level, [segments])]
    current_segs = [segments[0]]
    current_start = segments[0][0]
    current_end = segments[0][1]

    for s, e in segments[1:]:
        gap = s - current_end
        if gap < look_behind:
            # 在 30s 范围内 -> 合并
            current_segs.append((s, e))
            current_end = max(current_end, e)
        else:
            # 间隔 >= 30s -> 这个块结束
            blocks.append((current_start, current_end, current_segs))
            current_segs = [(s, e)]
            current_start = s
            current_end = e
    blocks.append((current_start, current_end, current_segs))

    # ── 第二步:每块判断密集程度 ──
    block_entries = []  # [(block_start, block_end, duck_level)]
    for b_start, b_end, segs in blocks:
        # 块内最短间隔
        min_gap = float("inf")
        for i in range(len(segs) - 1):
            gap = segs[i + 1][0] - segs[i][1]
            if gap < min_gap:
                min_gap = gap
        level = duck_dense if min_gap < dense_gap else duck_sparse
        block_entries.append((b_start, b_end, level))

    # ── 第三步:构建 FFmpeg 表达式 ──
    # 每个 block 生成:
    #   [b_start-attack, b_start] -> max_level 线性降到 duck_level
    #   [b_start, b_end]         -> duck_level
    #   [b_end, b_end+release]   -> duck_level 线性升到 max_level
    # block 之间 -> max_level

    # 从右向左嵌套 if/else
    inner = str(max_level)
    for b_start, b_end, d in reversed(block_entries):
        ramp_start = max(0.0, b_start - attack)
        ramp_end = ramp_start + attack
        recover_end = b_end + release

        # 表达式片段(从外到内):
        # t < ramp_start       -> max_level(块前)
        # ramp_start ≤ t < b_start -> 线性 ramp max_level -> d
        # b_start ≤ t < b_end      -> d
        # b_end ≤ t < recover_end  -> 线性 ramp d -> max_level
        # t ≥ recover_end          -> inner(下一个块或最终值)

        diff_down = max_level - d
        diff_up = max_level - d  # same difference

        segment_expr = (
            f"if(lt(t,{ramp_start}),{max_level},"
            f"if(lt(t,{b_start}),{max_level}-{diff_down}*(t-{ramp_start})/{attack},"
            f"if(lt(t,{b_end}),{d},"
            f"if(lt(t,{recover_end}),{d}+{diff_up}*(t-{b_end})/{release},"
            f"{inner})))))"
        )
        inner = segment_expr

    return inner


def _mix_audio(graph, audio_chain, total_duration: float, script: dict):
    """
    将旁白和 BGM 混入主音频轨.

    管线:
      clip_audio -> [amix ← narration] -> [amix ← BGM(volume)] -> 最终音频
    """
    import bmf

    narration_segments = script.get("narration_segments", [])
    bgm_path = script.get("bgm_path", "")
    bgm_volume = script.get("bgm_volume", 0.4)

    # ── 旁白混音 ──
    if narration_segments:
        valid_narrs = [n for n in narration_segments
                       if n.get("audio_path", "") and os.path.exists(win_to_wsl(n["audio_path"]))]
        if valid_narrs:
            print(f"[BMF Worker] 混入 {len(valid_narrs)} 段旁白...", file=sys.stderr)

            narr_chains = []
            for n in valid_narrs:
                wsl_path = win_to_wsl(n["audio_path"])
                t_start = n.get("start_time", 0)
                dur = n.get("estimated_duration", 4)

                # 解码旁白音频
                narr_stream = graph.decode({"input_path": wsl_path})
                narr_a = narr_stream['audio']

                # 截断到估算时长
                if dur < 86400:
                    narr_a = narr_a.ff_filter("atrim", start=0, duration=dur)

                # 延迟到时间线位置(毫秒)
                delay_ms = int(t_start * 1000)
                narr_a = narr_a.ff_filter("adelay", delays=str(delay_ms))

                if narr_a is not None:
                    narr_chains.append(narr_a)

            if narr_chains:
                # 逐段 amix 混入主轨(不用 concat,避免 adelay 时长累加)
                # normalize=0: 不做自动归一化,防止多次 amix 累积衰减
                for narr_a in narr_chains:
                    audio_chain = bmf.ff_filter(
                        [audio_chain, narr_a],
                        "amix",
                        inputs=2,
                        duration="first",
                        weights="1 1",
                        normalize=0,
                    )

    # ── SFX 混音(卡点/转场音效) ──
    sfx_events = _collect_sfx_events(script)
    if sfx_events:
        speech_segments = _find_speech_segments(script) if script.get("bgm_ducking") else []
        print(f"[BMF Worker] 混入 {len(sfx_events)} 个音效",
              file=sys.stderr)

        for i, sfx in enumerate(sfx_events):
            sfx_path = sfx.get("path", "")
            sfx_wsl = win_to_wsl(sfx_path) if sfx_path else ""
            if not sfx_path or not os.path.exists(sfx_wsl):
                continue

            t = sfx.get("time", 0.0)
            dur = sfx.get("duration", 0.3)
            vol = sfx.get("volume", 0.5)

            # ── 人声避让:SFX 落在语音段内 -> 压低音量 ──
            if speech_segments:
                for ss, se in speech_segments:
                    if ss <= t < se or ss < t + dur <= se or (t <= ss and t + dur >= se):
                        vol *= 0.15  # 人声区间 SFX 降到 15%
                        break

            # 解码 SFX 文件
            sfx_stream = graph.decode({"input_path": sfx_wsl})
            sfx_a = sfx_stream['audio']
            if sfx_a is None:
                continue

            # 裁剪音效时长 + 微淡入淡出防爆音
            sfx_a = sfx_a.ff_filter("atrim", start=0, duration=dur)
            sfx_a = sfx_a.ff_filter("afade", type="in", start_time=0, duration=0.02)
            sfx_a = sfx_a.ff_filter("afade", type="out", start_time=max(0, dur - 0.02), duration=0.02)
            sfx_a = sfx_a.ff_filter("adelay", delays=f"{int(t * 1000)}|{int(t * 1000)}", all=1)
            sfx_a = sfx_a.ff_filter("volume", volume=vol)

            # 混入主音频链(SFX 在 BGM 之前,保证比 BGM 响亮)
            audio_chain = bmf.ff_filter(
                [audio_chain, sfx_a],
                "amix",
                inputs=2,
                duration="first",
                weights="1 1",
                normalize=0,
            )

    # ── BGM 混音 ──
    if bgm_path:
        bgm_wsl = win_to_wsl(bgm_path)
        if os.path.exists(bgm_wsl):
            print(f"[BMF Worker] 混入 BGM: {os.path.basename(bgm_path)} (音量={bgm_volume})",
                  file=sys.stderr)

            bgm_stream = graph.decode({"input_path": bgm_wsl})
            bgm_a = bgm_stream['audio']

            if bgm_a is not None:
                # 语音闪避(ducking):有人声时自动压低 BGM,人声结束后恢复
                bgm_ducking = script.get("bgm_ducking", False)
                bgm_duck_level = script.get("bgm_duck_level", 0.15)
                bgm_duck_attack = script.get("bgm_duck_attack", 0.08)
                bgm_duck_release = script.get("bgm_duck_release", 0.35)

                if bgm_ducking:
                    speech_segments = _find_speech_segments(script)
                    if speech_segments:
                        volume_expr = _build_ducking_envelope(
                            speech_segments,
                            duck_dense=bgm_duck_level,
                            duck_sparse=bgm_duck_level * 2,
                            attack=bgm_duck_attack,
                            release=bgm_duck_release,
                        )
                        bgm_a = bgm_a.ff_filter("volume", volume=volume_expr, eval="frame")
                        duck_secs = sum(e - s for s, e in speech_segments)
                        print(f"[BMF Worker] BGM 闪避: {len(speech_segments)}段对话, "
                              f"共{duck_secs:.0f}s (避让={bgm_duck_level}, "
                              f"attack={bgm_duck_attack}s release={bgm_duck_release}s)",
                              file=sys.stderr)
                else:
                    # 无闪避 -> 恒定音量
                    bgm_a = bgm_a.ff_filter("volume", volume=bgm_volume)

                # 裁剪到成片时长
                bgm_a = bgm_a.ff_filter("atrim", start=0, duration=total_duration)

                # amix 混入主轨(BGM 用 bgm_volume 权重)
                weights_str = f"1 {bgm_volume}"
                audio_chain = bmf.ff_filter(
                    [audio_chain, bgm_a],
                    "amix",
                    inputs=2,
                    duration="first",
                    weights=weights_str,
                    normalize=0,
                )

    return audio_chain


# ═══════════════════════════════════════════════════
#  音频策略
# ═══════════════════════════════════════════════════

def _apply_audio_action(audio_stream, audio_action: str, duration: float):
    """
    音频设计指令.

    支持:
      mute / bgm_only     -> 静音(留 BGM 在混音层处理)
      keep                 -> 原样不动
      fade_in              -> 0.5s 淡入
      fade_out             -> 0.5s 淡出
      crossfade            -> 首尾各 0.5s
      fade:N_in:N_out      -> 自定义淡入淡出秒数
    """
    action = audio_action.lower().strip()

    if not action or action == "keep":
        return audio_stream

    if action in ("mute", "bgm_only"):
        return audio_stream.ff_filter("volume", volume=0)

    if action == "fade_in":
        return audio_stream.ff_filter("afade", t="in", st=0, d=0.5)

    if action == "fade_out":
        st = max(0, duration - 0.5)
        return audio_stream.ff_filter("afade", t="out", st=st, d=0.5)

    if action == "crossfade":
        audio_stream = audio_stream.ff_filter("afade", t="in", st=0, d=0.5)
        st_out = max(0, duration - 0.5)
        return audio_stream.ff_filter("afade", t="out", st=st_out, d=0.5)

    # 自定义: fade:in_sec:out_sec
    if action.startswith("fade:"):
        parts = action.split(":")
        if len(parts) >= 3:
            try:
                fade_in = float(parts[1])
                fade_out = float(parts[2])
                if fade_in > 0:
                    audio_stream = audio_stream.ff_filter("afade", t="in", st=0, d=fade_in)
                if fade_out > 0:
                    st = max(0, duration - fade_out)
                    audio_stream = audio_stream.ff_filter("afade", t="out", st=st, d=fade_out)
                return audio_stream
            except ValueError:
                pass

    print(f"  [WARN] 未知 audio_action: {audio_action}", file=sys.stderr)
    return audio_stream


# ═══════════════════════════════════════════════════
#  音频 EQ
# ═══════════════════════════════════════════════════

def _apply_audio_eq(audio_stream, eq_spec: str):
    """
    三段式均衡器.格式: "low=2:mid=0:high=-3" (dB 增益)

    FFmpeg equalizer filter: frequency + width + gain
    low=100Hz, mid=1000Hz, high=8000Hz (宽 Q 值)
    """
    if not eq_spec:
        return audio_stream

    parts = eq_spec.lower().replace(" ", "").split(":")
    eq_map = {}
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            try:
                eq_map[k.strip()] = float(v.strip())
            except ValueError:
                pass

    if not eq_map:
        return audio_stream

    print(f"  [audio_eq] {eq_map}", file=sys.stderr)

    for band, freq, width in [
        ("low", 100, 200),
        ("mid", 1000, 1000),
        ("high", 8000, 4000),
    ]:
        if band in eq_map:
            audio_stream = audio_stream.ff_filter(
                "equalizer",
                frequency=freq,
                width=width,
                gain=eq_map[band],
                width_type="h",
            )

    return audio_stream


# ═══════════════════════════════════════════════════
#  音频压缩
# ═══════════════════════════════════════════════════

def _apply_audio_compress(audio_stream, compress_spec: str):
    """
    动态压缩器.格式: "threshold=-20:ratio=4:attack=5:release=50"

    FFmpeg compand filter 参数映射:
      threshold -> 低于此 dB 不处理
      ratio     -> 压缩比
      attack    -> 启动时间 ms
      release   -> 释放时间 ms
    """
    if not compress_spec:
        return audio_stream

    parts = compress_spec.lower().replace(" ", "").split(":")
    comp_map = {}
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            try:
                comp_map[k.strip()] = float(v.strip())
            except ValueError:
                pass

    if not comp_map:
        return audio_stream

    threshold = comp_map.get("threshold", -20)
    ratio = comp_map.get("ratio", 4)
    attack = comp_map.get("attack", 5) / 1000.0   # ms -> s
    release = comp_map.get("release", 50) / 1000.0

    print(f"  [audio_compress] thr={threshold}dB ratio={ratio}:1 "
          f"atk={attack*1000:.0f}ms rel={release*1000:.0f}ms", file=sys.stderr)

    # compand: 低于 threshold + attack 的部分压缩
    # 攻击时间 = 音量变化响应速度
    # 公式: 在 threshold 以下,输出 = threshold + (input - threshold) / ratio
    # compand 的点对: [input_dB, output_dB]
    points = [
        f"-90/-90",                    # 静音不变
        f"{threshold}/{threshold}",   # threshold 开始压缩
        f"0/0",                       # 0dB 不变
    ]
    points_str = "|".join(points)

    return audio_stream.ff_filter(
        "compand",
        attacks=attack,
        decays=release,
        points=points_str,
        soft_knee=0.01,
        volume=0,              # 不额外调音量
        delay=0.01,            # 微延迟让压缩生效
    )


# ═══════════════════════════════════════════════════
#  视觉特效
# ═══════════════════════════════════════════════════

def _apply_effects(video_stream, effects: list):
    """
    逐镜头特效链.

    支持:
      blur:N     -> boxblur 模糊 (N=半径, 默认5)
      gblur:N    -> 高斯模糊 (N=sigma, 默认3)
      sharpen    -> unsharp 锐化
      noise:N    -> 噪点 (N=强度, 默认8)
      hflip / vflip -> 水平/垂直翻转
      glow       -> 简单发光 (gblur + blend=screen)
      denoise    -> 时域+空域降噪 (hqdn3d)
      vignette   -> 暗角效果
    """
    for ef in effects:
        name = ef.get("name", "") if isinstance(ef, dict) else (ef if isinstance(ef, str) else "")
        if not name:
            continue

        if name == "blur":
            radius = ef.get("radius", 5) if isinstance(ef, dict) else 5
            video_stream = video_stream.ff_filter("boxblur", lr=radius)
        elif name == "gblur":
            sigma = ef.get("sigma", 3) if isinstance(ef, dict) else 3
            video_stream = video_stream.ff_filter("gblur", sigma=sigma)
        elif name == "sharpen":
            video_stream = video_stream.ff_filter(
                "unsharp", luma_msize_x=5, luma_msize_y=5, luma_amount=1.0
            )
        elif name == "noise":
            strength = ef.get("strength", 8) if isinstance(ef, dict) else 8
            video_stream = video_stream.ff_filter(
                "noise", all_seed=42, all_strength=strength
            )
        elif name == "hflip":
            video_stream = video_stream.ff_filter("hflip")
        elif name == "vflip":
            video_stream = video_stream.ff_filter("vflip")
        elif name == "glow":
            sigma = ef.get("sigma", 5) if isinstance(ef, dict) else 5
            video_stream = video_stream.ff_filter("gblur", sigma=sigma)
        elif name == "denoise":
            # 时域+空域降噪 (hqdn3d)
            luma_spatial = ef.get("luma_spatial", 4) if isinstance(ef, dict) else 4
            chroma_spatial = ef.get("chroma_spatial", 6) if isinstance(ef, dict) else 6
            luma_temporal = ef.get("luma_temporal", 6) if isinstance(ef, dict) else 6
            chroma_temporal = ef.get("chroma_temporal", 8) if isinstance(ef, dict) else 8
            video_stream = video_stream.ff_filter(
                "hqdn3d",
                luma_spatial=luma_spatial,
                chroma_spatial=chroma_spatial,
                luma_tmp=luma_temporal,
                chroma_tmp=chroma_temporal,
            )
        elif name == "vignette":
            # 暗角效果
            angle = ef.get("angle", "PI/5") if isinstance(ef, dict) else "PI/5"
            mode = ef.get("mode", "forward") if isinstance(ef, dict) else "forward"
            video_stream = video_stream.ff_filter(
                "vignette", angle=angle, mode=mode
            )
        else:
            print(f"  [WARN] 不支持的特效: {name}", file=sys.stderr)

    return video_stream


# ═══════════════════════════════════════════════════
#  关键帧动画
# ═══════════════════════════════════════════════════

def _apply_keyframe_animation(video_stream, keyframe: str, duration: float,
                               source_size: tuple[int, int] = (1280, 720)):
    """
    关键帧动画——镜头级推拉摇移.

    支持:
      slow_push_in  / push_in   -> 从 1.0x 缓慢推进到 1.15x
      slow_pull_out / pull_out  -> 从 1.15x 缓慢拉远到 1.0x
      pan:right / pan:left / pan:up / pan:down -> 平移
    """
    if not keyframe:
        return video_stream

    import re
    anim = keyframe.lower().strip()
    fps = 30.0
    n_frames = max(1, int(duration * fps))
    w, h = source_size
    canvas = f"{w}x{h}"

    # ── 推拉(zoompan filter) ──
    if anim in ("slow_push_in", "push_in"):
        z_expr = f"1.0 + 0.15 * on / {n_frames}"
        return video_stream.ff_filter("zoompan", z=z_expr, d=1, s=canvas)

    if anim in ("slow_pull_out", "pull_out"):
        z_expr = f"1.15 - 0.15 * on / {n_frames}"
        return video_stream.ff_filter("zoompan", z=z_expr, d=1, s=canvas)

    # ── 平移(zoompan filter,保持输出尺寸不变) ──
    # 用 zoompan 替代 crop,避免尺寸变化导致 concat 失败
    pan_match = re.match(r'pan:(right|left|up|down)(?::(\d+))?', anim)
    if pan_match:
        direction = pan_match.group(1)
        shift_px = int(pan_match.group(2)) if pan_match.group(2) else 60  # 默认位移 60px

        # zoom 补偿:放大到刚好不露黑边
        z_expr = f"iw/(iw-{shift_px})"

        if direction == "right":
            x_expr = f"on * {shift_px} / {n_frames}"
            return video_stream.ff_filter("zoompan", z=z_expr, x=x_expr, y=0, s=canvas, d=1)
        elif direction == "left":
            x_expr = f"{shift_px} - on * {shift_px} / {n_frames}"
            return video_stream.ff_filter("zoompan", z=z_expr, x=x_expr, y=0, s=canvas, d=1)
        elif direction == "down":
            y_expr = f"on * {shift_px} / {n_frames}"
            return video_stream.ff_filter("zoompan", z=z_expr, x=0, y=y_expr, s=canvas, d=1)
        elif direction == "up":
            y_expr = f"{shift_px} - on * {shift_px} / {n_frames}"
            return video_stream.ff_filter("zoompan", z=z_expr, x=0, y=y_expr, s=canvas, d=1)

    print(f"  [WARN] 未知 keyframe_animation: {keyframe}", file=sys.stderr)
    return video_stream


# ═══════════════════════════════════════════════════
#  合成(色键抠像)
# ═══════════════════════════════════════════════════

def _apply_compositing(video_stream, compositing: str):
    """
    合成指令.

    支持:
      chroma_key:green@tolerance=60  -> 绿幕抠像
      luma_key:above@240             -> 亮度键 (保留亮部)
    """
    if not compositing:
        return video_stream

    import re

    if compositing.startswith("chroma_key:"):
        rest = compositing.split(":", 1)[1]
        color = "green"
        similarity = 0.5
        # 解析: green@tolerance=60
        m = re.match(r'(\w+)@tolerance=(\d+)', rest)
        if m:
            color = m.group(1)
            similarity = int(m.group(2)) / 100.0

        return video_stream.ff_filter("colorkey", color=color, similarity=similarity)

    if compositing.startswith("luma_key:"):
        rest = compositing.split(":", 1)[1]
        threshold = 200
        m = re.match(r'(above|below)@(\d+)', rest)
        if m:
            direction = m.group(1)
            threshold = int(m.group(2))
        # 用 geq 做亮度键
        if direction == "above":
            expr = f"if(gt(lum(X,Y),{threshold}), p(X,Y), 0)"
        else:
            expr = f"if(lt(lum(X,Y),{threshold}), p(X,Y), 0)"
        return video_stream.ff_filter("geq", r=expr, g=expr, b=expr)

    print(f"  [WARN] 未知 compositing: {compositing}", file=sys.stderr)
    return video_stream


# ═══════════════════════════════════════════════════
#  裁剪
# ═══════════════════════════════════════════════════

def _apply_crop(video_stream, crop_spec: str, source_size: tuple = (1920, 1080)):
    """
    裁剪滤镜.

    三种格式:
      aspect=9:16[:focus=center|top|bottom]  -> 按比例裁剪
      aspect=1:1                             -> 正方形
      rect=x:y:w:h                           -> 比例坐标 (0-1)
    """
    if not crop_spec:
        return video_stream

    src_w, src_h = source_size

    if crop_spec.startswith("aspect="):
        parts = crop_spec.replace("aspect=", "").split(":")
        target_ratio_str = parts[0]  # "9:16" or "1:1" or "16/9"
        focus = parts[2] if len(parts) > 2 and parts[1] == "focus" else "center"

        # 解析目标宽高比
        if "/" in target_ratio_str:
            tr_w, tr_h = map(float, target_ratio_str.split("/"))
        elif ":" in target_ratio_str:
            tr_w, tr_h = map(float, target_ratio_str.split(":"))
        else:
            print(f"  [WARN] crop aspect 格式无效: {crop_spec}", file=sys.stderr)
            return video_stream

        target_ratio = tr_w / tr_h
        src_ratio = src_w / src_h

        if src_ratio > target_ratio:
            # 源更宽 -> 裁左右
            out_w = int(src_h * target_ratio)
            out_h = src_h
        else:
            # 源更高 -> 裁上下
            out_w = src_w
            out_h = int(src_w / target_ratio)

        # 焦点位置
        if focus == "center":
            x = (src_w - out_w) // 2
            y = (src_h - out_h) // 2
        elif focus == "top":
            x = (src_w - out_w) // 2
            y = 0
        elif focus == "bottom":
            x = (src_w - out_w) // 2
            y = src_h - out_h
        elif focus == "left":
            x = 0
            y = (src_h - out_h) // 2
        elif focus == "right":
            x = src_w - out_w
            y = (src_h - out_h) // 2
        else:
            x = (src_w - out_w) // 2
            y = (src_h - out_h) // 2

        print(f"  [crop] {src_w}x{src_h} -> {out_w}x{out_h} ({target_ratio_str})", file=sys.stderr)
        return video_stream.ff_filter("crop", w=out_w, h=out_h, x=x, y=y)

    elif crop_spec.startswith("rect="):
        # rect=x:y:w:h 比例坐标
        coords = crop_spec.replace("rect=", "").split(":")
        if len(coords) == 4:
            rx, ry, rw, rh = map(float, coords)
            x = int(rx * src_w)
            y = int(ry * src_h)
            w = int(rw * src_w)
            h = int(rh * src_h)
            print(f"  [crop] rect {src_w}x{src_h} -> {w}x{h} @({x},{y})", file=sys.stderr)
            return video_stream.ff_filter("crop", w=w, h=h, x=x, y=y)

    print(f"  [WARN] 未知 crop_spec: {crop_spec}", file=sys.stderr)
    return video_stream


# ═══════════════════════════════════════════════════
#  视频防抖
# ═══════════════════════════════════════════════════

def _apply_stabilize(video_stream, stabilize: bool):
    """视频防抖 — deshake filter(单次分析+稳定)"""
    if not stabilize:
        return video_stream
    print("  [stabilize] 防抖处理中...", file=sys.stderr)
    return video_stream.ff_filter("deshake")


# ═══════════════════════════════════════════════════
#  蒙版(基于 geq 逐像素 alpha)
# ═══════════════════════════════════════════════════

def _apply_masks(video_stream, masks: list):
    """
    蒙版系统 — 椭圆/矩形蒙版 + 羽化.

    mask 格式:
      {"type": "ellipse", "cx": 0.5, "cy": 0.5, "rx": 0.2, "ry": 0.3, "feather": 20, "invert": false}
      {"type": "rect", "x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8, "feather": 15, "invert": true}

    实现: geq 生成蒙版 alpha -> 叠加到原画面(蒙版外变黑/透明)
    """
    if not masks:
        return video_stream

    print(f"  [masks] {len(masks)} 个蒙版...", file=sys.stderr)

    for i, mask in enumerate(masks):
        mtype = mask.get("type", "ellipse")
        invert = mask.get("invert", False)
        feather = int(mask.get("feather", 0))

        # 构建 geq 表达式
        if mtype == "ellipse":
            cx = float(mask.get("cx", 0.5))
            cy = float(mask.get("cy", 0.5))
            rx = float(mask.get("rx", 0.2))
            ry = float(mask.get("ry", 0.3))

            # 椭圆距离公式: ((X-cx*W)^2/(rx*W)^2 + (Y-cy*H)^2/(ry*H)^2)
            # 有羽化: smoothstep 过渡
            if feather > 0:
                fd = feather  # 羽化距离(像素)
                expr = (
                    f"st(1, ((X-{cx}*W)*({cx}*W))/({rx}*W*{rx}*W) + ((Y-{cy}*H)*({cy}*H))/({ry}*H*{ry}*H));"
                    f"if(lte(ld(1),1-{fd}/max(W,H)),1,"
                    f"if(gte(ld(1),1),0,"
                    f"(1-ld(1))*max(W,H)/{fd}))"
                )
            else:
                expr = (
                    f"st(1, ((X-{cx}*W)*({cx}*W))/({rx}*W*{rx}*W) + ((Y-{cy}*H)*({cy}*H))/({ry}*H*{ry}*H));"
                    f"if(lte(ld(1),1),1,0)"
                )

        elif mtype == "rect":
            x = float(mask.get("x", 0))
            y = float(mask.get("y", 0))
            w = float(mask.get("w", 1))
            h = float(mask.get("h", 1))

            if feather > 0:
                expr = (
                    f"st(1, 1);"
                    f"if(lt(X,{x}*W),st(1,(X-{x}*W+{feather})/{feather}));"
                    f"if(gt(X,({x}+{w})*W),st(1,(({x}+{w})*W+{feather}-X)/{feather}));"
                    f"if(lt(Y,{y}*H),st(1,(Y-{y}*H+{feather})/{feather}));"
                    f"if(gt(Y,({y}+{h})*H),st(1,(({y}+{h})*H+{feather}-Y)/{feather}));"
                    f"clip(ld(1),0,1)"
                )
            else:
                expr = (
                    f"if(and(gte(X,{x}*W),lt(X,({x}+{w})*W),"
                    f"gte(Y,{y}*H),lt(Y,({y}+{h})*H)),1,0)"
                )
        else:
            print(f"  [WARN] 未知蒙版类型: {mtype}", file=sys.stderr)
            continue

        # 反转
        if invert:
            expr = f"1-({expr})"

        # geq 生成 alpha 蒙版 -> 乘到原画面 RGB
        # geq 输出: 蒙版区域保持原色,蒙版外变黑
        alpha_expr = f"({expr})"
        lum_expr_r = f"p(X,Y)*({alpha_expr})"
        lum_expr_g = f"p(X,Y)*({alpha_expr})"
        lum_expr_b = f"p(X,Y)*({alpha_expr})"

        print(f"  [mask {i+1}] {mtype} invert={invert} feather={feather}", file=sys.stderr)
        video_stream = video_stream.ff_filter(
            "geq",
            r=lum_expr_r, g=lum_expr_g, b=lum_expr_b,
        )

    return video_stream


# ═══════════════════════════════════════════════════
#  精确关键帧动画(属性级插值)
# ═══════════════════════════════════════════════════

EASING_FUNCTIONS = {
    "linear":      "t",
    "ease_in":     "t*t",
    "ease_out":    "t*(2-t)",
    "ease_in_out": "if(lt(t,0.5),2*t*t,-1+(4-2*t)*t)",
    "hold":        "0",
}


def _apply_keyframes(video_stream, keyframes: list, duration: float,
                     source_size: tuple = (1920, 1080), fps: float = 30.0):
    """
    精确关键帧系统 — 支持 scale/position/rotation/opacity 逐属性插值.

    keyframes 格式:
      [
        {"property": "scale", "keyframes": [
          {"time": 0.0, "value": 1.0, "easing": "ease_out"},
          {"time": 2.0, "value": 1.2, "easing": "linear"}
        ]},
        {"property": "position", "keyframes": [
          {"time": 0.0, "value": [0.5, 0.5], "easing": "ease_in_out"},
          {"time": 3.0, "value": [0.8, 0.3], "easing": "ease_out"}
        ]}
      ]

    实现:逐帧表达式 -> zoompan filter(可同时控制 scale + position + rotation)
    """
    if not keyframes:
        return video_stream

    w, h = source_size
    canvas = f"{w}x{h}"
    n_frames = max(1, int(duration * fps))

    # 解析各属性的关键帧
    prop_kfs = {}
    for prop in keyframes:
        pname = prop.get("property", "")
        kfs = prop.get("keyframes", [])
        if not kfs or not pname:
            continue
        # 补充 implicit 首尾帧
        if kfs[0]["time"] > 0.01:
            kfs.insert(0, {"time": 0.0, "value": kfs[0]["value"], "easing": "linear"})
        if kfs[-1]["time"] < duration - 0.01:
            kfs.append({"time": duration, "value": kfs[-1]["value"], "easing": "linear"})
        prop_kfs[pname] = kfs

    if not prop_kfs:
        return video_stream

    print(f"  [keyframes] {list(prop_kfs.keys())} ({len(prop_kfs)} 属性, {n_frames} 帧)",
          file=sys.stderr)

    # 构建 zoompan 逐帧表达式
    # z = scale, x/y = position offset (in input pixels), d = duration (1 frame)
    z_components = []
    x_components = []
    y_components = []

    # Scale 贡献
    if "scale" in prop_kfs:
        z_components.append(_build_keyframe_expr(prop_kfs["scale"], n_frames))

    # Position 贡献(0-1 归一化坐标 -> pixel offset for zoompan)
    if "position" in prop_kfs:
        pos_kfs = prop_kfs["position"]
        pos_expr = _build_keyframe_expr(
            [{"time": k["time"], "value": k["value"][0], "easing": k.get("easing", "linear")}
             for k in pos_kfs], n_frames
        )
        x_components.append(f"({pos_expr}*{w})")
        pos_expr_y = _build_keyframe_expr(
            [{"time": k["time"], "value": k["value"][1], "easing": k.get("easing", "linear")}
             for k in pos_kfs], n_frames
        )
        y_components.append(f"({pos_expr_y}*{h})")

    # 仅当有 scale 或 position 时才用 zoompan
    if z_components or x_components or y_components:
        z_expr = " * ".join(z_components) if z_components else "1"
        # zoompan x/y = 从输入图像中截取的起始位置
        # 居中策略: (iw - iw/z)/2 + position_offset
        if x_components:
            x_expr = f"(iw-iw/({z_expr}))/2 + ({' + '.join(x_components)})"
        else:
            x_expr = f"(iw-iw/({z_expr}))/2"
        if y_components:
            y_expr = f"(ih-ih/({z_expr}))/2 + ({' + '.join(y_components)})"
        else:
            y_expr = f"(ih-ih/({z_expr}))/2"

        return video_stream.ff_filter("zoompan", z=z_expr, x=x_expr, y=y_expr,
                                       d=1, s=canvas)

    return video_stream


def _build_keyframe_expr(kfs: list, n_frames: int) -> str:
    """
    将关键帧数组转换为逐帧表达式字符串.

    输出: 形如 if(eq(on,0),v0,if(eq(on,1),v1,...)) 的 FFmpeg 表达式
    每帧 = 找到它所在的关键帧区间 ->  easing 插值
    """
    if len(kfs) == 1:
        return str(kfs[0]["value"])

    # 对每帧预计算值
    frame_values = []
    for fn in range(n_frames):
        t = fn / max(1, n_frames - 1)
        # 找区间
        val = kfs[0]["value"]
        for i in range(len(kfs) - 1):
            t0 = kfs[i]["time"] / kfs[-1]["time"] if kfs[-1]["time"] > 0 else 0
            t1 = kfs[i + 1]["time"] / kfs[-1]["time"] if kfs[-1]["time"] > 0 else 1
            if t0 <= t <= t1:
                # 在该区间内插值
                local_t = (t - t0) / max(t1 - t0, 0.001)
                ease = kfs[i + 1].get("easing", "linear")
                # 简化 easing(FFmpeg 表达式内实现有限,取线性近似)
                v0 = float(kfs[i]["value"])
                v1 = float(kfs[i + 1]["value"])
                val = v0 + (v1 - v0) * local_t
                break
        frame_values.append(val)

    # 压缩连续相同值
    # 构建 if-then 链
    # 对足够少的帧可以直接逐帧,对多帧取关键变化点
    if n_frames <= 120:
        expr_parts = []
        for fn, v in enumerate(frame_values):
            expr_parts.append(f"eq(on,{fn})*{v}")
        return "+".join(expr_parts)

    # 长片段:使用分段线性近似
    # 每隔 frame_step 帧设一个关键点
    frame_step = max(1, n_frames // 60)
    segments = []
    for fi in range(0, n_frames, frame_step):
        v_start = frame_values[fi]
        v_end = frame_values[min(fi + frame_step, n_frames - 1)]
        segments.append(f"{v_start}+({v_end - v_start})*(on-{fi})/{frame_step}")
    # 用区间 if 链
    expr_parts = []
    for i, seg in enumerate(segments):
        fn_start = i * frame_step
        fn_end = min((i + 1) * frame_step, n_frames)
        expr_parts.append(f"if(and(gte(on,{fn_start}),lt(on,{fn_end})),{seg}")
    expr = "+".join(expr_parts) + ")" * len(expr_parts) + f"+{frame_values[-1]}*gte(on,{n_frames})"
    return expr


# ═══════════════════════════════════════════════════
#  精确调色(LUT / colorbalance / curves)
# ═══════════════════════════════════════════════════

def _apply_color_grade(video_stream, grade_spec, grade: dict = None):
    """
    调色引擎 (v2).

    支持:
      1. grade_spec (字符串, 向后兼容): "暖色调+降饱和"
      2. grade (结构化 dict):
         {
           "temperature": 5600,       # 色温 K -> colortemperature
           "tint": 10,                # 色调偏移 -> colortemperature
           "exposure": 0.3,           # 曝光偏移 -> eq
           "brightness": 0.1,         # 亮度 -> eq
           "contrast": 1.15,          # 对比度 -> eq (或 curves)
           "saturation": 0.9,         # 饱和度 -> eq
           "highlights": -10,         # 高光 -> colorbalance
           "shadows": 15,             # 阴影 -> colorbalance
           "midtone": 5,              # 中间调 -> colorbalance
           "gamma_r/g/b": 1.1,        # gamma -> eq
           "curves_master": "0/0 0.5/0.6 1/1",   # 主曲线
           "curves_r/g/b": "...",     # 通道曲线
           "black_point": 0.05,       # 黑点 -> colorlevels
           "white_point": 0.95,       # 白点 -> colorlevels
           "lut_path": "/path/to/lut.cube",  # 3D LUT
         }
    """
    # ── 结构化参数(优先) ──
    if grade:
        # eq filter 参数
        eq_params = {}
        contrasts = []
        if "brightness" in grade:
            eq_params["brightness"] = float(grade["brightness"])
        else:
            exposure = float(grade.get("exposure", 0))
            if exposure != 0:
                eq_params["brightness"] = exposure  # 近似

        if "saturation" in grade:
            eq_params["saturation"] = float(grade["saturation"])
        if "contrast" in grade:
            eq_params["contrast"] = float(grade["contrast"])

        if "gamma_r" in grade or "gamma_g" in grade or "gamma_b" in grade:
            if "gamma_r" in grade:
                eq_params["gamma_r"] = float(grade["gamma_r"])
            if "gamma_g" in grade:
                eq_params["gamma_g"] = float(grade["gamma_g"])
            if "gamma_b" in grade:
                eq_params["gamma_b"] = float(grade["gamma_b"])

        if eq_params:
            video_stream = video_stream.ff_filter("eq", **eq_params)

        # colorbalance: shadows/midtones/highlights(三向色轮)
        if "shadows" in grade or "highlights" in grade or "midtone" in grade:
            cb = {}
            if "shadows" in grade:
                s = float(grade["shadows"])
                cb["rs"] = cb["gs"] = cb["bs"] = s / 100.0
            if "highlights" in grade:
                h = float(grade["highlights"])
                cb["rh"] = cb["gh"] = cb["bh"] = -h / 100.0
            if "midtone" in grade:
                m = float(grade["midtone"])
                cb["rm"] = cb["gm"] = cb["bm"] = m / 100.0
            if cb:
                video_stream = video_stream.ff_filter("colorbalance", **cb)

        # colortemperature: 真实色温(Kelvin) + 色调
        if "temperature" in grade or "tint" in grade:
            ct_args = {}
            if "temperature" in grade:
                ct_args["temperature"] = int(float(grade["temperature"]))
            if "tint" in grade:
                ct_args["tint"] = float(grade["tint"])
            if ct_args:
                video_stream = video_stream.ff_filter("colortemperature", **ct_args)

        # colorlevels: 黑/白点(输入级调节)
        if "black_point" in grade or "white_point" in grade:
            try:
                cl_args = {}
                if "black_point" in grade:
                    cl_args["rimin"] = float(grade["black_point"])
                if "white_point" in grade:
                    cl_args["rimax"] = float(grade["white_point"])
                if cl_args:
                    video_stream = video_stream.ff_filter("colorlevels", **cl_args)
            except Exception:
                pass

        # curves: RGB / master 曲线控制点
        curves_parts = []
        master = grade.get("curves_master", "")
        red = grade.get("curves_r", "")
        green = grade.get("curves_g", "")
        blue = grade.get("curves_b", "")
        if master:
            curves_parts.append(f"master='{master}'")
        if red:
            curves_parts.append(f"r='{red}'")
        if green:
            curves_parts.append(f"g='{green}'")
        if blue:
            curves_parts.append(f"b='{blue}'")
        if curves_parts:
            try:
                video_stream = video_stream.ff_filter("curves", ":".join(curves_parts))
            except Exception:
                pass

        # LUT
        lut_path = grade.get("lut_path", "")
        if lut_path:
            lut_wsl = win_to_wsl(lut_path)
            if os.path.exists(lut_wsl):
                print(f"  [grade] LUT: {os.path.basename(lut_path)}", file=sys.stderr)
                video_stream = video_stream.ff_filter("lut3d", file=lut_wsl)
            else:
                print(f"  [WARN] LUT 文件不存在: {lut_wsl}", file=sys.stderr)

        return video_stream

    # ── 字符串 fallback(向后兼容) ──
    if not grade_spec:
        return video_stream

    grade_lower = grade_spec.lower()
    params = {}

    # 亮度/对比度
    if any(kw in grade_lower for kw in ["提亮", "明亮", "bright", "light"]):
        params["brightness"] = 0.05
    if any(kw in grade_lower for kw in ["暗", "dark", "gloom"]):
        params["brightness"] = -0.05

    # 饱和度
    if any(kw in grade_lower for kw in ["鲜艳", "饱和", "vivid", "saturat"]):
        params["saturation"] = 1.3
    if any(kw in grade_lower for kw in ["降饱和", "desaturat", "灰色", "黑白", "grayscale"]):
        params["saturation"] = 0.5

    # 对比度
    if any(kw in grade_lower for kw in ["高对比", "强对比", "high contrast"]):
        params["contrast"] = 1.2

    # 暖色调/冷色调
    if any(kw in grade_lower for kw in ["暖", "warm"]):
        params["gamma_r"] = 1.1
        params["gamma_b"] = 0.9
    if any(kw in grade_lower for kw in ["冷", "cool", "blue"]):
        params["gamma_b"] = 1.1
        params["gamma_r"] = 0.9

    if params:
        return video_stream.ff_filter("eq", **params)
    return video_stream


# ═══════════════════════════════════════════════════
#  调整图层
# ═══════════════════════════════════════════════════

def _apply_adjustment_layers(video_stream, adjustment_layers: list,
                              total_duration: float):
    """
    调整图层 — 在指定时间窗口内对全画面施加特效/调色.

    每个图层:
      {"start_time": 5.0, "duration": 10.0,
       "effects": [{"name": "blur", "radius": 5}],
       "grade": {"contrast": 1.2, "saturation": 0.8},
       "blend_mode": "normal"}

    实现: split -> apply -> concat(按图层时间线切分重组)
    """
    if not adjustment_layers:
        return video_stream

    import bmf

    # 按 start_time 排序
    sorted_layers = sorted(adjustment_layers, key=lambda l: l.get("start_time", 0))

    # 合并重叠图层(简化:取所有图层的时间切割点,构建段列表)
    cut_points = [0.0]
    for layer in sorted_layers:
        t0 = max(0, layer.get("start_time", 0))
        t1 = min(total_duration, t0 + layer.get("duration", 1))
        cut_points.append(t0)
        cut_points.append(t1)
    cut_points.append(total_duration)
    cut_points = sorted(set(cut_points))

    # 按切点构建段
    segments = []
    for i in range(len(cut_points) - 1):
        seg_start = cut_points[i]
        seg_end = cut_points[i + 1]
        seg_dur = seg_end - seg_start
        if seg_dur < 0.01:
            continue
        segments.append({
            "start": seg_start,
            "end": seg_end,
            "duration": seg_dur,
        })

    if len(segments) <= 1:
        return video_stream

    print(f"  [adj_layers] {len(adjustment_layers)} 图层, {len(segments)} 段",
          file=sys.stderr)

    # 逐段裁剪 + 应用该时间段内生效的图层效果 + concat
    seg_videos = []
    for i, seg in enumerate(segments):
        t_start, dur = seg["start"], seg["duration"]

        # 找出在此段时间内生效的图层
        active_layers = []
        for layer in sorted_layers:
            lt0 = layer.get("start_time", 0)
            lt1 = lt0 + layer.get("duration", 1)
            if lt0 < seg["end"] and lt1 > seg["start"]:
                active_layers.append(layer)

        # 裁剪该段
        seg_v = video_stream.ff_filter("trim", start=t_start, duration=dur)

        # 对该段应用所有生效图层
        for layer in active_layers:
            effects = layer.get("effects", [])
            if effects:
                seg_v = _apply_effects(seg_v, effects)
            grade = layer.get("grade", {})
            if grade:
                seg_v = _apply_color_grade(seg_v, "", grade)
            # blend_mode 对全局图层暂不适用(已是成品画面)

        seg_videos.append(seg_v)
        print(f"  [adj {i+1}] {t_start:.1f}s-{seg['end']:.1f}s "
              f"({len(active_layers)} 图层活跃)", file=sys.stderr)

    # Concat 所有段
    result = seg_videos[0]
    for j in range(1, len(seg_videos)):
        result = bmf.concat(result, seg_videos[j], n=2, v=1, a=0)

    return result


# ═══════════════════════════════════════════════════
#  曲线变速
# ═══════════════════════════════════════════════════

def _apply_speed_ramp(graph, source_wsl: str, t_start: float,
                      duration: float, speed_ramp: list):
    """
    曲线变速——将镜头按 speed_ramp 分段,逐段变速后 concat 回一个流.

    speed_ramp 格式:
      [{"pct": 0,  "speed": 0.3},
       {"pct": 50, "speed": 1.0},
       {"pct": 100,"speed": 3.0}]

    含义: 0-50% 时间段从 0.3x 渐变到 1.0x,50-100% 从 1.0x 到 3.0x.
    实现: 按段平均速度做匀速,多段 concat.
    """
    if not speed_ramp or len(speed_ramp) < 2:
        return None, None, duration

    import bmf

    print(f"  [speed_ramp] {len(speed_ramp)-1} 段曲线 -> 逐段变速...", file=sys.stderr)

    seg_videos = []
    seg_audios = []
    total_out = 0.0

    for i in range(len(speed_ramp) - 1):
        pct_s = speed_ramp[i]["pct"] / 100.0
        pct_e = speed_ramp[i + 1]["pct"] / 100.0
        spd_s = speed_ramp[i]["speed"]
        spd_e = speed_ramp[i + 1]["speed"]

        seg_src_start = t_start + pct_s * duration
        seg_src_end = t_start + pct_e * duration
        seg_src_dur = seg_src_end - seg_src_start

        # 该段取平均速度做匀速近似
        avg_speed = (spd_s + spd_e) / 2.0

        # decode 该段
        seg_stream = graph.decode({
            "input_path": source_wsl,
            "start_time": seg_src_start,
        })
        seg_v = seg_stream['video'].ff_filter("trim", start=0, duration=seg_src_dur)
        seg_a = seg_stream['audio'].ff_filter("atrim", start=0, duration=seg_src_dur)

        if avg_speed != 1.0:
            seg_v = seg_v.ff_filter("setpts", expr=f"{1/avg_speed}*PTS")
            seg_a = seg_a.ff_filter("atempo", tempo=avg_speed)

        seg_videos.append(seg_v)
        seg_audios.append(seg_a)
        total_out += seg_src_dur / avg_speed

    # concat 各段
    v_chain = seg_videos[0]
    a_chain = seg_audios[0]
    for j in range(1, len(seg_videos)):
        v_chain = bmf.concat(v_chain, seg_videos[j], n=2, v=1, a=0)
        a_chain = bmf.concat(a_chain, seg_audios[j], n=2, v=0, a=1)

    print(f"    -> 总输出 {total_out:.1f}s", file=sys.stderr)
    return v_chain, a_chain, total_out


# ═══════════════════════════════════════════════════
#  叠加文字
# ═══════════════════════════════════════════════════

def _apply_overlay_text(video_stream, text: str, duration: float, animation: str = "fadeSlide"):
    """
    叠加文字——drawtext filter + 入场动画.

    位置: 底部居中,白色文字+黑色阴影
    动画:
      fadeSlide  -> 从下方滑入+淡入(默认)
      pop        -> 从大缩小弹入
      karaoke    -> 暂不支持,fallback 到 fadeSlide
      typewriter -> 暂不支持,fallback 到 fadeSlide
    """
    if not text:
        return video_stream

    # 截断过长文字
    display_text = text[:100] if len(text) > 100 else text
    # 转义特殊字符
    display_text = display_text.replace("'", "\\'").replace(":", "\\:")

    fontsize = 28
    x = "(w-text_w)/2"
    anim_dur = min(0.5, duration * 0.3)  # 动画时长
    t_show = duration * 0.15  # 开始显示时间
    t_hide = duration * 0.85  # 开始隐藏时间

    fontfile = _get_fontfile(text)
    if animation == "pop":
        # 弹入:字体从 2x 缩小到 1x
        fontsize_expr = f"if(lt(t,{t_show}),0,if(lt(t,{t_show+anim_dur}),56-28*(t-{t_show})/{anim_dur},28))"
        y_val = "h-th-60"
        kwargs = dict(
            text=display_text,
            fontsize=fontsize_expr,
            fontcolor="white",
            shadowcolor="black@0.7",
            shadowx=2, shadowy=2,
            x=x, y=y_val,
            enable=f"between(t,{t_show},{t_hide})",
        )
        if fontfile:
            kwargs["fontfile"] = fontfile
        return video_stream.ff_filter("drawtext", **kwargs)
    elif animation == "fadeSlide":
        # 从下方 20px 滑到目标位置 + 淡入
        y_expr = f"if(lt(t,{t_show}),h,if(lt(t,{t_show+anim_dur}),h-20-(t-{t_show})*20/{anim_dur},h-th-60))"
        alpha_expr = f"if(lt(t,{t_show}),0,if(lt(t,{t_show+anim_dur}),(t-{t_show})/{anim_dur},1))"
        kwargs = dict(
            text=display_text,
            fontsize=fontsize,
            fontcolor="white",
            alpha=alpha_expr,
            shadowcolor="black@0.7",
            shadowx=2, shadowy=2,
            x=x, y=y_expr,
            enable=f"between(t,{t_show},{t_hide})",
        )
        if fontfile:
            kwargs["fontfile"] = fontfile
        return video_stream.ff_filter("drawtext", **kwargs)
    else:
        if animation not in ("fadeSlide", "pop"):
            print(f"  [WARN] subtitle_animation='{animation}' 不支持,fallback 到静态", file=sys.stderr)

    # 静态 fallback
    kwargs = dict(
        text=display_text,
        fontsize=fontsize,
        fontcolor="white",
        shadowcolor="black@0.7",
        shadowx=2, shadowy=2,
        x=x, y="h-th-60",
        enable=f"between(t,{t_show},{t_hide})",
    )
    if fontfile:
        kwargs["fontfile"] = fontfile
    return video_stream.ff_filter("drawtext", **kwargs)


# ═══════════════════════════════════════════════════
#  标注
# ═══════════════════════════════════════════════════

ANNOTATION_STYLES = {
    "highlight": {
        "text_prefix": "★ ",
        "fontcolor": "yellow",
        "fontsize": 32,
        "border": True,
        "boxcolor": "black@0.5",
    },
    "arrow": {
        "text_prefix": "-> ",
        "fontcolor": "white",
        "fontsize": 30,
        "border": True,
        "boxcolor": "red@0.6",
    },
    "kill": {
        "text_prefix": "⚡ KILL! ",
        "fontcolor": "red",
        "fontsize": 36,
        "border": True,
        "boxcolor": "black@0.7",
    },
    "label": {
        "text_prefix": "",
        "fontcolor": "white",
        "fontsize": 26,
        "border": True,
        "boxcolor": "black@0.6",
        "boxborderw": 4,
    },
    "emphasis": {
        "text_prefix": "",
        "fontcolor": "white",
        "fontsize": 40,
        "border": False,
        "shadowcolor": "black",
        "shadowx": 3,
        "shadowy": 3,
    },
    "badge": {
        "text_prefix": "● ",
        "fontcolor": "white",
        "fontsize": 24,
        "border": True,
        "boxcolor": "#FF5722@0.8",
        "boxborderw": 6,
    },
    "timer": {
        "text_prefix": "⏱ ",
        "fontcolor": "white",
        "fontsize": 28,
        "border": False,
    },
    "progress": {
        "text_prefix": "",
        "fontcolor": "#00FF00@0.9",
        "fontsize": 24,
        "border": False,
    },
}


def _apply_annotation(video_stream, ann_type: str, duration: float,
                      ann_x: float = 0.5, ann_y: float = 0.4):
    """
    标注叠加——在指定位置渲染标注图形/文字.

    支持的标注类型:
      highlight  -> ★ 高亮标记(黄色底框)
      arrow      -> -> 箭头指向
      kill       -> ⚡ KILL! 击杀标记
      label      -> 标签框
      emphasis   -> 超大强调文字
      badge      -> ● 徽章标记
      timer      -> ⏱ 计时器标记
      progress   -> 进度条(底部 drawbox)
    """
    if not ann_type:
        return video_stream

    style = ANNOTATION_STYLES.get(ann_type)
    if not style:
        print(f"  [WARN] unknown annotation_type: {ann_type}", file=sys.stderr)
        return video_stream

    prefix = style.get("text_prefix", "")
    text = prefix + ann_type.upper()
    fontsize = style.get("fontsize", 28)
    fontcolor = style.get("fontcolor", "white")
    border = style.get("border", False)
    boxcolor = style.get("boxcolor", "black@0.5")
    boxborderw = style.get("boxborderw", 4)
    shadowcolor = style.get("shadowcolor", "")
    shadowx = style.get("shadowx", 2)
    shadowy = style.get("shadowy", 2)

    # 位置:ann_x, ann_y 是 0-1 比例
    x_expr = f"{ann_x}*w-text_w/2"
    y_expr = f"{ann_y}*h-th/2"

    # 出现时机:在前 20% 时间段显示
    t_show = duration * 0.05
    t_hide = min(duration * 0.35, duration - 0.3)

    drawtext_opts = {
        "text": text,
        "fontsize": fontsize,
        "fontcolor": fontcolor,
        "x": x_expr,
        "y": y_expr,
        "enable": f"between(t,{t_show},{t_hide})",
    }

    if shadowcolor:
        drawtext_opts["shadowcolor"] = shadowcolor
        drawtext_opts["shadowx"] = shadowx
        drawtext_opts["shadowy"] = shadowy

    if border:
        drawtext_opts["box"] = 1
        drawtext_opts["boxcolor"] = boxcolor
        drawtext_opts["boxborderw"] = boxborderw

    fontfile = _get_fontfile(text)
    if fontfile:
        drawtext_opts["fontfile"] = fontfile

    return video_stream.ff_filter("drawtext", **drawtext_opts)


# ═══════════════════════════════════════════════════
#  字体
# ═══════════════════════════════════════════════════

# WSL2 内可用中文字体路径(二级后备:Windows 字体 -> Linux 包字体 -> 通用路径)
_CJK_FONT_CANDIDATES = [
    # ── Windows 字体(通过 /mnt/c 挂载) ──
    "/mnt/c/Windows/Fonts/msyh.ttc",        # 微软雅黑(最常用)
    "/mnt/c/Windows/Fonts/msyhbd.ttc",      # 微软雅黑粗体
    "/mnt/c/Windows/Fonts/simhei.ttf",      # 黑体
    "/mnt/c/Windows/Fonts/simsun.ttc",      # 宋体
    "/mnt/c/Windows/Fonts/simkai.ttf",      # 楷体
    # ── Linux 包字体(WSL 内 apt install fonts-*) ──
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",        # 文泉驿正黑
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",      # 文泉驿微米黑
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",  # Noto CJK
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf", # Droid
    # ── 通用后备 ──
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",     # DejaVu(有部分CJK)
]


def _has_cjk(text: str) -> bool:
    """检测文本是否包含中日韩字符"""
    for ch in text:
        cp = ord(ch)
        if (0x4E00 <= cp <= 0x9FFF or   # CJK统一汉字
            0x3400 <= cp <= 0x4DBF or   # CJK扩展A
            0x20000 <= cp <= 0x2A6DF or # CJK扩展B
            0xF900 <= cp <= 0xFAFF or   # CJK兼容汉字
            0x3040 <= cp <= 0x309F or   # 平假名
            0x30A0 <= cp <= 0x30FF or   # 片假名
            0xAC00 <= cp <= 0xD7AF):    # 韩文
            return True
    return False


def _get_fontfile(text: str) -> str:
    """根据文本内容返回合适的字体路径"""
    if not _has_cjk(text):
        return ""  # 英文用 FFmpeg 默认字体
    for fp in _CJK_FONT_CANDIDATES:
        if os.path.exists(fp):
            return fp
    return ""  # 找不到就算了


# ═══════════════════════════════════════════════════
#  GPU 检测
# ═══════════════════════════════════════════════════

def _probe_resolution(source_wsl: str) -> tuple[int, int]:
    """用 ffprobe 探源视频分辨率,缓存结果."""
    cache = getattr(_probe_resolution, "_cache", None)
    if cache is None:
        _probe_resolution._cache = {}
        cache = _probe_resolution._cache

    if source_wsl in cache:
        return cache[source_wsl]

    try:
        import subprocess as sp
        result = sp.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=p=0", source_wsl],
            capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace",
        )
        w, h = map(int, result.stdout.strip().split(","))
        cache[source_wsl] = (w, h)
        return (w, h)
    except Exception:
        cache[source_wsl] = (1280, 720)
        return (1280, 720)


def _check_cuda() -> bool:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
        return result.returncode == 0 and len(result.stdout.strip()) > 0
    except Exception:
        return False


# ═══════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print("用法: python3 bmf_worker.py <editing_script.json> [output.mp4]", file=sys.stderr)
        sys.exit(1)

    script_path = sys.argv[1]

    with open(script_path, "r", encoding="utf-8") as f:
        script = json.load(f)

    # 优先从 JSON 读输出路径(避免中文路径在命令行传递时乱码)
    output_path = script.pop("_output_path", None)
    if not output_path and len(sys.argv) >= 3:
        output_path = sys.argv[2]
    if not output_path:
        output_path = os.path.join(os.path.dirname(script_path), "output.mp4")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    print(f"[BMF Worker] 开始渲染: {script.get('title', '未命名')}", file=sys.stderr)
    t_start = time.time()

    try:
        build_bmf_graph(script, output_path)

        elapsed = time.time() - t_start
        output_size_mb = os.path.getsize(output_path) / (1024 * 1024) if os.path.exists(output_path) else 0
        print(f"[BMF Worker] DONE: {elapsed:.1f}s, {output_size_mb:.1f}MB", file=sys.stderr)

    except Exception as e:
        print(f"[BMF Worker] FAIL: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
