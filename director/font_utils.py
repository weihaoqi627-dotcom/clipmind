"""
字体工具
========
扫描项目 fonts/ 目录和系统字体,按名称查找可用字体.
支持 ffmpeg drawtext fontfile 参数生成.
"""
import sys, os
from pathlib import Path
from typing import Optional

PROJECT_DIR = Path(__file__).parent.parent
FONT_DIR = PROJECT_DIR / "fonts"

# 全局缓存
_font_index: dict[str, list[Path]] = {}
_index_loaded = False


def _build_index() -> dict[str, list[Path]]:
    """扫描 fonts/ 和系统字体目录,建立 名称 -> 文件列表 映射"""
    index: dict[str, list[Path]] = {}

    # 1. 项目字体目录
    if FONT_DIR.exists():
        for f in FONT_DIR.iterdir():
            if f.suffix.lower() in (".ttf", ".otf", ".ttc"):
                if f.stat().st_size < 1024:
                    continue
                name = f.stem
                index.setdefault(name, []).append(f)

    # 2. Windows 系统字体目录
    windir = os.environ.get("WINDIR", r"C:\Windows")
    sys_font = Path(windir) / "Fonts"
    if sys_font.exists():
        for f in sys_font.iterdir():
            if f.suffix.lower() not in (".ttf", ".otf", ".ttc"):
                continue
            if f.stat().st_size < 1024:
                continue
            name = f.stem
            # 只索引系统中文字体(减小索引体积)
            # 常用中文字体关键词
            if any(kw in name.lower() for kw in (
                "simhei", "simsun", "msyh", "msjh", "deng", "fang",
                "kai", "ming", "song", "hei", "yuan", "noto",
                "microsoft yahei", "microsoft jheng",
            )) or f.suffix.lower() in (".ttc",):
                index.setdefault(name, []).append(f)

    return index


def _ensure_index():
    global _index_loaded, _font_index
    if not _index_loaded:
        _font_index = _build_index()
        _index_loaded = True


def find_font(name: str) -> Optional[str]:
    """
    按名称查找字体文件.

    Args:
        name: 字体名称(模糊匹配),如 "思源黑体","微软雅黑","后现代"

    Returns:
        字体文件绝对路径,或 None
    """
    _ensure_index()

    # 精确匹配
    if name in _font_index:
        return str(_font_index[name][0])

    # 模糊匹配
    name_lower = name.lower()
    for idx_name, paths in _font_index.items():
        if name_lower in idx_name.lower():
            return str(paths[0])

    return None


def list_fonts() -> list[str]:
    """列出所有可用字体名称"""
    _ensure_index()
    return sorted(_font_index.keys())


def get_drawtext_args(
    text: str,
    font_name: str = "",
    font_path: str = "",
    font_size: int = 48,
    font_color: str = "white",
    x: str = "(w-text_w)/2",
    y: str = "(h-text_h)/2",
    shadow: bool = True,
) -> str:
    """
    生成 ffmpeg drawtext 参数字符串.

    Args:
        text: 文字内容
        font_name: 字体名称(自动查找)
        font_path: 直接指定字体路径(优先)
        font_size: 字号
        font_color: 颜色
        x, y: 位置表达式
        shadow: 是否加阴影

    Returns:
        drawtext 参数字符串,如 "drawtext=text='hello':fontfile=xxx:fontsize=48:..."
    """
    from director.tools.effects import _escape_dt_text

    # 确定字体路径
    if not font_path and font_name:
        found = find_font(font_name)
        if found:
            font_path = found

    if not font_path:
        # 回退:用 effects.py 里的系统字体查找
        try:
            from director.tools.render import _find_chinese_font
            font_path = _find_chinese_font()
        except Exception:
            pass

    if not font_path:
        raise FileNotFoundError(f"找不到可用字体: {font_name or '(未指定)'}")

    escaped = _escape_dt_text(text)
    parts = [
        f"drawtext=text={escaped}",
        f"fontfile='{font_path}'",
        f"fontsize={font_size}",
        f"fontcolor={font_color}",
        f"x={x}",
        f"y={y}",
    ]
    if shadow:
        parts.append("shadowcolor=black@0.5:shadowx=2:shadowy=2")

    return ":".join(parts)


# ─── 调试 ───────────────────────────────────────────────────

if __name__ == "__main__":
    _ensure_index()
    print(f"字体索引: {len(_font_index)} 个")
    for name in sorted(_font_index.keys())[:20]:
        paths = _font_index[name]
        print(f"  {name} ({len(paths)} 文件)")
