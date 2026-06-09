"""记忆存储 — 分身分析产出的结构化索引持久化

索引采用**分级结构**:一个总览文件 + 每个素材独立文件.
100+ 素材场景下,Diretor 先看 summary.json 总览,再按需深入单个素材文件,
不会被海量 chunk 数据撑爆上下文.

文件布局:
  _index/
  ├── summary.json              ← 总览(素材数/类型/标签/文件索引)
  ├── material_mat_001.json     ← 每个素材独立索引(含该素材的 chunks)
  ├── material_mat_002.json
  ├── delegate_20250101_*.json  ← 分身记录(不变)
  └── analysis_index.json       ← (旧格式,向后兼容,只读不写)

生命周期:项目创建 -> 每次 dispatch_clone 写入 -> 项目完成删除.
"""
import os, json, re, time
from typing import Optional

INDEX_DIR = "_index"

# 素材 ID 计数器(线程安全用文件名保证)
_material_counter = 0


def _index_dir(work_dir: str) -> str:
    return os.path.join(work_dir, INDEX_DIR)


def ensure_index_dir(work_dir: str) -> str:
    d = _index_dir(work_dir)
    os.makedirs(d, exist_ok=True)
    return d


def _next_material_id() -> str:
    """生成自增素材 ID: mat_001, mat_002, ..."""
    global _material_counter
    _material_counter += 1
    return f"mat_{_material_counter:04d}"


def _filename_to_material_id(filename: str, existing_ids: set) -> str:
    """从文件名生成稳定的素材 ID(避免同名重复)"""
    safe = re.sub(r"[^\w\u4e00-\u9fff]", "_", filename)[:30]
    base = f"mat_{safe}"
    if base not in existing_ids:
        return base
    for i in range(2, 999):
        candidate = f"{base}_{i}"
        if candidate not in existing_ids:
            return candidate
    return _next_material_id()


# ─── 写入 ────────────────────────────────────────────────

def save_analysis_index(work_dir: str, analysis_data: dict) -> str:
    """并行分析完成后,把结构化索引写入 work_dir/_index/

    写入 **分级索引**:
      - summary.json — 总览,轻量级
      - material_*.json — 每个素材一个独立文件

    Args:
        work_dir: 项目工作目录
        analysis_data: 素材分析结果 dict (由 Director/Agent 通过 batch_analyze 生成)
            {"report": str, "per_material": [chunk_results...], ...}

    Returns:
        写入的 summary.json 路径
    """
    idx_dir = ensure_index_dir(work_dir)
    per_material = analysis_data.get("per_material", [])

    # ── 按素材分组 ──
    material_groups = {}  # filename -> list[chunk_dict]
    for c in per_material:
        fname = c.get("filename", "") or os.path.basename(c.get("source", ""))
        if not fname:
            fname = f"unknown_{len(material_groups)}"
        material_groups.setdefault(fname, []).append(c)

    # ── 检查已有素材 ID,保持稳定 ──
    existing_ids = set()
    for fname in os.listdir(idx_dir):
        if fname.startswith("material_") and fname.endswith(".json"):
            existing_ids.add(fname.replace(".json", ""))

    material_index = {}  # material_id -> {info}
    total_chunks = 0

    for fname, chunks in material_groups.items():
        mid = _filename_to_material_id(fname, existing_ids)
        existing_ids.add(mid)

        # 构建该素材的 chunks
        material_chunks = []
        for c in chunks:
            material_chunks.append({
                "source": c.get("source", ""),
                "filename": fname,
                "chunk_idx": c.get("chunk_idx", 0),
                "start_offset": c.get("start_offset", 0),
                "end_offset": c.get("end_offset", 0),
                "duration": c.get("duration", 0),
                "video_description": c.get("video_analysis", ""),
                "transcript": c.get("audio_analysis", ""),
            })

        total_chunks += len(material_chunks)

        # 提取标签(从场景描述中提取关键词)
        tags = _extract_tags(material_chunks)
        total_duration = sum(c.get("duration", 0) for c in material_chunks)

        # 写入独立素材文件
        material_data = {
            "type": "material_index",
            "material_id": mid,
            "filename": fname,
            "total_chunks": len(material_chunks),
            "total_duration": round(total_duration, 1),
            "tags": tags,
            "chunks": material_chunks,
        }
        mat_path = os.path.join(idx_dir, f"{mid}.json")
        with open(mat_path, "w", encoding="utf-8") as f:
            json.dump(material_data, f, ensure_ascii=False, indent=2)

        material_index[mid] = {
            "filename": fname,
            "chunks": len(material_chunks),
            "duration": round(total_duration, 1),
            "tags": tags,
            "file": f"{mid}.json",
        }

    # ── 写入总览文件 ──
    full_report = analysis_data.get("report", "")
    summary = {
        "type": "index_summary",
        "created_at": time.time(),
        "total_materials": len(material_groups),
        "total_chunks": total_chunks,
        "elapsed": analysis_data.get("elapsed", 0),
        "materials": [],
        "full_report": full_report,
    }

    # 按素材名排序,确保总览稳定
    for mid in sorted(material_index.keys()):
        info = material_index[mid]
        summary["materials"].append({
            "id": mid,
            "filename": info["filename"],
            "chunks": info["chunks"],
            "duration": info["duration"],
            "tags": info["tags"],
            "file": info["file"],
        })

    summary_path = os.path.join(idx_dir, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    return summary_path


def _extract_tags(chunks: list[dict]) -> list[str]:
    """从 chunk 的场景描述中提取关键词标签"""
    tag_keywords = {
        "动作", "碰撞", "爆炸", "高速", "冲击",
        "对话", "交谈", "讨论", "独白",
        "练习", "训练",
        "情感", "悲伤", "感动", "回忆", "哭泣",
        "搞笑", "幽默", "喜剧",
        "介绍", "解说", "说明",
        "风景", "场景", "环境",
        "开场", "结尾",
        "爆发", "冲刺",
        "追逐", "奔跑",
        "紧张", "悬疑", "危机",
        "胜利", "失败", "逆转",
        "美食", "烹饪",
        "教学", "教程", "演示",
        "开箱", "评测", "对比",
    }
    text = " ".join(
        c.get("video_description", "") + " " + c.get("transcript", "")
        for c in chunks
    ).lower()
    found = []
    for kw in sorted(tag_keywords):
        if kw in text:
            found.append(kw)
    return found[:8]  # 最多8个标签


def save_delegate_entities(work_dir: str, mission: str, entities: list[dict], agent_type: str = "分身") -> str:
    """分身执行完毕后,把产出的实体写入索引

    Args:
        work_dir: 项目工作目录
        mission: 分身的任务描述
        entities: 分身产出的结构化实体列表
        agent_type: 分身类型

    Returns:
        写入的文件路径
    """
    idx_dir = ensure_index_dir(work_dir)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r"[^\w\-]", "_", mission[:40])
    path = os.path.join(idx_dir, f"delegate_{timestamp}_{safe_name}.json")

    record = {
        "type": "delegate_entities",
        "agent_type": agent_type,
        "mission": mission,
        "created_at": timestamp,
        "entities": entities,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    return path


# ─── 查询 ────────────────────────────────────────────────

def search_index(work_dir: str, query: str, max_results: int = 10) -> list[dict]:
    """在当前项目索引中搜索

    搜索范围: 所有素材索引文件的 chunk 视频描述/ASR 文本/完整报告.
    多词搜索:自动拆成单个关键词,任意词匹配即返回.
    例:搜索"战斗 推荐"会匹配包含"战斗"或"推荐"的chunk.

    Args:
        work_dir: 项目工作目录
        query: 搜索关键词(空格分隔的多个关键词用OR逻辑匹配)
        max_results: 最多返回结果数

    Returns:
        匹配的 chunk 片段列表
    """
    idx_dir = _index_dir(work_dir)
    if not os.path.exists(idx_dir):
        return []

    results = []
    seen_keys = set()

    # 将查询拆成单个关键词(多词搜索:任意词匹配即返回)
    keywords = [kw.strip().lower() for kw in query.split() if kw.strip()]
    if not keywords:
        keywords = [query.lower()]

    full_report_cache = None  # 从 summary.json 缓存 full_report

    # 遍历所有索引文件
    for fname in sorted(os.listdir(idx_dir)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(idx_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        dtype = data.get("type", "")

        # ── 新格式: material_index(素材独立文件) ──
        if dtype == "material_index":
            for chunk in data.get("chunks", []):
                haystack = (chunk.get("video_description", "")
                           + " " + chunk.get("transcript", "")).lower()
                chunk_key = (data.get("filename", ""), chunk.get("start_offset", 0))
                if chunk_key in seen_keys:
                    continue
                matched = any(kw in haystack for kw in keywords)
                if matched:
                    results.append({
                        "source": "material_index",
                        "material_id": data.get("material_id", ""),
                        "filename": data.get("filename", ""),
                        "start_offset": chunk.get("start_offset", 0),
                        "end_offset": chunk.get("end_offset", 0),
                        "video_description": chunk.get("video_description", ""),
                        "transcript": chunk.get("transcript", ""),
                        "match_in": "video_description" if any(kw in chunk.get("video_description", "").lower() for kw in keywords) else "transcript",
                    })
                seen_keys.add(chunk_key)

        # ── 新格式: summary.json(总览,搜索 full_report) ──
        elif dtype == "index_summary":
            full_report_cache = data.get("full_report", "")

        # ── 旧格式兼容: analysis_index(单文件全量索引) ──
        elif dtype == "analysis_index":
            for chunk in data.get("chunks", []):
                haystack = (chunk.get("video_description", "")
                           + " " + chunk.get("transcript", "")).lower()
                chunk_key = (chunk.get("filename", ""), chunk.get("start_offset", 0))
                if chunk_key in seen_keys:
                    continue
                matched = any(kw in haystack for kw in keywords)
                if matched:
                    results.append({
                        "source": "analysis_index",
                        "filename": chunk.get("filename", ""),
                        "start_offset": chunk.get("start_offset", 0),
                        "end_offset": chunk.get("end_offset", 0),
                        "video_description": chunk.get("video_description", ""),
                        "transcript": chunk.get("transcript", ""),
                        "match_in": "video_description" if any(kw in chunk.get("video_description", "").lower() for kw in keywords) else "transcript",
                    })
                seen_keys.add(chunk_key)
            # 旧格式的 full_report 也在自身文件里
            full = data.get("full_report", "")
            if full and any(kw in full.lower() for kw in keywords):
                results.append({
                    "source": "analysis_index",
                    "note": f"在完整分析报告中匹配到「{query}」",
                })

        # ── delegate_entities(分身记录,不变) ──
        elif dtype == "delegate_entities":
            mission = data.get("mission", "")
            mission_match = any(kw in mission.lower() for kw in keywords)
            for ent in data.get("entities", []):
                ent_text = (ent.get("result", "") + " " + ent.get("tool", "")).lower()
                if mission_match or any(kw in ent_text for kw in keywords):
                    results.append({
                        "source": "delegate",
                        "mission": mission[:200],
                        "tool": ent.get("tool", ""),
                        "result": ent.get("result", ""),
                    })
            if mission_match and not data.get("entities"):
                results.append({
                    "source": "delegate",
                    "mission": mission[:200],
                })

    # 在 full_report 中搜索(从 summary.json 或旧文件获取)
    if full_report_cache and any(kw in full_report_cache.lower() for kw in keywords):
        results.append({
            "source": "index_summary",
            "note": f"在完整分析报告中匹配到「{query}」",
        })

    # 去重 + 截断
    seen = set()
    deduped = []
    for r in results:
        key = json.dumps(r, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    return deduped[:max_results]


def get_index_summary(work_dir: str) -> str:
    """返回当前索引的摘要文本(Director 可读)"""
    idx_dir = _index_dir(work_dir)
    if not os.path.exists(idx_dir):
        return "索引为空"

    summary_path = os.path.join(idx_dir, "summary.json")
    if os.path.exists(summary_path):
        # ── 新格式:读取 summary.json ──
        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
        else:
            lines = [
                f"索引目录: {idx_dir}",
                f"素材数: {data.get('total_materials', 0)}",
                f"分析 chunk 数: {data.get('total_chunks', 0)}",
                f"耗时: {data.get('elapsed', 0):.0f}s",
                "",
                "素材列表:",
            ]
            for m in data.get("materials", []):
                tags_str = ", ".join(m.get("tags", [])) if m.get("tags") else "-"
                lines.append(
                    f"  [{m.get('id','')}] {m.get('filename','')} "
                    f"| {m.get('chunks',0)} chunk "
                    f"| {m.get('duration',0):.0f}s "
                    f"| 标签: {tags_str}"
                )
            lines.append("")
            lines.append('细看某个素材: browse_memory(target="素材文件名.json")')
            return "\n".join(lines)
    # fallback: 不保证其他情况下能返回什么
    total_files = 0
    total_chunks = 0
    files_list = []
    for fname in sorted(os.listdir(idx_dir)):
        if not fname.endswith(".json"):
            continue
        total_files += 1
        files_list.append(fname)
        fpath = os.path.join(idx_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            dtype = data.get("type", "")
            if dtype in ("analysis_index", "material_index", "index_summary"):
                total_chunks += data.get("total_chunks", 0)
        except Exception:
            pass

    lines = [
        f"索引目录: {idx_dir}",
        f"索引文件数: {total_files}",
        f"分析 chunk 数: {total_chunks}",
        "文件列表:",
    ]
    for fname in files_list:
        lines.append(f"  - {fname}")
    return "\n".join(lines)


# ─── 生命周期 ─────────────────────────────────────────────

def clear_index(work_dir: str) -> bool:
    """项目完成后清理索引文件"""
    idx_dir = _index_dir(work_dir)
    if not os.path.exists(idx_dir):
        return True
    try:
        for fname in os.listdir(idx_dir):
            fpath = os.path.join(idx_dir, fname)
            try:
                if os.path.isfile(fpath):
                    os.remove(fpath)
            except OSError:
                pass
        os.rmdir(idx_dir)
        return True
    except OSError:
        return False


# ─── 注册为 Director 全局工具 ───────────────────────────────

_work_dir_global = None


def _find_work_dir() -> str:
    """从全局变量或管线状态获取 work_dir"""
    global _work_dir_global
    if _work_dir_global:
        return _work_dir_global
    try:
        from director.tools.cut import _find_draft_dir
        return _find_draft_dir()
    except Exception:
        pass
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "drafts", "main")


def set_work_dir(wd: str):
    """设置全局 work_dir（Director Runner 初始化时调用）"""
    global _work_dir_global
    _work_dir_global = wd


from director.registry import tool


def _extract_scenes_raw(vd_str: str) -> list[dict]:
    """从 video_description 的 JSON 字符串中提取场景列表

    video_description 里 scenes 的 content 往往被包在 markdown code block 里,
    且 raw 内容可能被截断. 此函数用多种策略尝试提取.
    """
    try:
        vd = json.loads(vd_str)
    except (json.JSONDecodeError, TypeError):
        return []
    scenes = vd.get("scenes", [])
    if not scenes:
        return []

    out = []
    for s in scenes:
        if "error" not in s:
            out.append(s)
            continue
        # 从 raw 字段提取场景文本
        raw = s.get("raw", "")
        if not raw:
            continue
        # 提取 markdown code block 中的内容
        m = re.search(r'```(?:json)?\s*(.*?)(```|$)', raw, re.DOTALL)
        if not m:
            continue
        raw_json_text = m.group(1).strip()
        if not raw_json_text:
            continue

        # 策略1: 尝试完整 JSON 解析
        try:
            extracted = json.loads(raw_json_text)
            if isinstance(extracted, list):
                out.extend(extracted)
                continue
        except json.JSONDecodeError:
            pass

        # 策略2: 逐对象提取 — raw 是截断的 JSON 数组,用正则匹配每个 {...} 块
        for obj_match in re.finditer(r'\{\s*"start"[^}]+?\}', raw_json_text, re.DOTALL):
            try:
                obj = json.loads(obj_match.group(0))
                out.append(obj)
            except json.JSONDecodeError:
                pass

    return out


def _format_material_pretty(data: dict, max_chars: int = 8000) -> str:
    """把单个素材索引文件渲染成易读格式

    自动提取 scenes 内容,跳过 JSON 的转义嵌套,直接展示场景描述.
    """
    lines = []
    chunks = data.get("chunks", [])
    total = data.get("total_chunks", 0)
    filename = data.get("filename", "")
    tags = data.get("tags", [])
    lines.append(f"## 素材: {filename} ({total} 个chunk)")
    if tags:
        lines.append(f"   标签: {', '.join(tags)}")
    lines.append("")

    for i, chunk in enumerate(chunks):
        source = chunk.get("source", "")[-30:]
        so = chunk.get("start_offset", 0)
        eo = chunk.get("end_offset", 0)
        dur = chunk.get("duration", 0)
        lines.append(f"--- Chunk {i} | {so:.0f}s-{eo:.0f}s ({dur:.0f}s) | {source}")

        # 提取并展示场景
        vd = chunk.get("video_description", "")
        scenes = _extract_scenes_raw(vd)
        if scenes:
            for si, sc in enumerate(scenes):
                start = sc.get("start", 0)
                end = sc.get("end", 0)
                content = (sc.get("content", "") or "")[:200]
                speech = (sc.get("speech_summary", "") or "")[:120]
                keep = sc.get("keep", None)
                keep_mark = "✅" if keep is True else ("❌" if keep is False else "❓")
                lines.append(f"  [{keep_mark}] {start:.0f}s-{end:.0f}s {content}")
                if speech:
                    lines.append(f"       语音: {speech}")
        else:
            # fallback: 显示 transcript 摘要
            transcript = chunk.get("transcript", "")[:200]
            if transcript and transcript != "(音频分析无结果)":
                lines.append(f"  (无场景数据, ASR: {transcript[:120]}...)")
            else:
                lines.append(f"  (无场景数据)")

        # 检查是否超出长度限制
        current = "\n".join(lines)
        if len(current) > max_chars:
            lines.append(f"...(截断, 还有 {total - i - 1} 个chunk 未显示)")
            break

    return "\n".join(lines)


def _format_summary_pretty(data: dict) -> str:
    """把 summary.json 渲染成易读格式"""
    lines = [
        f"## 分析总览 ({data.get('total_materials', 0)} 个素材, "
        f"{data.get('total_chunks', 0)} 个chunk, "
        f"耗时 {data.get('elapsed', 0):.0f}s)",
        "",
        "素材列表:",
    ]
    for m in data.get("materials", []):
        tags_str = ", ".join(m.get("tags", [])) if m.get("tags") else "-"
        lines.append(
            f"  [{m.get('id','')}] {m.get('filename','')} "
            f"| {m.get('chunks',0)} chunk "
            f"| {m.get('duration',0):.0f}s "
            f"| 标签: {tags_str}"
        )

    # 报告摘要
    report = data.get("full_report", "")
    if report:
        lines.append("")
        lines.append("分析报告摘要:")
        # 取前 500 字符
        lines.append(f"  {report[:500].replace(chr(10), ' ')}")
        if len(report) > 500:
            lines.append("  ...(完整报告存于 summary.json 中)")

    lines.append("")
    lines.append('想细看某个素材: browse_memory(target="海贼王素材.mp4") 按素材名, 或 browse_memory(target="mat_海贼王素材_mp4") 按素材ID')
    return "\n".join(lines)


def _search_analysis_index(idx_dir: str, target: str) -> str | None:
    """当 browse_memory 找不到文件时, 去 analysis_index.json 里搜素材名/文件名."""
    analysis_path = os.path.join(idx_dir, "analysis_index.json")
    if not os.path.exists(analysis_path):
        return None

    try:
        with open(analysis_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    chunks = data.get("chunks", [])
    # 支持多种传参方式:
    #   1. mat_海贼王素材_mp4  → 去掉 mat_ 前缀, _mp4 当作 .mp4
    #   2. 海贼王素材.mp4      → 直接搜
    #   3. 海贼王素材          → 模糊搜
    target_clean = target
    if target_clean.startswith("mat_"):
        target_clean = target_clean[4:]
    if target_clean.endswith("_mp4"):
        target_clean = target_clean[:-4] + ".mp4"
    target_lower = target_clean.lower().replace("_", " ").replace(".mp4", "")

    matched = []
    seen_sources = set()
    for chunk in chunks:
        source = chunk.get("source", "")
        if target_lower in source.lower().replace(".mp4", "") or \
           target_lower in source.lower().replace("_", " "):
            key = (source, chunk.get("start_offset", 0))
            if key not in seen_sources:
                seen_sources.add(key)
                matched.append(chunk)

    if not matched:
        return None

    filtered = {
        "type": "analysis_index",
        "filename": target,
        "total_chunks": len(matched),
        "chunks": matched,
    }
    return _format_material_pretty(filtered, max_chars=10000)


@tool(
    name="browse_memory",
    description="""浏览记忆存储——直接看内容,不搜关键词.
两种模式:
  不传参数 → 粗看:显示素材总览(素材数/标签/时长)
  传素材文件名 → 细看:读取该素材的完整场景分析

支持新格式(分级索引)和旧格式(单文件 analysis_index.json).
100+素材场景下,粗看只看总览不卡,细看按需加载.""",
    phase="all",
    category="index",
    group="记忆查询",
    tags=["browse", "memory", "index", "search"],
)
def browse_memory(target: str = "") -> str:
    """浏览记忆存储——直接看内容,不做关键词搜索.

    Args:
        target: 为空时粗看(素材总览); 指定文件名时细看(读取完整内容)

    Returns:
        概览文本或完整文件内容(截断到10000字符)
    """
    work_dir = _find_work_dir()
    idx_dir = _index_dir(work_dir)
    if not os.path.exists(idx_dir):
        return "(记忆存储目录不存在,还没存过东西)"

    if target:
        # ── 细看模式 ──
        fpath = os.path.join(idx_dir, target)
        if os.path.exists(fpath):
            pass  # 直接读取
        else:
            # 尝试匹配 material_id 或视频文件名 → 去 analysis_index 里搜
            found = _search_analysis_index(idx_dir, target)
            if found is not None:
                return found
            files = [f for f in os.listdir(idx_dir) if f.endswith(".json")]
            avail = "\n".join(f"  - {f}" for f in files)
            return f"文件不存在: {target}\n可用文件:\n{avail}"
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)

            dtype = data.get("type", "")

            # 新格式: material_index -> 格式化渲染
            if dtype == "material_index":
                return _format_material_pretty(data)

            # 新格式: index_summary -> 总览渲染
            if dtype == "index_summary":
                return _format_summary_pretty(data)

            # 旧格式兼容: analysis_index
            if dtype == "analysis_index":
                return _format_material_pretty(data)

            # delegate 或其他: 返回原始 JSON
            content = json.dumps(data, ensure_ascii=False, indent=2)
            if len(content) > 10000:
                content = content[:10000] + f"\n\n...(截断,完整文件 {len(content)} 字符)"
            return content
        except Exception as e:
            return f"读取失败: {e}"

    # ── 粗看模式 ──
    summary_path = os.path.join(idx_dir, "summary.json")
    if os.path.exists(summary_path):
        # 新格式:读取 summary.json
        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return _format_summary_pretty(data)
        except Exception:
            pass

    # fallback:列出所有文件概览(旧格式)
    lines = ["## 记忆存储概览", ""]
    for fname in sorted(os.listdir(idx_dir)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(idx_dir, fname)
        size = os.path.getsize(fpath)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            dtype = data.get("type", "unknown")
            if dtype == "analysis_index":
                info = (f"分析索引 | {data.get('total_chunks', 0)} 个chunk, "
                       f"{data.get('total_materials', 0)} 个素材, "
                       f"耗时 {data.get('elapsed', 0):.0f}s")
            elif dtype == "material_index":
                info = (f"素材索引 | {data.get('filename','')} "
                       f"| {data.get('total_chunks',0)} chunk")
            elif dtype == "index_summary":
                info = (f"总览 | {data.get('total_materials',0)} 个素材, "
                       f"{data.get('total_chunks',0)} 个chunk")
            elif dtype == "delegate_entities":
                mission = data.get("mission", "")[:60]
                ent_count = len(data.get("entities", []))
                info = f"分身记录 | {ent_count} 条工具调用: {mission}"
            else:
                info = f"类型: {dtype}"
        except Exception:
            info = "读取失败"
        lines.append(f"  [{size/1024:.0f}KB] {fname}")
        lines.append(f"           {info}")

    lines.append("")
    lines.append('想细看某个素材: browse_memory(target="文件名.mp4") 或 browse_memory(target="素材文件名")')
    return "\n".join(lines)


@tool(
    name="search_memory",
    description="""在项目索引中搜索之前分身产出的分析结果.

⚠️ 注意:关键词搜索对中文效果差,建议优先用 browse_memory 直接浏览.
   browse_memory() → 查看素材总览
   browse_memory(target="文件名.mp4") 或 browse_memory(target="mat_ID名") → 细看某个素材

支持新格式(分级索引)和旧格式.""",
    phase="all",
    category="index",
    tags=["search", "index", "memory", "analyze"],
    group="记忆查询",
)
def search_memory(query: str = "") -> str:
    """在项目索引中搜索之前分析的产出内容.

    Args:
        query: 搜索关键词.为空时返回索引摘要.

    Returns:
        匹配的索引内容(JSON 字符串)
    """
    work_dir = _find_work_dir()
    if not query:
        return get_index_summary(work_dir)

    try:
        results = search_index(work_dir, query)
        if not results:
            summary = get_index_summary(work_dir)
            if "分析 chunk 数" in summary or "素材数" in summary:
                fallback = search_index(work_dir, "")
                if fallback:
                    return (
                        f"(关键词「{query}」无匹配,返回全量索引内容:)\n"
                        + json.dumps(fallback[:5], ensure_ascii=False, indent=2)
                    )
            return f"(未在索引中找到「{query}」的匹配,索引为空)"
        return json.dumps(results, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"(索引查询失败: {e})"


@tool(
    name="get_index_info",
    description="""查看当前项目索引概览.

返回:素材数、分析 chunk 数、素材列表(含标签).
在 batch_analyze 完成后用于确认索引已写入.
100+素材场景下快速查看全貌.""",
    phase="all",
    category="index",
    tags=["index", "info", "status"],
    group="记忆查询",
)
def get_index_info() -> str:
    """查看项目索引概览"""
    work_dir = _find_work_dir()
    return get_index_summary(work_dir)
