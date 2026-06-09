"""
管线状态管理 — 多阶段编排器的文件系统状态
==============================================

PipelineState 现在只跟踪流程控制,不存实际剪辑数据.
所有剪辑数据(片段,编排,滤镜,BGM,字幕)都在 Draft 里.

用法:
    state = PipelineState(work_dir)
    state.current_stage = "coarse_filter"
    state.draft_id = "20260530_123456"
    state.save()

线程安全:所有修改操作都持 _lock,save() 也持同一把锁,
不会出现读-改-写竞态.
"""
import json, os, time, threading
from datetime import datetime
from typing import Optional

# 模块级锁 — 所有修改操作共用
_pipeline_lock = threading.Lock()


class PipelineState:
    """管线的文件系统状态.原子写入,不会损坏."""

    def __init__(self, work_dir: str):
        self.work_dir = os.path.abspath(work_dir)
        self.state_path = os.path.join(work_dir, "pipeline_state.json")
        self._data = self._load_or_init()
        # 确保子目录存在
        for sub in ("segments", "previews", "segments_compressed", "output"):
            d = os.path.join(self.work_dir, sub)
            os.makedirs(d, exist_ok=True)

    def _load_or_init(self) -> dict:
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return self._empty()

    def _empty(self) -> dict:
        return {
            "version": 1,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "current_stage": "idle",
            "user_task": "",
            "director_brief": "",
            "video_paths": [],
            "segments": [],
            "arrangement": [],
            "previews": [],
            "editing": {
                "step": 0,
                "last_preview": "",
                "completed_ops": [],
            },
            "stage_results": {},
            "draft_id": "",
        }

    # ─── 原子写入 ─────────────────────────────────────────

    def save(self):
        """写入状态(原子操作:先写临时文件再 rename)"""
        with _pipeline_lock:
            self._data["updated_at"] = datetime.now().isoformat()
            tmp = f"{self.state_path}.{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.state_path)

    # ─── 属性访问(全部用 .get 安全访问)─────────────────

    @property
    def current_stage(self) -> str:
        return self._data.get("current_stage", "idle")

    @current_stage.setter
    def current_stage(self, stage: str):
        with _pipeline_lock:
            self._data["current_stage"] = stage

    @property
    def user_task(self) -> str:
        return self._data.get("user_task", "")

    @user_task.setter
    def user_task(self, task: str):
        with _pipeline_lock:
            self._data["user_task"] = task

    @property
    def director_brief(self) -> str:
        return self._data.get("director_brief", "")

    @director_brief.setter
    def director_brief(self, brief: str):
        with _pipeline_lock:
            self._data["director_brief"] = brief

    @property
    def video_paths(self) -> list:
        return self._data.get("video_paths", [])

    @video_paths.setter
    def video_paths(self, paths: list):
        with _pipeline_lock:
            self._data["video_paths"] = list(paths)

    @property
    def segments(self) -> list:
        return self._data.get("segments", [])

    @property
    def arrangement(self) -> list:
        return self._data.get("arrangement", [])

    @property
    def editing_step(self) -> int:
        return self._data.get("editing", {}).get("step", 0)

    @property
    def last_preview(self) -> str:
        return self._data.get("editing", {}).get("last_preview", "")

    @property
    def completed_ops(self) -> list:
        return self._data.get("editing", {}).get("completed_ops", [])

    # ─── 片段管理(全部持锁)─────────────────────────────

    def add_segment(self, seg_id: str, source: str, start: float, end: float,
                    path: str, duration: float = 0, description: str = "") -> dict:
        """添加一个裁剪片段到管线状态.如果 ID 已存在则更新."""
        seg = {
            "id": seg_id,
            "source": os.path.abspath(source),
            "source_path": os.path.abspath(source),
            "start": round(start, 2),
            "end": round(end, 2),
            "path": path,
            "duration": round(duration or (end - start), 2),
            "description": description,
            "status": "keep",
            "created_at": datetime.now().isoformat(),
        }
        with _pipeline_lock:
            existing = self._data.get("segments", [])
            for i, s in enumerate(existing):
                if s.get("id") == seg_id:
                    existing[i] = seg
                    return seg
            existing.append(seg)
        return seg

    def discard_segment(self, seg_id: str) -> bool:
        """标记片段为弃用(不删除文件,只改状态)"""
        with _pipeline_lock:
            for s in self._data.get("segments", []):
                if s.get("id") == seg_id:
                    s["status"] = "discard"
                    return True
        return False

    def keep_segments(self) -> list:
        """返回所有保留(未弃用)的片段"""
        return [s for s in self._data.get("segments", []) if s.get("status") != "discard"]

    def get_segment(self, seg_id: str) -> Optional[dict]:
        for s in self._data.get("segments", []):
            if s.get("id") == seg_id:
                return s
        return None

    # ─── 编排管理 ─────────────────────────────────────────

    def set_arrangement(self, order: list[str]):
        with _pipeline_lock:
            self._data["arrangement"] = list(order)

    # ─── 编辑进度 ─────────────────────────────────────────

    def editing_advance(self, preview_path: str, operations: list[str],
                        description: str = ""):
        with _pipeline_lock:
            editing = self._data.setdefault("editing", {})
            step = editing.get("step", 0) + 1
            editing["step"] = step
            editing["last_preview"] = preview_path
            editing["completed_ops"] = list(operations)
            previews = self._data.setdefault("previews", [])
            previews.append({
                "step": step,
                "path": preview_path,
                "description": description,
                "created_at": datetime.now().isoformat(),
            })

    # ─── 阶段结果 ─────────────────────────────────────────

    def set_stage_result(self, stage: str, result: dict):
        with _pipeline_lock:
            sr = self._data.setdefault("stage_results", {})
            sr[stage] = {**result, "timestamp": datetime.now().isoformat()}

    def get_stage_result(self, stage: str) -> Optional[dict]:
        return self._data.get("stage_results", {}).get(stage)

    # ─── 阶段输出追踪（自动重新分析用）──────────────────────

    def set_stage_output(self, stage: str, output_path: str):
        """记录某个阶段产出的视频路径，供下一阶段自动触发重新分析."""
        with _pipeline_lock:
            outputs = self._data.setdefault("stage_outputs", {})
            outputs[stage] = {
                "path": os.path.abspath(output_path),
                "timestamp": datetime.now().isoformat(),
            }

    def get_stage_output(self, stage: str) -> Optional[str]:
        """返回某阶段产出视频路径，None 表示未产出."""
        entry = self._data.get("stage_outputs", {}).get(stage)
        return entry["path"] if entry else None

    @property
    def latest_stage_output(self) -> Optional[str]:
        """返回最新阶段的产出视频路径（按时间戳排序）."""
        outputs = self._data.get("stage_outputs", {})
        if not outputs:
            return None
        sorted_entries = sorted(
            outputs.items(),
            key=lambda kv: kv[1].get("timestamp", ""),
            reverse=True,
        )
        return sorted_entries[0][1].get("path")

    # ─── 草稿桥接 ─────────────────────────────────────────

    @property
    def draft_id(self) -> str:
        return self._data.get("draft_id", "")

    @draft_id.setter
    def draft_id(self, val: str):
        with _pipeline_lock:
            self._data["draft_id"] = val

    # ─── 摘要(给 AI 看的)────────────────────────────────

    def summary(self) -> str:
        """生成管线当前状态的人类可读摘要"""
        lines = [f"## 管线状态", f"当前阶段: {self.current_stage}"]

        segs = self.keep_segments()
        if segs:
            total_dur = sum(s.get("duration", 0) for s in segs)
            lines.append(f"\n已裁剪片段: {len(segs)} 个,总时长 {total_dur:.0f}s")
            for s in segs[:30]:
                desc = f" - {s.get('description', '')[:60]}" if s.get("description") else ""
                lines.append(f"  [{s.get('id', '?')}] {s.get('source', '?')} {s.get('start', 0):.0f}s->{s.get('end', 0):.0f}s ({s.get('duration', 0):.0f}s){desc}")
            if len(segs) > 30:
                lines.append(f"  ... 还有 {len(segs)-30} 个片段")

        arr = self.arrangement
        if arr:
            lines.append(f"\n编排顺序: {' -> '.join(arr[:20])}")
            if len(arr) > 20:
                lines.append(f"  ... 共 {len(arr)} 个位置")

        previews = self._data.get("previews", [])
        if previews:
            last = previews[-1]
            lines.append(f"\n最新预览: {last.get('path', '?')} (step {last.get('step', '?')})")
            lines.append(f"已完成操作: {', '.join(self.completed_ops)}")

        return "\n".join(lines)

    def segments_summary(self) -> str:
        """给编排阶段看的片段摘要"""
        segs = self.keep_segments()
        lines = ["## 可用片段"]
        for s in segs:
            desc = f" - {s.get('description', '')}" if s.get("description") else ""
            lines.append(f"  [{s.get('id', '?')}] 时长{s.get('duration', 0):.0f}s, 来源:{s.get('source', '?')} ({s.get('start', 0):.0f}s->{s.get('end', 0):.0f}s){desc}")
        lines.append(f"\n共 {len(segs)} 个片段,总时长 {sum(s.get('duration', 0) for s in segs):.0f}s")
        return "\n".join(lines)

    def editing_context(self) -> str:
        """编辑阶段的上下文摘要"""
        arr = self.arrangement
        ops = self.completed_ops
        segs = self.keep_segments()

        lines = ["## 当前编辑状态"]
        lines.append(f"片段总数: {len(segs)}")
        if arr:
            lines.append(f"编排顺序: {' -> '.join(arr)}")
        lines.append(f"已完成操作: {', '.join(ops) if ops else '(无)'}")
        previews = self._data.get("previews", [])
        if previews:
            lines.append(f"\n最新渲染预览: {previews[-1].get('path', '?')}")
        else:
            lines.append(f"\n⚠ 尚无渲染预览,需要先生成基线预览")

        vpaths = self.video_paths
        if vpaths:
            lines.append(f"\n源视频文件:")
            for vp in vpaths:
                lines.append(f"  - {vp}")

        lines.append(f"\n编排后的片段详情:")
        seg_map = {s.get("id"): s for s in segs if s.get("id")}
        ordered = [seg_map[sid] for sid in arr if sid in seg_map]
        for s in ordered[:30]:
            desc = f" - {s.get('description', '')[:80]}" if s.get("description") else ""
            fpath = s.get("path", s.get("source_path", ""))
            lines.append(f"  [{s.get('id', '?')}] {s.get('source', '?')} ({s.get('duration', 0):.0f}s) 文件: {fpath}{desc}")

        return "\n".join(lines)
