#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抽帧核对成片字幕是否跟着配音走（防止「画面卡在正文开头」那类分段渲染错位）。

背景：HyperFrames 分段渲染的成片里，运行时只认「创作时」写入的字幕时间，事后
tl.shiftChildren() 被忽略，导致每段都在重播正文开头 —— 配音完整播完，画面和字幕
却停在第 1、2 行。这类错位靠听是听不出来的，必须抽帧比对。

做法：按给定的正文秒数（或自动均匀取样），用 body-timings.json 反查该时刻「应该」
显示 script-body.csv 哪一行，再从成片（加片头偏移）抽出实际帧，拼成带标注的对比图。
人工看一张图即可判断是否对齐，也可据此确认场景切换是否同步。

用法：
  python3 scripts/qc-caption-align.py <episode> <final.mp4> [秒数...]
      [--intro 3.0] [--cols 4] [--out tmp/qc/xxx.png] [--samples 12]

  例：python3 scripts/qc-caption-align.py 霍比特人 episodes/霍比特人/renders/霍比特人-final.mp4 \
        12 45 90 130 170 210 250 --intro 3.0
"""
import csv
import json
import os
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TW, TH = 260, 347  # 缩略图尺寸（保持 720x960 比例）


def parse_args(argv):
    opts = {"intro": 3.0, "cols": 4, "out": None, "samples": None}
    pos = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--intro":
            opts["intro"] = float(argv[i + 1]); i += 2
        elif a == "--cols":
            opts["cols"] = int(argv[i + 1]); i += 2
        elif a == "--out":
            opts["out"] = argv[i + 1]; i += 2
        elif a == "--samples":
            opts["samples"] = int(argv[i + 1]); i += 2
        else:
            pos.append(a); i += 1
    if len(pos) < 2:
        sys.exit(__doc__)
    return pos, opts


pos, opts = parse_args(sys.argv[1:])
ep_arg, video = pos[0], pos[1]
ep = ep_arg if os.path.isdir(ep_arg) else os.path.join(ROOT, "episodes", ep_arg)
times = [float(x) for x in pos[2:]]

timings = json.load(open(os.path.join(ep, "audio", "body-timings.json"), encoding="utf-8"))
caps = sorted(timings["captions"], key=lambda c: int(c["order"]))
body_dur = float(timings["duration"])
texts = {}
with open(os.path.join(ep, "script-body.csv"), encoding="utf-8") as f:
    for row in csv.DictReader(f):
        texts[int(row["order"])] = row["text"].strip().replace(" ", "")

if not times:
    if not opts["samples"]:
        sys.exit("未给秒数时请用 --samples N 指定均匀取样数")
    times = [round(body_dur * (i + 1) / (opts["samples"] + 1), 2) for i in range(opts["samples"])]

out = opts["out"] or os.path.join(ROOT, "tmp", "qc-caption-align.png")
os.makedirs(os.path.dirname(out), exist_ok=True)
work = out + ".frames"
os.makedirs(work, exist_ok=True)

FONT = "/System/Library/Fonts/STHeiti Medium.ttc"
f_lab = ImageFont.truetype(FONT, 17)
f_exp = ImageFont.truetype(FONT, 15)


def expected_at(t):
    """该秒应显示的字幕行；落在句间静音时返回最近的前一行并标注。"""
    for c in caps:
        if float(c["start"]) <= t <= float(c["end"]):
            o = int(c["order"])
            return o, texts.get(o, ""), ""
    prior = [c for c in caps if float(c["end"]) < t]
    if not prior:
        return 0, "(正文开始前)", ""
    o = int(prior[-1]["order"])
    gap = t - float(prior[-1]["end"])
    return o, texts.get(o, ""), f"（句间静音 {gap:.2f}s）"


cols = min(opts["cols"], max(1, len(times)))
rows = (len(times) + cols - 1) // cols
LAB_H = 62
sheet = Image.new("RGB", (cols * (TW + 12) + 12, rows * (TH + LAB_H + 16) + 12), (16, 16, 16))
draw = ImageDraw.Draw(sheet)

print(f"{'正文秒':>8} {'成片秒':>8}  order  期望字幕")
for i, t in enumerate(times):
    vt = t + opts["intro"]
    o, exp, note = expected_at(t)
    fp = os.path.join(work, f"f{i:02d}.png")
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", f"{vt:.3f}", "-i", video, "-frames:v", "1", fp],
                       capture_output=True)
    if r.returncode != 0 or not os.path.exists(fp):
        print(f"  ⚠️ {vt:.2f}s 抽帧失败: {r.stderr.decode()[-200:]}")
        continue
    col, row = i % cols, i // cols
    x = 12 + col * (TW + 12)
    y = 12 + row * (TH + LAB_H + 16)
    sheet.paste(Image.open(fp).convert("RGB").resize((TW, TH), Image.LANCZOS), (x, y))
    lab = f"body {t:.1f}s / video {vt:.1f}s  → order {o}"
    draw.text((x + 2, y + TH + 2), lab, font=f_lab, fill=(255, 235, 150))
    draw.text((x + 2, y + TH + 24), f"期望: {exp[:22]}{note}", font=f_exp, fill=(200, 220, 255))
    print(f"{t:8.2f} {vt:8.2f} {o:>6}  {exp}{note}")

sheet.save(out)
print(f"\n对比图 → {out}")
print("核对方法：每格画面底部的字幕，应与该格「期望」文字一致；若多格重复同一行"
      "（尤其停在 order 1-2），即为音画错位。")
