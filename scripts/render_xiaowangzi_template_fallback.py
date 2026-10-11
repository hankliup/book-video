#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""《小王子》共享模板样式兜底渲染（ffmpeg + Pillow，不依赖 HyperFrames 正文会话）。

沿用 scripts/render_jingzhiniao_template_fallback.py 已验收的实现，只替换本书的
叙事分段、常驻书名与封面素材：

  · 片头复用 HyperFrames 渲染成功的玻璃碎片片头（intro.mp4），裁到结果页定格 3.0s
  · 正文 result-bridge 起幅 0.32s → 8 张氛围图按叙事段落切换，0.64/0.72s 交叉溶解
  · 运镜用 PIL.Image.transform 的浮点仿射矩阵做居中缓推（源图只有 720x960，
    倍率自适应为 1.006→1.042，缓入缓出），不使用 ffmpeg zoompan：zoompan 会把
    x/y/裁剪宽高取整到源图整数像素，慢镜头里就是「静止帧/跳变帧」交替的持续抖动
    （见 feedback-camera-motion-smoothness）。桥接图静止倍率按片头 INTRO_TRIM 处的
    page-bg scale 反推，保证 3.0s 接缝不跳画幅。
  · 常驻《小王子》+「圣埃克絮佩里 / 著」（top 70/166，82/34px，900 字重）
  · 字幕白字黑投影（bottom 126px，56/50px，无底板），文本以 script-body.csv 为准；
    script-body.csv 里的空格是旧 md-to-sentence-csv 管线留下的视觉断行标记，不是口播内容，
    渲染前统一去掉，改由模板的分级断行器决定视觉行；超过 24 字的长行按子句切成多条，
    并把切换点吸附到配音真实停顿上。
  · 混音参数与 scripts/render-episode-final.mjs 一致（BGM 0.32 / loudnorm -14 LUFS）

产物：episodes/小王子/renders/小王子-final.mp4 + 封面-小王子.png
幂等可重建：重跑即覆盖（候选文件先落 tmp，技术验收通过后才激活）。

预检：--caps-only 只看字幕切条，--wrap-check 只看折行与超宽。
环境变量：INTRO_TRIM_SECONDS=3.0  REBUILD_BG=1（强制重合成正文背景）
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

ROOT = "/Users/apple/Documents/Obsidian/自媒体/短视频/文学/视频制作"
EP = os.path.join(ROOT, "episodes", "小王子")
IMG = os.path.join(EP, "images")
AUDIO = os.path.join(EP, "audio")
RENDERS = os.path.join(EP, "renders")
ASSETS = os.path.join(ROOT, "assets")
PREVIEW = os.path.join(ROOT, "tmp", "preview-book-3de4ff1673")
INTRO_SOURCE = os.path.join(PREVIEW, "intro", "renders", "intro.mp4")
TMP = os.path.join(ROOT, "tmp", "xwz_tpl")
os.makedirs(TMP, exist_ok=True)
os.makedirs(RENDERS, exist_ok=True)

FPS = 30
W, H = 720, 960
FONT = "/System/Library/Fonts/STHeiti Medium.ttc"
TITLE_TEXT = "《小王子》"
AUTHOR_TEXT = "圣埃克絮佩里 / 著"
TITLE_SIZE = 82      # getTitleLayout("小王子")：660/(5+1)=110 → 上限 82
TITLE_TOP = 70
AUTHOR_SIZE = 34
AUTHOR_TOP = 166

# 片头裁切点：共享片头合成时长 4.27s，其中 ~2.62s 起结果页（桥接图 + 书名/作者）稳定定格，
# 切在这里可让片头末帧与正文首帧同为桥接图，消除黑场硬切的闪屏接缝（与已交付系列片对齐）。
INTRO_TRIM = float(os.environ.get("INTRO_TRIM_SECONDS", "3.0"))
INTRO_OFFSET_MS = round(INTRO_TRIM * 1000)
BGM_VOL = 0.32
SCROLL_START, SCROLL_END = 1.08, 2.38
WATER_START = round(INTRO_TRIM + 0.32, 2)

BRIDGE_IN = 0.32       # 桥接图独立停留时长，之后与场景 1 交叉溶解
XFADE_FIRST = 0.64     # 桥接图 → 场景 1
XFADE = 0.72           # 场景之间
MAX_UNIT = 24          # 单条字幕最多约两行、每行 12 字
SMALL_CHARS = 12

# 叙事段落 → 氛围图（atmosphere-*.png 的编号顺序即本书叙事顺序）
SCENE_PLAN = [
    ("atmosphere-1", 1, 10),    # 雨夜城市霓虹：书名 + 越来越像一个大人
    ("atmosphere-2", 11, 15),   # 沙漠星空：B-612 号小行星与离开
    ("atmosphere-3", 16, 24),   # 夜色人群：形形色色的大人
    ("atmosphere-4", 25, 33),   # 手迎窗光：童年的认真 vs 用数字衡量
    ("atmosphere-5", 34, 42),   # 玫瑰：星球上的那朵玫瑰
    ("atmosphere-6", 43, 55),   # 麦田：狐狸「你为它付出的时间」与关系
    ("atmosphere-7", 56, 71),   # 海上日落：不能被数字计算的东西
    ("atmosphere-8", 72, 89),   # 窗边茶与书：慢下来的生活
    ("result-bridge", 90, 101), # 星空：抬头看自己的星星、记得为什么出发
]


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


# --------------------------------------------------------------- 时间轴与文案
timings = json.load(open(os.path.join(AUDIO, "body-timings.json"), encoding="utf-8"))
if timings.get("alignment", {}).get("requiresAgentReview"):
    print(f"[warn] body-timings.json 标记为低置信（method="
          f"{timings['alignment'].get('method')}），已按说话时长兜底校正，"
          f"字幕文本仍以 script-body.csv 为准")
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

# 低置信时间兜底：ASR 对齐偶有行尾锚点过早，按「字数 / 平均语速」估一个最短可见时长，
# 向后延到下一条开始之前（只改时长，不改文本）。
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

# 校验场景计划覆盖全部 order
plan_for_order = {}
for name, a, b in SCENE_PLAN:
    for o in range(a, b + 1):
        plan_for_order[o] = name
uncovered = [o for o in (int(c["order"]) for c in caps) if o not in plan_for_order]
if uncovered:
    raise SystemExit(f"SCENE_PLAN 未覆盖 order: {sorted(set(uncovered))}")
for name, _, _ in SCENE_PLAN:
    if not os.path.exists(os.path.join(IMG, f"{name}.png")):
        raise SystemExit(f"缺少配图素材: {os.path.join(IMG, name + '.png')}")


# ------------------------------------------- 1. 长行切条：子句打包 + 停顿吸附
def silencedetect(path, noise="-35dB", min_dur=0.18):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-af",
                        f"silencedetect=noise={noise}:d={min_dur}", "-f", "null", "-"],
                       capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start:\s*([-\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([-\d.]+)", r.stderr)]
    return [(s + e) / 2 for s, e in zip(starts, ends) if e > s]


pauses = silencedetect(os.path.join(AUDIO, "body-voiceover.mp3"))
# 旧管线用空格标记视觉断行，不是口播内容；统一去掉后交给分级断行器。
SPACE_RE = re.compile(r"\s+")
TRAILING_PUNCT = "，。、；：！？…—·”’）》"
# 折行时只吃掉"被换行替代"的分句标点；书名号、引号、句末语气标点必须保留，
# 否则 "《小王子》" 会被削成 "《小王子"，"越来越像一个“大人”" 会丢掉后引号。
LINE_END_STRIP = "，、；："
CLAUSE_RE = re.compile(r"(?<=[，。、；：！？…])")


def split_long_clause(clause):
    """无标点的超长子句：按字符均分，保证每条 ≤ MAX_UNIT。"""
    n = math.ceil(len(clause) / MAX_UNIT)
    size = math.ceil(len(clause) / n)
    return [clause[i:i + size] for i in range(0, len(clause), size)]


def pack_units(text):
    units = []
    for clause in (c for c in CLAUSE_RE.split(text) if c.strip()):
        pieces = [clause] if len(clause) <= MAX_UNIT else split_long_clause(clause)
        for piece in pieces:
            if units and len(units[-1]) + len(piece) <= MAX_UNIT:
                units[-1] += piece
            else:
                units.append(piece)
    return [u.strip() for u in units if u.strip()]


def allocate_times(units, start, end):
    """按字数比例分配子条时间，再把内部切换点吸附到最近的配音停顿。"""
    n = len(units)
    span = end - start
    total_chars = sum(len(u) for u in units) or 1
    min_keep = span / (n * 1.8)          # 单条最短时长，防止吸附后某条被挤没
    bounds = [start]
    acc = 0
    for u in units[:-1]:
        acc += len(u)
        bounds.append(start + span * acc / total_chars)
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
    text = SPACE_RE.sub("", text_by_order[order])
    units = pack_units(text)
    span = end - start
    # 行太短不足以切多条时，回退为整行同屏（由 caption_lines 折行）
    while len(units) > 1 and span / len(units) < 0.7:
        units[-2] += units[-1]
        units.pop()
    if len(units) == 1:
        caption_items.append((order, text, start, end))
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


# 慢推幅度按源图分辨率自适应：本书 9 张图都是 720x960（cover=1.0），
# 再按共享模板的 1.020→1.062 推镜等于把画面放大 6% 重采样，会明显发糊；
# 源图≈画幅时只做轻微推镜，全程基本 1:1 采样。
def zoom_range(cover):
    if cover > 1.15:      # 源图明显大于画幅（如 1024x1365）：共享模板原本的推镜幅度
        return 1.020, 1.062
    return 1.006, 1.042


# 桥接图静止倍率：片头 page-bg 从 2.38s 起把 scale 由 1 缓推到 1.035（时长 1.87s），
# 正文首帧必须用 INTRO_TRIM 处的同一倍率起幅，否则 3.0s 接缝处画面会跳一档。
_intro_u = min(1.0, max(0.0, (INTRO_TRIM - 2.38) / 1.87))
BRIDGE_ZOOM = 1.0 + 0.035 * ease_in_out(_intro_u)
print(f"[bridge] 静止倍率 {BRIDGE_ZOOM:.4f}（对齐片头 {INTRO_TRIM}s 定格）")


def zoom_frame(im, factor):
    """以图片中心为锚点，按浮点倍率取景到 720x960（无整数取抖）。"""
    iw, ih = im.size
    inv = 1.0 / factor
    return im.transform(
        (W, H), Image.AFFINE,
        (inv, 0.0, iw / 2.0 - W * inv / 2.0,
         0.0, inv, ih / 2.0 - H * inv / 2.0),
        resample=Image.BICUBIC)


# 层出现时刻：桥接图 0s，场景 1 在 BRIDGE_IN，之后取相邻场景首句之间的停顿中点
scene_groups = []
for name, a, b in SCENE_PLAN:
    orders = [o for o in (int(c["order"]) for c in caps) if a <= o <= b]
    first = next(c for c in caps if int(c["order"]) == orders[0])
    last = next(c for c in caps if int(c["order"]) == orders[-1])
    scene_groups.append({"name": name, "start_order": orders[0], "first_start": float(first["start"]),
                         "last_end": float(last["end"])})
appear = [0.0, BRIDGE_IN]
for i in range(1, len(scene_groups)):
    prev_end = scene_groups[i - 1]["last_end"]
    nxt_start = scene_groups[i]["first_start"]
    appear.append((prev_end + nxt_start) / 2)
appear_times = appear + [body_dur]
layer_names = ["result-bridge"] + [g["name"] for g in scene_groups]
xfades = [0.0, XFADE_FIRST] + [XFADE] * (len(scene_groups) - 1)
print("[bg] 层时间表 " + ", ".join(f"{n}@{t:.2f}s" for n, t in zip(layer_names, appear)))

bg_path = os.path.join(TMP, "body-bg.mp4")
covers = {}
for name in set(layer_names):
    src = Image.open(os.path.join(IMG, f"{name}.png")).convert("RGB")
    iw, ih = src.size
    covers[name] = (src, max(W / iw, H / ih))
    print(f"[img] {name:16} {iw}x{ih} cover={max(W / iw, H / ih):.3f} "
          f"zoom={zoom_range(covers[name][1])[0]:.3f}→{zoom_range(covers[name][1])[1]:.3f}")
zooms = {k: zoom_range(v[1]) for k, v in covers.items()}


def layer_frame(k, t):
    src, cover = covers[layer_names[k]]
    if k == 0:
        return zoom_frame(src, cover * BRIDGE_ZOOM)
    z0, z1 = zooms[layer_names[k]]
    window = appear_times[k + 1] - appear_times[k]
    u = (t - appear_times[k]) / window if window > 0 else 1.0
    return zoom_frame(src, cover * (z0 + (z1 - z0) * ease_in_out(u)))


if os.path.exists(bg_path) and abs(probe_dur(bg_path) - body_dur) < 0.2 \
        and not os.environ.get("REBUILD_BG"):
    print(f"[bg] 复用 {bg_path} {probe_dur(bg_path):.2f}s")
else:
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
        pipe.stdin.write(cur.tobytes())
        if frame % 1500 == 0:
            print(f"[bg] {frame}/{n_total} {t:.1f}s")
    pipe.stdin.close()
    pipe.wait()
    if pipe.returncode != 0:
        raise SystemExit(f"正文背景合成失败: {pipe.returncode}")
    del covers
print(f"[bg] {probe_dur(bg_path):.2f}s (target {body_dur:.2f}s)")


# ------------------------------------ 3. 常驻书名层 + 字幕层（白字黑投影，无底板）
def spaced_text(im, text, font, top, spacing_em):
    """逐字排布模拟模板 letter-spacing，并加 0 7px 18px rgba(0,0,0,.96) 投影。"""
    spacing = font.size * spacing_em
    widths = [font.getlength(ch) for ch in text]
    total = sum(widths) + spacing * (len(text) - 1)
    x0 = (W - total) / 2
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    x = x0
    for ch, cw in zip(text, widths):
        sd.text((x, top + 7), ch, font=font, fill=(0, 0, 0, 245))
        x += cw + spacing
    shadow = shadow.filter(ImageFilter.GaussianBlur(9))
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
spaced_text(title_im, AUTHOR_TEXT, ImageFont.truetype(FONT, AUTHOR_SIZE), AUTHOR_TOP, 0.06)
title_png = os.path.join(TMP, "title.png")
title_im.save(title_png)


BREAK_AFTER = "，。、；：！？…—的地着过于了"
BREAK_BEFORE = "《“‘（是也不就才能够让把被但而却还更这那由以和或并且使令向从到"
# 这些双字词中间不能断行（否则会出现「不/能」「星/星」这类硬切）
NO_SPLIT = {"只能", "可能", "能够", "才能", "得不", "一个", "某个", "于是", "所以", "因为", "无法",
            "错过", "值得", "今天", "重读", "地方", "存在", "回忆", "一段", "人生", "童年",
            "不是", "也是", "就是", "还有", "这样", "那样", "自己", "我们", "你们", "他们",
            "成为", "怎样", "如何", "必须", "需要", "可以", "经历", "回应", "愿望", "越来越",
            "小王子", "星球", "玫瑰", "星星", "日落", "大人", "孩子", "世界", "生活", "认真"}


def _can_break(body, j):
    """j 是下一行的起始下标；判断能否在 j 处断行。"""
    if not (0 < j < len(body)):
        return False
    if body[j] in "”’）》。！？，、；：":       # 闭合标点不能出现在行首
        return False
    if body[j - 1] + body[j] in NO_SPLIT:
        return False
    return True


def _tier_of(body, j):
    if body[j - 1] in "，。、；：！？…—":
        return 1                                   # 分句标点之后最自然
    if body[j - 1] in BREAK_AFTER or body[j] in BREAK_BEFORE:
        return 2                                   # 虚词边界
    return 3


def pick_break(s, lo, hi):
    """在 [lo, hi] 里挑断点：先比层级，再比与区间中点的距离。"""
    mid = (lo + hi) // 2
    pool = [j for j in range(max(1, lo), min(len(s), hi) + 1) if _can_break(s, j)]
    if not pool:
        return max(1, min(mid, len(s) - 1))
    best_tier = min(_tier_of(s, j) for j in pool)
    if best_tier == 3:
        return mid
    return min([j for j in pool if _tier_of(s, j) == best_tier], key=lambda j: abs(j - mid))


def caption_lines(text):
    """每行 ≤12 字、最多 3 行；14 字以内保持单行，靠字号自适应放下。"""
    body = text.strip()
    if len(body) <= 14:
        return [body]
    lines = []
    rest = body
    while rest and len(lines) < 2:
        slots = 3 - len(lines)
        if len(rest) <= 12 + 2:
            break
        lo = max(6, math.ceil(len(rest) / slots) - 3)
        hi = min(len(rest) - 4, 12 + 3)
        if hi < lo:
            break
        j = pick_break(rest, lo, hi)
        lines.append(rest[:j])
        rest = rest[j:]
    lines.append(rest)
    return [ln for ln in lines if ln] or [body]


def build_caption(text, path):
    lines = [ln.rstrip(LINE_END_STRIP) for ln in caption_lines(text)]
    # 闭合标点不单独出现在行首：并回上一行行尾
    while len(lines) >= 2 and lines[1][:1] in "”’）》。！？，、；：":
        lines[0] += lines[1][0]
        lines[1] = lines[1][1:]
        if not lines[1]:
            lines.pop(1)
    lines = [ln for ln in lines if ln]
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


if "--wrap-check" in sys.argv:   # 只预检折行，不进入合成
    for order, text, s, e in caption_items:
        lines = [ln.rstrip(LINE_END_STRIP) for ln in caption_lines(text)]
        size = 56 if max(len(ln) for ln in lines) <= SMALL_CHARS else 50
        f = ImageFont.truetype(FONT, size)
        while max(f.getlength(ln) for ln in lines) > W - 68 and size > 36:
            size -= 2
            f = ImageFont.truetype(FONT, size)
        flag = "  <== 超宽" if max(f.getlength(ln) for ln in lines) > W - 68 else ""
        print(f"{s:7.2f} [{order:>3}] {size}px | {' / '.join(lines)}{flag}")
    sys.exit(0)

transparent = os.path.join(TMP, "transparent.png")
Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(transparent)

# 字幕轨按「绝对帧号」锚定，而不是把每条四舍五入后的帧数再累加：
# 累加法会把每条 ≤0.05s 的间隙整条丢掉、再叠加 ±0.5 帧取整误差，
# 实测到片尾累计滞后 ~267ms（字幕比配音慢）。绝对帧号不存在漂移。
target_frames = round(body_dur * FPS)


def frame_of(t):
    return min(target_frames, max(0, round(t * FPS)))


entries = []
cursor = 0
for i, (order, text, s, e) in enumerate(caption_items):
    p = os.path.join(TMP, f"cap-{i:03d}-{order:03d}.png")
    build_caption(text, p)
    f0, f1 = frame_of(s), frame_of(e)
    if f1 <= f0:
        f1 = f0 + 1
    if f0 > cursor:
        entries.append((f0 - cursor, transparent))
    entries.append((f1 - f0, p))
    cursor = max(cursor, f1)
if cursor < target_frames:
    entries.append((target_frames - cursor, transparent))
print(f"[caps] 字幕轨 {sum(n for n, _ in entries)} 帧 = {sum(n for n, _ in entries) / FPS:.2f}s"
      f"（绝对帧锚定，最大条目误差 <1 帧）")

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
    raise SystemExit(f"缺少 HyperFrames 片头渲染结果: {INTRO_SOURCE}")
intro_mp4 = os.path.join(TMP, "intro.mp4")
ff(["-i", INTRO_SOURCE,
    "-vf", f"trim=0:{INTRO_TRIM},setpts=PTS-STARTPTS,fps={FPS},format=yuv420p",
    "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS), "-crf", "18", intro_mp4])


# ------------------------------------------------------------------ 5. 混音合成
intro_story = os.path.join(PREVIEW, "audio", "intro-voiceover-story.mp3")
if not os.path.exists(intro_story):
    os.makedirs(os.path.dirname(intro_story), exist_ok=True)
    run(["node", "scripts/process-voiceover.mjs",
         os.path.join(ASSETS, "template-audio", "intro-voiceover.mp3"),
         intro_story, "story"])
body_story = os.path.join(AUDIO, "body-voiceover-story.mp3")
bgm = os.path.join(ASSETS, "bgm", "Where are you(low).mp3")
scroll_sfx = os.path.join(ASSETS, "sfx", "gear-scroll.mp3")
water_sfx = os.path.join(ASSETS, "sfx", "water.mp3")
for p in [intro_story, body_story, bgm, scroll_sfx, water_sfx]:
    if not os.path.exists(p):
        raise SystemExit(f"缺少音频素材: {p}")

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
    f"[6:a]atrim=0:1.06,asetpts=PTS-STARTPTS,aresample=48000,volume=1.0,"
    f"adelay={round(WATER_START * 1000)}|{round(WATER_START * 1000)}[watersfx]",
    "[introa][bodya][bgm][scrollsfx][watersfx]amix=inputs=5:duration=longest:dropout_transition=0:normalize=0,"
    "alimiter=limit=0.95,loudnorm=I=-14.0:TP=-1.0:LRA=7.0,"
    "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a]",
]
candidate = os.path.join(TMP, "小王子-final.mp4")
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
out_final = os.path.join(RENDERS, "小王子-final.mp4")
shutil.move(candidate, out_final)


# --------------------------------------------------------- 6. 封面（1080x1920 竖版）
cover_out = os.path.join(RENDERS, "封面-小王子.png")
cover = Image.open(os.path.join(IMG, "atmosphere-2.png")).convert("RGB")
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
d.text((540, 250), "《小王子》", font=ImageFont.truetype(FONT, 96),
       fill=(255, 255, 255, 255), stroke_width=4, stroke=(0, 0, 0, 235), anchor="ma")
d.text((540, 400), "圣埃克絮佩里 / 著", font=ImageFont.truetype(FONT, 40),
       fill=(240, 240, 240, 240), stroke_width=2, stroke=(0, 0, 0, 215), anchor="ma")
d.text((540, 1780), "读书视频 · 文学经典系列", font=ImageFont.truetype(FONT, 30),
       fill=(225, 225, 225, 210), anchor="ma")
cover.convert("RGB").save(cover_out)
print(f"[cover] {cover_out}")
print(f"\n✅ 成片: {out_final}  ({dur:.2f}s)")
