#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
《少年维特的烦恼》共享模板样式兜底渲染（ffmpeg + Pillow，不依赖 HyperFrames 正文渲染）。

为什么不走 HyperFrames 正文渲染，也不用旧的 scripts/render_witte.py：
  · HyperFrames 0.7.33 对 250s+ 正文的单次会话渲染在本机稳定崩溃
    （Page.captureScreenshot: Target closed），分段渲染的时间偏移又不被运行时承认
    （见《霍比特人》《白鲸》的音画错位教训）。
  · render_witte.py 用 ffmpeg zoompan 做 Ken Burns，且带 ±40px 斜向漂移：
    zoompan 的 x/y/裁剪宽高会取整到源图整数像素，源图只有 1024x1365，
    1 像素 ≈ 0.7 输出像素，慢镜头里就是用户明确反馈过的「画面晃、看着不舒服」。
    它的字幕还带半透明圆角底衬（卡片 UI），违反 AGENTS.md 的视觉规则。

本脚本对齐已验收的 render_baijing_template_fallback.py / render_dorian_template_fallback.py：
  · 片头复用本期 HyperFrames 渲染的 intro.mp4（玻璃碎片片头），裁 3.0s
  · 正文背景：按文案叙事段分配 7 张图（每张 11–37s），0.64s 交叉溶解，
    居中缓推 1.020→1.062 用 Image.transform 浮点仿射合成，无整数抖动、无斜向漂移
  · 常驻《少年维特的烦恼》+「[德]约翰·沃尔夫冈·冯·歌德 / 著」（top 70/140，60/34px）
  · 字幕白字黑投影（bottom 126px，56/50px，无卡片底框），文本以 script-body.csv 为准
  · 长句按标点切成 ≤12 字的显示分句，用 word-timings.json 的逐字时间轴定起止，
    避免 57 字长行被截断或挤出画面
  · 混音参数与 scripts/render-episode-final.mjs 一致（BGM 0.32 / loudnorm -14 LUFS）

产物：episodes/少年维特的烦恼/renders/少年维特的烦恼-final.mp4
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
EPISODE = os.environ.get("EPISODE", "少年维特的烦恼")
EP = os.path.join(ROOT, "episodes", EPISODE)
IMG = os.path.join(EP, "images")
AUDIO = os.path.join(EP, "audio")
RENDERS = os.path.join(EP, "renders")
ASSETS = os.path.join(ROOT, "assets")
PREVIEW = os.environ.get(
    "PREVIEW", os.path.join(ROOT, "tmp", "preview-book-c324765b3f"))
INTRO_SOURCE = os.environ.get(
    "INTRO_SOURCE", os.path.join(PREVIEW, "intro", "renders", "intro.mp4"))
TMP = os.environ.get("FALLBACK_TMP", os.path.join(ROOT, "tmp", "werther_tpl"))
os.makedirs(TMP, exist_ok=True)
os.makedirs(RENDERS, exist_ok=True)

FPS = 30
W, H = 720, 960
FONT = "/System/Library/Fonts/STHeiti Medium.ttc"  # 共享模板在本机的实际回退字体

brief = json.load(open(os.path.join(EP, "brief.json"), encoding="utf-8"))
TITLE_TEXT = f"《{brief['display_title']}》"
AUTHOR_TEXT = f"{brief['author']} / 著"

# 片头裁切点：共享片头 4.27s，约 2.62s 起结果页（桥接图 + 书名/作者）稳定定格，
# 与已交付的《白鲸》《道林·格雷的画像》对齐取 3.0s。
INTRO_TRIM = float(os.environ.get("INTRO_TRIM_SECONDS", "3.0"))
INTRO_OFFSET_MS = round(INTRO_TRIM * 1000)
BGM_VOL = 0.32
SCROLL_START, SCROLL_END = 1.08, 2.38
XFADE = 0.64          # 场景交叉溶解时长
MAX_CONTENT = 12      # 单个显示分句的正文字数上限

# 两条常驻规则的载体都在 scripts/lib/，本脚本只调用、不自立字符表或秒数：
#   · 水声—水波同窗：assets/sfx/water.mp3 起播的那 WATER_DUR 秒，背景层必须出现外扩涟漪。
#     常数真源 scripts/lib/water-ripple.json，视觉实现 scripts/lib/water_ripple.py。
#   · 上屏字幕零标点、零空白：script-body.csv 保留标点（断句/折行靠它），只在画字那一刻按
#     scripts/lib/subtitle-punctuation.json 处理；句中断点号一律**换成换行**（旧口径的
#     全角空格 U+3000 已于 2026-09-27 废止），译名间隔号 · 保留。
#     一条字幕最多 MAX_LINES（真源 displayMaxLines）个视觉行，装不下的分句顺延成下一条
#     字幕，而不是缩字号或在屏上留间隙。
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from caption_display import (  # noqa: E402
    MAX_LINES,
    caption_style,
    display_lines,
    strip_display as display_text,
)
from water_ripple import WATER_DUR, WATER_OFFSET, ripple, water_u  # noqa: E402

WATER_START = round(INTRO_TRIM + WATER_OFFSET, 2)   # 成片时间轴上的水波起点（秒）
WATER_NFRAMES = round(WATER_DUR * FPS)              # 涟漪帧数
# 正文背景第 0 帧对应成片 INTRO_TRIM，所以背景循环里的起点是「相对切点的偏移秒」
WATER_FRAME0 = round(WATER_OFFSET * FPS)


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


# ---------------------------------------------------------------- 时间轴与文案
timings = json.load(open(os.path.join(AUDIO, "body-timings.json"), encoding="utf-8"))
caps = sorted(timings["captions"], key=lambda c: int(c["order"]))
body_dur = float(timings["duration"])
final_dur = round(INTRO_TRIM + body_dur, 2)

rows = list(csv.DictReader(open(os.path.join(EP, "script-body.csv"), encoding="utf-8-sig")))
text_by_order = {int(r["order"]): r["text"].strip() for r in rows}
row_time = {int(c["order"]): (float(c["start"]), float(c["end"])) for c in caps}

word = json.load(open(os.path.join(AUDIO, "word-timings.json"), encoding="utf-8"))

PUNCT = set("，。？！、；：，“”‘’《》（）…—·,.?!;:'\"()　 \t-")
BREAKS = set("，。？！、；：…—")


def content_chars(s):
    return [ch for ch in s if ch not in PUNCT]


def char_times(order):
    """把 script-body.csv 该行的正文字符逐个映射到 word-timings 的逐字时间轴。"""
    text = text_by_order[order]
    entries = word["byOrder"].get(str(order)) or []
    wc = [e for e in entries if e["ch"] not in PUNCT]
    tc = [ch for ch in text if ch not in PUNCT]
    if len(wc) != len(tc) or [e["ch"] for e in wc] != tc:
        raise SystemExit(f"order {order} 逐字时间轴与 script-body.csv 无法对齐，请重跑 align-word-timings")
    return {i: (float(e["start"]), float(e["end"])) for i, e in enumerate(wc)}


def protected_spans(clause):
    """引号/书名号内部不允许被切开。"""
    spans = []
    for opener, closer in (("“", "”"), ("‘", "’"), ("《", "》"), ("（", "）")):
        depth = None
        for i, ch in enumerate(clause):
            if ch == opener and depth is None:
                depth = i
            elif ch == closer and depth is not None:
                spans.append((depth, i))
                depth = None
    return spans


# 折行断点必须落在词边界上：只按字数均衡硬切会劈开「强烈」「青春」「未婚夫」这类
# 双音节词（本期实测 7 处），观众读到的是半截词。jieba 为可选依赖，未安装时退回
# 纯均衡切分，不影响渲染（与《荆棘鸟》同一策略）。
try:
    import jieba
except Exception:
    jieba = None

# 本书专名：默认词典会把「少年维特的烦恼」切成「少年 / 维特 / 的 / 烦恼」，
# 词边界反而把书名从中间放行；显式加词后切点才会绕开它。
if jieba is not None:
    for _w in ("少年维特的烦恼", "维特", "绿蒂", "阿尔伯特", "歌德", "情感逻辑"):
        jieba.add_word(_w, freq=100000)


def word_cut_bounds(clause):
    """该子句的合法断点集合（jieba 词边界位置）；jieba 缺失时返回 None 表示不约束。"""
    if jieba is None:
        return None
    bounds = {0, len(clause)}
    p = 0
    for tok in jieba.cut(clause):
        p += len(tok)
        bounds.add(p)
    return bounds


def split_long(clause, limit):
    """无标点的超长子句按字数均衡切分；切点避开引号/书名号内部和词中间，不改动原文。"""
    n = len(clause)
    parts = math.ceil(n / limit)
    spans = protected_spans(clause)
    bounds = word_cut_bounds(clause)
    out, pos = [], 0
    for p in range(parts - 1):
        target = round(n * (p + 1) / parts)
        rest = parts - p - 1
        # 切点 j 表示 clause[:j] | clause[j:]，落在 span 内部（含紧贴后引号）都算破坏成对符号
        cands = [j for j in range(pos + 1, n - rest + 1)
                 if not any(a < j <= b for a, b in spans)]
        if not cands:
            cands = [j for j in range(pos + 1, n - rest + 1)]
        # 先保证不劈词，再追求各条字数均衡；两者都满足才是既不劈词又不失衡
        def rank(j):
            splits_word = 0 if (bounds is None or j in bounds) else 1
            return (splits_word, abs(j - target), j)
        cut = min(cands, key=rank)
        out.append(clause[pos:cut])
        pos = cut
    out.append(clause[pos:])
    return out


def build_chunks(order):
    """一行旁白 -> 若干条按标点切分、带真实起止时间的显示分句。"""
    text = text_by_order[order]
    row_start, row_end = row_time[order]
    # 1) 按标点切子句，标点跟随前文
    clauses, cur = [], ""
    for ch in text:
        cur += ch
        if ch in BREAKS:
            clauses.append(cur)
            cur = ""
    if cur.strip():
        clauses.append(cur)
    # 2) 过长的子句再按字数均衡切
    atoms = []
    for cl in clauses:
        if len(content_chars(cl)) > MAX_CONTENT:
            atoms += split_long(cl.strip(), MAX_CONTENT)
        else:
            atoms.append(cl.strip())
    atoms = [a for a in atoms if content_chars(a)]
    if not atoms:
        return []
    # 3) 贪心合并相邻短子句：预算是**上屏行数**，不只是字数。
    #    断点号如今一律换成换行，一个分句在屏上就是一行，所以两条短句可以并、三条不能并。
    #    只按字数打包会重现旧缺陷：「她温柔，聪明，善良」共 9 字挤进一条，撑成 3 行顶穿
    #    字幕安全区。行数装不下的分句顺延成下一条字幕，而不是缩字号。
    merged = []
    for a in atoms:
        if merged:
            cand = merged[-1] + a
            if (len(content_chars(cand)) <= MAX_CONTENT
                    and len(display_lines(cand)) <= MAX_LINES):
                merged[-1] = cand
                continue
        merged.append(a)
    # 4) 用逐字时间轴定每条分句的起止
    ct = char_times(order)
    idx = 0
    out = []
    for m in merged:
        k = len(content_chars(m))
        s = ct[idx][0]
        e = ct[idx + k - 1][1]
        out.append({"order": order, "text": m, "start": s, "end": e})
        idx += k
    # 5) 停留到下一条开始（最长 0.5s），避免分句之间闪断
    for i, ch in enumerate(out):
        nxt = out[i + 1]["start"] if i + 1 < len(out) else row_end
        ch["show_end"] = min(max(ch["end"], nxt - 0.04), ch["end"] + 0.5)
    # 6) 极短分句并入前一条（同样受行数预算约束，并完超行就让它单独成条）
    fixed = []
    for ch in out:
        cand = fixed[-1]["text"] + ch["text"] if fixed else ""
        if fixed and (ch["show_end"] - ch["start"]) < 0.9 \
                and len(content_chars(cand)) <= MAX_CONTENT + 2 \
                and len(display_lines(cand)) <= MAX_LINES:
            fixed[-1]["text"] = cand
            fixed[-1]["show_end"] = ch["show_end"]
        else:
            fixed.append(dict(ch))
    for ch in fixed:
        n = len(display_lines(ch["text"]))
        if n > MAX_LINES:
            raise SystemExit(
                f"[lines] order {order} 一条字幕上屏 {n} 行 > displayMaxLines={MAX_LINES}："
                f"{ch['text']!r} → {display_lines(ch['text'])}\n"
                f"       打包的行数预算没兜住，就在 script-body.csv 里把这一行拆开。")
    return fixed


chunks = []
for c in caps:
    chunks += build_chunks(int(c["order"]))
chunks.sort(key=lambda x: (x["start"], x["order"]))
for ch in chunks:
    ch["display"] = display_text(ch["text"])
    # 门禁侧的视觉行编码用 " / " 分隔（check-caption-punctuation.py 的 display_lines
    # 就是按 " / " 切行的），画字侧仍用换行。两者分开存：把 "\n" 直接写进轨里，
    # 门禁会把整条当成一行、并把 U+000A 报成"空白间隙"。
    ch["track_display"] = " / ".join(display_lines(ch["display"]))

# 去标点只许动符号，不许吃字：按 order 把上屏文本拼回去，与 script-body.csv 逐字比对。
# 字符集与 scripts/check-caption-punctuation.py 的 core_chars 保持一致（汉字+扩展A区+字母数字+·）。
CORE_RE = re.compile(r"[㐀-鿿A-Za-z0-9·]")
by_order = {}
for ch in chunks:
    by_order.setdefault(ch["order"], []).append(ch["display"])
for order, parts in sorted(by_order.items()):
    src = "".join(CORE_RE.findall(text_by_order[order]))
    got = "".join(CORE_RE.findall("".join(parts)))
    if src != got:
        raise SystemExit(f"order {order} 分句/去标点吃掉或改动了正文：\n"
                         f"  CSV : {src}\n  上屏: {got}")

# captionStyle 是给门禁看的口径标记：check-caption-punctuation.py 靠它区分
# 「新口径（断点号→换行）」和「旧口径（断点号→全角空格）」的轨。缺了这个字段，
# 一条漏掉的全角空格只会被当成旧轨的既有间隙、只告警不判红，等于把门禁自己关掉了。
_cap_style = caption_style()
json.dump([{"index": i, "order": x["order"], "text": x["text"],
            "display": x["track_display"], "captionStyle": _cap_style,
            "start": round(x["start"], 3), "end": round(x["end"], 3),
            "show_end": round(x["show_end"], 3)}
           for i, x in enumerate(chunks)],
          open(os.path.join(TMP, "chunks.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print(f"[caps] {len(caps)} 行旁白 -> {len(chunks)} 条显示分句，"
      f"最长 {max(len(content_chars(x['text'])) for x in chunks)} 字（上屏已去标点）")
if os.environ.get("CHUNKS_ONLY"):
    raise SystemExit(0)

# ------------------------------------ 1. 正文背景：按叙事段配图 + 亚像素缓推 + 交叉溶解
# (配图, 起始行, 结束行)：result-bridge 用于片头衔接与结尾金句，
# 其余 6 张氛围图按《少年维特的烦恼》叙事弧线分配，每张都用到，相邻段不重复。
SCENE_RUNS = [
    ("result-bridge", 1, 1),      # 书名，与片头定格同图
    ("atmosphere-3", 2, 4),       # 共鸣开头：明知没有结果仍靠近（烛光下的信）
    ("atmosphere-1", 5, 7),       # 歌德写下这本书 / 维特来到乡间（夏日麦田菩提树）
    ("atmosphere-2", 8, 10),      # 遇到绿蒂：温柔聪明善良（切面包的暖光厨房）
    ("atmosphere-4", 11, 17),     # 阿尔伯特与现实的秩序（空旷华丽的厅堂）
    ("atmosphere-5", 18, 25),     # 越克制越想念，精神困境（冬雾枯树冰河）
    ("atmosphere-4", 26, 31),     # 现实不因深爱改变秩序（回到厅堂）
    ("atmosphere-6", 32, 38),     # 强烈不等于正确：成熟的转折（云海黎明）
    ("atmosphere-2", 39, 44),     # 人生不该只有爱情：工作朋友兴趣（日常静物）
    ("atmosphere-3", 45, 48),     # 歌德的提醒：可以有感情但不能失去自我
    ("atmosphere-5", 49, 52),     # 三个问题：得不到 / 差距 / 清醒（回到冬景）
    ("atmosphere-6", 53, 55),     # 真正的成长：继续向前走（回到黎明）
    ("result-bridge", 56, 56),    # 结尾金句
]
ZOOM_START = 1.020
ZOOM_END = 1.062
BRIDGE_ZOOM = 1.035   # 桥接图保持静止，与片头定格画幅对齐


def ease_in_out(u):
    return 0.5 - 0.5 * math.cos(math.pi * min(1.0, max(0.0, u)))


def zoom_frame(im, factor):
    """以图片中心为锚点，按浮点倍率取景到 720x960（无整数抖动）。"""
    iw, ih = im.size
    inv = 1.0 / factor
    return im.transform(
        (W, H), Image.AFFINE,
        (inv, 0.0, iw / 2.0 - W * inv / 2.0,
         0.0, inv, ih / 2.0 - H * inv / 2.0),
        resample=Image.BICUBIC)


# 段边界：本段起点 = 该段首行的旁白起点；交叉溶解在边界前 XFADE 秒完成
bounds = [0.0]
for i, (_, _, last) in enumerate(SCENE_RUNS[:-1]):
    bounds.append(row_time[SCENE_RUNS[i + 1][1]][0])
bounds.append(body_dur)
for i in range(1, len(bounds) - 1):
    if bounds[i] - bounds[i - 1] < XFADE * 2:
        raise SystemExit(f"第 {i} 段时长不足容纳交叉溶解，请检查 SCENE_RUNS")

layer_names = [n for n, _, _ in SCENE_RUNS]
layer_start = [0.0] + [b - XFADE for b in bounds[1:-1]]
layer_len = [(layer_start[k + 1] - layer_start[k]) + XFADE
             for k in range(len(layer_start) - 1)]
layer_len.append(body_dur - layer_start[-1])
print("[layers] " + " / ".join(
    f"{n}({bounds[k + 1] - bounds[k]:.1f}s)" for k, n in enumerate(layer_names)))

bg = os.path.join(TMP, "body-bg.mp4")
if os.path.exists(bg) and abs(probe_dur(bg) - body_dur) > 0.2:
    os.remove(bg)
    print("[bg] 旧背景时长不符，重新合成")


# ------------------------------------------- 2. 常驻书名层 + 字幕层（Pillow）
def _silhouette(widths, spacing, text, font, x0, y, drop):
    """把文字画成纯黑剪影，供多层高斯模糊当投影用。"""
    sil = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sil)
    x = x0
    for ch, cw in zip(text, widths):
        sd.text((x, y + drop), ch, font=font, fill=(0, 0, 0, 255))
        x += cw + spacing
    return sil


def _shadow_layers(sil):
    """宽而软的落影 + 紧贴笔画的窄描边：金色大厅、面包桌这类高亮氛围图上仍可读，
    暗背景上观感与单层投影一致，不引入卡片底框。"""
    return (sil.filter(ImageFilter.GaussianBlur(14)),
            sil.filter(ImageFilter.GaussianBlur(3)))


def _layout(text, font, spacing_em):
    widths = [font.getlength(ch) for ch in text]
    spacing = font.size * spacing_em
    total = sum(widths) + spacing * (len(text) - 1)
    return widths, spacing, (W - total) / 2


def spaced_text(im, text, font, top, spacing_em):
    """逐字排布模拟模板 letter-spacing，白字 + 黑投影（0 7px 18px rgba(0,0,0,.96) 的加强版）。"""
    widths, spacing, x0 = _layout(text, font, spacing_em)
    sil = _silhouette(widths, spacing, text, font, x0, top, 7)
    soft, tight = _shadow_layers(sil)
    im.alpha_composite(soft)
    im.alpha_composite(tight)
    ld = ImageDraw.Draw(im)
    x = x0
    for ch, cw in zip(text, widths):
        ld.text((x, top), ch, font=font, fill=(255, 255, 255, 255))
        x += cw + spacing


def caption_png(text, path):
    """单条上屏字幕：白字 + 高斯模糊黑投影，无卡片底框。

    `text` 必须是 **已按 lib/subtitle-punctuation.json 处理的上屏文本**（display_text 的结果，
    断点号已换成换行），字号与宽度都按屏上真正要画的那几行算。

    多行必须**逐行**排版、逐行落影：旧写法把整条字幕当成一串横排字符画剪影（换行符宽度算 0），
    白字却交给 PIL 的多行 anchor="ma" 去折，两套版式叠在一起——投影变成一条比正文宽得多的横带，
    在文字左右两侧露出鬼影，且多行块从 top 往下长，最后一行掉出字幕安全区。
    现在剪影和白字用同一组行坐标，整块按**行数**上移，保证底边仍落在 H-126。
    """
    lines = display_lines(text)
    if not lines:
        raise SystemExit(f"[punct] 上屏文本为空（标点剥离把整条剥没了）：{text!r}")
    gaps = {ch for ln in lines for ch in ln if ch.isspace()}
    if gaps:
        raise SystemExit(f"[gap] 上屏字幕仍有空白间隙（断点号必须换成换行）：{text!r} → {lines}")
    if len(lines) > MAX_LINES:
        raise SystemExit(f"[lines] 一条字幕上屏 {len(lines)} 行 > displayMaxLines={MAX_LINES}："
                         f"{text!r} → {lines}")
    size = 56 if max(len(ln) for ln in lines) <= MAX_CONTENT else 50
    font = ImageFont.truetype(FONT, size)
    while max(font.getlength(ln) for ln in lines) > W - 68 and size > 36:
        size -= 2
        font = ImageFont.truetype(FONT, size)
    line_h = round(size * 1.16)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    top = H - 126 - line_h * len(lines)
    sil = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sil)
    for i, ln in enumerate(lines):
        sd.text((W // 2, top + i * line_h + 7), ln, font=font,
                fill=(0, 0, 0, 255), anchor="ma")
    soft, tight = _shadow_layers(sil)
    im.alpha_composite(soft)
    im.alpha_composite(tight)
    d = ImageDraw.Draw(im)
    for i, ln in enumerate(lines):
        d.text((W // 2, top + i * line_h), ln, font=font,
               fill=(255, 255, 255, 255), anchor="ma")
    im.save(path)
    return im


title_im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
spaced_text(title_im, TITLE_TEXT, ImageFont.truetype(FONT, 60), 70, 0.04)
spaced_text(title_im, AUTHOR_TEXT, ImageFont.truetype(FONT, 34), 140, 0.06)

# 常驻《书名》/作者层属于模板身份，按策略豁免去标点（书名号、间隔号照旧上屏）。
# 先清掉上一版 cap-*.png：分句数变了以后旧文件会留在 TMP 里被 QC 当候选，报假错配。
for stale in os.listdir(TMP):
    if stale.startswith("cap-") and stale.endswith(".png"):
        os.remove(os.path.join(TMP, stale))

blank = Image.new("RGBA", (W, H), (0, 0, 0, 0))
for i, ch in enumerate(chunks):
    ch["index"] = i
    ch["img"] = os.path.join(TMP, f"cap-{i:03d}-{ch['order']}.png")
    caption_png(ch["display"], ch["img"])
    ch["alpha"] = Image.open(ch["img"]).convert("RGBA")

# caption-track.json = 「画面上到底上了什么字」的证据。check-caption-punctuation.py
# 核的是 display 字段，缺它直接判违规，所以中间层 TMP 之外还要在 episodes/<书名>/ 持久留一份。
track = [{"index": ch["index"], "order": ch["order"], "text": ch["text"],
          "display": ch["track_display"], "captionStyle": _cap_style,
          "start": round(ch["start"], 3), "end": round(ch["end"], 3)} for ch in chunks]
for p in (os.path.join(TMP, "caption-track.json"),
          os.path.join(EP, "caption-track.json")):
    json.dump(track, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"[caps] caption-track.json 落 {len(track)} 条（含 display 上屏文本）")

if os.environ.get("LAYERS_ONLY"):
    print(f"[layers-only] 书名层 + {len(chunks)} 条字幕层已生成于 {TMP}")
    raise SystemExit(0)

# 常驻书名 + 当前分句的合成层只在分句切换时重建，避免每帧重复 alpha_composite
title_only = title_im
_overlay_cache = {}


def active_chunk(t):
    for i, ch in enumerate(chunks):
        if ch["start"] <= t < ch["show_end"]:
            return i
    return -1


def overlay_for(i):
    if i not in _overlay_cache:
        if i < 0:
            _overlay_cache[i] = title_only
        else:
            lay = title_im.copy()
            lay.alpha_composite(chunks[i]["alpha"])
            _overlay_cache[i] = lay
    return _overlay_cache[i]


# ---------------------------------- 单趟合成：背景缓推 + 交叉溶解 + 水波 + 字幕，直接产出 body.mp4
body_mp4 = os.path.join(TMP, "body.mp4")
# 背景缓存签名：水波窗口或字幕层文本变了，旧的 body.mp4 必须作废重合成，
# 否则「改了渲染器但成片没变」会静默发生（时长一致，缓存检查查不出来）。
bg_sig = {"water_frame0": WATER_FRAME0, "water_nframes": WATER_NFRAMES,
          "bridge_zoom": BRIDGE_ZOOM, "layers": layer_names,
          "captions": [c["display"] for c in chunks]}
bg_marker = os.path.join(TMP, "body.sig.json")
if os.path.exists(body_mp4):
    prev = None
    if os.path.exists(bg_marker):
        prev = json.load(open(bg_marker, encoding="utf-8"))
    if prev != bg_sig or abs(probe_dur(body_mp4) - body_dur) > 0.2:
        os.remove(body_mp4)
        print("[bg] 背景参数/字幕已变，重新合成")

if not os.path.exists(body_mp4):
    covers = {}
    for name in set(layer_names):
        src = Image.open(os.path.join(IMG, f"{name}.png")).convert("RGB")
        iw, ih = src.size
        covers[name] = (src, max(W / iw, H / ih))

    n_total = round(body_dur * FPS)
    pipe = subprocess.Popen(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
         "-i", "pipe:0", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
         "-crf", "18", "-preset", "medium", body_mp4],
        stdin=subprocess.PIPE)

    def layer_frame(k, t):
        src, cover = covers[layer_names[k]]
        if layer_names[k] == "result-bridge":
            return zoom_frame(src, cover * BRIDGE_ZOOM)   # 桥接图静止，与片头定格对齐
        u = (t - layer_start[k]) / layer_len[k]
        return zoom_frame(src, cover * (ZOOM_START + (ZOOM_END - ZOOM_START) * ease_in_out(u)))

    idx = 0
    cap_idx = -1
    for frame in range(n_total):
        t = frame / FPS
        while idx < len(layer_start) - 1 and t >= layer_start[idx + 1]:
            idx += 1
        cur = layer_frame(idx, t)
        # 交叉溶解：进入本层后的前 XFADE 秒与上一层线性混合（等价 xfade=transition=fade）
        if idx > 0 and t - layer_start[idx] < XFADE:
            p = (t - layer_start[idx]) / XFADE
            cur = Image.blend(layer_frame(idx - 1, t), cur, min(1.0, max(0.0, p)))
        # 水波视觉效果：常驻规则，必须与水波音效同帧起、同帧止。
        # 只作用在背景层——书名层与字幕层在其后才 paste，所以涟漪不会把字扭糊。
        _wu = water_u(frame, FPS, WATER_FRAME0, WATER_NFRAMES)
        if _wu is not None:
            cur = ripple(cur, _wu)
        ci = active_chunk(t)
        if ci != cap_idx:
            cap_idx = ci
        ov = overlay_for(cap_idx)
        cur.paste(ov, (0, 0), ov)
        pipe.stdin.write(cur.tobytes())
        if frame % 1500 == 0:
            print(f"[bg] {frame}/{n_total} ({t:.1f}s)", flush=True)
    pipe.stdin.close()
    pipe.wait()
    if pipe.returncode != 0:
        raise SystemExit(f"正文合成失败: {pipe.returncode}")
    json.dump(bg_sig, open(bg_marker, "w", encoding="utf-8"), ensure_ascii=False)

print(f"[body] {probe_dur(body_mp4):.2f}s (target {body_dur:.2f}s)")
print(f"[water] 水波背景 {WATER_FRAME0}→{WATER_FRAME0 + WATER_NFRAMES - 1} 帧 "
      f"= 成片 {WATER_START:.2f}–{round(WATER_START + WATER_DUR, 2)}s（与水波音效同窗）")

# ------------------------------------------------------- 3. 片头（本期 HyperFrames 渲染）
if not os.path.exists(INTRO_SOURCE):
    raise SystemExit(f"缺少 HyperFrames 片头渲染结果: {INTRO_SOURCE}")
intro_mp4 = os.path.join(TMP, "intro.mp4")
ff(["-i", INTRO_SOURCE,
    "-vf", f"trim=0:{INTRO_TRIM},setpts=PTS-STARTPTS,fps={FPS},format=yuv420p",
    "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS), "-crf", "18", intro_mp4])

# ------------------------------------------------------------------- 4. 混音合成
intro_story = os.path.join(ASSETS, "template-audio", "intro-voiceover-story.mp3")
body_story = os.path.join(AUDIO, "body-voiceover-story.mp3")
if not os.path.exists(body_story):
    run(["node", os.path.join(ROOT, "scripts", "process-voiceover.mjs"),
         os.path.join(AUDIO, "body-voiceover.mp3"), body_story, "story"])
bgm_dir = os.path.join(ASSETS, "bgm")
bgm_env = os.environ.get("BGM")
shared_bgm = sorted(
    os.path.join(bgm_dir, n) for n in os.listdir(bgm_dir) if n.lower().endswith(".mp3"))
bgm = os.path.join(bgm_dir, bgm_env) if bgm_env else (shared_bgm[0] if shared_bgm else None)
if not bgm or not os.path.exists(bgm):
    raise SystemExit(f"缺少共享 BGM：{bgm_dir} 下没有 mp3（BGM={bgm_env}）")
scroll_sfx = os.path.join(ASSETS, "sfx", "gear-scroll.mp3")
water_sfx = os.path.join(ASSETS, "sfx", "water.mp3")
for f in [intro_story, body_story, bgm, scroll_sfx, water_sfx]:
    if not os.path.exists(f):
        raise SystemExit(f"Missing audio: {f}")
print(f"[bgm] {os.path.basename(bgm)}")

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
out_final = os.path.join(RENDERS, f"{EPISODE}-final.mp4")
candidate = os.path.join(TMP, f"{EPISODE}-final-候选.mp4")
ff(["-i", intro_mp4, "-i", body_mp4, "-i", intro_story, "-i", body_story,
    "-stream_loop", "-1", "-i", bgm, "-i", scroll_sfx, "-i", water_sfx,
    "-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", candidate])

# ------------------------------------------------- 技术验收：分辨率/音频流/时长一致性
probe = json.loads(subprocess.run(
    ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", candidate],
    capture_output=True, text=True).stdout)
v = next((s for s in probe["streams"] if s["codec_type"] == "video"), {})
a = next((s for s in probe["streams"] if s["codec_type"] == "audio"), None)
dur = float(probe["format"]["duration"])
assert v.get("width") == W and v.get("height") == H, f"分辨率错误: {v}"
assert a, "缺少音频流"
assert abs(dur - final_dur) < 1.0, f"时长异常: {dur} vs 计划 {final_dur}"

SCRIPTS = os.path.dirname(os.path.abspath(__file__))


def gate(script, *args):
    """跑 scripts/ 下的验收门禁，返回 CompletedProcess（不打印即失败也要看到原因）。"""
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, script), *args],
                          capture_output=True, text=True)


# 门禁必须卡在 shutil.move 之前：候选没过就不许覆盖已交付成片。
# 「只加水声不加水波」是唯一一个分辨率/时长/音频流/字幕/切点全部照常通过的缺陷。
_water_gate = gate("check-water-ripple.py", candidate,
                   "--intro-trim", str(INTRO_TRIM), "--fps", str(FPS))
print(_water_gate.stdout.strip())
if _water_gate.returncode != 0:
    raise SystemExit(
        "水波同窗门禁未通过，候选不激活到 renders/：水声与可见涟漪必须落在同一帧窗口。"
        "查背景层是否调了 lib 的 ripple()、混音是否引了 assets/sfx/water.mp3。"
        "已叠字幕的成片不能事后补波（会扭糊字幕），必须重渲正文背景。\n"
        + (_water_gate.stderr or "")[-500:])

# 字幕上屏零标点：拿本脚本落的 caption-track.json（display 字段）+ TMP 里的 cap-*.png 核对。
# 去标点发生在渲染最后一步，一旦有人把剥离点挪到分句之前，成片不会报错，只会静默退化出
# 无断点的长串；所以规则要落地，就得有一道拿字幕轨当证据的门禁，而不是靠人眼抽帧。
_cap_gate = gate("check-caption-punctuation.py", os.path.join(EP, "caption-track.json"),
                 "--csv", os.path.join(EP, "script-body.csv"), "--png-dir", TMP)
print(_cap_gate.stdout.strip())
if _cap_gate.returncode != 0:
    raise SystemExit(
        "字幕标点门禁未通过，候选不激活到 renders/：上屏字幕必须零标点"
        "（句中标点换全角空格，· 与常驻《书名》层豁免）。\n"
        + (_cap_gate.stdout or "")[-800:] + (_cap_gate.stderr or "")[-400:])

shutil.move(candidate, out_final)
print(f"[final] {out_final} {dur:.2f}s 720x960 audio={a['codec_name']}")
