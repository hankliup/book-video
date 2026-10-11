#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""《窄门》共享模板样式兜底渲染（ffmpeg + Pillow 浮点合成，不依赖 HyperFrames 正文会话）。

为什么走这条路：HyperFrames 0.7.33 在本机对 180s+ 正文的单次会话渲染稳定崩溃
（Page.captureScreenshot: Target closed），分段渲染又因运行时只认「创作时」写入的字幕
时间（事后 tl.shiftChildren() 被忽略）而每段重播正文开头 —— 已交付的《白鲸》即因此
音画错位。沿用《道林·格雷的画像》《少年》《霍比特人》已验证的浮点合成路线，并按本期
需要合并了两条改进：

  · 场景铺图改读 episodes/窄门/scene-map.json（18 层 / 12 张氛围图），不再硬编码
    SCENE_PLAN；切镜时间取相邻行号区间之间配音间隙的中点，保证换图一定落在停顿里。
  · 慢推倍率按源图分辨率自适应：本期 12 张氛围图都是 720x960，与画幅 1:1，
    cover==1 时若仍用 1.020→1.062 等于把画面放大 6% 再采样，慢镜头里会发糊；
    这里按 cover 决定推镜幅度（见 zoom_range）。
  · 运镜一律用 PIL.Image.transform 的浮点仿射矩阵做居中缓推，不使用 ffmpeg zoompan：
    zoompan 会把 x/y/裁剪宽高取整到源图整数像素，720x960 源图上 1px = 1 输出像素，
    慢镜头里就是持续抖动。
  · 常驻书名/作者的字号与 top 直接从本期片头 index.html 的 .target 内联样式解析，
    桥接图静止倍率按片头 page-bg 的 scale 补间（2.38s 起 1.87s 内到 1.035）反算，
    保证片头末帧与正文首帧是同一帧画面，接缝不跳。
  · 字幕白字黑投影（bottom 126px，56/50px，无底板），文本以 script-body.csv 为准：
    每行是若干以空格分隔的子句，按子句打包成 ≤2 行、每行 ≤13 字的字幕条，
    并把条间切换点吸附到配音真实停顿上（jieba 词边界兜底，杜绝劈词）。
  · 混音参数与 scripts/render-episode-final.mjs 一致（BGM 0.32 / loudnorm -14 LUFS）

产物：episodes/窄门/renders/窄门-final.mp4 + 封面-窄门.png
幂等可重建：候选文件先落 tmp，技术验收通过后才激活到 renders/。

用法：
  python3 scripts/render_zhamen_template_fallback.py             # 全量渲染
  python3 scripts/render_zhamen_template_fallback.py --caps-only # 只预检字幕切条
  FORCE_BG=1 ...                                                 # 强制重合成正文背景
"""
import csv
import json
import math
import os
import re
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import jieba

ROOT = "/Users/apple/Documents/Obsidian/自媒体/短视频/文学/视频制作"
EPISODE = os.environ.get("EPISODE", "窄门")
EP = os.path.join(ROOT, "episodes", EPISODE)
IMG = os.path.join(EP, "images")
AUDIO = os.path.join(EP, "audio")
RENDERS = os.path.join(EP, "renders")
ASSETS = os.path.join(ROOT, "assets")
PREVIEW = os.environ.get(
    "PREVIEW", os.path.join(ROOT, "tmp", "preview-book-07b7f9b34a"))
INTRO_SOURCE = os.environ.get(
    "INTRO_SOURCE", os.path.join(PREVIEW, "intro", "renders", "intro.mp4"))
INTRO_HTML = os.path.join(PREVIEW, "intro", "index.html")
TMP = os.environ.get("FALLBACK_TMP", os.path.join(ROOT, "tmp", "zhamen_tpl"))
os.makedirs(TMP, exist_ok=True)
os.makedirs(RENDERS, exist_ok=True)

FPS = 30
W, H = 720, 960
FONT = "/System/Library/Fonts/STHeiti Medium.ttc"  # 共享模板在本机的实际回退字体

brief = json.load(open(os.path.join(EP, "brief.json"), encoding="utf-8"))
TITLE_TEXT = f"《{brief['display_title']}》"
AUTHOR_TEXT = f"{brief['author']} / 著"

# 片头裁切点：共享片头合成时长 4.27s，其中 ~2.62s 起结果页（桥接图 + 书名/作者）稳定定格，
# 切在这里可让片头末帧与正文首帧同为桥接图，消除黑场硬切的闪屏接缝（与已交付系列片对齐）。
INTRO_TRIM = float(os.environ.get("INTRO_TRIM_SECONDS", "3.0"))
INTRO_OFFSET_MS = round(INTRO_TRIM * 1000)
BGM_VOL = 0.32
SCROLL_START, SCROLL_END = 1.08, 2.38
# 水波「音效—画面」同窗常量的唯一真源：scripts/lib/water-ripple.json → lib/water_ripple.py。
# 这里只 import，绝不另抄秒数：写死 0.32/1.06 后一旦 lib 改动，就会变成「有水声没水波」
# 或「水波比声音早半秒」这种没人报错的错窗（见 AGENTS.md「Visual And Audio Rules」）。
sys.path.insert(0, os.path.join(ROOT, "scripts", "lib"))
from water_ripple import WATER_DUR, WATER_OFFSET, ripple, water_u  # noqa: E402

WATER_START = round(INTRO_TRIM + WATER_OFFSET, 2)   # 成片时间轴上的水波起点（秒）
WATER_NFRAMES = round(WATER_DUR * FPS)              # 涟漪帧数
# 正文背景第 0 帧对应成片 INTRO_TRIM，所以背景循环里的起点帧直接按偏移秒数换算
WATER_FRAME0 = round(WATER_OFFSET * FPS)

# 片头 .target .page-bg：2.38s 起 1.87s 内 sine.inOut 推到 scale 1.035
BRIDGE_PUSH_START, BRIDGE_PUSH_DUR, BRIDGE_PUSH_TO = 2.38, 1.87, 0.035

XFADE_FIRST = 0.64     # 桥接图 → 场景 1
XFADE = 0.72           # 场景之间
SMALL_CHARS = 12
MAX_UNIT = 24          # 单条字幕最多约两行、每行 13 字
MAX_LINE = 13          # 单行最大字数（56/50px 下 13 字仍落在 720-68 安全区内）


def run(cmd):
    r = subprocess.run(cmd)
    if r.returncode != 0:
        raise SystemExit(f"CMD failed ({r.returncode}): {' '.join(str(c) for c in cmd)}")


def ff(args):
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args])


def probe_dur(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", p],
                       capture_output=True, text=True)
    return float(r.stdout.strip())


# ---------------------------------------------------- 片头排版参数（与本期片头对齐）
def intro_title_metrics():
    """从本期 HyperFrames 片头 HTML 的 .target 内联样式解析字号与 top。

    字号/位置若与片头不一致，3.0s 定格切到正文常驻层时书名会明显跳位。
    """
    size, top, a_top = 82, 70, 166
    if os.path.exists(INTRO_HTML):
        h = open(INTRO_HTML, encoding="utf-8").read()
        i = h.find("page target")
        seg = h[i:i + 800] if i >= 0 else ""
        m = re.search(r'page-title" style="font-size:\s*([\d.]+)px', seg)
        if m:
            size = round(float(m.group(1)))
        m = re.search(r'page-author" style="top:\s*([\d.]+)px', seg)
        if m:
            a_top = round(float(m.group(1)))
    return size, top, a_top


TITLE_SIZE, TITLE_TOP, AUTHOR_TOP = intro_title_metrics()
AUTHOR_SIZE = 34
print(f"[title] {TITLE_TEXT} {TITLE_SIZE}px@{TITLE_TOP} / {AUTHOR_SIZE}px@{AUTHOR_TOP}（取自本期片头）")


# ---------------------------------------------------------------- 时间轴与文案
timings = json.load(open(os.path.join(AUDIO, "body-timings.json"), encoding="utf-8"))
caps = sorted(timings["captions"], key=lambda c: int(c["order"]))
body_dur = float(timings["duration"])
final_dur = round(INTRO_TRIM + body_dur, 2)

text_by_order = {}
with open(os.path.join(EP, "script-body.csv"), encoding="utf-8") as f:
    for row in csv.DictReader(f):
        if row["version"] == timings["scriptVersion"]:
            text_by_order[int(row["order"])] = row["text"].strip()

missing = [int(c["order"]) for c in caps if int(c["order"]) not in text_by_order]
if missing:
    raise SystemExit(f"script-body.csv 缺少 order: {missing}")

# 行边界一致性：body-timings 的字幕行是按 script-body.csv 对齐出来的，两者行数必须一一对应，
# 否则说明 script-body.csv 被改过而时间戳是旧的 —— 那会直接造成音画错位（本项目踩过的坑）。
if len(caps) != len(text_by_order):
    raise SystemExit(f"script-body.csv 行数 {len(text_by_order)} 与 body-timings 字幕数 {len(caps)} 不一致，"
                     "请用 scripts/create-body-timings.mjs 重新生成时间戳")

# 低置信时间兜底：ASR 对齐偶有行尾锚点过早（如 order 1 书名只有 0.74s），
# 按「字数 / 平均语速」估一个最短可见时长，向后延到下一条开始之前。
AVG_RATE = 4.5          # 本系列旁白平均约 4.5 字/秒
MIN_KEEP = 0.9
adjusted = []
for i, c in enumerate(caps):
    text = text_by_order[int(c["order"])]
    span = float(c["end"]) - float(c["start"])
    need = max(MIN_KEEP, len(text.replace(" ", "")) / AVG_RATE * 0.55)
    if span >= need:
        continue
    nxt = float(caps[i + 1]["start"]) if i + 1 < len(caps) else body_dur
    new_end = min(float(c["start"]) + need, nxt - 0.05)
    if new_end > float(c["end"]):
        adjusted.append((int(c["order"]), round(span, 2), round(new_end - float(c["start"]), 2)))
        c["end"] = round(new_end, 3)
if adjusted:
    print(f"[warn] {len(adjusted)} 行字幕时长低于语速估计，已按说话时长兜底延长（不改变文本）：")
    for o, was, now in adjusted:
        print(f"       order {o:>3}: {was}s → {now}s")


# ---------------------------------------------------------------- 场景映射
scene_map = json.load(open(os.path.join(EP, "scene-map.json"), encoding="utf-8"))
layers_cfg = scene_map["layers"]
max_order = max(text_by_order)
for cfg in layers_cfg:
    a, b = int(cfg["order_start"]), int(cfg["order_end"])
    if not (1 <= a <= b <= max_order):
        raise SystemExit(f"scene-map 行号越界：{cfg['name']} {a}-{b}（script-body.csv 共 {max_order} 行）")
    if not os.path.exists(os.path.join(IMG, f"{cfg['name']}.png")):
        raise SystemExit(f"缺少配图素材: {os.path.join(IMG, cfg['name'] + '.png')}")
covered = sorted({o for c in layers_cfg for o in range(int(c["order_start"]), int(c["order_end"]) + 1)})
if covered != list(range(1, max_order + 1)):
    raise SystemExit(f"scene-map 未完整覆盖 1..{max_order}，缺 {sorted(set(range(1, max_order + 1)) - set(covered))[:10]}")
for i in range(1, len(layers_cfg)):
    if layers_cfg[i]["name"] == layers_cfg[i - 1]["name"]:
        raise SystemExit(f"scene-map 出现相邻同图：{layers_cfg[i]['name']}")

span = {int(c["order"]): (float(c["start"]), float(c["end"])) for c in caps}
layer_names = ["result-bridge"] + [c["name"] for c in layers_cfg]
# 桥接图独立停留 BRIDGE_IN 秒后与场景 1 交叉溶解；其余层切点取相邻行号区间之间配音间隙的中点
BRIDGE_IN = 0.32
appear = [0.0, BRIDGE_IN]
for i in range(1, len(layers_cfg)):
    prev_end = span[int(layers_cfg[i - 1]["order_end"])][1]
    cur_start = span[int(layers_cfg[i]["order_start"])][0]
    appear.append(round((prev_end + cur_start) / 2.0, 3))
appear_times = appear + [body_dur]
layer_start, layer_end = appear[1:], appear[2:]
layer_len = [round(e - s, 3) for s, e in zip(layer_start, layer_end)]
xfades = [0.0, XFADE_FIRST] + [XFADE] * (len(layers_cfg) - 1)
print(f"[layers] {len(layers_cfg)} 层 / 正文 {body_dur:.2f}s")
for cfg, s, ln in zip(layers_cfg, layer_start, layer_len):
    print(f"  {cfg['name']:16} {s:7.2f}s +{ln:6.2f}s  {cfg['theme'][:30]}")
too_short = [(c["name"], round(l, 2)) for c, l in zip(layers_cfg, layer_len) if l < 4.0]
if too_short:
    print(f"[warn] 以下场景停留不足 4s，切换偏碎：{too_short}")


# ------------------------------ 1. 长行切条：按子句打包 + 停顿吸附
def silencedetect(path, noise="-35dB", min_dur=0.18):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-af",
                        f"silencedetect=noise={noise}:d={min_dur}", "-f", "null", "-"],
                       capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start:\s*([-\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([-\d.]+)", r.stderr)]
    return [(s + e) / 2 for s, e in zip(starts, ends) if e > s]


voice = os.path.join(AUDIO, "body-voiceover.mp3")
if not os.path.exists(voice):
    voice = os.path.join(AUDIO, "body-voiceover-story.mp3")
pauses = silencedetect(voice)
TRAILING_PUNCT = "，。、；：！？…—·”’）》"


def jieba_split(clause):
    """无空格/标点的超长子句：用 jieba 找词边界，切成尽量均衡且 ≤MAX_LINE 的两行。"""
    words = [w for w in jieba.cut(clause) if w.strip()]
    n = len(clause)
    bounds, acc = [], 0
    for w in words[:-1]:
        acc += len(w)
        bounds.append(acc)
    if not bounds:
        return [clause[:MAX_LINE], clause[MAX_LINE:]]
    fit = [b for b in bounds if b <= MAX_LINE and n - b <= MAX_LINE]
    pool = fit or bounds
    best = min(pool, key=lambda b: abs(b - n / 2))
    return [clause[:best], clause[best:]]


def clause_pieces(clause):
    """子句 → 可上屏的行：>MAX_LINE 的词边界安全拆成两行。"""
    if len(clause) <= MAX_LINE:
        return [clause]
    return jieba_split(clause)


def pack_units(text):
    """script-body.csv 一行（空格分隔的子句）→ 若干字幕条，每条 ≤2 行、每行 ≤MAX_LINE 字。"""
    clauses = [c for c in re.split(r"\s+", text.strip()) if c]
    units = []
    for clause in clauses:
        pieces = clause_pieces(clause)
        if len(pieces) > 1:           # 长子句独占一条，两行同屏
            units.append(" ".join(pieces))
            continue
        if (units and len(units[-1].split(" ")) < 2
                and len(units[-1]) + 1 + len(clause) <= MAX_UNIT
                and max(len(clause), max((len(x) for x in units[-1].split(" ")), default=0)) <= MAX_LINE):
            units[-1] += " " + clause
        else:
            units.append(clause)
    return [u.strip() for u in units if u.strip()]


def allocate_times(units, start, end):
    """按字数比例分配子条时间，再把内部切换点吸附到最近的配音停顿。"""
    n = len(units)
    span_t = end - start
    total_chars = sum(len(u.replace(" ", "")) for u in units) or 1
    min_keep = span_t / (n * 1.8)          # 单条最短时长，防止吸附后某条被挤没
    bounds = [start]
    acc = 0
    for u in units[:-1]:
        acc += len(u.replace(" ", ""))
        bounds.append(start + span_t * acc / total_chars)
    bounds.append(end)
    for i in range(1, n):
        lo = bounds[i - 1] + min_keep
        hi = bounds[i + 1] - min_keep
        cands = [p for p in pauses if lo <= p <= hi and abs(p - bounds[i]) <= 0.9]
        if cands:
            bounds[i] = min(cands, key=lambda p: abs(p - bounds[i]))
    for i in range(1, n):                # 兜底：吸附后仍需单调且各条不短于 min_keep
        bounds[i] = max(bounds[i], bounds[i - 1] + min_keep)
    bounds[-1] = end
    for i in range(n - 1, 0, -1):
        bounds[i] = min(bounds[i], bounds[i + 1] - min_keep)
    return [(u, bounds[i], bounds[i + 1]) for i, u in enumerate(units)]


caption_items = []
for c in caps:
    order = int(c["order"])
    start, end = float(c["start"]), float(c["end"])
    text = text_by_order[order]
    units = pack_units(text)
    span_t = end - start
    # 行太短不足以切多条时，回退为整行同屏（由 caption_lines 折行）
    while len(units) > 1 and span_t / len(units) < 0.7:
        units[-2] += " " + units[-1]
        units.pop()
    if len(units) == 1:
        caption_items.append((order, units[0], start, end))
    else:
        for u, s, e in allocate_times(units, start, end):
            caption_items.append((order, u, s, e))
print(f"[caps] {len(caps)} 行 → {len(caption_items)} 条字幕（{len(pauses)} 个停顿锚点）")
track = [{"order": o, "text": t, "start": round(s, 3), "end": round(e, 3)}
         for o, t, s, e in caption_items]
with open(os.path.join(TMP, "caption-track.json"), "w", encoding="utf-8") as f:
    json.dump(track, f, ensure_ascii=False, indent=1)
if "--caps-only" in sys.argv:      # 只做字幕切条预检，不进入重合成
    for item in track:
        print(f"  {item['start']:7.2f}-{item['end']:7.2f} [{item['order']:>3}] {item['text']}")
    sys.exit(0)


# ------------------------------- 2. 正文背景：亚像素缓推 + 交叉溶解（单趟合成）
def ease_in_out(u):
    return 0.5 - 0.5 * math.cos(math.pi * min(1.0, max(0.0, u)))


def zoom_range(cover):
    if cover > 1.15:      # 源图明显大于画幅：共享模板原本的推镜幅度
        return 1.020, 1.062
    return 1.006, 1.042   # 源图≈画幅：只做轻微推镜，全程基本 1:1 采样


def zoom_frame(im, factor):
    """以图片中心为锚点，按浮点倍率取景到 720x960（无整数取抖）。"""
    iw, ih = im.size
    inv = 1.0 / factor
    return im.transform(
        (W, H), Image.AFFINE,
        (inv, 0.0, iw / 2.0 - W * inv / 2.0,
         0.0, inv, ih / 2.0 - H * inv / 2.0),
        resample=Image.BICUBIC)


# 桥接图静止倍率：对齐片头 page-bg 在 INTRO_TRIM 处的 scale，否则切点会有一跳
_intro_u = min(1.0, max(0.0, (INTRO_TRIM - BRIDGE_PUSH_START) / BRIDGE_PUSH_DUR))
BRIDGE_ZOOM = 1.0 + BRIDGE_PUSH_TO * ease_in_out(_intro_u)
print(f"[bridge] 静止倍率 {BRIDGE_ZOOM:.4f}（对齐片头 {INTRO_TRIM}s 定格）")

bg_path = os.path.join(TMP, "body-bg.mp4")
# body-bg 只有在「输入全部未变」时才可复用：时长、切镜表、水波窗口任一变化都必须重合成，
# 否则改了 scene-map 或加了涟漪却静默拿到旧背景，验收还全绿。标记落 body-bg.json。
_bg_sig = {"body_dur": round(body_dur, 3), "fps": FPS,
           "layers": layer_names, "layer_start": [round(v, 3) for v in appear_times],
           "water_frame0": WATER_FRAME0, "water_nframes": WATER_NFRAMES}
_bg_marker = os.path.join(TMP, "body-bg.json")


def _bg_reusable():
    if not (os.path.exists(bg_path) and abs(probe_dur(bg_path) - body_dur) < 0.2):
        return False
    try:
        return json.load(open(_bg_marker, encoding="utf-8")) == _bg_sig
    except Exception:
        return False


if os.environ.get("FORCE_BG") != "1" and _bg_reusable():
    print(f"[bg] 复用 {bg_path} {probe_dur(bg_path):.2f}s（切镜表与水波窗口未变）")
else:
    covers = {}
    for name in set(layer_names):
        src = Image.open(os.path.join(IMG, f"{name}.png")).convert("RGB")
        iw, ih = src.size
        covers[name] = (src, max(W / iw, H / ih))
        print(f"[img] {name:16} {iw}x{ih} cover={max(W / iw, H / ih):.3f}")
    zooms = {k: zoom_range(covers[k][1]) for k in covers}

    def layer_frame(k, t):
        name = layer_names[k]
        src, cover = covers[name]
        if k == 0 or layers_cfg[k - 1].get("static"):
            return zoom_frame(src, cover * BRIDGE_ZOOM)
        z0, z1 = zooms[name]
        window = appear_times[k + 1] - appear_times[k]
        u = (t - appear_times[k]) / window if window > 0 else 1.0
        return zoom_frame(src, cover * (z0 + (z1 - z0) * ease_in_out(u)))

    n_total = round(body_dur * FPS)
    pipe = subprocess.Popen(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
         "-i", "pipe:0", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
         "-crf", "17", "-preset", "medium", bg_path],
        stdin=subprocess.PIPE)
    idx = 0
    for frame in range(n_total):
        t = frame / FPS
        while idx < len(appear) - 1 and t >= appear[idx + 1]:
            idx += 1
        cur = layer_frame(idx, t)
        if idx > 0 and t - appear[idx] < xfades[idx]:
            p = (t - appear[idx]) / xfades[idx]
            cur = Image.blend(layer_frame(idx - 1, t), cur, min(1.0, max(0.0, p)))
        # 水波视觉效果：常驻规则，必须与水波音效同帧起、同帧止。
        # 只作用在背景层——书名层与字幕层在其后才绘制，所以涟漪不会把字扭糊。
        _wu = water_u(frame, FPS, WATER_FRAME0, WATER_NFRAMES)
        if _wu is not None:
            cur = ripple(cur, _wu)
        pipe.stdin.write(cur.tobytes())
        if frame % 1500 == 0:
            print(f"[bg] {frame}/{n_total} {t:.1f}s")
    pipe.stdin.close()
    pipe.wait()
    if pipe.returncode != 0:
        raise SystemExit(f"正文背景合成失败: {pipe.returncode}")
    json.dump(_bg_sig, open(_bg_marker, "w", encoding="utf-8"), ensure_ascii=False)
    del covers
print(f"[bg] {probe_dur(bg_path):.2f}s (target {body_dur:.2f}s)")
print(f"[water] 水波背景 {WATER_FRAME0}→{WATER_FRAME0 + WATER_NFRAMES - 1} 帧 "
      f"= 成片 {WATER_START:.2f}–{round(WATER_START + WATER_DUR, 2)}s（与水波音效同窗）")


# ------------------------------------ 3. 常驻书名层 + 字幕层（白字黑投影，无底板）
def spaced_text(im, text, font, top, spacing_em, sh_offset=7, sh_blur=9):
    """逐字排布模拟模板 letter-spacing，并加 0 7px 18px rgba(0,0,0,.96) 投影。"""
    spacing = font.size * spacing_em
    widths = [font.getlength(ch) for ch in text]
    total = sum(widths) + spacing * (len(text) - 1)
    x0 = (W - total) / 2
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    x = x0
    for ch, cw in zip(text, widths):
        sd.text((x, top + sh_offset), ch, font=font, fill=(0, 0, 0, 245))
        x += cw + spacing
    shadow = shadow.filter(ImageFilter.GaussianBlur(sh_blur))
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    x = x0
    for ch, cw in zip(text, widths):
        ld.text((x, top), ch, font=font, fill=(255, 255, 255, 255))
        x += cw + spacing
    im.alpha_composite(shadow)
    im.alpha_composite(layer)


title_im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
spaced_text(title_im, TITLE_TEXT, ImageFont.truetype(FONT, TITLE_SIZE), TITLE_TOP, 0.04)
# 片头 .page-author 的投影是 0 5px 14px，比书名更浅一档
spaced_text(title_im, AUTHOR_TEXT, ImageFont.truetype(FONT, AUTHOR_SIZE), AUTHOR_TOP,
            0.06, sh_offset=5, sh_blur=7)
title_png = os.path.join(TMP, "title.png")
title_im.save(title_png)


def caption_lines(text):
    """一个字幕条 = 1~2 个行；空格即换行（子句边界，jieba 词边界安全）。"""
    parts = [p.rstrip(TRAILING_PUNCT) for p in text.split(" ") if p.strip()]
    parts = [p for p in parts if p]
    if len(parts) >= 2:
        return parts[:2]
    body = (parts[0] if parts else text).rstrip(TRAILING_PUNCT)
    if len(body) <= MAX_LINE:
        return [body]
    return jieba_split(body)


def build_caption(text, path):
    lines = [ln for ln in caption_lines(text) if ln]
    size = 56 if max(len(ln) for ln in lines) <= SMALL_CHARS else 50
    font = ImageFont.truetype(FONT, size)
    while max(font.getlength(ln) for ln in lines) > W - 68 and size > 36:
        size -= 2
        font = ImageFont.truetype(FONT, size)
    line_h = round(size * 1.16)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    top = H - 126 - line_h * len(lines)
    d = ImageDraw.Draw(im)
    for i, ln in enumerate(lines):
        y = top + i * line_h
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(sh).text((W // 2, y + 7), ln, font=font, fill=(0, 0, 0, 245), anchor="ma")
        sh = sh.filter(ImageFilter.GaussianBlur(9))
        im.alpha_composite(sh)
        d.text((W // 2, y), ln, font=font, fill=(255, 255, 255, 255), anchor="ma")
    im.save(path)


transparent = os.path.join(TMP, "transparent.png")
Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(transparent)

entries = []
prev_end = 0.0
for i, (order, text, s, e) in enumerate(caption_items):
    if s - prev_end > 0.05:
        entries.append((max(1, round((s - prev_end) * FPS)), transparent))
    p = os.path.join(TMP, f"cap-{i:03d}-{order:03d}.png")
    build_caption(text, p)
    entries.append((max(1, round((e - s) * FPS)), p))
    prev_end = e
target_frames = round(body_dur * FPS)
if sum(n for n, _ in entries) < target_frames:
    entries.append((target_frames - sum(n for n, _ in entries), transparent))
print(f"[caps] 字幕轨 {sum(n for n, _ in entries)} 帧 = {sum(n for n, _ in entries) / FPS:.2f}s")

sub_mov = os.path.join(TMP, "sub.mov")
pipe = subprocess.Popen(
    ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
     "-f", "rawvideo", "-pix_fmt", "argb", "-s", f"{W}x{H}", "-r", str(FPS),
     "-i", "pipe:0", "-c:v", "qtrle", "-pix_fmt", "argb", sub_mov],
    stdin=subprocess.PIPE)
current_path, current_raw = None, None
for n, p in entries:
    if p != current_path:
        current_raw = Image.open(p).convert("RGBA").tobytes()
        current_path = p
    for _ in range(n):
        pipe.stdin.write(current_raw)
pipe.stdin.close()
pipe.wait()
if pipe.returncode != 0:
    raise SystemExit(f"字幕层管道失败: {pipe.returncode}")

body_mp4 = os.path.join(TMP, "body.mp4")
ff(["-i", bg_path, "-i", sub_mov, "-i", title_png,
    "-filter_complex",
    "[0:v][1:v]overlay=0:0:format=auto[t];[t][2:v]overlay=0:0:format=auto,format=yuv420p[v]",
    "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
    "-crf", "18", "-preset", "medium", body_mp4])
print(f"[body] {probe_dur(body_mp4):.2f}s")


# ------------------------------------------------------- 4. 片头（复用成片级渲染）
if not os.path.exists(INTRO_SOURCE):
    raise SystemExit(f"缺少 HyperFrames 片头渲染结果: {INTRO_SOURCE}\n"
                     f"  node scripts/create-episode-preview.mjs {EPISODE} && "
                     f"(cd {os.path.dirname(INTRO_SOURCE)}/.. && npm run render)")
intro_mp4 = os.path.join(TMP, "intro.mp4")
ff(["-i", INTRO_SOURCE,
    "-vf", f"trim=0:{INTRO_TRIM},setpts=PTS-STARTPTS,fps={FPS},format=yuv420p",
    "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS), "-crf", "18", intro_mp4])


# ------------------------------------------------------------------ 5. 混音合成
intro_story = os.path.join(ASSETS, "template-audio", "intro-voiceover-story.mp3")
body_story = os.path.join(AUDIO, "body-voiceover-story.mp3")
for p in [intro_story, body_story]:
    if not os.path.exists(p):
        raise SystemExit(f"缺少配音素材: {p}")
# 共享 BGM 文件名被手工改过（全角/半角括号不一致），按目录枚举解析避免硬编码失配
bgm_dir = os.path.join(ASSETS, "bgm")
bgm_env = os.environ.get("BGM")
shared_bgm = sorted(os.path.join(bgm_dir, n) for n in os.listdir(bgm_dir)
                    if n.lower().endswith(".mp3"))
bgm = os.path.join(bgm_dir, bgm_env) if bgm_env else (shared_bgm[0] if shared_bgm else None)
if not bgm or not os.path.exists(bgm):
    raise SystemExit(f"缺少共享 BGM：{bgm_dir} 下没有 mp3（BGM={bgm_env}）")
print(f"[bgm] {os.path.basename(bgm)}")
scroll_sfx = os.path.join(ASSETS, "sfx", "gear-scroll.mp3")
water_sfx = os.path.join(ASSETS, "sfx", "water.mp3")
for p in [scroll_sfx, water_sfx]:
    if not os.path.exists(p):
        raise SystemExit(f"缺少音效素材: {p}")

scroll_dur = round(SCROLL_END - SCROLL_START, 2)
bgm_atrim = round(final_dur - INTRO_TRIM, 2)
fc = [
    "[0:v][1:v]concat=n=2:v=1:a=0[v]",
    "[2:a]asetpts=PTS-STARTPTS,aresample=48000,volume=1.0[introa]",
    f"[3:a]asetpts=PTS-STARTPTS,aresample=48000,adelay={INTRO_OFFSET_MS}|{INTRO_OFFSET_MS},volume=1.0[bodya]",
    f"[4:a]atrim=0:{bgm_atrim},asetpts=PTS-STARTPTS,aresample=48000,adelay={INTRO_OFFSET_MS}|{INTRO_OFFSET_MS},"
    f"volume={BGM_VOL},afade=t=in:st=0:d=0.5,afade=t=out:st={round(bgm_atrim - 0.8, 2)}:d=0.8[bgm]",
    f"[5:a]atrim=0:{scroll_dur},asetpts=PTS-STARTPTS,aresample=48000,volume=1.4,"
    f"afade=t=in:st=0:d=0.01,afade=t=out:st={round(scroll_dur - 0.2, 2)}:d=0.2,"
    f"adelay={round(SCROLL_START * 1000)}|{round(SCROLL_START * 1000)}[scrollsfx]",
    f"[6:a]atrim=0:{WATER_DUR},asetpts=PTS-STARTPTS,aresample=48000,volume=1.0,"
    f"adelay={round(WATER_START * 1000)}|{round(WATER_START * 1000)}[watersfx]",
    "[introa][bodya][bgm][scrollsfx][watersfx]amix=inputs=5:duration=longest:dropout_transition=0:normalize=0,"
    "alimiter=limit=0.95,loudnorm=I=-14.0:TP=-1.0:LRA=7.0,"
    "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a]",
]
candidate = os.path.join(TMP, f"{EPISODE}-final.mp4")
ff(["-i", intro_mp4, "-i", body_mp4, "-i", intro_story, "-i", body_story,
    "-stream_loop", "-1", "-i", bgm, "-i", scroll_sfx, "-i", water_sfx,
    "-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", candidate])

# ------------------------------------------------ 技术验收：通过后才激活到 renders/
probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                                   "-of", "json", candidate], capture_output=True, text=True).stdout)
v = next((s for s in probe["streams"] if s["codec_type"] == "video"), {})
a = next((s for s in probe["streams"] if s["codec_type"] == "audio"), None)
dur = float(probe["format"]["duration"])
assert v.get("width") == W and v.get("height") == H, f"分辨率错误: {v}"
assert a, "缺少音频流"
assert abs(dur - final_dur) < 1.0, f"时长异常: {dur:.2f}s vs 计划 {final_dur:.2f}s"
print(f"[check] 720x960 / {dur:.2f}s / 音频 {a['codec_name']} 验收通过")
out_final = os.path.join(RENDERS, f"{EPISODE}-final.mp4")

# 水波同窗校验（常驻规则，见 AGENTS.md「Visual And Audio Rules」）：water.mp3 在切点后起播，
# 画面必须同时出现一圈外扩涟漪。这道校验必须卡在 move 之前——「只加声音不加画面」是唯一
# 一个分辨率/时长/音频流/字幕/切点全部照常通过的缺陷。门禁自己 import lib 的常数，
# 所以这里只传切点与帧率，不传任何秒数。
_water_gate = subprocess.run(
    [sys.executable, os.path.join(ROOT, "scripts", "check-water-ripple.py"),
     candidate, "--intro-trim", str(INTRO_TRIM), "--fps", str(FPS)],
    capture_output=True, text=True)
print(_water_gate.stdout.strip())
if _water_gate.returncode != 0:
    raise SystemExit(
        "水波同窗门禁未通过，候选不激活到 renders/：水声与可见涟漪必须落在同一帧窗口。"
        "查背景层是否调了 lib 的 ripple()、混音是否引了 assets/sfx/water.mp3。"
        "已叠字幕的成片不能事后补波（会扭糊字幕），必须重渲正文背景（FORCE_BG=1）。\n"
        + (_water_gate.stderr or "")[-500:])
shutil.move(candidate, out_final)


# --------------------------------------------------------- 6. 封面（1080x1920 竖版）
cover_src = os.environ.get("COVER_IMAGE", "atmosphere-4")
cover_out = os.path.join(RENDERS, f"封面-{EPISODE}.png")
cover = Image.open(os.path.join(IMG, f"{cover_src}.png")).convert("RGB")
cw, ch = cover.size
scale = max(1080 / cw, 1920 / ch)
cover = cover.resize((round(cw * scale), round(ch * scale)), Image.LANCZOS)
left = (cover.width - 1080) // 2
top = (cover.height - 1920) // 2
cover = cover.crop((left, top, left + 1080, top + 1920)).convert("RGBA")
shade = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
ImageDraw.Draw(shade).rectangle([0, 0, 1080, 1920], fill=(0, 0, 0, 90))
cover = Image.alpha_composite(cover, shade)
d = ImageDraw.Draw(cover)
d.text((540, 250), TITLE_TEXT, font=ImageFont.truetype(FONT, 96),
       fill=(255, 255, 255, 255), stroke_width=4, stroke=(0, 0, 0, 235), anchor="ma")
d.text((540, 400), AUTHOR_TEXT, font=ImageFont.truetype(FONT, 38),
       fill=(240, 240, 240, 240), stroke_width=2, stroke=(0, 0, 0, 215), anchor="ma")
d.text((540, 1780), "读书视频 · 文学经典系列", font=ImageFont.truetype(FONT, 30),
       fill=(225, 225, 225, 210), anchor="ma")
cover.convert("RGB").save(cover_out)
print(f"[cover] {cover_out}（底图 {cover_src}）")
print(f"\n✅ 成片: {out_final}  ({dur:.2f}s)")
