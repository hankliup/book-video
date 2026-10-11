#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
《少年维特的烦恼》V-B ffmpeg fallback 渲染管线（不依赖 HyperFrames / Chrome）。
改编自 render_jingzhiniao_fallback.py：
- 7 张 720x960 候选图（result-bridge + atmosphere-1..6）按 ts-image-map.json（TypeSafe Jev 剧情段重选）
  或内置手动映射做 Ken Burns 缓动缩放平移，concat 成 body 背景；
- 字幕：Pillow 逐句透明 PNG（白字描边 + 半透明圆角底衬），逐帧 RGBA pipe 烘字幕轨；
- 片头：result-bridge 标题卡 1.83s（与正文首帧同为 result-bridge，消除闪屏）；
- 混音：官方 filter_complex（intro story + body story + BGM + 齿轮/水波音效）。
产物：renders/少年维特的烦恼-final.mp4 + renders/封面-少年维特的烦恼.png
幂等可重建：重跑即覆盖。
"""
import json, os, subprocess, math, shutil, glob, csv
from PIL import Image, ImageDraw, ImageFont

ROOT = "/Users/apple/Documents/Obsidian/自媒体/短视频/文学/视频制作"
EP = os.path.join(ROOT, "episodes", "少年维特的烦恼")
IMG = os.path.join(EP, "images")
AUDIO = os.path.join(EP, "audio")
RENDERS = os.path.join(EP, "renders")
ASSETS = os.path.join(ROOT, "assets")
TMP = os.path.join(ROOT, "tmp", "witte_fallback")
os.makedirs(TMP, exist_ok=True)
os.makedirs(RENDERS, exist_ok=True)

FPS = 30
FONT = "/System/Library/Fonts/STHeiti Medium.ttc"
BG_W, BG_H = 720, 960
INTRO_TRIM = 1.83
INTRO_OFFSET_MS = 1830
TITLE = "《少年维特的烦恼》"
AUTHOR_SUB = "[德] 歌德 · 文学经典"
FFMPEG = "ffmpeg"

# ---------- image -> caption scene mapping (56 captions, order 1..56) ----------
IMG_FOR_ORDER = {}
def fill(a, b, name):
    for o in range(a, b + 1):
        IMG_FOR_ORDER[o] = name

# 手动兜底映射（ts-image-map.json 缺失时使用）；按 V-B 叙事段分配
fill(1, 1, "result-bridge")      # 标题卡
fill(2, 4, "atmosphere-3")      # 共鸣开头：明知没有结果仍靠近（内心独白/写信）
fill(5, 9, "atmosphere-1")       # 维特来到乡间，遇到绿蒂
fill(10, 21, "atmosphere-5")    # 痛苦与精神困境
fill(22, 27, "atmosphere-5")    # 维特的问题：过于敏感投入依赖内心
fill(28, 37, "atmosphere-6")    # 强烈≠深刻；失去后继续过下去
fill(38, 43, "atmosphere-6")    # 全部意义寄托于一种情感
fill(44, 51, "atmosphere-6")    # 歌德的提醒：保留清醒
fill(52, 55, "atmosphere-6")    # 真正的成长：继续向前走
fill(56, 56, "result-bridge")   # 结尾金句

# 若 TypeSafe 重选过配图，覆盖手动映射（幂等）
_ts_map = os.path.join(EP, "ts-image-map.json")
if os.path.exists(_ts_map):
    try:
        _om = json.load(open(_ts_map, encoding="utf-8")).get("order_map", {})
        for _o, _n in _om.items():
            IMG_FOR_ORDER[int(_o)] = _n
        print(f"[image-map] 使用 ts-image-map.json（{len(_om)} 句覆盖手动映射, source={json.load(open(_ts_map, encoding='utf-8')).get('source')}）")
    except Exception as e:
        print(f"[image-map] ts-image-map 读取失败，回退手动映射: {e}")

def run(cmd, **kw):
    print("\n$ " + " ".join(str(c) for c in cmd))
    r = subprocess.run(cmd, **kw)
    if r.returncode != 0:
        raise SystemExit(f"FFMPEG/CMD failed: {r.returncode}\n{' '.join(str(c) for c in cmd)}")
    return r

def ff(args, check=True):
    run([FFMPEG, "-y", *args], check=check)

# ---------- load timings + script text (V-B) ----------
timings = json.load(open(os.path.join(AUDIO, "body-timings.json"), encoding="utf-8"))
caps = sorted(timings["captions"], key=lambda c: c["order"])
body_dur = float(timings["duration"])
final_dur = INTRO_TRIM + body_dur

text_by_order = {}
with open(os.path.join(EP, "script-body.csv"), encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        if row.get("version") == "B":
            text_by_order[int(row["order"])] = row["text"].strip()

# ---------- 校验 image->caption 映射覆盖完整且素材存在 ----------
_missing = [o for o in range(1, len(caps) + 1) if o not in IMG_FOR_ORDER]
if _missing:
    raise SystemExit(f"IMG_FOR_ORDER 缺少 order: {sorted(set(_missing))}")
for _n in sorted(set(IMG_FOR_ORDER.values())):
    _p = os.path.join(IMG, f"{_n}.png")
    if not os.path.exists(_p):
        raise SystemExit(f"缺少配图素材: {_p}")

INTRO_OUT = os.path.join(TMP, "intro.mov")
def gen_intro():
    base = Image.open(os.path.join(IMG, "result-bridge.png")).convert("RGBA").resize((BG_W, BG_H), Image.LANCZOS)
    title_font = ImageFont.truetype(FONT, 64)
    sub_font = ImageFont.truetype(FONT, 30)
    total = round(INTRO_TRIM * FPS)
    fade_in = round(0.5 * FPS)
    fade_out_start = round(1.43 * FPS)
    pipe = subprocess.Popen(
        [FFMPEG, "-y", "-f", "rawvideo", "-pix_fmt", "argb", "-s", f"{BG_W}x{BG_H}",
         "-r", str(FPS), "-i", "pipe:0", "-c:v", "qtrle", "-pix_fmt", "argb", INTRO_OUT],
        stdin=subprocess.PIPE)
    for fi in range(total):
        a = 1.0
        if fi < fade_in:
            a = fi / fade_in
        elif fi > fade_out_start:
            a = max(0.0, (total - fi) / (total - fade_out_start))
        im = base.copy()
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, BG_W, BG_H], fill=(0, 0, 0, int(110 * a)))
        ta = int(255 * a)
        d.text((BG_W // 2, int(BG_H * 0.42)), TITLE, font=title_font,
               fill=(255, 255, 255, ta), stroke_width=4, stroke=(0, 0, 0, int(230 * a)), anchor="ma")
        d.text((BG_W // 2, int(BG_H * 0.42) + 78), AUTHOR_SUB, font=sub_font,
               fill=(235, 235, 235, int(220 * a)), stroke_width=2, stroke=(0, 0, 0, int(200 * a)), anchor="ma")
        pipe.stdin.write(im.tobytes())
    pipe.stdin.close()
    pipe.wait()
    if pipe.returncode != 0:
        raise SystemExit(f"intro gen failed: {pipe.returncode}")
    print(f"Intro saved: {INTRO_OUT} ({total} frames)")

if not os.path.exists(INTRO_OUT) or os.path.getsize(INTRO_OUT) == 0:
    gen_intro()
else:
    print(f"Reusing intro: {INTRO_OUT}")

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

# ---------- 2. subtitles: PIL per-caption PNG + 逐帧 RGBA pipe ----------
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

# ---------- 3. process body voiceover with story preset ----------
body_voice = os.path.join(AUDIO, "body-voiceover.mp3")
body_story = os.path.join(AUDIO, "body-voiceover-story.mp3")
run(["node", os.path.join(ROOT, "scripts", "process-voiceover.mjs"), body_voice, body_story, "story"])

# ---------- 4. mux audio (official filter_complex) ----------
intro_story = os.path.join(ASSETS, "template-audio", "intro-voiceover-story.mp3")
bgm_dir = os.path.join(ASSETS, "bgm")
bgm_files = [f for f in os.listdir(bgm_dir) if f.endswith(".mp3")]
bgm = os.path.join(bgm_dir, sorted(bgm_files)[0])
print(f"Using BGM: {os.path.basename(bgm)}")
scroll_sfx = os.path.join(ASSETS, "sfx", "gear-scroll.mp3")
water_sfx = os.path.join(ASSETS, "sfx", "water.mp3")

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
    f"[4:a]atrim=0:{bgm_atrim:.4f},asetpts=PTS-STARTPTS,aresample=48000,adelay={INTRO_OFFSET_MS}|{INTRO_OFFSET_MS},volume={BGM_VOL},afade=t=in:st=0:d=0.5,afade=t=out:st={final_dur-0.8:.2f}:d=0.8[bgm]",
    f"[5:a]atrim=0:{scroll_dur},asetpts=PTS-STARTPTS,aresample=48000,volume=1.4,afade=t=in:st=0:d=0.01,afade=t=out:st={scroll_fade_out_st}:d=0.2,adelay={scroll_delay}|{scroll_delay}[scrollsfx]",
    f"[6:a]atrim=0:1.06,asetpts=PTS-STARTPTS,aresample=48000,volume=1.0,adelay={water_delay}|{water_delay}[watersfx]",
    "[introa][bodya][bgm][scrollsfx][watersfx]amix=inputs=5:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.95,loudnorm=I=-14.0:TP=-1.0:LRA=7.0,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a]",
]
out_candidate = os.path.join(TMP, "witte-final-候选.mp4")
ff(["-i", INTRO_OUT, "-i", body_mp4, "-i", intro_story, "-i", body_story,
    "-stream_loop", "-1", "-i", bgm, "-i", scroll_sfx, "-i", water_sfx,
    "-filter_complex", ";".join(fc),
    "-map", "[v]", "-map", "[a]",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high",
    "-level", "4.1", "-c:a", "aac", "-b:a", "192k",
    "-movflags", "+faststart", "-shortest", out_candidate])

# ---------- 技术验收：720x960、时长>0、含音频流、时长与计划一致 ----------
probe_proc = subprocess.run(
    ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", out_candidate],
    capture_output=True, text=True)
if probe_proc.returncode != 0:
    raise SystemExit(f"ffprobe 失败: {probe_proc.stderr}")
probe = json.loads(probe_proc.stdout)
v = next((s for s in probe["streams"] if s["codec_type"] == "video"), {})
a = next((s for s in probe["streams"] if s["codec_type"] == "audio"), None)
dur = float(probe["format"]["duration"])
assert v["width"] == 720 and v["height"] == 960, f"分辨率错误: {v}"
assert a, "缺少音频流"
assert abs(dur - final_dur) < 1.0, f"时长异常: {dur} vs 计划 {final_dur}"
print(f"验收通过: 720x960, {dur:.2f}s, 音频流={a['codec_name']}")

# 激活：覆盖 renders/ 下同名成片
out_final = os.path.join(RENDERS, "少年维特的烦恼-final.mp4")
shutil.move(out_candidate, out_final)

# ---------- 5. 封面：1080x1920 竖版 ----------
cover_src = os.path.join(IMG, "result-bridge.png")
cover_out = os.path.join(RENDERS, "封面-少年维特的烦恼.png")
cover_im = Image.open(cover_src).convert("RGBA").resize((1080, 1920), Image.LANCZOS)
d = ImageDraw.Draw(cover_im)
d.rectangle([0, 0, 1080, 1920], fill=(0, 0, 0, 120))
title_font = ImageFont.truetype(FONT, 88)
author_font = ImageFont.truetype(FONT, 36)
d.text((540, 760), "《少年维特的烦恼》", font=title_font,
       fill=(255, 255, 255, 255), stroke_width=4, stroke=(0, 0, 0, 230), anchor="ma")
d.text((540, 880), "[德] 歌德", font=author_font,
       fill=(255, 255, 255, 220), stroke_width=2, stroke=(0, 0, 0, 200), anchor="ma")
d.text((540, 1800), "读书视频 · 文学经典系列", font=ImageFont.truetype(FONT, 28),
       fill=(200, 200, 200, 200), anchor="ma")
cover_im.save(cover_out)
print(f"Cover saved: {cover_out}")

print(f"\n✅ Done! Final video: {out_final}")
