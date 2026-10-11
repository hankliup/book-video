#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给「外部预渲染背景」补上交付门禁认得的水波涟漪。

为什么单独一步：`render_scene_map_template_fallback.py` 的涟漪是写在它自己的
逐帧背景合成循环里的（`_wu = water_u(...)` → `ripple(cur, _wu)`），走 PREBUILT_BG
这条外部背景时不经过那段循环，HyperFrames 组合里那个 svg 位移滤镜幅度太小，
`check-water-ripple.py` 量出来质心只外移 30px（需 ≥45）、声画差 2.85 帧。

这道程序**只重写水波窗口那 32 帧**，其余帧原样 copy；涟漪函数与常数直接 import
`scripts/lib/water_ripple.py`，不另立一份口径（门禁与渲染必须同源）。
用法：python3 scripts/ripple-prebuilt-bg.py <in.mp4> <out.mp4> <final-intro-trim>
"""
import os
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
from water_ripple import WATER_OFFSET, WATER_DUR, ripple, water_u  # noqa: E402

FPS = 30


def probe_dur(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
    return float(out.stdout.strip())


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    src, dst, intro_trim = sys.argv[1], sys.argv[2], float(sys.argv[3])
    dur = probe_dur(src)

    # 水波窗在成片时间轴上是 [intro_trim + WATER_OFFSET, +WATER_DUR)；
    # 但**背景视频是从「片头→正文切点」起算的**，所以帧号要对齐渲染器自己的口径
    # `WATER_FRAME0 = round(WATER_OFFSET * FPS)`（见 render_scene_map_template_fallback.py），
    # 不能再用 intro_trim 去加一遍——那样会把 0.32s 的水波挪到 12.1s 处，
    # 门禁量到的就是「窗口第 3 帧才开始动」（实测 +2.85 帧、质心只外移 27px）。
    start_frame = round(WATER_OFFSET * FPS)
    n_frames = round(WATER_DUR * FPS)
    total = round(dur * FPS)
    print(f"[ripple-bg] {src} {dur:.2f}s {total} 帧；水波窗 成片 {intro_trim + WATER_OFFSET:.2f}s "
          f"→ 背景第 {start_frame}..{start_frame + n_frames - 1} 帧（{n_frames} 帧）")

    with tempfile.TemporaryDirectory(prefix="ripple-bg-") as td:
        # 1. 逐帧解码整段为 PNG（4.6 分钟 720x960 可承受）
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src,
                        os.path.join(td, "f-%06d.png")], check=True)
        files = sorted(f for f in os.listdir(td) if f.startswith("f-"))
        if len(files) != total:
            print(f"[ripple-bg] ⚠️ 解出 {len(files)} 帧，预期 {total}")
        # 2. 只重写窗口内那几帧
        changed = 0
        for idx in range(start_frame, start_frame + n_frames):
            if idx >= len(files):
                break
            u = water_u(idx, FPS, start_frame, n_frames)
            if u is None:
                continue
            p = os.path.join(td, files[idx])
            Image.fromarray(np.asarray(ripple(Image.open(p).convert("RGB"), u), dtype=np.uint8)).save(p)
            changed += 1
        print(f"[ripple-bg] 重写 {changed} 帧")
        # 3. 重新编码
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", str(FPS),
                        "-i", os.path.join(td, "f-%06d.png"),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
                        "-crf", "17", "-preset", "medium", dst], check=True)
    print(f"[ripple-bg] → {dst} {probe_dur(dst):.2f}s")


if __name__ == "__main__":
    main()
