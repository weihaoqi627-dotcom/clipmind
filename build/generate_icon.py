"""
ClipMind 图标生成器
=====================
完整图标: 深色圆角底 + 一笔画鱼白描 + 微光晕.
匹配软件内部设计语言,输出多分辨率 .ico.
用法: python build/generate_icon.py
输出: build/icon.ico
"""
import os, re, struct, io

try:
    from PIL import Image, ImageDraw, ImageFilter
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow"])
    from PIL import Image, ImageDraw, ImageFilter

OUTPUT = os.path.join(os.path.dirname(__file__), "icon.ico")

# ── Logo 定义（来自 SvgIcon.vue）──
LOGO_PATH = "M44 13 C24 15, 14 24, 14 30 C14 36, 22 45, 28 45 L28 18 L34 36 L40 18 L40 45"
STROKE_W = 4.5
SCALE, TX, TY = 1.35, -7.5, -7.5
SIZES = [16, 24, 32, 48, 64, 128, 256]

# 暗色主题色（匹配 App.vue 提亮后的 surface-base）
BG_COLOR = (0, 0, 0, 255)
STROKE_COLOR = (255, 255, 255, 255)
GLOW_COLOR = (99, 102, 241, 40)  # 极淡品牌紫晕


def tokenize(d: str) -> list:
    s = re.sub(r'([MLCQZ])\s*(\d)', r'\1 \2', d)
    s = re.sub(r'(\d)\s*([MLCQZ])', r'\1 \2', s)
    tokens = s.replace(",", " ").split()
    cmds, i = [], 0
    while i < len(tokens):
        if tokens[i] in "MLCQZ":
            cmd = tokens[i]; i += 1
            args = []
            while i < len(tokens) and tokens[i] not in "MLCQZ":
                args.append(float(tokens[i])); i += 1
            cmds.append((cmd, args))
        else:
            i += 1
    return cmds


def transform(x, y):
    return (x * SCALE + TX, y * SCALE + TY)


def bezier_pts(p0, p1, p2, p3, steps=64):
    pts = []
    for i in range(steps + 1):
        t = i / steps
        b0 = (1-t)**3; b1 = 3*t*(1-t)**2
        b2 = 3*t*t*(1-t); b3 = t*t*t
        pts.append((b0*p0[0]+b1*p1[0]+b2*p2[0]+b3*p3[0],
                    b0*p0[1]+b1*p1[1]+b2*p2[1]+b3*p3[1]))
    return pts


def render_icon(size: int) -> Image.Image:
    """完整图标: 圆角暗底 + 白描鱼 + 微光"""
    # ── 背景: 圆角矩形 ──
    r = size * 0.20  # 圆角半径
    bg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw_bg = ImageDraw.Draw(bg)
    draw_bg.rounded_rectangle([(0, 0), (size-1, size-1)], radius=r, fill=BG_COLOR)

    # ── 鱼路径坐标 ──
    pad = size * 0.08
    inner = size - 2 * pad
    s = inner / 60.0
    ox, oy = pad, pad

    def sp(x, y):
        tx, ty = transform(x, y)
        return (tx * s + ox, ty * s + oy)

    cmds = tokenize(LOGO_PATH)
    all_pts, current = [], (0.0, 0.0)
    for cmd, args in cmds:
        if cmd == "M":
            current = sp(args[0], args[1])
            all_pts = [current]
        elif cmd == "L":
            p = sp(args[0], args[1])
            all_pts.append(p); current = p
        elif cmd == "C":
            c1 = sp(args[0], args[1]); c2 = sp(args[2], args[3])
            end = sp(args[4], args[5])
            curve = bezier_pts(current, c1, c2, end)
            all_pts.extend(curve[1:]); current = end

    if len(all_pts) < 2:
        return bg

    # ── 4x 超采样绘制（抗锯齿）──
    hi = size * 4
    himg = Image.new("RGBA", (hi, hi), (0, 0, 0, 0))

    # 先画圆角背景
    hdraw = ImageDraw.Draw(himg)
    hr = r * 4
    hdraw.rounded_rectangle([(0, 0), (hi-1, hi-1)], radius=hr, fill=BG_COLOR)

    # 鱼路径缩放到高分辨率
    hs = hi / size
    hpts = [(x * hs, y * hs) for x, y in all_pts]

    # 计算笔画宽度
    hw = max(3, int(STROKE_W * (inner / 60) * hs))

    # 先画极淡品牌色光晕（扩大 2px 模糊模拟发光）
    glow_img = Image.new("RGBA", (hi, hi), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow_img)
    glow_draw.line(hpts, fill=GLOW_COLOR, width=hw + 4, joint="curve")
    glow_img = glow_img.filter(ImageFilter.GaussianBlur(radius=3))
    himg = Image.alpha_composite(himg, glow_img)

    # 再画白色描边鱼
    hdraw = ImageDraw.Draw(himg)
    hdraw.line(hpts, fill=STROKE_COLOR, width=hw, joint="curve")

    # 缩回到目标尺寸
    return himg.resize((size, size), Image.LANCZOS)


def pack_ico(images: dict) -> bytes:
    sizes = sorted(images.keys())
    header = struct.pack('<HHH', 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b''
    for s in sizes:
        data = images[s]
        w = s if s < 256 else 0
        h = s if s < 256 else 0
        entries += struct.pack('<BBBBHHII', w, h, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return header + entries + b''.join(images[s] for s in sizes)


def main():
    os.environ["PYTHONIOENCODING"] = "utf-8"
    print("生成 ClipMind 图标 (深色圆角底 + 一笔画鱼白描)...")
    pngs = {}
    for s in SIZES:
        img = render_icon(s)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        pngs[s] = buf.getvalue()
        print(f"  {s}x{s}  PNG={len(pngs[s])/1024:.1f}KB")

    ico_data = pack_ico(pngs)
    with open(OUTPUT, "wb") as f:
        f.write(ico_data)
    kb = len(ico_data) / 1024
    print(f"输出: {OUTPUT} ({kb:.1f} KB, {len(SIZES)} 尺寸)")


if __name__ == "__main__":
    main()
