# -*- coding: utf-8 -*-
"""
DeepSeek 桌面悬浮窗
--------------------------------------------------
用 pywebview + WebView2 建一个独立的顶层窗口加载 chat.deepseek.com。
独立窗口走的是顶级导航（top-level navigation），不受站点
`Content-Security-Policy: frame-ancestors 'none'` 的限制，因此能正常显示。

特性：
  * 无边框 / 置顶 / 贴屏幕右侧
  * 顶部 8px 拖动条 + 页面空白处可拖
  * 登录态持久化（private_mode=False + storage_path）
  * 全局热键 Alt+D 显示 / 隐藏
  * 系统托盘（显示隐藏、置顶开关、重新加载、退出）
  * 位置 / 尺寸 / 置顶状态自动记忆
  * 单实例
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
import json
import os
import re
import sys
import tempfile
import threading
import time

import webview

# ---------------------------------------------------------------- 常量

APP_NAME = "AIMAX"             # 程序标识：数据目录名、单实例互斥体、托盘名（不要带空格）
TITLE = "AI MAX"               # 窗口标题固定，单实例查找依赖它（不要随站点变化）
VERSION = "1.1.9"              # 版本号，启动动画与托盘提示里会显示
OLD_APP_NAME = "DeepSeekFloat"  # 旧版数据目录名，首次启动时把数据搬过来
URL = "https://chat.deepseek.com"

# 首次运行 / 配置损坏时的兜底站点列表
DEFAULT_SITES = [
    {"name": "DeepSeek", "url": URL},
]

# 「添加站点」对话框里的快捷预设：点一下就填好名称和网址
PRESETS = [
    ("DeepSeek", "https://chat.deepseek.com"),
    ("Kimi", "https://www.kimi.com"),
    ("豆包", "https://www.doubao.com/chat/"),
    ("通义", "https://tongyi.aliyun.com/qianwen/"),
    ("元宝", "https://yuanbao.tencent.com/chat"),
    ("ChatGPT", "https://chatgpt.com"),
    ("Claude", "https://claude.ai"),
    ("Gemini", "https://gemini.google.com/app"),
    ("Grok", "https://grok.com"),
]

# 标题栏标签（tab）尺寸
TAB_MIN_W = 50         # 单个标签最小宽度
TAB_MAX_W = 132        # 单个标签最大宽度，超长名称用省略号
TAB_PAD = 12           # 标签内左右留白
TAB_GAP = 4            # 标签间距
MORE_W = 26            # "⋯" 溢出指示占位宽度

SPLASH_SECONDS = float(os.environ.get("DSFLOAT_SPLASH", "2.6"))  # 启动动画时长，播完再跳转真实站点

# 启动动画：纯内联 HTML/CSS/JS，由 WebView 本地载入（无网络请求）。
# 播完后由 _splash_then_site() 导航到 URL。
SPLASH_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  html, body { width:100%; height:100%; overflow:hidden; }
  body {
    position:relative;
    background:
      radial-gradient(120% 70% at 50% 8%, #101c42 0%, #0a0d1a 46%, #04050a 100%);
    font-family:"Segoe UI","Microsoft YaHei",system-ui,-apple-system,sans-serif;
    color:#fff;
    display:flex; align-items:center; justify-content:center;
    user-select:none; -webkit-user-select:none;
    cursor:default;
  }
  .grid {
    position:absolute; inset:-10%;
    background-image:
      linear-gradient(rgba(122,155,255,.20) 1px, transparent 1px),
      linear-gradient(90deg, rgba(122,155,255,.20) 1px, transparent 1px);
    background-size:32px 32px;
    -webkit-mask-image:radial-gradient(circle at 50% 46%, #000 0%, transparent 70%);
    mask-image:radial-gradient(circle at 50% 46%, #000 0%, transparent 70%);
    animation:gridIn 1.6s cubic-bezier(.2,.8,.3,1) both;
  }
  @keyframes gridIn { from{opacity:0; transform:scale(1.18)} to{opacity:.42; transform:scale(1)} }

  .blob { position:absolute; border-radius:50%; filter:blur(48px); }
  .b1 { width:360px; height:360px; background:radial-gradient(circle,#3b6bff 0%,rgba(59,107,255,0) 70%);
        animation:breathe 3.4s ease-in-out infinite; }
  .b2 { width:260px; height:260px; background:radial-gradient(circle,#8b5cf6 0%,rgba(139,92,246,0) 68%);
        animation:breathe 3.4s ease-in-out infinite; animation-delay:-1.5s; }
  @keyframes breathe { 0%,100%{transform:scale(.82); opacity:.40} 50%{transform:scale(1.16); opacity:.92} }

  .p { position:absolute; border-radius:50%; background:#a8c2ff;
       box-shadow:0 0 8px 1px rgba(110,150,255,.85); animation:rise linear infinite; }
  @keyframes rise { from{transform:translateY(0) scale(.6); opacity:0}
                    12%{opacity:1} to{transform:translateY(-95vh) scale(1); opacity:0} }

  .wrap { position:relative; z-index:3; display:flex; flex-direction:column;
          align-items:center; padding:0 26px; width:100%; }

  .logo {
    font-size:53px; font-weight:800; letter-spacing:.5px; line-height:1.1;
    white-space:nowrap;
    background:linear-gradient(100deg,#6ea8ff 0%,#a9c3ff 18%,#ffffff 38%,#d9ccff 56%,#8b5cf6 76%,#6ea8ff 100%);
    background-size:260% 100%;
    -webkit-background-clip:text; background-clip:text;
    color:transparent; -webkit-text-fill-color:transparent;
    animation:flow 3.6s linear infinite, logoIn .95s cubic-bezier(.2,.8,.3,1) both;
  }
  @keyframes flow { from{background-position:0% 50%} to{background-position:260% 50%} }
  @keyframes logoIn {
    from { opacity:0; transform:translateY(16px) scale(.93); letter-spacing:6px; }
    to   { opacity:1; transform:none; letter-spacing:.5px; }
  }

  .rule { height:1px; width:0; margin:16px 0 15px;
          background:linear-gradient(90deg,transparent,#6d8cff,#c8d4ff,#6d8cff,transparent);
          box-shadow:0 0 12px rgba(110,150,255,.9);
          animation:ruleGrow .85s .35s cubic-bezier(.2,.8,.3,1) forwards; }
  @keyframes ruleGrow { to { width:184px; } }

  .slogan { display:flex; font-size:25px; font-weight:600; color:#eaf0ff;
            text-shadow:0 0 22px rgba(120,160,255,.55); }
  .slogan span { display:inline-block; opacity:0;
                 transform:translateY(12px) scale(.9); filter:blur(4px);
                 animation:charIn .62s cubic-bezier(.2,.8,.3,1) forwards; }
  @keyframes charIn { to { opacity:1; transform:none; filter:none; } }

  .sub { margin-top:13px; font-size:9.5px; letter-spacing:.30em; color:#7f8db8;
         text-align:center; opacity:0; animation:fadeUp .8s 1.05s ease-out forwards; }
  @keyframes fadeUp { from{opacity:0; transform:translateY(8px)} to{opacity:1; transform:none} }

  .bar { margin-top:44px; width:196px; height:2px; border-radius:2px;
         background:rgba(255,255,255,.09); overflow:hidden; }
  .bar i { display:block; height:100%; width:0; border-radius:2px;
           background:linear-gradient(90deg,#3b6bff,#a78bfa,#eaf0ff);
           box-shadow:0 0 12px rgba(130,165,255,.95);
           animation:fill 2.35s .12s cubic-bezier(.32,.62,.4,1) forwards; }
  @keyframes fill { from{width:0} to{width:100%} }

  .hint { margin-top:12px; font-size:10.5px; letter-spacing:.16em; color:#66719a;
          opacity:0; animation:fadeUp .7s .5s ease-out forwards; }

  /* 版本号小徽标：启动时一眼能看到当前版本 */
  .ver {
    margin-top:15px; padding:2px 10px; border-radius:20px;
    border:1px solid rgba(122,155,255,.30);
    background:rgba(122,155,255,.08);
    font-size:10px; letter-spacing:.16em; color:#8c97bf;
    opacity:0; animation:fadeUp .7s .9s ease-out forwards;
  }
</style>
</head>
<body>
  <div class="grid"></div>
  <div class="blob b1" style="left:14%;top:26%"></div>
  <div class="blob b2" style="right:8%;bottom:20%"></div>

  <div class="wrap">
    <div class="logo" id="logo">__BRAND__</div>
    <div class="rule"></div>
    <div class="slogan" id="slogan" data-text="__SLOGAN__"></div>
    <div class="sub">CHANGING THE WORLD WITH INTELLIGENCE</div>
    <div class="bar"><i></i></div>
    <div class="hint">正在唤醒 · WAKING UP</div>
    <div class="ver">v__VERSION__</div>
  </div>

<script>
(function () {
  // 名字长度不定，按实际文字宽度自动缩字号，长名字也不会被裁掉
  var logo = document.getElementById('logo');
  var box = document.querySelector('.wrap');
  function textWidth(txt, fs) {
    var s = document.createElement('span');
    s.style.cssText = 'position:absolute;visibility:hidden;white-space:nowrap;' +
                      'font-weight:800;letter-spacing:.5px;font-size:' + fs + 'px';
    s.textContent = txt;
    document.body.appendChild(s);
    var w = s.offsetWidth;
    document.body.removeChild(s);
    return w;
  }
  var avail = Math.max(120, (box ? box.clientWidth : 320) - 36);
  var fs = 53;
  while (fs > 18 && textWidth(logo.textContent, fs) > avail) {
    fs -= 2;
  }
  logo.style.fontSize = fs + 'px';

  // 逐字浮现
  var text = document.getElementById('slogan').getAttribute('data-text') || '';
  var sbox = document.getElementById('slogan');
  for (var i = 0; i < text.length; i++) {
    var s = document.createElement('span');
    s.textContent = text[i];
    s.style.animationDelay = (0.55 + i * 0.13) + 's';
    s.style.marginRight = (i === text.length - 1) ? '0' : '0.30em';
    sbox.appendChild(s);
  }
  // 上升粒子
  for (var k = 0; k < 26; k++) {
    var p = document.createElement('i');
    p.className = 'p';
    var size = 1 + Math.random() * 2.2;
    var dur = 2.2 + Math.random() * 3.2;
    p.style.width = size + 'px';
    p.style.height = size + 'px';
    p.style.left = (Math.random() * 100) + '%';
    p.style.bottom = '-8px';
    p.style.animationDuration = dur + 's';
    p.style.animationDelay = (-Math.random() * dur) + 's';
    p.style.opacity = 0.25 + Math.random() * 0.6;
    document.body.appendChild(p);
  }
})();
</script>
</body>
</html>
"""

APP_DIR = os.environ.get("AIMAX_DATA") or os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
OLD_APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), OLD_APP_NAME)
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
WEBVIEW_DIR = os.path.join(APP_DIR, "webview")
LOG_PATH = os.path.join(APP_DIR, "run.log")


def migrate_legacy_data() -> None:
    """
    首次以 AI MAX 身份启动时，把旧目录（DeepSeekFloat）里的配置和 WebView 存储搬过来。

    搬的是 config.json 和 webview/ 两样，后者存着各站点的登录态 —— 不搬的话
    用户升级后所有站点都得重新登录一次。旧目录保留不动，方便回退到旧 exe。
    """
    import shutil

    try:
        if os.path.abspath(OLD_APP_DIR) == os.path.abspath(APP_DIR):
            return
        if not os.path.isdir(OLD_APP_DIR):
            return
        if os.path.exists(CONFIG_PATH):      # 已经迁过了，别覆盖用户后来改的配置
            return
        os.makedirs(APP_DIR, exist_ok=True)
        for name in ("config.json", "webview"):
            src = os.path.join(OLD_APP_DIR, name)
            dst = os.path.join(APP_DIR, name)
            if not os.path.exists(src) or os.path.exists(dst):
                continue
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
    except Exception:
        pass        # 迁移失败不算致命：新目录会用默认配置，用户重新登录即可

DEFAULTS = {
    "width": 444,          # 视觉尺寸（不含 Windows 的不可见边框）
    "height": 781,
    "x": None,
    "y": None,
    "on_top": True,
    "hotkey": "alt+d",
    "drag_bar": 8,
    "auto_hide": True,     # 贴到屏幕顶边时自动收起
    "sites": None,         # 站点列表，None 表示用 DEFAULT_SITES
    "active": 0,           # 当前站点下标
    "theme": "dark",       # 界面配色：dark / light（托盘里可切换）
    "brand": None,         # 启动动画里的名字，None 表示用 DEFAULT_BRAND
    "update_url": "",      # 更新清单直链；留空表示不检查更新
}

# 启动动画上显示的名字（改这里就能全量替换，不需要动 HTML）
DEFAULT_BRAND = "AI MAX"
DEFAULT_SLOGAN = "改变世界"

# ---------------------------------------------------------------- 界面配色
#
# 只影响"自己的壳"：标题栏、标签、右键菜单、对话框。
# 网页内容本身由各站点自己决定（Kimi / 豆包 / 通义这些网页本身就是白底的，
# 深色外壳套白底页面看着突兀，所以给一个浅色壳子可选）。

THEMES = {
    "dark": {
        "bg": (26, 26, 29),          # 标题栏底色
        "fg": (190, 190, 198),       # 普通文字/图标
        "dim": (138, 140, 150),      # 未选中标签
        "strong": (255, 255, 255),   # 选中标签、悬停文字
        "tab_sel": (46, 47, 56),     # 选中标签底
        "tab_hov": (37, 38, 45),     # 悬停标签底
        "hov": (58, 58, 66),         # 窗口按钮悬停
        "hov_red": (232, 72, 77),    # 关闭按钮悬停
        "accent": (77, 107, 254),    # 强调色（选中下划线/主按钮）
        "panel": (30, 31, 36),       # 对话框底
        "bar": (38, 39, 45),         # 对话框标题栏
        "field": (44, 45, 52),       # 输入框底
        "edge": (64, 66, 78),        # 描边/分隔线
        "menu": (32, 33, 38),        # 右键菜单底
        "menu_sel": (54, 56, 66),    # 右键菜单悬停
        "menu_text": (222, 224, 232),
    },
    "light": {
        "bg": (247, 247, 250),
        "fg": (74, 76, 86),
        "dim": (132, 134, 144),
        "strong": (22, 23, 28),
        "tab_sel": (231, 232, 240),
        "tab_hov": (238, 239, 245),
        "hov": (226, 227, 235),
        "hov_red": (232, 72, 77),
        "accent": (47, 88, 240),
        "panel": (250, 250, 252),
        "bar": (240, 241, 246),
        "field": (255, 255, 255),
        "edge": (205, 207, 218),
        "menu": (252, 252, 254),
        "menu_sel": (232, 234, 243),
        "menu_text": (38, 40, 48),
    },
}

# create_window 请求的尺寸与实际外框之间的固定差值（实测 460x820 -> 444x781）
CHROME_DW = 16
CHROME_DH = 39

# 贴顶自动隐藏
AUTO_HIDE_PEEK = 6        # 收起后仍露出屏幕的像素高度
AUTO_HIDE_DELAY = 0.45    # 鼠标离开多久后收起
AUTO_HIDE_POLL = 0.08     # 轮询间隔

# 窗口句柄与状态
state = {
    "hwnd": None,
    "on_top": True,
    "tray": None,
    "win": None,
    "last_pos": None,
    "dragbar": None,   # 必须持有引用，否则 WNDPROC 回调会被 GC 回收
    "autohide": None,
    "auto_hide": True,
    "auto_hide_hidden": False,
    "maximized": False,      # 是否处于最大化（由标题栏按钮控制）
    "restore_rect": None,    # 最大化前的窗口矩形 (x, y, w, h)
    "max_btn": None,         # 最大化按钮控件引用
    "form": None,            # 主窗体的 WinForms 对象
    "tab_strip": None,       # 标题栏标签条（自绘 Panel）
    "nav_strip": None,       # 标题栏左侧导航按钮（自绘 Panel）
    "sites": None,           # 站点列表内存缓存（避免重绘时反复读磁盘）
    "active": 0,             # 当前站点下标
    "ctx_menu": None,        # 自绘弹出菜单窗口（防 GC）
    "menu_open": False,      # 菜单是否正在显示（显示期间别让窗口自动收起）
    "theme": "dark",         # 当前配色名
    "palette": None,         # 配色缓存（System.Drawing.Color 字典）
    "native_idx": None,       # 复用 pywebview 自带 WebView2 的站点下标
    "title_hold": None,      # 标题栏控件引用（换配色时要挨个改色）
    # ---- 更新下载进度（标题栏圆环 + 悬停详情面板）----
    "dl_ind": None,          # 圆形进度按钮（自绘 Panel）
    "dl_progress": None,     # 下载状态字典（got/total/speed/eta/cancel…）
    "dl_visible": False,     # 是否正在下载
    "dl_timer": None,        # 刷新圆环的定时器
    "dl_popup": None,        # 悬停详情面板（Form）
    "dl_watch": None,        # 鼠标离开自动收起的定时器
}
_lock = threading.Lock()


def log(msg: str) -> None:
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


# ---------------------------------------------------------------- 配置

def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def save_config(cfg: dict) -> None:
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except Exception as e:
        log(f"save_config failed: {e}")


# ---------------------------------------------------------------- 启动动画文案

def brand_name() -> str:
    """启动动画上显示的名字。配置里 brand 优先，没配就用 DEFAULT_BRAND。"""
    cfg = load_config()
    b = str(cfg.get("brand") or "").strip()
    return (b or DEFAULT_BRAND)[:24]


def splash_html() -> str:
    """把品牌名/标语/版本号填进启动动画模板（用替换而不是 format，避免 CSS 里的 % 出问题）。"""
    from html import escape
    return (SPLASH_HTML
            .replace("__BRAND__", escape(brand_name(), quote=True))
            .replace("__SLOGAN__", escape(DEFAULT_SLOGAN, quote=True))
            .replace("__VERSION__", escape(VERSION, quote=True)))


# ---------------------------------------------------------------- 配色

def theme_name() -> str:
    t = state.get("theme") or DEFAULTS["theme"]
    return t if t in THEMES else "dark"


def palette() -> dict:
    """当前配色的 System.Drawing.Color 字典（缓存，重绘时高频调用）。"""
    p = state.get("palette")
    if p is None:
        from System.Drawing import Color
        spec = THEMES[theme_name()]
        p = {k: Color.FromArgb(*v) for k, v in spec.items()}
        state["palette"] = p
    return p


def ui_scale() -> float:
    """
    当前显示缩放（96 DPI = 1.0）。

    自绘控件的位置都是按 96 DPI 的像素写的，而 9pt 字体的实际像素高度会
    跟着 DPI 变（4K + 200% 下翻倍），写死的坐标跟不上，控件就会互相压住。
    所以这类窗口一律用这个系数把坐标缩放一遍。

    进程若不感知 DPI，屏幕 DC 会返回 96（系统整窗位图拉伸），返回 1.0 同样正确。
    """
    try:
        from System.Drawing import Graphics
        from System import IntPtr
        g = Graphics.FromHwnd(IntPtr.Zero)
        try:
            return max(1.0, float(g.DpiX) / 96.0)
        finally:
            g.Dispose()
    except Exception:
        return 1.0


def set_theme(name: str) -> None:
    """切换深/浅配色：改标题栏、标签、按钮、对话框，并写回配置。"""
    name = name if name in THEMES else "dark"
    if name == theme_name() and state.get("palette") is not None:
        return
    state["theme"] = name
    state["palette"] = None            # 让 palette() 重新生成
    cfg = load_config()
    cfg["theme"] = name
    save_config(cfg)
    apply_chrome_theme()
    log(f"theme -> {name}")


def toggle_theme() -> None:
    set_theme("light" if theme_name() == "dark" else "dark")


def apply_chrome_theme() -> None:
    """把当前配色刷到已经建好的标题栏控件上（必须走 GUI 线程）。"""
    P = palette()
    form = state.get("form")
    hold = state.get("title_hold") or {}

    def run():
        try:
            if form is not None:
                form.BackColor = P["bg"]
            strip = hold.get("strip")
            if strip is not None:
                strip.BackColor = P["bg"]
                strip.Invalidate()
            nav = hold.get("nav")
            if nav is not None:
                nav.BackColor = P["bg"]
                nav.Invalidate()
            dl = hold.get("dl")
            if dl is not None:
                dl.BackColor = P["bg"]
                dl.Invalidate()
            for key in ("min", "max", "close"):
                b = hold.get(key)
                if b is not None:
                    b.BackColor = P["bg"]
                    b.ForeColor = P["fg"]
        except Exception as e:
            log(f"apply_chrome_theme failed: {e!r}")

    invoke_gui(run)


# ---------------------------------------------------------------- 弹出菜单
#
# 不用 ContextMenuStrip：WinForms 的菜单外观由渲染器决定，而这一版 pythonnet
# 不支持覆写 .NET 虚方法（ToolStripRenderer.OnRender* 里打了日志一次都没被调用），
# 于是深色外壳里会弹出系统浅色菜单 —— 就是"风格不搭"的根因。
# 这里索性自己画：无边框小窗口 + 自绘条目，配色全部走 palette()。

MENU_ITEM_H = 30
MENU_SEP_H = 9
MENU_PAD_Y = 6
MENU_PAD_X = 16
MENU_MIN_W = 156

# 全局界面字号（pt）。自绘窗口都按它建字体：字号不变、像素高度随 DPI 变，
# 所以布局坐标要另外乘 ui_scale()，两者配合才能在高分屏下不错位。
UI_FONT_PT = 9.0


def close_popup_menu() -> None:
    f = state.get("ctx_menu")
    state["ctx_menu"] = None
    state["menu_open"] = False
    if f is None:
        return
    try:
        f.Close()
    except Exception:
        pass
    try:
        f.Dispose()
    except Exception:
        pass


def show_popup_menu(anchor, x: int, y: int, entries) -> None:
    """
    在 anchor 控件的 (x, y) 位置弹出自绘菜单。
    entries: [(文字, 回调), None(分隔线), ...]，回调在菜单关闭之后执行。
    """
    from System.Windows.Forms import (
        Form, FormBorderStyle, FormStartPosition, TextRenderer,
        TextFormatFlags, Keys, Control,
    )
    from System.Drawing import (
        Point, Size, Rectangle, SolidBrush, Pen, Font, FontStyle, Color,
    )
    from System.Drawing.Drawing2D import GraphicsPath, SmoothingMode

    # 连点右键时先关掉上一个，避免叠出多个菜单
    close_popup_menu()

    P = palette()
    try:
        font = Font("Segoe UI", 9.0, FontStyle.Regular)
    except Exception:
        font = None

    tw, n_sep, n_item = 0, 0, 0
    for en in entries:
        if en is None:
            n_sep += 1
            continue
        n_item += 1
        try:
            wpx = TextRenderer.MeasureText(en[0], font).Width if font else len(en[0]) * 9
        except Exception:
            wpx = len(en[0]) * 9
        tw = max(tw, int(wpx))
    w = max(MENU_MIN_W, tw + MENU_PAD_X * 2 + 8)
    h = n_item * MENU_ITEM_H + n_sep * MENU_SEP_H + MENU_PAD_Y * 2

    # 屏幕坐标：贴边时往里挪，别弹到屏幕外
    try:
        p = anchor.PointToScreen(Point(int(x), int(y) + 4))
        sx, sy = int(p.X), int(p.Y)
    except Exception:
        sx, sy = int(x), int(y)
    ml, mt, mr, mb = monitor_work_area(sx, sy)
    sx = max(ml + 2, min(sx, mr - w - 2))
    sy = max(mt + 2, min(sy, mb - h - 2))

    form = Form()
    form.FormBorderStyle = getattr(FormBorderStyle, "None")   # None 是 Python 关键字
    form.StartPosition = FormStartPosition.Manual
    form.ShowInTaskbar = False
    form.ShowIcon = False
    form.TopMost = True          # 主窗置顶，菜单不置顶会被网页盖住
    form.KeyPreview = True
    form.ClientSize = Size(int(w), int(h))
    form.Location = Point(int(sx), int(sy))
    form.BackColor = P["panel"]
    try:
        form.Font = Font("Segoe UI", 9.0, FontStyle.Regular)
    except Exception:
        pass

    rows = []
    cy = MENU_PAD_Y
    for en in entries:
        if en is None:
            rows.append(("sep", "", None, Rectangle(0, cy, int(w), MENU_SEP_H)))
            cy += MENU_SEP_H
        else:
            rows.append(("item", en[0], en[1], Rectangle(0, cy, int(w), MENU_ITEM_H)))
            cy += MENU_ITEM_H

    ui = {"hot": -1}

    def rounded(rect, rad):
        path = GraphicsPath()
        d = rad * 2
        path.AddArc(rect.X, rect.Y, d, d, 180, 90)
        path.AddArc(rect.Right - d, rect.Y, d, d, 270, 90)
        path.AddArc(rect.Right - d, rect.Bottom - d, d, d, 0, 90)
        path.AddArc(rect.X, rect.Bottom - d, d, d, 90, 90)
        path.CloseFigure()
        return path

    def on_paint(s_, e):
        g = e.Graphics
        try:
            g.SmoothingMode = SmoothingMode.AntiAlias
        except Exception:
            pass
        g.Clear(P["panel"])
        # 1px 边框：无边框窗口没有系统轮廓，描一圈才看得出边界
        pen = Pen(P["edge"], 1)
        try:
            g.DrawRectangle(pen, 0, 0, int(w) - 1, int(h) - 1)
        finally:
            pen.Dispose()

        for i, (kind, text, _cb, rect) in enumerate(rows):
            if kind == "sep":
                sp = Pen(P["edge"], 1)
                try:
                    yy = rect.Y + rect.Height // 2
                    g.DrawLine(sp, MENU_PAD_X - 4, yy, int(w) - MENU_PAD_X + 4, yy)
                finally:
                    sp.Dispose()
                continue
            if i == ui["hot"]:
                hl = Rectangle(3, rect.Y + 1, int(w) - 7, rect.Height - 2)
                path = rounded(hl, 5)
                br = SolidBrush(P["menu_sel"])
                try:
                    g.FillPath(br, path)
                finally:
                    br.Dispose()
                    path.Dispose()
            col = P["strong"] if i == ui["hot"] else P["menu_text"]
            TextRenderer.DrawText(
                g, text, font,
                Rectangle(MENU_PAD_X, rect.Y, int(w) - MENU_PAD_X * 2, rect.Height), col,
                TextFormatFlags.Left | TextFormatFlags.VerticalCenter
                | TextFormatFlags.NoPrefix | TextFormatFlags.EndEllipsis,
            )

    def item_at(yy):
        for i, (kind, _t, _c, rect) in enumerate(rows):
            if kind == "item" and rect.Y <= yy < rect.Bottom:
                return i
        return -1

    def on_move(s_, e):
        i = item_at(e.Y)
        if i != ui["hot"]:
            ui["hot"] = i
            form.Invalidate()

    def on_leave(s_, e):
        if ui["hot"] != -1:
            ui["hot"] = -1
            form.Invalidate()

    def on_click(s_, e):
        i = item_at(e.Y)
        if i < 0:
            return
        cb = rows[i][2]
        close_popup_menu()
        if cb:
            try:
                cb()
            except Exception as ex:
                log(f"menu action failed: {ex!r}")

    def on_key(s_, e):
        if e.KeyCode == Keys.Escape:
            close_popup_menu()

    form.Paint += on_paint
    form.MouseMove += on_move
    form.MouseLeave += on_leave
    form.MouseClick += on_click
    form.KeyDown += on_key
    form.Deactivate += (lambda s_, e: close_popup_menu())

    state["ctx_menu"] = form
    state["menu_open"] = True
    try:
        form.Show()
        form.Activate()
        ph = int(form.Handle.ToInt64())      # Handle 是 IntPtr，不能直接 int()
        set_tool_window(ph)
        enable_round_corners(ph)
    except Exception as e:
        log(f"popup menu failed: {e!r}")


# ---------------------------------------------------------------- 站点（多 AI 站点 / 标签）

def _clean_sites(raw) -> list[dict]:
    """把配置里的站点数组清洗成 [{name, url}]，非法项直接丢掉。"""
    out: list[dict] = []
    if isinstance(raw, list):
        for s in raw:
            if not isinstance(s, dict):
                continue
            url = str(s.get("url") or "").strip()
            if not url:
                continue
            if not url.lower().startswith(("http://", "https://")):
                url = "https://" + url
            name = str(s.get("name") or "").strip() or url.split("//")[-1].split("/")[0]
            out.append({"name": name, "url": url})
    return out


def sites() -> list[dict]:
    """站点列表（内存缓存，标题栏重绘时会高频读取）。"""
    if state.get("sites") is None:
        cfg = load_config()
        items = _clean_sites(cfg.get("sites")) or [dict(DEFAULT_SITES[0])]
        try:
            act = int(cfg.get("active") or 0)
        except Exception:
            act = 0
        state["sites"] = items
        state["active"] = max(0, min(act, len(items) - 1))
    return state["sites"]


def active_index() -> int:
    items = sites()
    try:
        i = int(state.get("active") or 0)
    except Exception:
        i = 0
    return max(0, min(i, len(items) - 1))


def commit_sites(items: list[dict], active: int) -> None:
    """写回配置并刷新标题栏缓存的标签。"""
    items = _clean_sites(items) or [dict(DEFAULT_SITES[0])]
    active = max(0, min(int(active), len(items) - 1))
    state["sites"] = items
    state["active"] = active
    cfg = load_config()
    cfg["sites"] = items
    cfg["active"] = active
    save_config(cfg)
    refresh_tabs()


def refresh_tabs() -> None:
    strip = state.get("tab_strip")
    if not strip:
        return
    try:
        from System import Action
        strip.Invoke(Action(lambda: strip.Invalidate()))
    except Exception:
        pass


def invoke_gui(fn) -> None:
    """把界面操作丢回 WinForms 线程执行（托盘线程调用对话框时必须走这里）。"""
    form = state.get("form")
    if form is None:
        return
    from System import Action
    try:
        form.BeginInvoke(Action(fn))
    except Exception:
        try:
            form.Invoke(Action(fn))
        except Exception as e:
            log(f"invoke_gui failed: {e!r}")


def load_active_site(force: bool = False) -> None:
    """切到当前站点：只切显示/隐藏，页面不重新加载。"""
    items = sites()
    idx = active_index()
    url = items[idx]["url"]
    log(f"show site -> [{idx}] {items[idx]['name']} {url}")
    if not state.get("win"):
        return
    invoke_gui(lambda: show_site(idx, force))


# ---------------------------------------------------------------- 多站点 WebView
#
# 为什么不是所有标签共用一个 WebView：那样切标签只能 load_url，等于重新打开，
# 搜索结果和滚动位置全丢。这里给每个站点一个独立的 WebView2 实例，
# 切标签只是 Visible 切换，页面根本没重新加载。
#
# 硬约束：同一个用户数据目录在一个进程里只能建一个 CoreWebView2 环境，
# 第二个会报 0x8007139F。所以所有实例都要共用 pywebview 那个控件的环境，
# 通过 EnsureCoreWebView2Async(env) 传进去。

site_views = {}        # 站点下标 -> WebView2 控件
_view_hooks = []       # 事件回调保引用，否则会被 GC 回收

DESK_WEBVIEW_SETTINGS = {
    "AreDefaultContextMenusEnabled": False,
    "AreBrowserAcceleratorKeysEnabled": False,
    "IsStatusBarEnabled": False,
    "IsSwipeNavigationEnabled": False,
    "IsZoomControlEnabled": True,
    "AreDefaultScriptDialogsEnabled": True,
}


def native_view():
    """pywebview 自己创建的那个 WebView2 控件（给第一个显示的站点复用）。"""
    form = state.get("form")
    if form is None:
        return None
    try:
        v = form.browser.webview
        if v is not None:
            return v
    except Exception:
        pass
    try:
        for c in form.Controls:
            if "WebView2" in type(c).__name__:
                return c
    except Exception:
        pass
    return None


def _view_bg_color():
    P = palette()
    c = P["bg"]
    return c


def _apply_view_settings(core) -> None:
    try:
        st = core.Settings
        for k, v in DESK_WEBVIEW_SETTINGS.items():
            try:
                setattr(st, k, v)
            except Exception:
                pass
    except Exception as e:
        log(f"webview settings failed: {e!r}")


def _hook_view(core) -> None:
    """网页里 target=_blank / window.open 的链接，就在本标签内打开。"""
    def on_new_window(sender, args):
        try:
            uri = str(args.Uri)
            args.Handled = True
            log(f"new window -> same tab: {uri}")
            core.Navigate(uri)
        except Exception as e:
            log(f"new window failed: {e!r}")

    _view_hooks.append(on_new_window)
    try:
        core.NewWindowRequested += on_new_window
    except Exception as e:
        log(f"hook new window failed: {e!r}")

    _hook_nav_events(core)
    _hook_downloads(core, "created")


_nav_hooked = set()      # 已挂过导航事件的 CoreWebView2（用 id 记，对象生命周期与进程相同）


def _hook_nav_events(core) -> None:
    """
    历史记录 / 加载完成 → 重绘后退、前进按钮（能不能点取决于 CanGoBack）。

    注意：第一个站点复用的是 pywebview 自带的控件，它不走 _create_view 的
    初始化回调，所以这个函数必须能被两处调用（_hook_view 与 _view_for）。
    """
    key = id(core)
    if key in _nav_hooked:
        return
    _nav_hooked.add(key)

    def on_nav_changed(sender, args):
        refresh_nav_bar()

    def on_completed(sender, args):
        _clear_splash_history(core)
        refresh_nav_bar()

    _view_hooks.append(on_nav_changed)
    _view_hooks.append(on_completed)
    try:
        core.HistoryChanged += on_nav_changed
    except Exception as e:
        log(f"hook HistoryChanged failed: {e!r}")
    try:
        core.NavigationCompleted += on_completed
    except Exception as e:
        log(f"hook NavigationCompleted failed: {e!r}")


def _clear_splash_history(core) -> None:
    """
    启动动画是用 NavigateToString 塞进同一个 WebView 的，会留在历史记录里，
    于是「后退」能一路退回动画页 —— 那个页面没有任何用处，用户会以为坏了。

    站点第一次加载完成时用 CDP 的 Page.resetNavigationHistory 重置历史，
    动画条目就彻底消失，后退最远只能回到站点的第一条记录。
    """
    if not state.get("pending_hist_reset"):
        return
    state["pending_hist_reset"] = False
    try:
        core.CallDevToolsProtocolMethodAsync("Page.resetNavigationHistory", "{}")
        log("history reset: splash entry removed")
    except Exception as e:
        log(f"history reset failed: {e!r}")


# ---------------------------------------------------------------- 网页下载
#
# pywebview 的 ALLOW_DOWNLOADS 默认是 False，遇到下载直接 args.Cancel = True ——
# 表现就是「点了保存、也选了路径，但什么文件都没落地」。所以这里自己接管：
#   1) 打开 ALLOW_DOWNLOADS，让它别拦；
#   2) 换掉它自带的 on_download_starting（否则会连弹两个另存为）；
#   3) 每个 CoreWebView2 都挂 DownloadStarting：问路径 → 写进 ResultFilePath → 完成提示。
#
# 另外记一份「上次保存到哪」，下次默认打开那个目录，省得每次都翻。

_dl_hooked = set()     # 已挂过 DownloadStarting 的 CoreWebView2（按 id 记）


def downloads_dir() -> str:
    """系统「下载」文件夹，读不到就退回 ~/Downloads。"""
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
        ) as k:
            return str(winreg.QueryValueEx(k, "{374DE290-123F-4565-9164-39C4925E467B}")[0])
    except Exception:
        return os.path.join(os.path.expanduser("~"), "Downloads")


def ask_save_path(suggested: str):
    """另存为对话框。返回绝对路径；用户取消返回 None。"""
    name = os.path.basename(str(suggested or "").split("?")[0]) or "download.bin"

    auto = str(os.environ.get("AIMAX_DL_AUTOSAVE") or "").strip()
    if auto:                       # 自动化测试用：不问，直接落到指定目录
        os.makedirs(auto, exist_ok=True)
        return os.path.join(auto, name)

    from System.Windows.Forms import SaveFileDialog, DialogResult

    dlg = SaveFileDialog()
    dlg.Title = "保存文件"
    dlg.FileName = name
    start = str(load_config().get("save_dir") or "").strip() or downloads_dir()
    if os.path.isdir(start):
        dlg.InitialDirectory = start
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    if ext:
        dlg.Filter = f"{ext.upper()} 文件 (*.{ext})|*.{ext}|所有文件 (*.*)|*.*"
    else:
        dlg.Filter = "所有文件 (*.*)|*.*"
    dlg.RestoreDirectory = True
    dlg.OverwritePrompt = True

    form = state.get("form")
    try:
        res = dlg.ShowDialog(form) if form is not None else dlg.ShowDialog()
    except Exception as e:
        log(f"save dialog failed: {e!r}")
        return None
    if res != DialogResult.OK:
        return None
    path = str(dlg.FileName or "").strip()
    if not path:
        return None
    cfg = load_config()
    cfg["save_dir"] = os.path.dirname(path)
    save_config(cfg)
    return path


def on_download_starting(args) -> None:
    """接管一次下载：挑路径 → 交给 WebView2 写 → 完成/中断都记日志并提示。"""
    try:
        op = args.DownloadOperation
        path = ask_save_path(str(args.ResultFilePath or ""))
        if not path:
            args.Cancel = True
            log("download: cancelled by user")
            return
        args.Handled = True                 # 关掉 WebView2 自带的下载条
        args.ResultFilePath = path
        log(f"download -> {path}")

        def on_state(sender, e):
            try:
                st = str(op.State)
                if "Completed" in st:
                    log(f"download done: {path} exists={os.path.exists(path)}")
                    toast(f"已保存 {os.path.basename(path)}")
                elif "Interrupted" in st:
                    log(f"download interrupted: {op.InterruptedKind}")
                    toast("下载失败，请重试")
            except Exception as ex:
                log(f"download state failed: {ex!r}")

        _view_hooks.append(on_state)
        op.StateChanged += on_state
    except Exception as e:
        log(f"download handling failed: {e!r}")
        try:
            args.Cancel = True
        except Exception:
            pass


def _hook_downloads(core, tag: str = "view") -> None:
    key = id(core)
    if key in _dl_hooked:
        return
    _dl_hooked.add(key)

    def on_starting(sender, args):
        on_download_starting(args)

    _view_hooks.append(on_starting)
    try:
        core.DownloadStarting += on_starting
        log(f"download hook ready ({tag})")
    except Exception as e:
        log(f"hook DownloadStarting failed: {e!r}")


_pv_patched = False    # pywebview 自带的下载回调是否已换成我们的


def patch_pywebview_download() -> None:
    """把 pywebview 自带的 on_download_starting 换成我们的，避免两个对话框。"""
    global _pv_patched
    try:
        from webview.platforms import edgechromium as _ec
        # pywebview 6.x 里宿主类叫 EdgeChrome（老版本叫 BrowserView），两个都试一下
        cls = getattr(_ec, "EdgeChrome", None) or getattr(_ec, "BrowserView", None)
        if cls is None:
            raise AttributeError("找不到 edgechromium 宿主类")
        cls.on_download_starting = lambda self, sender, args: on_download_starting(args)
        _pv_patched = True
        log(f"pywebview download handler replaced ({cls.__name__})")
    except Exception as e:
        log(f"patch pywebview download failed: {e!r}")


def _navigate(view, url: str) -> None:
    from System import Uri
    try:
        if view.CoreWebView2 is None:      # 还没初始化好，等 on_init 再导航
            return
    except Exception:
        return
    try:
        view.Source = Uri(url)
    except Exception as e:
        log(f"navigate failed: {e!r}")


def _create_view(idx: int):
    from System.Windows.Forms import DockStyle
    from Microsoft.Web.WebView2.WinForms import WebView2

    form = state.get("form")
    native = native_view()
    env = None
    try:
        env = native.CoreWebView2.Environment
    except Exception:
        env = None
    if env is None:
        # 环境拿不到就别硬建：再建一个会跟现有用户数据目录打架
        log("create view skipped: no CoreWebView2Environment")
        return None

    ctl = WebView2()
    ctl.Dock = DockStyle.Fill
    ctl.Visible = False
    try:
        ctl.DefaultBackgroundColor = _view_bg_color()
    except Exception:
        pass
    form.Controls.Add(ctl)

    def on_init(sender, args):
        try:
            if not args.IsSuccess:
                log(f"webview init failed: {args.InitializationException}")
                return
            _apply_view_settings(ctl.CoreWebView2)
            _hook_view(ctl.CoreWebView2)
            log(f"view ready [{idx}]")
            # 控件建好之后才能导航（Source 要等 CoreWebView2 存在）
            try:
                url = sites()[idx]["url"]
            except Exception:
                return
            _navigate(ctl, url)
        except Exception as e:
            log(f"on_init failed: {e!r}")

    _view_hooks.append(on_init)
    ctl.CoreWebView2InitializationCompleted += on_init
    try:
        ctl.EnsureCoreWebView2Async(env)
    except Exception as e:
        log(f"EnsureCoreWebView2Async failed: {e!r}")
        try:
            ctl.Dispose()
        except Exception:
            pass
        return None

    log(f"view created [{idx}]")
    return ctl


def _view_for(idx: int):
    """取该站点的控件；没有就建。第一个显示的站点直接复用 pywebview 那个。"""
    v = site_views.get(idx)
    if v is not None:
        return v
    if state.get("native_idx") is None:
        native = native_view()
        if native is not None:
            try:
                if native.CoreWebView2 is not None:
                    state["native_idx"] = idx
                    site_views[idx] = native
                    # 复用 pywebview 的控件，同样要挂导航事件，否则后退/前进按钮不刷新。
                    # 下载不用再挂：pywebview 自己的 DownloadStarting 已经被换成我们的回调了，
                    # 再挂一次会「问两遍路径、存两次」。
                    _hook_nav_events(native.CoreWebView2)
                    if not _pv_patched:
                        _hook_downloads(native.CoreWebView2, "native")
                    return native
            except Exception:
                pass
    v = _create_view(idx)
    if v is not None:
        site_views[idx] = v
    return v


def show_site(idx: int, force: bool = False) -> None:
    """切到第 idx 个站点（GUI 线程）。force=True 才重新导航。"""
    items = sites()
    if not (0 <= idx < len(items)):
        return
    first = idx not in site_views
    view = _view_for(idx)
    if view is None:
        return

    for k, v in list(site_views.items()):
        if k == idx:
            continue
        try:
            v.Visible = False
        except Exception:
            pass

    try:
        view.Visible = True
        view.BringToFront()
        view.Focus()
    except Exception as e:
        log(f"show view failed: {e!r}")

    # 第一次显示、或网址被改过，才真正导航
    if first or force:
        log(f"navigate view [{idx}] {items[idx]['url']}")
        _navigate(view, items[idx]["url"])

    # 每个站点的历史不一样，切过来要重算后退/前进能不能点
    refresh_nav_bar()


def reload_site(idx: int) -> None:
    """刷新当前站点的网页（不重建控件）。"""
    view = site_views.get(idx)
    try:
        core = view.CoreWebView2 if view is not None else None
        if core is not None:
            core.Reload()
            return
    except Exception:
        pass
    show_site(idx, True)


def nav_action(kind: str) -> None:
    """
    标题栏导航按钮：back / forward / reload / home。

    「主页」不是浏览器的空白页，而是**该站点配置里的网址** —— 这个工具是
    多站点壳子，每个标签有自己的"主页"，回首页就是回到那个网址。
    """
    idx = active_index()
    items = sites()
    if not (0 <= idx < len(items)):
        return
    view = site_views.get(idx)
    core = None
    try:
        core = view.CoreWebView2 if view is not None else None
    except Exception:
        core = None

    if kind == "home":
        url = items[idx]["url"]
        log(f"nav home -> [{idx}] {url}")
        if core is not None:
            _navigate(view, url)
        else:
            invoke_gui(lambda: show_site(idx, True))
        return

    if core is None:
        log(f"nav {kind} skipped: view not ready")
        return
    try:
        if kind == "back":
            if core.CanGoBack:
                core.GoBack()
        elif kind == "forward":
            if core.CanGoForward:
                core.GoForward()
        elif kind == "reload":
            core.Reload()
        log(f"nav {kind}")
    except Exception as e:
        log(f"nav {kind} failed: {e!r}")


def refresh_nav_bar() -> None:
    """导航状态变了（历史记录/加载完成），重绘导航按钮的可用态。"""
    strip = state.get("nav_strip")
    if strip is None:
        return
    from System import Action
    try:
        strip.Invoke(Action(lambda: strip.Invalidate()))
    except Exception:
        try:
            strip.Invalidate()
        except Exception:
            pass


def drop_site_view(idx: int) -> None:
    """删站点时清掉它的控件，并把后面站点的下标整体前移。"""
    view = site_views.pop(idx, None)
    for k in sorted([k for k in site_views if k > idx]):
        site_views[k - 1] = site_views.pop(k)
    if state.get("native_idx") == idx:
        # pywebview 的控件不能 Dispose，只解除映射，之后会被别的站点接管
        state["native_idx"] = None
        return
    if view is not None:
        form = state.get("form")
        try:
            if form is not None:
                form.Controls.Remove(view)
            view.Dispose()
        except Exception:
            pass


def shift_site_views(after: int) -> None:
    """新站点插到中间时，把后面的下标顶开（目前只在末尾追加，留作备用）。"""
    for k in sorted([k for k in site_views if k >= after], reverse=True):
        site_views[k + 1] = site_views.pop(k)


def switch_site(idx: int) -> None:
    """切换到第 idx 个站点（标签点击 / 托盘切换都走这里）。"""
    items = sites()
    if not (0 <= idx < len(items)):
        return
    commit_sites(items, idx)
    load_active_site()


def cycle_site() -> None:
    items = sites()
    if len(items) > 1:
        switch_site((active_index() + 1) % len(items))


def add_site_flow() -> None:
    """弹「添加 AI 站点」对话框，确定后追加并立即切换过去。"""
    def run():
        try:
            r = site_dialog("添加 AI 站点")
            if not r:
                return
            items = list(sites())
            items.append({"name": r[0], "url": r[1]})
            commit_sites(items, len(items) - 1)
            load_active_site()
            log(f"site added: {r[0]} {r[1]}")
        except Exception as e:
            log(f"add_site_flow failed: {e!r}")
    invoke_gui(run)


def rename_site_flow(idx: int) -> None:
    def run():
        try:
            items = sites()
            if not (0 <= idx < len(items)):
                return
            old_url = items[idx]["url"]
            r = site_dialog("编辑站点", items[idx]["name"], items[idx]["url"])
            if not r:
                return
            items = list(items)
            items[idx] = {"name": r[0], "url": r[1]}
            commit_sites(items, active_index())
            if idx == active_index() and r[1] != old_url:
                load_active_site(True)      # 网址变了才重新导航；只改名字不动页面
            log(f"site edited -> {r[0]} {r[1]}")
        except Exception as e:
            log(f"rename_site_flow failed: {e!r}")
    invoke_gui(run)


def remove_site_flow(idx: int) -> None:
    def run():
        try:
            from System.Windows.Forms import MessageBox, MessageBoxButtons, MessageBoxIcon
            items = sites()
            if not (0 <= idx < len(items)):
                return
            if len(items) <= 1:
                MessageBox.Show("至少要保留一个站点。", TITLE)
                return
            r = MessageBox.Show(
                f"确定删除站点「{items[idx]['name']}」？\n（只删条目，不影响网页里的数据）",
                TITLE, MessageBoxButtons.YesNo, MessageBoxIcon.Question,
            )
            from System.Windows.Forms import DialogResult
            if r != DialogResult.Yes:
                return
            items = [s for i, s in enumerate(items) if i != idx]
            new_active = active_index()
            if idx < new_active or new_active >= len(items):
                new_active = max(0, new_active - 1)
            commit_sites(items, new_active)
            drop_site_view(idx)
            load_active_site()
            log(f"site removed: index {idx}")
        except Exception as e:
            log(f"remove_site_flow failed: {e!r}")
    invoke_gui(run)


def site_dialog(title: str, name0: str = "", url0: str = ""):
    """
    自绘的添加/编辑站点对话框（必须在 GUI 线程调用）。
    返回 (name, url)，取消返回 None。
    """
    from System.Windows.Forms import (
        Form, Label, TextBox, Button, Panel, DialogResult, FormBorderStyle,
        FormStartPosition, FlowLayoutPanel, FlowDirection, FlatStyle,
        MessageBox, MessageBoxIcon, Cursors, Padding as WFPadding, BorderStyle,
        MouseButtons, AutoScaleMode,
    )
    from System.Drawing import Point, Size, Font, FontStyle, Color, Pen

    P = palette()
    bg = P["panel"]
    fg = P["menu_text"]
    dim = P["dim"]
    field = P["field"]
    accent = P["accent"]
    bar_bg = P["bar"]
    edge = P["edge"]

    dlg = Form()
    dlg.Text = title
    # 无边框 + 自绘标题：系统和浅色主题不搭，自己画才能和主窗口一个风格
    # None 在 Python 里是关键字，同名枚举成员只能用 getattr 取
    dlg.FormBorderStyle = getattr(FormBorderStyle, "None")
    dlg.StartPosition = FormStartPosition.CenterParent if state.get("form") is not None \
        else FormStartPosition.CenterScreen
    dlg.MaximizeBox = False
    dlg.MinimizeBox = False
    dlg.ShowInTaskbar = False
    dlg.TopMost = True                    # 主窗是置顶的，对话框不置顶会被压住
    dlg.BackColor = bg
    dlg.ForeColor = fg
    try:
        dlg.Font = Font("Segoe UI", UI_FONT_PT, FontStyle.Regular)
    except Exception:
        pass

    # 下面的坐标表是按 96 DPI 的像素写的。4K + 150%/200% 缩放下，9pt 字体的
    # 实际像素高度会翻倍，而代码里写死的 Location/Size 不会跟着变，
    # 结果就是"文字比框大、控件互相压住"。
    # 处理办法：按 DPI 显式缩放全部坐标；字体保持 9pt 不动（它本来就会跟着 DPI 变）。
    # 同时关掉 WinForms 的自动缩放，否则可能二次缩放。
    try:
        dlg.AutoScaleMode = getattr(AutoScaleMode, "None")
    except Exception:
        pass

    def dpi_scale() -> float:
        sc = ui_scale()
        if sc > 1.01:
            return sc
        try:                       # 兜底：用窗体自己的 DPI
            return max(1.0, float(dlg.DeviceDpi) / 96.0)
        except Exception:
            return 1.0

    s = dpi_scale()

    def S(v) -> int:
        return int(round(v * s))

    FSZ = S(9)            # 各处留白基准
    BAR_H = S(34)         # 自绘标题条高度
    PAD = S(20)           # 左右留白
    LBL_W = S(44)         # 标签列宽
    FLD_X = S(74)         # 输入框左边界
    FLD_W = S(314)        # 输入框宽度
    log(f"site dialog dpi scale={s:.2f}")

    # ---- 自绘边框（无边框窗口没有系统阴影和轮廓，画一圈才看得出边界）----
    def on_form_paint(s, e):
        p = Pen(edge, 1)
        try:
            e.Graphics.DrawRectangle(p, 0, 0, dlg.ClientSize.Width - 1,
                                     dlg.ClientSize.Height - 1)
        finally:
            p.Dispose()

    dlg.Paint += on_form_paint

    # ---- 自绘标题栏（可拖动 + 关闭）----
    tbar = Panel()
    tbar.Location = Point(1, 1)
    tbar.Size = Size(S(406), BAR_H)
    tbar.BackColor = bar_bg
    dlg.Controls.Add(tbar)

    ttl = Label()
    ttl.Text = title
    ttl.AutoSize = True
    try:
        ttl.Font = dlg.Font
    except Exception:
        pass
    try:
        ttl_h = int(ttl.PreferredHeight)
    except Exception:
        ttl_h = S(18)
    ttl.Location = Point(S(13), max(1, (BAR_H - ttl_h) // 2))
    ttl.ForeColor = P["strong"]
    ttl.BackColor = Color.Transparent
    tbar.Controls.Add(ttl)

    x_w = S(34)
    btn_x = Button()
    btn_x.Text = "\u2715"
    btn_x.Size = Size(x_w, max(S(22), BAR_H - S(10)))
    btn_x.Location = Point(S(406) - x_w - S(4), max(1, (BAR_H - btn_x.Height) // 2))
    btn_x.FlatStyle = FlatStyle.Flat
    btn_x.FlatAppearance.BorderSize = 0
    btn_x.BackColor = bar_bg
    btn_x.ForeColor = P["fg"]
    btn_x.TabStop = False
    btn_x.Cursor = Cursors.Hand
    btn_x.MouseEnter += (lambda s, e: (setattr(btn_x, "BackColor", Color.FromArgb(232, 72, 77)),
                                       setattr(btn_x, "ForeColor", Color.White)))
    btn_x.MouseLeave += (lambda s, e: (setattr(btn_x, "BackColor", bar_bg),
                                       setattr(btn_x, "ForeColor", P["fg"])))
    tbar.Controls.Add(btn_x)

    def do_cancel(s=None, e=None):
        dlg.DialogResult = DialogResult.Cancel
        dlg.Close()

    btn_x.Click += do_cancel

    def start_drag(s, e):
        if e.Button != MouseButtons.Left:
            return
        try:
            user32.ReleaseCapture()
            user32.SendMessageW(wt.HWND(int(dlg.Handle)), WM_NCLBUTTONDOWN, HTCAPTION, 0)
        except Exception:
            pass

    tbar.MouseDown += start_drag
    ttl.MouseDown += start_drag

    sep = Panel()
    sep.Location = Point(1, BAR_H)
    sep.Size = Size(S(406), 1)
    sep.BackColor = edge
    dlg.Controls.Add(sep)

    # ---- 一行表单：输入框高度由字体决定，不能再写死，
    #      否则高 DPI 下输入框长高了会压到下一行 ----
    def mk_row(text, value, y, tabidx):
        t = TextBox()
        t.Text = value
        t.Width = FLD_W
        # 必须先给字体再量高度：控件还没挂到父窗体上时，Font 还是系统默认的
        # 9pt@96dpi，量出来的 PreferredHeight 偏小，挂上去换成大字体后文字就被裁了
        try:
            t.Font = dlg.Font
        except Exception:
            pass
        t.BackColor = field
        t.ForeColor = fg
        t.BorderStyle = BorderStyle.FixedSingle
        t.TabIndex = tabidx
        try:
            fh = int(t.PreferredHeight)      # 单行 TextBox 的真实高度（随字体变）
        except Exception:
            fh = S(24)
        t.Size = Size(FLD_W, fh)
        t.Location = Point(FLD_X, y)
        dlg.Controls.Add(t)

        l = Label()
        l.Text = text
        l.AutoSize = False
        try:
            l.Font = dlg.Font
        except Exception:
            pass
        try:
            lh = int(l.PreferredHeight)
        except Exception:
            lh = S(18)
        lh = max(lh, int(fh * 0.8))
        l.Size = Size(LBL_W, lh)
        l.Location = Point(PAD, y + max(0, (fh - lh) // 2))
        l.ForeColor = dim
        l.BackColor = Color.Transparent
        dlg.Controls.Add(l)
        return t, y + fh + S(16)

    y = BAR_H + S(17)
    tb_name, y = mk_row("名称", name0, y, 0)
    tb_url, y = mk_row("网址", url0, y, 1)

    hint = Label()
    hint.Text = "也可以点下面的常用站点，自动填好"
    hint.AutoSize = False
    try:
        hint.Font = dlg.Font
    except Exception:
        pass
    try:
        hint_h = int(hint.PreferredHeight)
    except Exception:
        hint_h = S(18)
    hint_h = max(hint_h, int(dlg.Font.Height) + S(2))
    hint.Location = Point(PAD, y)
    hint.Size = Size(S(370), hint_h)
    hint.ForeColor = dim
    hint.BackColor = Color.Transparent
    dlg.Controls.Add(hint)

    fp_y = y + hint_h + S(4)
    fp = FlowLayoutPanel()
    fp.Location = Point(S(16), fp_y)
    fp.Size = Size(S(376), S(108))
    fp.FlowDirection = FlowDirection.LeftToRight
    fp.WrapContents = True
    fp.BackColor = bg
    dlg.Controls.Add(fp)

    def fill(n, u):
        try:
            tb_name.Text = n
            tb_url.Text = u
        except Exception:
            pass

    for pname, purl in PRESETS:
        b = Button()
        b.Text = pname
        b.Size = Size(S(86), S(27))     # 固定尺寸 → 四列整齐对齐
        b.Margin = WFPadding(0, 0, S(6), S(6))
        b.FlatStyle = FlatStyle.Flat
        b.FlatAppearance.BorderColor = edge
        b.FlatAppearance.BorderSize = 1
        b.BackColor = field
        b.ForeColor = fg
        b.Cursor = Cursors.Hand
        b.TabStop = False              # 不参与 Tab 循环，否则 Tab 键会被预设按钮截走
        b.Click += (lambda _s, _e, n=pname, u=purl: fill(n, u))
        fp.Controls.Add(b)

    # ---- 底部按钮行 ----
    btn_h = max(S(30), int(dlg.Font.Height) + S(10))
    btn_y = fp_y + S(108) + S(20)
    ok_w = S(88)

    ok = Button()
    ok.Text = "保存"
    ok.Size = Size(ok_w, btn_h)
    ok.Location = Point(S(196), btn_y)
    ok.FlatStyle = FlatStyle.Flat
    ok.FlatAppearance.BorderSize = 0
    ok.BackColor = accent
    ok.ForeColor = Color.White
    ok.DialogResult = DialogResult.OK
    ok.TabIndex = 3
    dlg.Controls.Add(ok)

    cancel = Button()
    cancel.Text = "取消"
    cancel.Size = Size(ok_w, btn_h)
    cancel.Location = Point(S(296), btn_y)
    cancel.FlatStyle = FlatStyle.Flat
    cancel.FlatAppearance.BorderColor = edge
    cancel.FlatAppearance.BorderSize = 1
    cancel.BackColor = field
    cancel.ForeColor = fg
    cancel.DialogResult = DialogResult.Cancel
    cancel.TabIndex = 4
    dlg.Controls.Add(cancel)

    # 尺寸放到最后算：高度由实际排版结果决定，高 DPI 下自动变高，不会切掉按钮
    dlg.ClientSize = Size(S(408), btn_y + btn_h + S(16))

    # Tab 顺序：名称 → 网址 → 保存 → 取消
    fp.TabIndex = 2
    fp.TabStop = False

    dlg.AcceptButton = ok
    dlg.CancelButton = cancel
    dlg.ActiveControl = tb_name
    try:
        tb_name.SelectAll()
    except Exception:
        pass

    owner = state.get("form")
    ret = dlg.ShowDialog(owner) if owner is not None else dlg.ShowDialog()
    if ret != DialogResult.OK:
        try:
            dlg.Dispose()
        except Exception:
            pass
        return None

    name = (tb_name.Text or "").strip()
    url = (tb_url.Text or "").strip()
    try:
        dlg.Dispose()
    except Exception:
        pass
    if not url:
        MessageBox.Show("网址不能为空。", TITLE, 0, MessageBoxIcon.Warning)
        return None
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    if not name:
        name = url.split("//")[-1].split("/")[0]
    return name, url


# ---------------------------------------------------------------- Win32

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi

GWL_EXSTYLE = -20
GWL_STYLE = -16
GWL_WNDPROC = -4
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000

WM_NCHITTEST = 0x0084
WM_HOTKEY = 0x0312
HTCAPTION = 2

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2


def get_work_area() -> tuple[int, int, int, int]:
    """返回主屏工作区 (left, top, right, bottom)，已排除任务栏。"""
    r = wt.RECT()
    try:
        user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
        if r.right > r.left and r.bottom > r.top:
            return r.left, r.top, r.right, r.bottom
    except Exception:
        pass
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def virtual_screen() -> tuple[int, int, int, int]:
    """所有显示器拼成的虚拟桌面范围，副屏是负数坐标。"""
    vx = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
    vy = user32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
    vw = user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
    vh = user32.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
    if vw <= 0 or vh <= 0:
        return get_work_area()
    return vx, vy, vx + vw, vy + vh


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("rcMonitor", wt.RECT),
        ("rcWork", wt.RECT),
        ("dwFlags", wt.DWORD),
    ]


def monitor_work_area(x: int, y: int) -> tuple[int, int, int, int]:
    """返回包含该坐标的那块屏幕的工作区（已排除该屏的任务栏）。"""
    try:
        hmon = user32.MonitorFromPoint(wt.POINT(int(x), int(y)), 2)  # NEAREST
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcWork
            if r.right > r.left and r.bottom > r.top:
                return r.left, r.top, r.right, r.bottom
    except Exception:
        pass
    return get_work_area()


def cursor_pos() -> tuple[int, int]:
    p = wt.POINT()
    try:
        user32.GetCursorPos(ctypes.byref(p))
    except Exception:
        pass
    return p.x, p.y


def enable_round_corners(hwnd: int) -> None:
    """Win11 圆角；老系统静默失败。"""
    try:
        pref = ctypes.c_int(DWMWCP_ROUND)
        dwmapi.DwmSetWindowAttribute(
            wt.HWND(hwnd), DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(pref), ctypes.sizeof(pref),
        )
    except Exception:
        pass


def hide_from_taskbar(hwnd: int) -> None:
    """
    加上 WS_EX_TOOLWINDOW 并去掉 WS_EX_APPWINDOW，窗口就不出现在任务栏
    （Alt+Tab 里也不会有），只留托盘图标 —— 和微信那种用法一致。
    样式改完必须 hide+show 一次才会生效，所以启动时会闪一下，属正常。
    """
    if not hwnd:
        return
    try:
        get_ptr = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
        set_ptr = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        get_ptr.restype = ctypes.c_void_p
        get_ptr.argtypes = [wt.HWND, ctypes.c_int]
        set_ptr.restype = ctypes.c_void_p
        set_ptr.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_void_p]

        ex = get_ptr(wt.HWND(hwnd), GWL_EXSTYLE) or 0
        new_ex = (int(ex) | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        set_ptr(wt.HWND(hwnd), GWL_EXSTYLE, ctypes.c_void_p(new_ex))

        user32.ShowWindow(wt.HWND(hwnd), 0)   # SW_HIDE
        user32.ShowWindow(wt.HWND(hwnd), 5)   # SW_SHOW
        log(f"taskbar hidden: exstyle {int(ex):#x} -> {new_ex:#x}")
    except Exception as e:
        log(f"hide_from_taskbar failed: {e!r}")


def set_tool_window(hwnd: int) -> None:
    """只加 WS_EX_TOOLWINDOW（不 hide/show），让弹出菜单不占任务栏和 Alt+Tab。"""
    if not hwnd:
        return
    try:
        get_ptr = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
        set_ptr = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        get_ptr.restype = ctypes.c_void_p
        get_ptr.argtypes = [wt.HWND, ctypes.c_int]
        set_ptr.restype = ctypes.c_void_p
        set_ptr.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_void_p]
        ex = get_ptr(wt.HWND(hwnd), GWL_EXSTYLE) or 0
        new_ex = (int(ex) | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        if new_ex != int(ex):
            set_ptr(wt.HWND(hwnd), GWL_EXSTYLE, ctypes.c_void_p(new_ex))
    except Exception as e:
        log(f"set_tool_window failed: {e!r}")


def find_app_window() -> int | None:
    """枚举本进程的可见顶层窗口，返回第一个（即 WebView 窗口）。"""
    pid = os.getpid()
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def _cb(hwnd, _lp):
        wpid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value == pid and user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(_cb, 0)
    return found[0] if found else None


class DragBar:
    """给无边框窗口的顶部若干像素装上 HTCAPTION，实现拖动。"""

    WNDPROC = ctypes.WINFUNCTYPE(
        ctypes.c_void_p, wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM
    )

    def __init__(self, hwnd: int, height: int = 8, exclude_right: int = 0):
        self.hwnd = hwnd
        self.height = max(0, int(height))
        self.exclude_right = max(0, int(exclude_right))
        self._old = None
        self._proc = None
        self._install()

    def _install(self) -> None:
        if not self.hwnd:
            return
        try:
            kernel32 = ctypes.windll.kernel32
            set_ptr = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
            set_ptr.restype = ctypes.c_void_p
            set_ptr.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_void_p]
            user32.CallWindowProcW.restype = ctypes.c_void_p
            user32.CallWindowProcW.argtypes = [
                ctypes.c_void_p, wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM
            ]

            self._proc = self.WNDPROC(self._wndproc)  # 必须持有引用防 GC
            proc_ptr = ctypes.cast(self._proc, ctypes.c_void_p)
            kernel32.SetLastError(0)
            self._old = set_ptr(wt.HWND(self.hwnd), GWL_WNDPROC, proc_ptr)
            err = kernel32.GetLastError()
            log(f"drag bar set: old={self._old} err={err} hwnd={self.hwnd}")
        except Exception as e:
            log(f"drag bar install failed: {e!r}")

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_NCHITTEST and self.height > 0:
                x = ctypes.c_short(lparam & 0xFFFF).value
                y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
                rect = wt.RECT()
                user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(rect))
                # 右侧 exclude_right 像素让给关闭按钮，否则点不到
                if rect.top <= y < rect.top + self.height and \
                        rect.left <= x < rect.right - self.exclude_right:
                    return HTCAPTION
        except Exception:
            pass
        return user32.CallWindowProcW(self._old, hwnd, msg, wparam, lparam)


# ---------------------------------------------------------------- 全局热键

class HotkeyListener(threading.Thread):
    """独立线程注册 + 消息泵。RegisterHotKey 必须在同一线程 GetMessage。"""

    def __init__(self, combo: str, callback):
        super().__init__(daemon=True)
        self.combo = combo
        self.callback = callback
        self.ready = threading.Event()
        self.ok = False

    @staticmethod
    def _parse(combo: str) -> tuple[int, int]:
        mods = 0
        key = None
        for part in combo.lower().replace(" ", "").split("+"):
            if part == "alt":
                mods |= MOD_ALT
            elif part == "ctrl":
                mods |= MOD_CONTROL
            elif part == "shift":
                mods |= MOD_SHIFT
            elif part == "win":
                mods |= MOD_WIN
            elif part:
                key = part
        vk = ord(key.upper()) if key and len(key) == 1 else (0x44 if key is None else 0x44)
        return mods, vk

    def run(self) -> None:
        mods, vk = self._parse(self.combo)
        if not user32.RegisterHotKey(None, 1, mods, vk):
            self.ok = False
            self.ready.set()
            log(f"RegisterHotKey failed for {self.combo}（可能被其他程序占用）")
            return
        self.ok = True
        self.ready.set()
        log(f"hotkey registered: {self.combo}")
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                try:
                    self.callback()
                except Exception as e:
                    log(f"hotkey callback error: {e}")
        user32.UnregisterHotKey(None, 1)


# ---------------------------------------------------------------- 窗口操作

def window_visible() -> bool:
    hwnd = state["hwnd"]
    if not hwnd:
        return False
    return bool(user32.IsWindowVisible(wt.HWND(hwnd)))


def toggle_window() -> None:
    win = state["win"]
    if not win:
        return
    if window_visible():
        close_popup_menu()
        _dl_popup_hide_async()
        win.hide()
    else:
        win.show()
        if state["hwnd"]:
            user32.SetForegroundWindow(wt.HWND(state["hwnd"]))
        ah = state.get("autohide")      # 收起状态下唤出，直接给完整窗口
        if ah:
            ah.force_expand()
    sync_tray_icon()


def show_window() -> None:
    win = state["win"]
    if win:
        win.show()
        if state["hwnd"]:
            user32.SetForegroundWindow(wt.HWND(state["hwnd"]))
        ah = state.get("autohide")
        if ah:
            ah.force_expand()
    sync_tray_icon()


def minimize_to_tray() -> None:
    """最小化到托盘：窗口藏起来，托盘图标留着；Alt+D 或点托盘图标唤回。"""
    win = state["win"]
    if not win:
        return
    close_popup_menu()
    _dl_popup_hide_async()
    ah = state.get("autohide")
    if ah and ah.hidden:
        ah.force_expand()      # 先复位，否则下次唤出窗口还停在屏幕外
    win.hide()
    sync_tray_icon()
    log("minimized to tray")
    tray = state.get("tray")
    if tray:
        try:
            tray.notify("已最小化到托盘，按 Alt+D 唤回", TITLE)
        except Exception:
            pass


def apply_topmost(hwnd: int | None, on: bool) -> None:
    """直接用 Win32 设置置顶，绕开 pywebview 在 winforms 下的兼容问题。"""
    if not hwnd:
        return
    HWND_TOPMOST = -1
    HWND_NOTOPMOST = -2
    SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE = 0x0002, 0x0001, 0x0010
    try:
        user32.SetWindowPos(
            wt.HWND(hwnd),
            wt.HWND(HWND_TOPMOST if on else HWND_NOTOPMOST),
            0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )
    except Exception as e:
        log(f"apply_topmost failed: {e}")


def set_on_top(value: bool) -> None:
    state["on_top"] = bool(value)
    # 最大化期间不置顶（否则铺满整屏会把别的窗口全遮住），设置值先记着，还原时生效
    apply_topmost(state["hwnd"], bool(value) and not state.get("maximized"))
    cfg = load_config()
    cfg["on_top"] = bool(value)
    save_config(cfg)
    log(f"on_top -> {value}" + ("（最大化中，暂不生效）" if state.get("maximized") else ""))


def reload_page() -> None:
    idx = active_index()
    invoke_gui(lambda: reload_site(idx))


# ---------------------------------------------------------------- 启动动画流程

def navigate_to_site() -> None:
    """把启动动画换成真实站点（当前选中的那个）。"""
    if not state["win"]:
        return
    state["navigated"] = True
    state["pending_hist_reset"] = True     # 站点加载完成后把动画页从历史里抹掉
    load_active_site()


def on_page_loaded() -> None:
    """首次 loaded 是启动动画；播完再跳转。之后每次加载只记日志。"""
    if state.get("splash_done"):
        log("page loaded")
        return
    state["splash_done"] = True
    log("splash loaded")
    threading.Thread(target=_splash_then_site, daemon=True, name="splash").start()


def _splash_then_site() -> None:
    time.sleep(SPLASH_SECONDS)
    navigate_to_site()


def _splash_watchdog() -> None:
    """兜底：万一动画页的 loaded 事件没来，别一直停在动画上。"""
    time.sleep(SPLASH_SECONDS + 4.0)
    if not state.get("navigated"):
        log("splash watchdog: force navigate")
        navigate_to_site()


def quit_app() -> None:
    log("quit requested")
    if state["tray"]:
        try:
            state["tray"].stop()
        except Exception:
            pass
    monitor_stop.set()
    win = state["win"]
    if win:
        try:
            win.destroy()
        except Exception:
            pass
    os._exit(0)


def on_window_closed() -> None:
    """Alt+F4 / 关窗后确保进程不残留。"""
    log("window closed")
    monitor_stop.set()
    if state["tray"]:
        try:
            state["tray"].stop()
        except Exception:
            pass
    os._exit(0)


# ---------------------------------------------------------------- 原生标题栏

TITLE_BAR_H = 34        # 标题条基准高度（实际按字体真实字高自适应，见 add_title_bar）
TITLE_BTN_W = 52        # 单个标题按钮的基准占位宽度

WM_NCLBUTTONDOWN = 0x00A1


def add_title_bar(hwnd: int) -> dict:
    """
    给无边框窗口加一条原生标题条：标签页 + ＋ / — / ▢ / ✕ 四个按钮。

    原理：WebView 在 Form 里是 Dock=Fill，所以给 Form 设 Padding(top=N) 之后，
    网页整体会被往下推 N 像素，顶部露出 Form 自己的背景。标题控件放在这块区域上，
    物理上就不可能压住网页里的元素。

    高度不写死：先量出字体在当前 DPI 下的真实字高，再据此决定条高。
    否则高分辨率屏（150% / 200% 缩放）下字体被放大、条高没变，文字下半截会被
    WebView 盖掉 —— 这就是"标题栏只显示半个 DeepSeek"的原因。

    标签条是自绘 Panel：既能点（切站点），也能拖（整个窗口移动）。
    拖动靠 ReleaseCapture + WM_NCLBUTTONDOWN(HTCAPTION) 实现，因此不需要
    整条标题栏都吃掉鼠标事件，标签的点击才不会失效。

    返回 dict：bar=条高物理像素，zone=右侧按钮区物理宽度，strip_ok=标签条是否建成。
    """
    from System import Action, IntPtr
    from System.Windows.Forms import (
        Control, Label, Button, Panel, Padding, FlatStyle, Cursors, DockStyle,
        TextRenderer, TextFormatFlags, MouseButtons, Cursor,
    )
    from System.Drawing import (
        Point, Size, Font, FontStyle, Color, Rectangle, SolidBrush, Pen,
    )
    from System.Drawing.Drawing2D import SmoothingMode, GraphicsPath

    form = Control.FromHandle(IntPtr(hwnd))
    if form is None:
        raise RuntimeError("Control.FromHandle 返回 None")
    state["form"] = form

    # 颜色一律从 palette() 取：换配色只要刷新控件颜色 + 重绘，不必重建标题栏
    P0 = palette()
    fg, fg_dim, bg = P0["fg"], P0["dim"], P0["bg"]
    accent = P0["accent"]
    tab_sel, tab_hov = P0["tab_sel"], P0["tab_hov"]

    hold = {}
    out = {"bar": int(TITLE_BAR_H), "zone": int(TITLE_BTN_W), "strip_ok": False}

    def build():
        # ---------- 先量字高，再定条高 ----------
        probe = Label()       # 用 Label 量，和旧版一致（PreferredHeight 已验证可靠）
        try:
            probe.Font = Font("Segoe UI", 9.0, FontStyle.Regular)
        except Exception:
            pass
        try:
            th = int(probe.PreferredHeight)
        except Exception:
            th = 16
        th = max(th, 14)
        pad = max(6, int(round(th * 0.6)))
        bar = max(int(TITLE_BAR_H), th + pad * 2)

        # 底部留一条和窗口同色的窄边，专门给右下角的改尺寸手柄用。
        # WebView2 是独立子窗口，压在它上面的控件在某些机器上会被它盖掉，
        # 所以手柄不能浮在网页上 —— 和标题栏一样，让网页让出这块地方。
        grip_h = max(8, int(round(th * 0.55)))
        grip_w = max(grip_h * 2, 22)

        form.Padding = Padding(0, bar, 0, grip_h)
        form.BackColor = bg

        btn_h = max(int(TITLE_BAR_H) - 8, th + 4)
        btn_w = max(30, int(round(th * 2.0)))
        tab_h = btn_h

        def make_button(text, hover_key):
            """hover_key 是 palette 里的键名，悬停色在事件发生时取，
            这样换配色不用重建按钮。"""
            b = Button()
            b.Text = text
            b.Size = Size(btn_w, btn_h)
            b.FlatStyle = FlatStyle.Flat
            b.FlatAppearance.BorderSize = 0
            b.BackColor = palette()["bg"]
            b.ForeColor = palette()["fg"]
            b.TabStop = False
            b.Cursor = Cursors.Hand
            try:
                b.Font = Font("Segoe UI", 9.0, FontStyle.Regular)
            except Exception:
                pass

            def enter(s, e):
                P = palette()
                b.BackColor = P[hover_key]
                b.ForeColor = Color.White if hover_key == "hov_red" else P["strong"]

            def leave(s, e):
                P = palette()
                b.BackColor = P["bg"]
                b.ForeColor = P["fg"]

            b.MouseEnter += enter
            b.MouseLeave += leave
            return b

        # ---------- 标签条（自绘）----------
        strip = Panel()
        strip.BackColor = bg
        strip.TabStop = False
        strip.Cursor = Cursors.Hand
        strip.Location = Point(0, 0)
        strip.Size = Size(100, bar)
        try:
            # Panel.DoubleBuffered 是 protected，用反射打开，避免重绘闪烁
            from System.Reflection import BindingFlags
            prop = strip.GetType().GetProperty(
                "DoubleBuffered", BindingFlags.Instance | BindingFlags.NonPublic
            )
            prop.SetValue(strip, True)
        except Exception:
            pass

        try:
            tab_font = Font("Segoe UI", 9.0, FontStyle.Regular)
        except Exception:
            tab_font = strip.Font

        ui = {"hot": -1, "down": None, "press": None,
              "moved": False, "segs": []}

        def measure(text):
            try:
                return int(TextRenderer.MeasureText(text or "", tab_font).Width)
            except Exception:
                return len(text or "") * 8

        def ellipsize(text, maxw):
            if measure(text) <= maxw:
                return text
            s = text
            while len(s) > 1 and measure(s + "\u2026") > maxw:
                s = s[:-1]
            return s + "\u2026"

        def compute_segs():
            """算出当前该显示哪几个标签、各自 x 区间。保证当前站点一定可见。"""
            avail = max(30, int(strip.ClientSize.Width) - 12)
            items = sites()
            n = len(items)
            widths = []
            for it in items:
                tw = measure(it.get("name") or "")
                widths.append(max(TAB_MIN_W, min(TAB_MAX_W, tw + TAB_PAD * 2)))
            a = max(0, min(active_index(), n - 1)) if n else 0

            lo = hi = a
            if n:
                def need(lo, hi):
                    v = sum(widths[lo:hi + 1]) + TAB_GAP * max(0, hi - lo)
                    if lo > 0:
                        v += TAB_GAP + MORE_W
                    if hi < n - 1:
                        v += TAB_GAP + MORE_W
                    return v

                grew = True
                while grew:
                    grew = False
                    if hi < n - 1 and need(lo, hi + 1) <= avail:
                        hi += 1
                        grew = True
                        continue
                    if lo > 0 and need(lo - 1, hi) <= avail:
                        lo -= 1
                        grew = True

            segs_ = []
            x = 8
            if n == 0:
                return segs_
            if lo > 0:
                segs_.append((x, x + MORE_W, "more_l", -1))
                x += MORE_W + TAB_GAP
            for i in range(lo, hi + 1):
                segs_.append((x, x + widths[i], "tab", i))
                x += widths[i] + TAB_GAP
            if hi < n - 1:
                segs_.append((x, x + MORE_W, "more_r", -1))
            return segs_

        def rounded(rect, rad):
            p = GraphicsPath()
            d = rad * 2
            p.AddArc(rect.X, rect.Y, d, d, 180, 90)
            p.AddArc(rect.Right - d, rect.Y, d, d, 270, 90)
            p.AddArc(rect.Right - d, rect.Bottom - d, d, d, 0, 90)
            p.AddArc(rect.X, rect.Bottom - d, d, d, 90, 90)
            p.CloseFigure()
            return p

        # ---------- 左侧导航按钮（后退 / 前进 / 刷新 / 主页）----------
        # 为什么自绘而不是用 Button：系统按钮在深浅两套配色下都不好统一
        # （已经有了一次 ToolStrip 的教训）。图标也用矢量画，不依赖字体 ——
        # Segoe MDL2 这类图标字体在部分机器上缺失，会直接显示成方块。
        nav_btn_w = max(22, int(round(th * 1.45)))     # 图标放大后要给足边距
        nav_pad = max(3, int(round(th * 0.25)))
        nav_w = nav_btn_w * 4 + nav_pad * 2 + 2
        nav_btn_h = btn_h
        icon_s = max(12, int(round(th * 1.0)))
        NAV_KINDS = ("back", "forward", "reload", "home")

        nav = Panel()
        nav.BackColor = bg
        nav.TabStop = False
        nav.Cursor = Cursors.Hand
        nav.Location = Point(0, 0)
        nav.Size = Size(nav_w, bar)
        try:
            from System.Reflection import BindingFlags as _BF
            _p = nav.GetType().GetProperty("DoubleBuffered",
                                           _BF.Instance | _BF.NonPublic)
            _p.SetValue(nav, True)
        except Exception:
            pass

        nav_ui = {"hot": None, "down": None}

        def nav_cells():
            cells = []
            x = nav_pad + 1
            top = max(0, (bar - nav_btn_h) // 2)
            for k in NAV_KINDS:
                cells.append((k, Rectangle(x, top, nav_btn_w, nav_btn_h)))
                x += nav_btn_w
            return cells

        def nav_hit(x, y):
            for k, r in nav_cells():
                if r.Contains(x, y):
                    return k
            return None

        def nav_can():
            """当前视图能不能后退 / 前进；取不到就当不可用。"""
            v = site_views.get(active_index())
            try:
                c = v.CoreWebView2 if v is not None else None
                if c is not None:
                    return bool(c.CanGoBack), bool(c.CanGoForward)
            except Exception:
                pass
            return False, False

        def nav_icon(g, kind, cx, cy, size, color):
            """在 (cx, cy) 画 16×16 逻辑网格的矢量图标，按 size 缩放。"""
            import math
            from System.Drawing import PointF, RectangleF
            k = size / 16.0
            pen = Pen(color, max(1.2, size * 0.105))
            br = None
            try:
                try:
                    from System.Drawing.Drawing2D import LineCap
                    pen.StartCap = LineCap.Round
                    pen.EndCap = LineCap.Round
                except Exception:
                    pass

                def P(x, y):
                    return PointF(cx + x * k, cy + y * k)

                if kind in ("back", "forward"):
                    d = 1.0 if kind == "back" else -1.0    # 箭头朝向：左 / 右
                    g.DrawLine(pen, P(-5.2 * d, 0), P(5.2 * d, 0))
                    g.DrawLine(pen, P(-5.2 * d, 0), P(-0.4 * d, -4.3))
                    g.DrawLine(pen, P(-5.2 * d, 0), P(-0.4 * d, 4.3))
                elif kind == "reload":
                    r = 4.9 * k
                    g.DrawArc(pen, RectangleF(cx - r, cy - r, r * 2, r * 2), 310, 285)
                    # 箭头：落在弧线起点，沿顺时针切线方向
                    a = math.radians(310)
                    px, py = cx + r * math.cos(a), cy + r * math.sin(a)
                    tx, ty = -math.sin(a), math.cos(a)      # 顺时针切线
                    nx, ny = math.cos(a), math.sin(a)       # 半径向外
                    tip = PointF(px + tx * r * 0.62, py + ty * r * 0.62)
                    b1 = PointF(px - tx * r * 0.30 + nx * r * 0.52,
                                py - ty * r * 0.30 + ny * r * 0.52)
                    b2 = PointF(px - tx * r * 0.30 - nx * r * 0.52,
                                py - ty * r * 0.30 - ny * r * 0.52)
                    br = SolidBrush(color)
                    g.FillPolygon(br, [tip, b1, b2])
                elif kind == "home":
                    g.DrawLines(pen, [P(-6.0, 0.4), P(0, -5.6), P(6.0, 0.4)])
                    g.DrawLines(pen, [P(-4.4, 0.4), P(-4.4, 5.6),
                                      P(4.4, 5.6), P(4.4, 0.4)])
                    g.DrawLines(pen, [P(-1.5, 2.7), P(-1.5, 5.6)])
                    g.DrawLines(pen, [P(1.5, 2.7), P(1.5, 5.6)])
                    g.DrawLines(pen, [P(-1.5, 2.7), P(1.5, 2.7)])
            finally:
                if br is not None:
                    br.Dispose()
                pen.Dispose()

        def nav_paint(s_, e):
            P = palette()
            g = e.Graphics
            try:
                g.SmoothingMode = SmoothingMode.AntiAlias
            except Exception:
                pass
            g.Clear(P["bg"])
            can_back, can_fwd = nav_can()
            for kind, r in nav_cells():
                enabled = True
                if kind == "back":
                    enabled = can_back
                elif kind == "forward":
                    enabled = can_fwd
                hov = nav_ui["hot"] == kind
                if hov and enabled:
                    inner = Rectangle(r.X + 2, r.Y + 2, r.Width - 4, r.Height - 4)
                    path = rounded(inner, 6)
                    bg_br = SolidBrush(P["tab_hov"])
                    try:
                        g.FillPath(bg_br, path)
                    finally:
                        bg_br.Dispose()
                        path.Dispose()
                if not enabled:
                    col = P["edge"]
                elif hov:
                    col = P["strong"]
                else:
                    col = P["fg"]
                nav_icon(g, kind, r.X + r.Width // 2, r.Y + r.Height // 2, icon_s, col)

        def nav_move(s_, e):
            hit_ = nav_hit(e.X, e.Y)
            if hit_ != nav_ui["hot"]:
                nav_ui["hot"] = hit_
                nav.Invalidate()
            d = nav_ui["down"]
            if d is not None:
                if abs(e.X - d[1]) > 4 or abs(e.Y - d[2]) > 4:
                    nav_ui["down"] = None
                    start_drag()

        def nav_leave(s_, e):
            if nav_ui["hot"] is not None:
                nav_ui["hot"] = None
                nav.Invalidate()

        def nav_down(s_, e):
            if e.Button == MouseButtons.Left:
                nav_ui["down"] = (nav_hit(e.X, e.Y), e.X, e.Y)

        def nav_up(s_, e):
            d = nav_ui["down"]
            nav_ui["down"] = None
            if d is None or d[0] is None:
                return
            if nav_hit(e.X, e.Y) == d[0]:
                nav_action(d[0])

        nav.Paint += nav_paint
        nav.MouseMove += nav_move
        nav.MouseLeave += nav_leave
        nav.MouseDown += nav_down
        nav.MouseUp += nav_up
        state["nav_strip"] = nav

        def on_paint(s, e):
            g = e.Graphics
            P = palette()          # 每次绘制都取当前配色，切换主题只需 Invalidate
            try:
                g.SmoothingMode = SmoothingMode.AntiAlias
            except Exception:
                pass
            g.Clear(P["bg"])
            segs_ = compute_segs()
            ui["segs"] = segs_
            a = active_index()
            ty = max(2, (bar - tab_h) // 2)
            items = sites()

            for x0, x1, kind, idx in segs_:
                w = x1 - x0
                if kind == "tab":
                    sel = idx == a
                    hov = idx == ui["hot"]
                    if sel or hov:
                        rr = Rectangle(x0, ty, w, tab_h)
                        path = rounded(rr, 7)
                        br = SolidBrush(P["tab_sel"] if sel else P["tab_hov"])
                        try:
                            g.FillPath(br, path)
                        finally:
                            br.Dispose()
                            path.Dispose()
                    txt = ellipsize(items[idx].get("name") or "", max(10, w - TAB_PAD * 2))
                    col = P["strong"] if sel else (P["fg"] if hov else P["dim"])
                    TextRenderer.DrawText(
                        g, txt, tab_font, Rectangle(x0, ty, w, tab_h), col,
                        TextFormatFlags.HorizontalCenter
                        | TextFormatFlags.VerticalCenter
                        | TextFormatFlags.NoPrefix,
                    )
                    if sel:
                        br2 = SolidBrush(P["accent"])
                        try:
                            g.FillRectangle(br2, x0 + 7, bar - 2, max(4, w - 14), 2)
                        finally:
                            br2.Dispose()
                else:
                    br3 = SolidBrush(P["dim"])
                    try:
                        cy = ty + tab_h // 2 - 1
                        cx = x0 + w // 2
                        for dx in (-4, 0, 4):
                            g.FillEllipse(br3, cx + dx - 1, cy, 2, 2)
                    finally:
                        br3.Dispose()

        def hit(x, y):
            for x0, x1, kind, idx in ui.get("segs") or []:
                if x0 <= x < x1:
                    return kind, idx
            return None, -1

        def start_drag():
            try:
                user32.ReleaseCapture()
                user32.SendMessageW(wt.HWND(hwnd), WM_NCLBUTTONDOWN, HTCAPTION, 0)
            except Exception as e:
                log(f"drag failed: {e!r}")

        def show_overflow(sender, x, y):
            """⋯ 菜单：列出所有没显示出来的站点。"""
            items = sites()
            visible = {i for _a, _b, k, i in (ui.get("segs") or []) if k == "tab"}
            hidden = [i for i in range(len(items)) if i not in visible]
            if not hidden:
                return
            entries = [(items[i].get("name") or "未命名",
                        (lambda i=i: switch_site(i))) for i in hidden]
            show_popup_menu(strip, x, y, entries)

        def on_move(s, e):
            kind, idx = hit(e.X, e.Y)
            new_hot = idx if kind == "tab" else -1
            if new_hot != ui["hot"]:
                ui["hot"] = new_hot
                strip.Invalidate()
            if ui["down"] is not None:
                dx = e.X - ui["down"][0]
                dy = e.Y - ui["down"][1]
                if abs(dx) > 4 or abs(dy) > 4:
                    ui["moved"] = True
                    ui["down"] = None
                    start_drag()

        def on_leave(s, e):
            if ui["hot"] != -1:
                ui["hot"] = -1
                strip.Invalidate()

        def on_down(s, e):
            if e.Button == MouseButtons.Left:
                ui["down"] = (e.X, e.Y)
                ui["moved"] = False
                ui["press"] = hit(e.X, e.Y)

        def on_up(s, e):
            down = ui["down"]
            moved = ui["moved"]
            ui["down"] = None
            ui["moved"] = False
            if moved or down is None:
                return
            kind, idx = ui.get("press") or (None, -1)
            ui["press"] = None
            if kind == "tab":
                switch_site(idx)
            elif kind in ("more_l", "more_r"):
                show_overflow(strip, e.X, e.Y)

        def on_context(s, e):
            kind, idx = hit(e.X, e.Y)
            if kind != "tab":
                # 标签外的空白处：只给全局操作
                entries = [
                    ("添加 AI 站点\u2026", add_site_flow),
                    None,
                    ("重新加载页面", reload_page),
                    ("切换深浅配色", toggle_theme),
                ]
            else:
                entries = [
                    ("重命名 / 改网址\u2026",
                     (lambda i=idx: rename_site_flow(i))),
                    ("删除此站点",
                     (lambda i=idx: remove_site_flow(i))),
                    None,
                    ("重新加载", reload_page),
                    ("添加站点\u2026", add_site_flow),
                ]
            show_popup_menu(strip, e.X, e.Y, entries)

        strip.Paint += on_paint
        strip.MouseMove += on_move
        strip.MouseDown += on_down
        strip.MouseUp += on_up
        strip.MouseLeave += on_leave
        strip.MouseClick += (lambda s, e: on_context(s, e) if e.Button == MouseButtons.Right else None)
        state["tab_strip"] = strip

        # ---------- 右下角改尺寸手柄 ----------
        grip = Panel()
        grip.BackColor = bg
        grip.Cursor = Cursors.SizeNWSE
        grip.TabStop = False
        grip.Size = Size(grip_w, grip_h)
        grip.Location = Point(0, 0)      # 实际位置由 place() 贴右下角

        def on_grip_paint(s, e):
            P = palette()
            pen = Pen(P["dim"], 1)
            try:
                w, h = grip.Width, grip.Height
                step = max(3, int(round(h / 4.0)))
                for i in range(1, 4):
                    off = i * step
                    e.Graphics.DrawLine(pen, w - 5 - off, h - 4, w - 5, h - 4 - off)
            finally:
                pen.Dispose()

        grip.Paint += on_grip_paint

        # 手动改尺寸：frameless 窗口没有系统可拖的边框，
        # 所以自己抓鼠标、按屏幕坐标算增量再 SetWindowPos。
        rs = {"on": False, "sx": 0, "sy": 0, "w": 0, "h": 0}

        def grip_down(s, e):
            if e.Button != MouseButtons.Left or state.get("maximized"):
                return
            r = wt.RECT()
            if not user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r)):
                return
            p = Cursor.Position
            rs.update(on=True, sx=int(p.X), sy=int(p.Y),
                      w=int(r.right - r.left), h=int(r.bottom - r.top))
            try:
                grip.Capture = True
            except Exception:
                pass
            log(f"resize start {rs['w']}x{rs['h']}")

        def grip_move(s, e):
            if not rs["on"]:
                return
            dpi = 1.0
            try:
                dpi = max(1.0, float(form.DeviceDpi) / 96.0)
            except Exception:
                pass
            p = Cursor.Position
            nw = rs["w"] + (int(p.X) - rs["sx"])
            nh = rs["h"] + (int(p.Y) - rs["sy"])
            nw = max(int(320 * dpi), nw)       # 320x400 是窗口下限
            nh = max(int(400 * dpi), nh)
            try:
                r = wt.RECT()
                user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r))
                _, _, right, bottom = monitor_work_area(r.left + 40, r.top + 1)
                nw = min(nw, max(320, int(right - r.left)))
                nh = min(nh, max(400, int(bottom - r.top)))
            except Exception:
                pass
            user32.SetWindowPos(wt.HWND(hwnd), wt.HWND(0), 0, 0, int(nw), int(nh),
                                0x0002 | 0x0004 | 0x0010)   # NOMOVE|NOZORDER|NOACTIVATE

        def grip_up(s, e):
            if not rs["on"]:
                return
            rs["on"] = False
            try:
                grip.Capture = False
            except Exception:
                pass
            save_window_size()

        grip.MouseDown += grip_down
        grip.MouseMove += grip_move
        grip.MouseUp += grip_up

        # ---------- 右侧按钮 ----------
        btn_min = make_button("\u2014", "hov")         # — 最小化到托盘
        btn_max = make_button("\u25a2", "hov")         # ▢ 最大化
        btn_cls = make_button("\u2715", "hov_red")     # ✕ 关闭

        # ---------- 下载进度圆环（吸附在窗口按钮左侧）----------
        # 常驻显示：空闲是个淡色下载图标（点一下 = 检查更新），
        # 下载中变成环形进度；悬停弹出详情面板。
        dl_ind = build_dl_indicator(th, btn_h)
        state["dl_ind"] = dl_ind

        def place(*_):
            w = int(form.ClientSize.Width)
            y = max(0, (bar - btn_h) // 2)
            x = w - 4
            for b in (btn_cls, btn_max, btn_min):   # 从右往左
                x -= (b.Width + 1)
                b.Location = Point(max(0, x), y)
            # 圆形下载按钮排在三个窗口按钮左边；不下载时整个不占位置
            if state.get("dl_visible"):
                ind_w = btn_h
                x -= (ind_w + 1)
                dl_ind.Size = Size(ind_w, btn_h)
                dl_ind.Location = Point(max(0, x), y)
            # 导航按钮钉在最左边，标签条顺延到它右侧
            nav.Size = Size(nav_w, bar)
            strip.Location = Point(nav_w, 0)
            strip.Size = Size(max(40, int(x) - 6 - nav_w), bar)
            strip.Invalidate()
            # 手柄贴在窗口右下角的那条留白上
            try:
                grip.Location = Point(max(0, w - grip_w),
                                      max(0, int(form.ClientSize.Height) - grip_h))
            except Exception:
                pass
            try:
                nav.Invalidate()
            except Exception:
                pass

        state["relayout"] = place

        def on_max(s, e):
            threading.Thread(target=toggle_maximize, daemon=True).start()

        def on_min(s, e):
            log("native minimize button clicked")
            threading.Thread(target=minimize_to_tray, daemon=True).start()

        def on_close(s, e):
            log("native close button clicked")
            threading.Thread(target=quit_app, daemon=True).start()

        btn_max.Click += on_max
        btn_min.Click += on_min
        btn_cls.Click += on_close
        form.Resize += place

        form.Controls.Add(nav)
        form.Controls.Add(strip)
        form.Controls.Add(dl_ind)
        form.Controls.Add(btn_min)
        form.Controls.Add(btn_max)
        form.Controls.Add(btn_cls)
        form.Controls.Add(grip)
        nav.BringToFront()
        strip.BringToFront()
        dl_ind.BringToFront()
        btn_min.BringToFront()
        btn_max.BringToFront()
        btn_cls.BringToFront()
        grip.BringToFront()
        place()
        log(f"resize grip {grip_w}x{grip_h} at ({grip.Location.X},{grip.Location.Y}) "
            f"client={form.ClientSize.Width}x{form.ClientSize.Height}")

        hold["strip"] = strip
        hold["nav"] = nav
        hold["dl"] = dl_ind
        hold["close"] = btn_cls
        hold["max"] = btn_max
        hold["min"] = btn_min
        hold["grip"] = grip
        state["title_hold"] = hold
        state["max_btn"] = btn_max
        state["grip"] = grip
        out["strip_ok"] = True

        # ---- 把物理像素尺寸量出来（逻辑像素 × DPI 缩放可能不等于物理）----
        scale = 1.0
        try:
            scale = float(form.DeviceDpi) / 96.0
        except Exception:
            pass

        bar_phys = 0
        try:
            for c in form.Controls:
                try:
                    if c.Dock == DockStyle.Fill:
                        p = c.PointToScreen(Point(0, 0))
                        r = wt.RECT()
                        user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r))
                        bar_phys = int(p.Y) - int(r.top)
                        break
                except Exception:
                    continue
        except Exception:
            pass
        if bar_phys <= 0:
            bar_phys = int(round(bar * scale))

        try:
            # 用屏幕坐标相减：PointToScreen 与 GetWindowRect 始终在同一坐标系里，
            # 不必去猜当前是不是 DPI 缩放、该不该乘系数
            pm = btn_min.PointToScreen(Point(0, 0))
            r2 = wt.RECT()
            user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r2))
            zone_phys = int(r2.right - int(pm.X)) + 10
        except Exception:
            zone_phys = int(TITLE_BTN_W)

        out["bar"] = int(bar_phys)
        out["zone"] = int(zone_phys)
        out["th"] = int(th)
        out["dpi"] = int(scale * 96)

    form.Invoke(Action(build))
    log(f"title bar added: bar={out['bar']}px zone={out['zone']}px "
        f"text={out.get('th')}px dpi={out.get('dpi')} strip={out['strip_ok']}")
    return out


def sync_max_button() -> None:
    """最大化/还原按钮的图标跟随状态。"""
    b = state.get("max_btn")
    if not b:
        return
    text = "\u2750" if state.get("maximized") else "\u25a2"   # ❐ / ▢
    try:
        from System import Action
        b.Invoke(Action(lambda: setattr(b, "Text", text)))
    except Exception:
        try:
            b.Text = text
        except Exception:
            pass


def toggle_maximize() -> None:
    """标题栏按钮：在"铺满所在屏工作区"和"原尺寸"之间切换。"""
    hwnd = state["hwnd"]
    if not hwnd:
        return

    r = wt.RECT()
    if not user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r)):
        return

    flags = 0x0004 | 0x0010      # SWP_NOZORDER | SWP_NOACTIVATE

    if state.get("maximized"):
        rect = state.get("restore_rect")
        if not rect:
            cfg = load_config()
            w = int(cfg.get("width") or DEFAULTS["width"])
            h = int(cfg.get("height") or DEFAULTS["height"])
            ml, mt, mr, mb = monitor_work_area(r.left + 40, r.top + 1)
            rect = (max(ml, mr - w - 16), mt + 40, w, h)
        user32.SetWindowPos(wt.HWND(hwnd), wt.HWND(0),
                            int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]), flags)
        state["maximized"] = False
        # 还原后回到「窗口置顶」设置值（最大化期间是被强制撤掉的）
        apply_topmost(hwnd, bool(state.get("on_top", True)))
        log(f"restore -> {rect} topmost={bool(state.get('on_top', True))}")
        ah = state.get("autohide")
        if ah:
            # 还原后的位置可能又贴着屏幕顶边，先别急着自动收起，
            # 否则用户会觉得"点了还原没反应"。等他碰过窗口再离开才收。
            ah.armed = False
            ah.outside_since = None
    else:
        ah = state.get("autohide")
        if ah and ah.hidden:
            ah.force_expand()
        state["restore_rect"] = (r.left, r.top, r.right - r.left, r.bottom - r.top)
        ml, mt, mr, mb = monitor_work_area(r.left + 40, r.top + 1)
        user32.SetWindowPos(wt.HWND(hwnd), wt.HWND(0),
                            ml, mt, mr - ml, mb - mt, flags)
        state["maximized"] = True
        # 最大化等于铺满整屏，此时还置顶会把桌面上的其他窗口全挡死：
        # 撤掉置顶，等还原再按「窗口置顶」设置恢复。
        apply_topmost(hwnd, False)
        log(f"maximize -> ({ml},{mt}) {mr - ml}x{mb - mt} topmost=False")

    sync_max_button()


# ---------------------------------------------------------------- 图标
#
# 一处画法，两处使用：exe 图标（app.ico 由 _mkicon.py 调用本函数生成）和托盘图标。
# 造型 = 对角渐变圆角方块 + 白色「AI」大字 + 字距拉开的「MAX」小字。

LOGO_C1 = (59, 107, 255)      # 渐变起：蓝
LOGO_C2 = (139, 92, 246)      # 渐变止：紫
LOGO_RADIUS = 0.225           # 圆角占边长的比例（接近 Win11 图标观感）


def logo_image(size: int = 128, two_line: bool = True):
    """
    生成 AI MAX 图标（PIL RGBA 图）。

    two_line=False 时只写「AI」——16/24px 下两行字会糊成一团，小尺寸不如单行清楚。
    先按 512 画再降采样，缩小后边缘才不会毛躁。
    """
    from PIL import Image, ImageDraw, ImageFont

    S = 512
    # 对角渐变：左上蓝 → 右下紫
    grad = Image.new("RGB", (S, S))
    px = grad.load()
    for y in range(S):
        for x in range(S):
            t = (x + y) / (2.0 * (S - 1))
            px[x, y] = (int(round(LOGO_C1[0] + (LOGO_C2[0] - LOGO_C1[0]) * t)),
                        int(round(LOGO_C1[1] + (LOGO_C2[1] - LOGO_C1[1]) * t)),
                        int(round(LOGO_C1[2] + (LOGO_C2[2] - LOGO_C1[2]) * t)))

    rad = int(S * LOGO_RADIUS)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=rad, fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    # 内侧细描边：深色任务栏上也能看清图标边界
    d.rounded_rectangle([S * 0.004, S * 0.004, S * 0.996, S * 0.996],
                        radius=rad, outline=(255, 255, 255, 40), width=max(1, S // 300))

    try:
        bold = r"C:\Windows\Fonts\segoeuib.ttf"
        f_ai = ImageFont.truetype(bold, int(S * (0.42 if two_line else 0.60)))
        f_mx = ImageFont.truetype(bold, int(S * 0.195))
    except Exception:
        f_ai = f_mx = ImageFont.load_default()

    if two_line:
        d.text((S * 0.5, S * 0.395), "AI", font=f_ai, fill=(255, 255, 255, 255), anchor="mm")
        # MAX 手工排字：PIL 没有字距参数，逐字画才能拉开
        txt, gap = "MAX", S * 0.032
        widths = [d.textlength(c, font=f_mx) for c in txt]
        x = S * 0.5 - (sum(widths) + gap * (len(txt) - 1)) / 2
        for c, w in zip(txt, widths):
            d.text((x, S * 0.712), c, font=f_mx, fill=(255, 255, 255, 240), anchor="lm")
            x += w + gap
    else:
        d.text((S * 0.5, S * 0.5), "AI", font=f_ai, fill=(255, 255, 255, 255), anchor="mm")

    if size == S:
        return img
    return img.resize((int(size), int(size)), Image.LANCZOS)


# ---------------------------------------------------------------- 托盘

def make_tray_image():
    """托盘图标与 exe 图标同款（同一套画法，改一处两边一起变）。"""
    return logo_image(128, two_line=True)


def sync_tray_icon() -> None:
    tray = state["tray"]
    if not tray:
        return
    try:
        name = sites()[active_index()]["name"]
    except Exception:
        name = "DeepSeek"
    try:
        tray.title = (f"{TITLE} v{VERSION} · {name} · "
                      f"{'已显示' if window_visible() else '已隐藏'}")
    except Exception:
        pass


def start_tray(cfg: dict) -> None:
    import pystray

    def _toggle(icon=None, item=None):
        toggle_window()

    def _top(icon=None, item=None):
        set_on_top(not state["on_top"])

    def _maximize(icon=None, item=None):
        threading.Thread(target=toggle_maximize, daemon=True).start()

    def _minimize(icon=None, item=None):
        minimize_to_tray()

    def _reload(icon=None, item=None):
        reload_page()

    def _reset(icon=None, item=None):
        reset_position()

    def _autohide(icon=None, item=None):
        set_auto_hide(not state.get("auto_hide", True))

    def _add_site(icon=None, item=None):
        add_site_flow()

    def _next_site(icon=None, item=None):
        cycle_site()

    def _quit(icon=None, item=None):
        quit_app()

    def _check_update(icon=None, item=None):
        check_update(manual=True)

    def _update_url(icon=None, item=None):
        update_url_flow()

    width_menu = pystray.Menu(*[
        pystray.MenuItem(
            label,
            (lambda w=w: set_visual_width(w)),
            checked=(lambda i, w=w: state.get("width") == w),
            radio=True,
        )
        for label, w in (("窄 400", 400), ("标准 444", 444),
                         ("宽 560", 560), ("超宽 680", 680))
    ])

    theme_menu = pystray.Menu(*[
        pystray.MenuItem(
            label,
            (lambda n=n: set_theme(n)),
            checked=(lambda i, n=n: theme_name() == n),
            radio=True,
        )
        for label, n in (("深色", "dark"), ("浅色", "light"))
    ])

    items = [
        pystray.MenuItem("显示 / 隐藏    Alt+D", _toggle, default=True),
        pystray.MenuItem("最小化到托盘", _minimize),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("下一个站点", _next_site),
        pystray.MenuItem("添加 AI 站点\u2026", _add_site),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("界面配色", theme_menu),
        pystray.MenuItem("窗口置顶", _top, checked=lambda i: state["on_top"]),
        pystray.MenuItem("最大化 / 还原", _maximize),
        pystray.MenuItem("窗口宽度", width_menu),
        pystray.MenuItem("贴顶自动隐藏", _autohide, checked=lambda i: state.get("auto_hide", True)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("重新加载页面", _reload),
        pystray.MenuItem("重置窗口位置", _reset),
        pystray.Menu.SEPARATOR,
        # 版本号：只作展示，点了没反应（置灰不可点）
        pystray.MenuItem(lambda i: f"{TITLE}  v{VERSION}", lambda i, it: None,
                         enabled=False),
        pystray.MenuItem("检查更新", _check_update),
        pystray.MenuItem("设置更新地址\u2026", _update_url),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", _quit),
    ]

    tray = pystray.Icon(APP_NAME, make_tray_image(), f"{TITLE} v{VERSION}", pystray.Menu(*items))
    state["tray"] = tray
    # 用守护线程跑，避免窗口关掉后托盘线程把进程吊住
    threading.Thread(target=tray.run, daemon=True, name="tray").start()
    log("tray started")


# ---------------------------------------------------------------- 位置监控

monitor_stop = threading.Event()


def geometry_monitor() -> None:
    """
    只记录窗口位置，不记录尺寸。
    frameless 窗口不支持边缘拖拽改尺寸，若把 GetWindowRect 的外框尺寸
    回写进配置，下次启动会被当成请求尺寸，窗口就会逐次缩小。
    """
    hwnd = state["hwnd"]
    if not hwnd:
        return
    rect = wt.RECT()
    while not monitor_stop.is_set():
        try:
            # 自动收起时的偏移位置不能写进配置，否则下次启动窗口会跑到屏幕外
            # 最大化状态同理：全屏矩形写进配置，下次启动就变成铺满屏
            if (not state.get("auto_hide_hidden")
                    and not state.get("maximized")
                    and user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(rect))):
                cur = (rect.left, rect.top)
                if cur != state["last_pos"]:
                    state["last_pos"] = cur
                    cfg = load_config()
                    cfg.update({"x": cur[0], "y": cur[1]})
                    save_config(cfg)
        except Exception:
            pass
        monitor_stop.wait(1.0)


# ---------------------------------------------------------------- 远程更新
#
# 更新源有两种写法，都填在 config 的 update_url 里（托盘「设置更新地址」可改）：
#
#   1) GitHub Releases（默认）——"owner/repo" 或 "https://github.com/owner/repo"
#      查 api.github.com/repos/<owner>/<repo>/releases/latest：版本号取 tag_name，
#      说明取 release 正文，下载地址取 zip/exe 资产，校验值优先用资产的 digest。
#      仓库必须公开，否则匿名读不到 Releases、也下载不了资产。
#
#   2) JSON 清单直链——任何支持「匿名 HTTPS 直链」的地方都行：
#      {"version": "1.2.0",
#       "url":  "https://…/AIMAX.zip",     # exe 或 zip（zip 里取第一个 exe）
#       "notes": "这次改了什么",             # 可省略
#       "sha256": "…"}                      # 可省略，给了就校验
#
# 留空 = 用内置 UPDATE_SOURCE；填 off / none / - 表示彻底不检查（离线机、内网）。
#
# 为什么资料库不能直接当下载源：它的下载链接是登录态下签发的短时效地址，
# 发布出来的页面又是 SPA 壳（HTML 里没有正文），裸 exe 也不在允许的文件类型里。
# 所以资料库里放的是「分发包 + 版本说明」，软件端的自动检查走 GitHub Releases。

UPDATE_SOURCE = "GitHubJackLiu/aimax"    # 内置更新源：owner/repo
GH_API = "https://api.github.com"

# 实测：api.github.com 国内可直连，但 github.com/…/releases/download/… 常被卡住。
# 下载失败时按顺序试这些公共加速前缀（内容有 sha256 校验，被改包会直接拒绝）。
UPDATE_MIRRORS = (
    "https://ghfast.top/",
    "https://gh-proxy.com/",
    "https://ghproxy.net/",
)

UPDATE_CHECK_DELAY = 6.0      # 启动后延迟检查，先让页面加载完


def parse_version(v) -> tuple:
    """'v1.2.3' -> (1, 2, 3)；解析不出来的段落按 0 算。"""
    out = []
    for seg in str(v or "").strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in seg if ch.isdigit())
        out.append(int(digits) if digits else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out[:3])


def parse_update_source(raw: str):
    """
    把「设置更新地址」里填的东西识别成：
        ('github', 'owner/repo')   GitHub Releases 模式
        ('manifest', 'https://…')  JSON 清单模式
        (None, None)               关闭自动检查
    """
    s = str(raw or "").strip()
    if not s:
        s = UPDATE_SOURCE
    if s.lower() in ("off", "none", "0", "false", "-", "no"):
        return (None, None)
    m = re.match(r"^https?://(?:www\.)?github\.com/([\w.\-]+)/([\w.\-]+?)(?:\.git)?/*$",
                 s, re.I)
    if m:
        return ("github", f"{m.group(1)}/{m.group(2)}")
    if re.match(r"^[\w.\-]+/[\w.\-]+$", s):
        return ("github", s)
    return ("manifest", s)


def _gh_json(url: str):
    """匿名调 GitHub REST（公开仓库无需 token）。"""
    import urllib.request
    req = urllib.request.Request(url, headers={
        "User-Agent": f"{APP_NAME}/{VERSION}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Cache-Control": "no-cache",
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def github_release_info(repo: str) -> dict:
    """读 latest release，整理成和清单一样的结构。"""
    data = _gh_json(f"{GH_API}/repos/{repo}/releases/latest")
    tag = str(data.get("tag_name") or "").strip()
    if not tag:
        raise RuntimeError("release 没有 tag_name")

    assets = data.get("assets") or []
    pick = None
    for a in assets:                       # 优先 zip（分发包），其次 exe
        name = str(a.get("name") or "").lower()
        if name.endswith(".zip"):
            pick = a
            break
    if pick is None:
        for a in assets:
            if str(a.get("name") or "").lower().endswith(".exe"):
                pick = a
                break
    if pick is None:
        raise RuntimeError("release 里没有 zip/exe 资产")

    out = {
        "version": tag,
        "url": str(pick.get("browser_download_url") or ""),
        "notes": str(data.get("body") or "").strip(),
        "size": int(pick.get("size") or 0),
        "source": f"github:{repo}",
    }
    digest = str(pick.get("digest") or "")
    if digest.lower().startswith("sha256:"):
        out["sha256"] = digest.split(":", 1)[1].strip().lower()
    return out


def update_url() -> str:
    return str(load_config().get("update_url") or "").strip()


def set_update_url(url: str) -> None:
    cfg = load_config()
    cfg["update_url"] = str(url or "").strip()
    save_config(cfg)
    log(f"update_url -> {cfg['update_url'] or '(empty)'}")


def toast(msg: str, title: str = TITLE) -> None:
    """托盘气泡提示（后台线程也可调）。"""
    tray = state.get("tray")
    if tray is None:
        log(f"toast: {msg}")
        return
    try:
        tray.notify(msg, title)
    except Exception as e:
        log(f"toast failed: {e!r}")


def _open_url_stream(url: str, timeout: float = 12.0):
    import urllib.request
    req = urllib.request.Request(url, headers={
        "User-Agent": f"{APP_NAME}/{VERSION}",
        "Cache-Control": "no-cache",
    })
    return urllib.request.urlopen(req, timeout=timeout)


def check_update(manual: bool = False) -> None:
    """检查更新（非阻塞）。manual=True 时无论结果如何都会给用户反馈。"""
    threading.Thread(target=_check_worker, args=(bool(manual),),
                     daemon=True, name="update-check").start()


def _check_worker(manual: bool) -> None:
    kind, target = parse_update_source(update_url())
    if kind is None:
        log("update check skipped: disabled by config")
        if manual:
            toast("自动检查更新已关闭（设置更新地址里填 off 即关闭）")
        return

    try:
        if kind == "github":
            log(f"update check: github {target}")
            data = github_release_info(target)
        else:
            log(f"update check: manifest {target}")
            with _open_url_stream(target) as r:
                raw = r.read()
            data = json.loads(raw.decode("utf-8", "replace"))
    except Exception as e:
        log(f"update check failed ({kind} {target}): {e!r}")
        if manual:
            toast(f"检查更新失败：{kind} 源连不上或没有可用版本")
        return

    remote = str(data.get("version") or "").strip()
    if not remote:
        log("update check: manifest has no version")
        if manual:
            toast("更新清单里没有版本号")
        return
    if parse_version(remote) <= parse_version(VERSION):
        log(f"update check: latest {remote} (local {VERSION})")
        if manual:
            toast(f"已是最新版本 v{VERSION}")
        return

    log(f"update available: {remote} (local {VERSION})")
    state["update_info"] = data
    invoke_gui(lambda: show_update_dialog(data))


def show_update_dialog(data: dict) -> None:
    """发现新版本时的小窗：显示版本号与更新说明，让用户决定。"""
    from System.Windows.Forms import (
        Form, FormBorderStyle, FormStartPosition, Button, FlatStyle, Cursors, Label,
    )
    from System.Drawing import Point, Size, Font, FontStyle, Color

    old = state.get("update_dialog")
    if old is not None:
        try:
            old.Close()
        except Exception:
            pass
        state["update_dialog"] = None

    P = palette()
    s = ui_scale()

    def S(v):
        return int(round(v * s))

    remote = str(data.get("version") or "").strip()
    notes = str(data.get("notes") or "").strip()

    W = S(400)
    body = notes if notes else "（没有提供更新说明）"
    if len(body) > 400:
        body = body[:400] + "…"

    # 说明按对话框宽度折行后的实际高度——原来写死 64px，长说明会被裁成一行看不全
    body_h = S(64)
    try:
        from System.Windows.Forms import TextRenderer, TextFormatFlags
        _f = Font("Segoe UI", UI_FONT_PT, FontStyle.Regular)
        _m = TextRenderer.MeasureText(body, _f, Size(W - S(40), 0),
                                      TextFormatFlags.WordBreak)
        body_h = max(S(20), min(S(200), _m.Height + S(4)))
    except Exception as e:
        log(f"update dialog measure failed: {e!r}")

    H = max(S(206), S(74) + body_h + S(14) + S(32) + S(20))

    form = Form()
    form.FormBorderStyle = getattr(FormBorderStyle, "None")
    form.StartPosition = FormStartPosition.CenterScreen
    form.MaximizeBox = False
    form.MinimizeBox = False
    form.ShowInTaskbar = False
    form.TopMost = True
    form.ClientSize = Size(W, H)
    form.BackColor = P["panel"]
    try:
        form.Font = Font("Segoe UI", UI_FONT_PT, FontStyle.Regular)
    except Exception:
        pass

    def on_paint(s_, e):
        from System.Drawing import Pen
        pen = Pen(P["edge"], 1)
        try:
            e.Graphics.DrawRectangle(pen, 0, 0, W - 1, H - 1)
        finally:
            pen.Dispose()

    form.Paint += on_paint

    ttl = Label()
    ttl.Text = f"发现新版本  v{remote.lstrip('vV')}"
    ttl.ForeColor = P["strong"]
    ttl.BackColor = Color.Transparent
    ttl.Location = Point(S(20), S(18))
    ttl.Size = Size(W - S(40), S(24))
    form.Controls.Add(ttl)

    cur = Label()
    _size = int(data.get("size") or 0)
    cur.Text = (f"当前版本 v{VERSION}   ·   安装包 {_fmt_size(_size)}"
                if _size > 0 else f"当前版本 v{VERSION}")
    cur.ForeColor = P["dim"]
    cur.BackColor = Color.Transparent
    cur.Location = Point(S(20), S(46))
    cur.Size = Size(W - S(40), S(20))
    form.Controls.Add(cur)

    txt = Label()
    txt.Text = body
    txt.ForeColor = P["fg"]
    txt.BackColor = Color.Transparent
    txt.AutoSize = False
    txt.Location = Point(S(20), S(74))
    txt.Size = Size(W - S(40), body_h)
    form.Controls.Add(txt)

    def mk(text, primary):
        b = Button()
        b.Text = text
        b.Size = Size(S(96), S(32))
        b.FlatStyle = FlatStyle.Flat
        b.FlatAppearance.BorderSize = 0 if primary else 1
        if not primary:
            b.FlatAppearance.BorderColor = P["edge"]
        b.BackColor = P["accent"] if primary else P["bg"]
        b.ForeColor = Color.White if primary else P["fg"]
        b.Cursor = Cursors.Hand
        b.TabStop = False
        return b

    btn_ok = mk("立即更新", True)
    btn_no = mk("稍后", False)
    btn_no.Location = Point(W - S(20) - S(96) * 2 - S(10), H - S(20) - S(32))
    btn_ok.Location = Point(W - S(20) - S(96), H - S(20) - S(32))
    form.Controls.Add(btn_no)
    form.Controls.Add(btn_ok)

    def close_self():
        state["update_dialog"] = None
        try:
            form.Close()
        except Exception:
            pass

    def on_ok(s_, e):
        close_self()
        log("update: user accepted")
        start_download(data)

    def on_no(s_, e):
        close_self()
        log("update: user postponed")

    btn_ok.Click += on_ok
    btn_no.Click += on_no
    form.FormClosed += (lambda s_, e: state.update({"update_dialog": None}))

    state["update_dialog"] = form
    form.Show()


# ---------------------------------------------------------------- 下载进度

class UpdateCancelled(Exception):
    """用户在进度窗里点了取消。"""


def _fmt_size(n: float) -> str:
    """1536 -> '1.5 KB'；总量未知（0）时返回 '?'。"""
    n = float(n or 0)
    if n <= 0:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return "?"


def _fmt_secs(sec: float) -> str:
    sec = int(max(0, sec))
    if sec < 60:
        return f"{sec} 秒"
    if sec < 3600:
        return f"{sec // 60} 分 {sec % 60} 秒"
    return f"{sec // 3600} 小时 {(sec % 3600) // 60} 分"


def _dl_state() -> dict:
    st = state.get("dl_progress")
    if not isinstance(st, dict):
        st = {"remote": "", "total": 0, "got": 0, "speed": 0.0, "eta": 0.0,
              "cancel": False, "samples": [], "phase": "downloading",
              "started": time.time()}
        state["dl_progress"] = st
    return st


def _dl_update(got: int, total: int) -> None:
    """下载线程每个 chunk 调一次；只写共享状态，界面由定时器读。"""
    st = _dl_state()
    st["got"] = int(got)
    if total > 0:
        st["total"] = int(total)


def _dl_reset(total: int) -> None:
    """换下一个镜像重试时，把已下载量清零重算。"""
    st = _dl_state()
    st["got"] = 0
    st["samples"] = []
    st["speed"] = 0.0
    st["eta"] = 0.0
    if total > 0:
        st["total"] = int(total)


def _dl_should_cancel() -> bool:
    return bool(_dl_state().get("cancel"))


def _dl_icon(g, cx: float, cy: float, size: float, color, kind: str = "download") -> None:
    """16×16 逻辑网格的矢量图标（下载箭头 / 对勾 / 叉）。"""
    from System.Drawing import PointF, Pen
    k = size / 16.0
    pen = Pen(color, max(1.2, size * 0.11))
    try:
        def P(x, y):
            return PointF(cx + x * k, cy + y * k)

        if kind == "download":
            g.DrawLine(pen, P(0, -5.4), P(0, 2.8))
            g.DrawLine(pen, P(0, 2.8), P(-3.5, -0.7))
            g.DrawLine(pen, P(0, 2.8), P(3.5, -0.7))
            g.DrawLine(pen, P(-4.9, 5.6), P(4.9, 5.6))
        elif kind == "check":
            g.DrawLines(pen, [P(-4.6, 0.4), P(-1.2, 3.9), P(4.8, -3.4)])
        else:      # cross
            g.DrawLine(pen, P(-3.8, -3.8), P(3.8, 3.8))
            g.DrawLine(pen, P(-3.8, 3.8), P(3.8, -3.8))
    finally:
        pen.Dispose()


def build_dl_indicator(th: int, btn_h: int):
    """
    标题栏上的「下载」圆形按钮。

    空闲：淡色下载图标；下载中：外圈显示进度弧（浏览器那种）；
    悬停：按钮下方弹出详情面板（进度条 / 速度 / 剩余 / 取消）。
    """
    from System.Windows.Forms import Panel, Cursors, PaintEventArgs
    from System.Drawing import (
        Point, Size, Rectangle, RectangleF, Pen, SolidBrush, Color,
    )
    from System.Drawing.Drawing2D import SmoothingMode, GraphicsPath

    P = palette()
    ind = Panel()
    ind.BackColor = P["bg"]
    ind.TabStop = False
    ind.Cursor = Cursors.Hand
    ind.Size = Size(btn_h, btn_h)
    ind.Visible = False        # 只在下载更新时出现
    try:
        from System.Reflection import BindingFlags as _BF
        _p = ind.GetType().GetProperty("DoubleBuffered",
                                       _BF.Instance | _BF.NonPublic)
        _p.SetValue(ind, True)
    except Exception:
        pass

    ui = {"hot": False}
    ring_w = max(2, int(round(th * 0.115)))
    icon_s = max(12, int(round(th * 1.0)))     # 与左侧导航图标同尺寸（NAV_ICON 规则）

    def on_paint(s_, e):
        PP = palette()
        g = e.Graphics
        try:
            g.SmoothingMode = SmoothingMode.AntiAlias
        except Exception:
            pass
        g.Clear(PP["bg"])
        w, h = ind.Width, ind.Height
        if ui["hot"]:
            # 圆形按钮，悬停底色也画成圆（方角底色会显得和图标不搭）
            pad = max(1, int(round(th * 0.10)))
            box = RectangleF(pad, pad, w - pad * 2, h - pad * 2)
            br = SolidBrush(PP["tab_hov"])
            try:
                g.FillEllipse(br, box)
            finally:
                br.Dispose()

        st = state.get("dl_progress")
        active = isinstance(st, dict)
        cx, cy = w / 2.0, h / 2.0
        color = PP["strong"] if ui["hot"] else PP["fg"]

        if active:
            inset = ring_w / 2.0 + max(1.0, th * 0.07)
            rr = min(w, h) / 2.0 - inset
            box = RectangleF(cx - rr, cy - rr, rr * 2, rr * 2)
            base = Pen(PP["field"], ring_w)
            try:
                g.DrawEllipse(base, box)
            finally:
                base.Dispose()
            pct = _dl_pct(st)
            arc = Pen(PP["accent"], ring_w)
            try:
                if pct < 0:
                    deg = (time.time() * 210.0) % 360.0
                    g.DrawArc(arc, box, deg, 96.0)
                elif pct > 0:
                    g.DrawArc(arc, box, -90.0, max(2.0, 359.9 * pct))
            finally:
                arc.Dispose()
            _dl_icon(g, cx, cy, icon_s, color, "download")
        else:
            _dl_icon(g, cx, cy, icon_s, PP["strong"] if ui["hot"] else PP["dim"])

    # 键盘/高 DPI 安全：面板尺寸可能变，重绘时按实时尺寸算，无需重建

    def on_enter(s_, e):
        ui["hot"] = True
        ind.Invalidate()
        try:
            _dl_popup_show()
        except Exception as ex:
            log(f"dl popup failed: {ex!r}")

    def on_leave(s_, e):
        ui["hot"] = False
        ind.Invalidate()

    def on_click(s_, e):
        st = state.get("dl_progress")
        if isinstance(st, dict):
            _dl_popup_show()
            return
        # 空闲时点击 = 手动检查更新
        try:
            check_update(manual=True)
        except Exception as ex:
            log(f"dl indicator click failed: {ex!r}")

    ind.Paint += on_paint
    ind.MouseEnter += on_enter
    ind.MouseLeave += on_leave
    ind.MouseClick += on_click
    return ind


def _dl_widget():
    """标题栏上的圆形下载按钮（自绘 Panel）；还没建出来时返回 None。"""
    return state.get("dl_ind")


def _dl_popup():
    return state.get("dl_popup")


def _dl_timer_start() -> None:
    from System.Windows.Forms import Timer as WinTimer
    t = state.get("dl_timer")
    if t is None:
        t = WinTimer()
        t.Interval = 110
        t.Tick += _dl_tick
        state["dl_timer"] = t
    try:
        t.Start()
    except Exception as e:
        log(f"dl timer start failed: {e!r}")


def _dl_timer_stop() -> None:
    t = state.get("dl_timer")
    if t is not None:
        try:
            t.Stop()
        except Exception:
            pass


def _dl_speed(st: dict) -> float:
    """按最近 2.5 秒的采样算平均速度（字节/秒）。"""
    now = time.time()
    got = int(st.get("got") or 0)
    samples = st.get("samples") or []
    samples.append((now, got))
    samples = [x for x in samples if now - x[0] <= 2.5][-16:]
    st["samples"] = samples
    if len(samples) >= 2:
        t0, g0 = samples[0]
        t1, g1 = samples[-1]
        if t1 - t0 >= 0.3 and g1 >= g0:
            st["speed"] = (g1 - g0) / (t1 - t0)
            st["eta"] = (st.get("total", 0) - g1) / st["speed"] if st.get("speed") else 0.0
    return float(st.get("speed") or 0.0)


def _dl_pct(st: dict) -> float:
    """0..1；总量未知返回 -1（表示画不定量弧）。"""
    tot = int(st.get("total") or 0)
    if tot <= 0:
        return -1.0
    return max(0.0, min(1.0, int(st.get("got") or 0) / float(tot)))


def _dl_tick(sender=None, e=None) -> None:
    """GUI 线程：刷新圆形进度与悬停面板。"""
    st = state.get("dl_progress")
    if not isinstance(st, dict):
        _dl_timer_stop()
        return
    _dl_speed(st)
    ind = _dl_widget()
    if ind is not None:
        try:
            ind.Invalidate()
        except Exception:
            pass
    pop = _dl_popup()
    if pop is not None:
        try:
            if pop.Visible:
                pop.Invalidate()
        except Exception:
            pass


def _dl_begin_ui(remote: str, total: int) -> None:
    """GUI 线程：开始下载时把圆形按钮切到进度态。"""
    st = _dl_state()
    st.update({"remote": str(remote or ""), "total": int(total or 0), "got": 0,
               "speed": 0.0, "eta": 0.0, "cancel": False, "samples": [],
               "phase": "downloading", "started": time.time()})
    state["dl_visible"] = True
    ind = _dl_widget()
    if ind is not None:
        try:
            ind.Visible = True
            ind.Invalidate()
        except Exception:
            pass
    rl = state.get("relayout")
    if rl is not None:
        try:
            rl()
        except Exception:
            pass
    _dl_timer_start()


def _dl_end_ui() -> None:
    """GUI 线程：下载结束（成功/失败/取消），圆形按钮隐藏。"""
    state["dl_progress"] = None
    state["dl_visible"] = False
    _dl_timer_stop()
    _dl_popup_hide()
    ind = _dl_widget()
    if ind is not None:
        try:
            ind.Visible = False
            ind.Invalidate()
        except Exception:
            pass
    rl = state.get("relayout")
    if rl is not None:
        try:
            rl()
        except Exception:
            pass


def _dl_finish() -> None:
    """工作线程可调：收掉进度界面。"""
    try:
        invoke_gui(_dl_end_ui)
    except Exception:
        state["dl_progress"] = None


# ---------------------------------------------------------------- 悬停详情面板

POPUP_W, POPUP_H = 262, 108


def _dl_popup_rects(w, h):
    """面板里的点击热区（取消按钮）。"""
    s = ui_scale()
    bw, bh = int(round(66 * s)), int(round(22 * s))
    return {"cancel": (w - int(round(12 * s)) - bw,
                       h - int(round(10 * s)) - bh, bw, bh)}


def _dl_popup_build():
    from System.Windows.Forms import Form, FormBorderStyle, FormStartPosition
    from System.Drawing import Size as DSize, Color
    P = palette()

    pop = Form()
    pop.FormBorderStyle = getattr(FormBorderStyle, "None")
    pop.ShowInTaskbar = False
    pop.TopMost = True
    pop.StartPosition = FormStartPosition.Manual
    pop.BackColor = P["panel"]
    pop.ClientSize = DSize(int(round(POPUP_W * ui_scale())), int(round(POPUP_H * ui_scale())))

    def on_paint(s_, e):
        _dl_popup_paint(pop, e)

    pop.Paint += on_paint

    def on_click(s_, e):
        w, h = pop.ClientSize.Width, pop.ClientSize.Height
        r = _dl_popup_rects(w, h)["cancel"]
        if r is None:
            return
        x, y, bw, bh = r
        if x <= e.X <= x + bw and y <= e.Y <= y + bh:
            st = state.get("dl_progress")
            if isinstance(st, dict):
                st["cancel"] = True
                log("update: cancel requested (indicator)")
                pop.Invalidate()

    pop.MouseClick += on_click
    return pop


def _dl_popup_paint(pop, e) -> None:
    from System.Drawing import Font, FontStyle, Pen, SolidBrush, Rectangle, Color
    from System.Drawing.Drawing2D import SmoothingMode, GraphicsPath

    P = palette()
    s = ui_scale()

    def S(v):
        return int(round(v * s))

    st = state.get("dl_progress")
    active = isinstance(st, dict)
    if not active:
        st = {}

    g = e.Graphics
    try:
        g.SmoothingMode = SmoothingMode.AntiAlias
    except Exception:
        pass
    w, h = pop.ClientSize.Width, pop.ClientSize.Height
    g.Clear(P["panel"])

    pen = Pen(P["edge"], 1)
    try:
        g.DrawRectangle(pen, 0, 0, w - 1, h - 1)
    finally:
        pen.Dispose()

    try:
        f_ttl = Font("Segoe UI", UI_FONT_PT, FontStyle.Bold)
        f_txt = Font("Segoe UI", UI_FONT_PT - 0.5)
    except Exception:
        f_ttl = f_txt = None

    def text(t, font, color, x, y):
        if font is None:
            return
        br = SolidBrush(color)
        try:
            g.DrawString(t, font, br, float(x), float(y))
        finally:
            br.Dispose()

    remote = str(st.get("remote") or "").lstrip("vV")
    if active:
        title = f"正在下载  v{remote}" if remote else "正在下载更新"
    else:
        title = f"{TITLE}  v{VERSION}"
    text(title, f_ttl, P["strong"], S(13), S(9))

    tot = int(st.get("total") or 0)
    got = int(st.get("got") or 0)
    pct = _dl_pct(st) if active else -1.0

    # 进度条：底槽 + 已下载
    bx, by, bh = S(13), S(38), S(6)
    bw = w - S(26)
    rad = bh / 2.0

    def capsule(x, y, cw, ch, r):
        p = GraphicsPath()
        d = r * 2.0
        p.AddArc(x, y, d, d, 180, 90)
        p.AddArc(x + cw - d, y, d, d, 270, 90)
        p.AddArc(x + cw - d, y + ch - d, d, d, 0, 90)
        p.AddArc(x, y + ch - d, d, d, 90, 90)
        p.CloseFigure()
        return p

    slot = SolidBrush(P["field"])
    try:
        g.FillPath(slot, capsule(bx, by, bw, bh, rad))
    finally:
        slot.Dispose()

    if active:
        if pct > 0:
            br = SolidBrush(P["accent"])
            try:
                g.FillPath(br, capsule(bx, by, max(bh, bw * pct), bh, rad))
            finally:
                br.Dispose()
        elif pct < 0:
            seg = bw * 0.3
            span = bw - seg
            pos = (time.time() * 0.55) % 2.0
            x = pos * span if pos <= 1.0 else (2.0 - pos) * span
            br = SolidBrush(P["accent"])
            try:
                g.FillPath(br, capsule(bx + x, by, seg, bh, rad))
            finally:
                br.Dispose()

    if active:
        spd = float(st.get("speed") or 0.0)
        head = (f"{pct * 100:.0f}%  ·  {_fmt_size(got)} / {_fmt_size(tot)}"
                if pct >= 0 else f"已下载 {_fmt_size(got)}")
        text(head, f_txt, P["fg"], S(13), S(50))
        tail = ""
        if spd > 0:
            tail = f"{_fmt_size(spd)}/s"
            eta = float(st.get("eta") or 0.0)
            if pct >= 0 and eta > 0:
                tail += f"  ·  剩余 {_fmt_secs(eta)}"
        if tail:
            text(tail, f_txt, P["dim"], S(13), S(68))
        if st.get("cancel"):
            text("正在取消…", f_txt, P["dim"], S(13), S(68))

        # 取消按钮
        x, y, cbw, cbh = _dl_popup_rects(w, h)["cancel"]
        bpen = Pen(P["edge"], 1)
        try:
            g.DrawRectangle(bpen, x, y, cbw - 1, cbh - 1)
        finally:
            bpen.Dispose()
        canceling = bool(st.get("cancel"))
        text("取消中" if canceling else "取消下载", f_txt,
             P["dim"] if canceling else P["fg"], x + S(12), y + S(3))
    else:
        text("点击圆形按钮检查更新", f_txt, P["dim"], S(13), S(50))
        text(f"当前版本 v{VERSION} · 检查 GitHub Releases", f_txt, P["dim"], S(13), S(68))

    if f_ttl is not None:
        try:
            f_ttl.Dispose()
            f_txt.Dispose()
        except Exception:
            pass


def _dl_popup_watch_tick(sender=None, e=None) -> None:
    """鼠标离开按钮与面板后自动收起。"""
    from System.Windows.Forms import Cursor
    pop = _dl_popup()
    ind = _dl_widget()
    if pop is None or not pop.Visible:
        t = state.get("dl_watch")
        if t is not None:
            try:
                t.Stop()
            except Exception:
                pass
        return
    try:
        cp = Cursor.Position
        inside = False
        for c in (ind, pop):
            if c is None:
                continue
            r = c.RectangleToScreen(c.ClientRectangle)
            if r.Contains(cp):
                inside = True
                break
        if not inside:
            _dl_popup_hide()
    except Exception as e:
        log(f"dl popup watch failed: {e!r}")
        _dl_popup_hide()


def _dl_popup_hide() -> None:
    pop = _dl_popup()
    if pop is not None:
        try:
            if pop.Visible:
                pop.Hide()
        except Exception:
            pass
    t = state.get("dl_watch")
    if t is not None:
        try:
            t.Stop()
        except Exception:
            pass


def _dl_popup_hide_async() -> None:
    """窗口被隐藏/最小化时也从任意线程收面板（跨线程直接 Hide 不安全）。"""
    if state.get("dl_popup") is None:
        return
    try:
        invoke_gui(_dl_popup_hide)
    except Exception:
        pass


def _dl_popup_show() -> None:
    """在圆形按钮下方弹出详情面板（右对齐）。"""
    from System.Drawing import Point as DPoint
    from System.Windows.Forms import Timer as WinTimer

    ind = _dl_widget()
    form = state.get("form")
    if ind is None or form is None:
        return
    pop = _dl_popup()
    if pop is None:
        pop = _dl_popup_build()
        state["dl_popup"] = pop

    try:
        p = ind.PointToScreen(DPoint(0, 0))
        fr = form.RectangleToScreen(form.ClientRectangle)
        w, h = pop.ClientSize.Width, pop.ClientSize.Height
        # 右对齐到窗口右边（浏览器下载面板的习惯位置）
        x = fr.Right - w - int(round(6 * ui_scale()))
        y = p.Y + ind.Height + int(round(4 * ui_scale()))
        try:
            left, top, right, bottom = monitor_work_area(int(p.X), int(p.Y))
            x = max(left + 4, min(x, right - w - 4))
            y = max(top + 4, min(y, bottom - h - 4))
        except Exception:
            pass
        pop.Location = DPoint(int(x), int(y))
    except Exception as e:
        log(f"dl popup place failed: {e!r}")

    try:
        if not pop.Visible:
            pop.Show()
        pop.BringToFront()
        pop.Invalidate()
    except Exception as e:
        log(f"dl popup show failed: {e!r}")

    t = state.get("dl_watch")
    if t is None:
        t = WinTimer()
        t.Interval = 120
        t.Tick += _dl_popup_watch_tick
        state["dl_watch"] = t
    try:
        t.Start()
    except Exception:
        pass



def start_download(data: dict) -> None:
    toast("正在下载新版本…")
    threading.Thread(target=_download_worker, args=(dict(data),),
                     daemon=True, name="update-download").start()


def _fetch_file(url: str, path: str, timeout: float = 30.0,
                on_progress=None, should_cancel=None) -> str:
    """把 url 下载到 path，返回 sha256 十六进制。空响应按失败处理。"""
    import hashlib
    h = hashlib.sha256()
    got = 0
    with _open_url_stream(url, timeout=timeout) as r, open(path, "wb") as f:
        try:
            total = int(r.headers.get("Content-Length") or 0)
        except Exception:
            total = 0
        if on_progress:
            on_progress(0, total)
        while True:
            if should_cancel and should_cancel():
                raise UpdateCancelled("user cancelled")
            chunk = r.read(64 * 1024)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            got += len(chunk)
            if on_progress:
                on_progress(got, total)
    if got <= 0:
        raise RuntimeError("空响应")
    log(f"update: fetched {got} bytes")
    return h.hexdigest()


def _download_worker(data: dict) -> None:
    url = str(data.get("url") or "").strip()
    if not url:
        log("update: manifest has no url")
        toast("更新清单里没有下载地址")
        return

    remote = str(data.get("version") or "").strip().lstrip("vV")
    try:
        expected = int(data.get("size") or 0)
    except Exception:
        expected = 0

    d = os.path.join(tempfile.gettempdir(), "AIMAX-update")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    ext = ".zip" if url.lower().split("?")[0].endswith(".zip") else ".exe"
    raw_path = os.path.join(d, "download" + ext)

    # 直连 github.com 常被卡住：失败后依次退到 config 里的 update_mirror 和内置加速前缀
    seen = []
    for pre in ([str(load_config().get("update_mirror") or "").strip()]
                + list(UPDATE_MIRRORS)):
        pre = pre.strip().rstrip("/")
        cand = url if not pre else pre + "/" + url
        if cand not in seen:
            seen.append(cand)

    state["dl_progress"] = None          # 清掉上一轮残留
    invoke_gui(lambda: _dl_begin_ui(remote, expected))

    digest = ""
    err = None
    for i, u in enumerate(seen):
        try:
            log(f"update: downloading [{i + 1}/{len(seen)}] {u}")
            if i > 0:
                _dl_reset(expected)      # 换镜像重来，进度清零
            digest = _fetch_file(u, raw_path,
                                 timeout=12.0 if i == 0 else 90.0,
                                 on_progress=_dl_update,
                                 should_cancel=_dl_should_cancel)
            break
        except UpdateCancelled:
            log("update: cancelled by user")
            _dl_finish()
            toast("已取消本次更新")
            return
        except Exception as e:
            err = e
            log(f"update download failed ({u}): {e!r}")
            if _dl_should_cancel():
                log("update: cancelled by user")
                _dl_finish()
                toast("已取消本次更新")
                return
    if not digest:
        log(f"update download gave up: {err!r}")
        _dl_finish()
        toast("下载新版本失败，请稍后再试")
        return

    want = str(data.get("sha256") or "").strip().lower()
    if want and digest != want:
        log(f"update: sha256 mismatch {digest} != {want}")
        _dl_finish()
        toast("下载文件校验失败，已放弃本次更新")
        return

    new_exe = os.path.join(d, "AIMAX_new.exe")
    try:
        if ext == ".zip":
            import shutil
            import zipfile
            with zipfile.ZipFile(raw_path) as z:
                names = [n for n in z.namelist() if n.lower().endswith(".exe")]
                if not names:
                    raise RuntimeError("zip 里没有 exe")
                with z.open(names[0]) as src, open(new_exe, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        else:
            os.replace(raw_path, new_exe)
    except Exception as e:
        log(f"update unpack failed: {e!r}")
        _dl_finish()
        toast("更新包内容不对，已放弃本次更新")
        return

    _dl_finish()
    apply_update(new_exe)


def apply_update(new_exe: str) -> None:
    """
    自替换：当前 exe 正在运行，Windows 不允许覆盖自身。

    做法：把新版本以 `--apply-update <目标exe>` 启动，然后本进程退出。
    新进程在 main() 最开头就走这个分支，反复尝试覆盖目标文件
    （旧进程没退干净时会抛 PermissionError，等半秒重试），覆盖成功后启动它。
    这条链路完全在 Python 里，不用 cmd / 批处理 / tasklist —— 那些在部分
    环境（受限终端、安全软件拦截）下会卡住，实测批处理版本会死等。
    """
    import subprocess

    if not getattr(sys, "frozen", False):
        log("update: running from source, skip self-replace")
        toast("源码模式下不自动替换，请手动更新")
        return

    target = os.path.abspath(sys.executable)
    try:
        log(f"update: relaunch helper {new_exe} -> {target}")
        subprocess.Popen([new_exe, "--apply-update", target],
                         creationflags=0x00000008,      # DETACHED_PROCESS
                         close_fds=True)
    except Exception as e:
        log(f"update helper failed: {e!r}")
        toast("无法启动更新程序，请手动替换 exe")
        return

    toast("更新已就绪，正在重启…")
    time.sleep(0.6)
    quit_app()


def run_apply_update(target: str) -> None:
    """以 --apply-update 启动时走这里：等旧进程退出 → 覆盖 → 启动新版本。"""
    import shutil
    import subprocess

    me = os.path.abspath(sys.executable)
    log(f"apply-update: {me} -> {target}")
    deadline = time.time() + 45.0
    ok = False
    while time.time() < deadline:
        try:
            shutil.copyfile(me, target)
            ok = True
            break
        except Exception:
            time.sleep(0.5)
    log(f"apply-update: replaced={ok}")
    try:
        subprocess.Popen([target], creationflags=0x00000008, close_fds=True)
    except Exception as e:
        log(f"apply-update: relaunch failed {e!r}")
    os._exit(0)


def ask_text_dialog(title: str, label: str, value: str, hint: str = ""):
    """单行输入对话框（自绘，风格与主界面一致）。返回输入内容或 None。"""
    from System.Windows.Forms import (
        Form, FormBorderStyle, FormStartPosition, Button, TextBox, Label,
        FlatStyle, Cursors, BorderStyle,
    )
    from System.Drawing import Point, Size, Font, FontStyle, Color

    P = palette()
    s = ui_scale()

    def S(v):
        return int(round(v * s))

    W = S(408)
    form = Form()
    form.FormBorderStyle = getattr(FormBorderStyle, "None")
    form.StartPosition = FormStartPosition.CenterScreen
    form.ShowInTaskbar = False
    form.TopMost = True
    form.BackColor = P["bg"]
    form.ClientSize = Size(W, S(150))
    try:
        form.Font = Font("Segoe UI", UI_FONT_PT, FontStyle.Regular)
    except Exception:
        pass

    def on_paint(s_, e):
        from System.Drawing import Pen
        pen = Pen(P["edge"], 1)
        try:
            e.Graphics.DrawRectangle(pen, 0, 0, form.ClientSize.Width - 1,
                                     form.ClientSize.Height - 1)
        finally:
            pen.Dispose()

    form.Paint += on_paint

    ttl = Label()
    ttl.Text = title
    ttl.ForeColor = P["strong"]
    ttl.BackColor = Color.Transparent
    ttl.Location = Point(S(20), S(16))
    ttl.Size = Size(W - S(40), S(22))
    form.Controls.Add(ttl)

    lab = Label()
    lab.Text = label
    lab.ForeColor = P["dim"]
    lab.BackColor = Color.Transparent
    lab.Location = Point(S(20), S(46))
    lab.Size = Size(S(60), S(20))
    form.Controls.Add(lab)

    tb = TextBox()
    tb.Text = value
    tb.Location = Point(S(84), S(44))
    tb.Width = W - S(84) - S(20)
    tb.BackColor = P["field"]
    tb.ForeColor = P["fg"]
    tb.BorderStyle = BorderStyle.FixedSingle
    try:
        tb.Size = Size(tb.Width, int(tb.PreferredHeight))
    except Exception:
        pass
    form.Controls.Add(tb)

    if hint:
        h = Label()
        h.Text = hint
        h.ForeColor = P["dim"]
        h.BackColor = Color.Transparent
        h.Location = Point(S(20), S(76))
        h.Size = Size(W - S(40), S(20))
        form.Controls.Add(h)

    result = {"v": None}

    def mk(text, primary):
        b = Button()
        b.Text = text
        b.Size = Size(S(88), S(30))
        b.FlatStyle = FlatStyle.Flat
        b.FlatAppearance.BorderSize = 0 if primary else 1
        if not primary:
            b.FlatAppearance.BorderColor = P["edge"]
        b.BackColor = P["accent"] if primary else P["field"]
        b.ForeColor = Color.White if primary else P["fg"]
        b.Cursor = Cursors.Hand
        return b

    ok = mk("确定", True)
    cancel = mk("取消", False)
    ok.Location = Point(W - S(20) - S(88), S(150) - S(16) - S(30))
    cancel.Location = Point(W - S(20) - S(88) * 2 - S(8), S(150) - S(16) - S(30))
    form.Controls.Add(ok)
    form.Controls.Add(cancel)

    def do_ok(s_, e):
        result["v"] = (tb.Text or "").strip()
        form.Close()

    ok.Click += do_ok
    cancel.Click += (lambda s_, e: form.Close())
    form.AcceptButton = ok
    form.CancelButton = cancel
    form.ActiveControl = tb
    form.ShowDialog()
    return result["v"]


def update_url_flow() -> None:
    """托盘：设置更新地址。"""
    def run():
        try:
            cur = update_url() or UPDATE_SOURCE
            v = ask_text_dialog(
                "设置更新地址", "更新源",
                cur,
                "填 owner/repo 或 github.com/owner/repo 走 Releases；"
                "也可填清单 JSON 直链；填 off 关闭检查",
            )
            if v is None:
                return
            set_update_url(v)
            kind, target = parse_update_source(v)
            if kind is None:
                toast("已关闭自动检查更新")
            elif kind == "github":
                toast(f"更新源已保存：GitHub {target}")
            else:
                toast("更新源已保存：清单直链")
        except Exception as e:
            log(f"update_url_flow failed: {e!r}")
    invoke_gui(run)


def check_update_flow() -> None:
    """托盘：立即检查更新。"""
    check_update(manual=True)


# ---------------------------------------------------------------- 启动

def resolve_geometry(cfg: dict):
    """返回视觉尺寸与坐标（不含 CHROME 补偿）。支持多显示器负坐标。"""
    w = int(cfg.get("width") or DEFAULTS["width"])
    h = int(cfg.get("height") or DEFAULTS["height"])

    ml, mt, mr, mb = get_work_area()          # 主屏，作为兜底基准
    w = max(320, min(w, mr - ml))
    h = max(400, min(h, mb - mt))

    vx, vy, vx2, vy2 = virtual_screen()       # 全部屏幕拼成的桌面
    x = cfg.get("x")
    y = cfg.get("y")

    # 位置只要落在虚拟桌面内、且至少留出一部分在屏幕上就沿用；
    # 这样副屏（负坐标）里摆好的位置不会再被拽回主屏。
    x_ok = x is not None and (vx - 20 <= x <= vx2 - 120)
    y_ok = y is not None and (vy - 20 <= y <= vy2 - 120)
    if not x_ok:
        x = mr - w - 16                        # 默认贴主屏右侧
    if not y_ok:
        y = max(mt, (mb - mt - h) // 2 + mt)
    log(f"geometry: virtual=({vx},{vy})-({vx2},{vy2}) -> ({x},{y}) {w}x{h}")
    return w, h, x, y


def set_visual_width(width: int) -> None:
    """切换窗口宽度档位。"""
    hwnd = state["hwnd"]
    if not hwnd:
        return
    rect = wt.RECT()
    user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(rect))
    cur_h = rect.bottom - rect.top
    cur_x = rect.left
    # 注意：SetWindowPos 的 cx 直接就是外框宽度，不像 create_window 会去吃边框，
    # 所以这里不能再加 CHROME_DW。
    user32.SetWindowPos(
        wt.HWND(hwnd), wt.HWND(0), cur_x, rect.top,
        int(width), cur_h,
        0x0004 | 0x0010,   # SWP_NOZORDER | SWP_NOACTIVATE
    )
    cfg = load_config()
    cfg["width"] = int(width)
    save_config(cfg)
    state["width"] = int(width)
    log(f"width -> {width}")
    save_window_size()      # 宽度档位也要覆盖掉上次手拖的尺寸


def save_window_size() -> None:
    """把当前外框尺寸记下来（只在用户手动拖完手柄后调用）。"""
    hwnd = state["hwnd"]
    if not hwnd or state.get("maximized"):
        return
    r = wt.RECT()
    try:
        if not user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r)):
            return
    except Exception:
        return
    w = int(r.right - r.left)
    h = int(r.bottom - r.top)
    if w < 200 or h < 200:
        return
    try:
        cfg = load_config()
        cfg["win_w"] = w
        cfg["win_h"] = h
        save_config(cfg)
        log(f"window size -> {w}x{h}")
    except Exception as e:
        log(f"save_window_size failed: {e!r}")


def apply_saved_size(hwnd: int, cfg: dict) -> None:
    """
    启动时恢复上次手动拖出来的尺寸。

    这里直接写外框尺寸，不走 create_window 的 width/height ——
    那条路上有个 CHROME_DW/DH 的边框补偿，高 DPI 下补偿值会变，
    写回配置就会让窗口每次启动缩一点。
    """
    w = int(cfg.get("win_w") or 0)
    h = int(cfg.get("win_h") or 0)
    if w < 200 or h < 200:
        return
    r = wt.RECT()
    if not user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r)):
        return
    x, y = int(r.left), int(r.top)
    try:
        _, _, right, bottom = monitor_work_area(x + 40, y + 1)
        w = min(w, max(320, int(right - x)))
        h = min(h, max(400, int(bottom - y)))
    except Exception:
        pass
    user32.SetWindowPos(wt.HWND(hwnd), wt.HWND(0), 0, 0, int(w), int(h),
                        SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE)
    log(f"applied saved size {w}x{h}")


def reset_position() -> None:
    """把窗口放到鼠标当前所在那块屏幕的右侧、垂直居中。"""
    hwnd = state["hwnd"]
    if not hwnd:
        return
    if state.get("maximized"):
        toggle_maximize()      # 先退出最大化，否则把全屏窗口挪走很怪
    ah = state.get("autohide")
    if ah:
        ah.force_expand()

    mx, my = cursor_pos()
    left, top, right, bottom = monitor_work_area(mx, my)
    cfg = load_config()
    w = int(cfg.get("width") or DEFAULTS["width"])
    h = int(cfg.get("height") or DEFAULTS["height"])
    x = right - w - 16
    y = max(top, (bottom - top - h) // 2 + top)
    user32.SetWindowPos(
        wt.HWND(hwnd), wt.HWND(0), x, y, 0, 0,
        0x0001 | 0x0004 | 0x0010,
    )  # SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
    log(f"position reset -> ({x},{y}) on monitor ({left},{top})-({right},{bottom})")


# ---------------------------------------------------------------- 贴顶自动隐藏

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010


class TopAutoHide(threading.Thread):
    """
    窗口被拖到屏幕顶边后自动收起，只在屏幕上沿留一条细边；
    鼠标碰到细边就滑下来，移开一会儿再自动收上去。

    判定"贴顶"用的是窗口所在那块屏幕的工作区顶边（rcWork.top），
    所以任务栏在顶部时会自动让开。
    """

    def __init__(self) -> None:
        super().__init__(daemon=True, name="autohide")
        self.hidden = False
        self.animating = False
        self.outside_since = None
        # 只有鼠标进过窗口一次，才允许自动收起。
        # 否则启动时窗口恰好贴顶，会立刻被收上去，启动动画根本看不见。
        self.armed = False

    # -- 对外 ---------------------------------------------------------
    def force_expand(self) -> None:
        if not self.hidden:
            return
        hwnd = state["hwnd"]
        if not hwnd:
            return
        r = wt.RECT()
        user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r))
        ml, mt, _, _ = monitor_work_area(r.left + 40, r.top + 1)
        self._slide(hwnd, r.left, r.top, mt)
        self.hidden = False
        state["auto_hide_hidden"] = False

    # -- 内部 ---------------------------------------------------------
    def _slide(self, hwnd: int, x: int, y0: int, y1: int,
               steps: int = 7, delay: float = 0.013) -> None:
        self.animating = True
        try:
            flags = SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
            for i in range(1, steps + 1):
                y = int(y0 + (y1 - y0) * i / steps)
                user32.SetWindowPos(wt.HWND(hwnd), wt.HWND(0), x, y, 0, 0, flags)
                time.sleep(delay)
        finally:
            self.animating = False

    def run(self) -> None:
        while not monitor_stop.is_set():
            try:
                self._tick()
            except Exception as e:
                log(f"autohide error: {e!r}")
            monitor_stop.wait(AUTO_HIDE_POLL)

    def _tick(self) -> None:
        hwnd = state["hwnd"]
        if not hwnd or self.animating:
            return
        # 最大化时窗口顶边天然贴合工作区顶边，绝不能当成"贴顶"去收起
        if state.get("maximized"):
            if self.hidden:
                self.force_expand()
            return
        # 右键菜单弹在窗口外，鼠标自然会离开窗口；这时不能把窗口收起来
        if state.get("menu_open"):
            self.outside_since = None
            return
        if not state.get("auto_hide", True):
            if self.hidden:
                self.force_expand()
            return

        r = wt.RECT()
        if not user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r)):
            return
        w = r.right - r.left
        h = r.bottom - r.top
        if w <= 0 or h <= 0:
            return

        ml, mt, mr, mb = monitor_work_area(r.left + w // 2, r.top + 1)

        # 拖动过程中不要抢
        if user32.GetAsyncKeyState(0x01) & 0x8000:
            self.outside_since = None
            return

        px, py = cursor_pos()

        if self.hidden:
            # 收起态：鼠标碰到顶部细边就展开
            if (r.left <= px < r.right) and (mt <= py < mt + AUTO_HIDE_PEEK + 2):
                self._slide(hwnd, r.left, r.top, mt)
                self.hidden = False
                state["auto_hide_hidden"] = False
                log("autohide: expand")
            return

        # 展开态：只有贴住屏幕顶边才启用自动收起
        if r.top > mt + 2:
            self.outside_since = None
            return

        inside = (r.left <= px < r.right) and (r.top <= py < r.bottom)
        if inside:
            self.outside_since = None
            self.armed = True       # 用户碰过窗口，之后才允许自动收起
            return

        if not self.armed:
            return

        now = time.time()
        if self.outside_since is None:
            self.outside_since = now
        elif now - self.outside_since > AUTO_HIDE_DELAY:
            target = mt - h + AUTO_HIDE_PEEK
            self._slide(hwnd, r.left, r.top, target)
            self.hidden = True
            state["auto_hide_hidden"] = True
            self.outside_since = None
            log(f"autohide: collapse to y={target}")


def set_auto_hide(on: bool) -> None:
    state["auto_hide"] = bool(on)
    cfg = load_config()
    cfg["auto_hide"] = bool(on)
    save_config(cfg)
    ah = state.get("autohide")
    if ah and not on:
        ah.force_expand()
    log(f"auto_hide -> {on}")


ensure_mutex = None


def ensure_single_instance() -> bool:
    """命名互斥体；已存在实例则唤起它并退出。"""
    global ensure_mutex
    kernel32 = ctypes.windll.kernel32
    # 互斥体名可由环境变量覆盖：调试时能另起一个实例，不影响正在用的那个
    name = os.environ.get("AIMAX_MUTEX") or ("Global\\" + APP_NAME + "SingleInstance")
    ensure_mutex = kernel32.CreateMutexW(None, False, name)
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        hwnd = user32.FindWindowW(None, TITLE)
        if hwnd:
            user32.ShowWindow(hwnd, 9)       # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
        return False
    return True


def setup_after_start():
    """
    注意：本函数由 pywebview 在 GUI 线程上调用。这里只能做极轻量的工作，
    任何阻塞（托盘、热键、pystray 之类）都会堵死 UI 消息循环，
    导致 WebView 永远拿不到导航任务、窗口一片黑。
    """
    log("gui loop started")
    cfg = load_config()

    hwnd = None
    for _ in range(50):
        hwnd = find_app_window()
        if hwnd:
            break
        time.sleep(0.1)
    state["hwnd"] = hwnd
    log(f"hwnd={hwnd}")

    threading.Thread(
        target=_deferred_setup, args=(hwnd, cfg), daemon=True, name="deferred-setup"
    ).start()


def _deferred_setup(hwnd: int | None, cfg: dict) -> None:
    """后台线程：圆角、拖动条、置顶、托盘、热键、位置监控。每步独立容错。"""
    def step(name: str, fn, *args):
        try:
            fn(*args)
            log(f"{name} ready")
        except Exception as e:
            log(f"{name} FAILED: {e!r}")

    if hwnd:
        step("round corners", enable_round_corners, hwnd)
        step("hide from taskbar", hide_from_taskbar, hwnd)

        def _title_and_drag():
            # 标签条自己处理点击 + 拖动，所以正常情况下不再额外装 HTCAPTION 拖动条
            info = add_title_bar(hwnd)
            if not info.get("strip_ok"):
                # 标签条没建起来时的兜底：整条标题栏都给 HTCAPTION，至少还能拖窗
                state["dragbar"] = DragBar(hwnd, info["bar"], info["zone"])
        step("title bar", _title_and_drag)
        step("saved size", apply_saved_size, hwnd, cfg)

    step("topmost", apply_topmost, hwnd, bool(cfg.get("on_top", True)))
    step("tray", start_tray, cfg)

    def _hotkey():
        hk = HotkeyListener(cfg.get("hotkey", "alt+d"), toggle_window)
        hk.start()
        hk.ready.wait(2.0)
        log(f"hotkey registered={hk.ok}")
    step("hotkey", _hotkey)

    if hwnd:
        step("geometry monitor",
             lambda: threading.Thread(target=geometry_monitor, daemon=True).start())

    def _autohide():
        ah = TopAutoHide()
        state["autohide"] = ah
        ah.start()
    step("autohide", _autohide)

    step("splash watchdog",
         lambda: threading.Thread(target=_splash_watchdog, daemon=True).start())

    # 远程更新检查：延后几秒，等页面加载完再做，避免和启动抢网络
    def _update_later():
        time.sleep(UPDATE_CHECK_DELAY)
        check_update()
    step("update check",
         lambda: threading.Thread(target=_update_later, daemon=True,
                                  name="update-delay").start())

    log("setup done")

    # 自测用：AIMAX_TEST_SWITCH=<站点下标> 时，启动几秒后自动切到该站点。
    # 用来验证「新建的标签（非 pywebview 自带控件）下载能不能落地」，平时不会触发。
    test_switch = str(os.environ.get("AIMAX_TEST_SWITCH") or "").strip()
    if test_switch.isdigit():
        def _later(idx=int(test_switch)):
            time.sleep(5.0)
            invoke_gui(lambda: show_site(idx))
            log(f"test switch -> [{idx}]")
        threading.Thread(target=_later, daemon=True, name="test-switch").start()


def main() -> None:
    # 更新助手模式：由上一个版本拉起，只负责覆盖 exe 再启动，不初始化界面
    argv = sys.argv[1:]
    if len(argv) >= 2 and argv[0] == "--apply-update":
        run_apply_update(argv[1])
        return

    migrate_legacy_data()          # 必须在读写配置之前：把旧版数据搬过来
    os.makedirs(APP_DIR, exist_ok=True)
    if not ensure_single_instance():
        log("已有实例在运行，退出")
        return

    log(f"{TITLE} v{VERSION} starting")

    cfg = load_config()
    if not isinstance(cfg.get("sites"), list) or not cfg.get("sites"):
        # 老配置（没有 sites 字段）在这里补上默认站点，别把 null 一直留着
        cfg["sites"] = [dict(DEFAULT_SITES[0])]
        cfg["active"] = 0
        save_config(cfg)
    state["on_top"] = bool(cfg.get("on_top", True))
    state["auto_hide"] = bool(cfg.get("auto_hide", True))
    state["theme"] = cfg.get("theme") if cfg.get("theme") in THEMES else "dark"
    state["palette"] = None
    w, h, x, y = resolve_geometry(cfg)
    state["width"] = w

    # 网页里 target=_blank / window.open 的链接不要丢给系统浏览器，
    # 留在本工具里打开（pywebview 内部会导航到同一个 WebView）
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = False

    # 关键：pywebview 默认禁止下载，会把 DownloadStarting 直接 Cancel 掉，
    # 于是「点保存、选了路径、但没文件」。放开后由自己的 handler 接管。
    webview.settings['ALLOW_DOWNLOADS'] = True
    patch_pywebview_download()

    win = webview.create_window(
        TITLE,
        html=splash_html(),        # 先放启动动画，播完再跳站点
        width=w + CHROME_DW,
        height=h + CHROME_DH,
        x=x,
        y=y,
        frameless=True,
        easy_drag=False,           # 关掉整窗拖动：只允许拖标题栏
        text_select=True,          # 关键：pywebview 默认 False，会注入 user-select:none 导致选不中文字
        on_top=bool(cfg.get("on_top", True)),
        min_size=(320, 400),
        background_color=("#f7f7fa" if state["theme"] == "light" else "#0a0d1a"),
    )
    state["win"] = win

    try:
        win.events.loaded += on_page_loaded
        win.events.shown += lambda: log("window shown")
        win.events.closed += on_window_closed
    except Exception:
        pass

    log(f"launch {w}x{h} at ({x},{y}), on_top={cfg.get('on_top')}")
    webview.start(
        setup_after_start,
        private_mode=False,          # 关键：保留登录态
        storage_path=WEBVIEW_DIR,
        gui="edgechromium",
    )


if __name__ == "__main__":
    main()
