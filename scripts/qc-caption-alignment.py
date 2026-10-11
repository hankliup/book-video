#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""字幕-配音对齐验收：证明成片某一秒画面上显示的字幕，就是字幕轨在该时刻应该显示的那一条。

原理：取成片真实帧，与同一时刻的正文背景帧（body-bg.mp4）在字幕带作差，得到画面上
真正"长出来"的字幕墨迹；再和每个候选字幕条（cap-*.png 的 alpha 墨迹）算 IoU。
IoU 最大者 = 画面实际显示的那一条，与「该时刻应显示」对照即可量化时间偏移
（分段渲染 shiftChildren 从未生效的那类音画错位）。

为什么不用 MAD 直接挑最小：MAD 会偏向"墨迹少"的候选（短字幕≈纯背景），
实测会把 12/16 个正确时刻误判成 MISMATCH。IoU 按候选自身面积归一，不受长度影响。

需要浮点兜底渲染留下的中间产物（body-bg.mp4 + cap-*.png + caption-track.json）。

取帧必须按**帧号**，不能按四舍五入的秒。渲染器把每条字幕锚到 `round(t*fps)` 那一帧
（`render_scene_map_template_fallback.py` 的 `frame_of()`），而 `n/fps` 在 30fps 下绝大多数
是无限循环小数：`f"{2006/30:.4f}"` = `66.8667` 已经**大于**第 2006 帧的 pts，ffmpeg 取的是
「pts ≥ seek」的首帧，于是读到第 2007 帧。每 3 个索引就有 1 个会这样错开一帧；再叠上
`check-episode-delivery.py` 用「钩子配音秒数 + 模板裁切秒数」算出的正文起点比真实帧网格晚
0.4 帧，《阿特拉斯耸耸肩》2026-10-07 的 10 个「带内零墨迹」全部落在行尾与下一行之间那
1~2 帧真空中 —— 画面上字幕好好在，门禁看的是隔壁那一帧。修法不是放宽阈值，也不是把
「边界空帧」的豁免加长（两者一律不动），而是让门禁问它想问的那一帧。

用法：
  python3 scripts/qc-caption-alignment.py <final.mp4> <caption-track.json> <cap目录> <body-bg.mp4> \
      --intro-trim 3.0 [--pre-body-frames 305] [--fps 30] [--times 12,45,90,...] [--span 12] [--n 16]
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np
from PIL import Image  # noqa: E402

W, H = 720, 960
BAND = (0, 680, W, 850)          # 字幕带：bottom 126px + 两行 56/50px

# argmax 判「画面此刻是哪一条」时，两条只差一个字（她握了七十二秒 / 她哭了七十二秒）的
# 近邻字幕，IoU 差只有千分之几，这个量级就是编码噪声。《三体3·死神永生》2026-10-02 的
# 138.96s 与 140.58s 两点因此被判 MISMATCH，而把成片那一帧裁出来看，画面上正是应显示的那条。
# 差距小于本阈值时不再信 argmax，改看两条候选**墨迹不同的那片像素**上画面有没有墨（见 diff_vote）。
NEAR_TIE = 0.02


def diff_vote(frame_ink, ink_a, ink_b):
    """两条字幕只在少数像素上不同时，用那片像素判定哪条真的在画面上。

    返回 >0 表示 a 在屏，<0 表示 b 在屏，0 表示两条墨迹等价（无从区分，也不构成错配）。
    这是把「谁在屏」这个问题问回到唯一能回答它的证据上：两条形不同的那几个字。
    """
    diff = ink_a ^ ink_b
    if int(diff.sum()) < 60:
        return 0
    a_only = int((ink_a & ~ink_b & frame_ink).sum())
    b_only = int((ink_b & ~ink_a & frame_ink).sum())
    return a_only - b_only



def grab(path, t, out):
    # 必须带 -y：同名输出已存在时 ffmpeg 会弹「Overwrite? [y/N]」，非交互下直接放弃并留下
    # 上一轮的旧帧，比对结果全是假 MISMATCH。
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.6f}",
                        "-i", path, "-frames:v", "1", "-pix_fmt", "rgb24", out],
                       capture_output=True, stdin=subprocess.DEVNULL)
    if r.returncode != 0 or not os.path.exists(out):
        raise SystemExit(f"取帧失败 {path}@{t}: {r.stderr.decode()[-300:]}")
    return Image.open(out).convert("RGB")


def probe_fps(path, default=30.0):
    """探测成片的 r_frame_rate（形如 "30/1"）。取帧按帧号，所以这个数只用来换算偏移。"""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    try:
        num, den = r.stdout.strip().split("/")
        fps = float(num) / float(den)
    except Exception:
        return default
    return fps if fps > 0 else default


def seek_time(n, fps):
    """第 n 帧的 seek 秒数：落在第 n 帧前**半帧**处。

    ffmpeg 取「pts ≥ seek」的首帧，所以半帧的提前量把候选唯一钉在第 n 帧上：
    第 n-1 帧的 pts = n/fps - 1/fps 在这之前，第 n 帧的 pts = n/fps 在这之后。
    绝不写 `f"{n/fps:.4f}"` —— 那是把无限循环小数往上进位，读回来的是第 n+1 帧。
    """
    return (n + 0.0) / fps - 0.5 / fps


def grab_frame(path, n, out, fps):
    return grab(path, seek_time(n, fps), out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("track")
    ap.add_argument("capdir")
    ap.add_argument("bg")
    ap.add_argument("--intro-trim", type=float, default=3.0)
    ap.add_argument("--pre-body-frames", type=int, default=-1,
                    help="正文第 0 帧在成片里的帧号；不给则按 round(intro_trim*fps) 换算")
    ap.add_argument("--fps", type=float, default=0.0, help="0=按成片探测，默认再退到 30")
    ap.add_argument("--times", default="")
    ap.add_argument("--n", type=int, default=16, help="未给 --times 时的均匀采样点数")
    ap.add_argument("--span", type=float, default=12.0, help="候选字幕的时间搜索半径（秒）")
    ap.add_argument("--ink-thresh", type=int, default=28, help="帧差判定为字幕墨迹的阈值")
    ap.add_argument("--min-ink", type=int, default=300, help="候选最少墨迹像素数，低于则不参与判定")
    ap.add_argument("--work", default="/tmp/qc-caption-align")
    args = ap.parse_args()

    track = json.load(open(args.track, encoding="utf-8"))
    by_index = {}
    listing = os.listdir(args.capdir)
    for i, item in enumerate(track):
        # 必须按 track 的 (index, order) 取精确文件名：字幕重切后同一 index 会留下
        # 上一版的 cap-XXX-<旧order>.png，glob 首条命中会把旧字幕当候选，报出一片假 MISMATCH。
        exact = f"cap-{i:03d}-{item['order']}.png"
        pngs = [exact] if exact in listing else [
            f for f in listing if f.startswith(f"cap-{i:03d}-") and f.endswith(".png")]
        if pngs:
            by_index[i] = os.path.join(args.capdir, pngs[0])
    if not by_index:
        raise SystemExit("没找到 cap-*.png，无法比对（检查 --capdir）")

    if args.times:
        times = [float(x) for x in args.times.split(",")]
    else:
        last = args.intro_trim + track[-1]["end"]
        times = [round(args.intro_trim + (last - args.intro_trim) * k / args.n, 2)
                 for k in range(1, args.n + 1)]

    os.makedirs(args.work, exist_ok=True)
    fps = args.fps or probe_fps(args.video)
    pre_frames = (args.pre_body_frames if args.pre_body_frames >= 0
                  else round(args.intro_trim * fps))
    bad = 0
    checked = 0
    print(f"{'成片秒':>8} {'正文秒':>8}  {'应显示':<28} {'实测最佳匹配':<28} 判定")
    for t in times:
        # 先量化到帧网格再换算正文秒：成片第 n 帧 ↔ 正文第 (n - pre_frames) 帧，
        # 而「这一帧该显示哪一条」必须问同一个网格，否则期望和像素差半帧就永远对不上。
        n_film = round(t * fps)
        n_bg = n_film - pre_frames
        if n_bg < 0:
            raise SystemExit(f"取点 {t}s 落在正文起点之前（pre_body_frames={pre_frames}, fps={fps}）："
                             "偏移传错了。在钩子段里找正文字幕，得到的全是空屏，"
                             "这道门就会把没检查过的画面报成绿灯。")
        bt = n_bg / fps
        expect_i = next((i for i, x in enumerate(track) if x["start"] <= bt < x["end"]), None)
        if expect_i is None:
            print(f"{t:8.2f} {bt:8.2f}  （该时刻无字幕）")
            continue
        frame = np.asarray(grab_frame(args.video, n_film, os.path.join(args.work, "frame.png"),
                                      fps).crop(BAND), dtype=np.int16)
        bgf = grab_frame(args.bg, n_bg, os.path.join(args.work, "bg.png"), fps).crop(BAND)
        bg = np.asarray(bgf, dtype=np.int16)
        frame_ink = (np.abs(frame - bg).max(axis=2) > args.ink_thresh)

        def _disp(item):
            # 有 display 字段时按「真正上屏的行」打印（CSV 原文带标点，但成片字幕不显示标点，
            # 拿原文比对标签会让人误判画面错了行）。
            return item.get("display") or item["text"].replace(" ", "/")

        # 采样点撞上字幕上/下沿的那一帧时，成片按 round(t*fps) 锚帧已经把这条收走了，
        # 带内自然一片空白：IoU=0.000、次优差=0.000，看着像错位其实只是 ±1 帧的采样边界
        # （《摩诃婆罗多》去标点重渲实测 4/224 点全是这一类）。真·漏字幕（带内零墨迹且不在边界）
        # 仍然按 MISMATCH 计，不会被这条规则放过去。
        if int(frame_ink.sum()) < args.min_ink:
            item = track[expect_i]
            edge = 1.0 / 30.0
            if item["end"] - bt < edge or bt - item["start"] < edge:
                print(f"{t:8.2f} {bt:8.2f}  {_disp(item)[:28]:<28} {'':<28} "
                      f"边界空帧 帧{n_bg}（采样撞上下沿 ±1 帧，不计错配）")
                continue
            print(f"{t:8.2f} {bt:8.2f}  {_disp(item)[:28]:<28} {'画面上没有字幕':<28} "
                  f"MISMATCH  带内零墨迹 帧{n_bg}")
            checked += 1
            bad += 1
            continue

        scores = []
        for i, png in by_index.items():
            if abs(track[i]["start"] - track[expect_i]["start"]) > args.span:
                continue
            cap = Image.open(png).convert("RGBA").crop(BAND)
            comp = bgf.copy()
            comp = comp.convert("RGBA")
            comp.alpha_composite(cap)
            comp_rgb = np.asarray(comp.convert("RGB"), dtype=np.int16)
            # 候选的墨迹必须和画面上那团墨迹**同一个量法**：字幕带一条深灰投影（alpha 75~110），
            # 压在暗照片上时「成片 − 背景」的差低于 ink_thresh，画面上根本看不见它，而按
            # alpha>70 数的候选墨迹把它算进来 —— 候选于是比实测量大一倍，IoU 按候选自身面积
            # 归一，反而偏爱「墨迹更少」的那条错字幕。《名利场》2026-10-10 正文 68.30s：
            # 屏上是「眼睛亮」（覆盖帧墨迹 99.9%），候选墨迹 9230px；「话不多」只覆盖 90%
            # 却因为只有 7489px 在 IoU 上赢 0.025，次优差又刚好压在 NEAR_TIE 之上，
            # 近平局表决不触发，348 点里栽了 2 点。改法是把候选也按「叠到同一帧背景上之后
            # 看得不见的不算墨迹」来数：投影在亮背景上自然算、在暗背景上自然不算，
            # 和帧墨迹的口径就对齐了。
            cap_ink = (np.abs(comp_rgb - bg).max(axis=2) > args.ink_thresh)
            if int(cap_ink.sum()) < args.min_ink:
                continue
            inter = int((cap_ink & frame_ink).sum())
            union = int((cap_ink | frame_ink).sum())
            mad = float(np.abs(frame - comp_rgb).mean())
            scores.append((1.0 - inter / max(1, union), i, mad, cap_ink))
        if not scores:
            print(f"{t:8.2f} {bt:8.2f}  无可比对候选（墨迹过少）")
            continue
        scores.sort(key=lambda s: (s[0], s[1]))
        best_d, best, best_mad, best_ink = scores[0]
        margin = scores[1][0] - best_d if len(scores) > 1 else float("inf")
        exp_txt = _disp(track[expect_i])
        got_txt = _disp(track[best])
        ok = best == expect_i
        note = f"帧{n_bg} IoU={1 - best_d:.3f} MAD={best_mad:.2f} 次优差={margin:.3f}"
        if not ok and margin < NEAR_TIE:
            # 近平局：argmax 在这个量级上分不开两条只差一字的字幕，改问「两条长得不一样
            # 的那几个字，画面上到底是哪一组墨迹」。改判要有正证据（应显示条的独有墨迹），
            # 单纯「分不开」不放行——墨迹等价才算这条字幕本来就无从区分。
            exp_ink = next((ink for _, i, _, ink in scores if i == expect_i), None)
            if exp_ink is not None:
                vote = diff_vote(frame_ink, exp_ink, best_ink)
                if vote > 0:
                    ok, got_txt = True, exp_txt
                    note += f" 近平局改判（应显示条独有墨迹 +{vote}px）"
                elif vote == 0:
                    ok = True
                    note += " 近平局且两条墨迹等价（不构成错配）"
                else:
                    note += f" 近平局仍判错（实测条独有墨迹 +{-vote}px）"
        checked += 1
        bad += (not ok)
        print(f"{t:8.2f} {bt:8.2f}  {exp_txt[:28]:<28} {got_txt[:28]:<28} "
              f"{'OK' if ok else 'MISMATCH'}  {note}")
    print(f"\n有效比对 {checked} 点，错配 {bad} 点")
    sys.exit(1 if bad or not checked else 0)


if __name__ == "__main__":
    main()
