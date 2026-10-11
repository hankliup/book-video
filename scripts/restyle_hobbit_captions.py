# -*- coding: utf-8 -*-
"""Re-overlay Hobbit body captions without card UI, plus persistent title/author."""
import csv, json, math, os, subprocess
from PIL import Image, ImageDraw, ImageFont

ROOT = "/Users/apple/Documents/Obsidian/自媒体/短视频/文学/视频制作"
EP = os.path.join(ROOT, "episodes", "霍比特人")
TMP = "/tmp/hobbit_fallback"
FPS = 30
FONT = "/System/Library/Fonts/STHeiti Medium.ttc"
BG_W, BG_H = 720, 960
FFMPEG = "ffmpeg"

def ff(args):
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *args]
    r = subprocess.run(cmd)
    if r.returncode != 0:
        raise SystemExit(f"ffmpeg failed: {cmd}")

def parse_csv_line(line):
    return next(csv.reader([line]))

rows = []
with open(os.path.join(EP, "script-body.csv"), encoding="utf-8") as f:
    lines = f.read().splitlines()
headers = parse_csv_line(lines[0])
for line in lines[1:]:
    if not line.strip():
        continue
    rec = dict(zip(headers, parse_csv_line(line)))
    if rec.get("version") == "A":
        rows.append(rec)
text_by_order = {int(r["order"]): r["text"] for r in rows}

timings = json.load(open(os.path.join(EP, "audio", "body-timings.json"), encoding="utf-8"))
caps = sorted(timings["captions"], key=lambda c: int(c["order"]))
body_dur = float(timings["duration"])

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
                    return [s[: j + 1], s[j + 1 :]]
        return [s[:best], s[best:]]
    return [s[:max_chars], s[max_chars:]]

def draw_shadow_text(d, xy, text, font, fill, stroke_w=4):
    d.text(xy, text, font=font, fill=fill, stroke_width=stroke_w, stroke=(0, 0, 0, 255), anchor="ma")

def render_caption_png(text, path):
    parts = [p for p in text.split(" ") if p]
    if len(parts) >= 2 and max(len(p) for p in parts) <= 15:
        lines = parts
        font_size = 52
    else:
        n = len(text)
        if n > 30:
            font_size = 44
            per = max(15, math.ceil(n / 2))
            lines = split_lines(text, per)
            if len(lines) > 2:
                lines = [text[:per], text[per:]]
        else:
            font_size = 52
            lines = split_lines(text, 15)
    f = ImageFont.truetype(FONT, font_size)
    lh = font_size + 10
    line_gap = 8
    im = Image.new("RGBA", (BG_W, BG_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    block_h = lh * len(lines) + line_gap * (len(lines) - 1)
    # CSS caption bottom: 126px → block sits above that
    ty = BG_H - 126 - block_h + lh // 2
    for ln in lines:
        draw_shadow_text(d, (BG_W // 2, ty), ln, f, (255, 255, 255, 255), 4)
        ty += lh + line_gap
    im.save(path)

# persistent title / author
title_im = Image.new("RGBA", (BG_W, BG_H), (0, 0, 0, 0))
td = ImageDraw.Draw(title_im)
title_font = ImageFont.truetype(FONT, 82)
author_font = ImageFont.truetype(FONT, 34)
draw_shadow_text(td, (BG_W // 2, 70 + 41), "《霍比特人》", title_font, (255, 255, 255, 255), 4)
draw_shadow_text(td, (BG_W // 2, 166 + 17), "J.R.R.托尔金 / 著", author_font, (255, 255, 255, 255), 3)
title_png = os.path.join(TMP, "title-persistent.png")
title_im.save(title_png)

transparent = os.path.join(TMP, "transparent.png")
Image.new("RGBA", (BG_W, BG_H), (0, 0, 0, 0)).save(transparent)

cap_pngs = []
for c in caps:
    o = int(c["order"])
    p = os.path.join(TMP, f"cap-plain-{o:03d}.png")
    render_caption_png(text_by_order.get(o, ""), p)
    cap_pngs.append((o, p, float(c["start"]), float(c["end"])))

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
    total_frames = body_frames_target

_img_cache = {}
def load_rgba(p):
    if p not in _img_cache:
        _img_cache[p] = Image.open(p).convert("RGBA")
    return _img_cache[p]

sub_mov = os.path.join(TMP, "sub-plain.mov")
pipe = subprocess.Popen(
    [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
     "-f", "rawvideo", "-pix_fmt", "argb", "-s", f"{BG_W}x{BG_H}",
     "-r", str(FPS), "-i", "pipe:0", "-c:v", "qtrle", "-pix_fmt", "argb", sub_mov],
    stdin=subprocess.PIPE,
)
for n, p in entries:
    raw = load_rgba(p).tobytes()
    for _ in range(n):
        pipe.stdin.write(raw)
pipe.stdin.close()
rc = pipe.wait()
if rc != 0:
    raise SystemExit(f"sub-plain.mov failed: {rc}")
del _img_cache
print(f"sub frames: {total_frames} = {total_frames / FPS:.2f}s")

body_bg = os.path.join(TMP, "body-bg.mp4")
body_mp4 = os.path.join(TMP, "body-plain.mp4")
ff([
    "-i", body_bg,
    "-i", sub_mov,
    "-loop", "1", "-i", title_png,
    "-filter_complex",
    "[0:v][1:v]overlay=0:0:format=auto[v1];[v1][2:v]overlay=0:0:format=auto,trim=0:%.4f,setpts=PTS-STARTPTS" % body_dur,
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
    "-crf", "20", "-preset", "medium", "-an", body_mp4,
])
print("body-plain:", body_mp4)
