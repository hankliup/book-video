#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""字幕边界压测必须按**帧号**取帧，不能按四舍五入的秒去 seek。

为什么钉这一条（2026-10-07《阿特拉斯耸耸肩》交付）：十三道门禁里只有「字幕边界压测」
❌，10 个点全报「画面上没有字幕 · 带内零墨迹」，而逐帧核对成片证明画面是对的——
正文第 1636~1701 帧一直是行 19「不交 / 就叫自私」的墨迹，1702/1703 是两行之间那 0.04s
停顿留下的真空，1704 起是行 20。渲染器按 `round(t*fps)` 把每条字幕钉到绝对帧号，门禁问的
也是同一批秒数，但它取帧写的是 `-ss f"{t:.4f}"`：`2006/30 = 66.86666…` 进位成 `66.8667`，
已经**大于**第 2006 帧的 pts，而 ffmpeg 取的是「pts ≥ seek」的首帧，于是读到第 2007 帧。
每 3 个索引就有 1 个会这样错开一帧；再叠上交付包装器把正文起点算成「钩子配音 7.18s
(两位小数) + 模板 3.0s」= 10.18s，而装配按帧 concat 给的是 305 帧 = 10.1667s（差 0.4 帧），
半帧的坐标错刚好把行尾那个 ±0.06s 的采样推进真空。画面上字幕在，门禁看的是隔壁那一帧。

修法不是放宽阈值：`--min-ink`、`NEAR_TIE`、「边界空帧 ±1 帧」的豁免全都一个字没动。
改的是「问哪一帧」这件事本身——先把取点量化到帧网格，再按帧号取，期望与像素同源于同一个 n。

五个方向，全部落在真像素或真门禁进程上：
  ① 健康夹具（两条字幕、中间 2 帧真空、行尾采样正好压在**最后一条有墨帧**上）走新口径
     → 4 个点全绿，IoU 与次优差都实打实。
  ② 同一样取点用**改动前的秒数口径**真取一次帧（`-ss "%.4f"` 打 ffmpeg），量到的必须
     是隔壁那一帧、且那帧是真空 → 证明 ① 不是空转，缺陷形状在同一份产物上复现。
  ③ 真错位仍然必须红：把声明窗尾比画面实际多写 3 帧 → 门禁 MISMATCH。
     这条是「修好了但把牙磨平了」的反证。
  ④ 偏移传早了不许静默跳过：取点落到正文起点之前 → 门禁直接拒绝点名偏移错，
     而不是打「该时刻无字幕」把从没检查过的钩子段算成绿灯。
  ⑤ 静态：门禁本体仍按帧网格取帧（`seek_time` / `round(t * fps)` / 不再有 `.4f` 的 seek），
     交付包装器仍把「成片帧数 − 正文背景帧数」喂给 `--pre-body-frames`。
"""
import json
import math
import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
QC = os.path.join(ROOT, "scripts", "qc-caption-alignment.py")
WRAP = os.path.join(ROOT, "scripts", "check-episode-delivery.py")

W, H, FPS = 720, 960, 30
BAND = (0, 680, W, 850)
BG_RGB = (30, 30, 30)
INK_RGB = (255, 255, 255)
Y0, Y1 = 700, 759            # 字幕带内的墨迹行

PRE = 50                      # 钩子+模板占的帧数；50/30 = 1.666…，秒数口径两位小数给 1.67
PRE_BODY_S = round(PRE / FPS, 2)
# 行 1：画面画在第 15~39 帧。声明窗尾 1.35s → round(30×1.35) = round(40.5) = 40（取整到偶数），
#       所以画到 39 为止；行尾采样 bt = 1.35−0.06 = 1.29 → round(38.7) = 39 → 压在**最后一条
#       有墨帧**上，正是《阿特拉斯》行 19 的相对形状。
R1_DRAW = (15, 40)
R1_TIME = (15 / FPS, 40.5 / FPS)
GAP = (40, 42)                # 两行之间的真空帧（正文帧号）
R2_DRAW = (42, 70)
R2_TIME = (42 / FPS, 70 / FPS)
N_BODY = 75
R1_SPAN = (40, 680)           # 宽条
R2_SPAN = (40, 360)           # 窄条：两条字幕形状不同，argmax 才分得开，③ 的错配才有依据


def draw_span(img, span, rgba):
    d = ImageDraw.Draw(img)
    d.rectangle([span[0], Y0, span[1] - 1, Y1], fill=rgba)


def caption_png(path, span):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw_span(im, span, INK_RGB + (255,))
    im.save(path)


def frame_png(path, span):
    im = Image.new("RGB", (W, H), BG_RGB)
    if span:
        draw_span(im, span, INK_RGB)
    im.save(path)


def encode(seq_dir, out, frames):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-framerate", str(FPS), "-i", os.path.join(seq_dir, "%05d.png"),
                        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                        "-color_range", "pc", "-g", "5", out],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"夹具编码失败 {out}: {r.stderr[-300:]}")
    n = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                        "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", out],
                       capture_output=True, text=True).stdout.strip()
    if int(n or 0) != frames:
        raise SystemExit(f"夹具帧数 {n} != {frames}：CFR 没生效，测试的帧网格不可信")


def span_at_body(n):
    if R1_DRAW[0] <= n < R1_DRAW[1]:
        return R1_SPAN
    if R2_DRAW[0] <= n < R2_DRAW[1]:
        return R2_SPAN
    return None


def build(work):
    """成片 = 前 PRE 帧空白 + 正文按窗上字幕；body-bg = 正文帧数的纯色。"""
    frames_dir = os.path.join(work, "frames")
    bg_dir = os.path.join(work, "bgframes")
    cap_dir = os.path.join(work, "caps")
    for d in (frames_dir, bg_dir, cap_dir):
        os.makedirs(d, exist_ok=True)
    caption_png(os.path.join(cap_dir, "cap-000-1.png"), R1_SPAN)
    caption_png(os.path.join(cap_dir, "cap-001-2.png"), R2_SPAN)
    for n in range(PRE + N_BODY):
        frame_png(os.path.join(frames_dir, f"{n:05d}.png"),
                  span_at_body(n - PRE) if n >= PRE else None)
    for n in range(N_BODY):
        frame_png(os.path.join(bg_dir, f"{n:05d}.png"), None)
    film = os.path.join(work, "final.mp4")
    bg = os.path.join(work, "body-bg.mp4")
    encode(frames_dir, film, PRE + N_BODY)
    encode(bg_dir, bg, N_BODY)
    track = [{"index": 0, "order": 1, "text": "第一条字幕", "display": "第一条字幕",
              "start": round(R1_TIME[0], 3), "end": round(R1_TIME[1], 3)},
             {"index": 1, "order": 2, "text": "第二条字幕", "display": "第二条字幕",
              "start": round(R2_TIME[0], 3), "end": round(R2_TIME[1], 3)}]
    tr_path = os.path.join(work, "caption-track.json")
    json.dump(track, open(tr_path, "w", encoding="utf-8"), ensure_ascii=False)
    return film, bg, cap_dir, tr_path


def boundary_times(track):
    ts = []
    for c in track:
        ts.append(round(PRE_BODY_S + c["start"] + 0.06, 3))
        ts.append(round(PRE_BODY_S + c["end"] - 0.06, 3))
    return ts


def run_qc(film, bg, cap_dir, tr_path, times, pre_frames=PRE):
    cmd = [sys.executable, QC, film, tr_path, cap_dir, bg,
           "--intro-trim", str(PRE_BODY_S), "--times", ",".join(f"{t:.3f}" for t in times),
           "--work", os.path.join(os.path.dirname(film), "qcwork")]
    if pre_frames is not None:
        cmd += ["--pre-body-frames", str(pre_frames)]
    return subprocess.run(cmd, capture_output=True, text=True)


def band_ink(a_path, b_path):
    a = np.asarray(Image.open(a_path).convert("RGB").crop(BAND), dtype=np.int16)
    b = np.asarray(Image.open(b_path).convert("RGB").crop(BAND), dtype=np.int16)
    return int((np.abs(a - b).max(axis=2) > 28).sum())


def grab(path, t_str, out):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", t_str,
                        "-i", path, "-frames:v", "1", "-pix_fmt", "rgb24", out],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise SystemExit(f"取帧失败 {path}@{t_str}: {r.stderr[-200:]}")
    return out


def old_formula_probe(film, bg, n_body, work):
    """改动前的口径：把成片第 PRE+n 帧的 pts 写成 4 位小数去 seek，看真取回第几帧。"""
    exact = (PRE + n_body) / FPS
    seek = f"{exact:.4f}"
    f = grab(film, seek, os.path.join(work, "old.png"))
    b = grab(bg, f"{n_body / FPS - 0.5 / FPS:.6f}", os.path.join(work, "oldbg.png"))
    landed = math.ceil(float(seek) * FPS)
    return band_ink(f, b), landed - PRE


def main():
    work = f"/tmp/qc-frame-sampling-{os.getpid()}"
    os.makedirs(work, exist_ok=True)
    fails = []
    try:
        film, bg, cap_dir, tr_path = build(work)
        track = json.load(open(tr_path, encoding="utf-8"))
        times = boundary_times(track)

        # ① 新口径：4 个边界点必须逐点比中，不许有任何跳过
        r = run_qc(film, bg, cap_dir, tr_path, times)
        out = r.stdout
        if r.returncode != 0 or "错配 0 点" not in out:
            fails.append(f"① 健康夹具被自己判红（rc={r.returncode}）：\n{out[-1200:]}")
        if out.count(" OK ") < len(times):
            fails.append(f"① 健康夹具只有 {out.count(' OK ')}/{len(times)} 点逐比成功"
                         "（其余被跳成「该时刻无字幕」或「边界空帧」= 这条门在夹具上是空转）")
        if "MISMATCH" in out:
            fails.append(f"① 健康夹具出现 MISMATCH：\n{out[-800:]}")

        # ② 老口径真取一次帧：同一个取点必须错开一帧，且错进真空
        last_ink = R1_DRAW[1] - 1
        old_ink, old_frame = old_formula_probe(film, bg, last_ink, work)
        if not (old_frame == last_ink + 1 and GAP[0] <= old_frame < GAP[1]):
            fails.append(f"② 对照失效：老口径在成片第 {PRE + last_ink} 帧没有错开进真空，"
                         f"读到的是正文第 {old_frame} 帧（墨迹 {old_ink}）——"
                         "夹具没复现缺陷，① 的绿灯就证明不了新口径修了什么")
        if old_ink >= 300:
            fails.append(f"② 对照失效：老口径读到的那帧仍有 {old_ink} 墨迹像素，不是真空")

        # ③ 真错位必须红：声明窗尾比画面多写 3 帧（第 42 帧画面上是行 2，不是行 1）
        bad = [dict(track[0], end=round(R1_TIME[1] + 3 / FPS, 3)), track[1]]
        bad_path = os.path.join(work, "bad-track.json")
        json.dump(bad, open(bad_path, "w", encoding="utf-8"), ensure_ascii=False)
        r3 = run_qc(film, bg, cap_dir, bad_path, boundary_times(bad))
        if "MISMATCH" not in r3.stdout or r3.returncode == 0:
            fails.append("③ 把声明窗尾比画面多写 3 帧，门禁仍然放行 —— 改完取帧口径等于把牙磨平")
        if "错配 1 点" not in r3.stdout:
            fails.append(f"③ 真错位的红点数量不对（应恰好 1）：\n{r3.stdout[-700:]}")

        # ④ 偏移传早了必须拒绝，不能静默跳过
        r4 = run_qc(film, bg, cap_dir, tr_path, [round(PRE_BODY_S - 0.5, 3)])
        if "正文起点之前" not in (r4.stdout + r4.stderr):
            fails.append("④ 取点落在正文起点之前时门禁没有拒绝："
                         "包装器注释点名的「在钩子段里找正文字幕，六道门对着一段没检查的画面绿灯」"
                         "会照原样复发")

        # ⑤ 静态：口径不许退回按秒 seek
        qc_src = open(QC, encoding="utf-8").read()
        for tok in ("def seek_time", "def grab_frame", "round(t * fps)", "--pre-body-frames"):
            if tok not in qc_src:
                fails.append(f"⑤ 门禁里 {tok} 不见了：取帧口径退回了按秒 seek")
        if '-ss", f"{t:.4f}"' in qc_src:
            fails.append("⑤ 门禁里又出现了 `f\"{t:.4f}\"` 的 seek —— 那正是把 66.86666 进位成 "
                         "66.8667、读到隔壁帧的写法")
        wrap = open(WRAP, encoding="utf-8").read()
        for tok in ("def ffprobe_frames", "stream=nb_frames", "--pre-body-frames"):
            if tok not in wrap:
                fails.append(f"⑤ 交付包装器里 {tok} 不见了：正文起点又退回按两位小数秒相加")
    finally:
        subprocess.run(["rm", "-rf", work], capture_output=True)

    for line in fails:
        print("❌", line)
    if fails:
        raise SystemExit(1)
    print(f"✅ 边界压测按帧网格取帧：健康夹具 {len(times)} 点逐比全绿 / "
          f"老口径同一取点确实错开一帧读进真空（正文第 {GAP[0]} 帧，墨迹 "
          f"{old_ink}）/ 声明比画面多 3 帧仍判红 / 偏移早于正文起点直接拒绝 / "
          "门禁与包装器的帧号口径静态在场")


if __name__ == "__main__":
    main()
