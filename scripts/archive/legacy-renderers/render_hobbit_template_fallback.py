#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
《霍比特人》共享模板样式兜底渲染（浮点亚像素缓推 + ts-image-map 场景切换）。

为什么不走 HyperFrames / 分段渲染：
  · HyperFrames 0.7.33 在本机对 262s 正文的单次会话渲染稳定崩溃
    （Page.captureScreenshot: Target closed，约 630/7860 帧）。
  · 分段渲染不可用：运行时只认「创作时」写入的字幕时间，事后 tl.shiftChildren() 被忽略，
    每段都在重播正文开头 —— 已交付的《霍比特人》《白鲸》成片音画错位即此根因。
  · 也不用 ffmpeg zoompan：它的 x/y/裁剪宽高会取整到源图整数像素，1024x1365 源图上
    1 像素 ≈ 0.7 输出像素，慢镜头里变成持续抖动（用户反馈「氛围图有点晃」的实测根因）。

这里用 Pillow 的浮点仿射取景逐帧合成，复刻 templates/shared-video-template 的视觉与混音规格：
  · 片头复用 HyperFrames 已成功渲染的 intro.mp4（玻璃碎片片头），裁 3.0s 切在结果页定格
  · 正文按 episodes/霍比特人/ts-image-map.json（TypeSafe Jev 选型）的场景段落层：
    result-bridge 起幅 → 每段只做居中缓推（1.020→1.062，缓入缓出），段间 0.64s 交叉溶解
  · 常驻《霍比特人》+「J.R.R.托尔金 / 著」（top 70/140，60/34px）
  · 字幕白字黑投影（bottom 126px，56/50px，无卡片底框），文本以 script-body.csv 为准
  · 混音参数与 scripts/render-episode-final.mjs 一致（BGM 0.32 / loudnorm -14 LUFS）

用法: python3 scripts/render_hobbit_template_fallback.py
环境变量: INTRO_TRIM_SECONDS=3.0  REBUILD_BG=1（强制重合成正文背景）
"""
import csv
import json
import math
import os
import re
import subprocess

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = "/Users/apple/Documents/Obsidian/自媒体/短视频/文学/视频制作"
EP = os.path.join(ROOT, "episodes", "霍比特人")
IMG = os.path.join(EP, "images")
AUDIO = os.path.join(EP, "audio")
RENDERS = os.path.join(EP, "renders")
ASSETS = os.path.join(ROOT, "assets")
INTRO_SOURCE = os.path.join(ROOT, "tmp", "preview-book-21c282096a", "intro", "renders", "intro.mp4")
TMP = os.path.join(ROOT, "tmp", "hobbit-fallback")
os.makedirs(TMP, exist_ok=True)
os.makedirs(RENDERS, exist_ok=True)

FPS = 30
W, H = 720, 960
FONT = "/System/Library/Fonts/STHeiti Medium.ttc"  # 共享模板在本机的实际回退字体
TITLE_TEXT = "《霍比特人》"
AUTHOR_TEXT = "J.R.R.托尔金 / 著"

# 共享片头合成时长 4.27s，约 2.6s 起结果页（桥接图 + 书名/作者）稳定定格；
# 1.83s 只是片头口播长度，会把画面切在书名滚动中段，故对齐系列成片取 3.0s。
INTRO_TRIM = float(os.environ.get("INTRO_TRIM_SECONDS", "3.0"))
INTRO_OFFSET_MS = round(INTRO_TRIM * 1000)
BGM_VOL = 0.32
SCROLL_START, SCROLL_END = 1.08, 2.38
WATER_START = round(INTRO_TRIM + 0.32, 2)

XFADE = 0.64          # 场景交叉溶解时长
SMALL_CHARS = 12      # 单行超过此字数用 50px

ZOOM_START = 1.020
ZOOM_END = 1.062
BRIDGE_ZOOM = 1.035   # 桥接图保持静止，与片头定格画幅对齐


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
cap_by_order = {int(c["order"]): c for c in caps}
body_dur = float(timings["duration"])
final_dur = round(INTRO_TRIM + body_dur, 2)

text_by_order = {}
with open(os.path.join(EP, "script-body.csv"), encoding="utf-8") as f:
    for row in csv.DictReader(f):
        text_by_order[int(row["order"])] = row["text"].strip()

# ------------------------------------------------ 场景层：ts-image-map 选型 → 连续 runs
_ts_map = json.load(open(os.path.join(EP, "ts-image-map.json"), encoding="utf-8"))
order_map = {int(k): v for k, v in _ts_map["order_map"].items()}
missing = [o for o in text_by_order if o not in order_map]
if missing:
    raise SystemExit(f"ts-image-map.json 缺少 order {missing[:5]}，先跑 scripts/ts_image_select_hobbit.py")

runs = []  # {"name", "start", "end", "orders"}
for o in sorted(order_map):
    name = order_map[o]
    if not os.path.exists(os.path.join(IMG, f"{name}.png")):
        raise SystemExit(f"缺少氛围图 {name}.png（用 scripts/fetch-pexels-by-id.py 恢复）")
    if runs and runs[-1]["name"] == name:
        runs[-1]["orders"].append(o)
    else:
        runs.append({"name": name, "orders": [o]})

# 段边界取「下一段首句的开始」，把段间静音归给前一段，避免溶解期间画面空档
for i, r in enumerate(runs):
    r["start"] = 0.0 if i == 0 else float(cap_by_order[r["orders"][0]]["start"])
    nxt = runs[i + 1]["orders"][0] if i + 1 < len(runs) else None
    r["end"] = body_dur if nxt is None else float(cap_by_order[nxt]["start"])
    if r["end"] <= r["start"]:
        raise SystemExit(f"场景段 {i} 时长非法: {r}")

print(f"[scenes] {len(runs)} 段 ← ts-image-map.json (source={_ts_map.get('source')})")
for i, r in enumerate(runs):
    print(f"  {i}: {r['name']:15} orders {r['orders'][0]:>2}-{r['orders'][-1]:<2} "
          f"{r['start']:7.2f}→{r['end']:7.2f} ({r['end'] - r['start']:5.2f}s)")


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


# 每层比名义窗口提前 XFADE 开始绘制：溶解在下一段首句开口那一刻刚好完成
layer_start = [0.0]
for i in range(1, len(runs)):
    layer_start.append(runs[i]["start"] - XFADE)
layer_len = []
for i in range(len(runs)):
    begin = layer_start[i]
    end = runs[i]["end"]
    layer_len.append(end - begin)
if abs((layer_start[-1] + layer_len[-1]) - body_dur) > 0.01:
    raise SystemExit(f"层时间轴未闭合: {layer_start[-1] + layer_len[-1]:.3f} vs {body_dur:.3f}")

bg = os.path.join(TMP, "body-bg.mp4")
if os.path.exists(bg) and abs(probe_dur(bg) - body_dur) < 0.2 and not os.environ.get("REBUILD_BG"):
    print(f"[bg] reuse body-bg.mp4 {probe_dur(bg):.2f}s")
else:
    covers = {}
    for r in runs:
        if r["name"] in covers:
            continue
        src = Image.open(os.path.join(IMG, f"{r['name']}.png")).convert("RGB")
        iw, ih = src.size
        covers[r["name"]] = (src, max(W / iw, H / ih))

    n_total = round(body_dur * FPS)
    pipe = subprocess.Popen(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
         "-i", "pipe:0", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
         "-crf", "17", "-preset", "medium", bg],
        stdin=subprocess.PIPE)

    def layer_frame(k, t):
        name = runs[k]["name"]
        src, cover = covers[name]
        if name == "result-bridge":       # 桥接图静止，画幅与片头定格对齐
            return zoom_frame(src, cover * BRIDGE_ZOOM)
        span = runs[k]["end"] - runs[k]["start"]
        u = (t - runs[k]["start"]) / span if span > 0 else 1.0
        return zoom_frame(src, cover * (ZOOM_START + (ZOOM_END - ZOOM_START) * ease_in_out(u)))

    idx = 0
    for frame in range(n_total):
        t = frame / FPS
        while idx < len(layer_start) - 1 and t >= layer_start[idx + 1]:
            idx += 1
        cur = layer_frame(idx, t)
        # 交叉溶解：进入本层后的前 XFADE 秒内与上一层线性混合（等价 xfade=transition=fade）
        if idx > 0 and t - layer_start[idx] < XFADE:
            p = (t - layer_start[idx]) / XFADE
            cur = Image.blend(layer_frame(idx - 1, t), cur, min(1.0, max(0.0, p)))
        pipe.stdin.write(cur.tobytes())
        if frame and frame % 900 == 0:
            print(f"[bg] {frame}/{n_total} 帧 t={t:6.1f}s")
    pipe.stdin.close()
    pipe.wait()
    if pipe.returncode != 0:
        raise SystemExit(f"正文背景合成失败: {pipe.returncode}")
print(f"[bg] {probe_dur(bg):.2f}s (target {body_dur:.2f}s)")


# ------------------------------------------- 2. 常驻书名层 + 字幕层（Pillow）
def spaced_text(im, text, font, top, spacing_em):
    """逐字排布模拟模板 letter-spacing，并加 0 7px 18px rgba(0,0,0,.96) 投影。
    模板里 top 是文字块上沿（line-height:1），故用 anchor='ma'（x 居中 / y=ascender）。"""
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
def find_asset(subdir, want):
    """按「去括号去空格」指纹在 assets/<subdir> 里定位素材。
    BGM 文件名在本仓库历史上全角/半角括号混用过（render_dorian_template_fallback.py
    里写的全角 `Where are you（low）.mp3` 与磁盘上的半角 `(low)` 就对不上），
    硬编码任何一种写法都会在某天突然找不到文件。"""
    d = os.path.join(ASSETS, subdir)
    key = re.sub(r"[()\s（）]", "", want.lower())
    for f in sorted(os.listdir(d)):
        if re.sub(r"[()\s（）]", "", f.lower()) == key:
            return os.path.join(d, f)
    raise SystemExit(f"assets/{subdir} 里找不到素材 {want}（目录内容: {os.listdir(d)}）")


intro_story = find_asset("template-audio", "intro-voiceover-story.mp3")
body_story = os.path.join(AUDIO, "body-voiceover-story.mp3")
bgm = find_asset("bgm", "Where are you(low).mp3")
scroll_sfx = find_asset("sfx", "gear-scroll.mp3")
water_sfx = find_asset("sfx", "water.mp3")
for p in (intro_story, body_story, bgm, scroll_sfx, water_sfx):
    if not os.path.exists(p):
        raise SystemExit(f"缺少音频素材: {p}")
print(f"[audio] bgm={os.path.basename(bgm)} intro={os.path.basename(intro_story)}")

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
out_final = os.path.join(RENDERS, "霍比特人-final.mp4")
ff(["-i", intro_mp4, "-i", body_mp4, "-i", intro_story, "-i", body_story,
    "-stream_loop", "-1", "-i", bgm, "-i", scroll_sfx, "-i", water_sfx,
    "-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", out_final])
print(f"[final] {out_final} {probe_dur(out_final):.2f}s")


# --------------------------------------------------------------------- 5. 封面
cover = Image.open(os.path.join(IMG, "result-bridge.png")).convert("RGB")
cover = cover.resize((1440, 1920), Image.LANCZOS).crop((180, 0, 1260, 1920))
grad = Image.new("L", (1080, 1080), 0)
for y in range(1080):
    ImageDraw.Draw(grad).line([(0, y), (1079, y)], fill=int(200 * (y / 1080) ** 1.6))
mask = Image.new("RGB", (1080, 1080), (0, 0, 0))
cover.paste(mask, (0, 840), grad)
cd = ImageDraw.Draw(cover)
cf = ImageFont.truetype(FONT, 96)
cf_s = ImageFont.truetype(FONT, 40)
cd.text((540, 1430), TITLE_TEXT, font=cf, fill=(255, 255, 255, 255),
        stroke_width=4, stroke=(0, 0, 0, 220), anchor="ma")
cd.text((540, 1540), "J.R.R.托尔金 · 奇幻文学经典", font=cf_s,
        fill=(230, 230, 230, 230), stroke_width=2, stroke=(0, 0, 0, 180), anchor="ma")
cd.text((540, 1620), "真正的勇气，是明明害怕，还是愿意往前走", font=cf_s,
        fill=(220, 220, 220, 220), stroke_width=2, stroke=(0, 0, 0, 160), anchor="ma")
out_cover = os.path.join(RENDERS, "封面-霍比特人.png")
cover.save(out_cover)
print(f"[cover] {out_cover} {cover.size}")

print("\nDONE")
print("final:", out_final)
print("final_dur", final_dur, "body_dur", round(body_dur, 2))
