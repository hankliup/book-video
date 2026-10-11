#!/usr/bin/env python3
"""片头原样核对 —— 把 2026-10-05 的用户裁决落到**交付像素**上。

裁决：「恢复原来的固定开场白效果，只改动钩子视频的闪屏问题」。共享片头的**源码**回滚
已经由 npm run check 守死（七张底图与 media/_raw/ 逐字节相同、.template-blackout 仍被
时间轴驱动、intro_runway.py / grade-template-pages.py 不许回来）。但源码回滚证明不了一部
**已经渲出去的成片**：判据②~⑥ 按裁决把片头窗豁免成「只报告不判红」，所以一部在回滚之前
渲的片子照样能十道门全绿——它里面就是 10-04 那版被否决的片头。

这道门读成片开场的那几百帧，用三个各自独立的证人问同一件事：片头是不是原样。
三个证人全部从模板真源推出来（底图文件本身 + scripts/lib/intro-template.json 的时刻表），
不引入第二个亮度口径，也不给片头加任何绝对亮度上下限——亮度只有一个真源，
那是编辑选择而不是缺陷（AGENTS.md「It still does not cap peak brightness」）。

  W1 接缝黑场   钩子段尾 hook_fade_out_seconds 淡出到黑 + 模板自己的 0.13s 全黑起幅，
               ⇒ 片头起点附近必须有帧 <BLACK_MAX。回滚前那版把钩子末帧垫 0.5s 再 0.6s
               等长溶解，接缝从来到不了黑（实测《幽谷百合》11:55 版整窗口 0 帧黑）。
  W2 底图电平   滚动书单那 1s 里的每个电平都必须落在 media/_raw/pages/*.jpg 在 720x960
               可见窗内的均值上（容差 LEVEL_TOL）。10-04 那版把六张图归一到 155~185 亮带，
               离原图平台 ≥38 级，任何时间偏移都遮不住这件事。
  W3 砸黑一拍   书单→结果页的 .template-blackout 必须还在（模板时间 result_page_at 之前
               一小段里出现全黑）。回滚前那版把它改成了 0.50s 交叉溶解。

红 = 这部片子的片头不是回滚后的原样，唯一解法是重渲该段（不是压暗、不是放宽阈值、
不是改装配层）。认不出三段结构（没有 script-hook.csv / 没有钩子配音 / 推不出本期目录）
时印 ⚠️ 无从证明 并以 0 退出——宁可明说看不见，也不拿一个猜出来的窗口去判红。

用法：
  python3 scripts/check-intro-is-original.py "<书名>"
  python3 scripts/check-intro-is-original.py --all
  python3 scripts/check-intro-is-original.py <mp4> [<mp4> …]
  python3 scripts/check-intro-is-original.py <mp4> --as "<书名>"
      把一个**不在 episodes/ 里**的成片（例如留作对照的回滚前副本）按某一期的三段结构来判：
      钩子时长从 episodes/<书名>/audio/hook-voiceover-story.mp3 取。没有它，仓库外的 mp4 只能
      报「无从证明」，而对照恰好都是仓库外的副本——一道只能判绿自己目录的门证不了任何事。
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib.util as _ilu  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = _ilu.spec_from_file_location(
    "lf", os.path.join(_ROOT, "scripts", "check-luminance-flicker.py"))
lf = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(lf)

INTRO_LIB_PATH = os.path.join(_ROOT, "scripts", "lib", "intro-template.json")
RAW_PAGE_GLOB = os.path.join(
    _ROOT, "templates", "shared-video-template", "intro", "media", "_raw", "pages", "scroll-*.jpg")

BLACK_MAX = 8.0        # 「到了黑」的灰度上限：模板自己的黑场起幅实测 0.0
LEVEL_TOL = 12.0       # 电平离**原图**平台的容差：健康片实测 ≤5；10-04 那版把六张图归一到
#                       155~185 亮带后，离原图平台 ≥38 级，容差不许为它让步
LEVEL_MIN_FRAMES = 3   # 一个电平至少帧数（0.1s）：短于此的是换页那一拍，不是平台
MIN_LEVELS = 3         # 少于这么多个平台就无从判断（窗口没落在书单上）
TAIL_PAD = 0.30        # 结果页前留白：那之后是**每期自己**的 result-bridge 拷贝，不属于模板
ROLLING_END_BACK = 0.30


def load_lib():
    with open(INTRO_LIB_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def fps_of(path):
    txt = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate", "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True).stdout.strip()
    try:
        num, den = txt.split("/")
        f = float(num) / float(den)
    except (ValueError, ZeroDivisionError):
        return 0.0
    return f if f > 0 else 30.0


def raw_page_levels():
    """六张共享底图在模板可见窗（720x960，background-size:cover + center）里的探针均值。

    走和门禁**同一个**解码器与同一个缩放（36x48 灰度），免得这里用 Pillow、那里用
    libswscale，两把尺子量出两个平台。
    """
    levels = []
    for p in sorted(glob.glob(RAW_PAGE_GLOB)):
        cmd = ["ffmpeg", "-v", "error", "-i", p, "-frames:v", "1",
               "-vf", "scale=720:960:force_original_aspect_ratio=increase,"
                      "crop=720:960,scale=36:48,format=gray",
               "-f", "rawvideo", "-"]
        raw = subprocess.run(cmd, capture_output=True).stdout
        a = np.frombuffer(raw, dtype=np.uint8)
        if a.size == 36 * 48:
            levels.append(float(a.mean()))
    return levels


def probe_window(path, t0, t1, fps):
    """[t0,t1) 秒的逐帧均亮度（从第 0 帧一起解，索引才是整数帧）。"""
    limit = int(round(t1 * fps))
    curve = lf.frame_luma(path, limit=limit)
    i0 = max(0, int(round(t0 * fps)))
    return curve[i0:limit], i0


def black_runs(curve, fps, t0):
    """这一段里的全黑帧，返回 (帧数, 最早一个黑帧的模板时间)"""
    idx = np.where(np.asarray(curve) < BLACK_MAX)[0]
    if not len(idx):
        return 0, None
    return int(len(idx)), round(t0 + float(idx[0]) / fps, 3)


def check_one(path, lib, levels, ep_override=None):
    ep = ep_override or lf.episode_dir_of(path)
    fps = fps_of(path)
    if not ep or not fps:
        return "unknown", "认不出本期结构或读不到帧率：无从证明"
    if not os.path.exists(os.path.join(ep, "script-hook.csv")):
        return "unknown", "两段结构（没有 script-hook.csv），片头就是第一个镜头：不适用"
    hook_dur = round(lf.media_duration(
        os.path.join(ep, "audio", "hook-voiceover-story.mp3")), 2)
    if hook_dur <= 0:
        return "unknown", "读不到 audio/hook-voiceover-story.mp3 的时长：无从证明"
    trim = float(lib["trim_seconds"])
    scroll_start = float(lib["scroll_start"])
    result_at = float(lib["result_page_at"])
    hook_fade = float(lib.get("hook_fade_out_seconds", 0.0))

    curve, _ = probe_window(path, 0.0, hook_dur + trim + 0.2, fps)
    n = len(curve)
    need = int(round((hook_dur + trim) * fps))
    if n < need:
        return "unknown", f"只解出 {n} 帧 < 片头末尾 {need} 帧：无从证明"

    def seg(a, b):
        return curve[int(round(a * fps)):max(int(round(a * fps)), int(round(b * fps)))]

    fails, notes = [], []

    # W1 接缝黑场：钩子段尾淡出 + 模板自己的全黑起幅
    w0 = max(0.0, hook_dur - hook_fade - 0.15)
    cnt, first = black_runs(seg(w0, hook_dur + 0.30), fps, w0)
    if cnt:
        notes.append(f"W1 接缝黑场 ✅ {cnt} 帧（模板 {first:+.2f}s 起）")
    else:
        fails.append("W1 接缝不到黑：装配层又在把钩子末帧垫帧/溶解，那是 10-04 那版片头")

    # W2 底图电平：滚动书单窗内的每个平台都得落在**未调色原图**（media/_raw/pages）的平台上
    r0, r1 = hook_dur + scroll_start, hook_dur + result_at - ROLLING_END_BACK
    rc = seg(r0, r1)
    held = lf.held_levels(rc, min_frames=LEVEL_MIN_FRAMES)
    if len(held) < MIN_LEVELS:
        notes.append(f"W2 底图电平 ⚠️ 只认出 {len(held)} 个平台（需 ≥{MIN_LEVELS}）：不计入判决")
    else:
        worst, worst_lv = 0.0, None
        for _, _, lv in held:
            d = min(abs(lv - x) for x in levels)
            if d > worst:
                worst, worst_lv = d, lv
        if worst <= LEVEL_TOL:
            notes.append(f"W2 底图电平 ✅ {len(held)} 个平台全部落在原图平台上"
                         f"（最大偏离 {worst:.1f} 级 ≤ {LEVEL_TOL:g}）")
        else:
            fails.append(f"W2 底图被改过：{len(held)} 个平台里最偏离的一个离原图 {worst:.1f} 级"
                         f"（>{LEVEL_TOL:g}，实测电平 {worst_lv:.1f}）——片头的底图被重新调过亮度，"
                         "而用户 2026-10-05 要的是按 media/_raw 原图交付")

    # W3 砸黑一拍：书单→结果页的 .template-blackout 必须还在像素里（2026-10-05 裁决：
    # 那一拍的下探全黑是设计，不是缺陷——「恢复原来的固定开场白效果」）。判据是结果页定格
    # 前 0.5s 的窗口里**必须有全黑帧**；没有黑 = 有人把它改成了直接溶解，那是被撤回的那版。
    b0, b1 = hook_dur + result_at - 0.50, hook_dur + result_at + 0.10
    cnt3, first3 = black_runs(seg(b0, b1), fps, b0)
    if not cnt3:
        fails.append("W3 书单→结果页的砸黑一拍不见了：那一拍又被改成了直接溶解，"
                     "而用户 2026-10-05 要的是原样（实测原样是 86→0→44 / 233ms）")
    else:
        notes.append(f"W3 砸黑一拍 ✅ {cnt3} 帧（模板 {first3:+.2f}s 起）")

    detail = "；".join(notes)
    if fails:
        return "red", detail + "\n      · " + "\n      · ".join(fails)
    return "green", detail


def episode_finals():
    out = []
    for p in sorted(glob.glob(os.path.join(_ROOT, "episodes", "*", "renders", "*-final.mp4"))):
        out.append(p)
    return out


def main(argv):
    lib = load_lib()
    levels = raw_page_levels()
    if len(levels) < 2:
        print("❌ 读不到 media/_raw/pages/scroll-*.jpg，没有基准可比")
        return 1
    args, ep_override, i = [], None, 0
    while i < len(argv):
        a = argv[i]
        if a == "--as":
            if i + 1 >= len(argv):
                print("❌ --as 后面要跟书名")
                return 2
            name = argv[i + 1]
            ep_override = os.path.join(_ROOT, "episodes", name)
            if not os.path.isdir(ep_override):
                print(f"❌ --as：episodes/{name} 不存在，没有结构可参照")
                return 1
            i += 2
            continue
        if not a.startswith("--"):
            args.append(a)
        i += 1

    if "--all" in argv:
        paths = episode_finals()
    elif args:
        paths = []
        for a in args:
            if a.endswith(".mp4"):
                paths.append(a if os.path.isabs(a) else os.path.join(_ROOT, a))
            else:
                paths.append(os.path.join(_ROOT, "episodes", a, "renders", f"{a}-final.mp4"))
    else:
        print(__doc__)
        return 2

    print(f"片头原样核对 ｜ 基准＝media/_raw/pages 六张**未调色原图**在 720x960 可见窗内的平台："
          f"{sorted(round(x, 1) for x in levels)}")
    print(f"           ｜ 时刻表＝scripts/lib/intro-template.json"
          f"（scroll_start={lib['scroll_start']} result_page_at={lib['result_page_at']} "
          f"trim={lib['trim_seconds']} hook_fade={lib.get('hook_fade_out_seconds')}）")
    reds, unknowns = [], []
    for p in paths:
        name = os.path.basename(os.path.dirname(os.path.dirname(p))) if p.endswith("-final.mp4") \
            else os.path.basename(p)
        if not os.path.exists(p):
            reds.append((name, "成片不存在"))
            print(f"  ❌ {name:14s} 成片不存在")
            continue
        verdict, detail = check_one(p, lib, levels, ep_override)
        mark = {"green": "✅", "red": "❌", "unknown": "⚠️"}[verdict]
        print(f"  {mark} {name:14s} {detail}")
        if verdict == "red":
            reds.append((name, detail))
        elif verdict == "unknown":
            unknowns.append(name)
    # 末行必须是 tally：check-episode-delivery.py 用 run_gate 取「最后一行」当汇总结论，
    # 把「只有重渲该段」的名字列表留在后面会让汇总里出现一个孤零零的 ·《书名》。
    if reds:
        print("\n不通过的都是**回滚之前渲的片头**，压暗、放宽阈值、改装配层都不解决它——只有重渲该段：")
        for n, _ in reds:
            print(f"  · {n}")
    print(f"\n片头原样核对：{len(paths) - len(reds) - len(unknowns)} 通过 / "
          f"{len(reds)} 不通过 / {len(unknowns)} 无从证明")
    return 1 if reds else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
