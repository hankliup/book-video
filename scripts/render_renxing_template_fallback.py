#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""《人性的枷锁》共享模板样式兜底渲染（ffmpeg + Pillow，不依赖 HyperFrames 正文会话）。

背景：HyperFrames 0.7.33 在本机对 297s 正文（约 8917 帧）的单次会话渲染稳定崩溃
（Page.captureScreenshot: Target closed，先例见《道林·格雷的画像》264s /《荆棘鸟》262s）；
分段渲染又因运行时只认「创作时」写入的字幕时间（事后 shiftChildren 被忽略）而无法复用，
故按 templates/shared-video-template 的视觉与混音规格做浮点合成复刻：

  · 片头复用 HyperFrames 渲染成功的玻璃碎片片头（intro.mp4），裁到结果页定格 3.0s
  · 正文 result-bridge 起幅 0.32s → 8 张氛围图按叙事段落切换，0.64/0.72s 交叉溶解
  · 运镜用 PIL.Image.transform 的浮点仿射矩阵做居中缓推（1.020→1.062，缓入缓出），
    不使用 ffmpeg zoompan，也不加模板里的 x/y 斜向漂移：zoompan 会把 x/y/裁剪宽高取整到
    源图整数像素，慢镜头里就是持续抖动（《道林·格雷的画像》成片实测相邻帧静止/跳变交替）。
  · 常驻《人性的枷锁》+「毛姆 / 著」（top 70/166，82/34px，900 字重）
  · 字幕白字黑投影（bottom 126px，56/50px，无底板），文本以 script-body.csv 为准；
    超过 24 字的长行按子句切分成多条，并把切换点吸附到配音真实停顿上。
  · 水波：assets/sfx/water.mp3 在切点后 WATER_OFFSET 秒起播 WATER_DUR 秒，同一窗口内
    背景层施加 lib/water_ripple.ripple()（书名/字幕在其后绘制），激活前跑 check-water-ripple.py
  · 混音参数与 scripts/render-episode-final.mjs 一致（BGM 0.32 / loudnorm -14 LUFS）

产物：episodes/人性的枷锁/renders/人性的枷锁-final.mp4 + 封面-人性的枷锁.png
幂等可重建：重跑即覆盖（候选文件先落 tmp，技术验收通过后才激活）。

用法：
  python3 scripts/render_renxing_template_fallback.py            # 完整渲染
  python3 scripts/render_renxing_template_fallback.py --caps-only # 只做字幕切条预检
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
EP = os.path.join(ROOT, "episodes", "人性的枷锁")
IMG = os.path.join(EP, "images")
AUDIO = os.path.join(EP, "audio")
RENDERS = os.path.join(EP, "renders")
ASSETS = os.path.join(ROOT, "assets")
PREVIEW = os.path.join(ROOT, "tmp", "preview-book-a04c35a315")
INTRO_SOURCE = os.path.join(PREVIEW, "intro", "renders", "intro.mp4")
TMP = os.path.join(ROOT, "tmp", "renxing_tpl")
os.makedirs(TMP, exist_ok=True)
os.makedirs(RENDERS, exist_ok=True)

FPS = 30
W, H = 720, 960
FONT = "/System/Library/Fonts/STHeiti Medium.ttc"   # 共享模板在本机的实际回退字体
TITLE_TEXT = "《人性的枷锁》"
AUTHOR_TEXT = "毛姆 / 著"
TITLE_SIZE = 82      # getTitleLayout：660/(7+1)=82.5 → 上限 82
TITLE_TOP = 70
AUTHOR_SIZE = 34
AUTHOR_TOP = 166

# 片头裁切点：共享片头合成时长 4.27s，其中 ~2.62s 起结果页（桥接图 + 书名/作者）稳定定格，
# 切在这里可让片头末帧与正文首帧同为桥接图，消除黑场硬切的闪屏接缝（与已交付系列片对齐）。
INTRO_TRIM = float(os.environ.get("INTRO_TRIM_SECONDS", "3.0"))
INTRO_OFFSET_MS = round(INTRO_TRIM * 1000)
BGM_VOL = 0.32
SCROLL_START, SCROLL_END = 1.08, 2.38

# 水波窗口 = 片头→正文切点后 WATER_OFFSET 秒起、持续 WATER_DUR 秒。
# 音效与画面必须共用这一对常数（真源 scripts/lib/water-ripple.json，薄壳 scripts/lib/water_ripple.py）：
# 这里过去写着 `WATER_START = round(INTRO_TRIM + 0.32, 2)` 且背景层从未施加涟漪，结果成片 3.32s
# 「有水声没水波」——时长/字幕/响度/音画对齐全部照常通过，只有 check-water-ripple.py 拦得住。
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from water_ripple import WATER_DUR, WATER_OFFSET, ripple, water_u  # noqa: E402

WATER_START = round(INTRO_TRIM + WATER_OFFSET, 2)   # 成片时间轴上的水波起点（秒）
WATER_NFRAMES = round(WATER_DUR * FPS)              # 涟漪帧数
# 正文背景第 0 帧 = 成片 INTRO_TRIM，所以背景循环里的起点帧直接是偏移秒换算
WATER_FRAME0 = round(WATER_OFFSET * FPS)

BRIDGE_IN = 0.32       # 桥接图独立停留时长，之后与场景 1 交叉溶解
XFADE_FIRST = 0.64     # 桥接图 → 场景 1
XFADE = 0.72           # 场景之间
ZOOM_START = 1.020
ZOOM_END = 1.062

# 桥接图缩放必须等于片头 page-bg 推镜在 INTRO_TRIM 时刻的**实际**缩放，
# 否则 3.0s 接缝会「跳一下」。共享片头：tl.to(".target .page-bg",{scale:1.035,
# duration:1.87,ease:"sine.inOut"},2.38) —— 直接抄别的书的 BRIDGE_ZOOM=1.035
# 会让正文首帧比片头末帧大 2.6%（实测接缝最佳拟合缩放 1.028）。
INTRO_BG_SCALE = 1.035
INTRO_BG_AT = 2.38
INTRO_BG_DUR = 1.87


def intro_bg_scale_at(t):
    u = min(1.0, max(0.0, (t - INTRO_BG_AT) / INTRO_BG_DUR))
    eased = 0.5 * (1.0 - math.cos(math.pi * u))      # sine.inOut
    return 1.0 + (INTRO_BG_SCALE - 1.0) * eased


BRIDGE_ZOOM = round(intro_bg_scale_at(INTRO_TRIM), 5)
MAX_UNIT = 24          # 单条字幕最多约两行、每行 12 字
SMALL_CHARS = 12

# 叙事段落 → 氛围图（编号即 images/atmosphere-*.png）
SCENE_PLAN = [
    ("atmosphere-1", 1, 12),    # 钩子：明知不合适却舍不得离开，内心的执念
    ("atmosphere-2", 13, 19),   # 菲利普：失去双亲、宗教家庭、跛足、神学会计医学绘画
    ("atmosphere-3", 20, 30),   # 我到底应该怎样活 / 爱上米尔德里德、深深陷入
    ("atmosphere-4", 31, 42),   # 人并不总是被现实困住：欲望、虚荣、自卑、恐惧
    ("atmosphere-7", 43, 56),   # 接受普通与不完美，自己决定怎么生活，走向一线光
    ("atmosphere-6", 57, 75),   # 人生没有固定剧本：吃饭工作恋爱旅行养育，普通人的一生
    ("atmosphere-5", 76, 90),   # 看不见的枷锁、问自己、已经走在挣脱的路上
    ("atmosphere-8", 91, 103),  # 不再需要标准答案，把选择权交还给自己
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

# 低置信时间兜底：ASR 对齐偶有行尾锚点过早的行，按「字数 / 平均语速」估一个最短可见时长，
# 向后延到下一条开始之前（只改时长，不改字幕文本）。
AVG_RATE = 4.5          # 本系列旁白平均约 4.5 字/秒
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
TRAILING_PUNCT = "，。、；：！？…—·”’）》"
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
    text = text_by_order[order]
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

# 短行（如「而是：」只有 0.19s 配音）按语速兜底后仍只有一两帧可读时间，会闪一下。
# 向两侧已有的静音间隙借时间：先向右（读完要停留），不够再向左，且永不压到邻条。
MIN_CAPTION = float(os.environ.get("MIN_CAPTION_SECONDS", "1.05"))
_gap = 0.06


def widen_short(items, min_dur):
    it = [list(x) for x in items]
    changed = []
    for i, row in enumerate(it):
        span = row[3] - row[2]
        if span >= min_dur:
            continue
        need = min_dur - span
        room_r = (it[i + 1][2] - row[3]) if i + 1 < len(it) else max(0.0, body_dur - row[3])
        take = min(max(room_r - _gap, 0.0), need)
        row[3] += take
        need -= take
        if need > 0 and i > 0:
            room_l = row[2] - it[i - 1][3]
            take = min(max(room_l - _gap, 0.0), need)
            row[2] -= take
        changed.append((row[0], span, row[3] - row[2]))
    return [tuple(x) for x in it], changed


caption_items, widened = widen_short(caption_items, MIN_CAPTION)
for o, before, after in widened:
    print(f"[caps] order {o:>3} 上屏 {before:.2f}s → {after:.2f}s（借静音间隙，最短 {MIN_CAPTION:.2f}s）")

track = [{"index": i, "order": o, "text": t, "start": round(s, 3), "end": round(e, 3)}
         for i, (o, t, s, e) in enumerate(caption_items)]
with open(os.path.join(TMP, "caption-track.json"), "w", encoding="utf-8") as f:
    json.dump(track, f, ensure_ascii=False, indent=1)
if "--caps-only" in sys.argv:      # 只做字幕切条预检，不进入重合成
    for item in track:
        print(f"  {item['start']:7.2f}-{item['end']:7.2f} [{item['order']:>3}] {item['text']}")
    sys.exit(0)


# ------------------------------- 2. 正文背景：亚像素缓推 + 交叉溶解（单趟合成）
def ease_in_out(u):
    return 0.5 - 0.5 * math.cos(math.pi * min(1.0, max(0.0, u)))


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
for name in layer_names:
    src = Image.open(os.path.join(IMG, f"{name}.png")).convert("RGB")
    iw, ih = src.size
    covers[name] = (src, max(W / iw, H / ih))


def zoom_range(cover):
    """慢推幅度按源图分辨率自适应。

    cover = max(W/iw, H/ih)，源图相对画幅越大它越小，重采样余量 1/cover 越大。
    本期 atmosphere-1/2/8 是上一轮留下的 720x960（cover=1.000，余量 1.00）：
    再按共享模板的 1.020→1.062 推就等于把画面放大 6% 重采样，尾段会明显发糊，
    所以只做 1.006→1.042、全程基本 1:1 采样。1024x1365 的 6 张余量 1.42，用模板原幅度。
    （注意：共享/《小王子》脚本里那句 `cover > 1.15` 与注释方向相反——cover 大代表源图
    比画幅**小**，所以那边实际上所有图都走了轻微档。这里按余量判断，别照抄。）
    """
    if 1.0 / max(cover, 1e-6) >= 1.15:
        return ZOOM_START, ZOOM_END
    return 1.006, 1.042


zooms = {name: zoom_range(covers[name][1]) for name in layer_names}
for name in layer_names:
    src, cover = covers[name]
    print(f"[img] {name:16} {src.size[0]}x{src.size[1]} cover={cover:.3f} "
          f"余量={1 / cover:.2f} zoom={zooms[name][0]:.3f}→{zooms[name][1]:.3f}")


def layer_frame(k, t):
    src, cover = covers[layer_names[k]]
    if k == 0:
        return zoom_frame(src, cover * BRIDGE_ZOOM)
    z0, z1 = zooms[layer_names[k]]
    window = appear_times[k + 1] - appear_times[k]
    u = (t - appear_times[k]) / window if window > 0 else 1.0
    return zoom_frame(src, cover * (z0 + (z1 - z0) * ease_in_out(u)))


# body-bg.mp4 只有在「所有输入都没变」时才可复用：切镜表、推镜档位、水波窗口任一变化都必须
# 重合成。原来只比时长，加完涟漪仍会静默拿到没有涟漪的旧背景，而验收照过（正是「有声无波」
# 的复发路径）。签名落 body-bg.json，与共享渲染器同一套做法。
_bg_sig = {
    "body_dur": round(body_dur, 3), "fps": FPS, "layers": layer_names,
    "appear": [round(x, 3) for x in appear], "xfades": xfades,
    "bridge_zoom": BRIDGE_ZOOM,
    "zooms": {k: [round(v[0], 5), round(v[1], 5)] for k, v in zooms.items()},
    "water_frame0": WATER_FRAME0, "water_nframes": WATER_NFRAMES,
}
_bg_marker = os.path.join(TMP, "body-bg.json")


def _bg_reusable():
    if not (os.path.exists(bg_path) and abs(probe_dur(bg_path) - body_dur) < 0.2):
        return False
    try:
        return json.load(open(_bg_marker, encoding="utf-8")) == _bg_sig
    except Exception:
        return False


if _bg_reusable() and os.environ.get("FORCE_BG") != "1":
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
def _shadow_layers(sil):
    """宽而软的落影 + 紧贴笔画的窄描边（与共享渲染器同一套双通道）。

    单层 GaussianBlur(9) 在高亮低对比的氛围图上会把白字糊掉：本期 atmosphere-5
    书名带亮度 154/对比 24.8、atmosphere-7 113/29，都属于这类。双通道仍是「白字黑投影」，
    不引入卡片底框，暗背景观感不变。
    """
    return (sil.filter(ImageFilter.GaussianBlur(14)),
            sil.filter(ImageFilter.GaussianBlur(3)))


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
        sd.text((x, top + 7), ch, font=font, fill=(0, 0, 0, 255))
        x += cw + spacing
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    x = x0
    for ch, cw in zip(text, widths):
        ld.text((x, top), ch, font=font, fill=(255, 255, 255, 255))
        x += cw + spacing
    for sl in _shadow_layers(shadow):
        im.alpha_composite(sl)
    im.alpha_composite(layer)


title_im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
spaced_text(title_im, TITLE_TEXT, ImageFont.truetype(FONT, TITLE_SIZE), TITLE_TOP, 0.04)
spaced_text(title_im, AUTHOR_TEXT, ImageFont.truetype(FONT, AUTHOR_SIZE), AUTHOR_TOP, 0.06)
title_png = os.path.join(TMP, "title.png")
title_im.save(title_png)


def caption_lines(text):
    """script-body.csv 用空格分隔视觉行（md-to-sentence-csv.py 已按词边界保证每行 ≤12 字），
    所以优先按空格断行；无空格时才退回「靠近中点的标点处折行」。"""
    body = text.strip()
    parts = [p for p in body.split(" ") if p]
    if len(parts) == 2:
        return parts
    if len(parts) > 2:
        target = sum(len(p) for p in parts) / 2
        left, acc = [], 0
        for i, p in enumerate(parts):
            if left and (acc + len(p) > target or i == len(parts) - 1):
                return [" ".join(left), " ".join(parts[i:])]
            left.append(p)
            acc += len(p)
    if len(body) <= SMALL_CHARS:
        return [body]
    if len(body) <= 2 * SMALL_CHARS + 2:
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
    lines = [ln.rstrip(TRAILING_PUNCT) for ln in caption_lines(text)]
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
        ImageDraw.Draw(sh).text((W // 2, y + 7), ln, font=font, fill=(0, 0, 0, 255), anchor="ma")
        for sl in _shadow_layers(sh):        # 与书名层同一套双通道投影
            im.alpha_composite(sl)
        d.text((W // 2, y), ln, font=font, fill=(255, 255, 255, 255), anchor="ma")
    im.save(path)
    return lines          # 真正画到画面上的行——写进字幕轨 display 字段供标点上屏门禁核对


transparent = os.path.join(TMP, "transparent.png")
Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(transparent)

# 字幕轨必须按「绝对帧号」锚定：逐条累加取整会把每条 ≤0.5 帧的误差攒起来，
# 本期实测尾部滞后 213ms / 6 帧（见 project memory「字幕帧累加错位」）。
target_frames = round(body_dur * FPS)


def frame_of(sec):
    return max(0, min(target_frames, round(sec * FPS)))


entries = []
cursor = 0
# 清掉上一版的 cap-*.png：字幕重切后同一 index 会对应不同 order，旧文件留在 TMP 里会被
# qc-caption-alignment 当候选，实测报出成片字幕错配的假 MISMATCH（86/212 条）。
for stale in os.listdir(TMP):
    if stale.startswith("cap-") and stale.endswith(".png"):
        os.remove(os.path.join(TMP, stale))
drawn = {}
for i, (order, text, s, e) in enumerate(caption_items):
    f0, f1 = frame_of(s), frame_of(e)
    if f1 <= cursor:                       # 整条已被上一条吃掉，跳过
        continue
    f0 = max(f0, cursor)
    if f1 <= f0:
        continue
    if f0 > cursor:
        entries.append((f0 - cursor, transparent))
    p = os.path.join(TMP, f"cap-{i:03d}-{order:03d}.png")
    drawn[i] = (os.path.basename(p), build_caption(text, p))
    entries.append((f1 - f0, p))
    cursor = f1
if cursor < target_frames:
    entries.append((target_frames - cursor, transparent))
n_frames = sum(n for n, _ in entries)
assert n_frames == target_frames, f"字幕轨 {n_frames} 帧 != 目标 {target_frames} 帧"
print(f"[caps] 字幕轨 {n_frames} 帧 = {n_frames / FPS:.2f}s（绝对帧号锚定）")

# 字幕轨是「画面上到底上了什么字」的唯一证据：TMP 会被清理，所以在 episodes/<书名>/ 也
# 持久落一份，供 check-caption-punctuation.py / --all 审计读取。
# display 记录真正绘制的行（剥完上屏标点），index/order 与 cap-*.png 文件名一一对应。
track = [dict(row, display=" / ".join(drawn[row["index"]][1]))
         for row in track if row["index"] in drawn]
for row in track:
    row["file"] = drawn[row["index"]][0]
for path in (os.path.join(TMP, "caption-track.json"),
             os.path.join(EP, "caption-track.json")):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(track, f, ensure_ascii=False, indent=1)

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
candidate = os.path.join(TMP, "人性的枷锁-final.mp4")
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

# 水波同窗门禁（常驻规则，见 AGENTS.md「Visual And Audio Rules」）：必须卡在候选激活到
# renders/ 之前。「只加水声不加涟漪」是唯一一个分辨率/时长/音频流/字幕/音画对齐全部照常
# 通过的缺陷——成片看起来完全正常，只有这道量化门禁拦得住。门禁自己 import lib 的
# WATER_OFFSET / WATER_DUR，所以这里只传切点与帧率，不传任何秒数。
_water_gate = subprocess.run(
    [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "check-water-ripple.py"),
     candidate, "--intro-trim", str(INTRO_TRIM), "--fps", str(FPS)],
    capture_output=True, text=True)
print(_water_gate.stdout.strip())
if _water_gate.returncode != 0:
    raise SystemExit(
        "水波同窗门禁未通过，候选不激活到 renders/："
        "水声与可见涟漪必须落在同一帧窗口。查背景层是否调了 lib 的 ripple()、"
        "混音是否引了 assets/sfx/water.mp3。已叠字幕的成片不能事后补波（会扭糊字幕），"
        "必须重渲正文背景（FORCE_BG=1）。\n" + (_water_gate.stderr or "")[-500:])

out_final = os.path.join(RENDERS, "人性的枷锁-final.mp4")
shutil.move(candidate, out_final)


# --------------------------------------------------------- 6. 封面（1080x1920 竖版）
cover_out = os.path.join(RENDERS, "封面-人性的枷锁.png")
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
d.text((540, 250), "《人性的枷锁》", font=ImageFont.truetype(FONT, 88),
       fill=(255, 255, 255, 255), stroke_width=4, stroke=(0, 0, 0, 235), anchor="ma")
d.text((540, 400), "毛姆 / 著", font=ImageFont.truetype(FONT, 40),
       fill=(240, 240, 240, 240), stroke_width=2, stroke=(0, 0, 0, 215), anchor="ma")
d.text((540, 1780), "读书视频 · 文学经典系列", font=ImageFont.truetype(FONT, 30),
       fill=(225, 225, 225, 210), anchor="ma")
cover.convert("RGB").save(cover_out)
print(f"[cover] {cover_out}")
print(f"\n✅ 成片: {out_final}  ({dur:.2f}s)")
