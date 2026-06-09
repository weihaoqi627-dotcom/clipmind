"""
动效工具 — 特效设计阶段
========================
AI 通过反复调用这些工具来设计文字特效,花字模板,叠加动画.
"""
import json, os, re, subprocess, time
from pathlib import Path
from typing import Optional

from director.registry import tool

_PROJECT_DIR = Path(__file__).parent.parent.parent

# ─── 花字库(向后兼容,从统一预设库读取)───────────────────

_FLOWER_TEXT_CACHE = None

def _load_flower_texts() -> list:
    """加载花字库(从统一预设库筛选 flower_text 分类)"""
    global _FLOWER_TEXT_CACHE
    if _FLOWER_TEXT_CACHE is not None:
        return _FLOWER_TEXT_CACHE
    try:
        from preset_library.manager import search as preset_search
        results = preset_search(category="flower_text")
        # 转换为旧格式兼容
        converted = []
        for r in results:
            full = preset_search.__self__ if hasattr(preset_search, '__self__') else None
            # 重新获取完整信息
            from preset_library.manager import get as preset_get
            p = preset_get(r["id"])
            if not p:
                continue
            template = p.get("template", {})
            entry = {
                "id": p["id"],
                "name": p["name"],
                "description": p["description"],
                "type": p["type"],
                "verified": p.get("verified", False),
                "tags": {
                    "video_type": ["all"],
                    "stage": [t for t in p.get("tags", []) if t in ("opening","transition","highlight","caption","ending")]
                },
                "fonts": {"{font_family_primary}": {"placeholder": "{font_family_primary}", "font": "system"}},
                "raw_html_template": template.get("html", "") if isinstance(template, dict) else "",
            }
            converted.append(entry)
        _FLOWER_TEXT_CACHE = converted
    except Exception:
        _FLOWER_TEXT_CACHE = []
    return _FLOWER_TEXT_CACHE


def _fix_flower_text_title(title: str) -> str:
    """修复花字标题中的乱码(latin-1 -> utf-8)"""
    if not title:
        return title
    try:
        fixed = title.encode("latin-1").decode("utf-8")
        if fixed and len(fixed) > 1:
            return fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return title


@tool(
    name="list_flower_texts",
    description="按视频类型和场景阶段列出可用的花字模板,返回 ID,名称,描述,类型,字体占位列表",
    phase="plan",
    category="effect",
    tags=["flower_text", "template", "list"],
    group="花字与动画(效果层)",
)
def list_flower_texts(video_type: str = "", stage: str = "") -> str:
    """
    按视频类型和场景阶段列出可用的花字模板.

    Args:
        video_type: 视频类型(all/anime/vlog/talk/game/movie/commercial),留空列出所有
        stage: 场景阶段(opening/transition/highlight/caption/ending),留空列出所有

    Returns:
        JSON 格式的花字列表
    """
    fts = _load_flower_texts()
    if not fts:
        return "(无花字库)"

    if video_type or stage:
        matched = []
        for ft in fts:
            tags = ft.get("tags", {})
            vt_list = tags.get("video_type", ["all"])
            st_list = tags.get("stage", [])
            vt_ok = not video_type or (video_type in vt_list or "all" in vt_list)
            st_ok = not stage or stage in st_list
            if vt_ok and st_ok:
                matched.append(ft)
        fts = matched

    if not fts:
        return "(无匹配花字)"

    result = []
    for ft in fts:
        entry = {
            "id": ft.get("id", ""),
            "name": _fix_flower_text_title(ft.get("name", "")),
            "description": _fix_flower_text_title(ft.get("description", "")),
            "type": ft.get("type", ""),
            "verified": ft.get("verified", False),
            "tags": ft.get("tags", {}),
            "fonts": list(ft.get("fonts", {}).keys()),
        }
        result.append(entry)

    return json.dumps(result, ensure_ascii=False, indent=2)


@tool(
    name="apply_flower_text",
    description="将花字渲染并叠加到视频上.接受 flower_id(预设),css_style/composition(即时参数),或 raw_html(AI自写HTML)",
    phase="edit",
    category="effect",
    tags=["flower_text", "render", "overlay"],
    group="花字与动画(效果层)",
)
def apply_flower_text(
    video_path: str,
    flower_id: str = "",
    text: str = "",
    position: str = "center",
    start_time: float = 0.0,
    duration: float = 5.0,
    font_size: int = 60,
    output_path: str = "",
    # ── 字体替换(预设模式下可选)──
    font_path: str = "",
    # ── 即时生成参数(flower_id 为空时使用)──
    css_style: Optional[dict] = None,
    composition: Optional[dict] = None,
    gsap_style: Optional[dict] = None,
    raw_html: Optional[str] = None,
    draft_id: str = "",
) -> str:
    """
    对视频应用花字效果.三种模式:
    1. 预设模式:传 flower_id 从库中查找,可选 font_path 替换字体
    2. 即时生成模式:传 css_style/composition 参数
    3. AI 自由模式:传 raw_html,AI 自己写完整 HTML,不受模板限制

    Args:
        video_path: 输入视频路径
        flower_id: 花字ID(从 list_flower_texts 获取),留空则用其他参数即时生成
        text: 要显示的文字内容
        position: 位置(top/center/bottom/top_left/top_right/bottom_left/bottom_right),默认center
        start_time: 显示起始时间(秒)
        duration: 显示持续时长(秒),默认5秒
        font_size: 字号(像素),默认60
        output_path: 输出路径(可选)
        font_path: 字体替换(如"fonts/得意黑_xxx.ttf"),留空用模板默认字体
        css_style: [即时生成] CSS花字样式参数
        composition: [即时生成] Composition花字模板参数
        gsap_style: [可选] GSAP动画参数
        raw_html: [AI自由模式] AI自己写的完整花字HTML.包含{text}占位符.
                  这是最强的模式——AI可以生成任何它想得到的效果.
                  HTML必须包含标准HF协议:
                  - 根元素 data-composition-id="main" data-duration="X"
                  - window.__timelines["main"] = gsap.timeline({paused:true})
                  - window.__hf = {duration: X, seek: function(t){...}}
                  - 背景透明,1280x720

    Returns:
        结果信息
    """
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"
    if not text:
        return "请提供 text 参数"

    # ── 确定花字参数来源 ──
    if raw_html:
        # 模式3:AI 自由模式 — 自己写 HTML
        ft_type = "raw_html"
        ft = {"raw_html": raw_html}
        gsap = gsap_style or {}
    elif flower_id:
        # 模式1:从库中查找预设
        fts = _load_flower_texts()
        ft = None
        for f in fts:
            if str(f.get("id", "")) == str(flower_id):
                ft = f
                break
        if not ft:
            return f"未找到花字: {flower_id}"
        ft_type = ft.get("type", "css")
        if gsap_style is None:
            gsap = ft.get("gsap_style", {})
        else:
            gsap = gsap_style
    elif css_style or composition:
        # 模式2:即时生成
        if composition:
            ft_type = "composition"
            ft = {"composition": composition}
        else:
            ft_type = "css"
            ft = {"css_style": css_style}
        gsap = gsap_style or {}
    else:
        return "请提供 flower_id,css_style/composition 或 raw_html 参数"

    # ── HF 渲染花字为带 alpha 的 MOV ──
    from hf_engine.templates.flower_text import generate_flower_text_html
    from hf_engine.hf_cli import render_composition

    if ft_type == "raw_html":
        # 模式3:AI 自由模式 — 直接渲染 AI 写的 HTML
        html = generate_flower_text_html(
            text=text,
            raw_html=ft.get("raw_html", ""),
            gsap_style=gsap,
            width=1280,
            height=720,
            font_size=font_size,
            hold_duration=duration,
        )
    elif ft_type == "composition":
        html = generate_flower_text_html(
            text=text,
            composition=ft.get("composition", {}),
            gsap_style=gsap,
            width=1280,
            height=720,
            font_size=font_size,
            hold_duration=duration,
        )
    else:
        css = ft.get("css_style", {})
        html = generate_flower_text_html(
            text=text,
            css_style=css,
            gsap_style=gsap,
            width=1280,
            height=720,
            font_size=font_size,
            hold_duration=duration,
        )

    # ── 字体嵌入(预设模式专用)──
    if font_path and (ft_type == "composition" or ft_type == "raw_html"):
        from hf_engine.templates.flower_text import embed_font
        html = embed_font(html, font_path)

    import hashlib
    cache_key = hashlib.md5(
        f"flower_{flower_id}_{text}_{font_size}_{font_path}".encode()
    ).hexdigest()[:12]

    # 渲染花字为 ProRes MOV(带 alpha 通道)
    mov_path = render_composition(
        html_content=html,
        cache_key=cache_key,
        width=1280,
        height=720,
        fps=24,
        format="mov",
    )

    if not mov_path or not os.path.exists(mov_path):
        return "❌ HF 花字渲染失败"

    # ── 合成到视频上 ──
    # 检测原视频分辨率
    import re as _re
    r = subprocess.run(["ffmpeg", "-i", video_path], capture_output=True, timeout=30, check=False)
    info = (r.stdout + r.stderr).decode("utf-8", errors="replace")
    res_m = _re.search(r",\s*(\d{3,})x(\d{3,})", info)
    if res_m:
        vw, vh = int(res_m.group(1)), int(res_m.group(2))
    else:
        vw, vh = 1280, 720

    # 位置映射:计算 overlay 坐标
    pos_x = {"center": "(W-w)/2", "top": "(W-w)/2", "bottom": "(W-w)/2",
             "top_left": "0", "top_right": "W-w", "bottom_left": "0", "bottom_right": "W-w"}
    pos_y = {"center": "(H-h)/2", "top": "0", "bottom": "H-h",
             "top_left": "0", "top_right": "0", "bottom_left": "H-h", "bottom_right": "H-h"}
    ox = pos_x.get(position, "(W-w)/2")
    oy = pos_y.get(position, "(H-h)/2")

    if not output_path:
        tag = hashlib.md5(f"{video_path}_{flower_id}_{text}".encode()).hexdigest()[:8]
        output_path = os.path.join(_PROJECT_DIR, "output", f"flower_{tag}.mp4")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # overlay: 花字 MOV(有透明通道)叠加到原视频
    # 注意:enable 表达式的逗号必须转义,否则 ffmpeg 会当作滤镜链分隔符
    enable_expr = f"between(t\\,{start_time}\\,{start_time + duration})"
    overlay_filter = (
        f"[0:v]scale={vw}:{vh}[bg];"
        f"[1:v]scale={vw}:{vh}[fg];"
        f"[bg][fg]overlay={ox}:{oy}:enable={enable_expr}"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", mov_path,
        "-filter_complex", overlay_filter,
        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-c:a", "copy",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, timeout=600, check=False)

    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        size_b = os.path.getsize(output_path)
        name = _fix_flower_text_title(ft.get("name", ""))
        if draft_id:
            from director.draft import Draft
            d = Draft(draft_id)
            if d.load():
                d.add_flower_text(text=text, flower_id=flower_id, time=start_time, duration=duration)
                d.save("花字添加完成")
        if size_b < 1024 * 1024:
            return f"✅ 花字应用完成「{name}」: {output_path} ({size_b / 1024:.1f}KB)"
        return f"✅ 花字应用完成「{name}」: {output_path} ({size_b / (1024 * 1024):.1f}MB)"

    err = result.stderr.decode("utf-8", errors="replace")[-500:]
    return f"❌ 花字合成失败: {err}"


# ─── 辅助函数 ──────────────────────────────────────────────

def _escape_dt_text(text: str) -> str:
    """转义 drawtext text 参数中的特殊字符"""
    text = text.replace("'", "'")
    text = text.replace(":", "\\:")
    text = text.replace(";", "")
    return text


def _escape_dt_expr(expr: str) -> str:
    return expr.replace(",", "\\,")


def _check_nvenc() -> bool:
    """检查 NVENC 编码器是否可用"""
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True, timeout=10
    )
    return "h264_nvenc" in (r.stdout + r.stderr).decode("utf-8", errors="replace")


# ─── 原有函数 ──────────────────────────────────────────────


@tool(
    name="list_available_effects",
    description="列出所有可用的文字特效",
    phase="plan",
    category="effect",
    tags=["effect", "list"],
    group="花字与动画(效果层)",
)
def list_available_effects() -> str:
    """列出所有可用的文字特效(本地库已清空,新生成的特效将存储于此)."""
    return json.dumps([], ensure_ascii=False, indent=2)


@tool(
    name="describe_effect",
    description="查看某个特效的详细信息",
    phase="plan",
    category="effect",
    tags=["effect", "detail"],
    group="花字与动画(效果层)",
)
def describe_effect(effect_id: str) -> str:
    """
    查看某个特效的详细信息.
    从 list_available_effects 获取 effect_id.
    本地库已清空,新生成的特效可通过此工具查看.
    """
    return "本地特效库已清空,暂无特效信息"


@tool(
    name="list_available_fonts",
    description="列出可用字体,返回名称,类型(衬线/无衬线),大小",
    phase="plan",
    category="effect",
    tags=["font", "list"],
    group="花字与动画(效果层)",
)
def list_available_fonts(chinese_only: bool = True) -> str:
    """列出可用的字体库(按类型分类).

    Args:
        chinese_only: 是否只显示中文字体,默认True

    Returns:
        字体列表信息
    """
    from flower_text_manager import list_available_fonts as _list_fonts
    fonts = _list_fonts(chinese_only=chinese_only)
    if not fonts:
        return "(无字体库)"

    # 按类型分组
    sans = [f for f in fonts if f["type"] == "sans"]
    serif = [f for f in fonts if f["type"] == "serif"]

    lines = [f"共 {len(fonts)} 个字体(仅显示名称)"]
    lines.append(f"\n无衬线 ({len(sans)}):")
    for f in sans[:20]:
        lines.append(f"  {f['name']} ({f['size_mb']}MB)")
    lines.append(f"\n衬线 ({len(serif)}):")
    for f in serif[:10]:
        lines.append(f"  {f['name']} ({f['size_mb']}MB)")

    lines.append(f"\n使用 find_font 查找具体的字体文件路径")
    return "\n".join(lines)


@tool(
    name="find_font",
    description="按名称模糊查找字体,返回字体文件路径",
    phase="plan",
    category="effect",
    tags=["font", "search"],
    group="花字与动画(效果层)",
)
def find_font(font_name: str) -> str:
    """
    查找字体.传字体名(如"思源宋体","得意黑","站酷快乐体").

    Returns:
        字体路径或"未找到"
    """
    from flower_text_manager import get_font_path
    result = get_font_path(font_name)
    if result:
        return f"fonts/{result}"
    return "未找到"


# ─── GSAP 工具函数 ──────────────────────────────────────────────


@tool(
    name="list_gsap_eases",
    description="列出 GSAP 可用的缓动函数,返回名称,效果描述,适用场景",
    phase="plan",
    category="effect",
    tags=["gsap", "ease", "reference"],
    group="花字与动画(效果层)",
)
def list_gsap_eases(category: str = "") -> str:
    """
    列出 GSAP 可用的缓动函数.

    Args:
        category: 分类过滤(power/back/bounce/elastic/sine/circ/expo/none),留空列出所有

    Returns:
        缓动函数列表
    """
    eases = {
        "power": [
            {"name": "power1.out", "desc": "轻微减速,自然平缓", "use": "通用入场,位移"},
            {"name": "power1.inOut", "desc": "两头慢中间快", "use": "对称动画"},
            {"name": "power2.out", "desc": "明显减速,稳重", "use": "元素入场,淡入"},
            {"name": "power2.inOut", "desc": "平滑加速再减速", "use": "位移动画"},
            {"name": "power3.out", "desc": "快速启动后减速", "use": "强调元素,快速入场"},
            {"name": "power3.inOut", "desc": "强烈的加减速", "use": "大幅度位移动画"},
            {"name": "power4.out", "desc": "极快启动后缓停", "use": "冲击性效果"},
        ],
        "back": [
            {"name": "back.out(1.7)", "desc": "超出目标后回弹,默认幅度", "use": "按钮点击,小元素弹出"},
            {"name": "back.out(2)", "desc": "中等回弹", "use": "花字入场,卡片弹出"},
            {"name": "back.out(3)", "desc": "强烈回弹", "use": "强调文字,Q 弹效果"},
            {"name": "back.inOut(1.7)", "desc": "往返都有回弹", "use": "往复动画"},
        ],
        "bounce": [
            {"name": "bounce.out", "desc": "掉落弹跳效果", "use": "从上方掉落,重物落地"},
            {"name": "bounce.in", "desc": "弹跳着消失", "use": "收缩消失"},
            {"name": "bounce.inOut", "desc": "弹跳入场再弹跳出场", "use": "趣味转场"},
        ],
        "elastic": [
            {"name": "elastic.out(1, 0.3)", "desc": "弹性振荡后稳定", "use": "弹簧效果,拉伸动画"},
            {"name": "elastic.out(1, 0.5)", "desc": "更持久的振荡", "use": "果冻效果"},
            {"name": "elastic.in(1, 0.3)", "desc": "收缩着消失", "use": "被吸走效果"},
        ],
        "sine": [
            {"name": "sine.inOut", "desc": "正弦曲线,平滑往复", "use": "呼吸,脉冲,悬浮"},
            {"name": "sine.out", "desc": "平滑减速", "use": "轻柔淡出"},
        ],
        "circ": [
            {"name": "circ.out", "desc": "圆弧曲线减速", "use": "滚动效果,圆形动画"},
            {"name": "circ.inOut", "desc": "圆弧加减速", "use": "旋转过渡"},
        ],
        "expo": [
            {"name": "expo.out", "desc": "极快启动后缓停", "use": "闪现场景"},
            {"name": "expo.in", "desc": "缓慢启动极快结束", "use": "弹出式警告"},
        ],
        "none": [
            {"name": "none", "desc": "线性,无缓动", "use": "进度条,机械运动"},
        ],
    }

    if category:
        data = eases.get(category, {})
        if not data:
            return f"未知分类:{category}.可用分类:{', '.join(eases.keys())}"
    else:
        data = {}
        for cat, items in eases.items():
            data.update({f"{cat}.{k}": v for k, v in enumerate(items)})

    lines = ["## GSAP 缓动函数参考"]
    for cat, items in eases.items():
        if category and cat != category:
            continue
        lines.append(f"\n### {cat.upper()}")
        for item in items:
            lines.append(f"- `{item['name']}` — {item['desc']};适用于{item['use']}")

    lines.append("\n\n**用法示例:**")
    lines.append('```javascript')
    lines.append('gsap.to(".box", { x: 100, ease: "back.out(3)", duration: 0.5 })')
    lines.append('```')

    return "\n".join(lines)


@tool(
    name="preview_gsap_effect",
    description="生成 GSAP 特效的 HTML 预览文件.预设效果可快速查看,也可通过参数自定义动画细节",
    phase="plan",
    category="effect",
    tags=["gsap", "preview", "html"],
    group="花字与动画(效果层)",
)
def preview_gsap_effect(
    text: str = "预览文字",
    font_size: int = 60,
    duration: float = 2.5,
    # ── 预设效果(快速查看)──
    effect_type: str = "",
    # ── 自定义参数(覆盖预设的动画细节)──
    entrance: Optional[dict] = None,
    exit_anim: Optional[dict] = None,
    loop_anim: Optional[dict] = None,
    # ── 自定义 CSS 样式 ──
    custom_css: Optional[dict] = None,
) -> str:
    """
    生成 GSAP 特效的 HTML 预览文件.

    **两种用法:**
    1. 快速查看:只传 effect_type(如"bounce"),用预设参数
    2. 自定义:传 effect_type + 自定义参数(entrance/exit/loop/custom_css)

    Args:
        text: 预览文字
        font_size: 字号(像素)
        duration: 总时长(秒)
        effect_type: 预设效果(bounce/pop/glow/typewriter/underline/split/fire/marble)
        entrance: 入场动画参数 {from:{}, to:{duration, ease}}
        exit_anim: 出场动画参数 {to:{duration, ease}}
        loop_anim: 循环动画参数(如脉冲)
        custom_css: 自定义 CSS 样式

    Returns:
        预览文件路径

    **示例:**
    ```python
    # 快速查看预设
    preview_gsap_effect("限时特惠", 60, 2.5, effect_type="pop")

    # 自定义入场缓动和时长
    preview_gsap_effect(
        "爆款", 72, 3.0, effect_type="bounce",
        entrance={"from": {"opacity": 0, "y": -200}, "to": {"duration": 0.8, "ease": "back.out(3)"}},
        exit_anim={"to": {"duration": 0.4, "ease": "power2.in"}}
    )
    ```
    """
    import hashlib
    from pathlib import Path

    preview_dir = Path(_PROJECT_DIR) / "_tmp_preview"
    preview_dir.mkdir(exist_ok=True)

    # 生成预览 HTML
    if effect_type:
        html = _gen_preview_by_type(
            effect_type, text, font_size, duration,
            entrance=entrance, exit_anim=exit_anim, loop_anim=loop_anim,
            custom_css=custom_css
        )
    else:
        # 没传 effect_type,用默认 bounce
        html = _gen_preview_by_type(
            "bounce", text, font_size, duration,
            entrance=entrance, exit_anim=exit_anim, loop_anim=loop_anim,
            custom_css=custom_css
        )

    # 保存文件
    hash_key = hashlib.md5(f"{effect_type or 'custom'}_{text}_{font_size}".encode()).hexdigest()[:8]
    html_path = preview_dir / f"preview_{hash_key}.html"

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    return f"✅ 预览已生成:{html_path}\\n在浏览器打开即可查看效果"


def _merge_anim_params(preset: dict, custom: Optional[dict]) -> dict:
    """合并预设和自定义动画参数"""
    if not custom:
        return preset
    result = preset.copy()
    for key, value in custom.items():
        if isinstance(value, dict) and key in result and isinstance(result[key], dict):
            result[key] = {**result[key], **value}
        else:
            result[key] = value
    return result


def _gen_preview_by_type(
    effect_type: str,
    text: str,
    font_size: int,
    duration: float,
    entrance: Optional[dict] = None,
    exit_anim: Optional[dict] = None,
    loop_anim: Optional[dict] = None,
    custom_css: Optional[dict] = None,
) -> str:
    """按预设类型生成预览,支持参数覆盖"""
    preset_map = {
        "bounce": _gen_preview_bounce,
        "pop": _gen_preview_pop,
        "glow": _gen_preview_glow,
        "typewriter": _gen_preview_typewriter,
        "underline": _gen_preview_underline,
        "split": _gen_preview_split,
        "fire": _gen_preview_fire,
        "marble": _gen_preview_marble,
    }
    gen_fn = preset_map.get(effect_type.lower(), _gen_preview_bounce)
    return gen_fn(text, font_size, duration, entrance, exit_anim, loop_anim, custom_css)


def _gen_preview_bounce(text: str, font_size: int, duration: float,
                        entrance: Optional[dict] = None, exit_anim: Optional[dict] = None,
                        loop_anim: Optional[dict] = None, custom_css: Optional[dict] = None) -> str:
    """弹簧弹入预览 — 参数自定义版本"""
    # 默认 GSAP 参数
    default_ent = {"from": {"opacity": 0, "y": -200}, "to": {"opacity": 1, "y": 0, "duration": 0.6, "ease": "bounce.out"}}
    default_ext = {"to": {"opacity": 0, "y": -20, "duration": 0.3, "ease": "power2.in"}}

    # 合并自定义参数
    ent = _merge_anim_params(default_ent, entrance)
    ext = _merge_anim_params(default_ext, exit_anim)

    css_bg = custom_css.get("background", "#1a1a1a") if custom_css else "#1a1a1a"

    # 构建 from vars(入场起始状态)
    ent_from_props = {k:v for k,v in ent.get("from", {}).items() if k not in ("duration", "ease")}
    ent_from_js = _dict_to_js_props(ent_from_props)
    ent_from_dur = ent.get("from", {}).get("duration", 0.6)
    ent_from_ease = ent.get("from", {}).get("ease", "power2.out")

    # 构建 to vars(入场目标状态)
    ent_to_props = {k:v for k,v in ent.get("to", {}).items() if k not in ("duration", "ease")}
    ent_to_js = _dict_to_js_props(ent_to_props)
    ent_to_dur = ent.get("to", {}).get("duration", 0.6)
    ent_to_ease = ent.get("to", {}).get("ease", "bounce.out")

    # 出场参数
    exit_props = {k:v for k,v in ext.get("to", {}).items() if k not in ("duration", "ease")}
    exit_js = _dict_to_js_props(exit_props)
    exit_dur = ext.get("to", {}).get("duration", 0.3)
    exit_ease = ext.get("to", {}).get("ease", "power2.in")
    exit_time = duration - exit_dur

    return f'''<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8"><title>Preview - Bounce</title>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<style>
body {{ margin:0; height:100vh; display:flex; align-items:center; justify-content:center; background:{css_bg}; }}
.box {{ font-size:{font_size}px; font-weight:700; color:#fff; text-shadow:0 4px 20px rgba(0,0,0,0.5); }}
</style></head>
<body><div class="box">{text}</div>
<script>
const tl = gsap.timeline();
tl.from(".box", {{ {ent_from_js}, duration:{ent_from_dur}, ease:"{ent_from_ease}" }}, 0);
tl.to(".box", {{ {ent_to_js}, duration:{ent_to_dur}, ease:"{ent_to_ease}" }}, 0);
tl.to(".box", {{ {exit_js}, duration:{exit_dur}, ease:"{exit_ease}" }}, {exit_time});
</script></body></html>'''


def _dict_to_js_props(d: dict) -> str:
    """把 dict 转为 JS 对象属性字符串"""
    if not d:
        return ""
    parts = []
    for k, v in d.items():
        if isinstance(v, str):
            parts.append(f'{k}:"{v}"')
        elif isinstance(v, bool):
            parts.append(f'{k}:{str(v).lower()}')
        else:
            parts.append(f'{k}:{v}')
    return ", ".join(parts)


# ─── 预设效果生成函数(保持简单,快速查看用)──────────────────────────


@tool(
    name="generate_gsap_html",
    description="生成自定义 GSAP 动画的 HTML.支持两种模式:browser_preview(仅预览)或 hf_composition(可被 HF CLI 渲染成视频)",
    phase="all",
    category="effect",
    tags=["gsap", "html", "custom", "hyperframes"],
    group="花字与动画(效果层)",
)
def generate_gsap_html(
    text: str = "文字",
    font_size: int = 60,
    duration: float = 2.5,
    # ── GSAP 动画参数(结构化 dict)──
    gsap_style: Optional[dict] = None,
    # ── CSS 样式参数 ──
    css_style: Optional[dict] = None,
    # ── 输出模式 ──
    hf_mode: bool = False,
) -> str:
    """
    生成自定义 GSAP 动画的 HTML.

    **两种模式:**
    1. `hf_mode=False`(默认):浏览器预览 HTML,直接在浏览器打开查看效果
    2. `hf_mode=True`:HF Composition HTML,可被 HF CLI 渲染成视频(符合 HF 协议)

    **HF 模式要求:**
    - 输出符合 HF 协议:data-* 属性,window.__timelines 注册,window.__hf 接口
    - 用于 `hf_cli.render_composition()` 渲染成 MOV/MP4
    - 背景透明(传 `css_style={"background": "transparent"}`)

    Args:
        text: 文字内容
        font_size: 字号(像素)
        duration: 总时长(秒)
        gsap_style: GSAP 动画参数,结构如下:
                    {
                      "entrance": {"from": {...}, "to": {...}},  # 入场
                      "exit": {"to": {...}},                      # 出场
                      "loop": {...}                                # 循环(可选)
                    }
        css_style: CSS 样式参数,如:
                   {"background": "#1a1a1a", "color": "#fff", "font_weight": 800}
        hf_mode: True 输出 HF 兼容格式(可渲染视频),False 仅浏览器预览

    Returns:
        HTML 文件路径

    **示例:**
    ```python
    # 浏览器预览
    generate_gsap_html("爆款", 72, 3.0, gsap_style={...}, css_style={...})

    # HF 渲染模式(用于视频输出)
    generate_gsap_html(
        "限时特惠", 80, 4.0,
        gsap_style={"entrance": {...}, "exit": {...}},
        css_style={"background": "transparent", "color": "#ff6b6b"},
        hf_mode=True
    )
    ```
    """
    import hashlib
    from pathlib import Path

    preview_dir = Path(_PROJECT_DIR) / "_tmp_preview"
    preview_dir.mkdir(exist_ok=True)

    # 默认参数
    default_gsap = {
        "entrance": {"from": {"opacity": 0, "y": -50}, "to": {"opacity": 1, "y": 0, "duration": 0.5, "ease": "power2.out"}},
        "exit": {"to": {"opacity": 0, "duration": 0.3, "ease": "power2.in"}}
    }
    default_css = {"background": "#1a1a1a", "color": "#fff", "font_weight": 800}

    # 合并参数
    gsap = _merge_anim_params_recursive(default_gsap, gsap_style or {})
    css = {**default_css, **(css_style or {})}

    # 生成 HTML
    html = _build_gsap_html(text, font_size, duration, gsap, css, hf_mode=hf_mode)

    # 保存 HTML(预览模式 + 调试用)
    hash_key = hashlib.md5(f"{'hf' if hf_mode else 'custom'}_{text}_{font_size}".encode()).hexdigest()[:8]
    html_path = preview_dir / f"{'hf_' if hf_mode else 'preview_'}{hash_key}.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    if not hf_mode:
        return f"✅ 预览已生成(浏览器打开查看): {html_path}"

    # ── HF 模式:直接渲染 HTML -> MOV ──
    try:
        from hf_engine.hf_cli import render_composition as _hf_render
    except ImportError:
        return f"⚠️ hf_engine 模块不可用,仅生成了 HTML: {html_path}"

    try:
        mov_path = _hf_render(
            html_content=html,
            cache_key=f"gsap_{hash_key}",
            width=1280,
            height=720,
            fps=24,
            format="mov",
        )
    except Exception as e:
        return f"⚠️ HF 渲染失败: {e}\nHTML 已保存: {html_path}"

    return f"✅ MOV 已渲染: {mov_path}"


def _merge_anim_params_recursive(base: dict, override: dict) -> dict:
    """深度合并动画参数 dict"""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_anim_params_recursive(result[key], value)
        else:
            result[key] = value
    return result


def _build_gsap_html(text: str, font_size: int, duration: float, gsap: dict, css: dict, *, hf_mode: bool = False) -> str:
    """
    构建 GSAP HTML.

    Args:
        hf_mode: True 输出符合 HF 协议的 HTML (可被 HF CLI 渲染), False 仅浏览器预览
    """
    ent = gsap.get("entrance", {})
    ext = gsap.get("exit", {})
    loop = gsap.get("loop", {})

    # CSS
    bg = css.get("background", "#1a1a1a")
    color = css.get("color", "#fff")
    weight = css.get("font_weight", 800)
    shadow = css.get("text_shadow", "0 4px 20px rgba(0,0,0,0.5)")

    # 入场 from
    ent_from = {k:v for k,v in ent.get("from", {}).items() if k not in ("duration", "ease")}
    ent_from_dur = ent.get("from", {}).get("duration", 0.5)
    ent_from_ease = ent.get("from", {}).get("ease", "power2.out")
    # 入场 to
    ent_to = {k:v for k,v in ent.get("to", {}).items() if k not in ("duration", "ease")}
    ent_to_dur = ent.get("to", {}).get("duration", 0.5)
    ent_to_ease = ent.get("to", {}).get("ease", "power2.out")

    # 出场
    exit_props = {k:v for k,v in ext.get("to", {}).items() if k not in ("duration", "ease")}
    exit_dur = ext.get("to", {}).get("duration", 0.3)
    exit_ease = ext.get("to", {}).get("ease", "power2.in")
    exit_time = duration - exit_dur

    ent_from_js = _dict_to_js_props(ent_from)
    ent_to_js = _dict_to_js_props(ent_to)
    exit_js = _dict_to_js_props(exit_props)

    # 循环动画 JS
    loop_js = ""
    if loop:
        loop_props = {k:v for k,v in loop.items() if k not in ("duration", "ease", "repeat", "yoyo")}
        loop_dur = loop.get("duration", 1)
        loop_ease = loop.get("ease", "sine.inOut")
        loop_repeat = loop.get("repeat", -1)
        loop_yoyo = loop.get("yoyo", True)
        loop_js = f'\ntl.to(".box", {{ {_dict_to_js_props(loop_props)}, duration:{loop_dur}, ease:"{loop_ease}", repeat:{loop_repeat}, yoyo:{str(loop_yoyo).lower()} }}, {ent_to_dur});'

    if hf_mode:
        # HF 协议模式:输出可被 HF CLI 渲染的 HTML
        return _build_hf_compatible_html(
            text=text, font_size=font_size, duration=duration,
            bg=bg, color=color, weight=weight, shadow=shadow,
            ent_from_js=ent_from_js, ent_from_dur=ent_from_dur, ent_from_ease=ent_from_ease,
            ent_to_js=ent_to_js, ent_to_dur=ent_to_dur, ent_to_ease=ent_to_ease,
            exit_js=exit_js, exit_dur=exit_dur, exit_ease=exit_ease, exit_time=exit_time,
            loop_js=loop_js
        )
    else:
        # 浏览器预览模式(原有逻辑)
        return f'''<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8"><title>GSAP Custom Preview</title>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<style>
body {{ margin:0; height:100vh; display:flex; align-items:center; justify-content:center; background:{bg}; }}
.box {{ font-size:{font_size}px; font-weight:{weight}; color:{color}; text-shadow:{shadow}; }}
</style></head>
<body><div class="box">{text}</div>
<script>
const tl = gsap.timeline();
tl.from(".box", {{ {ent_from_js}, duration:{ent_from_dur}, ease:"{ent_from_ease}" }}, 0);
tl.to(".box", {{ {ent_to_js}, duration:{ent_to_dur}, ease:"{ent_to_ease}" }}, 0);{loop_js}
tl.to(".box", {{ {exit_js}, duration:{exit_dur}, ease:"{exit_ease}" }}, {exit_time});
</script></body></html>'''


def _build_hf_compatible_html(
    text: str, font_size: int, duration: float,
    bg: str, color: str, weight: int, shadow: str,
    ent_from_js: str, ent_from_dur: float, ent_from_ease: str,
    ent_to_js: str, ent_to_dur: float, ent_to_ease: str,
    exit_js: str, exit_dur: float, exit_ease: str, exit_time: float,
    loop_js: str
) -> str:
    """
    构建符合 HF 协议的 HTML.

    HF 协议要求:
    - data-composition-id="main", data-width, data-height, data-duration
    - window.__timelines["main"] = gsap.timeline({paused:true})
    - window.__hf = {duration, seek}
    - class="clip" + data-start/duration 用于 timed elements
    """
    # 计算 hold 时间(入场后到出场前的保持时间)
    hold_duration = exit_time - ent_to_dur

    # 循环动画在 HF 模式下需要特殊处理(在 hold 期间循环)
    hf_loop_js = ""
    if loop_js:
        # 在 HF 中,循环动画需要在 hold 期间持续执行
        # 我们将循环动画放在入场完成后开始
        hf_loop_js = loop_js

    return f'''<!DOCTYPE html>
<html lang="zh" data-composition-id="main" data-width="1280" data-height="720" data-duration="{duration}">
<head><meta charset="UTF-8"><title>GSAP HF Composition</title>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<style>
body {{ margin:0; width:1280px; height:720px; display:flex; align-items:center; justify-content:center; background:{bg}; overflow:hidden; }}
.box {{ font-size:{font_size}px; font-weight:{weight}; color:{color}; text-shadow:{shadow}; }}
</style></head>
<body>
<div class="clip" data-start="0" data-duration="{duration}">
<div class="box">{text}</div>
</div>
<script>
// HF 协议:注册 paused timeline
const tl = gsap.timeline({{paused:true}});
window.__timelines = window.__timelines || {{}};
window.__timelines["main"] = tl;

// 构建动画
tl.from(".box", {{ {ent_from_js}, duration:{ent_from_dur}, ease:"{ent_from_ease}" }}, 0);
tl.to(".box", {{ {ent_to_js}, duration:{ent_to_dur}, ease:"{ent_to_ease}" }}, 0);{hf_loop_js}
tl.to(".box", {{ {exit_js}, duration:{exit_dur}, ease:"{exit_ease}" }}, {exit_time});

// HF 协议:seek 接口
window.__hf = {{
  duration: {duration},
  seek: function(t) {{
    tl.seek(t);
    // dispatch hf-seek 事件(HF 引擎会监听)
    window.dispatchEvent(new CustomEvent('hf-seek', {{detail: {{time: t, compositionId: "main"}}}}));
  }}
}};

// 初始化:告知 HF 运行时已就绪
window.addEventListener('hf-ready', () => {{
  console.log('[HF] Composition ready');
}});
</script></body></html>'''


# ─── 预设效果生成函数(保持简单,快速查看用)──────────────────────────


def _gen_preview_pop(text: str, font_size: int, duration: float) -> str:
    """弹出变色预览"""
    return f'''<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8"><title>Preview - Pop</title>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<style>
body {{ margin:0; height:100vh; display:flex; align-items:center; justify-content:center; background:#1a1a1a; }}
.box {{ font-size:{font_size}px; font-weight:800; color:#fff; opacity:0; transform:scale(0) rotate(-15deg); }}
</style></head>
<body><div class="box">{text}</div>
<script>
const colors=['#ff6b6b','#feca57','#48dbfb','#ff9ff3','#54a0ff','#5f27cd'];
const tl = gsap.timeline();
tl.to(".box", {{opacity:1, scale:1, rotation:0, duration:0.5, ease:"back.out(3)"}});
colors.forEach((c,i) => tl.to(".box", {{color:c, duration:0.15}}, {0.5+i*0.15}));
tl.to(".box", {{opacity:0, scale:0.8, duration:0.3}}, {duration-0.4});
</script></body></html>'''


def _gen_preview_glow(text: str, font_size: int, duration: float) -> str:
    """多色霓虹预览"""
    return f'''<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8"><title>Preview - Glow</title>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<style>
body {{ margin:0; height:100vh; display:flex; align-items:center; justify-content:center; background:#0a0a0a; }}
.box {{
  font-size:{font_size}px; font-weight:900; letter-spacing:4px; color:#fff;
  background:linear-gradient(135deg,#ff6b6b,#feca57,#48dbfb,#ff9ff3);
  -webkit-background-clip:text; -webkit-text-fill-color:transparent;
  filter:drop-shadow(0 0 20px rgba(255,107,107,0.6));
  opacity:0; transform:scale(0.7);
}}
</style></head>
<body><div class="box">{text}</div>
<script>
const tl = gsap.timeline();
tl.to(".box", {{opacity:1, scale:1, duration:0.6, ease:"back.out(2)"}});
tl.to(".box", {{filter:'drop-shadow(0 0 40px rgba(255,107,107,0.9))', duration:0.5, yoyo:true, repeat:-1, ease:"sine.inOut"}}, 0.5);
tl.to(".box", {{opacity:0, duration:0.3}}, {duration-0.4});
</script></body></html>'''


def _gen_preview_typewriter(text: str, font_size: int, duration: float) -> str:
    """打字机预览"""
    char_dur = 0.08
    typing_dur = len(text) * char_dur
    return f'''<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8"><title>Preview - Typewriter</title>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<style>
body {{ margin:0; height:100vh; display:flex; align-items:center; justify-content:center; background:#1a1a2e; }}
.wrap {{ position:relative; }}
.box {{
  font-size:{font_size}px; font-family:monospace; color:#0f0;
  white-space:nowrap; overflow:hidden; border-right:3px solid #0f0; width:0; opacity:0;
}}
</style></head>
<body><div class="wrap"><div class="box">{text}</div></div>
<script>
const tl = gsap.timeline();
tl.set(".box", {{opacity:1}});
tl.to(".box", {{width:{len(text)*font_size*0.6}, duration:{typing_dur}, ease:"steps({len(text)})"}});
tl.to(".box", {{borderColor:"transparent", duration:0.3, repeat:-1, yoyo:true, ease:"steps(1)"}});
tl.to(".box", {{opacity:0, duration:0.3}}, {duration-0.4});
</script></body></html>'''


def _gen_preview_underline(text: str, font_size: int, duration: float) -> str:
    """下划线生长预览"""
    return f'''<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8"><title>Preview - Underline</title>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<style>
body {{ margin:0; height:100vh; display:flex; align-items:center; justify-content:center; background:#1a1a1a; }}
.wrap {{ position:relative; font-size:{font_size}px; font-weight:700; color:#fff; opacity:0; }}
.uline {{ position:absolute; bottom:-4px; left:0; height:3px; border-radius:2px; width:0; background:linear-gradient(90deg,#ff6b6b,#feca57); }}
</style></head>
<body><div class="wrap">{text}<div class="uline"></div></div>
<script>
const tl = gsap.timeline();
tl.to(".wrap", {{opacity:1, duration:0.3, ease:"power2.out"}});
tl.to(".uline", {{width:{len(text)*font_size*0.7}, duration:0.4, ease:"power3.out"}}, 0.15);
tl.to(".uline", {{background:"linear-gradient(90deg,#feca57,#48dbfb)", duration:0.5, yoyo:true, repeat:1}}, 0.3);
tl.to(".wrap", {{opacity:0, duration:0.3}}, {duration-0.4});
</script></body></html>'''


def _gen_preview_split(text: str, font_size: int, duration: float) -> str:
    """裂开效果预览"""
    return f'''<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8"><title>Preview - Split</title>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<style>
body {{ margin:0; height:100vh; display:flex; align-items:center; justify-content:center; background:#1a1a1a; }}
.half {{ position:absolute; font-size:{font_size}px; font-weight:700; color:#fff; opacity:0; }}
</style></head>
<body>
<div class="half half-l">{text[:len(text)//2] or text}</div>
<div class="half half-r">{text[len(text)//2:] or text}</div>
<script>
const tl = gsap.timeline();
const leftX = -{len(text)*font_size*0.2};
const rightX = {len(text)*font_size*0.2};
tl.to(".half-l", {{opacity:1, x:0, duration:0.4, ease:"power2.out"}});
tl.to(".half-r", {{opacity:1, x:0, duration:0.4, ease:"power2.out"}}, 0);
tl.to(".half-l", {{x:leftX, opacity:0, duration:0.4, ease:"power3.in"}}, {duration-0.6});
tl.to(".half-r", {{x:rightX, opacity:0, duration:0.4, ease:"power3.in"}}, {duration-0.6});
</script></body></html>'''


def _gen_preview_fire(text: str, font_size: int, duration: float) -> str:
    """火焰文字预览(简化版)"""
    return f'''<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8"><title>Preview - Fire</title>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<style>
body {{ margin:0; height:100vh; display:flex; align-items:center; justify-content:center; background:#0a0a0a; }}
.box {{
  font-size:{font_size}px; font-weight:900; letter-spacing:4px;
  background:linear-gradient(180deg,#FFF8E0 0%,#FFD700 15%,#FF8C00 40%,#FF4500 65%,#FF0000 100%);
  -webkit-background-clip:text; -webkit-text-fill-color:transparent;
  filter:drop-shadow(0 0 20px rgba(255,69,0,0.8));
  opacity:0; transform:scale(0.8);
  animation: flame-glow 1.5s ease-in-out infinite;
}}
@keyframes flame-glow {{
  0%,100% {{ filter:drop-shadow(0 0 20px rgba(255,69,0,0.8)) drop-shadow(0 0 40px rgba(255,165,0,0.5)); }}
  50% {{ filter:drop-shadow(0 0 30px rgba(255,0,0,0.9)) drop-shadow(0 0 60px rgba(255,69,0,0.7)); }}
}}
</style></head>
<body><div class="box">{text}</div>
<script>
const tl = gsap.timeline();
tl.to(".box", {{opacity:1, scale:1, duration:0.5, ease:"back.out(2)"}});
tl.to(".box", {{opacity:0, duration:0.3}}, {duration-0.4});
</script></body></html>'''


def _gen_preview_marble(text: str, font_size: int, duration: float) -> str:
    """大理石纹理预览"""
    return f'''<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8"><title>Preview - Marble</title>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<style>
body {{ margin:0; height:100vh; display:flex; align-items:center; justify-content:center; background:#1a1a1a; }}
.box {{
  position:relative; padding:20px 40px;
  background:rgba(30,30,30,0.9);
  border:1.5px solid rgba(100,100,100,0.4);
  border-radius:16px;
  font-size:{font_size}px; font-weight:800; letter-spacing:2px;
  color:#E8E8E8;
  text-shadow:0 4px 8px rgba(0,0,0,0.6);
  opacity:0; transform:translateY(20px);
}}
</style></head>
<body><div class="box">{text}</div>
<script>
const tl = gsap.timeline();
tl.to(".box", {{opacity:1, y:0, duration:0.5, ease:"power3.out"}});
tl.to(".box", {{opacity:0, duration:0.3}}, {duration-0.4});
</script></body></html>'''


# ─── HF 集成工具 ──────────────────────────────────────────────


@tool(
    name="list_hf_blocks",
    description="列出 HyperFrames 可用的 50+ registry blocks(转场/特效/叠加层/图表等)",
    phase="plan",
    category="effect",
    tags=["hyperframes", "registry", "blocks"],
    group="花字与动画(效果层)",
)
def list_hf_blocks(category: str = "") -> str:
    """
    列出 HyperFrames registry 中可用的 blocks.

    Args:
        category: 分类过滤(transition/overlay/chart/cinematic),留空列出所有

    Returns:
        blocks 列表
    """
    blocks = {
        "transition": [
            {"name": "flash-through-white", "desc": "白色闪白转场", "use": "通用转场"},
            {"name": "shimmer-sweep", "desc": "闪光扫过转场", "use": "科技感转场"},
            {"name": "crossfade", "desc": "交叉淡入淡出", "use": "柔和转场"},
            {"name": "slide-left", "desc": "左滑入", "use": "节奏转场"},
            {"name": "slide-right", "desc": "右滑入", "use": "节奏转场"},
            {"name": "zoom-in", "desc": "放大推进", "use": "强调转场"},
            {"name": "blur-transition", "desc": "模糊转场", "use": "电影感"},
        ],
        "overlay": [
            {"name": "instagram-follow", "desc": "Ins 关注动画", "use": "社交引流"},
            {"name": "youtube-subscribe", "desc": "YouTube 订阅", "use": "社交引流"},
            {"name": "lower-third", "desc": "下三分之一标题", "use": "人物介绍"},
            {"name": "social-handle", "desc": "社交账号展示", "use": "品牌曝光"},
        ],
        "chart": [
            {"name": "data-chart", "desc": "数据图表动画", "use": "数据展示"},
            {"name": "bar-chart", "desc": "柱状图", "use": "对比数据"},
            {"name": "line-chart", "desc": "折线图", "use": "趋势展示"},
            {"name": "pie-chart", "desc": "饼图", "use": "占比展示"},
            {"name": "number-counter", "desc": "数字计数器", "use": "数据增长"},
        ],
        "cinematic": [
            {"name": "film-grain", "desc": "胶片颗粒", "use": "复古质感"},
            {"name": "vignette", "desc": "暗角效果", "use": "电影感"},
            {"name": "light-leak", "desc": "漏光效果", "use": "氛围感"},
            {"name": "chromatic-aberration", "desc": "色差故障", "use": "赛博朋克"},
            {"name": "glow-bloom", "desc": "辉光泛光", "use": "梦幻感"},
        ],
    }

    if category:
        data = blocks.get(category, [])
        if not data:
            return f"未知分类:{category}.可用分类:{', '.join(blocks.keys())}"
    else:
        data = []
        for cat, items in blocks.items():
            for item in items:
                data.append({**item, "category": cat})

    lines = ["## HyperFrames Registry Blocks"]
    lines.append(f"共 {len(data)} 个可用 blocks\n")

    for cat, items in blocks.items():
        lines.append(f"\n### {cat.upper()}")
        for item in items:
            lines.append(f"- `{item['name']}` — {item['desc']};用于{item['use']}")

    lines.append("\n\n**使用方法:** 在 composition 中用 `npx hyperframes add <block-name>` 安装")
    return "\n".join(lines)


@tool(
    name="hf_snapshot",
    description="对 HF composition 截取关键帧 PNG,快速验证效果(无需完整渲染)",
    phase="plan",
    category="effect",
    tags=["hyperframes", "snapshot", "preview"],
    group="花字与动画(效果层)",
)
def hf_snapshot(
    composition_path: str,
    timestamps: list = None,
    frames: int = 5,
    output_dir: str = "",
) -> str:
    """
    截取 HF composition 的关键帧 PNG.

    Args:
        composition_path: composition HTML 路径
        timestamps: 指定时间戳列表(秒),如 [0.5, 2.0, 3.5]
        frames: 均匀截取帧数(timestamps 为空时使用)
        output_dir: 输出目录

    Returns:
        截图文件列表
    """
    import subprocess
    from pathlib import Path

    comp_path = Path(composition_path).resolve()
    if not comp_path.exists():
        return f"文件不存在:{composition_path}"

    # 构建命令
    cmd = ["npx", "hyperframes", "snapshot", str(comp_path.parent)]

    if timestamps:
        cmd.extend(["--at", ",".join(map(str, timestamps))])
    else:
        cmd.extend(["--frames", str(frames)])

    if output_dir:
        cmd.extend(["--output", output_dir])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            return f"HF snapshot 失败:{result.stderr[-500:]}"

        # 解析输出中的 PNG 路径
        png_paths = []
        for line in result.stdout.split("\n"):
            if ".png" in line and os.path.exists(line.strip()):
                png_paths.append(line.strip())

        if png_paths:
            return f"✅ 截取 {len(png_paths)} 帧:\n" + "\n".join(png_paths)
        return f"✅ Snapshot 完成:\n{result.stdout.strip()}"

    except Exception as e:
        return f"HF snapshot 异常:{str(e)}"


@tool(
    name="hf_lint",
    description="静态检查 HF composition HTML 结构(属性完整性/时间线注册/类名规范)",
    phase="plan",
    category="effect",
    tags=["hyperframes", "lint", "validate"],
    group="花字与动画(效果层)",
)
def hf_lint(composition_path: str) -> str:
    """
    静态检查 HF composition HTML.

    Args:
        composition_path: composition HTML 路径

    Returns:
        lint 结果
    """
    import subprocess
    from pathlib import Path

    comp_path = Path(composition_path).resolve()
    if not comp_path.exists():
        return f"文件不存在:{composition_path}"

    cmd = ["npx", "hyperframes", "lint", str(comp_path.parent)]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")

        output = result.stdout + result.stderr
        if result.returncode == 0:
            return f"✅ Lint 通过:\n{output.strip()}"
        else:
            return f"⚠️ Lint 发现问题:\n{output.strip()[-800:]}"

    except Exception as e:
        return f"HF lint 异常:{str(e)}"


@tool(
    name="hf_validate",
    description="在 headless Chrome 中加载 HF composition,检查运行时错误(JS 异常/资源缺失)",
    phase="plan",
    category="effect",
    tags=["hyperframes", "validate", "runtime"],
    group="花字与动画(效果层)",
)
def hf_validate(composition_path: str) -> str:
    """
    运行时验证 HF composition.

    Args:
        composition_path: composition HTML 路径

    Returns:
        validate 结果
    """
    import subprocess
    from pathlib import Path

    comp_path = Path(composition_path).resolve()
    if not comp_path.exists():
        return f"文件不存在:{composition_path}"

    cmd = ["npx", "hyperframes", "validate", str(comp_path.parent)]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace")

        output = result.stdout + result.stderr
        if result.returncode == 0:
            return f"✅ Validate 通过:\n{output.strip()}"
        else:
            return f"❌ Validate 失败:\n{output.strip()[-800:]}"

    except Exception as e:
        return f"HF validate 异常:{str(e)}"

SYSTEM_PROMPT_SPEECH = (
    "你是 ClipMind 的剪辑导演.你的工作是剪辑一段口播视频.\n\n"
    "## 工作方式\n"
    "你有一组工具,每个工具只能做一件事.你一次只能调一个工具.\n"
    "每一步:先想清楚当前该做什么 -> 调一个工具 -> 看结果 -> 再想下一步.\n\n"
    "## 阶段流程(参考,不强制)\n"
    "你应该分阶段进行,但不是硬编码:\n"
    "1. 素材分析阶段:逐段看素材,决定保留/弃用/存疑\n"
    "2. 排版阶段:编排镜头顺序,预览,修改\n"
    "3. 动效阶段:设计文字特效\n"
    "4. 渲染阶段:输出最终视频\n\n"
    "每个阶段内部你都可以反复质疑自己的判断.\n"
    "比如保留了一段素材后,可以回看原始素材确认是否该保留.\n\n"
    "## 重要规则\n"
    "- 一次只做一个动作\n"
    "- 做完一步,想想下一步该做什么,再调工具\n"
    "- 素材片段最短 30 秒\n"
    "- 不确定就标记存疑回头再看\n"
    "- 满意了再说'完事了'\n"
)

# 工具已通过 @tool 装饰器自动注册到 Registry
