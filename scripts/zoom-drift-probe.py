#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运镜平滑度复核：只在无字幕、无书名的干净带内测相邻帧差，找「静止帧/跳变帧交替」。

为什么需要它：`scripts/motion-jitter-probe.py --edge` 的默认采样带 rows=(560,700) 会压到
字幕区，字幕换行会被读成镜头抖（《美丽新世界》t=200 报 3.09px 其实是一次交叉溶解）。
而本系列的慢推真实位移只有 ~0.2px/s（缩放 1.020→1.062 摊到 30-40s 一镜），
单帧位移 0.007px，远低于任何亚像素估计的噪声底 —— 用互相关去积分「残差 RMS」会得到
一个恒定的假位移，看着很漂亮，其实测的是量化偏置，不是镜头。

所以这里只测两件真能落到观感上的事：
  1) 干净带相邻帧差的分布：平滑慢推应近似恒定；整数取整抖动会呈静止/跳变交替。
  2) 稀疏关键帧（默认每 1s）的绝对边缘位置：真实慢推应单调同向，不该来回走。

用法：python3 scripts/zoom-drift-probe.py <video> <起始秒> [帧数] [标签]
判读：静止帧与跳变帧都接近 0、且边缘位置无反向翻转 → 平滑。
"""
import subprocess
import sys

import numpy as np
from PIL import Image

W, H = 720, 960
BAND = (210, 330)          # 常驻书名 y≈70-207、字幕 y≈704-840，中间这条带是纯画面
KEY_STEP = 30              # 关键帧间隔（1s @30fps）：慢推 1s 累计 ~0.2px，够定位


def frames(path, start, count, step=1):
    """取 [start, start+count/30) 的帧；step>1 时按关键帧抽稀。"""
    dur = count / 30.0
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", path,
         "-t", f"{dur:.3f}", "-vf", f"fps={30 // step},scale={W}:{H}",
         "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"], capture_output=True)
    if p.returncode != 0:
        raise SystemExit(p.stderr.decode()[-400:])
    blob = p.stdout
    n = len(blob) // (W * H)
    return [np.asarray(Image.frombytes("L", (W, H), blob[i * W * H:(i + 1) * W * H]),
                       dtype=np.float64) for i in range(n)]


def diff_series(ims):
    a = np.stack(ims)[:, BAND[0]:BAND[1], :].astype(np.float32)
    d = np.abs(np.diff(a, axis=0)).mean(axis=(1, 2))
    med = float(np.median(d))
    jumps = int(np.sum(d > 1.8 * med)) if med > 0 else 0
    still = int(np.sum(d < 0.45 * med)) if med > 0 else 0
    # 交替run：跳变/静止相邻交替的最长长度，>6 就是肉眼可辨的一顿一顿
    flags = np.where(d > 1.8 * med, 1, np.where(d < 0.45 * med, -1, 0))
    run = best = 0
    for i, f in enumerate(flags):
        if f and i and flags[i - 1] and f != flags[i - 1]:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return d, med, jumps, still, best


def edge_positions(ims, lo=60, hi=660):
    """每帧独立定位干净带内最强上升沿的亚像素列位置。"""
    pos = []
    for im in ims:
        prof = im[BAND[0]:BAND[1], lo:hi].mean(axis=0)
        sm = np.convolve(prof, np.ones(5) / 5, mode="same")
        g = np.gradient(sm)
        i = int(np.argmax(np.abs(g)))
        if 0 < i < len(g) - 1:
            a, b, c = g[i - 1], g[i], g[i + 1]
            den = a - 2 * b + c
            frac = 0.5 * (a - c) / den if den else 0.0
            pos.append(lo + i + np.clip(frac, -0.5, 0.5))
        else:
            pos.append(lo + i)
    return np.asarray(pos)


def main():
    path, start = sys.argv[1], float(sys.argv[2])
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 300
    tag = sys.argv[4] if len(sys.argv) > 4 else f"{start}s"

    ims = frames(path, start, count)
    d, med, jumps, still, run = diff_series(ims)
    cv = float(np.std(d) / np.mean(d)) if np.mean(d) else 0.0
    print(f"{tag:>6}  干净带帧差 中位 {med:6.3f}  CV {cv:5.3f}  "
          f"跳变帧 {jumps}/{len(d)}  静止帧 {still}/{len(d)}  最长交替run {run}")

    keys = frames(path, start, count, step=KEY_STEP)
    pos = edge_positions(keys)
    vel = np.diff(pos)
    rev = int(np.sum(np.diff(np.sign(vel[vel != 0])) != 0))
    span = pos[-1] - pos[0]
    print(f"          关键帧边缘 {'→'.join(f'{p:.1f}' for p in pos)}")
    print(f"          10s 累计位移 {span:+.2f}px（{abs(span) / max(1e-9, len(vel) * KEY_STEP / 30):.2f}px/s）"
          f"  反向翻转 {rev}/{max(1, len(vel) - 1)}")
    bad = (run > 6) or (still > 0.25 * len(d)) or (rev > 0.4 * max(1, len(vel) - 1))
    print(f"  -> {tag} {'⚠️ 疑似台阶/抖动，需复核' if bad else '✅ 平滑（无静止/跳变交替、位移单调）'}")


if __name__ == "__main__":
    main()
