#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""成片验收：字幕落点抽帧 + 配音对齐量化（浮点合成/兜底渲染通用）。

两件事：
  1) 抽帧核对：按 --times 给定的秒数（默认在若干场景中段）抓帧，把该时刻字幕轨
     应当显示的文本网格化标到图上，输出一张带标注的 contact sheet，供人工/视觉核对。
  2) 音画对齐量化：把成片音轨按「成片秒 = 正文秒 + intro_trim」切片，与原始配音同区间
     做归一化互相关，得到相对源音频的时延与相关度。分段渲染错位（字幕卡在正文开头、
     或音频被整体平移）会直接体现在相关峰偏移上。

用法：
  python3 scripts/qc-final-av.py <final.mp4> <caption-track.json> \
      [--intro-trim 3.0] [--voice audio/body-voiceover.mp3] \
      [--times 15,48,91,123,163,218,265] [--out tmp/qc.png] [--no-audio]
"""
import argparse
import json
import math
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 720, 960


def grab(video, times, out_dir="tmp/qc-frames"):
    import os
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for t in times:
        p = f"{out_dir}/t{t:07.2f}.png"
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", str(t), "-i", video, "-frames:v", "1", p], check=True)
        paths.append((t, p))
    return paths


def expected_caption(track, t):
    for item in track:
        if item["start"] <= t <= item["end"]:
            return item["text"], item["order"]
    return "(无字幕)", None


def sheet(pairs, track, intro_trim, out_png, thumb_w=270):
    th = int(thumb_w * H / W)
    head = 56
    cols = min(len(pairs), 4) or 1
    rows = (len(pairs) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * thumb_w, rows * (th + head)), (16, 16, 16))
    dr = ImageDraw.Draw(canvas)
    try:
        f_big = ImageFont.truetype("/System/Library/Fonts/STHeiti Medium.ttc", 20)
        f_sm = ImageFont.truetype("/System/Library/Fonts/STHeiti Medium.ttc", 17)
    except OSError:
        f_big = f_sm = ImageFont.load_default()
    for i, (t, p) in enumerate(pairs):
        im = Image.open(p).convert("RGB")
        im.thumbnail((thumb_w, th))
        cx, cy = (i % cols) * thumb_w, (i // cols) * (th + head)
        exp, order = expected_caption(track, t - intro_trim)
        dr.text((cx + 6, cy + 6), f"成片 t={t:.2f}s  正文 t={t - intro_trim:.2f}s", font=f_big, fill=(255, 235, 120))
        dr.text((cx + 6, cy + 30), f"应显示[{order}] {exp}" if order else f"应显示 {exp}", font=f_sm, fill=(150, 235, 150))
        canvas.paste(im, (cx + (thumb_w - im.width) // 2, cy + head + (th - im.height) // 2))
    canvas.save(out_png)
    print(f"[sheet] {out_png}  {canvas.size}")


def audio_check(video, voice, intro_trim, track):
    """长段互相关：整体时延 + 若干中段窗口的局部时延（检测分段错位）。"""
    sr = 8000
    full = np.frombuffer(subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", video, "-ac", "1", "-ar", str(sr),
         "-f", "s16le", "-"], capture_output=True).stdout, dtype=np.int16).astype(np.float64)
    ref = np.frombuffer(subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", voice, "-ac", "1", "-ar", str(sr),
         "-f", "s16le", "-"], capture_output=True).stdout, dtype=np.int16).astype(np.float64)
    dur = len(full) / sr
    print(f"[audio] 成片 {dur:.2f}s / 源配音 {len(ref) / sr:.2f}s / intro_trim {intro_trim}s")

    def lag(seg, base, max_lag=int(2.0 * sr)):
        """±max_lag 内的归一化互相关峰。

        必须走 FFT：np.correlate 是 O(n²)，本片正文 280s@8k≈224 万点，
        整体比对要跑几小时；FFT 版一次 <1s，且只在 ±max_lag 内取峰。
        符号：峰值 m 表示成片音频比源配音「晚」 -m/sr 秒。
        """
        n = min(len(seg), len(base))
        if n < sr:
            return None, None
        seg, base = seg[:n], base[:n]
        seg = seg - seg.mean()
        base = base - base.mean()
        norm = np.linalg.norm(seg) * np.linalg.norm(base)
        if norm < 1e-6:
            return None, None
        N = 1 << int(math.ceil(math.log2(n + 2 * max_lag)))
        spec_seg, spec_base = np.fft.rfft(seg, N), np.fft.rfft(base, N)
        circ = np.fft.irfft(spec_base * np.conj(spec_seg), N) / norm
        idx = np.arange(-max_lag, max_lag + 1)
        vals = circ[idx % N]
        k = int(np.argmax(vals))
        return -idx[k] / sr, float(vals[k])

    body_start = int(intro_trim * sr)
    off, corr = lag(full[body_start:body_start + len(ref)], ref)
    print(f"[audio] 整体：相对源配音时延 {off:+.3f}s  相关度 {corr:.3f}")

    # 局部窗口：跨全片取样，检测「字幕/画面卡在开头」式的分段错位
    span = len(ref)
    print("[audio] 局部窗口（成片秒 / 相关度 / 相对源时延）：")
    bad = []
    for frac in (0.06, 0.22, 0.4, 0.58, 0.76, 0.93):
        s = int(span * frac)
        n = int(sr * 12)
        if s + n > span:
            continue
        seg = full[body_start + s: body_start + s + n]
        o, c = lag(seg, ref[s:s + n], max_lag=int(0.6 * sr))
        flag = "" if (c is not None and c > 0.8 and abs(o) < 0.12) else "   <== 需复核"
        if flag:
            bad.append((frac, o, c))
        print(f"         {intro_trim + s / sr:7.1f}s  corr={c:.3f}  lag={o:+.3f}s{flag}")
    if bad:
        print(f"[audio] {len(bad)} 个窗口对齐存疑")
    else:
        print("[audio] 全部窗口对齐一致，未见分段错位")
    return off, corr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("track")
    ap.add_argument("--intro-trim", type=float, default=3.0)
    ap.add_argument("--voice", default=None)
    ap.add_argument("--times", default="15,48,91,123,163,218,265")
    ap.add_argument("--out", default="tmp/qc-final-av.png")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--no-sheet", action="store_true")
    a = ap.parse_args()

    track = json.load(open(a.track, encoding="utf-8"))
    if not a.no_sheet:
        times = [float(x) for x in a.times.split(",") if x.strip()]
        pairs = grab(a.video, times)
        sheet(pairs, track, a.intro_trim, a.out)
    if not a.no_audio and a.voice:
        audio_check(a.video, a.voice, a.intro_trim, track)


if __name__ == "__main__":
    sys.exit(main())
