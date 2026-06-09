"""
Tool Registry — 工具注册与发现
================================
替代旧的硬编码 TOOLS = [...] 模式,用 @tool 装饰器自动注册.

用法:
    from director.registry import tool, get_tools_by_phase

    @tool(
        name="detect_scenes",
        phase="analyze",       # analyze / plan / edit / render / all
        category="scene",
    group="画面与场景",
    )
    def detect_scenes(video_path: str, ...):
        \"\"\"检测视频的所有镜头切换点.\"\"\"
        ...

    # 获取某阶段的所有工具
    analyze_tools = get_tools_by_phase("analyze")
    edit_tools = get_tools_by_phase("edit")
"""
import inspect
from typing import Callable, Optional


# ─── 全局注册表 ────────────────────────────────────────────

_tool_registry: dict[str, dict] = {}  # name -> tool_meta


# ─── 装饰器 ────────────────────────────────────────────────

def tool(
    name: Optional[str] = None,
    *,
    description: str = "",
    phase: str = "all",        # analyze / plan / edit / render / all
    category: str = "",        # scene / audio / color / effect / ...
    group: str | list[str] = "",  # 工具分组名,如"画面与场景" — 取代 TOOL_CATALOG 手动维护
    tags: Optional[list[str]] = None,
    examples: Optional[list[dict]] = None,
    parameters: Optional[dict] = None,
):
    """
    注册一个工具.

    Args:
        name: 工具名,默认取函数名
        description: 工具描述(AI 看到的).包含:
            - 作用(1 句)
            - 什么时候用
            - 什么时候千万别用(最重要!)
            - 副作用(写文件?改数据库?)
        phase: 暴露阶段.analyze -> plan -> edit -> render -> all
        group: 工具分组名.如 "画面与场景".多个分组的工具传列表.
               Director 通过 dispatch_clone(tool_groups=...) 按组分配.
        category: 分类名
        tags: 标签
        examples: 参数示例列表 [{"args": {...}, "result": "..."}, ...]
        parameters: JSON Schema 参数定义(默认从函数签名推导)
    """
    tags = tags or []

    def decorator(fn):
        nonlocal name, description
        fn_name = name or fn.__name__

        # 从函数签名推导 parameters
        derived_params = _derive_parameters(fn) if parameters is None else parameters

        _tool_registry[fn_name] = {
            "name": fn_name,
            "description": description or fn.__doc__ or "",
            "fn": fn,
            "phase": phase,
            "group": group if isinstance(group, list) else ([group] if group else []),
            "category": category,
            "tags": tags,
            "examples": examples or [],
            "parameters": derived_params,
        }
        return fn  # 返回原函数,不改变调用方式

    return decorator


# ─── Registry 访问 ──────────────────────────────────────────

def get_all_tools() -> list[dict]:
    """返回所有已注册工具"""
    return list(_tool_registry.values())


def get_tools_by_phase(*phases: str) -> list[dict]:
    """按阶段筛选工具.不传参数返回所有."""
    if not phases:
        return get_all_tools()
    phases_set = set(phases)
    return [t for t in _tool_registry.values()
            if t["phase"] in phases_set or t["phase"] == "all"]


def get_tools_by_category(category: str) -> list[dict]:
    """按分类筛选"""
    return [t for t in _tool_registry.values() if t["category"] == category]


def get_tools_by_tags(*tags: str) -> list[dict]:
    """按标签筛选(AND 逻辑)"""
    tags_set = set(tags)
    return [t for t in _tool_registry.values()
            if tags_set.issubset(set(t["tags"]))]


def get_tool(name: str) -> Optional[dict]:
    """按名称查找工具"""
    return _tool_registry.get(name)


def get_tools_by_group(group_name: str) -> list[str]:
    """按分组名返回该组所有工具名列表"""
    return [t["name"] for t in _tool_registry.values()
            if group_name in t.get("group", [])]


def get_group_map() -> dict[str, list[str]]:
    """返回 group -> [tool_names] 映射,用于自动生成 TOOL_CATALOG"""
    groups = {}
    for t in _tool_registry.values():
        for g in t.get("group", []):
            groups.setdefault(g, []).append(t["name"])
    return groups


def get_group_names() -> list[str]:
    """返回所有分组名列表(含效果层)"""
    return sorted(get_group_map().keys())


def get_phase_map() -> dict[str, list[str]]:
    """按阶段分组返回工具名列表"""
    phases = {}
    for t in _tool_registry.values():
        p = t["phase"]
        phases.setdefault(p, []).append(t["name"])
    return phases


def search_tools(query: str) -> list[dict]:
    """简单关键词搜索工具(替代 embedding 检索的轻量版)"""
    q = query.lower()
    results = []
    for t in _tool_registry.values():
        score = 0
        if q in t["name"].lower():
            score += 3
        if q in t["description"].lower():
            score += 2
        if q in t["category"].lower():
            score += 1
        for tag in t["tags"]:
            if q in tag.lower():
                score += 1
        if score > 0:
            results.append((score, t))
    results.sort(key=lambda x: -x[0])
    return [t for _, t in results]


def to_openai_tools(tool_dicts: list[dict]) -> list[dict]:
    """将注册表工具转为 OpenAI/DashScope 工具格式"""
    result = []
    for t in tool_dicts:
        result.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            }
        })
    return result


def get_phase_summary() -> str:
    """生成阶段工具摘要(给 system prompt 用)"""
    lines = []
    for phase in ("analyze", "plan", "edit", "render"):
        tools = get_tools_by_phase(phase)
        if tools:
            names = ",".join(t["name"] for t in tools)
            lines.append(f"  {phase}: {names}")
    return "\n".join(lines)


def clear_registry():
    """清空注册表(仅供测试用)"""
    _tool_registry.clear()


# ─── 辅助函数 ───────────────────────────────────────────────

def _derive_parameters(fn) -> dict:
    """从函数签名推导 JSON Schema 参数定义(简单版)"""
    sig = inspect.signature(fn)
    properties = {}
    required = []
    type_map = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
    }

    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue
        ptype = "string"  # 默认
        if param.annotation != inspect.Parameter.empty:
            ptype = type_map.get(param.annotation, "string")

        prop = {"type": ptype, "description": ""}

        if param.default != inspect.Parameter.empty:
            prop["default"] = param.default
        else:
            required.append(name)

        properties[name] = prop

    return {"type": "object", "properties": properties, "required": required}


def _enable_auto_registration():
    """
    启用自动注册模式:监听模块导入,自动收集 @tool 装饰的工具.
    无需手动调用,在 entry.py import 各工具模块时自动生效.
    """
    # 目前 @tool 装饰器在导入时自动注册,无需额外操作


# 导出常用函数
__all__ = [
    "tool", "get_all_tools", "get_tools_by_phase", "get_tools_by_category",
    "get_tools_by_tags", "get_tools_by_group", "get_group_map", "get_group_names",
    "get_tool", "get_phase_map", "search_tools",
    "to_openai_tools", "get_phase_summary", "clear_registry",
]
