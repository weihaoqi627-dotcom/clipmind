"""
ClipMind 图标生成（纯 PIL，无外部依赖）
输出: build/icon.png (1024x, 给 electron-builder)
      build/icon.ico (256x, 给 main.js BrowserWindow)
"""
from PIL import Image, ImageDraw, ImageFilter
import re, os, io, struct

LOGO_PATH = 'M44 13 C24 15, 14 24, 14 30 C14 36, 22 45, 28 45 L28 18 L34 36 L40 18 L40 45'
STROKE_W = 4.5

# SVG: transform="scale(1.35) translate(-7.5, -7.5)"
# SVG 变换顺序是右到左: 先 translate, 再 scale
SCALE = 1.35
TX, TY = -7.5, -7.5


def tokenize(d):
    """解析 SVG path 数据"""
    s = re.sub(r'([MLCQZ])\s*(\d)', r'\1 \2', d)
    s = re.sub(r'(\d)\s*([MLCQZ])', r'\1 \2', s)
    tokens = s.replace(',', ' ').split()
    cmds, i = [], 0
    while i < len(tokens):
        if tokens[i] in 'MLCQZ':
            cmd = tokens[i]
            i += 1
            args = []
            while i < len(tokens) and tokens[i] not in 'MLCQZ':
                args.append(float(tokens[i]))
                i += 1
            cmds.append((cmd, args))
        else:
            i += 1
    return cmds


def bezier_pts(p0, p1, p2, p3, steps=64):
    """三次贝塞尔曲线取点"""
    pts = []
    for i in range(steps + 1):
        t = i / steps
        b0 = (1 - t) ** 3
        b1 = 3 * t * (1 - t) ** 2
        b2 = 3 * t * t * (1 - t)
        b3 = t * t * t
        pts.append((
            b0 * p0[0] + b1 * p1[0] + b2 * p2[0] + b3 * p3[0],
            b0 * p0[1] + b1 * p1[1] + b2 * p2[1] + b3 * p3[1],
        ))
    return pts


def render_fish(size, with_glow=True):
    """
    渲染一笔画鱼图标。
    匹配 SvgIcon.vue 的 logo 定义:
      viewBox="0 0 60 60"
      stroke="#FFF"
      stroke-width="4.5"
      stroke-linecap="round"
      stroke-linejoin="round"
      transform="scale(1.35) translate(-7.5, -7.5)"
    """
    # SVG 变换: (x, y) → ((x + TX) * SCALE, (y + TY) * SCALE)
    # 然后缩放到 size 内，带 pad 边距
    pad = size * 0.12
    inner = size - 2 * pad
    s = inner / 60.0  # 60-unit viewBox → 内区域像素
    ox, oy = pad, pad

    def svg_transform(x, y):
        """先 translate, 再 scale, 再映射到像素坐标"""
        tx = (x + TX) * SCALE
        ty = (y + TY) * SCALE
        return (tx * s + ox, ty * s + oy)

    # 解析路径
    cmds = tokenize(LOGO_PATH)
    all_pts, current = [], (0.0, 0.0)
    for cmd, args in cmds:
        if cmd == 'M':
            current = svg_transform(args[0], args[1])
            all_pts = [current]
        elif cmd == 'L':
            p = svg_transform(args[0], args[1])
            all_pts.append(p)
            current = p
        elif cmd == 'C':
            c1 = svg_transform(args[0], args[1])
            c2 = svg_transform(args[2], args[3])
            end = svg_transform(args[4], args[5])
            curve = bezier_pts(current, c1, c2, end)
            all_pts.extend(curve[1:])
            current = end

    # 笔画宽度: 按 60-unit 空间中的 stroke-width 映射到像素
    hw = max(2, int(STROKE_W * s))

    # ── 绘制 ──
    img = Image.new('RGBA', (size, size), (0, 0, 0, 255))

    # 光晕（可选）
    if with_glow:
        glow = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(glow)
        gdraw.line(all_pts, fill=(99, 102, 241, 15), width=hw + 8, joint='curve')
        glow = glow.filter(ImageFilter.GaussianBlur(radius=size // 160))
        img = Image.alpha_composite(img, glow)

    # 白色描边（匹配 SVG: stroke="#FFF" + stroke-linecap="round" + stroke-linejoin="round"）
    draw = ImageDraw.Draw(img)
    draw.line(all_pts, fill=(255, 255, 255, 255), width=hw, joint='curve')

    return img


def pack_ico_simple(png_bytes_sizes):
    """
    手动打包 ICO（避免 PIL 的 ICO 多帧兼容性问题）
    png_bytes_sizes: list of (w_h_size, png_data)
    """
    entries = []
    offset = 6 + 16 * len(png_bytes_sizes)
    for w_h, png_data in png_bytes_sizes:
        w = w_h if w_h < 256 else 0
        h = w_h if w_h < 256 else 0
        entries.append({
            'data': png_data,
            'header': struct.pack('<BBBBHHII', w, h, 0, 0, 1, 32, len(png_data), offset),
        })
        offset += len(png_data)

    ico = struct.pack('<HHH', 0, 1, len(entries))
    for e in entries:
        ico += e['header']
    for e in entries:
        ico += e['data']
    return ico


def main():
    # ── 生成 1024x1024 PNG（electron-builder 用）──
    print('生成 1024x1024 icon.png...')
    img = render_fish(1024, with_glow=False)
    img.save('build/icon.png', 'PNG')
    print(f'  icon.png: {img.size}, mode={img.mode}')
    # 验证中心像素
    cx, cy = img.width // 2, img.height // 2
    print(f'  中心像素: {img.getpixel((cx, cy))}')

    # ── 生成多分辨率 ICO（main.js BrowserWindow 用）──
    print('生成 icon.ico (256x)...')
    sizes = [256, 48, 32, 16]
    png_entries = []
    for s in sizes:
        # 直接从 1024 缩放到目标尺寸
        resized = img.resize((s, s), Image.LANCZOS)
        buf = io.BytesIO()
        resized.save(buf, 'PNG')
        png_entries.append((s, buf.getvalue()))
        print(f'  {s}x{s}: {len(buf.getvalue()) / 1024:.0f}KB')

    ico_data = pack_ico_simple(png_entries)
    with open('build/icon.ico', 'wb') as f:
        f.write(ico_data)
    print(f'  icon.ico: {len(ico_data) / 1024:.0f}KB, {len(png_entries)} 尺寸')

    # ── 验证 ICO ──
    with Image.open('build/icon.ico') as v:
        print(f'  验证: {v.size}, mode={v.mode}')

    print('完成!')


if __name__ == '__main__':
    main()
