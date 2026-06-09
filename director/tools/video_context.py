"""
上下文传导 — 顺序编号，杜绝混乱
=================================

角色产出按顺序存放，不依赖角色名做文件 key。
每个文件叫 ctx_001.json → ctx_002.json → ...

这样做的原因：
  - 自由命名 → "分析师""内容分析师""分析专员" → 分不清读哪个
  - 顺序编号 → 第 1 个上下文永远叫 ctx_001，不用记名字

用法：
  # 存：自动编号
  save_context(data, work_dir, role="内容分析师")
  # → _agent_contexts/ctx_001.json

  # 取：按序号或取最新
  load_latest_context(work_dir)    # ctx_N.json（最新的）
  load_context(work_dir, 1)        # ctx_001.json

  # 看链
  list_contexts(work_dir)
  # → [{"index": 1, "role": "内容分析师", ...},
  #     {"index": 2, "role": "编排师", ...}]
"""

import json, os, subprocess, time, re
from pathlib import Path
from typing import Optional

from director.logging_config import get_logger

log = get_logger("tools.video_context")

_CONTEXT_DIR_NAME = "_agent_contexts"


# ═══════════════════════════════════════════════════════════
#  核心：顺序编号存储
# ═══════════════════════════════════════════════════════════

def _next_index(ctx_dir: str) -> int:
    """找到下一个可用的序号（自动补齐）"""
    if not os.path.isdir(ctx_dir):
        return 1
    max_n = 0
    for fname in os.listdir(ctx_dir):
        m = re.match(r"ctx_(\d+)\.json", fname)
        if m:
            n = int(m.group(1))
            if n > max_n:
                max_n = n
    return max_n + 1


def _ctx_path(ctx_dir: str, index: int) -> str:
    return os.path.join(ctx_dir, f"ctx_{index:03d}.json")


def save_context(data: dict, work_dir: str, role: str = "") -> str:
    """
    保存一段上下文，自动编号。

    Args:
        data: 上下文数据
        work_dir: 工作目录
        role: 角色名（仅用于标注，不做文件 key）

    Returns:
        文件绝对路径
    """
    ctx_dir = os.path.join(work_dir, _CONTEXT_DIR_NAME)
    os.makedirs(ctx_dir, exist_ok=True)

    index = _next_index(ctx_dir)
    path = _ctx_path(ctx_dir, index)

    payload = {
        "_meta": {
            "index": index,
            "role": role,
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        },
        "data": data,
    }

    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        log.info(f"[上下文] #{index} ({role}) 已保存 → {path}")
    except Exception as e:
        log.error(f"[上下文] 保存失败: {e}")
        try:
            os.remove(tmp)
        except OSError:
            pass

    return path


def load_context(work_dir: str, index: int) -> Optional[dict]:
    """
    加载指定序号的上下文（不含 _meta 元信息）。

    Args:
        work_dir: 工作目录
        index: 序号，1 起

    Returns:
        data 部分，None 表示不存在
    """
    ctx_dir = os.path.join(work_dir, _CONTEXT_DIR_NAME)
    path = _ctx_path(ctx_dir, index)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload.get("data")
    except (json.JSONDecodeError, IOError) as e:
        log.warning(f"[上下文] 读取 #{index} 失败: {e}")
        return None


def load_latest_context(work_dir: str) -> Optional[dict]:
    """加载最新一段上下文（data 部分）"""
    ctx_dir = os.path.join(work_dir, _CONTEXT_DIR_NAME)
    if not os.path.isdir(ctx_dir):
        return None
    max_n = 0
    for fname in os.listdir(ctx_dir):
        m = re.match(r"ctx_(\d+)\.json", fname)
        if m:
            n = int(m.group(1))
            if n > max_n:
                max_n = n
    if max_n == 0:
        return None
    return load_context(work_dir, max_n)


def list_contexts(work_dir: str) -> list[dict]:
    """
    列出所有上下文（按序号排序）。

    返回:
        [{"index": 1, "role": "...", "saved_at": "...", "has_data": True}, ...]
    """
    ctx_dir = os.path.join(work_dir, _CONTEXT_DIR_NAME)
    if not os.path.isdir(ctx_dir):
        return []

    entries = []
    for fname in os.listdir(ctx_dir):
        m = re.match(r"ctx_(\d+)\.json", fname)
        if not m:
            continue
        index = int(m.group(1))
        try:
            with open(os.path.join(ctx_dir, fname), "r", encoding="utf-8") as f:
                payload = json.load(f)
            meta = payload.get("_meta", {})
            entries.append({
                "index": meta.get("index", index),
                "role": meta.get("role", ""),
                "saved_at": meta.get("saved_at", ""),
            })
        except Exception:
            entries.append({"index": index, "role": "?", "saved_at": "?"})

    entries.sort(key=lambda e: e["index"])
    return entries


def get_context_chain_text(work_dir: str) -> str:
    """上下文链的人类可读文本（给 AI 看的）"""
    entries = list_contexts(work_dir)
    if not entries:
        return "（无上下文）"

    lines = ["## 上下文传导链", ""]
    for e in entries:
        role = e.get("role", "?")
        time_str = e.get("saved_at", "?")
        lines.append(f"  #{e['index']:03d} [{role}]  {time_str}")

    lines.append("")
    lines.append("用 load_context_by_index(N) 加载指定序号，或用 load_latest_context() 加载最新。")
    return "\n".join(lines)


def clear_contexts(work_dir: str):
    """清除所有上下文（新项目时调用）"""
    ctx_dir = os.path.join(work_dir, _CONTEXT_DIR_NAME)
    if os.path.isdir(ctx_dir):
        import shutil
        shutil.rmtree(ctx_dir, ignore_errors=True)
        log.info("[上下文] 已清除")


# ═══════════════════════════════════════════════════════════
#  技术分析工具（给角色自检时调用）
# ═══════════════════════════════════════════════════════════

def get_video_metadata(video_path: str) -> dict:
    """
    提取视频的基本技术元数据。
    角色在自检/复检时可以调用这个看视频的基本信息。

    返回:
        {"path", "duration_s", "width", "height", "fps",
         "codec", "file_size_mb", "has_audio", "audio_info"}
    """
    if not os.path.exists(video_path):
        return {"error": f"文件不存在: {video_path}"}

    meta = {"path": os.path.abspath(video_path)}

    try:
        meta["file_size_mb"] = round(os.path.getsize(video_path) / (1024 * 1024), 1)
    except Exception:
        meta["file_size_mb"] = 0.0

    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format",
             "-of", "json", video_path],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return meta

        data = json.loads(r.stdout)
        fmt = data.get("format", {})

        dur = fmt.get("duration", "0")
        try:
            meta["duration_s"] = round(float(dur), 2)
        except ValueError:
            meta["duration_s"] = 0.0

        bitrate = fmt.get("bit_rate", "0")
        try:
            meta["bitrate_kbps"] = round(int(bitrate) / 1000)
        except (ValueError, TypeError):
            meta["bitrate_kbps"] = 0

        meta["has_audio"] = False
        meta["audio_info"] = None

        for s in data.get("streams", []):
            codec_type = s.get("codec_type", "")
            if codec_type == "video":
                meta["width"] = s.get("width", 0)
                meta["height"] = s.get("height", 0)
                meta["codec"] = s.get("codec_name", "")
                r_frame_rate = s.get("r_frame_rate", "0/1")
                if "/" in r_frame_rate:
                    try:
                        num, den = r_frame_rate.split("/")
                        meta["fps"] = round(float(num) / float(den), 2)
                    except (ValueError, ZeroDivisionError):
                        meta["fps"] = 0.0
            elif codec_type == "audio":
                meta["has_audio"] = True
                meta["audio_info"] = {
                    "codec": s.get("codec_name"),
                    "sample_rate": s.get("sample_rate", 0),
                    "channels": s.get("channels", 0),
                }
    except Exception as e:
        meta["error"] = str(e)

    return meta


def check_resolution_match(metadata_a: dict, metadata_b: dict) -> dict:
    """比较两段视频的分辨率/帧率是否匹配（编排角色自检用）"""
    issues = []
    w1, h1 = metadata_a.get("width", 0), metadata_a.get("height", 0)
    w2, h2 = metadata_b.get("width", 0), metadata_b.get("height", 0)
    if (w1, h1) != (w2, h2):
        issues.append(f"分辨率不匹配: {w1}x{h1} vs {w2}x{h2}")

    f1, f2 = metadata_a.get("fps", 0), metadata_b.get("fps", 0)
    if f1 and f2 and abs(f1 - f2) > 1:
        issues.append(f"帧率不匹配: {f1}fps vs {f2}fps")

    a1 = metadata_a.get("has_audio", False)
    a2 = metadata_b.get("has_audio", False)
    if a1 != a2:
        issues.append(f"音频存在性不一致: {'有' if a1 else '无'} vs {'有' if a2 else '无'}")

    return {
        "match": len(issues) == 0,
        "issues": issues,
    }
