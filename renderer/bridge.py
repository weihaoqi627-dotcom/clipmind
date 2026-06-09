"""
WSL2 BMF 渲染桥接层
───────────────────
Windows 侧入口:接收 EditingScript -> 序列化 JSON -> WSL2 BMF Worker -> 返回成片路径

用法:
  from renderer.bridge import WslBmfBridge

  bridge = WslBmfBridge()
  output_path = bridge.render(editing_script, output_dir="C:\\output")
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional


# ═══════════════════════════════════════════════════
#  路径转换
# ═══════════════════════════════════════════════════

def _win_to_wsl(win_path: str) -> str:
    """C:\\Users\\... -> /mnt/c/Users/..."""
    if win_path.startswith("/mnt/"):
        return win_path
    win_path = win_path.replace("\\", "/")
    if len(win_path) >= 2 and win_path[1] == ":":
        drive = win_path[0].lower()
        return f"/mnt/{drive}{win_path[2:]}"
    return win_path


def _wsl_to_win(wsl_path: str) -> str:
    """/mnt/c/Users/... -> C:\\Users\\..."""
    if not wsl_path.startswith("/mnt/"):
        return wsl_path
    parts = wsl_path.split("/")
    drive = parts[2].upper()
    rest = "/".join(parts[3:])
    return f"{drive}:\\{rest.replace('/', chr(92))}"


class WslBmfBridge:
    """
    Windows ↔ WSL2 BMF 渲染桥接.
    """

    def __init__(self, wsl_distro: str = "Ubuntu-22.04", worker_path: str = None):
        """
        Args:
            wsl_distro: WSL2 发行版名称
            worker_path: bmf_worker.py 的路径(Windows 格式),默认为本文件同目录
        """
        self.wsl_distro = wsl_distro
        if worker_path is None:
            worker_path = str(Path(__file__).parent / "bmf_worker.py")
        self.worker_path = worker_path
        self.worker_path_wsl = _win_to_wsl(worker_path)

    def _check_wsl(self) -> bool:
        """检查 WSL 是否可用"""
        try:
            result = subprocess.run(
                ["wsl", "-d", self.wsl_distro, "--", "python3", "--version"],
                capture_output=True, text=True, timeout=15,
                encoding="utf-8", errors="replace",
            )
            return result.returncode == 0
        except Exception:
            return False

    def _check_bmf(self) -> bool:
        """检查 WSL2 内 BMF 是否可用"""
        try:
            result = subprocess.run(
                ["wsl", "-d", self.wsl_distro, "--", "python3", "-c", "import bmf; print('OK')"],
                capture_output=True, text=True, timeout=15,
                encoding="utf-8", errors="replace",
            )
            return "OK" in result.stdout
        except Exception:
            return False

    def _check_hf(self) -> bool:
        """检查 HyperFrames CLI 是否可用(需要 Node.js + headless Chrome)"""
        try:
            from hf_engine.engine import render_subtitle, render_overlay
            # 轻量测试:尝试导入成功即可,不实际渲染
            return True
        except ImportError:
            return False
        except Exception:
            return False

    def status(self) -> dict:
        """返回桥接状态"""
        return {
            "wsl_available": self._check_wsl(),
            "bmf_available": self._check_bmf(),
            "hf_available": self._check_hf(),
            "distro": self.wsl_distro,
            "worker_path": self.worker_path_wsl,
        }

    # ═══════════════════════════════════════════════════
    #  HF 能力矩阵 + 效果路由(从预设库动态加载)
    # ═══════════════════════════════════════════════════

    try:
        from presets.effects import get_hf_capabilities, get_bmf_capabilities
        HF_CAPABILITIES = get_hf_capabilities()
        BMF_CAPABILITIES = get_bmf_capabilities()
    except ImportError:
        # 预设库不可用时的硬编码后备(保持自举能力)
        HF_CAPABILITIES = {
            "transitions": {"glitch", "light_leak", "dip_black", "blur_fade",
                           "ripple", "mosaic_dissolve", "iris", "page_curl",
                           "speed_lines", "chromatic_aberration", "film_burn"},
            "effects": {
                # 2D WebGL shader(12个)
                "bloom", "glow_advanced", "neon_glow", "god_rays", "light_rays",
                "chromatic_shift", "color_dispersion", "scan_lines", "vignette_animated",
                "noise_grain", "film_grain", "digital_glitch",
                # 3D Three.js(13个)
                "particles", "particle_burst", "particle_flow", "sparkle",
                "fire", "smoke", "energy_field", "lens_flare",
                "3d_text", "3d_logo", "3d_particles", "shatter", "hologram",
            },
            "overlays": {"title_card", "lower_third", "center_badge",
                        "logo_reveal", "countdown", "progress_bar"},
            "subtitles": {"fadeSlide", "pop", "karaoke", "typewriter"},
        }
        BMF_CAPABILITIES = {
            "transitions": {"cut", "fade", "dissolve"},
            "effects": {"blur", "sharpen", "noise"},
        }

    @classmethod
    def hf_can_render(cls, category: str, name: str) -> bool:
        """检查 HF 能否渲染某类中的某项"""
        cap = cls.HF_CAPABILITIES.get(category, set())
        return name in cap

    @classmethod
    def bmf_can_render(cls, category: str, name: str) -> bool:
        return name in cls.BMF_CAPABILITIES.get(category, set())

    def _route_effects_to_hf(
        self,
        script_dict: dict,
        output_dir: str,
        hf_width: int,
        hf_height: int,
        hf_fps: int,
    ) -> dict:
        """
        效果路由器:逐效果判断 HF 能做就 HF 做,HF 不能就走 BMF.

        策略:
        1. 转场:HF-capable -> 预渲染 HF MOV;BMF-only -> 保留原样让 worker 处理
        2. 镜头特效:HF-capable -> 生成 HF 叠层 MOV 插入 overlays;BMF-only -> 保留 effects 字段
        3. 叠层:HF-capable -> 渲染 MOV 设 mov_path;BMF-only -> engine 改为 "native"
        4. 字幕动画:HF-capable -> 渲染 MOV;BMF-only -> engine 改为 "native" + drawtext

        Returns:
            修改后的 script_dict(增补了 HF mov_path 或降级标注)
        """
        from hf_engine.engine import render_subtitle, render_overlay, render_effect, register_effect
        from hf_engine import generate_transition_assets

        print("[Bridge] 效果路由:逐效果 HF/BMF 分发...", flush=True)
        hf_rendered = 0
        bmf_fallback = 0

        # ── 1. 转场路由 ──
        transitions = script_dict.get("transitions", [])
        hf_transitions = []
        bmf_transitions = []
        for t in transitions:
            t_type = t.get("type", "cut")
            if self.hf_can_render("transitions", t_type):
                hf_transitions.append(t)
            else:
                bmf_transitions.append(t)

        if hf_transitions:
            print(f"  [路由] 转场: {len(hf_transitions)}->HF, {len(bmf_transitions)}->BMF", flush=True)
            try:
                temp_dict = dict(script_dict)
                temp_dict["transitions"] = hf_transitions
                script_dict = generate_transition_assets(
                    temp_dict,
                    output_dir=output_dir,
                    width=hf_width,
                    height=hf_height,
                    fps=hf_fps,
                )
                # 恢复完整 transitions(HF生成的 + BMF原样的)
                all_trans = script_dict.get("transitions", [])
                hf_count = len(all_trans) - len(bmf_transitions)
                hf_rendered += hf_count
                bmf_fallback += len(bmf_transitions)
            except Exception as e:
                print(f"  [路由] HF 转场失败: {e},全部降级 BMF", flush=True)
                bmf_fallback += len(hf_transitions)

        # ── 2. 镜头特效路由 ──
        #    镜头特效本来是 BMF filter(在 worker 内逐 shot 应用)
        #    HF-capable 特效 -> 转为 overlays[] 中的 HF MOV 叠层
        new_overlays = list(script_dict.get("overlays", []))
        shots = script_dict.get("shots", [])

        # 构建 shot 时间线(计算累积时间)
        shot_timeline = {}
        t_cum = 0.0
        for s in shots:
            sid = s.get("shot_id", "")
            tr = s.get("time_range", [0, 0])
            raw_dur = tr[1] - tr[0]
            speed = s.get("speed", 1.0) or 1.0
            dur = raw_dur / speed if speed != 1.0 else raw_dur
            freeze_dur = s.get("freeze_duration", 0) or 0
            dur += freeze_dur
            shot_timeline[sid] = {"start": t_cum, "end": t_cum + dur, "duration": dur}
            t_cum += dur

        for s in shots:
            effects = s.get("effects", []) or []
            if not effects:
                continue
            sid = s.get("shot_id", "")
            st = shot_timeline.get(sid, {})
            shot_start = st.get("start", 0)
            shot_dur = st.get("duration", 1)

            hf_effects = []
            bmf_effects = []
            for eff in effects:
                eff_name = eff.get("name", "") if isinstance(eff, dict) else str(eff)
                if self.hf_can_render("effects", eff_name):
                    hf_effects.append(eff)
                else:
                    bmf_effects.append(eff)

            if hf_effects:
                # 替换 shot 的 effects 为仅 BMF 部分
                s["effects"] = bmf_effects if bmf_effects else []

                for i, eff in enumerate(hf_effects):
                    eff_name = eff.get("name", "") if isinstance(eff, dict) else str(eff)
                    eff_props = eff if isinstance(eff, dict) else {}
                    try:
                        # 特效走 render_effect()(不是 render_overlay()!)
                        params = {k: v for k, v in eff_props.items()
                                  if k not in ("name", "label", "blend_mode")}
                        mov = render_effect(
                            effect_name=eff_name,
                            output_dir=output_dir,
                            width=hf_width,
                            height=hf_height,
                            fps=hf_fps,
                            duration=shot_dur,
                            params=params if params else None,
                        )
                        ov = {
                            "element_id": f"hf_eff_{sid}_{i}",
                            "type": eff_name,
                            "engine": "hyperframes",
                            "start_time": shot_start,
                            "duration": shot_dur,
                            "blend_mode": eff_props.get("blend_mode", "screen"),
                            "props": {"mov_path": mov},
                        }
                        new_overlays.append(ov)
                        hf_rendered += 1
                        print(f"  [路由] 特效 {sid}.{eff_name} -> HF MOV {Path(mov).name}")
                    except NotImplementedError:
                        # 模板不存在——检查 VL 是否提供了 shader(当场生成)
                        shader_code = eff_props.get("shader", "")
                        if shader_code:
                            print(f"  [路由] 特效 {eff_name} 动态注册(VL提供shader, {len(shader_code)}字符)")
                            registered = register_effect(
                                effect_name=eff_name,
                                shader_code=shader_code,
                                description=eff_props.get("description", ""),
                                persist=True,
                            )
                            if registered:
                                # 重试渲染
                                try:
                                    params = {k: v for k, v in eff_props.items()
                                              if k not in ("name", "label", "blend_mode", "shader", "description")}
                                    mov = render_effect(
                                        effect_name=eff_name,
                                        output_dir=output_dir,
                                        width=hf_width,
                                        height=hf_height,
                                        fps=hf_fps,
                                        duration=shot_dur,
                                        params=params if params else None,
                                    )
                                    ov = {
                                        "element_id": f"hf_eff_{sid}_{i}",
                                        "type": eff_name,
                                        "engine": "hyperframes",
                                        "start_time": shot_start,
                                        "duration": shot_dur,
                                        "blend_mode": eff_props.get("blend_mode", "screen"),
                                        "props": {"mov_path": mov},
                                    }
                                    new_overlays.append(ov)
                                    hf_rendered += 1
                                    print(f"  [路由] 特效 {sid}.{eff_name} -> HF MOV(动态生成) {Path(mov).name}")
                                    continue
                                except Exception as e2:
                                    print(f"  [路由] 特效 {eff_name} 动态注册后渲染仍失败: {e2}")
                        # 没有 shader 或注册/渲染失败 -> 降级 BMF
                        print(f"  [路由] 特效 {eff_name} 无shader->BMF")
                        bmf_effects.append(eff)
                        bmf_fallback += 1
                    except Exception as e:
                        print(f"  [路由] 特效 {sid}.{eff_name} HF失败->BMF: {e}")
                        bmf_effects.append(eff)
                        bmf_fallback += 1

        # ── 3. 叠层路由 ──
        overlays = script_dict.get("overlays", [])
        for ov in overlays:
            if ov.get("engine") != "hyperframes":
                continue
            ov_type = ov.get("type", "")
            props = ov.get("props", {})
            dur = ov.get("duration", 3.0)

            # 已经有 mov_path 的跳过(之前已生成)
            if props.get("mov_path"):
                continue

            if self.hf_can_render("overlays", ov_type):
                try:
                    mov = render_overlay(
                        overlay_type=ov_type,
                        text=props.get("text", ""),
                        sub_text=props.get("sub_text", ""),
                        output_dir=output_dir,
                        width=hf_width,
                        height=hf_height,
                        duration=dur,
                        font_size=props.get("font_size", 56),
                        accent_color=props.get("accent_color", "#FF4444"),
                    )
                    ov.setdefault("props", {})["mov_path"] = mov
                    hf_rendered += 1
                    print(f"  [路由] 叠层 {ov_type} -> HF MOV {Path(mov).name}")
                except Exception as e:
                    print(f"  [路由] 叠层 {ov_type} HF失败->BMF: {e}")
                    bmf_fallback += 1
            else:
                # HF 不能渲染的叠层类型 -> 降级
                print(f"  [路由] 叠层 {ov_type} HF不支持->BMF")
                bmf_fallback += 1

        # ── 4. 字幕动画路由 ──
        #    字幕目前嵌在 shot 的 overlay_text + subtitle_animation 字段
        #    HF-capable 的动画类型 -> 生成 HF 字幕 MOV
        for s in shots:
            anim = s.get("subtitle_animation", "")
            overlay_text = s.get("overlay_text", "")
            if not anim or not overlay_text:
                continue
            if not self.hf_can_render("subtitles", anim):
                continue

            sid = s.get("shot_id", "")
            st = shot_timeline.get(sid, {})
            shot_start = st.get("start", 0)
            shot_dur = st.get("duration", 1)

            try:
                mov = render_subtitle(
                    text=overlay_text,
                    animation=anim,
                    output_dir=output_dir,
                    width=hf_width,
                    height=hf_height,
                    duration=shot_dur,
                    font_size=48,
                    font_color="#FFFFFF",
                    stroke_color="#000000",
                    stroke_width=2,
                    position="bottom",
                )
                ov = {
                    "element_id": f"hf_sub_{sid}",
                    "type": "subtitle",
                    "engine": "hyperframes",
                    "start_time": shot_start,
                    "duration": shot_dur,
                    "blend_mode": "normal",
                    "props": {"mov_path": mov, "text": overlay_text},
                }
                new_overlays.append(ov)
                hf_rendered += 1
                # 清除 shot 上的字幕文本(避免 BMF drawtext 重复渲染)
                s["overlay_text"] = ""
                s["subtitle_animation"] = ""
                print(f"  [路由] 字幕 {sid}.{anim} -> HF MOV {Path(mov).name}")
            except Exception as e:
                print(f"  [路由] 字幕 {sid}.{anim} HF失败->BMF: {e}")
                bmf_fallback += 1

        script_dict["overlays"] = new_overlays

        print(f"  [路由] 完成: {hf_rendered}->HF, {bmf_fallback}->BMF", flush=True)
        return script_dict

    def _degrade_hf_features(self, script_dict: dict) -> dict:
        """
        HF 不可用时,把依赖 HF 的特性降级为 BMF 原生方案.

        降级策略:
        - 非原生转场 -> dissolve(BMF xfade 原生支持)
        - HF 叠层 -> BMF drawtext(engine 改为 "native")
        - HF 字幕 -> BMF drawtext(engine 改为 "native")
        """
        print("[Bridge] HF 不可用,启用 BMF 原生降级方案", flush=True)

        # 1. 转场降级:非原生 -> dissolve
        # 原生 xfade: cut, fade, dissolve, fadeblack, fadewhite,
        #              slideright/left/down/up, wiperight/left/down/up
        NATIVE_XFADE = {
            "cut", "fade", "dissolve", "fadeblack", "fadewhite",
            "slide_left", "slide_right", "slide_up", "slide_down",
            "wipe_left", "wipe_right", "wipe_up", "wipe_down",
        }
        transitions = script_dict.get("transitions", [])
        for t in transitions:
            t_type = t.get("type", "cut")
            if t_type not in NATIVE_XFADE:
                print(f"  [降级] 转场 {t_type} -> dissolve")
                t["type"] = "dissolve"

        # 2. HF 叠层/字幕降级 -> BMF drawtext
        overlays = script_dict.get("overlays", [])
        for ov in overlays:
            if ov.get("engine") != "hyperframes":
                continue
            ov_type = ov.get("type", "")
            props = ov.get("props", {})
            print(f"  [降级] HF 叠层 [{ov_type}] -> BMF drawtext")

            if ov_type in ("subtitle", "animated_subtitle"):
                # HF 字幕 -> BMF drawtext(丢动画,保留文字+位置+样式)
                ov["engine"] = "native"
                # drawtext 参数映射
                position = props.get("position", "bottom")
                y_map = {"top": "h*0.08", "center": "h*0.5-text_h/2",
                         "bottom": "h*0.85", "upper_third": "h*0.25"}
                ov["props"] = {
                    "text": props.get("text", ""),
                    "font_size": props.get("font_size", 48),
                    "font_color": props.get("font_color", "#FFFFFF"),
                    "stroke_color": props.get("stroke_color", "#000000"),
                    "stroke_width": props.get("stroke_width", 2),
                    "y": y_map.get(position, "h*0.85"),
                    "box": props.get("box", 0),
                    "fade_in": 0.3,   # BMF 原生淡入替代动画
                    "fade_out": 0.3,
                }
            else:
                # 标题卡/其他 -> BMF drawtext
                ov["engine"] = "native"
                ov["props"] = {
                    "text": props.get("text", ""),
                    "sub_text": props.get("sub_text", ""),
                    "font_size": props.get("font_size", 56),
                    "font_color": props.get("font_color", "#FFFFFF"),
                    "stroke_color": "#000000",
                    "stroke_width": 3,
                    "y": "h*0.35",
                    "box": 1,
                    "box_color": "black@0.5",
                    "fade_in": 0.4,
                    "fade_out": 0.4,
                }

        return script_dict

    def render(
        self,
        editing_script,          # EditingScript 对象 或 dict
        output_dir: str = "",
        output_name: str = "",
        timeout: int = 1800,     # 30分钟超时
    ) -> str:
        """
        渲染 EditingScript -> MP4 成片.

        Args:
            editing_script: EditingScript 对象(有 to_json() 方法)或 dict
            output_dir: 输出目录(Windows 路径),默认为系统临时目录
            output_name: 输出文件名(不含扩展名),默认用 title
            timeout: 渲染超时秒数

        Returns:
            成片 MP4 的 Windows 路径

        Raises:
            RuntimeError: WSL/BMF 不可用或渲染失败
        """
        # 检查环境
        status = self.status()
        if not status["wsl_available"]:
            raise RuntimeError(
                f"WSL2 ({self.wsl_distro}) 不可用.请确保 WSL2 已安装并运行.\n"
                f"尝试: wsl --install -d {self.wsl_distro}"
            )
        if not status["bmf_available"]:
            raise RuntimeError(
                f"BMF 未在 WSL2 中安装.请运行:\n"
                f"  wsl -d {self.wsl_distro} -- pip3 install BabitMF BabitMF-GPU"
            )

        # 准备脚本数据
        if hasattr(editing_script, "to_json"):
            script_dict = editing_script.to_json()
        elif isinstance(editing_script, dict):
            script_dict = editing_script
        else:
            raise TypeError(f"editing_script 必须是 EditingScript 对象或 dict,实际类型: {type(editing_script)}")

        # 确保 source_video 是绝对路径(shots + parallel_clips)
        for shot in script_dict.get("shots", []):
            sv = shot.get("source_video", "")
            if sv and not os.path.isabs(sv):
                shot["source_video"] = str(Path(sv).resolve())
        for pc in script_dict.get("parallel_clips", []):
            sv = pc.get("source_video", "")
            if sv and not os.path.isabs(sv):
                pc["source_video"] = str(Path(sv).resolve())
        for ov in script_dict.get("overlays", []):
            mov = ov.get("props", {}).get("mov_path", "")
            if mov and not os.path.isabs(mov):
                ov.setdefault("props", {})["mov_path"] = str(Path(mov).resolve())

        # 输出路径
        if not output_dir:
            output_dir = tempfile.gettempdir()

        # ── 效果路由:逐效果 HF/BMF 分发 ──
        hf_width = script_dict.get("width", 1920)
        hf_height = script_dict.get("height", 1080)
        hf_fps = script_dict.get("fps", 30)

        if status.get("hf_available"):
            try:
                script_dict = self._route_effects_to_hf(
                    script_dict, output_dir, hf_width, hf_height, hf_fps,
                )
            except Exception as e:
                print(f"[Bridge] 效果路由失败,全部降级 BMF: {e}")
                script_dict = self._degrade_hf_features(script_dict)
        else:
            script_dict = self._degrade_hf_features(script_dict)
        output_dir = str(Path(output_dir).resolve())
        if not output_name:
            output_name = script_dict.get("title", "output")
        # 清理文件名
        output_name = "".join(c for c in output_name if c.isalnum() or c in " _-()()")
        output_path_win = str(Path(output_dir) / f"{output_name}.mp4")

        # 写入临时 JSON(放在 Windows 可写,WSL 可读的位置)
        temp_dir_win = str(Path(tempfile.gettempdir()) / "bmf_render")
        os.makedirs(temp_dir_win, exist_ok=True)
        json_path_win = str(Path(temp_dir_win) / f"script_{int(time.time())}.json")

        # 输出路径写进 JSON,避免命令行传递中文路径乱码
        script_dict["_output_path"] = _win_to_wsl(output_path_win)

        with open(json_path_win, "w", encoding="utf-8") as f:
            json.dump(script_dict, f, ensure_ascii=False, indent=2)

        # 转换路径为 WSL 格式(脚本 JSON 用 ASCII temp 路径,无中文问题)
        json_path_wsl = _win_to_wsl(json_path_win)
        worker_wsl = self.worker_path_wsl

        # 构建命令(不再传 output_path,worker 从 JSON 里读 _output_path)
        cmd = [
            "wsl", "-d", self.wsl_distro, "--",
            "python3", worker_wsl,
            json_path_wsl,
        ]

        print(f"[Bridge] 启动 WSL2 BMF 渲染:")
        print(f"  脚本: {json_path_win}")
        print(f"  输出: {output_path_win}")
        print(f"  镜头: {len(script_dict.get('shots', []))} 个")

        try:
            t_start = time.time()
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                # wsl.exe 自己处理路径,不需要设置 Windows cwd
            )

            elapsed = time.time() - t_start

            # 输出 stderr(BMF worker 的日志)
            if result.stderr:
                for line in result.stderr.strip().split("\n"):
                    if "[BMF Worker]" in line:
                        print(f"  {line.strip()}")

            if result.returncode != 0:
                error_msg = result.stderr or result.stdout or "未知错误"
                raise RuntimeError(
                    f"BMF 渲染失败 (exit code {result.returncode}, {elapsed:.0f}s):\n{error_msg[-500:]}"
                )

            if not os.path.exists(output_path_win):
                raise RuntimeError(f"渲染完成但输出文件不存在: {output_path_win}")

            output_size = os.path.getsize(output_path_win) / (1024 * 1024)
            print(f"[Bridge] 渲染成功: {elapsed:.0f}s, {output_size:.1f}MB")
            return output_path_win

        except subprocess.TimeoutExpired:
            raise RuntimeError(f"BMF 渲染超时({timeout}s)")

        finally:
            # 清理临时 JSON
            try:
                os.remove(json_path_win)
            except OSError:
                pass


# ═══════════════════════════════════════════════════
#  便捷函数
# ═══════════════════════════════════════════════════

_default_bridge: Optional[WslBmfBridge] = None


def get_bridge() -> WslBmfBridge:
    """获取默认桥接实例(单例)"""
    global _default_bridge
    if _default_bridge is None:
        _default_bridge = WslBmfBridge()
    return _default_bridge


def render(editing_script, output_dir: str = "", output_name: str = "", timeout: int = 1800) -> str:
    """便捷函数:渲染 EditingScript -> MP4"""
    return get_bridge().render(
        editing_script,
        output_dir=output_dir,
        output_name=output_name,
        timeout=timeout,
    )
