"""
草稿系统 — 持久化时间线
========================
每个操作(裁剪,抠像,调色,花字,BGM)都在当前草稿上追加,
写回草稿文件后既可预览(累积状态),也可在最后一步导出.

草稿 = 有版本的文件:
    drafts/
    ├── 20260526_001.json       ← 初始(素材分析完成)
    ├── 20260526_001_v2.json    ← 裁剪完成
    ├── 20260526_001_v3.json    ← 抠像完成
    └── 20260526_001_v4.json    ← 动画完成

用法:
    draft = Draft("20260526_001")        # 打开/创建草稿
    draft.load()                          # 读取
    draft.set_clip_filter(clip_id, "crop", {"x":100, "y":50, "w":800, "h":600})
    draft.set_bgm("path/to/bgm.mp3", volume=-15)
    draft.save("裁剪完成")                 # 保存为新版本
    draft.preview_timeline()              # 返回当前状态的 ffmpeg filter 描述
"""
import json, os, shutil, copy, time, threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from director.registry import tool

_PROJECT_DIR = Path(__file__).parent.parent
_DRAFT_DIR = _PROJECT_DIR / "drafts"

# 类级别保存锁,防止多线程 save() 版本竞态
_draft_save_lock = threading.Lock()


# ═══════════════════════════════════════════════════════
#  时间线 Schema
# ═══════════════════════════════════════════════════════

DRAFT_SCHEMA_VERSION = 1

def _empty_draft(name: str = "", video_type: str = "") -> dict:
    """创建空草稿"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return {
        "schema_version": DRAFT_SCHEMA_VERSION,
        "draft_id": ts,
        "name": name or f"草稿_{ts}",
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "video_type": video_type,
        "source_videos": [],
        "timeline": {
            "width": 1920,
            "height": 1080,
            "fps": 30,
            # 主轨道 — 多个片段(素材分析/排版后的 segments)
            "main_track": {
                "segments": [],
                "transitions": [],
            },
            # 叠层轨道(B-roll,PIP 视频)
            "overlay_track": [],
            # 图片/Logo/贴纸轨道
            "graphic_track": [],
            # 第二视频轨道(主轨道叠两个画面备用)
            "video_track_2": [],
            # 花字
            "flower_texts": [],
            # 字幕
            "subtitles": [],
        },
        "audio": {
            "bgm": None,           # {"source": "...", "volume": -15}
            "bgm_ducking": True,
            "vocal_track": None,   # 分离后的人声轨路径
            "voiceover": None,     # 配音轨
            "sfx": [],           # 音效列表 [{"source":"...", "start_time":.., "duration":.., "volume":..}]
        },
        "transcript": None,  # {"source_video": "...", "segments": [{"index":0, "start":0.5, "end":2.3, "text":"..."}]}
        "render_settings": {
            "output_format": "mp4",
            "quality": "high",
            "nvenc": None,   # None=自动检测
        },
        "version": 1,
        "version_label": "初始",
    }


# ═══════════════════════════════════════════════════════
#  每个片段的 filter 容器
# ═══════════════════════════════════════════════════════

def _empty_segment(source_path: str = "", start: float = 0, end: float = 0) -> dict:
    """创建空片段"""
    return {
        "id": 0,
        "source_path": source_path,
        "start": start,
        "end": end,
        "duration": round(end - start, 1),
        "speed": 1.0,
        "filters": {
            "crop": None,           # {"x":100, "y":50, "w":800, "h":600} 或 None
            "chromakey": None,      # {"color":"#00FF00", "similarity":0.15, "blend":0.1} 或 None
            "color_grading": None,  # {"curves_master":"...", "shadows_rgb":"..."} 或 None
            "color_preset": None,   # 预调色路径
            "denoise": None,        # {"type":"nlmeans", "strength":10}
            "stabilize": None,      # {"method":"vidstab", "shakiness":5}
            "animation": None,      # {"type":"motion", "keyframes":[...]}
        },
        "text": "",
        "status": "",
    }


# ═══════════════════════════════════════════════════════
#  Draft 类
# ═══════════════════════════════════════════════════════

class Draft:
    """
    草稿管理器.提供加载,保存,修改,预览能力.

    draft_id = "20260526_001"  -> 对应 drafts/20260526_001/
    每个版本是目录下的 v{版本号}.json 文件.
    """

    def __init__(self, draft_id: str = "", video_type: str = ""):
        self.draft_id = draft_id or datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        # 优先使用当前项目的 draft 目录（工作区固定路径）
        project_dir = os.environ.get("CLIPMIND_PIPELINE_DIR", "")
        if project_dir:
            self._dir = Path(project_dir) / "drafts" / self.draft_id
        else:
            self._dir = _DRAFT_DIR / self.draft_id
        self._data = None

    # ── 读取 ──

    def load(self) -> Optional[dict]:
        """加载最新版本的草稿.返回 None 表示草稿不存在."""
        if not self._dir.exists():
            return None
        versions = sorted(self._dir.glob("v*.json"), key=lambda p: int(p.stem[1:]))
        if not versions:
            return None
        with open(versions[-1], "r", encoding="utf-8") as f:
            self._data = json.load(f)
        return self._data

    def load_version(self, version: int) -> Optional[dict]:
        """加载指定版本的草稿"""
        vfile = self._dir / f"v{version}.json"
        if not vfile.exists():
            return None
        with open(vfile, "r", encoding="utf-8") as f:
            self._data = json.load(f)
        return self._data

    def list_versions(self) -> list[dict]:
        """列出所有版本"""
        if not self._dir.exists():
            return []
        versions = sorted(self._dir.glob("v*.json"), key=lambda p: int(p.stem[1:]))
        result = []
        for vf in versions:
            try:
                with open(vf, "r", encoding="utf-8") as f:
                    d = json.load(f)
                result.append({
                    "version": d.get("version", 0),
                    "label": d.get("version_label", ""),
                    "updated_at": d.get("updated_at", ""),
                    "file": vf.name,
                })
            except Exception:
                pass
        return result

    # ── 写入 ──

    def _init_empty(self, name: str = "", video_type: str = ""):
        """初始化空白草稿"""
        self._data = _empty_draft(name, video_type)
        self._data["draft_id"] = self.draft_id

    def _do_save(self, label: str = "") -> str:
        """内部保存，不获取锁。调用者必须持有 _draft_save_lock。"""
        if self._data is None:
            self._init_empty()
        self._data["updated_at"] = datetime.now().isoformat()

        # 自动递增版本
        versions = self.list_versions()
        if versions:
            next_ver = max(v["version"] for v in versions) + 1
        else:
            next_ver = 1
        self._data["version"] = next_ver
        self._data["version_label"] = label or f"v{next_ver}"

        # 确保目录存在
        self._dir.mkdir(parents=True, exist_ok=True)

        vpath = self._dir / f"v{next_ver}.json"
        tmp = vpath.with_suffix(f".tmp.{os.getpid()}")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, vpath)  # 原子写入,写入中断不会损坏
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

        return str(vpath)

    def save(self, label: str = "") -> str:
        """
        保存当前草稿为新版本.版本号自动递增.
        返回: 草稿目录路径
        """
        with _draft_save_lock:
            return self._do_save(label)

    def append_cut_segment(self, seg_data: dict) -> str:
        """原子地追加裁切片段（线程安全）。在锁内重新加载最新版本再保存。"""
        with _draft_save_lock:
            if not self.load():
                self._init_empty()
            existing = self._data["timeline"]["main_track"]["segments"]
            for i, s in enumerate(existing):
                if s.get("id") == seg_data.get("id"):
                    existing[i] = seg_data
                    return self._do_save(f"cut_{seg_data['id']}")
            existing.append(seg_data)
            return self._do_save(f"cut_{seg_data['id']}")

    def _ensure_loaded(self):
        """确保草稿已加载或初始化"""
        if self._data is None:
            if not self.load():
                self._init_empty()

    # ── 数据操作 ──

    def get_data(self) -> dict:
        """获取草稿数据"""
        self._ensure_loaded()
        return self._data

    @property
    def timeline(self) -> dict:
        self._ensure_loaded()
        return self._data["timeline"]

    # ── 源素材管理 ──

    def add_source(self, video_path: str) -> int:
        """添加一个源素材,返回索引"""
        self._ensure_loaded()
        if video_path not in self._data["source_videos"]:
            self._data["source_videos"].append(video_path)
        return self._data["source_videos"].index(video_path)

    def set_source_videos(self, paths: list):
        """设置源素材列表"""
        self._ensure_loaded()
        self._data["source_videos"] = list(paths)

    # ── 主轨道片段管理 ──

    def set_segments(self, segments: list):
        """设置主轨道片段(来自 analyze/arrange)"""
        self._ensure_loaded()
        self._data["timeline"]["main_track"]["segments"] = segments

    # ── 转录管理 ──

    def set_transcript(self, source_video: str, segments: list):
        """
        存储 AI 转录结果.segments 格式:
        [{"index": 0, "start": 0.5, "end": 2.3, "text": "大家好"}, ...]
        """
        self._ensure_loaded()
        self._data["transcript"] = {
            "source_video": source_video,
            "segments": segments,
        }

    def get_transcript(self) -> dict | None:
        """获取转录数据"""
        self._ensure_loaded()
        return self._data.get("transcript")

    def ensure_segment(self, segment_id: int) -> bool:
        """
        确保指定 id 的片段存在.
        如果主轨道是旧格式(list of dicts without id),先转换.
        """
        segments = self._data["timeline"]["main_track"]["segments"]
        for s in segments:
            if s.get("id") == segment_id:
                return True
        return False

    def append_segment(self, source_path: str, duration: float,
                        start: float = 0, description: str = "") -> dict:
        """
        在时间线末尾追加一个片段.

        自动分配 id(从已存在片段的最大 id +1),适合插入 AI 生成的场景.

        Args:
            source_path: 视频文件路径
            duration: 片段时长(秒)
            start: 起始时间(默认 0)
            description: 片段描述(如 "AI生成的过渡场景")

        Returns:
            创建的 segment dict
        """
        self._ensure_loaded()
        segments = self._data["timeline"]["main_track"]["segments"]
        max_id = max((s.get("id", -1) for s in segments), default=-1)
        new_id = max_id + 1

        seg = {
            "id": new_id,
            "source_path": os.path.abspath(source_path),
            "start": round(start, 2),
            "end": round(start + duration, 2),
            "duration": round(duration, 2),
            "speed": 1.0,
            "filters": {
                "crop": None, "chromakey": None,
                "color_grading": None, "color_preset": None,
                "denoise": None, "stabilize": None, "animation": None,
            },
            "text": description,
            "status": "generated",
        }
        segments.append(seg)
        self._data["updated_at"] = datetime.now().isoformat()
        return seg

    def reorder_segments(self, new_order: list[int]) -> bool:
        """
        按指定 clip_id 顺序重新排列主轨道片段.

        Args:
            new_order: 新的 clip_id 顺序列表,如 [2, 0, 1, 3]

        Returns:
            True 表示成功重排
        """
        self._ensure_loaded()
        segments = self._data["timeline"]["main_track"]["segments"]
        if not segments:
            return False

        # 构建 id -> segment 映射
        id_map = {}
        for s in segments:
            sid = s.get("id", -1)
            id_map[sid] = s

        # 按新顺序重建 segment 列表
        reordered = []
        seen = set()
        for sid in new_order:
            if sid in id_map and sid not in seen:
                reordered.append(id_map[sid])
                seen.add(sid)

        # 追加新顺序中没有的片段(放后面)
        for s in segments:
            sid = s.get("id", -1)
            if sid not in seen:
                reordered.append(s)

        if len(reordered) != len(segments):
            return False

        self._data["timeline"]["main_track"]["segments"] = reordered
        return True

    def set_clip_filter(self, clip_id: int, filter_name: str, params: dict):
        """
        给指定片段的某个滤镜设置参数.整个草稿只在这个时刻被修改这一步.
        既有的其他滤镜不受影响.

        示例:
            draft.set_clip_filter(0, "crop", {"x":100, "y":50, "w":800, "h":600})
            draft.set_clip_filter(0, "chromakey", {"color":"#00FF00", "similarity":0.15})
        """
        self._ensure_loaded()
        segments = self._data["timeline"]["main_track"]["segments"]

        # 找到对应片段
        segment = None
        for s in segments:
            if s.get("id") == clip_id:
                segment = s
                break

        if segment is None:
            # 如果主轨道是纯数字 id 列表,先转为完整片段
            if segments and isinstance(segments[0], (int, float)):
                from director.tools.analyze import _get_segments_cached
                src = self._data.get("source_videos", [""])[0] if self._data.get("source_videos") else ""
                if not src:
                    return f"没有源素材,无法解析 clip_id={clip_id}"
                segs = _get_segments_cached(src)
                resolved = []
                for s in segs:
                    resolved.append(_empty_segment(s.get("source_path", src), s.get("start", 0), s.get("end", 30)))
                    resolved[-1]["id"] = s.get("id", len(resolved) - 1)
                    resolved[-1]["text"] = s.get("text", "")
                    resolved[-1]["status"] = s.get("status", "")
                self._data["timeline"]["main_track"]["segments"] = resolved
                # 重新找
                for s in resolved:
                    if s.get("id") == clip_id:
                        segment = s
                        break
            elif not segments:
                # 没有 segment 时自动创建一个(搭积木模式:不分析也能直接裁/调色/动画)
                src = self._data.get("source_videos", [""])[0] if self._data.get("source_videos") else ""
                seg = _empty_segment(src, 0, 60)
                seg["id"] = clip_id
                self._data["timeline"]["main_track"]["segments"] = [seg]
                segment = seg

        if segment is None:
            return f"未找到 clip_id={clip_id}(共 {len(segments)} 个片段)"

        valid_filters = {"crop", "chromakey", "color_grading", "color_preset",
                         "denoise", "stabilize", "animation", "blur"}
        if filter_name not in valid_filters:
            return f"无效滤镜名称: {filter_name},可选: {', '.join(sorted(valid_filters))}"

        segment["filters"][filter_name] = params
        return f"已设置 clip#{clip_id}.{filter_name}"

    # ── 叠层管理(兼容 timeline.py 的 overlay cache)──

    def set_overlays(self, overlay_list: list, track: str = "overlay"):
        """设置叠层列表,track: 'overlay'/'graphic'/'video2'"""
        self._ensure_loaded()
        track_map = {
            "overlay": "overlay_track",
            "graphic": "graphic_track",
            "video2": "video_track_2",
        }
        key = track_map.get(track, track + "_track")
        self._data["timeline"][key] = list(overlay_list)

    def add_overlay_clip(self, source_path: str, track: str = "overlay",
                         start_time: float = 0, duration: float = 0,
                         x: float = 0.1, y: float = 0.1,
                         width: float = 0.3, height: float = 0.3,
                         opacity: float = 1.0) -> dict:
        """往指定轨道添加一个叠层片段.

        Args:
            source_path: 素材路径
            track: 'overlay'(PiP) / 'video2'(双屏)
            start_time: 在主轨道时间轴上的起始时间(秒)
            duration: 持续时长(0=到结束)
            x, y: 归一化位置 (0-1)
            width, height: 归一化尺寸 (0-1)
            opacity: 透明度 (0-1)

        Returns:
            添加的叠层片段 dict
        """
        self._ensure_loaded()
        track_map = {
            "overlay": "overlay_track",
            "video2": "video_track_2",
        }
        key = track_map.get(track, track + "_track")
        clips = self._data["timeline"].setdefault(key, [])
        clip = {
            "id": len(clips),
            "source_path": source_path,
            "start_time": start_time,
            "duration": duration,
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "opacity": opacity,
            "filters": {},
        }
        clips.append(clip)
        return clip

    # ── 音频管理 ──

    def set_bgm(self, source: str, volume: float = -15, ducking: bool = True):
        """设置背景音乐"""
        self._ensure_loaded()
        self._data["audio"]["bgm"] = {"source": source, "volume": volume}
        self._data["audio"]["bgm_ducking"] = ducking

    def set_vocal_track(self, source: str):
        """设置分离后人声轨"""
        self._ensure_loaded()
        self._data["audio"]["vocal_track"] = source

    # ── 花字管理 ──

    def add_flower_text(self, text: str, flower_id: str, time: float,
                        duration: float, font_path: str = "",
                        x: float = 0.5, y: float = 0.5):
        """添加一条花字"""
        self._ensure_loaded()
        flower_texts = self._data["timeline"]["flower_texts"]
        new_id = len(flower_texts)
        flower_texts.append({
            "id": f"ft_{new_id}",
            "text": text,
            "flower_id": flower_id,
            "time": time,
            "duration": duration,
            "font_path": font_path,
            "x": x,
            "y": y,
        })

    # ── 字幕管理 ──

    def set_subtitles(self, subtitles: list):
        """设置字幕列表"""
        self._ensure_loaded()
        self._data["timeline"]["subtitles"] = list(subtitles)

    # ── 转场管理 ──

    def set_transitions(self, transitions: list):
        """设置转场序列"""
        self._ensure_loaded()
        self._data["timeline"]["main_track"]["transitions"] = list(transitions)

    def get_summary(self) -> dict:
        """返回草稿摘要"""
        self._ensure_loaded()
        tl = self._data["timeline"]
        main = tl["main_track"]
        segments = main.get("segments", [])

        filter_counts = {}
        for s in segments:
            for fname, fparams in s.get("filters", {}).items():
                if fparams is not None:
                    filter_counts[fname] = filter_counts.get(fname, 0) + 1

        audio_cfg = self._data.get("audio", {})
        summary = {
            "draft_id": self._data["draft_id"],
            "name": self._data["name"],
            "version": self._data.get("version", 1),
            "version_label": self._data.get("version_label", ""),
            "source_count": len(self._data.get("source_videos", [])),
            "segments": len(segments),
            "segment_ids": [str(s.get("id", "")) for s in segments],
            "active_filters": filter_counts,
            "overlays": len(tl.get("overlay_track", [])),
            "graphics": len(tl.get("graphic_track", [])),
            "video_track_2": len(tl.get("video_track_2", [])),
            "flower_texts": len(tl.get("flower_texts", [])),
            "subtitles": len(tl.get("subtitles", [])),
            "has_bgm": audio_cfg.get("bgm") is not None,
            "has_vocal": audio_cfg.get("vocal_track") is not None,
            "sfx_count": len(audio_cfg.get("sfx", [])),
            "transitions": len(main.get("transitions", [])),
        }
        return summary


# ═══════════════════════════════════════════════════════
#  工具共享函数 — 所有工具加 draft_id 用这一个
# ═══════════════════════════════════════════════════════


def _write_to_draft(
    draft_id: str,
    clip_id: int,
    filter_name: str,
    filter_params: dict,
    label: str = "",
    audio_config: dict = None,
    flower_text: dict = None,
    sfx_item: dict = None,
) -> str:
    """
    工具共享函数:将操作结果写入草稿.
    每个工具调用这个函数来完成搭积木的最后一步.

    Args:
        draft_id: 草稿 ID(空字符串=跳过,不写草稿)
        clip_id: 片段 ID(用于 set_clip_filter)
        filter_name: 滤镜名称,如 "crop","chromakey","color_grading"
        filter_params: 滤镜参数 dict
        label: 版本标签,如 "裁剪完成"
        audio_config: 可选,音频配置 {"source":"...", "volume":-15}
        flower_text: 可选,花字配置 {"text":"...", "flower_id":"...", ...}
        sfx_item: 可选,音效配置 {"source":"...", "start_time":.., ...}

    Returns:
        成功或错误信息(空字符串表示跳过)
    """
    if not draft_id:
        return ""  # 没有 draft_id,跳过
    try:
        d = Draft(draft_id)
        if not d.load():
            return f"草稿 {draft_id} 不存在,跳过"
        d.set_clip_filter(clip_id, filter_name, filter_params)
        if audio_config:
            d.set_bgm(**audio_config)
        if flower_text:
            d.add_flower_text(**flower_text)
        if sfx_item:
            sfx_list = d.get_data()["audio"].setdefault("sfx", [])
            sfx_item.setdefault("id", len(sfx_list))
            sfx_list.append(sfx_item)
        d.save(label or filter_name)
        return f"已写入草稿 {draft_id}"
    except Exception as e:
        return f"写草稿失败: {e}"


# ═══════════════════════════════════════════════════════
#  AI 工具函数
# ═══════════════════════════════════════════════════════

@tool(
    name="create_draft",
    description="创建新草稿.视频管线开始的第一步.创建后各工具可以通过 draft_id 读写草稿,实现搭积木式编辑.",
    phase="all",
    category="timeline",
    tags=["draft", "create", "project"],
    group="草稿管理",
)
def create_draft(
    video_paths_json: str = "",
    video_type: str = "",
    name: str = "",
) -> str:
    """
    创建新草稿.视频管线开始的第一步.
    自动初始化空草稿,可选择关联素材路径.

    Args:
        video_paths_json: 素材路径 JSON 数组(可选),如 ["C:/videos/a.mp4", "C:/videos/b.mp4"]
        video_type: 视频类型(可选),如 "vlog", "anime", "talk" 等
        name: 草稿名称(可选)

    Returns:
        草稿信息 JSON
    """
    draft = Draft(video_type=video_type)
    if video_paths_json:
        try:
            paths = json.loads(video_paths_json) if isinstance(video_paths_json, str) else video_paths_json
            if isinstance(paths, list):
                draft.set_source_videos(paths)
        except (json.JSONDecodeError, TypeError):
            pass
    draft.save(name or "新建草稿")
    info = draft.get_summary()
    info["draft_dir"] = str(draft._dir)
    return json.dumps(info, ensure_ascii=False, indent=2)


@tool(
    name="list_drafts",
    description="列出所有草稿.返回每份草稿的摘要信息(ID,名称,版本数,状态等).AI 用此工具查找已有草稿,可继续编辑或从中导出.",
    phase="plan",
    category="timeline",
    tags=["draft", "list", "project"],
    group="草稿管理",
)
def list_drafts() -> str:
    """
    列出所有草稿.

    Returns:
        JSON 数组,每个草稿含 id,name,version,版本数
    """
    result = []
    # 优先列出当前项目下的草稿（CLIPMIND_PIPELINE_DIR）
    project_dir = os.environ.get("CLIPMIND_PIPELINE_DIR", "")
    if project_dir:
        pdraft_dir = Path(project_dir) / "drafts"
        if pdraft_dir.is_dir():
            for ddir in sorted(pdraft_dir.iterdir()):
                if not ddir.is_dir():
                    continue
                draft = Draft(ddir.name)
                info = draft.get_summary()
                if info:
                    versions = draft.list_versions()
                    info["versions"] = len(versions)
                    result.append(info)
    # 也列出全局草稿（如果目录存在）
    if _DRAFT_DIR.exists():
        for ddir in sorted(_DRAFT_DIR.iterdir()):
            if not ddir.is_dir():
                continue
            # 跳过已在项目草稿中列出的
            if project_dir and any(r.get("draft_id", r.get("id", "")) == ddir.name for r in result):
                continue
            draft = Draft(ddir.name)
            info = draft.get_summary()
            if info:
                versions = draft.list_versions()
                info["versions"] = len(versions)
                result.append(info)
    return json.dumps(result, ensure_ascii=False, indent=2) if result else "[]"


@tool(
    name="save_draft",
    description="保存当前草稿.每次调用都保存为新版本,不会覆盖之前的历史.AI 应在每个重要操作后调用此工具并写标签,如『裁剪完成』『抠像完成』『动画完成』等.",
    phase="all",
    category="timeline",
    tags=["draft", "save", "version"],
    group="草稿管理",
)
def save_draft(
    draft_id: str,
    label: str = "",
) -> str:
    """
    保存当前草稿.每次都保存为新版本,不覆盖历史.

    Args:
        draft_id: 草稿 ID
        label: 版本标签(可选),如 "裁剪完成","抠像完成"

    Returns:
        保存结果
    """
    draft = Draft(draft_id)
    if not draft.load():
        return f"草稿 {draft_id} 不存在"
    vpath = draft.save(label)
    summary = draft.get_summary()
    summary["saved_to"] = vpath
    return json.dumps(summary, ensure_ascii=False, indent=2)


@tool(
    name="show_draft",
    description="查看草稿摘要信息.返回当前草稿的状态:片段数,已应用的滤镜列表,叠层数,花字数,字幕数,BGM 状态等.AI 用此工具了解当前编辑进度.",
    phase="plan",
    category="timeline",
    tags=["draft", "view", "status"],
    group="草稿管理",
)
def show_draft(draft_id: str, version: int = 0) -> str:
    """
    查看草稿完整内容.

    Args:
        draft_id: 草稿 ID
        version: 版本号(0=最新版)

    Returns:
        草稿 JSON
    """
    draft = Draft(draft_id)
    data = draft.load_version(version) if version > 0 else draft.load()
    if data is None:
        return f"草稿 {draft_id} 不存在"
    # 返回摘要而非全部(完整内容太大)
    summary = draft.get_summary()
    return json.dumps(summary, ensure_ascii=False, indent=2)


@tool(
    name="revert_draft",
    description=(
        "回退草稿到指定版本.用户对某步不满意时,直接回到之前的状态重新来."
        "用 list_drafts 可查看所有版本号.回退后原最新版本不会被删除,只是新建一个版本."
    ),
    phase="all",
    category="timeline",
    tags=["draft", "revert", "undo"],
    group="草稿管理",
)
def revert_draft(draft_id: str, target_version: int) -> str:
    """
    回退草稿到指定版本.

    Args:
        draft_id: 草稿 ID
        target_version: 目标版本号(如 3 表示回退到 v3)

    Returns:
        回退结果
    """
    draft = Draft(draft_id)
    data = draft.load_version(target_version)
    if data is None:
        versions = draft.list_versions()
        nums = [v["version"] for v in versions]
        return f"草稿 {draft_id} 的 v{target_version} 不存在.可用版本: {nums}"
    draft._data = data
    vpath = draft.save(f"回退到 v{target_version}")
    return f"已回退草稿 {draft_id} 到 v{target_version},保存为 {vpath}"


@tool(
    name="add_overlay_clip",
    description=(
        "往草稿添加一个叠层视频片段(PiP 画中画或双屏)."
        "用于在主画面之上叠加另一个视频画面,如游戏+摄像头,访谈+B-roll."
        "叠层位置和尺寸可调,支持 overlay(画中画)和 video2(双屏)两种轨道."
    ),
    phase="plan",
    category="timeline",
    tags=["draft", "overlay", "pip", "multi-track"],
    group="草稿管理",
)
def add_overlay_clip(
    draft_id: str,
    source_path: str,
    track: str = "overlay",
    start_time: float = 0,
    duration: float = 0,
    x: float = 0.6,
    y: float = 0.6,
    width: float = 0.35,
    height: float = 0.35,
    opacity: float = 1.0,
) -> str:
    """
    往草稿添加一个叠层视频片段.

    Args:
        draft_id: 草稿 ID
        source_path: 叠层素材的完整路径
        track: 轨道类型."overlay"=画中画(小窗浮在主画面上),"video2"=双屏(各占一半区域)
        start_time: 在主轨道时间轴上的起始时间(秒),默认 0
        duration: 持续时长(秒),默认 0=到主轨道结束
        x: 叠层在画面中的水平位置(归一化 0-1),默认 0.6(右侧)
        y: 叠层在画面中的垂直位置(归一化 0-1),默认 0.6(下方)
        width: 叠层宽度(归一化 0-1),默认 0.35
        height: 叠层高度(归一化 0-1),默认 0.35
        opacity: 透明度(0-1),默认 1.0=不透明

    Returns:
        添加结果 JSON
    """
    if track not in ("overlay", "video2"):
        return f"无效轨道类型: {track},可选: overlay, video2"

    if not os.path.exists(source_path):
        return f"素材文件不存在: {source_path}"

    try:
        d = Draft(draft_id)
        if not d.load():
            return f"草稿 {draft_id} 不存在"

        clip = d.add_overlay_clip(
            source_path=source_path,
            track=track,
            start_time=start_time,
            duration=duration,
            x=x, y=y,
            width=width, height=height,
            opacity=opacity,
        )
        d.save(f"添加叠层 track={track}")
        return json.dumps(clip, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"添加叠层失败: {e}"


@tool(
    name="apply_clip_filter",
    description=(
        "给草稿主轨道的某个片段应用滤镜效果."
        "支持 chromakey(绿幕抠像),blur(模糊),crop(裁剪),denoise(降噪)等."
        "用于做画中画抠像,回忆画面模糊,镜头裁剪等效果."
    ),
    phase="plan",
    category="timeline",
    tags=["draft", "filter", "chromakey", "blur", "crop"],
    group="草稿管理",
)
def apply_clip_filter(
    draft_id: str,
    clip_id: int,
    filter_name: str,
    params_json: str = "",
) -> str:
    """
    给草稿主轨道的某个片段应用滤镜效果.

    Args:
        draft_id: 草稿 ID
        clip_id: 片段 ID(用 list_segments 可查到)
        filter_name: 滤镜类型.可选:
            - chromakey: 绿幕抠像.params_json: {"color":"#00FF00","similarity":0.15,"blend":0.1}
            - blur: 画面模糊.params_json: {"type":"gaussian","strength":5}
            - crop: 裁剪画面.params_json: {"x":100,"y":50,"w":800,"h":600}
            - denoise: 降噪.params_json: {"type":"nlmeans","strength":10}
            - stabilize: 防抖.params_json: {"method":"vidstab","shakiness":5}
            - animation: 动画.params_json: {"type":"motion","keyframes":[...]}
        params_json: JSON 字符串格式的参数(如上示例)

    Returns:
        设置结果
    """
    import json as _json
    try:
        params = _json.loads(params_json) if params_json else {}
    except _json.JSONDecodeError as e:
        return f"params_json 不是合法 JSON: {e}"

    if filter_name not in ("chromakey", "blur", "crop", "denoise", "stabilize",
                           "animation", "color_grading", "color_preset"):
        return (f"无效滤镜: {filter_name}.可选: chromakey, blur, crop, denoise, "
                f"stabilize, animation, color_grading, color_preset")

    try:
        d = Draft(draft_id)
        if not d.load():
            return f"草稿 {draft_id} 不存在"
        result = d.set_clip_filter(clip_id, filter_name, params)
        d.save(f"滤镜 {filter_name} clip#{clip_id}")
        return f"✅ {result}"
    except Exception as e:
        return f"应用滤镜失败: {e}"


@tool(
    name="add_flower_text",
    description="给草稿添加花字（文字特效）。支持自定义文字内容、位置、大小、颜色、动画效果。用于在视频画面上叠加标题、标注、装饰性文字。",
    phase="plan",
    category="timeline",
    tags=["draft", "flower_text", "text", "effect"],
    group="花字与动画(效果层)",
)
def add_flower_text_tool(
    draft_id: str,
    text: str = "",
    flower_id: str = "title",
    time: float = 0,
    duration: float = 3.0,
    font_size: int = 48,
    color: str = "#FFFFFF",
    x: float = 0.5,
    y: float = 0.5,
) -> str:
    """
    给草稿添加一条花字（文字特效）。

    Args:
        draft_id: 草稿 ID
        text: 文字内容
        flower_id: 花字样式 ID（如 title, subtitle, tag）
        time: 出现时间（秒）
        duration: 持续时长（秒）
        font_size: 字号（默认 48）
        color: 颜色十六进制（默认 #FFFFFF 白色）
        x: 水平位置（归一化 0-1，默认 0.5 居中）
        y: 垂直位置（归一化 0-1，默认 0.5 居中）

    Returns:
        添加结果
    """
    try:
        d = Draft(draft_id)
        if not d.load():
            return f"草稿 {draft_id} 不存在"
        d.add_flower_text(
            text=text or "",
            flower_id=flower_id,
            time=time,
            duration=duration,
            x=x,
            y=y,
        )
        d.save(f"花字_{flower_id}")
        return f"✅ 已添加花字「{text}」到草稿 {draft_id}"
    except Exception as e:
        return f"添加花字失败: {e}"


@tool(
    name="set_transitions",
    description="设置草稿主轨道片段之间的转场效果。转场列表按片段顺序排列，每个转场对应相邻两个片段之间的切换效果。",
    phase="plan",
    category="timeline",
    tags=["draft", "transition", "effect"],
    group="花字与动画(效果层)",
)
def set_transitions_tool(
    draft_id: str,
    transitions_json: str = "",
) -> str:
    """
    设置草稿主轨道的转场序列。

    Args:
        draft_id: 草稿 ID
        transitions_json: 转场列表 JSON，如 [{"type":"crossfade","duration":0.5},{"type":"fade_to_black","duration":0.3}]

    Returns:
        设置结果
    """
    import json as _json
    try:
        transitions = _json.loads(transitions_json) if transitions_json else []
    except _json.JSONDecodeError as e:
        return f"transitions_json 不是合法 JSON: {e}"

    if not isinstance(transitions, list):
        return "transitions_json 必须是 JSON 数组"

    try:
        d = Draft(draft_id)
        if not d.load():
            return f"草稿 {draft_id} 不存在"
        d.set_transitions(transitions)
        d.save(f"转场_{len(transitions)}条")
        return f"✅ 已设置 {len(transitions)} 个转场效果"
    except Exception as e:
        return f"设置转场失败: {e}"


# 工具已通过 @tool 装饰器自动注册到 Registry
