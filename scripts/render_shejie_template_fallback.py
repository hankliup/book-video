#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""《蛇结》共享模板样式兜底渲染（ffmpeg + Pillow，不依赖 HyperFrames 正文会话）。

背景与《荆棘鸟》《白鲸》一致：HyperFrames 0.7.33 在本机对 226s 正文的单次会话渲染会崩
（Page.captureScreenshot: Target closed），分段渲染又因运行时只认「创作时」写入的字幕时间
（事后 shiftChildren 被忽略）而不可用，故按 templates/shared-video-template 的视觉与混音
规格做浮点合成复刻：

  · 片头复用本期 HyperFrames 渲染成功的玻璃碎片片头 intro.mp4，裁到结果页定格 3.0s
  · 正文 result-bridge 起幅停留到「水波窗口收束后的第一个语音停顿中点」→ 8 张氛围图按叙事
    段落切换，最后回到 result-bridge（光柱下的绳结与日记）收在「结系在自己心里」，
    段间 0.64/0.72s 交叉溶解
  · 运镜用 PIL.Image.transform 的浮点仿射矩阵做居中缓推（1.020→1.062，缓入缓出）。
    不用 ffmpeg zoompan：它把 x/y/裁剪宽高取整到源图整数像素，慢镜头里就是持续抖动。
  · 桥接图倍率不是写死的 1.035，而是按片头 `.target .page-bg` 的 GSAP 曲线
    （2.38s→4.25s，1→1.035，sine.inOut）算出裁切点那一刻的真实倍率，
    使片头末帧与正文首帧同画幅，消除接缝处的轻微跳幅。
  · 常驻《蛇结》+「弗朗索瓦·莫里亚克 / 著」（top 70/166，82/34px，900 字重）
  · 字幕白字黑投影（bottom 126px，56/50px，无底板），文本以 script-body.csv 为准；
    超过 24 字的长行按子句切分成多条，并把切换点吸附到配音真实停顿上。
  · 水波视觉效果与水波音效同窗（常驻规则）：切点后 WATER_OFFSET 秒起、持续 WATER_DUR 秒，
    背景层调 lib/water_ripple.py 的 ripple() 荡出一圈自中心外扩的涟漪；书名层与字幕层在其后
    合成，字不会被扭糊。常数只在 lib/water-ripple.json 里有一份，混音 adelay/atrim 同源。
  · 混音参数与 scripts/render-episode-final.mjs 一致（BGM 0.32 / loudnorm -14 LUFS）

产物：episodes/蛇结/renders/蛇结-final.mp4 + 封面-蛇结.png
幂等可重建：候选文件先落 tmp，技术验收（含 check-water-ripple.py 水波同窗门禁）通过后才激活到 renders/。
body-bg.mp4 按 body-bg.json 签名（含 water_frame0/water_nframes）判断能否复用，签名不符即重合成。

用法: python3 scripts/render_shejie_template_fallback.py [--caps-only]
环境变量: INTRO_TRIM_SECONDS=3.0
          REBUILD_BG=1 / FORCE_BG=1（强制重合成正文背景；两个旗标都认）
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
EP = os.path.join(ROOT, "episodes", "蛇结")
IMG = os.path.join(EP, "images")
AUDIO = os.path.join(EP, "audio")
RENDERS = os.path.join(EP, "renders")
ASSETS = os.path.join(ROOT, "assets")
PREVIEW = os.path.join(ROOT, "tmp", "preview-book-79cda867ff")
INTRO_SOURCE = os.path.join(PREVIEW, "intro", "renders", "intro.mp4")
TMP = os.path.join(ROOT, "tmp", "sj_tpl")
os.makedirs(TMP, exist_ok=True)
os.makedirs(RENDERS, exist_ok=True)

FPS = 30
W, H = 720, 960
FONT = "/System/Library/Fonts/STHeiti Medium.ttc"
TITLE_TEXT = "《蛇结》"
AUTHOR_TEXT = "弗朗索瓦·莫里亚克 / 著"
TITLE_SIZE = 82      # getTitleLayout("蛇结")：660/(4+1)=132 → 上限 82
TITLE_TOP = 70
AUTHOR_SIZE = 34
AUTHOR_TOP = 166

# 片头裁切点：共享片头合成时长 4.27s，其中 ~2.62s 起结果页稳定定格，
# 切在这里可让片头末帧与正文首帧同为桥接图（与已交付系列片对齐）。
INTRO_TRIM = float(os.environ.get("INTRO_TRIM_SECONDS", "3.0"))
INTRO_OFFSET_MS = round(INTRO_TRIM * 1000)
BGM_VOL = 0.32
SCROLL_START, SCROLL_END = 1.08, 2.38
# 水波窗口 = 片头→正文切点后 WATER_OFFSET 秒起、持续 WATER_DUR 秒。
# 常数真源在 scripts/lib/water-ripple.json（Python 侧入口 lib/water_ripple.py），
# 音效的 adelay/atrim 与背景层的涟漪窗口同源，改一处两端同时生效。
# 这里绝不出现 0.32 / 1.06 这样的秒数字面量——否则就是「有水声没水波」或
# 「水波比声音早半秒」这种没人报错的错窗。
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from water_ripple import WATER_DUR, WATER_OFFSET, ripple, water_u  # noqa: E402

WATER_START = round(INTRO_TRIM + WATER_OFFSET, 2)   # 成片时间轴上的水波起点（秒）
WATER_NFRAMES = round(WATER_DUR * FPS)              # 涟漪帧数
# 正文背景第 0 帧对应成片 INTRO_TRIM，所以背景循环里的起点是「相对切点的偏移秒」
WATER_FRAME0 = round(WATER_OFFSET * FPS)

# 片头结果页底图的推近曲线：tl.to(".target .page-bg", {scale:1.035, duration:1.87}, 2.38)
INTRO_BG_START, INTRO_BG_END_SCALE, INTRO_BG_DUR = 2.38, 1.035, 1.87

BRIDGE_MIN_HOLD = 0.32  # 桥接图最少停留时长；实际切点按水波窗口后的第一个停顿推导，见下方
XFADE_FIRST = 0.64     # 桥接图 → 场景 1
XFADE = 0.72           # 场景之间
ZOOM_START = 1.020
ZOOM_END = 1.062
MAX_UNIT = 24          # 单条字幕最多约两行、每行 12 字
SMALL_CHARS = 12


def sine_in_out(u):
    return 0.5 - 0.5 * math.cos(math.pi * min(1.0, max(0.0, u)))


# 桥接图倍率 = 片头在裁切那一刻的实际 scale，保证接缝零跳幅
BRIDGE_ZOOM = 1.0 + (INTRO_BG_END_SCALE - 1.0) * sine_in_out(
    (INTRO_TRIM - INTRO_BG_START) / INTRO_BG_DUR)

# 叙事段落 → 配图（atmosphere-*.png 编号即本书叙事顺序）
SCENE_PLAN = [
    ("atmosphere-7", 1, 8),     # 书名+钩子：怀表被绳索打成死结
    ("atmosphere-5", 9, 16),    # 主人公路易：富有的老人、晚年觉得被背叛
    ("atmosphere-2", 17, 21),   # 把不满写进日记：暗处窥视的老人伏案
    ("atmosphere-3", 22, 29),   # 蛇结的含义与莫里亚克的笔法：古书页与蘸水笔
    ("atmosphere-6", 30, 38),   # 用金钱控制家人、越渴望越推远：餐桌上的争执
    ("atmosphere-4", 39, 48),   # 把委屈当许可证、一直在计算亏欠：窗光下的侧影
    ("atmosphere-1", 49, 58),   # 敌人是自己、生命结束时终于看见自己：空荡老宅门厅
    ("atmosphere-8", 59, 70),   # 换一个人结果相似、财富地位输赢皆空：昏暗的大窗
    ("result-bridge", 71, 83),  # 「真正的问题只剩一个」起回到光柱下的绳结与日记，收在忏悔
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

# 低置信时间兜底：句首行太短（如「所以，」仅 0.24s）时按语速估一个最短可见时长，
# 向后延到下一条开始之前，只改时长不改文本。
AVG_RATE = 4.9          # 本期旁白中位语速约 4.9 字/秒
MIN_KEEP = 0.9
adjusted = []
for i, c in enumerate(caps):
    text = text_by_order[int(c["order"])]
    span = float(c["end"]) - float(c["start"])
    need = max(MIN_KEEP, len(text) / AVG_RATE * 0.55)
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
        print(f"       order {o:>2}: {was}s → {now}s")

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


pauses = silencedetect(os.path.join(AUDIO, "body-voiceover-story.mp3"))
TRAILING_PUNCT = "，。、；：！？…—·”’）》"
CLAUSE_RE = re.compile(r"(?<=[，。、；：！？…])")


def split_long_clause(clause):
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
    min_keep = span / (n * 1.8)
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
    for i in range(1, n):
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
    span = end - start
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
if "--caps-only" in sys.argv:
    for item in track:
        print(f"  {item['start']:7.2f}-{item['end']:7.2f} [{item['order']:>2}] {item['text']}")
    sys.exit(0)


# ------------------------------- 2. 正文背景：亚像素缓推 + 交叉溶解（单趟合成）
def zoom_frame(im, factor):
    """以图片中心为锚点，按浮点倍率取景到 720x960（无整数取抖）。"""
    iw, ih = im.size
    inv = 1.0 / factor
    return im.transform(
        (W, H), Image.AFFINE,
        (inv, 0.0, iw / 2.0 - W * inv / 2.0,
         0.0, inv, ih / 2.0 - H * inv / 2.0),
        resample=Image.BICUBIC)


scene_groups = []
for name, a, b in SCENE_PLAN:
    orders = [o for o in (int(c["order"]) for c in caps) if a <= o <= b]
    first = next(c for c in caps if int(c["order"]) == orders[0])
    last = next(c for c in caps if int(c["order"]) == orders[-1])
    scene_groups.append({"name": name, "start_order": orders[0],
                         "first_start": float(first["start"]), "last_end": float(last["end"])})
# 桥接图停留到「水波窗口收束之后的第一个语音停顿中点」。
# 涟漪（正文 WATER_OFFSET → WATER_OFFSET+WATER_DUR）必须整段落在静止底图上：压在
# 桥接图→场景1 的交叉溶解里，观感是「一坨水下溶解」而不是一圈波，门禁 [water-video]
# 的径向质心也会被溶解自身的运动量污染（《蛇结》2026-09-27 实测：无波时溶解已经喂出
# 逐帧差 1.17 灰阶，却只外扩 17px）。切点仍取停顿中点，不会话没说完就跳画面。
WATER_END_BODY = round(WATER_OFFSET + WATER_DUR, 3)


def bridge_hold(items, t_min, floor):
    seq = sorted(items, key=lambda it: it[2])
    for a, b in zip(seq, seq[1:]):
        mid = (float(a[3]) + float(b[2])) / 2
        if float(b[2]) - float(a[3]) > 0.05 and mid >= t_min:
            return max(floor, round(mid, 2))
    return max(floor, round(t_min + 0.12, 2))


BRIDGE_IN = bridge_hold(caption_items, WATER_END_BODY, BRIDGE_MIN_HOLD)
appear = [0.0, BRIDGE_IN]
for i in range(1, len(scene_groups)):
    prev_end = scene_groups[i - 1]["last_end"]
    nxt_start = scene_groups[i]["first_start"]
    appear.append((prev_end + nxt_start) / 2)
appear_times = appear + [body_dur]
layer_names = ["result-bridge"] + [g["name"] for g in scene_groups]
xfades = [0.0, XFADE_FIRST] + [XFADE] * (len(scene_groups) - 1)
print(f"[bg] 桥接倍率 {BRIDGE_ZOOM:.4f}（片头 {INTRO_TRIM}s 处实际 scale）")
print("[bg] 层时间表 " + ", ".join(f"{n}@{t:.2f}s" for n, t in zip(layer_names, appear)))

bg_path = os.path.join(TMP, "body-bg.mp4")
covers = {}
for name in set(layer_names):
    src = Image.open(os.path.join(IMG, f"{name}.png")).convert("RGB")
    iw, ih = src.size
    covers[name] = (src, max(W / iw, H / ih))


def layer_frame(k, t):
    src, cover = covers[layer_names[k]]
    if k == 0:                          # 起幅桥接图静止，与片头定格画幅对齐
        return zoom_frame(src, cover * BRIDGE_ZOOM)
    window = appear_times[k + 1] - appear_times[k]
    u = (t - appear_times[k]) / window if window > 0 else 1.0
    return zoom_frame(src, cover * (ZOOM_START + (ZOOM_END - ZOOM_START) * sine_in_out(u)))


# body-bg.mp4 只有在「所有输入都没变」时才可复用：时长、切镜表、桥接倍率、水波窗口任一变化
# 都必须重合成。否则改了 scene 时序或加了水波却静默拿到旧背景，验收还全绿——这正是
# 《奥勃洛莫夫》《摩诃婆罗多》那几遍「有水声没水波」的成因。标记落 body-bg.json。
_bg_sig = {"body_dur": round(body_dur, 3), "fps": FPS,
           "layers": layer_names, "layer_appear": [round(x, 4) for x in appear],
           "xfades": [round(x, 4) for x in xfades],
           "bridge_zoom": round(BRIDGE_ZOOM, 6),
           "water_frame0": WATER_FRAME0, "water_nframes": WATER_NFRAMES}
_bg_marker = os.path.join(TMP, "body-bg.json")


def _bg_reusable():
    if not (os.path.exists(bg_path) and abs(probe_dur(bg_path) - body_dur) < 0.2):
        return False
    try:
        return json.load(open(_bg_marker, encoding="utf-8")) == _bg_sig
    except Exception:
        return False


def _bg_forced():
    # 本脚本历史旗标是 REBUILD_BG，通用渲染器用 FORCE_BG，两个都认，避免传错等于没传。
    return bool(os.environ.get("REBUILD_BG") or os.environ.get("FORCE_BG"))


if os.path.exists(bg_path) and abs(probe_dur(bg_path) - body_dur) < 0.2 \
        and _bg_reusable() and not _bg_forced():
    print(f"[bg] 复用 {bg_path} {probe_dur(bg_path):.2f}s（切镜表与水波窗口未变）")
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
        # 水波视觉效果：常驻规则，必须与水波音效同帧起、同帧止。
        # 只作用在背景层——书名层与字幕层在其后才 overlay，所以涟漪不会把字扭糊。
        _wu = water_u(frame, FPS, WATER_FRAME0, WATER_NFRAMES)
        if _wu is not None:
            cur = ripple(cur, _wu)
        pipe.stdin.write(cur.tobytes())
        if frame and frame % 1500 == 0:
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


def caption_lines(text):
    """一行 12 字以内单行；否则折成两行。

    script-body.csv 用空格标记短语边界（= 模板里 CSS 自然折行的位置），所以优先在
    靠近中点的空格处断行；没有可用空格时退回到靠近中点的标点处断行。
    """
    body = " ".join(text.split())
    if len(body) <= SMALL_CHARS:
        return [body]
    if len(body) <= 2 * SMALL_CHARS + 2:
        space_idx = [i for i, ch in enumerate(body) if ch == " "]
        if space_idx:
            k = min(space_idx, key=lambda i: abs(i + 0.5 - len(body) / 2))
            left, right = body[:k].strip(), body[k + 1:].strip()
            if left and right and max(len(left), len(right)) <= SMALL_CHARS + 2:
                return [x for x in (left.rstrip(TRAILING_PUNCT),
                                    right.rstrip(TRAILING_PUNCT)) if x]
        mid = len(body) // 2
        best = None
        for off in range(0, mid):
            for j in (mid - off, mid + off):
                if 0 < j < len(body) and body[j - 1] in "，。、；：！？…—":
                    best = j
                    break
            if best:
                break
        if best is None:
            best = mid
        left = body[:best].rstrip(TRAILING_PUNCT)
        right = body[best:].rstrip(TRAILING_PUNCT)
        return [x for x in (left, right) if x] or [body]
    return [body[:SMALL_CHARS], body[SMALL_CHARS:]]


def build_caption(text, path):
    lines = [ln.strip().rstrip(TRAILING_PUNCT) for ln in caption_lines(text)]
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


transparent = os.path.join(TMP, "transparent.png")
Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(transparent)

entries = []
prev_end = 0.0
for i, (order, text, s, e) in enumerate(caption_items):
    if s - prev_end > 0.05:
        entries.append((max(1, round((s - prev_end) * FPS)), transparent))
    p = os.path.join(TMP, f"cap-{i:03d}-{order:02d}.png")
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


# ------------------------------------------------------- 4. 片头（复用本期片头渲染）
if not os.path.exists(INTRO_SOURCE):
    raise SystemExit(f"缺少 HyperFrames 片头渲染结果: {INTRO_SOURCE}")
intro_mp4 = os.path.join(TMP, "intro.mp4")
ff(["-i", INTRO_SOURCE,
    "-vf", f"trim=0:{INTRO_TRIM},setpts=PTS-STARTPTS,fps={FPS},format=yuv420p",
    "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS), "-crf", "18", intro_mp4])


# ------------------------------------------------------------------ 5. 混音合成
intro_story = os.path.join(ASSETS, "template-audio", "intro-voiceover-story.mp3")
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
    f"[6:a]atrim=0:{WATER_DUR},asetpts=PTS-STARTPTS,aresample=48000,volume=1.0,"
    f"adelay={round(WATER_START * 1000)}|{round(WATER_START * 1000)}[watersfx]",
    "[introa][bodya][bgm][scrollsfx][watersfx]amix=inputs=5:duration=longest:dropout_transition=0:normalize=0,"
    "alimiter=limit=0.95,loudnorm=I=-14.0:TP=-1.0:LRA=7.0,"
    "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a]",
]
candidate = os.path.join(TMP, "蛇结-final.mp4")
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

# 水波同窗校验（常驻规则，见 AGENTS.md「Visual And Audio Rules」）：water.mp3 在切点后起播，
# 画面必须同时出现一圈外扩涟漪。这道校验卡在 shutil.move 之前——「只加声音不加画面」是唯一
# 一个分辨率/时长/音频流/字幕/切点全部照常通过的缺陷，成片看起来完全正常，只有量化门禁拦得住。
# 门禁自己读 lib 的 WATER_OFFSET/WATER_DUR，所以这里只传切点与帧率，不传任何秒数。
_water_gate = subprocess.run(
    [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "check-water-ripple.py"),
     candidate, "--intro-trim", str(INTRO_TRIM), "--fps", str(FPS)],
    capture_output=True, text=True)
print(_water_gate.stdout.strip())
if _water_gate.returncode != 0:
    raise SystemExit(
        "水波同窗门禁未通过，候选不激活到 renders/：水声与可见涟漪必须落在同一帧窗口。"
        "查背景层是否调了 lib 的 ripple()、混音是否引了 assets/sfx/water.mp3。"
        "已叠字幕的成片不能事后补波（会扭糊字幕），必须重渲正文背景（REBUILD_BG=1）。\n"
        + (_water_gate.stderr or "")[-500:])

out_final = os.path.join(RENDERS, "蛇结-final.mp4")
shutil.move(candidate, out_final)


# --------------------------------------------------------- 6. 封面（1080x1920 竖版）
cover_out = os.path.join(RENDERS, "封面-蛇结.png")
cover = Image.open(os.path.join(IMG, "result-bridge.png")).convert("RGB")
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
d.text((540, 400), AUTHOR_TEXT, font=ImageFont.truetype(FONT, 40),
       fill=(240, 240, 240, 240), stroke_width=2, stroke=(0, 0, 0, 215), anchor="ma")
d.text((540, 1700), "毁掉一段关系的，往往不是没有爱", font=ImageFont.truetype(FONT, 40),
       fill=(245, 245, 245, 235), stroke_width=2, stroke=(0, 0, 0, 200), anchor="ma")
d.text((540, 1780), "读书视频 · 文学经典系列", font=ImageFont.truetype(FONT, 30),
       fill=(225, 225, 225, 210), anchor="ma")
cover.convert("RGB").save(cover_out)
print(f"[cover] {cover_out}")
print(f"\n✅ 成片: {out_final}  ({dur:.2f}s)")
