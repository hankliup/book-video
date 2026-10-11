#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""量化正文运镜抖动：相邻帧差 + 亚像素边缘轨迹。

两个互补指标：
  1) 帧差 CV：平滑慢推的相邻帧差应基本恒定；整数取整抖动会「静止帧/跳变帧」交替。
  2) 亚像素边缘轨迹：跟踪一条高对比边缘（如蜡烛亮边）的列位置，
     用三阶多项式拟合慢推轨迹，残差 RMS 就是镜头的「抖」，速度符号翻转次数
     （reversal）则是「一顿一顿」的直接计数。平滑浮点运镜的残差应 <0.05px。

用法：python3 scripts/motion-jitter-probe.py <video> <起始秒> [采样帧数] [--edge] [--rows 起,止]

注意（踩过的坑）：--edge 的取样带默认 560-700，正文标题/字幕层压在上面时会把
「字幕换行」误读成「镜头在抖」。要么用 --rows 换到无文字的空档带（如氛围图的中段），
要么把采样帧数控制在同一条字幕内部，避开切换瞬间。
"""
import statistics
import subprocess
import sys

from PIL import Image

W, H = 720, 960
SW, SH = 360, 480  # 帧差比较用降采样，避开编码噪点主导


def frames(path, start, count, w=SW, h=SH, pix="gray"):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(start),
           "-i", path, "-vf", f"fps=30,scale={w}:{h}", "-frames:v", str(count),
           "-pix_fmt", pix, "-f", "rawvideo", "pipe:1"]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        raise SystemExit(p.stderr.decode()[-400:])
    blob = p.stdout
    n = len(blob) // (w * h)
    return [Image.frombytes("L", (w, h), blob[i * w * h:(i + 1) * w * h])
            for i in range(n)]


def frame_diffs(ims):
    out = []
    for a, b in zip(ims, ims[1:]):
        aa, bb = a.tobytes(), b.tobytes()
        out.append(sum(abs(x - y) for x, y in zip(aa, bb)) / len(aa))
    return out


def edge_track(path, start, count, rows=(560, 700), thresh=0.55):
    """返回每帧高对比上升沿的亚像素列坐标（全分辨率灰度）。"""
    ims = frames(path, start, count, w=W, h=H)
    import numpy as np
    pos = []
    for im in ims:
        a = np.asarray(im, dtype=np.float64)[rows[0]:rows[1], 60:660].mean(axis=0)
        a = np.convolve(a, np.ones(5) / 5, mode="same")
        lo, hi = a.min(), a.max()
        target = lo + (hi - lo) * thresh
        idx = int(np.argmax(a > target))
        if 0 < idx < len(a) - 1:
            frac = (target - a[idx - 1]) / max(1e-9, a[idx] - a[idx - 1])
            pos.append(60 + idx - 1 + frac)
        else:
            pos.append(60 + idx)
    return pos


def edge_report(pos):
    import numpy as np
    y = np.asarray(pos, dtype=np.float64)
    x = np.arange(len(y), dtype=np.float64)
    fit = np.polyval(np.polyfit(x, y, 3), x)
    resid = y - fit
    vel = np.diff(y)
    rev = int(np.sum(np.diff(np.sign(vel)) != 0))
    print(f"          边缘轨迹 {len(y)} 帧：位移 {y[-1] - y[0]:+.2f}px  "
          f"残差RMS {float(resid.std()):.3f}px  最大偏差 {float(np.abs(resid).max()):.3f}px  "
          f"速度翻转 {rev}/{len(vel) - 1}")


def main():
    argv = sys.argv[1:]
    edge = "--edge" in argv
    rows = (560, 700)
    if "--rows" in argv:
        i = argv.index("--rows")
        a, b = argv[i + 1].split(",")
        rows = (int(a), int(b))
        del argv[i:i + 2]
    args = [a for a in argv if a != "--edge"]
    path, start = args[0], float(args[1])
    count = int(args[2]) if len(args) > 2 else 150
    ims = frames(path, start, count)
    diffs = frame_diffs(ims)
    med = statistics.median(diffs)
    mean = statistics.fmean(diffs)
    sd = statistics.pstdev(diffs)
    jumps = sum(1 for d in diffs if med > 0 and d > 1.8 * med)
    still = sum(1 for d in diffs if med > 0 and d < 0.45 * med)
    print(f"{start:>7.2f}s  帧差均值 {mean:6.3f}  中位 {med:6.3f}  标准差 {sd:6.3f}  "
          f"CV {sd / mean:5.3f}  跳变帧 {jumps}/{len(diffs)}  静止帧 {still}/{len(diffs)}")
    if edge:
        print(f"          （取样带 y={rows[0]}-{rows[1]}）")
        edge_report(edge_track(path, start, count, rows=rows))


if __name__ == "__main__":
    main()
