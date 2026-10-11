#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""片头原样门的正反对照：用 ffmpeg 渲出几种开场形状，要求判决跟着形状翻面。

为什么必须有它（2026-10-05 建）：判据②~⑥ 不看共享片头窗之后，十道门里没有任何一道看得见
交付像素里的片头长什么样。这道门就是「片头 == 2026-10-03 交付那份」的唯一机械凭据。
每个证人必须有「形状在→绿」和「形状不在→红，且只有它那一项红」的一对：

  · W1 接缝黑场：钩子段尾淡出（hook_fade_out_seconds）+ 模板自己的全黑起幅必须在；
  · W2 底图电平：滚动书单的电平必须落在**未调色原图**（media/_raw/pages）的平台上——
    拿 10-04 那版 155~185 亮带渲的片在这里红，拿压暗底板渲的片也在这里红
    （AGENTS.md「never fix by dimming」：压暗和提亮都不许用来换绿灯）；
  · W3 砸黑一拍：书单→结果页的 .template-blackout 必须还在像素里。那一拍的下探全黑
    （实测 86→0→44 / 233ms）是 2026-10-03 交付效果的一部分，2026-10-05 用户裁决
    「恢复原来的固定开场白效果」把它带了回来；把它改成直接溶解就是被撤回的那版。

口径不重写：绿版用的页面就是 templates/…/media/_raw/pages/scroll-*.jpg 本体（未调色），
走门禁自己那个 36x48 缩放；判决跑的是 check-intro-is-original.check_one() 本身。
用法：python3 scripts/tests/test-intro-is-original.py
"""
from __future__ import annotations

import glob
import importlib.util
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TMP = os.path.join(ROOT, "tmp", "test-intro-is-original")

FPS = 30
HOOK = 4.0                      # 合成钩子时长，帧数 = 120

# 2026-10-04 那版底图归一的实测平台（media/pages 被 grade-template-pages.py 原地改写后
# 的可见窗均值）。这里只当**反例形状**用：它离 _raw 原图平台 ≥38 级，必须被 W2 判红，
# 而 W2 的基准永远是现场量出来的原图，不是这张表。
GRADED_BAND = [167.0, 171.0, 168.0, 173.0, 170.0]


def load(mod_name, rel):
    spec = importlib.util.spec_from_file_location(mod_name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GATE = load("intro_original", "scripts/check-intro-is-original.py")
LIB = GATE.load_lib()
PAGE_JPGS = sorted(glob.glob(GATE.RAW_PAGE_GLOB))


# 帧号全部钉在整数帧上，窗口才不会靠四舍五入对表。
F_BLACK_A = (120, 126)     # 模板自己的全黑起幅
F_PRE = (126, 152)         # 滚动书单之前的底图（门禁不判这段）
F_PAGES = [(152, 158), (158, 164), (164, 170), (170, 176), (176, 182)]
F_DARK = (182, 188)        # .template-blackout 砸黑一拍
F_RESULT = (188, 210)      # 结果页（本期自己的 result-bridge 拷贝，量级不属于模板）
F_BODY = (210, 240)


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"ffmpeg 失败：{' '.join(cmd)}\n{r.stderr[-1200:]}")


def hexc(level):
    v = max(0, min(255, int(round(level))))
    return f"0x{v:02x}{v:02x}{v:02x}"


def seg_from_color(idx, a, b, level, fade_out=False):
    """[a,b) 帧的一块纯色，必要时在块内淡到黑。"""
    n = b - a
    src = f"color=c={hexc(level)}:s=720x960"
    vf = "format=yuv420p"
    d = n / FPS
    if fade_out:
        # 淡出放在这块自己的末尾：钩子段尾 hook_fade_out_seconds 淡出
        start = max(0.0, d - float(LIB["hook_fade_out_seconds"]))
        vf = (f"fade=t=out:st={start:.4f}:d={d - start:.4f},format=yuv420p")
    out = os.path.join(TMP, f"seg{idx:02d}.mp4")
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", src,
         "-vf", vf, "-frames:v", str(n), "-r", str(FPS), "-an",
         # 全范围标记：默认 limited 将把 colorlevels 的 0x00 编码成 y=16，探针读出的
         # 「黑」就是 16 > BLACK_MAX(8)，砸黑一拍会被当成不存在。真片走的是 CSS 黑，
         # 实测 0.0；合成对照必须落在同一个读数上，否则测的是编码器而不是证人。
         "-color_range", "pc",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "4", "-g", "1", out])
    return out


def seg_from_image(idx, a, b, path):
    n = b - a
    out = os.path.join(TMP, f"seg{idx:02d}.mp4")
    run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", path,
         "-vf", "scale=720:960:force_original_aspect_ratio=increase,crop=720:960,"
                "format=yuv420p",
         "-frames:v", str(n), "-r", str(FPS), "-an",
         "-color_range", "pc",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "4", "-g", "1", out])
    return out


def build_film(name, *, graded=False, dim=False, seam_black=True, blackout=True):
    """按开关拼一部 8 秒的开场片子；每个开关只动一个证人的证据。"""
    segs = []
    # 钩子段本体
    hook_level = 60
    segs.append(seg_from_color(0, 0, 120, hook_level, fade_out=seam_black))
    i = 1
    if seam_black:
        segs.append(seg_from_color(i, *F_BLACK_A, 0))
        i += 1
        pre_a = F_BLACK_A[1]
    else:
        pre_a = F_BLACK_A[0]
    segs.append(seg_from_color(i, pre_a, F_PRE[1], 95))
    i += 1
    for k, (a, b) in enumerate(F_PAGES):
        if graded:
            # 2026-10-04 那版的亮带归一：构图没动，只有亮度被摊进 155~185
            segs.append(seg_from_color(i, a, b, GRADED_BAND[k]))
        elif dim:
            lv = [40.0, 42.0, 38.0, 44.0, 41.0][k]
            segs.append(seg_from_color(i, a, b, lv))
        else:
            segs.append(seg_from_image(i, a, b, PAGE_JPGS[k % len(PAGE_JPGS)]))
        i += 1
    # F_DARK：blackout=True 时是原样那一拍砸进全黑（W3 正例）；False 时是溶解中段的
    # 一个中间电平（W3 反例——跑道里没有黑帧 = .template-blackout 被摘了）
    segs.append(seg_from_color(i, *F_DARK, 0 if blackout else 150))
    i += 1
    segs.append(seg_from_color(i, *F_RESULT, 45))
    i += 1
    segs.append(seg_from_color(i, *F_BODY, 90))
    lst = os.path.join(TMP, f"{name}.txt")
    with open(lst, "w", encoding="utf-8") as fh:
        for s in segs:
            fh.write(f"file '{os.path.basename(s)}'\n")
    out = os.path.join(TMP, f"{name}.mp4")
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst,
         "-c", "copy", "-movflags", "+faststart", out])
    n = int(subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                            "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", out],
                           capture_output=True, text=True).stdout.strip() or 0)
    if n != F_BODY[1]:
        raise SystemExit(f"{name}：拼出来 {n} 帧，期望 {F_BODY[1]} 帧 —— 合成口径变了，测试作废")
    return out


def make_episode(dirname, *, with_hook_csv=True, with_audio=True):
    ep = os.path.join(TMP, dirname)
    shutil.rmtree(ep, ignore_errors=True)
    os.makedirs(os.path.join(ep, "audio"), exist_ok=True)
    if with_hook_csv:
        with open(os.path.join(ep, "script-hook.csv"), "w", encoding="utf-8") as fh:
            fh.write("order,text\n1,测试\n2,测试\n")
    if with_audio:
        run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", f"anullsrc=r=44100:cl=mono:d={HOOK}", "-c:a", "libmp3lame",
             os.path.join(ep, "audio", "hook-voiceover-story.mp3")])
    return ep


def witness_states(detail):
    """从结论串里认出三个证人各自绿/红。"""
    out = {}
    out["W1"] = "W1 接缝不到黑" in detail
    out["W2"] = "W2 底图被改过" in detail
    out["W3"] = "W3 书单→结果页的砸黑一拍不见了" in detail
    return out


def main():
    if not PAGE_JPGS:
        raise SystemExit(f"读不到 {GATE.RAW_PAGE_GLOB}")
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP, exist_ok=True)
    levels = GATE.raw_page_levels()
    ep = make_episode("健康期")
    failures = []

    def expect(path, ep_dir, want, *, only=None, label=""):
        verdict, detail = GATE.check_one(path, LIB, levels, ep_dir)
        st = witness_states(detail)
        reds = {k for k, v in st.items() if v}
        if verdict != want:
            failures.append(f"{label}：判决 {verdict}，期望 {want} —— {detail}")
            print(f"  ❌ {label} → {verdict}")
            return
        if want == "red":
            if only is not None and reds != set(only):
                failures.append(f"{label}：红的是 {sorted(reds)}，期望只有 {sorted(only)}")
                print(f"  ❌ {label} → red 但证人不对：{sorted(reds)}")
                return
            print(f"  ✅ {label} → red（{sorted(reds)}）")
        elif want == "green":
            if reds:
                failures.append(f"{label}：绿版里 {sorted(reds)} 却红了")
                print(f"  ❌ {label} → green 但含红证人")
                return
            print(f"  ✅ {label} → green")
        else:
            print(f"  ✅ {label} → {verdict}")

    healthy = build_film("healthy")
    expect(healthy, ep, "green", label="2026-10-03 原样（未调色底图＋接缝淡出＋砸黑一拍）")

    band = build_film("graded_band", graded=True)
    expect(band, ep, "red", only={"W2"}, label="底图被摊进 155~185 亮带（10-04 那版归一）")

    dimmed = build_film("dimmed", dim=True)
    expect(dimmed, ep, "red", only={"W2"}, label="压暗换绿灯（AGENTS.md 明令禁止的修法）")

    nb = build_film("blackout_removed", blackout=False)
    expect(nb, ep, "red", only={"W3"}, label="砸黑一拍被改成直接溶解（2026-10-06 被撤回的那版）")

    ns = build_film("no_seam_black", seam_black=False)
    expect(ns, ep, "red", only={"W1"}, label="接缝到不了黑（钩子末帧被垫住）")

    old = build_film("rollback_1004", graded=True, seam_black=False, blackout=False)
    expect(old, ep, "red", only={"W1", "W2", "W3"}, label="10-04 那版三个证人一起红")

    # 认不出结构时必须「无从证明」，不许拿猜出来的窗口判红
    no_csv = make_episode("无钩子期", with_hook_csv=False)
    expect(healthy, no_csv, "unknown", label="两段结构（无 script-hook.csv）")
    no_audio = make_episode("无钩子配音期", with_audio=False)
    expect(healthy, no_audio, "unknown", label="读不到钩子配音时长")

    print("\n" + ("❌ 片头原样门对照失败：\n  - " + "\n  - ".join(failures)
                  if failures else "✅ 片头原样门对照全部符合预期（6 类形状 + 2 类无从证明）"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
