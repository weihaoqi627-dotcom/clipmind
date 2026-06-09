"""
Tool Catalog — 工具目录(给 Director 看的菜单)
=========================================
自动从 @tool 注册表的 group 标记生成,不再手动维护.

加新工具只需在 @tool 装饰器中写 group="分组名",
工具自动出现在对应分组中.
"""


def _build_catalog():
    """从 registry 的 group 标记自动构建工具目录"""
    try:
        from director.registry import get_group_map, get_all_tools
        group_map = get_group_map()
        all_tools = {t["name"]: t for t in get_all_tools()}
        catalog = {}
        for group_name, tool_names in group_map.items():
            catalog[group_name] = {}
            for tname in tool_names:
                tool = all_tools.get(tname, {})
                desc = (tool.get("description", "") or tname)[:80]
                catalog[group_name][tname] = desc.replace("\n", " ")
        return catalog
    except Exception:
        return {}


# 向后兼容:外部代码可能 from director.tool_catalog import TOOL_CATALOG
# 它是一个 dict,首次访问时自动构建
_TOOL_CATALOG_CACHE = None


def _get_catalog():
    global _TOOL_CATALOG_CACHE
    if _TOOL_CATALOG_CACHE is None:
        _TOOL_CATALOG_CACHE = _build_catalog()
    return _TOOL_CATALOG_CACHE


class _CatalogDict(dict):
    """延迟加载的目录字典,兼容旧的 TOOL_CATALOG.items()/.get() 用法"""
    def __init__(self):
        super().__init__()

    def __getitem__(self, key):
        d = _get_catalog()
        if key in d:
            return d[key]
        raise KeyError(key)

    def __contains__(self, key):
        return key in _get_catalog()

    def items(self):
        return _get_catalog().items()

    def keys(self):
        return _get_catalog().keys()

    def values(self):
        return _get_catalog().values()

    def get(self, key, default=None):
        return _get_catalog().get(key, default)

    def __len__(self):
        return len(_get_catalog())

    def __iter__(self):
        return iter(_get_catalog())


TOOL_CATALOG = _CatalogDict()


# ─── 公开函数 ──────────────────────────────────────────────


def get_catalog_text() -> str:
    """生成给 Director prompt 注入的工具目录文本"""
    from director.registry import get_group_map

    group_map = get_group_map()
    lines = ["## 工具目录(按功能分组)", ""]
    for group_name, tool_names in sorted(group_map.items()):
        names_str = ", ".join(tool_names)
        lines.append(f"  [{group_name}] {names_str}")
    lines.append("")
    lines.append("## 工具使用注意事项")
    lines.append('  - **draft_id 统一用 "main"**: cut_segment 传 draft_id="main",所有操作都传同一个')
    lines.append("  - **预处理**: 后台自动压缩原始素材到720p(compressed_originals/),不自动切分")
    lines.append("  - **batch_analyze 需要先有片段**: 如果 state.segments 为空,先 split 再分析")
    lines.append('  - cut_segment: 传 draft_id="main" 让裁切自动进入草稿')
    lines.append('  - discard_segment(seg_id): seg_id 是字符串,如 "seg_008"')
    lines.append("  - mark_discard/clip_id: 整数 ID,不是 seg_id,不要搞混!")
    lines.append("  - screen_clip: 只在有浏览器环境的 Electron 可用,CLI 不可用")
    lines.append('  - reorder_draft_segments: 需要草稿存在(draft_id="main")')
    lines.append("")
    lines.append('用 `dispatch_clone(mission="任务描述", tool_groups=["组名1", "组名2"])` 派分身去做事。')
    lines.append("分身自动获得指定分组的工具（每组 8-15 个），不需要的组不加载，避免工具太多找不到。")
    lines.append("效果层(花字/转场): 用 dispatch_clone 派分身, tool_groups 给 \"花字与动画(效果层)\"。")
    return "\n".join(lines)


def get_group_names() -> str:
    """返回所有分组的名字(逗号分隔)"""
    from director.registry import get_group_names as _get_group_names
    return ", ".join(_get_group_names())


def get_tools_by_group(group_name: str) -> list[str]:
    """获取指定分组的所有工具名列表"""
    from director.registry import get_tools_by_group
    return get_tools_by_group(group_name)
