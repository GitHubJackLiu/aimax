# -*- coding: utf-8 -*-
"""
生成 AI MAX 的 exe 图标（多尺寸 .ico）并输出预览图。

画法不在本文件里，而是直接调用 aimax.logo_image —— 保证 exe 图标和托盘图标
出自同一段代码，以后改造型只改 aimax.py 一处。

用法：  .venv\\Scripts\\python.exe _mkicon.py app.ico
"""
import os
import sys

from PIL import Image

import aimax

HERE = os.path.dirname(os.path.abspath(__file__))

# Windows 会按显示场景挑最合适的一档，把小尺寸也塞进去才不会糊
SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]


def frames():
    # 16/24px 用单行「AI」：两行字在这个尺寸下已经不可辨认
    return [aimax.logo_image(s, two_line=s > 24) for s in SIZES]


def build_ico(path):
    f = frames()
    f[-1].save(path, format="ICO",
               sizes=[(s, s) for s in SIZES],
               append_images=f[:-1])


def preview(path):
    """对照图：一枚 256 大图看造型，右侧各档尺寸看缩略后还认不认得出。"""
    canvas = Image.new("RGBA", (780, 430), (245, 246, 250, 255))
    canvas.alpha_composite(aimax.logo_image(256, True), (18, 22))
    x = 300
    for s in (128, 64, 48, 32, 24, 16):
        canvas.alpha_composite(aimax.logo_image(s, s > 24), (x, 22))
        x += s + 16
    band = Image.new("RGBA", (780, 120), (26, 26, 29, 255))
    x = 300
    for s in (64, 48, 32, 24, 16):
        band.alpha_composite(aimax.logo_image(s, s > 24), (x, 22))
        x += s + 16
    canvas.alpha_composite(band, (0, 300))
    canvas.resize((1560, 860), Image.LANCZOS).save(path)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "app.ico"
    build_ico(out)
    preview(os.path.join(HERE, "_icon_preview2x.png"))
    print(f"ico {out}  {os.path.getsize(out)} bytes  sizes={SIZES}")
