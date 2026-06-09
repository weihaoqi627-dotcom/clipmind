"""
预设管理工具 — 库存查询 + 入库
==============================
Agent 用 search_presets 查库存,用 save_preset 存生成的成果.
"""
import json
from typing import Optional

from director.registry import tool


@tool(
    name="search_presets",
    description="搜索预设库:在所有效果类型(花字/转场/入场/出场/视觉特效/叠层等)中查找已有预设.匹配就复用,没匹配到就自己生成.",
    phase="plan",
    category="effect",
    tags=["preset", "search", "library"],
    group="花字与动画(效果层)",
)
def search_presets(
    category: str = "",
    query: str = "",
    tags: str = "",
    verified_only: bool = False,
) -> str:
    """
    搜索预设库,找到匹配的已有效果预设.

    先查库存,有就复用,没有再生成——这是核心工作流.

    Args:
        category: 预设分类.可选值:
            flower_text(花字),transition(转场),entrance(入场),exit(出场),
            visual_effect(视觉特效),overlay(叠层),camera_move(伪运镜),
            svg_animation(SVG动画),lower_third(下沿条),title_card(标题卡),
            data_viz(数据可视化),gsap_composition(GSAP通用动画)
            留空=搜索所有分类
        query: 自然语言描述你要找什么效果.如"霓虹发光标题","炫酷转场","弹跳入场"
        tags: 标签过滤,逗号分隔.如 "highlight,energetic"
        verified_only: 只看验证过的预设

    Returns:
        JSON 格式的匹配预设列表(含 id/name/description/tags/category/verified).
        如果匹配到就用 preset_id 调用对应的效果工具.
        如果没匹配到,自己用 generate_gsap_html / apply_flower_text 生成新效果.
    """
    from preset_library.manager import search as ps, list_categories

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    results = ps(
        category=category or "",
        query=query,
        tags=tag_list,
        verified_only=verified_only,
    )

    if not results:
        cats = list_categories()
        cat_names = "\n".join(f"  - {k}: {v}" for k, v in cats.items())
        return (
            f"(未找到匹配预设)\n\n"
            f"搜索: category={category or '全部'}, query='{query}', tags={tags or '无'}\n\n"
            f"可用分类:\n{cat_names}\n\n"
            f"-> 没有匹配的预设,请用 generate_gsap_html() 或 apply_flower_text(raw_html=...) 自己生成新效果.\n"
            f"-> 生成完成后用 save_preset() 存入预设库."
        )

    # 格式化输出
    lines = [f"找到 {len(results)} 个匹配预设:"]
    for r in results:
        verified = "✓" if r.get("verified") else "✗"
        lines.append(
            f"  [{verified}] {r['id']} | {r['category']} | {r['name']}\n"
            f"        {r['description']}\n"
            f"        标签: {', '.join(r.get('tags', []))}"
        )
    return "\n\n".join(lines)


@tool(
    name="save_preset",
    description="将你生成的效果保存到预设库中,下次遇到类似需求可以直接复用.适用于所有效果类型.",
    phase="edit",
    category="effect",
    tags=["preset", "save", "library"],
    group="花字与动画(效果层)",
)
def save_preset(
    category: str,
    name: str,
    description: str,
    tags: str,
    html_content: str = "",
    gsap_params: str = "",
    hf_block: str = "",
    preset_type: str = "raw_html",
    params: str = "",
) -> str:
    """
    将你新生成的效果保存为预设,下次可以直接复用.

    **重要:每次你用 generate_gsap_html 或 apply_flower_text(raw_html=...) 生成了新效果,
    都应该调用 save_preset 入库.这样预设库会越来越丰富,以后就不用重复生成了.**

    Args:
        category: 预设分类(必填).必须是以下之一:
            flower_text, transition, entrance, exit, visual_effect,
            overlay, camera_move, svg_animation, lower_third, title_card,
            data_viz, gsap_composition
        name: 效果名称(必填).简短描述,如"霓虹发光标题","弹性弹跳入场"
        description: 效果描述(必填).说明效果特点,适合什么场景
        tags: 标签(必填).逗号分隔,如 "highlight,energetic,colorful"
        html_content: [raw_html类型] 完整的 HTML 模板.用 {text} 占位文字内容.
        gsap_params: [gsap_params类型] GSAP 动画参数 JSON.
                     如: {"animations": [{"target":".main","props":{"opacity":0,"y":30},"duration":0.5,"ease":"power2.out"}]}
        hf_block: [hf_block类型] HF block 名称和参数 JSON.
                 如: {"block_name":"whip-pan","params":{"direction":"left"}}
        preset_type: 模板类型.raw_html(含HTML的完整模板)/ gsap_params(仅GSAP参数)/ hf_block(HF block引用)
        params: 可替换参数列表,逗号分隔.如 "text,font_family,duration"

    Returns:
        保存结果,含 preset_id
    """
    from preset_library.manager import save as ps_save, list_categories

    # 解析参数
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    param_list = [p.strip() for p in params.split(",") if p.strip()]

    # 构建 template
    if preset_type == "raw_html":
        if not html_content:
            return "❌ raw_html 类型需要提供 html_content 参数"
        template = {"html": html_content}
    elif preset_type == "gsap_params":
        if not gsap_params:
            return "❌ gsap_params 类型需要提供 gsap_params 参数"
        try:
            template = {"gsap": json.loads(gsap_params)}
        except json.JSONDecodeError:
            return "❌ gsap_params 不是有效的 JSON"
    elif preset_type == "hf_block":
        if not hf_block:
            return "❌ hf_block 类型需要提供 hf_block 参数"
        try:
            template = {"hf_block": json.loads(hf_block)}
        except json.JSONDecodeError:
            return "❌ hf_block 不是有效的 JSON"
    else:
        return f"❌ 无效 preset_type: {preset_type}"

    try:
        preset_id = ps_save(
            category=category,
            name=name,
            description=description,
            tags=tag_list,
            template=template,
            preset_type=preset_type,
            params=param_list,
        )
        return (
            f"✅ 预设已保存: {preset_id}\n"
            f"   分类: {category} | 名称: {name}\n"
            f"   标签: {', '.join(tag_list)}\n"
            f"   下次可以用 search_presets(category='{category}', query='{name}') 找到它"
        )
    except ValueError as e:
        cats = list_categories()
        cat_names = ", ".join(cats.keys())
        return f"❌ {e}\n可用分类: {cat_names}"
