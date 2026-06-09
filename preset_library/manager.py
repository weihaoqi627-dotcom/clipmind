"""
预设库管理器
============
统一管理所有效果预设:花字,转场,入场,出场,视觉特效,叠层等.
Agent 先查库存,有就复用;没有再生成,生成完入库.
"""
import json
import os
import time
from pathlib import Path
from typing import Optional

_PROJECT_DIR = Path(__file__).parent.parent
_PRESETS_PATH = Path(__file__).parent / "presets.json"

# ─── 预设分类定义 ───────────────────────────────────────────

CATEGORIES = {
    "flower_text":    "花字/文字特效",
    "transition":     "转场效果",
    "entrance":       "入场动画",
    "exit":           "出场动画",
    "visual_effect":  "视觉特效/画面处理",
    "overlay":        "叠层/UI元素",
    "camera_move":    "伪运镜",
    "svg_animation":  "SVG/Logo动画",
    "lower_third":    "下沿条/信息条",
    "title_card":     "标题卡/片头",
    "data_viz":       "数据可视化",
    "gsap_composition": "GSAP 通用动画组合",
}


# ─── 加载/保存 ──────────────────────────────────────────────

def _load() -> dict:
    """加载预设库 JSON"""
    if not _PRESETS_PATH.exists():
        return {"version": "1.0", "categories": CATEGORIES, "presets": []}
    try:
        with open(_PRESETS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"version": "1.0", "categories": CATEGORIES, "presets": []}


def _save(data: dict):
    """保存预设库 JSON"""
    _PRESETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_PRESETS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


_cache = None
_cache_time = 0.0


def _get_cache() -> dict:
    """获取缓存(5秒更新)"""
    global _cache, _cache_time
    now = time.time()
    if _cache is None or (now - _cache_time) > 5:
        _cache = _load()
        _cache_time = now
    return _cache


def _invalidate_cache():
    """使缓存失效"""
    global _cache
    _cache = None


# ─── 搜索 ───────────────────────────────────────────────────

def search(
    category: str = "",
    query: str = "",
    tags: Optional[list] = None,
    verified_only: bool = False,
    limit: int = 20,
) -> list[dict]:
    """
    搜索预设库.

    Args:
        category: 分类过滤(空=所有分类)
        query: 关键词搜索(匹配 name + description + tags)
        tags: 标签过滤
        verified_only: 只返回已验证
        limit: 最多返回数量

    Returns:
        预设摘要列表(不含完整 template,含 id/name/description/tags/category/verified)
    """
    data = _get_cache()
    presets = data.get("presets", [])

    results = []
    for p in presets:
        # 分类过滤
        if category and p.get("category", "") != category:
            continue
        # 验证过滤
        if verified_only and not p.get("verified", False):
            continue
        # 标签过滤
        if tags:
            p_tags = p.get("tags", [])
            if not any(t.lower() in [pt.lower() for pt in p_tags] for t in tags):
                continue
        # 关键词搜索
        if query:
            q = query.lower()
            name = p.get("name", "").lower()
            desc = p.get("description", "").lower()
            p_tags = " ".join(p.get("tags", [])).lower()
            if q not in name and q not in desc and q not in p_tags:
                continue
        # 返回摘要(不含完整模板)
        results.append({
            "id": p.get("id", ""),
            "category": p.get("category", ""),
            "name": p.get("name", ""),
            "description": p.get("description", ""),
            "tags": p.get("tags", []),
            "type": p.get("type", ""),
            "verified": p.get("verified", False),
            "params": p.get("params", []),
            "created_at": p.get("created_at", ""),
        })

    return results[:limit]


# ─── 获取单个预设 ───────────────────────────────────────────

def get(preset_id: str) -> Optional[dict]:
    """
    获取单个预设的完整信息(含 template).

    Args:
        preset_id: 预设 ID

    Returns:
        预设完整字典,或 None
    """
    data = _get_cache()
    for p in data.get("presets", []):
        if p.get("id") == preset_id:
            return p
    return None


# ─── 保存 ───────────────────────────────────────────────────

def save(
    category: str,
    name: str,
    description: str,
    tags: list,
    template: dict,
    preset_type: str = "raw_html",
    params: Optional[list] = None,
    preset_id: str = "",
) -> str:
    """
    保存新预设到库.

    Args:
        category: 分类(必须为 CATEGORIES 之一)
        name: 名称
        description: 描述
        tags: 标签列表
        template: 模板内容(根据 type 不同结构不同)
                  - raw_html: {"html": "..."}
                  - gsap_params: {"animations": {...}, "easing": "...", ...}
                  - hf_block: {"block_name": "...", "params": {...}}
        preset_type: 模板类型 (raw_html / gsap_params / hf_block / composition)
        params: 可替换参数列表,如 ["text", "duration", "font_family"]
        preset_id: 指定 ID(留空自动生成)

    Returns:
        保存的 preset_id
    """
    if category not in CATEGORIES:
        available = ", ".join(CATEGORIES.keys())
        raise ValueError(f"无效分类 '{category}'.可用: {available}")

    data = _load()

    if not preset_id:
        # 自动生成 ID
        existing_ids = {p["id"] for p in data.get("presets", [])}
        base = f"{category}_{name.lower().replace(' ', '_').replace('/', '_')[:30]}"
        pid = base
        n = 1
        while pid in existing_ids:
            pid = f"{base}_{n}"
            n += 1
        preset_id = pid

    preset = {
        "id": preset_id,
        "category": category,
        "name": name,
        "description": description,
        "tags": tags or [],
        "type": preset_type,
        "verified": False,
        "params": params or [],
        "template": template,
        "created_at": time.strftime("%Y-%m-%d"),
        "from_generation": True,
    }

    data.setdefault("presets", []).append(preset)
    _save(data)
    _invalidate_cache()

    return preset_id


# ─── 更新验证状态 ───────────────────────────────────────────

def verify(preset_id: str, verified: bool = True):
    """标记预设为已验证(或取消验证)"""
    data = _load()
    for p in data.get("presets", []):
        if p.get("id") == preset_id:
            p["verified"] = verified
            _save(data)
            _invalidate_cache()
            return True
    return False


# ─── 分类列表 ───────────────────────────────────────────────

def list_categories() -> dict:
    """返回所有分类及其说明"""
    return dict(CATEGORIES)


# ─── 统计 ───────────────────────────────────────────────────

def stats() -> dict:
    """返回预设库统计"""
    data = _get_cache()
    presets = data.get("presets", [])
    by_cat = {}
    verified = 0
    for p in presets:
        cat = p.get("category", "unknown")
        by_cat[cat] = by_cat.get(cat, 0) + 1
        if p.get("verified"):
            verified += 1
    return {
        "total": len(presets),
        "verified": verified,
        "by_category": by_cat,
        "categories_available": list(CATEGORIES.keys()),
    }
