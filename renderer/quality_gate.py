"""
施工监理 — Quality Gate
═══════════════════════
夹在 director(内容设计层)和 bridge(渲染层)之间.

拿原始视频(不是压缩视频)逐镜头做三道检查:
  1. 可行性 — 参数在物理上能否执行
  2. 冲突   — 多个效果是否互相矛盾
  3. 品质   — 效果参数是否合理(对照原片直方图)

输出:修正后的 EditingScript + 质检报告.

用法:
  from renderer.quality_gate import QualityGate

  gate = QualityGate()
  fixed_script, report = gate.inspect(editing_script)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys as _sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Windows gbk 兼容
_sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_sys.stderr.reconfigure(encoding='utf-8', errors='replace')


# ═══════════════════════════════════════════════════
#  数据结构
# ═══════════════════════════════════════════════════

@dataclass
class Issue:
    """一条质检问题"""
    level: str          # "blocker" | "error" | "warning" | "info"
    category: str       # "feasibility" | "conflict" | "quality"
    target: str         # "shot:shot_003" | "transition:0->1" | "parallel_clip:bg_001"
    message: str
    auto_fixed: bool = False
    fix_detail: str = ""


@dataclass
class QualityReport:
    """质检报告"""
    passed: bool = True
    total_checks: int = 0
    issues: list[Issue] = field(default_factory=list)
    auto_fixes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def blocker_count(self) -> int:
        return sum(1 for i in self.issues if i.level == "blocker")

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.level == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.level == "warning")

    def summary(self) -> str:
        lines = [f"质检{'通过' if self.passed else '未通过'}:"
                 f"{self.total_checks}项检查,"
                 f"{self.blocker_count}阻断/{self.error_count}错误/{self.warning_count}警告"]
        if self.auto_fixes:
            lines.append(f"\n自动修正 {len(self.auto_fixes)} 处:")
            for fix in self.auto_fixes:
                lines.append(f"  [FIX] {fix}")
        if self.warnings:
            lines.append(f"\n警告 {len(self.warnings)} 处:")
            for w in self.warnings:
                lines.append(f"  [WARN] {w}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════
#  视频元信息缓存(一次 ffprobe,多处用)
# ═══════════════════════════════════════════════════

class VideoMetaCache:
    """缓存原始视频的 ffprobe 信息"""

    def __init__(self):
        self._cache: dict[str, dict] = {}

    def _probe(self, path: str) -> dict:
        """ffprobe 原片,返回 {duration, width, height, fps}"""
        if path in self._cache:
            return self._cache[path]

        if not os.path.exists(path):
            self._cache[path] = {"exists": False}
            return self._cache[path]

        try:
            cmd = [
                "ffprobe", "-v", "quiet",
                "-select_streams", "v:0",
                "-show_entries",
                "stream=duration,width,height,r_frame_rate,codec_name",
                "-of", "json",
                path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                                    encoding="utf-8", errors="replace")
            if result.returncode != 0:
                self._cache[path] = {"exists": True, "error": result.stderr}
                return self._cache[path]

            data = json.loads(result.stdout)
            stream = (data.get("streams") or [{}])[0]

            # 解析帧率("30000/1001" -> 29.97)
            fps_str = stream.get("r_frame_rate", "30/1")
            num, den = fps_str.split("/")
            fps = float(num) / float(den) if int(den) != 0 else 30.0

            info = {
                "exists": True,
                "duration": float(stream.get("duration", 0)),
                "width": stream.get("width", 0),
                "height": stream.get("height", 0),
                "fps": fps,
                "codec": stream.get("codec_name", "unknown"),
            }
            self._cache[path] = info
            return info
        except Exception as e:
            self._cache[path] = {"exists": True, "error": str(e)}
            return self._cache[path]

    def get_duration(self, path: str) -> float:
        return self._probe(path).get("duration", 0)

    def get_resolution(self, path: str) -> tuple[int, int]:
        info = self._probe(path)
        return (info.get("width", 0), info.get("height", 0))

    def get(self, path: str) -> dict:
        return self._probe(path)


# ═══════════════════════════════════════════════════
#  监理引擎
# ═══════════════════════════════════════════════════

class QualityGate:
    """
    施工监理.

    在 EditingScript 传给 bridge.py 之前,对照原始视频做三道检查:
      1. 可行性(物理上能否执行)
      2. 冲突(效果是否互斥)
      3. 品质(参数是否合理)
    """

    # ── 配置 ──
    MAX_EFFECT_LAYERS = 3              # 单镜头最多特效层数(blur+glow+grade=3)
    MAX_TRANSITION_RATIO = 0.35        # 转场最长占比(转场时长/镜头时长)
    MIN_SHOT_DURATION = 0.15           # 最短镜头秒数
    DISSOLVE_MAX = 2.0                 # dissolve 最长秒数

    # 原生 BMF xfade 支持的转场类型
    NATIVE_XFADE = {
        "cut", "fade", "dissolve", "fadeblack", "fadewhite",
        "slide_left", "slide_right", "slide_up", "slide_down",
        "wipe_left", "wipe_right", "wipe_up", "wipe_down",
        "pixelize", "zoomin", "circleopen", "circleclose",
        "rectcrop", "radial",
    }

    # 互斥特效对(同时出现必有一个是废的)
    CONFLICT_EFFECT_PAIRS = [
        ({"blur", "gaussian_blur"}, {"sharpen"}),
        ({"blur"}, {"glow", "noise"}),
    ]

    # 特效优先级(数字越小越优先保留,冲突时砍优先级低的)
    EFFECT_PRIORITY = {
        "grade": 1,       # 调色最重要
        "chroma_key": 1,
        "compositing": 1,
        "glow": 2,
        "blur": 3,
        "gaussian_blur": 3,
        "sharpen": 4,
        "noise": 5,
        "hflip": 6,
        "vflip": 6,
    }

    # ── PiP 位置与字幕冲突映射 ──
    PIP_QUADRANTS = {
        "pip_tl": "top_left",
        "pip_tr": "top_right",
        "pip_bl": "bottom_left",
        "pip_br": "bottom_right",
    }

    def __init__(self, auto_fix: bool = True):
        self.auto_fix = auto_fix
        self.meta = VideoMetaCache()

    # ═══════════════════════════════════════════════
    #  入口
    # ═══════════════════════════════════════════════

    def inspect(self, editing_script) -> tuple:
        """
        质检入口.

        Args:
            editing_script: EditingScript 对象(有 to_json() 和 from_json())

        Returns:
            (fixed_editing_script, QualityReport)
        """
        if hasattr(editing_script, "to_json"):
            script = editing_script.to_json()
        elif isinstance(editing_script, dict):
            script = editing_script
        else:
            raise TypeError(f"editing_script 必须是 EditingScript 或 dict,实际: {type(editing_script)}")

        report = QualityReport()
        shots = script.get("shots", [])
        parallel_clips = script.get("parallel_clips", [])
        overlays = script.get("overlays", [])
        adjustment_layers = script.get("adjustment_layers", [])
        transitions = script.get("transitions", [])

        # ── 计算时间线(累积时间) ──
        shot_timeline = self._build_shot_timeline(shots)

        # ── 第一道:可行性 ──
        self._check_source_existence(shots, parallel_clips, report)
        self._check_time_ranges(shots, report)
        self._check_shot_duration(shots, report)
        self._check_transition_compat(transitions, report)
        self._check_overlay_duration(overlays, shot_timeline, report)

        # ── 第二道:冲突 ──
        self._check_effect_conflicts(shots, parallel_clips, adjustment_layers, report)
        self._check_pip_subtitle_conflict(shots, parallel_clips, report)
        self._check_framed_tight_violation(shots, parallel_clips, report)

        # ── 第三道:品质 ──
        self._check_transition_ratio(shots, transitions, report)
        self._check_audio_logic(shots, report)
        self._check_freeze_ratio(shots, report)

        # ── 执行自动修正 ──
        if self.auto_fix and report.issues:
            script = self._apply_fixes(script, report)

        report.total_checks = len(report.issues)
        report.passed = report.blocker_count == 0

        # ── 返回 ──
        if hasattr(editing_script, "from_json"):
            fixed_script = editing_script.__class__.from_json(script)
        else:
            fixed_script = script

        return fixed_script, report

    # ═══════════════════════════════════════════════
    #  工具方法
    # ═══════════════════════════════════════════════

    def _build_shot_timeline(self, shots: list) -> list[dict]:
        """构建累积时间线,返回 [{shot, start_time, end_time}, ...]"""
        timeline = []
        t = 0.0
        for shot in shots:
            tr = shot.get("time_range", [0, 0])
            raw_dur = tr[1] - tr[0]
            speed = shot.get("speed", 1.0) or 1.0
            dur = raw_dur / speed if speed != 1.0 else raw_dur
            freeze_dur = shot.get("freeze_duration", 0) or 0
            dur += freeze_dur
            timeline.append({"shot": shot, "start_time": t, "end_time": t + dur, "duration": dur})
            t += dur
        return timeline

    def _shot_by_id(self, shots: list) -> dict:
        return {s.get("shot_id", ""): s for s in shots}

    def _all_effects_for_shot(self, shot: dict, parallel_clips: list,
                               timeline: list, adjustment_layers: list) -> list:
        """汇总单镜头所有来源的特效"""
        effects = []

        # 主轨特效
        shot_eff = shot.get("effects", []) or []
        for e in shot_eff:
            e = e if isinstance(e, dict) else {"name": str(e)}
            effects.append({"source": "shot", "name": e.get("name", ""), "data": e})

        # 重叠的副轨特效
        shot_t = next((t for t in timeline if t["shot"].get("shot_id") == shot.get("shot_id")), None)
        if shot_t:
            for pc in (parallel_clips or []):
                pc_start = pc.get("start_time", 0)
                pc_dur = (pc.get("time_range", [0, 0])[1] - pc.get("time_range", [0, 0])[0])
                pc_speed = pc.get("speed", 1.0) or 1.0
                pc_end = pc_start + (pc_dur / pc_speed if pc_speed != 1.0 else pc_dur)
                if pc_start < shot_t["end_time"] and pc_end > shot_t["start_time"]:
                    for e in (pc.get("effects", []) or []):
                        e = e if isinstance(e, dict) else {"name": str(e)}
                        effects.append({"source": "parallel_clip", "name": e.get("name", ""), "data": e})

        # 重叠的调整图层特效
        for al in (adjustment_layers or []):
            al_start = al.get("start_time", 0)
            al_end = al_start + al.get("duration", 0)
            if al_start < shot_t["end_time"] and al_end > shot_t["start_time"]:
                for e in (al.get("effects", []) or []):
                    e = e if isinstance(e, dict) else {"name": str(e)}
                    effects.append({"source": "adj_layer", "name": e.get("name", ""), "data": e})

        return effects

    # ═══════════════════════════════════════════════
    #  第一道:可行性
    # ═══════════════════════════════════════════════

    def _check_source_existence(self, shots: list, parallel_clips: list, report: QualityReport):
        """检查源文件是否存在"""
        seen = set()
        for s in shots:
            sv = s.get("source_video", "")
            if sv in seen:
                continue
            seen.add(sv)
            info = self.meta.get(sv)
            if not info.get("exists"):
                report.issues.append(Issue(
                    "blocker", "feasibility",
                    f"source:{Path(sv).name}",
                    f"源视频不存在: {sv}",
                ))

        for pc in (parallel_clips or []):
            sv = pc.get("source_video", "")
            if sv in seen:
                continue
            seen.add(sv)
            info = self.meta.get(sv)
            if not info.get("exists"):
                report.issues.append(Issue(
                    "error", "feasibility",
                    f"parallel_clip:{pc.get('clip_id', '?')}",
                    f"副轨源视频不存在: {sv}",
                ))

    def _check_time_ranges(self, shots: list, report: QualityReport):
        """检查 time_range 不超出原片时长"""
        for s in shots:
            sv = s.get("source_video", "")
            tr = s.get("time_range", [0, 0])
            src_dur = self.meta.get_duration(sv)
            if src_dur <= 0:
                continue
            if tr[1] > src_dur + 0.05:  # 0.05s 容差
                report.issues.append(Issue(
                    "error", "feasibility",
                    f"shot:{s.get('shot_id', '?')}",
                    f"time_range[1]={tr[1]:.1f}s 超出原片时长 {src_dur:.1f}s",
                    auto_fixed=True,
                    fix_detail=f"time_range[1] {tr[1]:.1f} -> {src_dur:.1f}",
                ))

    def _check_shot_duration(self, shots: list, report: QualityReport):
        """检查镜头时长不低于下限"""
        for s in shots:
            tr = s.get("time_range", [0, 0])
            raw_dur = tr[1] - tr[0]
            speed = s.get("speed", 1.0) or 1.0
            dur = raw_dur / speed if speed != 1.0 else raw_dur
            if dur < self.MIN_SHOT_DURATION:
                report.issues.append(Issue(
                    "error", "feasibility",
                    f"shot:{s.get('shot_id', '?')}",
                    f"镜头时长 {dur:.2f}s < 最小 {self.MIN_SHOT_DURATION}s",
                ))

    def _check_transition_compat(self, transitions: list, report: QualityReport):
        """检查转场类型是否支持"""
        for t in (transitions or []):
            t_type = t.get("type", "cut")
            if t_type not in self.NATIVE_XFADE:
                report.issues.append(Issue(
                    "warning", "feasibility",
                    f"transition:{t_type}",
                    f"转场类型 '{t_type}' 非原生 BMF xfade,依赖 HF MOV 或降级",
                    auto_fixed=True,
                    fix_detail=f"转场 {t_type} -> dissolve(BMF 原生兜底)",
                ))

    def _check_overlay_duration(self, overlays: list, timeline: list, report: QualityReport):
        """检查叠层不超出成片总时长"""
        if not timeline:
            return
        total_dur = timeline[-1]["end_time"]
        for ov in (overlays or []):
            ov_end = ov.get("start_time", 0) + ov.get("duration", 0)
            if ov_end > total_dur + 0.1:
                report.issues.append(Issue(
                    "error", "feasibility",
                    f"overlay:{ov.get('element_id', '?')}",
                    f"叠层结束 {ov_end:.1f}s 超出成片 {total_dur:.1f}s",
                    auto_fixed=True,
                    fix_detail=f"叠层 duration {ov.get('duration')} -> {total_dur - ov.get('start_time', 0):.1f}",
                ))

    # ═══════════════════════════════════════════════
    #  第二道:冲突
    # ═══════════════════════════════════════════════

    def _check_effect_conflicts(self, shots: list, parallel_clips: list,
                                adjustment_layers: list, report: QualityReport):
        """检查特效互斥 + 层数超标"""
        timeline = self._build_shot_timeline(shots)
        for s in shots:
            shot_id = s.get("shot_id", "?")
            all_eff = self._all_effects_for_shot(s, parallel_clips, timeline, adjustment_layers)
            eff_names = set(e["name"] for e in all_eff)

            # 互斥检查
            for set_a, set_b in self.CONFLICT_EFFECT_PAIRS:
                if eff_names & set_a and eff_names & set_b:
                    conflict_a = list(eff_names & set_a)[0]
                    conflict_b = list(eff_names & set_b)[0]
                    # 保留优先级高的
                    pri_a = self.EFFECT_PRIORITY.get(conflict_a, 99)
                    pri_b = self.EFFECT_PRIORITY.get(conflict_b, 99)
                    keep = conflict_a if pri_a <= pri_b else conflict_b
                    drop = conflict_b if keep == conflict_a else conflict_a
                    report.issues.append(Issue(
                        "warning", "conflict",
                        f"shot:{shot_id}",
                        f"特效互斥: {conflict_a} + {conflict_b},保留 {keep},移除 {drop}",
                        auto_fixed=True,
                        fix_detail=f"移除 {drop}",
                    ))

            # 层数检查
            if len(all_eff) > self.MAX_EFFECT_LAYERS:
                # 按优先级排序,砍掉最低优先级的
                sorted_eff = sorted(all_eff, key=lambda e: self.EFFECT_PRIORITY.get(e["name"], 99))
                excess = sorted_eff[self.MAX_EFFECT_LAYERS:]
                excess_names = [e["name"] for e in excess]
                report.issues.append(Issue(
                    "warning", "conflict",
                    f"shot:{shot_id}",
                    f"特效层数 {len(all_eff)} > 上限 {self.MAX_EFFECT_LAYERS},移除: {', '.join(excess_names)}",
                    auto_fixed=True,
                    fix_detail=f"保留前{self.MAX_EFFECT_LAYERS}层,移除 {', '.join(excess_names)}",
                ))

    def _check_pip_subtitle_conflict(self, shots: list, parallel_clips: list,
                                      report: QualityReport):
        """检查 PiP 位置和字幕/标注位置冲突"""
        if not parallel_clips:
            return

        timeline = self._build_shot_timeline(shots)
        for s in shots:
            shot_id = s.get("shot_id", "?")
            annotation_y = s.get("annotation_y")
            overlay_text = s.get("overlay_text")
            if not (annotation_y or overlay_text):
                continue

            shot_t = next((t for t in timeline if t["shot"].get("shot_id") == shot_id), None)
            if not shot_t:
                continue

            # 字幕 Y 位置估算(0-1比例 -> 画面区域)
            sub_y = annotation_y if annotation_y else 0.85  # 默认底部
            sub_quadrant = "bottom" if sub_y > 0.6 else ("top" if sub_y < 0.4 else "center")

            for pc in parallel_clips:
                pc_start = pc.get("start_time", 0)
                pc_dur = (pc.get("time_range", [0, 0])[1] - pc.get("time_range", [0, 0])[0])
                pc_speed = pc.get("speed", 1.0) or 1.0
                pc_end = pc_start + (pc_dur / pc_speed if pc_speed != 1.0 else pc_dur)

                if pc_start < shot_t["end_time"] and pc_end > shot_t["start_time"]:
                    pc_pos = pc.get("position", "")
                    pc_quadrant = self.PIP_QUADRANTS.get(pc_pos, "")

                    # 同一区域 -> 冲突
                    if (pc_quadrant == "bottom_right" and sub_quadrant == "bottom") or \
                       (pc_quadrant == "bottom_left" and sub_quadrant == "bottom") or \
                       (pc_quadrant == "top_right" and sub_quadrant == "top") or \
                       (pc_quadrant == "top_left" and sub_quadrant == "top"):
                        report.issues.append(Issue(
                            "warning", "conflict",
                            f"shot:{shot_id}",
                            f"字幕/标注在{sub_quadrant},PiP在{pc_quadrant} — 可能重叠",
                            auto_fixed=True,
                            fix_detail="字幕 Y 偏移到对面区域",
                        ))

    def _check_framed_tight_violation(self, shots: list, parallel_clips: list,
                                       report: QualityReport):
        """检查 framed_tight 镜头是否被加了副轨"""
        if not parallel_clips:
            return

        timeline = self._build_shot_timeline(shots)
        for shot_t in timeline:
            shot = shot_t["shot"]
            if shot.get("compositing_hint") != "framed_tight":
                continue
            shot_id = shot.get("shot_id", "?")
            for pc in parallel_clips:
                pc_start = pc.get("start_time", 0)
                pc_dur = (pc.get("time_range", [0, 0])[1] - pc.get("time_range", [0, 0])[0])
                pc_speed = pc.get("speed", 1.0) or 1.0
                pc_end = pc_start + (pc_dur / pc_speed if pc_speed != 1.0 else pc_dur)
                if pc_start < shot_t["end_time"] and pc_end > shot_t["start_time"]:
                    report.issues.append(Issue(
                        "warning", "conflict",
                        f"shot:{shot_id}",
                        f"framed_tight 镜头被加了副轨 {pc.get('clip_id', '?')}",
                        auto_fixed=True,
                        fix_detail=f"移除副轨 {pc.get('clip_id', '?')} 对该镜头的叠加",
                    ))

    # ═══════════════════════════════════════════════
    #  第三道:品质
    # ═══════════════════════════════════════════════

    def _check_transition_ratio(self, shots: list, transitions: list, report: QualityReport):
        """转场时长不超过镜头时长的比例上限"""
        timeline = self._build_shot_timeline(shots)
        for shot_t in timeline:
            shot = shot_t["shot"]
            shot_id = shot.get("shot_id", "?")
            dur = shot_t["duration"]
            trans_dur = shot.get("transition_duration", 0) or 0

            if trans_dur > 0 and dur > 0:
                ratio = trans_dur / dur
                if ratio > self.MAX_TRANSITION_RATIO:
                    capped = dur * self.MAX_TRANSITION_RATIO
                    report.issues.append(Issue(
                        "warning", "quality",
                        f"shot:{shot_id}",
                        f"转场 {trans_dur:.1f}s 占镜头 {dur:.1f}s 的 {ratio:.0%},超过上限 {self.MAX_TRANSITION_RATIO:.0%}",
                        auto_fixed=True,
                        fix_detail=f"transition_duration {trans_dur:.1f} -> {capped:.1f}",
                    ))
            # dissolve 绝对上限
            trans_in = shot.get("transition_in", "")
            trans_out = shot.get("transition_out", "")
            if trans_dur > self.DISSOLVE_MAX and trans_in in ("dissolve", "fade") or \
               trans_dur > self.DISSOLVE_MAX and trans_out in ("dissolve", "fade"):
                report.issues.append(Issue(
                    "info", "quality",
                    f"shot:{shot_id}",
                    f"dissolve/fade {trans_dur:.1f}s > 上限 {self.DISSOLVE_MAX}s,过长转场影响节奏",
                    auto_fixed=True,
                    fix_detail=f"transition_duration {trans_dur:.1f} -> {self.DISSOLVE_MAX}",
                ))

    def _check_audio_logic(self, shots: list, report: QualityReport):
        """检查音频指令逻辑"""
        for s in shots:
            shot_id = s.get("shot_id", "?")
            audio_action = s.get("audio_action", "")
            tr = s.get("time_range", [0, 0])
            dur = (tr[1] - tr[0]) / (s.get("speed", 1.0) or 1.0)

            # mute 镜头设了 crossfade
            if audio_action in ("mute", "bgm_only") and s.get("transition_in") in ("crossfade", "fade"):
                report.issues.append(Issue(
                    "warning", "quality",
                    f"shot:{shot_id}",
                    f"静音镜头设了 {s.get('transition_in')} 音频转场 -> 无意义",
                    auto_fixed=True,
                    fix_detail="音频转场降级为 cut",
                ))

            # 极短镜头设 crossfade
            if dur < 0.3 and audio_action == "crossfade":
                report.issues.append(Issue(
                    "info", "quality",
                    f"shot:{shot_id}",
                    f"0.3s 内镜头 crossfade 无法听出效果,建议 cut",
                    auto_fixed=True,
                    fix_detail="crossfade -> cut",
                ))

    def _check_freeze_ratio(self, shots: list, report: QualityReport):
        """定格时长不能超过镜头有效时长太多"""
        for s in shots:
            shot_id = s.get("shot_id", "?")
            freeze_dur = s.get("freeze_duration", 0) or 0
            if freeze_dur <= 0:
                continue
            tr = s.get("time_range", [0, 0])
            raw_dur = tr[1] - tr[0]
            if freeze_dur > raw_dur * 3:
                report.issues.append(Issue(
                    "warning", "quality",
                    f"shot:{shot_id}",
                    f"定格 {freeze_dur:.1f}s 远超镜头时长 {raw_dur:.1f}s({freeze_dur/raw_dur:.0f}x)",
                    auto_fixed=True,
                    fix_detail=f"freeze_duration {freeze_dur:.1f} -> {raw_dur * 2:.1f}(2x上限)",
                ))

    # ═══════════════════════════════════════════════
    #  自动修正
    # ═══════════════════════════════════════════════

    def _apply_fixes(self, script: dict, report: QualityReport) -> dict:
        """将可自动修正的 issue 应用到脚本上"""
        timeline = self._build_shot_timeline(script.get("shots", []))
        shots = script.get("shots", [])
        shot_map = self._shot_by_id(shots)

        for issue in report.issues:
            if not issue.auto_fixed:
                continue

            # ── 转场降级 ──
            if issue.category == "feasibility" and "-> dissolve" in issue.fix_detail:
                for t in (script.get("transitions") or []):
                    t["type"] = "dissolve"
                report.auto_fixes.append(issue.fix_detail)
                continue

            # ── time_range 超出 ──
            if "time_range" in issue.fix_detail and "->" in issue.fix_detail:
                target = issue.target
                if target.startswith("shot:"):
                    shot_id = target.split(":", 1)[1]
                    shot = shot_map.get(shot_id)
                    if shot:
                        src_dur = self.meta.get_duration(shot.get("source_video", ""))
                        if src_dur > 0:
                            old_tr = shot["time_range"]
                            shot["time_range"] = [old_tr[0], min(old_tr[1], src_dur)]
                            report.auto_fixes.append(issue.fix_detail)
                continue

            # ── 叠层 duration 超出 ──
            if "叠层 duration" in issue.fix_detail:
                target = issue.target
                if target.startswith("overlay:"):
                    elem_id = target.split(":", 1)[1]
                    for ov in (script.get("overlays") or []):
                        if ov.get("element_id") == elem_id:
                            ov["duration"] = max(0.5, timeline[-1]["end_time"] - ov.get("start_time", 0))
                            report.auto_fixes.append(issue.fix_detail)
                continue

            # ── 转场占比超限 ──
            if "transition_duration" in issue.fix_detail and "->" in issue.fix_detail:
                target = issue.target
                if target.startswith("shot:"):
                    shot_id = target.split(":", 1)[1]
                    shot = shot_map.get(shot_id)
                    if shot:
                        shot_t = next((t for t in timeline if t["shot"].get("shot_id") == shot_id), None)
                        if shot_t:
                            capped = min(
                                shot_t["duration"] * self.MAX_TRANSITION_RATIO,
                                self.DISSOLVE_MAX,
                            )
                            shot["transition_duration"] = round(capped, 2)
                            report.auto_fixes.append(issue.fix_detail)
                continue

            # ── 定格超限 ──
            if "freeze_duration" in issue.fix_detail and "->" in issue.fix_detail:
                target = issue.target
                if target.startswith("shot:"):
                    shot_id = target.split(":", 1)[1]
                    shot = shot_map.get(shot_id)
                    if shot:
                        tr = shot.get("time_range", [0, 0])
                        raw_dur = tr[1] - tr[0]
                        shot["freeze_duration"] = round(raw_dur * 2, 1)
                        report.auto_fixes.append(issue.fix_detail)
                continue

            # ── 静音镜头音频转场降级 ──
            if "音频转场降级为 cut" in issue.fix_detail or "crossfade -> cut" in issue.fix_detail:
                target = issue.target
                if target.startswith("shot:"):
                    shot_id = target.split(":", 1)[1]
                    shot = shot_map.get(shot_id)
                    if shot:
                        shot["transition_in"] = "cut"
                        shot["transition_out"] = "cut"
                        report.auto_fixes.append(issue.fix_detail)
                continue

            # ── PiP+字幕冲突 -> 偏移字幕 ──
            if "字幕 Y 偏移到对面区域" in issue.fix_detail:
                target = issue.target
                if target.startswith("shot:"):
                    shot_id = target.split(":", 1)[1]
                    shot = shot_map.get(shot_id)
                    if shot and "annotation_y" in shot:
                        current_y = shot["annotation_y"]
                        # 底部->顶部,顶部->底部
                        shot["annotation_y"] = 0.15 if current_y > 0.6 else 0.85
                        report.auto_fixes.append(issue.fix_detail)
                continue

            # ── framed_tight -> 调整副轨 enable 时间 ──
            if "framed_tight" in issue.fix_detail:
                # 将 overlapping parallel_clip 的 start_time 推到 shot 结束后
                target = issue.target
                if target.startswith("shot:"):
                    shot_id = target.split(":", 1)[1]
                    shot_t = next((t for t in timeline if t["shot"].get("shot_id") == shot_id), None)
                    if shot_t:
                        for pc in (script.get("parallel_clips") or []):
                            pc_start = pc.get("start_time", 0)
                            if pc_start < shot_t["end_time"] and pc_start >= shot_t["start_time"]:
                                pc["start_time"] = shot_t["end_time"] + 0.01
                                report.auto_fixes.append(issue.fix_detail)
                continue

        return script


# ═══════════════════════════════════════════════════
#  便捷函数
# ═══════════════════════════════════════════════════

def inspect(editing_script, auto_fix: bool = True) -> tuple:
    """便捷函数:质检 EditingScript"""
    gate = QualityGate(auto_fix=auto_fix)
    return gate.inspect(editing_script)


def quick_report(editing_script) -> str:
    """只出报告,不改脚本"""
    _, report = inspect(editing_script, auto_fix=False)
    return report.summary()
