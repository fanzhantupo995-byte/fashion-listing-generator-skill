#!/usr/bin/env python3
"""参考详情页长图版块拆分工具（通用，不含任何商品/品类硬编码）

用法：
  扫描：python3 split_sections.py scan <长图路径> <输出目录>
        产出 nav.jpg（带 y 坐标的导航图）和 candidates.txt（候选分隔带）
  切图：python3 split_sections.py cut <长图路径> <输出目录> <边界文件>
        边界文件每行：模块名<TAB>起始y<TAB>结束y   （# 开头为注释）

设计约束：
  - scan 只做定位辅助，绝不用固定高度等分作为最终拆分结果
  - 最终切点由 AI 看版块内容后给出，落在版块分隔带上
"""
import sys
import json
from pathlib import Path
from PIL import Image, ImageDraw

Image.MAX_IMAGE_PIXELS = None


def load(path):
    im = Image.open(path)
    return im.convert("RGB") if im.mode != "RGB" else im


def scan(img_path, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    im = load(img_path)
    w, h = im.size

    # 1) 行特征分析：找近似纯色的横向分隔带（底色切换/留白）
    small = im.resize((160, h))
    px = small.load()
    bands, start = [], None
    for y in range(h):
        row = [px[x, y] for x in range(0, 160, 8)]
        avg = tuple(sum(c[i] for c in row) / len(row) for i in range(3))
        var = sum(sum((c[i] - avg[i]) ** 2 for i in range(3)) for c in row) / len(row)
        flat = var < 60
        if flat and start is None:
            start = y
        elif not flat and start is not None:
            if y - start >= h * 0.004:  # 至少占全图 0.4% 高度才算分隔带
                bands.append((start, y))
            start = None
    if start is not None and h - start >= h * 0.004:
        bands.append((start, h))

    with open(out / "candidates.txt", "w") as f:
        f.write(f"原图尺寸 {w} x {h}\n候选分隔带（start-end，中点可作切点）：\n")
        for a, b in bands:
            f.write(f"{a}-{b}  中点 {(a + b) // 2}\n")

    # 2) 导航图：分段缩略 + y 坐标标注，仅用于定位，不是拆分结果
    seg = 1700
    n = (h + seg - 1) // seg
    tw = 320
    cols = 6
    rows = (n + cols - 1) // cols
    th = int(tw * seg / w)
    label = 26
    nav = Image.new("RGB", (cols * tw, rows * (th + label)), "white")
    d = ImageDraw.Draw(nav)
    for i in range(n):
        top = i * seg
        piece = im.crop((0, top, w, min(h, top + seg)))
        ph = int(tw * piece.size[1] / w)
        piece = piece.resize((tw, ph))
        x, y = (i % cols) * tw, (i // cols) * (th + label)
        nav.paste(piece, (x, y + label))
        d.text((x + 6, y + 8), f"#{i+1:02d}  y={top}-{min(h, top+seg)}", fill="black")
    nav.save(out / "nav.jpg", quality=88)
    print(json.dumps({"width": w, "height": h, "bands": len(bands),
                      "nav": str(out / "nav.jpg"), "candidates": str(out / "candidates.txt")},
                     ensure_ascii=False))


def cut(img_path, out_dir, bounds_file):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    im = load(img_path)
    w, h = im.size
    lines = [l.strip() for l in Path(bounds_file).read_text().splitlines()
             if l.strip() and not l.startswith("#")]
    manifest = []
    for i, line in enumerate(lines, 1):
        name, a, b = line.split("\t")
        a, b = int(a), int(b)
        if not (0 <= a < b <= h):
            raise ValueError(f"第{i}行边界非法：{a}-{b}，原图高度 {h}")
        fn = f"{i:02d}-{name}.jpg"
        im.crop((0, a, w, b)).save(out / fn, quality=92)
        manifest.append({"index": i, "name": name, "start": a, "end": b,
                         "height": b - a, "file": fn})
    (out / "sections.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "scan":
        scan(sys.argv[2], sys.argv[3])
    elif cmd == "cut":
        cut(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        sys.exit(__doc__)
