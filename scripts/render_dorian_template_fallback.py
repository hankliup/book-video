#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
《道林·格雷的画像》共享模板样式兜底渲染。

背景：HyperFrames 0.7.33 在本机对 264s 正文的单次会话渲染稳定崩溃
（Page.captureScreenshot: Target closed，约 630/3969 帧，去掉 SVG 滤镜后仍复现），
分段渲染又因运行时只认「创作时」写入的字幕时间（事后 shiftChildren 被忽略、
整体偏移到负值则直接崩溃）而无法复用，故用 ffmpeg + Pillow 复刻
templates/shared-video-template 的视觉与混音规格：

  · 片头复用 HyperFrames 已成功渲染的 intro.mp4（玻璃碎片片头），裁 1.83s
  · 正文 result-bridge 起幅 0.32s → 3 张氛围图均分，0.64s 交叉溶解 + 缓慢推镜
  · 常驻《道林·格雷的画像》+「奥斯卡·王尔德 / 著」（top 70/140，60/34px，900 字重）
  · 字幕白字黑投影（bottom 126px，56/50px，无卡片底框），文本以 script-body.csv 为准
  · 混音参数与 scripts/render-episode-final.mjs 一致（BGM 0.32 / loudnorm -14 LUFS）
"""
import csv
import json
import math
import os
import subprocess

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = "/Users/apple/Documents/Obsidian/自媒体/短视频/文学/视频制作"
EP = os.path.join(ROOT, "episodes", "道林·格雷的画像")
IMG = os.path.join(EP, "images")
AUDIO = os.path.join(EP, "audio")
RENDERS = os.path.join(EP, "renders")
ASSETS = os.path.join(ROOT, "assets")
PREVIEW = os.path.join(ROOT, "tmp", "preview-book-b53f635c07")
INTRO_SOURCE = os.path.join(PREVIEW, "intro", "renders", "intro.mp4")
TMP = "/tmp/dorian_tpl"
os.makedirs(TMP, exist_ok=True)
os.makedirs(RENDERS, exist_ok=True)

FPS = 30
W, H = 720, 960
FONT = "/System/Library/Fonts/STHeiti Medium.ttc"  # 共享模板在本机的实际回退字体
TITLE_TEXT = "《道林·格雷的画像》"
AUTHOR_TEXT = "奥斯卡·王尔德 / 著"

# 片头裁切点：共享片头合成时长 4.25s，其中 ~2.62s 起「结果页」（桥接图 + 书名/作者）稳定定格。
# render-episode-final.mjs 里的 1.83s 等于片头口播长度，但会把画面切在书名滚动中段；
# 已交付的《霍比特人》成片实测片头 3.01s（切在结果页定格、与正文首帧同为桥接图），此处对齐系列成片。
INTRO_TRIM = float(os.environ.get("INTRO_TRIM_SECONDS", "3.0"))
INTRO_OFFSET_MS = round(INTRO_TRIM * 1000)
BGM_VOL = 0.32
SCROLL_START, SCROLL_END = 1.08, 2.38
WATER_START = round(INTRO_TRIM + 0.32, 2)

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

text_by_order = {}
with open(os.path.join(EP, "script-body.csv"), encoding="utf-8") as f:
    for row in csv.DictReader(f):
        text_by_order[int(row["order"])] = row["text"].strip()

scenes = ["atmosphere-1", "atmosphere-2", "atmosphere-3"]


# ------------------------------- 1. 正文背景：亚像素缓推 + 交叉溶解（单趟 Pillow 合成）
# 为什么不用 ffmpeg zoompan：zoompan 的 x/y/裁剪宽高都会取整到源图整数像素。源图只有
# 1024x1365，1 像素 ≈ 0.7 输出像素，再叠加 ±16px 的斜向漂移，慢镜头里就变成持续的抖动。
# 这里改用 Image.transform 的浮点仿射矩阵：每帧的缩放与中心都是连续浮点值，
# 且去掉斜向漂移，只保留 1.020→1.062 的居中缓推（88s 内约 4%，缓入缓出）。
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


bridge_len = BRIDGE_IN + XFADE
scene_len = (body_dur + 3 * XFADE - bridge_len) / 3
layer_names = ["result-bridge"] + scenes
layer_len = [bridge_len] + [scene_len] * 3
layer_start = [0.0]
for k in range(1, len(layer_len)):
    layer_start.append(layer_start[-1] + layer_len[k - 1] - XFADE)

bg = os.path.join(TMP, "body-bg-v2.mp4")
if os.path.exists(bg) and abs(probe_dur(bg) - body_dur) < 0.2:
    print(f"[bg] reuse body-bg-v2.mp4 {probe_dur(bg):.2f}s")
else:
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
        pipe.stdin.write(cur.tobytes())
    pipe.stdin.close()
    pipe.wait()
    if pipe.returncode != 0:
        raise SystemExit(f"正文背景合成失败: {pipe.returncode}")
print(f"[bg] {probe_dur(bg):.2f}s (target {body_dur:.2f}s)")


# ------------------------------------------- 2. 常驻书名层 + 字幕层（Pillow）
def spaced_text(im, text, font, top, spacing_em):
    """逐字排布模拟模板 letter-spacing，并加 0 7px 18px rgba(0,0,0,.96) 投影。
    模板里 top 是文字块上沿（line-height:1），故用 anchor="ma"（x 居中 / y=ascender）。"""
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
spaced_text(title_im, TITLE_TEXT, ImageFont.truetype(FONT, 60), 70, 0.04)
spaced_text(title_im, AUTHOR_TEXT, ImageFont.truetype(FONT, 34), 140, 0.06)
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


def build_caption(text, path):
    lines = caption_lines(text)
    size = 56 if max(len(ln) for ln in lines) <= SMALL_CHARS else 50
    font = ImageFont.truetype(FONT, size)
    while font.getlength(max(lines, key=len)) > W - 68 and size > 36:
        size -= 2
        font = ImageFont.truetype(FONT, size)
    line_h = round(size * 1.16)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    top = H - 126 - line_h * len(lines)
    d = ImageDraw.Draw(im)
    for i, ln in enumerate(lines):
        y = top + i * line_h
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(sh).text((W // 2, y + 7), ln, font=font,
                                fill=(0, 0, 0, 245), anchor="ma")
        sh = sh.filter(ImageFilter.GaussianBlur(9))
        im.alpha_composite(sh)
        d.text((W // 2, y), ln, font=font, fill=(255, 255, 255, 255), anchor="ma")
    im.save(path)


transparent = os.path.join(TMP, "transparent.png")
Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(transparent)

entries = []
prev_end = 0.0
for c in caps:
    s, e = float(c["start"]), float(c["end"])
    if s - prev_end > 0.05:
        entries.append((max(1, round((s - prev_end) * FPS)), transparent))
    text = text_by_order.get(int(c["order"]), "")
    p = os.path.join(TMP, f"cap-{int(c['order']):03d}.png")
    build_caption(text, p)
    entries.append((max(1, round((e - s) * FPS)), p))
    prev_end = e
target = round(body_dur * FPS)
if sum(n for n, _ in entries) < target:
    entries.append((target - sum(n for n, _ in entries), transparent))
print(f"[caps] {len(caps)} 条字幕 / {sum(n for n, _ in entries)} 帧")

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


# ------------------------------------------------------- 3. 片头（复用成片级渲染）
intro_mp4 = os.path.join(TMP, "intro.mp4")
if not os.path.exists(INTRO_SOURCE):
    raise SystemExit(f"缺少 HyperFrames 片头渲染结果: {INTRO_SOURCE}")
ff(["-i", INTRO_SOURCE,
    "-vf", f"trim=0:{INTRO_TRIM},setpts=PTS-STARTPTS,fps={FPS},format=yuv420p",
    "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS), "-crf", "18", intro_mp4])


# ------------------------------------------------------------------- 4. 混音合成
intro_story = os.path.join(PREVIEW, "audio", "intro-voiceover-story.mp3")
body_story = os.path.join(AUDIO, "body-voiceover-story.mp3")
bgm = os.path.join(ASSETS, "bgm", "Where are you（low）.mp3")
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
    f"[6:a]atrim=0:1.06,asetpts=PTS-STARTPTS,aresample=48000,volume=1.0,"
    f"adelay={round(WATER_START * 1000)}|{round(WATER_START * 1000)}[watersfx]",
    "[introa][bodya][bgm][scrollsfx][watersfx]amix=inputs=5:duration=longest:dropout_transition=0:normalize=0,"
    "alimiter=limit=0.95,loudnorm=I=-14.0:TP=-1.0:LRA=7.0,"
    "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a]",
]
out_final = os.path.join(RENDERS, "道林·格雷的画像-final.mp4")
ff(["-i", intro_mp4, "-i", body_mp4, "-i", intro_story, "-i", body_story,
    "-stream_loop", "-1", "-i", bgm, "-i", scroll_sfx, "-i", water_sfx,
    "-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", out_final])
print(f"[final] {out_final} {probe_dur(out_final):.2f}s")
