#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐镜运镜幅度实测：在纯背景 body-bg.mp4 上反解两帧之间的真实缩放增量与纵向位移。

为什么需要它：`zoom-drift-probe.py` 的第二行读数（关键帧边缘 → px/s）取干净带里
最强上升沿的列位置，在雾岭、暗石墙、纯天空这类低纹理背景上会钉到搜索窗边界（60 / 659）
或跳到另一个特征上，报出「0.06px/s」的假静止和「反向翻转 ⚠️」的假台阶。
它的**第一行**（帧差中位 / 跳变帧 / 静止帧 / 最长交替run）判平滑仍然可信，幅度必须以本脚本为准。

量法（对齐记忆「motion-visible-rule」定稿口径）：
  · 只量 `/tmp/<书名>_tpl/body-bg.mp4`（无字幕无书名的纯背景），取干净带 y200–700；
  · **(s, dy) 联合扫描**：慢推叠了 drift_y_px，纯 s 拟合会拿平移去补缩放对不上的行，读数系统性虚高；
  · 镜头窗口由 `body-bg.json` 的 `layer_start` 给出（正文秒，不减 INTRO_TRIM），凭印象取秒数会跨到相邻图层；
  · 期望值按渲染器的**正弦缓动**算，不是线性：`e = 0.5-0.5cos(πu)`、`z = z0+(z1-z0)e`，
    探针窗两端各掐 PAD 时线性期望会把每层都判成「幅度偏大」。

用法：
  python3 scripts/motion-amplitude-fit.py <书名> [--all] [层名...]
  python3 scripts/motion-amplitude-fit.py <书名> --video /tmp/xx/body-bg.mp4 --pad 1.0

判读：`边缘速度` 低于 0.3px/s 按「没有动态」处理（红）；`zoom 实测/期望` 偏离 >20% 要复核缓动模型或换层。
"""
import argparse
import json
import math
import os
import subprocess
import sys

import numpy as np
from PIL import Image

W, H = 720, 960
BAND = (200, 700)          # 避开常驻书名 y60–210 与字幕 y620+（背景层本就没有它们，留余量防串期）


def grab(path, t):
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}",
         "-i", path, "-frames:v", "1", "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"],
        capture_output=True)
    if len(p.stdout) != W * H:
        raise SystemExit(f"[grab] t={t:.2f} 取帧失败：{p.stderr.decode()[-300:]}")
    return np.frombuffer(p.stdout, np.uint8).reshape(H, W).astype(np.float32)


def score(a, b, f, dy):
    """把 A 绕中心等比放大 f、纵向挪 dy 后与 B 求干净带 MAD。"""
    zw, zh = int(round(W * f)), int(round(H * f))
    if zw < W or zh < H:
        return 1e9
    z = np.asarray(Image.fromarray(a.astype(np.uint8), "L").resize((zw, zh), Image.BILINEAR), np.float32)
    x0, y0 = (zw - W) // 2, (zh - H) // 2
    if not (0 <= y0 + dy <= zh - H):
        return 1e9
    crop = z[y0 + dy:y0 + dy + H, x0:x0 + W]
    return float(np.abs(crop[BAND[0]:BAND[1]] - b[BAND[0]:BAND[1]]).mean())


def fit(a, b):
    best = (1e9, 1.0, 0)
    for f in [1 + k * 0.01 for k in range(0, 26)]:
        for dy in range(-8, 9):
            m = score(a, b, f, dy)
            if m < best[0]:
                best = (m, f, dy)
    for _ in range(4):
        f0, dy0 = best[1], best[2]
        for f in [f0 + k * 0.002 for k in range(-5, 6)]:
            for dy in range(dy0 - 2, dy0 + 3):
                m = score(a, b, f, dy)
                if m < best[0]:
                    best = (m, f, dy)
    return best


def ease(u):
    return 0.5 - 0.5 * math.cos(math.pi * min(1.0, max(0.0, u)))


def expected(ep_dir, motion, name, a0, a1, tA, tB):
    """按 scene-map 的 motion 块算探针窗内的期望缩放比与纵向位移。"""
    if not motion:
        return None
    z0 = motion["zoom_start"]
    per10 = motion["zoom_per_10s"]
    travel = min(max(per10 * (a1 - a0) / 10.0, motion["zoom_travel_min"]), motion["zoom_travel_max"])
    z1 = z0 + travel
    za = z0 + (z1 - z0) * ease((tA - a0) / (a1 - a0))
    zb = z0 + (z1 - z0) * ease((tB - a0) / (a1 - a0))
    dya = motion.get("drift_y_px", 0) * ease((tA - a0) / (a1 - a0))
    dyb = motion.get("drift_y_px", 0) * ease((tB - a0) / (a1 - a0))
    return zb / za, dyb - dya


def main():
    ap = argparse.ArgumentParser(description="逐镜运镜幅度实测（(s, dy) 联合尺度拟合）")
    ap.add_argument("episode")
    ap.add_argument("--video", default=None, help="纯背景 body-bg.mp4，默认 /tmp/<书名>_tpl/body-bg.mp4")
    ap.add_argument("--manifest", default=None, help="body-bg.json，默认与视频同目录")
    ap.add_argument("--pad", type=float, default=1.0, help="镜长两端各掐掉的秒数（避开交叉淡入）")
    ap.add_argument("--all", action="store_true", help="量所有非静止层")
    ap.add_argument("shots", nargs="*", help="要量的层名，缺省为 8 个抽样镜")
    a = ap.parse_args()

    video = a.video or f"/tmp/{a.episode}_tpl/body-bg.mp4"
    man_path = a.manifest or os.path.join(os.path.dirname(video), "body-bg.json")
    man = json.load(open(man_path))
    starts, layers = man["layer_start"], man["layers"]
    ep_dir = f"episodes/{a.episode}"
    sm_path = os.path.join(ep_dir, "scene-map.json")
    sm = json.load(open(sm_path)) if os.path.exists(sm_path) else {}
    motion = sm.get("motion") or man.get("motion")

    if a.all:
        shots = [n for n in layers if n != "result-bridge"]
    elif a.shots:
        shots = a.shots
    else:
        shots = [n for n in ("atmosphere-1", "atmosphere-2", "atmosphere-4", "atmosphere-6",
                             "atmosphere-8", "atmosphere-12", "atmosphere-14", "atmosphere-17")
                 if n in layers]

    print(f"[video] {video}  干净带 y{BAND[0]}–{BAND[1]}  pad {a.pad}s  motion {'有' if motion else '无'}")
    reds = 0
    for name in shots:
        i = layers.index(name)
        a0 = starts[i]
        a1 = starts[i + 1] if i + 1 < len(starts) else man["body_dur"]
        tA, tB = a0 + a.pad, a1 - a.pad
        if tB - tA < 3:
            tA, tB = a0 + 0.5, a1 - 0.5
        if i == 0:
            print(f"{name:>16}  静止层，跳过（应读 s=1.000）")
            continue
        mad, f, dy = fit(grab(video, tA), grab(video, tB))
        dur = tB - tA
        dz = f - 1.0
        edge = W * dz / 2.0
        v = edge / dur
        exp = expected(ep_dir, motion, name, a0, a1, tA, tB)
        tail = ""
        if exp:
            tail = f"   期望 zoom +{exp[0] - 1:.4f} / dy {exp[1]:+.1f}px"
        flag = "⚠️ 低于 0.3px/s，按没动处理" if v < 0.3 else "✅"
        if v < 0.3:
            reds += 1
        print(f"{name:>16} {tA:7.2f}->{tB:7.2f} (Δ{dur:5.1f}s)  zoom +{dz:.4f}  边缘位移 {edge:5.1f}px "
              f"→ {v:4.2f}px/s   竖向 {dy:+d}px{tail}  MAD {mad:5.2f}  {flag}")
    print(f"\n{len(shots)} 层，判红 {reds} 层")
    return 1 if reds else 0


if __name__ == "__main__":
    sys.exit(main())
