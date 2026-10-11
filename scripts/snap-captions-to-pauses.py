#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把字幕时间轴的边界吸附到配音的真实停顿边界上。

为什么需要它（2026-09-26《霍比特人》实测）：
  · `create-body-timings.mjs` 的 silence-segments 法按「静音切出的语音段」数量去套
    script-body.csv 行号，段数与行数不等时整套映射会逐行滑走——本期实测漂到 +14.5s
    （第 50 行的话实际在 140.7s 说，字幕 126.5s 就上屏了），而它的窗口内静音占比
    反而是最漂亮的 0.5%，因为边界本来就是静音切出来的：**边界干净不等于行对上了**。
  · `create-body-timings-precise.mjs` / `align-word-timings.mjs` 的 token LCS 把「哪一行
    落在哪一秒」对准了（与 Whisper 段级时间窗互相印证，逐段误差 <0.1s），但
    whisper.cpp 未开 --dtw 时 token 时间是按字序在段内线性插值出来的
    （实测 330ms 等步长、t_dtw=-1），所以每条字幕的头尾会各自偏离真实语音 0.2~1s，
    窗口内最多 52% 是静音。

本脚本只修后者、不动前者：以 LCS 结果为准的「行↔时间」归属保持不变，
只把每条的 start 吸附到最近的语音起点（静音结束）、end 吸附到最近的语音终点
（静音开始），容差内找不到就原样保留，绝不因为吸附而让两条字幕重叠或把某条压塌。

用法：
  python3 scripts/snap-captions-to-pauses.py "<episode>/audio/body-timings.json" \
      --voice "<episode>/audio/body-voiceover.mp3" [--apply] [--report]

默认 dry-run，只打印前后对比与量化指标；加 --apply 才写回（并留 .presnap 备份）。
"""
import argparse
import json
import re
import shutil
import subprocess
import sys

TOL_START = 0.55   # 起点向前后各找 0.55s 内的语音起点
TOL_END_BACK = 0.55
TOL_END_FWD = 0.85  # 终点允许多等 0.85s 才找到停顿（说话尾巴比头更容易被插值低估）
MIN_DUR = 0.55
HOLD = 0.05        # 与下一条起点至少留出的空隙


def silencedetect(voice, noise="-35dB", d=0.18):
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", voice,
         "-af", f"silencedetect=noise={noise}:d={d}", "-f", "null", "-"],
        capture_output=True, text=True)
    ev = [(m.group(1), float(m.group(2)))
          for m in re.finditer(r"silence_(start|end):\s*([0-9.]+)", p.stderr)]
    sil = []
    for k, (kind, val) in enumerate(ev):
        if kind != "start":
            continue
        for kind2, val2 in ev[k + 1:k + 4]:
            if kind2 == "end":
                sil.append((val, val2))
                break
    sil.sort()
    return sil


def speech_spans(sil, duration):
    """静音的补集 = 语音区间。"""
    out, t = [], 0.0
    for a, b in sil:
        if a > t:
            out.append((t, a))
        t = max(t, b)
    if t < duration:
        out.append((t, duration))
    return out


def nearest(vals, x, lo, hi):
    cand = [v for v in vals if x - lo <= v <= x + hi]
    return min(cand, key=lambda v: abs(v - x)) if cand else None


def analyse(caps, spans, sil_starts, sil_ends, duration, label):
    inside = []
    for c in caps:
        s, e = c["start"], min(c["end"], duration)
        cov = sum(max(0.0, min(e, b) - max(s, a)) for a, b in spans if min(e, b) > max(s, a))
        inside.append(1.0 - cov / max(1e-6, e - s))
    on_hit = sum(1 for c in caps
                 if nearest(sil_ends, c["start"], TOL_START, TOL_START) is not None)
    off_hit = sum(1 for c in caps
                  if nearest(sil_starts, c["end"], TOL_END_BACK, TOL_END_FWD) is not None)
    print(f"{label:>10}  语音起点吸附命中 {on_hit}/{len(caps)}  语音终点吸附命中 {off_hit}/{len(caps)}  "
          f"窗口内平均静音 {100 * sum(inside) / len(inside):.1f}%  最长窗口 {max(c['end'] - c['start'] for c in caps):.2f}s")
    return inside


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("timings")
    ap.add_argument("--voice", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    doc = json.load(open(args.timings, encoding="utf-8"))
    caps = doc["captions"]
    duration = float(doc.get("duration") or 0.0)
    sil = silencedetect(args.voice)
    spans = speech_spans(sil, duration)
    sil_starts = [a for a, _ in sil]
    sil_ends = [b for _, b in sil]

    before = [dict(c) for c in caps]
    print(f"语音区间 {len(spans)} 段，静音 {len(sil)} 段，总时长 {duration:.2f}s")
    analyse(before, spans, sil_starts, sil_ends, duration, "吸附前")

    prev_end = 0.0
    for c in caps:
        s = nearest(sil_ends, c["start"], TOL_START, TOL_START)
        if s is None:
            s = c["start"]
        s = max(s, prev_end)
        e = nearest(sil_starts, c["end"], TOL_END_BACK, TOL_END_FWD)
        if e is None:
            e = c["end"]
        e = max(e, s + MIN_DUR)
        e = min(e, duration)
        c["start"], c["end"] = round(s, 3), round(e, 3)
        prev_end = e

    # 与下一条起点留出 HOLD，并保证不重叠、不塌缩
    for c, nxt in zip(caps, caps[1:]):
        if c["end"] > nxt["start"] - HOLD:
            c["end"] = round(max(c["start"] + MIN_DUR, nxt["start"] - HOLD), 3)
    for c in caps:
        c["end"] = round(max(c["end"], c["start"] + MIN_DUR), 3)

    after = analyse(caps, spans, sil_starts, sil_ends, duration, "吸附后")
    drift = max(abs(a["start"] - b["start"]) for a, b in zip(before, caps))
    print(f"起点最大移动 {drift:.2f}s（行↔时间的归属未改变，只动边界）")

    if args.report:
        for b, a in zip(before, caps):
            print(f"  #{b['order']:>3}  [{b['start']:6.2f},{b['end']:6.2f}] -> [{a['start']:6.2f},{a['end']:6.2f}]")

    if not args.apply:
        print("dry-run（加 --apply 写回）")
        return
    shutil.copyfile(args.timings, f"{args.timings}.presnap")
    doc["alignment"] = {**(doc.get("alignment") or {}),
                        "pauseSnapped": True,
                        "pauseSnap": {"voice": args.voice,
                                      "silences": len(sil),
                                      "toleranceStart": TOL_START,
                                      "toleranceEnd": [TOL_END_BACK, TOL_END_FWD]}}
    doc["captions"] = caps
    json.dump(doc, open(args.timings, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"已写回 {args.timings}（备份 {args.timings}.presnap）")


if __name__ == "__main__":
    sys.exit(main())
