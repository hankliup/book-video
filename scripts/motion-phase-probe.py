#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""亚像素运镜量化：相邻帧 FFT 相位相关 + 抛物线插值，直接量出每帧平移量。

为什么不用 motion-jitter-probe.py --edge：那条「行带均值 + argmax 过阈值」的轨迹
在有多根相近亮度的竖线/雾/水流时会整条边跳走，报出 10~30px 的假抖动；
而帧差 CV 在推镜只有 ~0.03px/帧时由编码噪声主导，也说明不了问题。
相位相关量的是「整幅画面相对上一帧移动了多少」，居中慢推的真值应≈0，
残差（标准差、符号翻转次数）才是用户能感觉到的「晃」。

用法：
  python3 scripts/motion-phase-probe.py <video> <起始秒> [帧数] [--rows y0,y1]
  例：python3 scripts/motion-phase-probe.py tmp/shonian_tpl/body-bg.mp4 20 150
"""
import math
import subprocess
import sys

import numpy as np


def frames_gray(path, start, count, w=720, h=960):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start),
           "-i", path, "-vf", f"fps=30,scale={w}:{h}", "-frames:v", str(count),
           "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        raise SystemExit(p.stderr.decode()[-400:])
    blob = p.stdout
    n = len(blob) // (w * h)
    return [np.frombuffer(blob[i * w * h:(i + 1) * w * h], dtype=np.uint8)
            .reshape(h, w).astype(np.float64) for i in range(n)]


def _parab(c0, cm, cp):
    d = cm - 2 * c0 + cp
    return 0.0 if abs(d) < 1e-12 else 0.5 * (cm - cp) / d


def phase_shift(a, b, rows=None):
    """b 相对 a 的亚像素平移 (dx, dy)，单位：像素（在全分辨率上）。"""
    # 缩放系数只能按「整帧」尺寸算：--rows 只是纵向切片，像素间距没变。
    # 早先用切片的 h 去除 960，120 行的干净带会把 dy 虚报 8 倍（0.005px/帧 → 0.04px/帧），
    # 看起来像「在抖」，其实量的是坐标换算。
    fh, fw = a.shape
    if rows:
        a, b = a[rows[0]:rows[1]], b[rows[0]:rows[1]]
    h, w = a.shape
    wy = np.hanning(h)[:, None]
    wx = np.hanning(w)[None, :]
    win = wy * wx
    A = np.fft.rfft2(a * win)
    B = np.fft.rfft2(b * win)
    R = A * np.conj(B)
    R /= np.abs(R) + 1e-9
    cc = np.fft.irfft2(R, s=a.shape)
    cc = np.fft.fftshift(cc)
    cy, cx = np.unravel_index(int(np.argmax(cc)), cc.shape)
    dy = _parab(cc[cy, cx], cc[(cy - 1) % h, cx], cc[(cy + 1) % h, cx])
    dx = _parab(cc[cy, cx], cc[cy, (cx - 1) % w], cc[cy, (cx + 1) % w])
    return (cx - w // 2 + dx) * (720 / fw), (cy - h // 2 + dy) * (960 / fh)


def main():
    args = [a for a in sys.argv[1:]]
    rows = None
    for a in list(args):
        if a.startswith("--rows"):
            i = args.index(a)
            spec = a.split("=", 1)[1] if "=" in a else args[i + 1]
            y0, y1 = (int(x) for x in spec.split(","))
            rows = (y0, y1)
            args.pop(i)
            if "=" not in a:
                args.pop(i)
    path, start = args[0], float(args[1])
    count = int(args[2]) if len(args) > 2 else 150
    ims = frames_gray(path, start, count)
    shifts = [phase_shift(a, b, rows) for a, b in zip(ims, ims[1:])]
    dx = np.array([s[0] for s in shifts])
    dy = np.array([s[1] for s in shifts])
    for name, v in (("水平 dx", dx), ("垂直 dy", dy)):
        rev = int(np.sum(np.diff(np.sign(v)) != 0))
        print(f"{start:>7.2f}s  {name}  均值 {v.mean():+.4f}px  标准差 {v.std():.4f}px  "
              f"范围 [{v.min():+.3f}, {v.max():+.3f}]  符号翻转 {rev}/{len(v) - 1}")
    print(f"          累计位移 ({dx.sum():+.2f}, {dy.sum():+.2f})px / {len(dx) / 30:.1f}s"
          f"{'  采样带 y=' + str(rows) if rows else '  全幅'}")


if __name__ == "__main__":
    main()
