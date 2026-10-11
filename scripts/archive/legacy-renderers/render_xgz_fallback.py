#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
《小公务员之死》ffmpeg fallback 渲染管线（不依赖 HyperFrames / Chrome）。
背景：4 张 720x960 图按场景分组做 Ken Burns 缓动缩放平移，concat 成 body 背景；
字幕：Pillow 逐句透明 PNG（白字描边 + 半透明圆角底衬），overlay 到背景；
片头：复用 HyperFrames 已渲好的 intro.mp4（1.83s result-bridge 标题卡）；
混音：复用官方 filter_complex（intro story + body story + BGM + 齿轮/水波音效）。
产物：renders/小公务员之死-final.mp4 + renders/封面-小公务员之死.png
幂等可重建：重跑即覆盖。
"""
import json, os, subprocess, math, shutil
from PIL import Image, ImageDraw, ImageFont

ROOT = "/Users/apple/Documents/Obsidian/自媒体/短视频/文学/视频制作"
EP = os.path.join(ROOT, "episodes", "小公务员之死")
IMG = os.path.join(EP, "images")
AUDIO = os.path.join(EP, "audio")
RENDERS = os.path.join(EP, "renders")
ASSETS = os.path.join(ROOT, "assets")
TMP = "/tmp/xgz_fallback"
os.makedirs(TMP, exist_ok=True)
os.makedirs(RENDERS, exist_ok=True)

FPS = 30
FONT = "/System/Library/Fonts/STHeiti Medium.ttc"
BG_W, BG_H = 720, 960
INTRO_TRIM = 1.83
INTRO_OFFSET_MS = 1830

FFMPEG = "ffmpeg"

# ---------- image -> caption scene mapping (dynamic) ----------
# 首句(标题)与末句用 result-bridge，中间句按 4 张氛围图均匀分配，
# 不依赖固定 caption 数量（脚本改动后也不会越界 KeyError）。
IMG_FOR_ORDER = {}

def run(cmd, **kw):
    print("\n$ " + " ".join(str(c) for c in cmd))
    r = subprocess.run(cmd, **kw)
    if r.returncode != 0:
        raise SystemExit(f"FFMPEG/CMD failed: {r.returncode}\n{' '.join(str(c) for c in cmd)}")
    return r

def ff(args):
    run([FFMPEG, "-y", *args], check=True)

# ---------- load timings + script text ----------
timings = json.load(open(os.path.join(AUDIO, "body-timings.json"), encoding="utf-8"))
caps = sorted(timings["captions"], key=lambda c: c["order"])
body_dur = float(timings["duration"])
final_dur = INTRO_TRIM + body_dur

import csv
text_by_order = {}
with open(os.path.join(EP, "script-body.csv"), encoding="utf-8") as f:
    for row in csv.DictReader(f):
        text_by_order[int(row["order"])] = row["text"].strip()

# ---------- 1. body background: per-run zoompan + concat ----------
def zoom_expr():
    # 峰值 zoom 1.42 (>=1.4)，~18s 呼吸周期，±40px 平移
    z = "min(1.2+0.00022*on,1.42)"
    x = "iw/2-(iw/zoom/2)+40*zoom*sin(6.2831853*on/540)"
    y = "ih/2-(ih/zoom/2)+40*zoom*cos(6.2831853*on/540)"
    return f"zoompan=z='{z}':d=__D__:x='{x}':y='{y}':s={BG_W}x{BG_H}:fps={FPS},format=yuv420p"

# group captions into runs of same image
runs = []
cur = None
for c in caps:
    name = IMG_FOR_ORDER[c["order"]]
    if cur is None or cur["name"] != name:
        cur = {"name": name, "start": c["start"], "end": c["end"], "orders": [c["order"]]}
        runs.append(cur)
    else:
        cur["end"] = c["end"]
        cur["orders"].append(c["order"])

# run0 从 0 开始（吸收首句前 ~0.06s 留白），其余 run 用「下一 run 起始」作为本段时长，
# 把 run 边界的静音间隙纳入本段，保证 concat 后视频时间轴与配音时间轴严格对齐。
runs[0]["start"] = 0.0
run_clips = []
for i, r in enumerate(runs):
    nxt_start = runs[i + 1]["start"] if i + 1 < len(runs) else r["end"]
    dur = nxt_start - r["start"]
    d = max(2, round(dur * FPS))
    imgp = os.path.join(IMG, f"{r['name']}.png")
    out = os.path.join(TMP, f"run-{i:02d}.mp4")
    vf = zoom_expr().replace("__D__", str(d))
    ff(["-loop", "1", "-i", imgp, "-vf", vf, "-r", str(FPS), "-t", f"{dur:.4f}", out])
    run_clips.append(out)
    print(f"run {i:02d} {r['name']} dur={dur:.2f}s frames={d}")

# concat runs (re-encode for safety)
listbg = os.path.join(TMP, "list_bg.txt")
with open(listbg, "w") as f:
    for c in run_clips:
        f.write(f"file '{c}'\n")
body_bg = os.path.join(TMP, "body-bg.mp4")
ff(["-f", "concat", "-safe", "0", "-i", listbg, "-c:v", "libx264",
    "-pix_fmt", "yuv420p", "-r", str(FPS), "-crf", "20", "-preset", "medium", body_bg])

# ---------- 2. subtitles: PIL per-caption PNG + concat ----------
def split_lines(s, max_chars=15):
    n = len(s)
    if n <= max_chars:
        return [s]
    if n <= max_chars * 2:
        mid = n // 2
        best = mid
        for off in range(0, 4):
            for j in (mid - off, mid + off):
                if 0 < j < n and s[j] in "，。；：！？、…":
                    best = j + 1
                    break
            else:
                continue
            break
        return [s[:best], s[best:]]
    return [s[:max_chars], s[max_chars:]]

def render_caption_png(text, path):
    # script-body.csv 的 text 已由 md-to-sentence-csv.py 用 DP 断好视觉行（空格分隔，
    # 每行 ≤12 字、≤2 行）。优先采用这些行，避免旧的「按字符数硬切」把
    # 词/句从中间劈开、并让行内留下悬空空格。
    parts = [p for p in text.split(" ") if p]
    if len(parts) >= 2 and max(len(p) for p in parts) <= 15:
        lines = parts
        font_size = 52
    else:
        n = len(text)
        if n > 30:
            font_size = 40
            per = max(15, math.ceil(n / 2))
            lines = split_lines(text, per)
            if len(lines) > 2:
                lines = [text[:per], text[per:]]
        else:
            font_size = 52
            lines = split_lines(text, 15)
    f = ImageFont.truetype(FONT, font_size)
    pad_x = 26; pad_y = 20; line_gap = 14
    lh = font_size + 12
    # measure
    tmp = Image.new("RGBA", (10, 10)); td = ImageDraw.Draw(tmp)
    tw = max(td.textlength(ln, font=f) for ln in lines)
    bw = int(tw) + pad_x * 2
    bh = lh * len(lines) + line_gap * (len(lines) - 1) + pad_y * 2
    im = Image.new("RGBA", (BG_W, BG_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    bx0 = (BG_W - bw) // 2
    by0 = int(BG_H * 0.70) - bh // 2
    d.rounded_rectangle([bx0, by0, bx0 + bw, by0 + bh], radius=22,
                        fill=(0, 0, 0, 150), outline=(255, 255, 255, 60), width=2)
    ty = by0 + pad_y
    for ln in lines:
        d.text((BG_W // 2, ty), ln, font=f, fill=(255, 255, 255, 255),
               stroke_width=3, stroke=(0, 0, 0, 255), anchor="ma")
        ty += lh + line_gap
    im.save(path)

# transparent filler
transparent = os.path.join(TMP, "transparent.png")
Image.new("RGBA", (BG_W, BG_H), (0, 0, 0, 0)).save(transparent)
_img_cache = {}
def load_rgba(p):
    if p not in _img_cache:
        _img_cache[p] = Image.open(p).convert("RGBA")
    return _img_cache[p]

cap_pngs = []
# 零/微时长连接词（如「最后，」「所以，」ASR 切成了 0s）合并进下一句，避免 1 帧闪现
pending = ""
for c in caps:
    o = c["order"]
    t = text_by_order.get(o, "")
    if (c["end"] - c["start"]) < 0.15:
        pending = (pending + " " + t).strip()
        continue
    if pending:
        t = (pending + " " + t).strip()
        pending = ""
    p = os.path.join(TMP, f"cap-{o:03d}.png")
    render_caption_png(t, p)
    cap_pngs.append((o, p, c["start"], c["end"]))
if pending:
    print(f"warn: trailing micro caption dropped: {pending}")

# 字幕轨：按精确时间轴逐帧生成 RGBA（concat demuxer 的 duration 只对末条目生效，已弃用）
entries = []  # (frames, png_path)
prev_end = 0.0
for o, p, s, e in cap_pngs:
    gap = s - prev_end
    if gap > 0.05:
        entries.append((max(1, round(gap * FPS)), transparent))
    entries.append((max(1, round((e - s) * FPS)), p))
    prev_end = e
total_frames = sum(n for n, _ in entries)
body_frames_target = round(body_dur * FPS)
if total_frames < body_frames_target:
    entries.append((body_frames_target - total_frames, transparent))

sub_mov = os.path.join(TMP, "sub.mov")
pipe = subprocess.Popen(
    [FFMPEG, "-y", "-f", "rawvideo", "-pix_fmt", "argb", "-s", f"{BG_W}x{BG_H}",
     "-r", str(FPS), "-i", "pipe:0", "-c:v", "qtrle", "-pix_fmt", "argb", sub_mov],
    stdin=subprocess.PIPE)
for n, p in entries:
    img = load_rgba(p)
    raw = img.tobytes()
    for _ in range(n):
        pipe.stdin.write(raw)
pipe.stdin.close()
pipe.wait()
if pipe.returncode != 0:
    raise SystemExit(f"sub.mov raw pipe failed: {pipe.returncode}")
del _img_cache
print(f"sub frames: {total_frames} = {total_frames/FPS:.2f}s")

# overlay subtitle on body bg
body_mp4 = os.path.join(TMP, "body.mp4")
ff(["-i", body_bg, "-i", sub_mov,
    "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
    "-crf", "20", "-preset", "medium", body_mp4])

# ---------- 3. intro: 复用 HyperFrames 已渲好的 intro.mp4 ----------
# intro.mp4 已在 /tmp/preview-book-xxx/intro/renders/intro.mp4
import glob
intro_candidates = glob.glob(os.path.join(ROOT, "tmp", "preview-book-*/intro/renders/intro.mp4"))
if not intro_candidates:
    raise SystemExit("未找到已渲染的 intro.mp4，请先运行 render-episode-final.mjs")
intro_mp4 = max(intro_candidates, key=os.path.getmtime)
print(f"Reusing intro: {intro_mp4}")

# 验证 intro 时长 ≈ 1.83s
probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", intro_mp4],
                       capture_output=True, text=True)
intro_dur = float(probe.stdout.strip())
print(f"Intro duration: {intro_dur:.2f}s (expected ~1.83s)")

# ---------- 4. mux audio (official filter_complex) ----------
intro_story = os.path.join(ASSETS, "template-audio", "intro-voiceover-story.mp3")
body_story = os.path.join(AUDIO, "body-voiceover-story.mp3")
bgm = os.path.join(ASSETS, "bgm", "Where are you?（low）.mp3")
scroll_sfx = os.path.join(ASSETS, "sfx", "gear-scroll.mp3")
water_sfx = os.path.join(ASSETS, "sfx", "water.mp3")

# 所有文件存在性检查
for f in [intro_story, body_story, bgm, scroll_sfx, water_sfx]:
    if not os.path.exists(f):
        raise SystemExit(f"Missing audio: {f}")

BGM_VOL = 0.32
scroll_start, scroll_end = 1.08, 2.38
scroll_dur = round(scroll_end - scroll_start, 2)
scroll_delay = round(scroll_start * 1000)
scroll_fade_out_st = round(scroll_dur - 0.2, 2)
water_start = round(INTRO_TRIM + 0.32, 2)
water_delay = round(water_start * 1000)
bgm_atrim = round(final_dur - INTRO_TRIM, 2)

fc = [
    f"[0:v]trim=0:{INTRO_TRIM},setpts=PTS-STARTPTS[v0]",
    f"[1:v]trim=0:{body_dur:.4f},setpts=PTS-STARTPTS[v1]",
    "[v0][v1]concat=n=2:v=1:a=0[v]",
    "[2:a]asetpts=PTS-STARTPTS,aresample=48000,volume=1.0[introa]",
    f"[3:a]asetpts=PTS-STARTPTS,aresample=48000,adelay={INTRO_OFFSET_MS}|{INTRO_OFFSET_MS},volume=1.0[bodya]",
    f"[4:a]atrim=0:{bgm_atrim:.4f},asetpts=PTS-STARTPTS,aresample=48000,adelay={INTRO_OFFSET_MS}|{INTRO_OFFSET_MS},volume={BGM_VOL},afade=t=in:st=0:d=0.5[bgm]",
    f"[5:a]atrim=0:{scroll_dur},asetpts=PTS-STARTPTS,aresample=48000,volume=1.4,afade=t=in:st=0:d=0.01,afade=t=out:st={scroll_fade_out_st}:d=0.2,adelay={scroll_delay}|{scroll_delay}[scrollsfx]",
    f"[6:a]atrim=0:1.06,asetpts=PTS-STARTPTS,aresample=48000,volume=1.0,adelay={water_delay}|{water_delay}[watersfx]",
    "[introa][bodya][bgm][scrollsfx][watersfx]amix=inputs=5:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.95,loudnorm=I=-14.0:TP=-1.0:LRA=7.0,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a]",
]
out_final = os.path.join(RENDERS, "小公务员之死-final.mp4")
ff(["-i", intro_mp4, "-i", body_mp4, "-i", intro_story, "-i", body_story,
    "-stream_loop", "-1", "-i", bgm, "-i", scroll_sfx, "-i", water_sfx,
    "-filter_complex", ";".join(fc),
    "-map", "[v]", "-map", "[a]",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high",
    "-level", "4.1", "-c:a", "aac", "-b:a", "192k",
    "-movflags", "+faststart", "-shortest", out_final])

# ---------- 5. 封面：1080x1920 竖版 ----------
# 使用 result-bridge 背景 + 标题 + 作者
cover_src = os.path.join(IMG, "result-bridge.png")
cover_out = os.path.join(RENDERS, "封面-小公务员之死.png")
cover_im = Image.open(cover_src).convert("RGBA")
# resize to 1080x1920
cover_im = cover_im.resize((1080, 1920), Image.LANCZOS)
d = ImageDraw.Draw(cover_im)
# 深色遮罩
d.rectangle([0, 0, 1080, 1920], fill=(0, 0, 0, 120))
# 标题
title_font = ImageFont.truetype(FONT, 100)
author_font = ImageFont.truetype(FONT, 36)
# 标题位置：上半部
d.text((540, 700), "《小公务员之死》", font=title_font,
       fill=(255, 255, 255, 255), stroke_width=4, stroke=(0, 0, 0, 230), anchor="ma")
# 作者
d.text((540, 820), "契诃夫", font=author_font,
       fill=(235, 235, 235, 220), stroke_width=2, stroke=(0, 0, 0, 200), anchor="ma")
# 底部标签
d.text((540, 1800), "读书视频 · 文学经典系列", font=ImageFont.truetype(FONT, 28),
       fill=(200, 200, 200, 200), anchor="ma")
cover_im.save(cover_out)
print(f"Cover saved: {cover_out}")

print(f"\n✅ Done! Final video: {out_final}")
