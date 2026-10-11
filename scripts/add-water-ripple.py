#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给已经渲好的素材补「水波视觉效果」（常驻规则：水波必须与水波音效同现）。

逐帧合成器 scripts/render_scene_map_template_fallback.py 已经把涟漪做进了正文**背景层**，
书名层和字幕层在它之后绘制，所以成片天然带波且字不会被扭。本脚本只服务一种素材：
  → 独立片头 renders/intro-only.mp4（HyperFrames 渲出来的整条片子，文字已烧进画面）。

用法上的硬边界（别踩）：
  · 已经叠好字幕的成片**不要**用本脚本后处理。--protect-type 只压住顶部书名带（y<0.30H），
    而字幕在画面中下部，涟漪会把字幕扭成一团糊（《面纱》实测「她嫁给那个医生」不可读）。
    成片要加水波就改 images/scene-map 后重跑兜底渲染器，让涟漪回到背景层去。
  · HyperFrames 路线（render-episode-final.mjs）渲的成片同理：字已烧进画面，只能重渲。
  · 无字幕素材（纯空镜、片头前 3 秒定格）可以安全后处理，此时可加 --no-protect-type 整幅荡。

做法：只把水波窗口那几十帧抽出来做涟漪，再用 trim=start_frame/end_frame + concat 拼回去，
一次重编码、音频直接 copy，不碰其余帧。窗口外的帧逐像素不变。

用法：
  python3 scripts/add-water-ripple.py <in.mp4> [-o <out.mp4>]
      [--intro-trim 3.0] [--start 3.32] [--fps 30] [--no-protect-type]

  --intro-trim  切点秒数，水波起点 = 切点 + 0.32（默认 3.0，与兜底渲染器一致）
  --start       直接指定水波起点秒数（覆盖 --intro-trim）；独立片头就传 3.32
  --no-protect-type  画面里没有烧进文字时才允许整幅一起荡（默认保护顶部书名带）
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from water_ripple import (WATER_DUR, WATER_OFFSET, ripple, water_nframes,  # noqa: E402
                          water_u)

from PIL import Image  # noqa: E402


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"CMD failed ({r.returncode}): {' '.join(map(str, cmd))}\n{r.stderr[-1200:]}")
    return r.stdout


def n_video_frames(path, fps):
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
               "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", path])
    try:
        return int(out.strip())
    except ValueError:
        dur = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                         "-of", "csv=p=0", path]).strip())
        return round(dur * fps)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("-o", "--out")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--intro-trim", type=float, default=3.0)
    ap.add_argument("--start", type=float, default=None)
    ap.add_argument("--no-protect-type", action="store_true")
    ap.add_argument("--crf", type=int, default=17)
    a = ap.parse_args()

    src = os.path.abspath(a.video)
    dst = os.path.abspath(a.out or src)
    fps = a.fps
    start = a.start if a.start is not None else round(a.intro_trim + WATER_OFFSET, 2)
    protect = not a.no_protect_type

    total = n_video_frames(src, fps)
    sf = round(start * fps)
    n = water_nframes(fps)
    n = min(n, max(0, total - sf))
    if n <= 0:
        print(f"[water] {os.path.basename(src)} 起点 {start}s 已到片尾（共 {total} 帧），无需加水波")
        return
    u_last = water_u(sf + n - 1, fps, sf, water_nframes(fps))
    print(f"[water] {os.path.basename(src)} 帧 {sf}–{sf + n - 1}/{total} "
          f"= {start:.2f}–{round(start + n / fps, 2)}s  u∈(0,{u_last:.2f}]  "
          f"protect_type={protect}")

    tmp = tempfile.mkdtemp(prefix="water-ripple-")
    try:
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src,
             "-vf", f"select='between(n\\,{sf}\\,{sf + n - 1})',setpts=N/FRAME_RATE/TB",
             "-vsync", "0", "-start_number", "0", os.path.join(tmp, "f-%03d.png")])
        files = sorted(f for f in os.listdir(tmp) if f.startswith("f-"))
        assert len(files) == n, f"抽帧 {len(files)} ≠ 计划 {n}"
        for k, fn in enumerate(files):
            u = water_u(sf + k, fps, sf, water_nframes(fps))
            p = os.path.join(tmp, fn)
            Image.open(p).convert("RGB").save(p)      # 统一成 RGB PNG
            ripple(Image.open(p), u, protect_type=protect).save(
                os.path.join(tmp, "d" + fn[1:]))
        dfiles = sorted(f for f in os.listdir(tmp) if f.startswith("d-"))
        assert len(dfiles) == n
        seq = os.path.join(tmp, "d-%03d.png")

        out_tmp = os.path.join(tmp, "out.mp4")
        # 尾段可能为空（水波窗口正好压到片尾），空 trim 喂给 concat 会报错，按需拼装。
        parts = [f"[0:v]trim=start_frame=0:end_frame={sf},setpts=PTS-STARTPTS,"
                 f"fps={fps},format=yuv420p,settb=AVTB[h]"]
        labels = ["[h]"]
        parts.append(f"[1:v]setpts=PTS-STARTPTS,fps={fps},format=yuv420p,settb=AVTB[s]")
        labels.append("[s]")
        if sf + n < total:
            parts.append(f"[0:v]trim=start_frame={sf + n},setpts=PTS-STARTPTS,"
                         f"fps={fps},format=yuv420p,settb=AVTB[t]")
            labels.append("[t]")
        fc = (";".join(parts) + ";" + "".join(labels) +
              f"concat=n={len(labels)}:v=1:a=0,format=yuv420p[v]")
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src,
             "-framerate", str(fps), "-start_number", "0", "-i", seq,
             "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
             "-r", str(fps), "-crf", str(a.crf), "-preset", "medium",
             "-c:a", "copy", "-movflags", "+faststart", out_tmp])
        after = n_video_frames(out_tmp, fps)
        assert abs(after - total) <= 1, f"帧数漂移 {total} → {after}"
        if dst == src:
            shutil.move(out_tmp, src)
        else:
            shutil.copy2(out_tmp, dst)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"[water] done → {dst}")


if __name__ == "__main__":
    main()
