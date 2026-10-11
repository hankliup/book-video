#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
《霍比特人》ffmpeg fallback 渲染管线（HyperFrames 浏览器渲染故障时的兜底）。
基于 render_jingzhiniao_fallback.py 适配：读 ts-image-map.json（TypeSafe 选型结果），
95 句字幕稿 + word-level LCS 时间轴。
"""
import json, os, subprocess, math, shutil
from PIL import Image, ImageDraw, ImageFont

ROOT = "/Users/apple/Documents/Obsidian/自媒体/短视频/文学/视频制作"
EP = os.path.join(ROOT, "episodes", "霍比特人")
IMG = os.path.join(EP, "images")
AUDIO = os.path.join(EP, "audio")
RENDERS = os.path.join(EP, "renders")
ASSETS = os.path.join(ROOT, "assets")
TMP = "/tmp/hobbit_fallback"
os.makedirs(TMP, exist_ok=True)
os.makedirs(RENDERS, exist_ok=True)

FPS = 30
FONT = "/System/Library/Fonts/STHeiti Medium.ttc"
BG_W, BG_H = 720, 960
INTRO_TRIM = 1.83
INTRO_OFFSET_MS = 1830
FFMPEG = "ffmpeg"

# ---------- image -> caption scene mapping (ts-image-map.json, 手写兜底) ----------
IMG_FOR_ORDER = {}
def fill(a, b, name):
    for o in range(a, b + 1):
        IMG_FOR_ORDER[o] = name

_TS_MAP_PATH = os.path.join(EP, "ts-image-map.json")
if os.path.exists(_TS_MAP_PATH):
    with open(_TS_MAP_PATH, encoding="utf-8") as _f:
        _ts_map = json.load(_f)
    for _o, _img in _ts_map.get("order_map", {}).items():
        IMG_FOR_ORDER[int(_o)] = _img
    print(f"[image-map] using ts-image-map.json (source={_ts_map.get('source')}, {len(IMG_FOR_ORDER)} orders)")
else:
    fill(1, 1, "result-bridge")
    fill(2, 9, "atmosphere-1")
    fill(10, 19, "atmosphere-3")
    fill(20, 31, "atmosphere-2")
    fill(32, 45, "atmosphere-3")
    fill(46, 58, "atmosphere-2")
    fill(59, 72, "atmosphere-1")
    fill(73, 84, "atmosphere-3")
    fill(85, 95, "atmosphere-1")
    print("[image-map] ts-image-map.json 不存在，使用手写默认映射")

def run(cmd, **kw):
    print("\n$ " + " ".join(str(c) for c in cmd))
    r = subprocess.run(cmd, **kw)
    if r.returncode != 0:
        raise SystemExit(f"CMD failed: {r.returncode}\n{' '.join(str(c) for c in cmd)}")
    return r

def ff(args):
    run([FFMPEG, "-y", *args])

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
    z = "min(1.2+0.00022*on,1.42)"
    x = "iw/2-(iw/zoom/2)+40*zoom*sin(6.2831853*on/540)"
    y = "ih/2-(ih/zoom/2)+40*zoom*cos(6.2831853*on/540)"
    return f"zoompan=z='{z}':d=__D__:x='{x}':y='{y}':s={BG_W}x{BG_H}:fps={FPS},format=yuv420p"

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

listbg = os.path.join(TMP, "list_bg.txt")
with open(listbg, "w") as f:
    for c in run_clips:
        f.write(f"file '{c}'\n")
body_bg = os.path.join(TMP, "body-bg.mp4")
ff(["-f", "concat", "-safe", "0", "-i", listbg, "-c:v", "libx264",
    "-pix_fmt", "yuv420p", "-r", str(FPS), "-crf", "20", "-preset", "medium", body_bg])

# ---------- 2. subtitles: PIL per-caption PNG ----------
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

transparent = os.path.join(TMP, "transparent.png")
Image.new("RGBA", (BG_W, BG_H), (0, 0, 0, 0)).save(transparent)
_img_cache = {}
def load_rgba(p):
    if p not in _img_cache:
        _img_cache[p] = Image.open(p).convert("RGBA")
    return _img_cache[p]

cap_pngs = []
for c in caps:
    o = c["order"]
    p = os.path.join(TMP, f"cap-{o:03d}.png")
    render_caption_png(text_by_order.get(o, ""), p)
    cap_pngs.append((o, p, c["start"], c["end"]))

entries = []
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

body_mp4 = os.path.join(TMP, "body.mp4")
ff(["-i", body_bg, "-i", sub_mov,
    "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
    "-crf", "20", "-preset", "medium", body_mp4])

# ---------- 3. intro: result-bridge 1.83s title card ----------
intro_bg = os.path.join(TMP, "intro-bg.mp4")
d0 = round(INTRO_TRIM * FPS)
vf0 = (f"zoompan=z='min(1.12+0.0006*on,1.22)':d={d0}"
       f":x='iw/2-(iw/zoom/2)+18*zoom*sin(6.2831853*on/540)'"
       f":y='ih/2-(ih/zoom/2)+18*zoom*cos(6.2831853*on/540)'"
       f":s={BG_W}x{BG_H}:fps={FPS},format=yuv420p")
ff(["-loop", "1", "-i", os.path.join(IMG, "result-bridge.png"), "-vf", vf0,
    "-r", str(FPS), "-t", f"{INTRO_TRIM:.4f}", intro_bg])

title_im = Image.new("RGBA", (BG_W, BG_H), (0, 0, 0, 0))
td = ImageDraw.Draw(title_im)
big = ImageFont.truetype(FONT, 110)
small = ImageFont.truetype(FONT, 40)
ty = int(BG_H * 0.40)
td.text((BG_W // 2, ty), "霍比特人", font=big,
        fill=(255, 255, 255, 255), stroke_width=4, stroke=(0, 0, 0, 230), anchor="ma")
td.text((BG_W // 2, ty + 100), "J.R.R.托尔金", font=small,
        fill=(235, 235, 235, 220), stroke_width=2, stroke=(0, 0, 0, 200), anchor="ma")
title_png = os.path.join(TMP, "intro-title.png")
title_im.save(title_png)

intro_mp4 = os.path.join(TMP, "intro.mp4")
ff(["-i", intro_bg, "-i", title_png,
    "-filter_complex", "[0:v][1:v]overlay=0:0,fade=t=in:st=0:d=0.5",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
    "-crf", "20", "-preset", "medium", intro_mp4])

# ---------- 4. mux audio ----------
intro_story = os.path.join(TMP, "intro-voiceover-story.mp3")
if not os.path.exists(intro_story):
    run(["node", "scripts/process-voiceover.mjs",
         os.path.join(ASSETS, "template-audio", "intro-voiceover.mp3"),
         intro_story, "story"], cwd=ROOT)
body_story = os.path.join(AUDIO, "body-voiceover-story.mp3")
if not os.path.exists(body_story):
    run(["node", "scripts/process-voiceover.mjs",
         os.path.join(AUDIO, "body-voiceover.mp3"),
         body_story, "story"], cwd=ROOT)
bgm = os.path.join(ASSETS, "bgm", "Where are you（low）.mp3")
scroll_sfx = os.path.join(ASSETS, "sfx", "gear-scroll.mp3")
water_sfx = os.path.join(ASSETS, "sfx", "water.mp3")

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
out_final = os.path.join(RENDERS, "霍比特人-final.mp4")
ff(["-i", intro_mp4, "-i", body_mp4, "-i", intro_story, "-i", body_story,
    "-stream_loop", "-1", "-i", bgm, "-i", scroll_sfx, "-i", water_sfx,
    "-filter_complex", ";".join(fc),
    "-map", "[v]", "-map", "[a]",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high",
    "-level", "4.1", "-c:a", "aac", "-b:a", "192k",
    "-movflags", "+faststart", out_final])

# ---------- 5. cover ----------
cover = Image.open(os.path.join(IMG, "result-bridge.png")).convert("RGB")
cover = cover.resize((1440, 1920), Image.LANCZOS).crop((180, 0, 1260, 1920))
gd = ImageDraw.Draw(cover)
grad = Image.new("L", (1080, 1080), 0)
for y in range(1080):
    ImageDraw.Draw(grad).line([(0, y), (1079, y)], fill=int(200 * (y / 1080) ** 1.6))
mask = Image.new("RGB", (1080, 1080), (0, 0, 0))
cover.paste(mask, (0, 840), grad.crop((0, 0, 1080, 1080)))
cd = ImageDraw.Draw(cover)
cf = ImageFont.truetype(FONT, 96)
cf_s = ImageFont.truetype(FONT, 40)
cd.text((540, 1430), "霍比特人", font=cf, fill=(255, 255, 255, 255),
        stroke_width=4, stroke=(0, 0, 0, 220), anchor="ma")
cd.text((540, 1540), "J.R.R.托尔金 · 奇幻文学经典", font=cf_s,
        fill=(230, 230, 230, 230), stroke_width=2, stroke=(0, 0, 0, 180), anchor="ma")
cd.text((540, 1620), "真正的勇气，是明明害怕，还是愿意往前走", font=cf_s,
        fill=(220, 220, 220, 220), stroke_width=2, stroke=(0, 0, 0, 160), anchor="ma")
cover.save(os.path.join(RENDERS, "封面-霍比特人.png"))

print("\nDONE")
print("final:", out_final)
print("final_dur", round(final_dur, 2), "body_dur", round(body_dur, 2))
