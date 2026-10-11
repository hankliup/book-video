#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""《白鲸》共享模板样式兜底渲染（长正文 218s）。

背景：HyperFrames 0.7.33 在本机对 200s+ 正文的单次会话渲染稳定崩溃
（Page.captureScreenshot: Target closed），分段渲染又因运行时只认「创作时」写入的
字幕时间（事后 tl.shiftChildren() 被忽略）而无法复用，故沿用
scripts/render_dorian_template_fallback.py 已验证的 ffmpeg + Pillow 浮点合成路线，
复刻 templates/shared-video-template 的视觉与混音规格：

  · 片头复用本期 HyperFrames 渲染的 intro.mp4（玻璃碎片片头 + 目标书定格），裁 3.0s
  · 正文 result-bridge 起幅 0.32s → 3 张氛围图均分，0.64s 交叉溶解 + 居中缓慢推镜
  · 常驻《白鲸》+「赫尔曼·梅尔维尔 / 著」（字号/行位从本期片头 HTML 解析，逐字字距 + 双通道黑投影）
  · 字幕白字黑投影（bottom 126px，56/50px，无卡片底框），文本以 script-body.csv 为准
  · 混音参数与 scripts/render-episode-final.mjs 一致（BGM 0.32 / loudnorm -14 LUFS）

慢推一律用 PIL.Image.transform(AFFINE) 的浮点矩阵合成，不做整数取整、不加斜向漂移，
避免 zoompan 的逐帧量化抖动（见交付前 scripts/motion-jitter-probe.py 抽查）。

用法：
  python3 scripts/render_baijing_template_fallback.py
  INTRO_SOURCE=... PREVIEW=... python3 scripts/render_baijing_template_fallback.py
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

import numpy as np  # 切点/水波验收做逐帧亮度统计

ROOT = "/Users/apple/Documents/Obsidian/自媒体/短视频/文学/视频制作"
EPISODE = os.environ.get("EPISODE", "白鲸")
EP = os.path.join(ROOT, "episodes", EPISODE)
IMG = os.path.join(EP, "images")
AUDIO = os.path.join(EP, "audio")
RENDERS = os.path.join(EP, "renders")
ASSETS = os.path.join(ROOT, "assets")
PREVIEW = os.environ.get(
    "PREVIEW", os.path.join(ROOT, "tmp", "preview-book-f03c46a261"))
INTRO_SOURCE = os.environ.get(
    "INTRO_SOURCE", os.path.join(PREVIEW, "intro", "renders", "intro.mp4"))
INTRO_HTML = os.path.join(PREVIEW, "intro", "index.html")
TMP = os.environ.get("FALLBACK_TMP", "/tmp/baijing_tpl")
os.makedirs(TMP, exist_ok=True)
os.makedirs(RENDERS, exist_ok=True)

FPS = 30
W, H = 720, 960
FONT = "/System/Library/Fonts/STHeiti Medium.ttc"  # 共享模板在本机的实际回退字体

brief = json.load(open(os.path.join(EP, "brief.json"), encoding="utf-8"))
TITLE_TEXT = f"《{brief['display_title']}》"
AUTHOR_TEXT = f"{brief['author']} / 著"

# 片头裁切点：共享片头合成时长 4.27s，其中 ~2.62s 起「结果页」（桥接图 + 书名/作者）稳定定格。
# render-episode-final.mjs 里的 1.83s 等于片头口播长度，会把画面切在书名滚动中段；
# 已交付系列成片实测切在结果页定格、与正文首帧同为桥接图，此处对齐（同《道林·格雷的画像》）。
INTRO_TRIM = float(os.environ.get("INTRO_TRIM_SECONDS", "3.0"))
INTRO_OFFSET_MS = round(INTRO_TRIM * 1000)
BGM_VOL = 0.32
SCROLL_START, SCROLL_END = 1.08, 2.38

# 水波「音效—画面」同窗：常数唯一真源是 scripts/lib/water-ripple.json（Python 薄壳
# scripts/lib/water_ripple.py）。渲染脚本里再出现 0.32 这样的秒数字面量就算违规——
# 改一处忘改另一处会变成「有水声没水波」或「水波比声音早半秒」，而且没人报错。
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from water_ripple import WATER_DUR, WATER_OFFSET, ripple, water_u  # noqa: E402
# 上屏标点字符表的唯一真源同样是 lib（subtitle-punctuation.json → caption_display.py）：
# 期专用渲染器自己抄一份「要剥掉哪些标点」，就一定会漏掉行中间的 ，、；：（本期 order 53）。
from caption_display import strip_display  # noqa: E402

WATER_START = round(INTRO_TRIM + WATER_OFFSET, 2)   # 成片时间轴上的水波起点（秒）
WATER_NFRAMES = round(WATER_DUR * FPS)              # 涟漪帧数
# 正文背景的第 0 帧对应成片 INTRO_TRIM，所以背景循环里的起点帧只偏移 WATER_OFFSET
WATER_FRAME0 = round(WATER_OFFSET * FPS)

BRIDGE_IN = 0.32      # 桥接图独立停留时长，之后与场景 1 交叉溶解
XFADE = 0.64          # 场景交叉溶解时长
SMALL_CHARS = 12      # 单行超过此字数用 50px


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

# order 1 是书名锚点行：ASR 只给到 0.25s 窗口，画面上会闪一下「白鲸」。
# 已交付各期（死魂灵 0.18s、陌生女人的来信 0.24s）都是从正文起幅就开始挂书名，
# 这里把首条起点回拉到 0.20s —— 前面本来就没有别的字幕，回拉不会与任何条目重叠。
if caps and int(caps[0]["order"]) == 1 and float(caps[0]["end"]) - float(caps[0]["start"]) < 0.8:
    caps[0] = dict(caps[0], start=0.20)
    print(f"[caps] 书名锚点行回拉至 0.20s（ASR 原窗口过短）")

text_by_order = {}
with open(os.path.join(EP, "script-body.csv"), encoding="utf-8") as f:
    for row in csv.DictReader(f):
        text_by_order[int(row["order"])] = row["text"].strip()

scenes = ["atmosphere-1", "atmosphere-2", "atmosphere-3"]


# ------------------------------- 1. 正文背景：亚像素缓推 + 交叉溶解（单趟 Pillow 合成）
ZOOM_START = 1.020
ZOOM_END = 1.062


def bridge_zoom_at(trim):
    """桥接图缩放必须等于片头 page-bg 动画在裁切点的实际缩放，否则 3.0s 接缝会「跳一下」。
    模板：tl.to('.target .page-bg', {scale:1.035, duration:1.87, ease:'sine.inOut'}, 2.38)。"""
    u = min(1.0, max(0.0, (trim - 2.38) / 1.87))
    return 1.0 + 0.035 * (0.5 - 0.5 * math.cos(math.pi * u))


BRIDGE_ZOOM = bridge_zoom_at(INTRO_TRIM)   # trim=3.0s ⇒ ≈1.0086


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


bridge_len = BRIDGE_IN + XFADE
scene_len = (body_dur + 3 * XFADE - bridge_len) / 3
layer_names = ["result-bridge"] + scenes
layer_len = [bridge_len] + [scene_len] * 3
layer_start = [0.0]
for k in range(1, len(layer_len)):
    layer_start.append(layer_start[-1] + layer_len[k - 1] - XFADE)
print(f"[layers] bridge {layer_len[0]:.2f}s + 3 scenes x {scene_len:.2f}s "
      f"starts={['%.2f' % s for s in layer_start]}")

bg = os.path.join(TMP, "body-bg.mp4")
# 复用判定必须带上画面参数指纹：只比时长会让改了 BRIDGE_ZOOM/推镜档位后仍拿旧背景
bg_params = {"body_dur": round(body_dur, 3), "zoom": [ZOOM_START, ZOOM_END],
             "bridge_zoom": round(BRIDGE_ZOOM, 5), "bridge_in": BRIDGE_IN, "xfade": XFADE,
             "scenes": scenes, "fps": FPS,
             "water_frame0": WATER_FRAME0, "water_nframes": WATER_NFRAMES}
bg_stamp = os.path.join(TMP, "body-bg.params.json")


def bg_params_match():
    try:
        return json.load(open(bg_stamp, encoding="utf-8")) == bg_params
    except Exception:
        return False


if os.path.exists(bg) and os.environ.get("FORCE_BG") != "1" \
        and bg_params_match() and abs(probe_dur(bg) - body_dur) < 0.2:
    print(f"[bg] reuse body-bg.mp4 {probe_dur(bg):.2f}s")
else:
    json.dump(bg_params, open(bg_stamp, "w", encoding="utf-8"), ensure_ascii=False)
    covers = {}
    for name in layer_names:
        src = Image.open(os.path.join(IMG, f"{name}.png")).convert("RGB")
        iw, ih = src.size
        covers[name] = (src, max(W / iw, H / ih))

    n_total = round(body_dur * FPS)
    pipe = subprocess.Popen(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
         "-i", "pipe:0", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
         "-crf", "17", "-preset", "medium", bg],
        stdin=subprocess.PIPE)

    def layer_frame(k, t):
        src, cover = covers[layer_names[k]]
        if k == 0:                      # 桥接图静止，画幅与片头定格对齐
            return zoom_frame(src, cover * BRIDGE_ZOOM)
        u = (t - layer_start[k]) / layer_len[k]
        return zoom_frame(src, cover * (ZOOM_START + (ZOOM_END - ZOOM_START) * ease_in_out(u)))

    idx = 0
    for frame in range(n_total):
        t = frame / FPS
        while idx < len(layer_start) - 1 and t >= layer_start[idx + 1]:
            idx += 1
        cur = layer_frame(idx, t)
        # 交叉溶解：进入本层后的前 XFADE 秒内，与上一层线性混合（等价于 xfade=transition=fade）
        if idx > 0 and t - layer_start[idx] < XFADE:
            p = (t - layer_start[idx]) / XFADE
            cur = Image.blend(layer_frame(idx - 1, t), cur, min(1.0, max(0.0, p)))
        # 水波视觉效果：常驻规则，必须与水波音效同帧起、同帧止。
        # 只作用在背景层——书名层与字幕层在其后才叠加绘制，所以涟漪不会把字扭糊。
        _wu = water_u(frame, FPS, WATER_FRAME0, WATER_NFRAMES)
        if _wu is not None:
            cur = ripple(cur, _wu)
        pipe.stdin.write(cur.tobytes())
        if frame and frame % (FPS * 30) == 0:
            print(f"[bg] {frame / FPS:.0f}/{body_dur:.0f}s")
    pipe.stdin.close()
    pipe.wait()
    if pipe.returncode != 0:
        raise SystemExit(f"正文背景合成失败: {pipe.returncode}")
    print(f"[water] 水波背景 {WATER_FRAME0}→{WATER_FRAME0 + WATER_NFRAMES - 1} 帧 "
          f"= 成片 {WATER_START:.2f}–{round(WATER_START + WATER_DUR, 2)}s（与水波音效同窗）")
print(f"[bg] {probe_dur(bg):.2f}s (target {body_dur:.2f}s)")


# ------------------------------------------- 2. 常驻书名层 + 字幕层（Pillow）
def _layout(text, font, spacing_em):
    widths = [font.getlength(ch) for ch in text]
    spacing = font.size * spacing_em
    total = sum(widths) + spacing * (len(text) - 1)
    return widths, spacing, (W - total) / 2


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
    """宽而软的落影 + 紧贴笔画的窄描边：月光云团、落日海面这类高亮低对比氛围图上仍可读，
    暗背景上观感与单层投影一致，不引入卡片底框。"""
    return (sil.filter(ImageFilter.GaussianBlur(14)),
            sil.filter(ImageFilter.GaussianBlur(3)))


def spaced_text(im, text, font, top, spacing_em):
    """逐字排布模拟模板 letter-spacing，白字 + 黑投影（0 7px 18px rgba(0,0,0,.96) 的加强版）。
    模板里 top 是文字块上沿（line-height:1），故 x 居中 / y=ascender。"""
    widths, spacing, x0 = _layout(text, font, spacing_em)
    soft, tight = _shadow_layers(_silhouette(widths, spacing, text, font, x0, top, 7))
    im.alpha_composite(soft)
    im.alpha_composite(tight)
    ld = ImageDraw.Draw(im)
    x = x0
    for ch, cw in zip(text, widths):
        ld.text((x, top), ch, font=font, fill=(255, 255, 255, 255))
        x += cw + spacing


def intro_title_metrics():
    """书名/作者字号与行位取自本期 HyperFrames 片头 .target 的内联样式。

    片头结果页与正文常驻层是同一份文字：兜底渲染若写死 60px/140，短书名（如《白鲸》
    片头是 82px/166）会在 3.0s 切点出现明显跳位。解析不到时退回共享渲染器的排版公式。
    """
    size = max(46, min(82, math.floor(660 / max(1, len(TITLE_TEXT) + 1))))
    top, a_top = 70, (166 if size >= 70 else 140)
    if os.path.exists(INTRO_HTML):
        h = open(INTRO_HTML, encoding="utf-8").read()
        i = h.find("page target")
        seg = h[i:i + 800] if i >= 0 else ""
        m = re.search(r'page-title" style="font-size:\s*([\d.]+)px', seg)
        if m:
            size = round(float(m.group(1)))
        m = re.search(r'page-title" style="top:\s*([\d.]+)px', seg)
        if m:
            top = round(float(m.group(1)))
        m = re.search(r'page-author" style="top:\s*([\d.]+)px', seg)
        if m:
            a_top = round(float(m.group(1)))
    return size, top, a_top


TITLE_SIZE, TITLE_TOP, AUTHOR_TOP = intro_title_metrics()
AUTHOR_SIZE = 34
print(f"[title] {TITLE_TEXT} {TITLE_SIZE}px@{TITLE_TOP} / {AUTHOR_SIZE}px@{AUTHOR_TOP}（取自本期片头）")

title_im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
spaced_text(title_im, TITLE_TEXT, ImageFont.truetype(FONT, TITLE_SIZE), TITLE_TOP, 0.04)
spaced_text(title_im, AUTHOR_TEXT, ImageFont.truetype(FONT, AUTHOR_SIZE), AUTHOR_TOP, 0.06)
title_png = os.path.join(TMP, "title.png")
title_im.save(title_png)

TRAILING_PUNCT = "，。、；：！？…—·”’）》"


def caption_lines(text):
    parts = [p.rstrip(TRAILING_PUNCT) for p in text.split(" ") if p]
    parts = [p for p in parts if p]
    if len(parts) >= 2:
        return parts
    body = text.rstrip(TRAILING_PUNCT)
    if len(body) <= 13:
        return [body]
    return [body[:13], body[13:]]


def caption_display(text):
    """折行之后的上屏文本：句中停顿号（，、；：）换成全角空格，其余标点删除，· 保留。

    只 rstrip 行尾标点是不够的：`有人觉得它 庞杂、缓慢，` 的 `、` 在行中间，旧写法会把它
    原样画上屏（`check-caption-punctuation.py` 实测报 order 53 残留 `、`）。字符表只能有
    真源一份 —— scripts/lib/subtitle-punctuation.json，经 lib/caption_display.py 读出。
    """
    lines = [ln for ln in (strip_display(x) for x in caption_lines(text)) if ln]
    return lines or [strip_display(text) or text.strip()]


def build_caption(text, path):
    lines = caption_display(text)
    size = 56 if max(len(ln) for ln in lines) <= SMALL_CHARS else 50
    font = ImageFont.truetype(FONT, size)
    while font.getlength(max(lines, key=len)) > W - 68 and size > 36:
        size -= 2
        font = ImageFont.truetype(FONT, size)
    line_h = round(size * 1.16)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    top = H - 126 - line_h * len(lines)
    for i, ln in enumerate(lines):
        y = top + i * line_h
        widths, spacing, x0 = _layout(ln, font, 0.0)
        soft, tight = _shadow_layers(_silhouette(widths, spacing, ln, font, x0, y, 7))
        im.alpha_composite(soft)
        im.alpha_composite(tight)
    d = ImageDraw.Draw(im)   # 投影 alpha_composite 完成后再取画笔
    for i, ln in enumerate(lines):
        d.text((W // 2, top + i * line_h), ln, font=font,
               fill=(255, 255, 255, 255), anchor="ma")
    im.save(path)


transparent = os.path.join(TMP, "transparent.png")
Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(transparent)

# 字幕轨按「绝对帧号」锚定：逐条累加 round(时长) 会把每条 ≤0.5 帧的取整误差攒起来，
# 79 条字幕的长正文实测能累积到 ~270ms 滞后（后段字幕比配音慢一行）。
target_frames = round(body_dur * FPS)


def frame_of(t):
    return min(target_frames, max(0, round(t * FPS)))


entries = []
cursor = 0
# 落一份 caption-track.json：scripts/qc-caption-alignment.py 靠它做「画面上实际显示的字幕」
# 与「该时刻应显示的字幕」的 IoU 比对 —— 音画错位那类问题必须这样量化才看得见。
track = []
for i, c in enumerate(caps):
    order = int(c["order"])
    s, e = float(c["start"]), float(c["end"])
    text = text_by_order.get(order, "")
    # display = 真正画在屏幕上的那一版（caption_lines 剥掉标点后的行），用 " / " 连接，
    # 与 scripts/check-caption-punctuation.py 的 schema 对齐。只落 CSV 原文的旧轨会让
    # 标点门禁退化成「79/79 条没有 display 字段」，等于这期的上屏零标点规则没被验收过。
    track.append({"index": i, "order": order, "text": text,
                  "display": " / ".join(caption_display(text)),
                  "start": round(s, 3), "end": round(e, 3)})
    p = os.path.join(TMP, f"cap-{i:03d}-{order}.png")
    build_caption(text, p)
    f0, f1 = frame_of(s), frame_of(e)
    if f1 <= f0:
        f1 = f0 + 1
    if f0 > cursor:
        entries.append((f0 - cursor, transparent))
    entries.append((f1 - f0, p))
    cursor = max(cursor, f1)
json.dump(track, open(os.path.join(TMP, "caption-track.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
# 再落一份到期目录：AGENTS.md 的交付门禁写的是 "<episode>/caption-track.json"，
# 而 TMP 是会被清掉的中间层——轨只活在 TMP 里，下期想复验就没有可比对的轨了。
json.dump(track, open(os.path.join(EP, "caption-track.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
if cursor < target_frames:
    entries.append((target_frames - cursor, transparent))
total_frames = sum(n for n, _ in entries)
print(f"[caps] {len(caps)} 条字幕 / 字幕轨 {total_frames} 帧 = {total_frames / FPS:.2f}s"
      f"（绝对帧锚定，最大条目误差 <1 帧）")
assert total_frames == target_frames, f"字幕轨帧数 {total_frames} != {target_frames}"

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
ff(["-i", bg, "-i", sub_mov, "-i", title_png,
    "-filter_complex",
    "[0:v][1:v]overlay=0:0:format=auto[t];[t][2:v]overlay=0:0:format=auto,format=yuv420p[v]",
    "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
    "-crf", "18", "-preset", "medium", body_mp4])
print(f"[body] {probe_dur(body_mp4):.2f}s")


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
# 共享 BGM 的文件名被手工改过（全角/半角括号不一致），按目录枚举解析避免硬编码失配
bgm_dir = os.path.join(ASSETS, "bgm")
bgm_env = os.environ.get("BGM")
shared_bgm = sorted(
    os.path.join(bgm_dir, n) for n in os.listdir(bgm_dir) if n.lower().endswith(".mp3")
)
bgm = os.path.join(bgm_dir, bgm_env) if bgm_env else (shared_bgm[0] if shared_bgm else None)
if not bgm or not os.path.exists(bgm):
    raise SystemExit(f"缺少共享 BGM：{bgm_dir} 下没有 mp3（BGM={bgm_env}）")
print(f"[bgm] {os.path.basename(bgm)}")
scroll_sfx = os.path.join(ASSETS, "sfx", "gear-scroll.mp3")
water_sfx = os.path.join(ASSETS, "sfx", "water.mp3")

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
# 先写候选，验收全绿才 move 激活到 renders/：不合格的输出绝不能覆盖已交付成片。
candidate = os.path.join(TMP, f"{EPISODE}-final.candidate.mp4")
# 成片这一步是第三次有损编码（bg crf17 → body crf18 → final），默认 crf23 在暗部海面
# 渐变上会重新引入块状/条带噪声，这里显式压到 crf17。
final_crf = os.environ.get("FINAL_CRF", "17")
ff(["-i", intro_mp4, "-i", body_mp4, "-i", intro_story, "-i", body_story,
    "-stream_loop", "-1", "-i", bgm, "-i", scroll_sfx, "-i", water_sfx,
    "-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
    "-crf", final_crf, "-preset", "medium",
    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", candidate])
dur = probe_dur(candidate)
assert abs(dur - final_dur) < 1.0, f"时长异常: {dur:.2f}s vs 计划 {final_dur:.2f}s"


def _zone_mad(video, n_a, n_b, box):
    """两帧在指定区域的平均绝对亮度差（0-255）。"""
    def frame(n):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", video, "-vf",
                              f"select=eq(n\\,{n})", "-frames:v", "1", "-f", "rawvideo",
                              "-pix_fmt", "gray", "pipe:1"],
                             capture_output=True, stdin=subprocess.DEVNULL).stdout
        return np.frombuffer(raw, dtype=np.uint8).reshape(H, W).astype(np.float64)
    a, b = frame(n_a), frame(n_b)
    x0, y0, x1, y1 = box
    return float(np.abs(a[y0:y1, x0:x1] - b[y0:y1, x0:x1]).mean())


# 切点守卫：接错片头（INTRO_SOURCE 指向别期）会让书名/画面在 3.0s 处跳一下。
# 画面区（y 210-680，避开书名与字幕）阈值 12；《白鲸》实测 2.4，与编码重建噪声同量级。
_cut = round(INTRO_TRIM * FPS)
_seam_pic = _zone_mad(candidate, _cut - 1, _cut, (0, 210, W, 680))
_seam_title = _zone_mad(candidate, _cut - 1, _cut, (0, 55, W, 210))
print(f"[seam] 切点 {_cut / FPS:.2f}s 画面区 MAD={_seam_pic:.2f} 书名区 MAD={_seam_title:.2f}")
if _seam_pic > 12.0:
    raise SystemExit(
        f"片头→正文切点跳变 {_seam_pic:.2f}（阈值 12）：接进来的多半不是本期的片头。"
        f"请显式传 INTRO_SOURCE（本期片头渲染结果）后重跑，当前用的是 {INTRO_SOURCE}")

# 水波同窗门禁（常驻规则，见 AGENTS.md「Visual And Audio Rules」）：water.mp3 在切点后
# WATER_OFFSET 秒起播的那 WATER_DUR 秒，画面上必须同时出现一圈自中心外扩的涟漪。
# 这道校验必须卡在 move 之前——「只加声音不加画面」是唯一一个分辨率/时长/音频流/字幕/切点
# 全部照常通过的缺陷：成片看起来完全正常，只有量化门禁拦得住。门禁自己 import lib 的常数，
# 所以这里只传切点与帧率，不传任何秒数。
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
shutil.move(candidate, out_final)
print(f"[final] {out_final} {probe_dur(out_final):.2f}s")
