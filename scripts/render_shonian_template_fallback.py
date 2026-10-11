#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""《少年》共享模板样式兜底渲染（ffmpeg + Pillow 浮点合成，不依赖 HyperFrames 正文会话）。

背景：HyperFrames 0.7.33 在本机对 432s 正文的单次会话渲染稳定崩溃
（Page.captureScreenshot: Target closed），分段渲染又因运行时只认「创作时」写入的字幕
时间（事后 shiftChildren 被忽略）而无法复用；故按 templates/shared-video-template
的视觉与混音规格做浮点合成复刻：

  · 片头复用 HyperFrames 渲染成功的玻璃碎片片头（tmp/preview-book-5addc4e438），
    裁到结果页定格 3.0s；片头结果页与正文首帧同为 result-bridge.png，接缝无闪屏
  · 正文 result-bridge 起幅 0.32s → 6 张氛围图按叙事段落切换，0.64/0.72s 交叉溶解
  · 运镜用 PIL.Image.transform 的浮点仿射矩阵做居中缓推（1.020→1.062，缓入缓出），
    不使用 ffmpeg zoompan：zoompan 会把 x/y/裁剪宽高取整到源图整数像素，
    1024x1260 源图上 1px ≈ 0.7 输出像素，慢镜头里就是持续抖动（《道林》已踩过的坑）。
    也不加斜向漂移，只做居中缓推。
  · 常驻《少年》+「[俄]费奥多尔·陀思妥耶夫斯基 / 著」（top 70/166，82/34px，900 字重，
    字号与 top 取自本书片头 .target .page-title / .page-author，保证与定格画面对齐）
  · 字幕白字黑投影（bottom 126px，56/50px，无底板），文本以 script-body.csv 为准；
    script-body.csv 每行是若干以空格分隔的子句，按子句打包成 ≤2 行、每行 ≤13 字的字幕条，
    并把条间切换点吸附到配音真实停顿上。
  · 混音参数与 scripts/render-episode-final.mjs 一致（BGM 0.32 / loudnorm -14 LUFS）

产物：episodes/少年/renders/少年-final.mp4 + 封面-少年.png
幂等可重建：重跑即覆盖（候选文件先落 tmp，技术验收通过后才激活）。

用法：
  python3 scripts/render_shonian_template_fallback.py            # 全量渲染
  python3 scripts/render_shonian_template_fallback.py --caps-only # 只预检字幕切条
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
EP = os.path.join(ROOT, "episodes", "少年")
IMG = os.path.join(EP, "images")
AUDIO = os.path.join(EP, "audio")
RENDERS = os.path.join(EP, "renders")
ASSETS = os.path.join(ROOT, "assets")
PREVIEW = os.path.join(ROOT, "tmp", "preview-book-5addc4e438")
INTRO_SOURCE = os.path.join(PREVIEW, "intro", "renders", "intro.mp4")
TMP = os.environ.get("FALLBACK_TMP", os.path.join(ROOT, "tmp", "shonian_tpl"))
os.makedirs(TMP, exist_ok=True)
os.makedirs(RENDERS, exist_ok=True)

FPS = 30
W, H = 720, 960
FONT = "/System/Library/Fonts/STHeiti Medium.ttc"
TITLE_TEXT = "《少年》"
AUTHOR_TEXT = "[俄]费奥多尔·陀思妥耶夫斯基 / 著"
TITLE_SIZE = 82      # 片头 .target .page-title: font-size 82px
TITLE_TOP = 70
AUTHOR_SIZE = 34     # 片头 .target .page-author: font-size 34px
AUTHOR_TOP = 166

# 片头裁切点：共享片头合成时长 4.27s，其中 ~2.62s 起结果页（桥接图 + 书名/作者）稳定定格，
# 切在这里可让片头末帧与正文首帧同为桥接图，消除黑场硬切的闪屏接缝（与已交付系列片对齐）。
INTRO_TRIM = float(os.environ.get("INTRO_TRIM_SECONDS", "3.0"))
INTRO_OFFSET_MS = round(INTRO_TRIM * 1000)
BGM_VOL = 0.32
SCROLL_START, SCROLL_END = 1.08, 2.38
# 水波「音效—画面」同窗常量的唯一真源：scripts/lib/water-ripple.json → lib/water_ripple.py。
# 这里只 import，绝不另抄秒数：写死 0.32 后一旦 lib 改动，就会变成「有水声没水波」
# 或「水波比声音早半秒」这种没人报错的错窗（见 AGENTS.md「Visual And Audio Rules」）。
sys.path.insert(0, os.path.join(ROOT, "scripts", "lib"))
from water_ripple import WATER_DUR, WATER_OFFSET, ripple, water_u  # noqa: E402
# 字幕上屏标点策略的唯一真源是 scripts/lib/subtitle-punctuation.json，Python 侧入口是
# scripts/lib/caption_display.py（JS 侧是 lib/subtitle-display.mjs）。本脚本早期自带一份
# SENTENCE_PUNCT 只剥行末点号，属于 AGENTS.md 点名的「手抄表」反例：行中间残留的断点号
# 会照样上屏。现在两条渲染路径共用同一份实现，门禁才有的可判。
from caption_display import MAX_LINES as POLICY_MAX_LINES  # noqa: E402
from caption_display import caption_style, display_lines  # noqa: E402

WATER_START = round(INTRO_TRIM + WATER_OFFSET, 2)   # 成片时间轴上的水波起点（秒）
WATER_NFRAMES = round(WATER_DUR * FPS)              # 涟漪帧数
# 正文背景第 0 帧对应成片 INTRO_TRIM，所以背景循环里的起点帧直接按偏移秒数换算
WATER_FRAME0 = round(WATER_OFFSET * FPS)

BRIDGE_IN = 0.32       # 桥接图独立停留时长，之后与场景 1 交叉溶解
XFADE_FIRST = 0.64     # 桥接图 → 场景 1
XFADE = 0.72           # 场景之间
ZOOM_START = 1.020
ZOOM_END = 1.062

# 片头结果页的桥接图不是静止的：intro/index.html 里
#   tl.to(".target .page-bg", { scale: 1.035, duration: 1.87, ease: "sine.inOut" }, 2.38)
# 所以裁切点 INTRO_TRIM 那一帧的缩放只走到 1.035 的一部分。正文首帧必须用同一时刻的
# 缩放值，否则接缝处会有一次 ~2.6%（约 19px）的跳变。INTRO_TRIM=3.0 → 1.0086。
INTRO_BG_ANIM_START = 2.38
INTRO_BG_ANIM_DUR = 1.87
INTRO_BG_ANIM_SCALE = 1.035


def _bridge_zoom():
    u = (INTRO_TRIM - INTRO_BG_ANIM_START) / INTRO_BG_ANIM_DUR
    u = min(1.0, max(0.0, u))
    eased = 0.5 - 0.5 * math.cos(math.pi * u)      # GSAP sine.inOut
    return 1.0 + (INTRO_BG_ANIM_SCALE - 1.0) * eased


BRIDGE_ZOOM = _bridge_zoom()   # 桥接图保持静止，与片头定格画幅逐像素对齐
MAX_UNIT = 24          # 单条字幕最多约两行、每行 13 字
MAX_LINE = 13          # 单行最大字数（56/50px 下 13 字仍落在 720-68 安全区内）
SMALL_CHARS = 12

# 叙事段落 → 氛围图（113 条 order 全覆盖，图片编号与溯源见 prompts.csv / data/少年_pexels_ids.json）
# 2026-09-27：氛围图整体从内置 AI 出图换成 Pexels 实拍（AGENTS.md「Visual And Audio Rules」）。
# 实拍图按段落给足 8 张，取消原来「牌桌/提灯各用两次」的复用，切点处不再出现回环同图。
SCENE_PLAN = [
    ("atmosphere-1", 1, 7),     # 雪夜暗巷撑伞独行：真正痛苦的是不知道自己是谁
    ("atmosphere-2", 8, 13),    # 阴冷的老石拱廊：夹在贵族与普通人两个世界之间
    ("atmosphere-3", 14, 29),   # 黑里悬着的水晶吊灯：「我要成为罗斯柴尔德」
    ("atmosphere-4", 30, 39),   # 烛光下摊开的纸牌：越想独立越被情感左右，父子间的爱与恨
    ("atmosphere-5", 40, 49),   # 风雪里的提灯：重新认识父母，看见局限仍自己负责
    ("atmosphere-6", 50, 72),   # 木板房间的窄床：财富未必自由，人如何成为自己
    ("atmosphere-7", 73, 95),   # 烛光下的金币与纸币：把人生过成一场永不停歇的自我证明
    ("atmosphere-8", 96, 113),  # 暮色雪原上一行脚印：没有标准答案，少年开始长大
]

# scene-map.json 是图片门禁（check-image-provenance.py）的判定对象，SCENE_PLAN 是实际渲染对象。
# 两者一旦漂移，门禁绿灯就只是在为另一个画面背书，所以在这里逐层比对，不一致直接不出片。
_SCENE_MAP_PATH = os.path.join(EP, "scene-map.json")
if os.path.exists(_SCENE_MAP_PATH):
    _sm = json.load(open(_SCENE_MAP_PATH, encoding="utf-8")).get("layers") or []
    _sm_body = [(c["name"], int(c["order_start"]), int(c["order_end"])) for c in _sm[1:]]
    if _sm_body != [tuple(x) for x in SCENE_PLAN]:
        raise SystemExit(
            "scene-map.json 与 SCENE_PLAN 不一致：\n"
            f"  scene-map: {_sm_body}\n  SCENE_PLAN: {SCENE_PLAN}\n"
            "改分段要同时改两处（scene-map 是门禁的判定对象）。")
else:
    print("⚠️ 没有 scene-map.json：图片门禁只能按兼容模式跑，建议补齐")


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

# 低置信时间兜底：ASR 对齐偶有行尾锚点过早（如 order 1 书名只有 0.49s），
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


# ------------------------------ 1. 长行切条：按子句打包 + 停顿吸附
def silencedetect(path, noise="-35dB", min_dur=0.18):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-af",
                        f"silencedetect=noise={noise}:d={min_dur}", "-f", "null", "-"],
                       capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start:\s*([-\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([-\d.]+)", r.stderr)]
    return [(s + e) / 2 for s, e in zip(starts, ends) if e > s]


pauses = silencedetect(os.path.join(AUDIO, "body-voiceover.mp3"))
# 行末只剥句点，闭合符号（” ’ ） 》）必须保留：否则「“我要有钱”」会上屏成没有下引号的孤引号
SENTENCE_PUNCT = "，。、；：！？…—·,;:!?"
CLOSING_MARKS = "”’）》)"
# 音译专名：jieba 默认词典会把「维尔西洛夫」切成「维尔/西洛夫」，字幕劈开人名最难看
PROPER_NOUNS = ["陀思妥耶夫斯基", "阿尔卡季", "维尔西洛夫", "马卡尔", "罗斯柴尔德", "涅瓦河"]
# 单字虚词/代词：不能成为第二行行首（「赚到/了曾经…」「不会给/你一套…」这类断法）
LEADING_PARTICLES = set("了着过地的吗呢吧呀啊哦嗯且之其此该这那们你")
OPEN_QUOTES = "“‘《（("
CLOSE_QUOTES = "”’》）)"


def _init_jieba():
    jieba.setLogLevel(60)          # 静音 prefix dict 加载日志
    for w in PROPER_NOUNS:
        jieba.add_word(w, freq=10000)


_init_jieba()


def _quote_spans(clause):
    """返回被引号/书名号包住的位置区间，切点不能落在其中。"""
    spans, stack = [], []
    for i, ch in enumerate(clause):
        if ch in OPEN_QUOTES:
            stack.append(i)
        elif ch in CLOSE_QUOTES and stack:
            spans.append((stack.pop(), i))
    return spans + [(s, len(clause) - 1) for s in stack]


def jieba_split(clause):
    """无空格/标点的超长子句：用 jieba 找词边界，切成尽量均衡且 ≤MAX_LINE 的两行。

    候选切点要排除：引号内部、行首是单字虚词、行尾是开引号。
    """
    words = [w for w in jieba.cut(clause) if w.strip()]
    n = len(clause)
    bounds, acc = [], 0
    for w in words[:-1]:
        acc += len(w)
        bounds.append(acc)
    spans = _quote_spans(clause)

    def ok(b):
        if clause[b - 1] in OPEN_QUOTES or clause[b] in CLOSE_QUOTES:
            return False
        if clause[b] in LEADING_PARTICLES:
            return False
        return not any(lo < b <= hi for lo, hi in spans)

    legal = [b for b in bounds if ok(b)]
    fit = [b for b in legal if b <= MAX_LINE and n - b <= MAX_LINE]
    pool = fit or legal or [b for b in bounds if b <= MAX_LINE and n - b <= MAX_LINE] or bounds
    if not pool:
        return [clause[:MAX_LINE], clause[MAX_LINE:]]
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
    span = end - start
    total_chars = sum(len(u.replace(" ", "")) for u in units) or 1
    min_keep = span / (n * 1.8)          # 单条最短时长，防止吸附后某条被挤没
    bounds = [start]
    acc = 0
    for u in units[:-1]:
        acc += len(u.replace(" ", ""))
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
# body-bg 只有在「输入全部未变」时才可复用：时长、切镜表、水波窗口任一变化都必须重合成，
# 否则改了配图分段或加了涟漪却静默拿到旧背景，验收还全绿。标记落 body-bg.json。
_bg_sig = {"body_dur": round(body_dur, 3), "fps": FPS,
           "layers": layer_names, "layer_start": [round(v, 3) for v in appear],
           "water_frame0": WATER_FRAME0, "water_nframes": WATER_NFRAMES}
_bg_marker = os.path.join(TMP, "body-bg.json")


def _bg_reusable():
    if not (os.path.exists(bg_path) and abs(probe_dur(bg_path) - body_dur) < 0.2):
        return False
    try:
        return json.load(open(_bg_marker, encoding="utf-8")) == _bg_sig
    except Exception:
        return False


covers = {}
for name in set(layer_names):
    src = Image.open(os.path.join(IMG, f"{name}.png")).convert("RGB")
    iw, ih = src.size
    covers[name] = (src, max(W / iw, H / ih))


def layer_frame(k, t):
    src, cover = covers[layer_names[k]]
    if k == 0:
        return zoom_frame(src, cover * BRIDGE_ZOOM)
    window = appear_times[k + 1] - appear_times[k]
    u = (t - appear_times[k]) / window if window > 0 else 1.0
    return zoom_frame(src, cover * (ZOOM_START + (ZOOM_END - ZOOM_START) * ease_in_out(u)))


if os.environ.get("FORCE_BG") != "1" and _bg_reusable():
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


def strip_clause_tail(p):
    """剥行末句点，但把成对的闭合符号（” ’ ） 》）留在原位。

    旧写法 p.rstrip(TRAILING_PUNCT) 会把「我们说“我要有钱”」的下引号一起吃掉，
    屏幕上只剩一个开引号，等于改动了 script-body.csv 的字面真源。
    """
    closers = ""
    while p and p[-1] in CLOSING_MARKS:
        closers = p[-1] + closers
        p = p[:-1]
    while p and p[-1] in SENTENCE_PUNCT:
        p = p[:-1]
    return p + closers


def caption_lines(text):
    """一个字幕条 = 1~POLICY_MAX_LINES 个视觉行；空格即换行（子句边界，jieba 词边界安全）。

    行内容再过一遍共享策略 `display_lines()`：行末点号被剥掉，行中间万一还留着断点号
    （script-body.csv 改过、pack_units 没切开）就按策略换成换行，其余符号消失，行内零空白。
    本期 script-body.csv 实测 0 处行中断点号，所以这一步不改变任何一行的像素；
    它保证的是「以后有人往 script-body.csv 里加了一句带、的长行，屏幕上不会留下那个、」。
    """
    parts = [strip_clause_tail(p) for p in text.split(" ") if p.strip()]
    parts = [p for p in parts if p]
    if len(parts) < 2:
        body = strip_clause_tail(parts[0] if parts else text)
        parts = [body] if len(body) <= MAX_LINE else jieba_split(body)
    lines = [ln for p in parts for ln in display_lines(p)]
    lines = [ln for ln in lines if ln]
    if len(lines) > POLICY_MAX_LINES:
        raise SystemExit(
            f"字幕条「{text}」按标点策略需要 {len(lines)} 个视觉行，超过 displayMaxLines="
            f"{POLICY_MAX_LINES}。按 AGENTS.md 不能缩字号也不能退回空格：请在 pack_units 里把它"
            "拆成前后衔接的两条字幕（每条自带时间），再重渲。")
    return lines or display_lines(text)


def build_caption(text, path):
    """画一条字幕，并返回真正上屏的那几行——字幕轨的 display 字段必须由它产出。

    门禁判的是「屏幕上有没有标点和空格间隙」，所以 display 必须是**画图这一步**的结果，
    不能拿 script-body.csv 原文去猜：猜出来的轨会让 check-caption-punctuation.py 变成空转。
    """
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
    return lines


transparent = os.path.join(TMP, "transparent.png")
Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(transparent)

# 字幕轨按「绝对帧号」锚定：每条字幕的起止各自 round(s*FPS)，而不是把时长累加。
# 累加写法会让每条字幕最多丢 0.5 帧、每个间隙再丢 0.5 帧，152 条下来系统性晚 0.1~0.2s，
# 短字幕（<0.6s）会被整条挤掉——正是「音画错位」那类坑。
entries = []
prev_frame = 0
target_frames = round(body_dur * FPS)
for i, (order, text, s, e) in enumerate(caption_items):
    f0 = min(max(prev_frame, round(s * FPS)), target_frames)
    f1 = min(max(f0 + 1, round(e * FPS)), target_frames)
    if f0 > prev_frame:
        entries.append((f0 - prev_frame, transparent))
    p = os.path.join(TMP, f"cap-{i:03d}-{order:03d}.png")
    # index 必须落进轨里：check-caption-punctuation.py 靠 cap-<index>-<order>.png 反查
    # 真正上屏的那张 PNG，缺 index 时它只能退回读 display 文本，等于没核对画面。
    track[i]["index"] = i
    track[i]["display"] = " / ".join(build_caption(text, p))
    entries.append((f1 - f0, p))
    prev_frame = f1
if prev_frame < target_frames:
    entries.append((target_frames - prev_frame, transparent))
_cap_style = caption_style()        # 「line-break@<版本>」：门禁据此判定这期按换行口径渲过
for _row in track:
    _row["captionStyle"] = _cap_style
with open(os.path.join(TMP, "caption-track.json"), "w", encoding="utf-8") as f:
    json.dump(track, f, ensure_ascii=False, indent=1)
# 字幕轨是「画面上到底上了什么字」的唯一证据，而 TMP 会被清，所以本期目录留一份。
with open(os.path.join(EP, "caption-track.json"), "w", encoding="utf-8") as f:
    json.dump(track, f, ensure_ascii=False, indent=1)
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
candidate = os.path.join(TMP, "少年-final.mp4")
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
out_final = os.path.join(RENDERS, "少年-final.mp4")

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
cover_out = os.path.join(RENDERS, "封面-少年.png")
cover = Image.open(os.path.join(IMG, "atmosphere-1.png")).convert("RGB")
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
d.text((540, 250), "《少年》", font=ImageFont.truetype(FONT, 96),
       fill=(255, 255, 255, 255), stroke_width=4, stroke=(0, 0, 0, 235), anchor="ma")
d.text((540, 400), "[俄]费奥多尔·陀思妥耶夫斯基 / 著", font=ImageFont.truetype(FONT, 38),
       fill=(240, 240, 240, 240), stroke_width=2, stroke=(0, 0, 0, 215), anchor="ma")
d.text((540, 1780), "读书视频 · 文学经典系列", font=ImageFont.truetype(FONT, 30),
       fill=(225, 225, 225, 210), anchor="ma")
cover.convert("RGB").save(cover_out)
print(f"[cover] {cover_out}")
print(f"\n✅ 成片: {out_final}  ({dur:.2f}s)")
