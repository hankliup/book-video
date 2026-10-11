#!/usr/bin/env python3
"""钩子段尾淡出（`hook_fade_out_seconds`）的回归：它是回滚之后**唯一保留**的抗闪屏装置。

2026-10-05 用户裁决「恢复原来的固定开场白效果，只改动钩子视频的闪屏问题」，于是装配层那套
（钩子末帧原地保持 `opening_runway_seconds` + `junction_dissolve_seconds` 等长交叉溶解）整套撤掉，
共享片头重新以「0.13s 全黑起幅 + ~0.47s 飞到亮度平台」开头——那是原效果的一部分，不许在片头上
动刀。三段结构里片头不再排在第一位，它前面是本期那张均亮度 40~60 的钩子照片，硬切过去就是
2026-10-04 第一次量到的那次眨眼（《嫌疑人X的献身》成片第 8.27s 单帧 42.4→0.0，而全片其余
8400 帧 p99 只有 2.7）；回滚之后片头窗豁免了**内部**，但**这对跨边界的接缝对仍然照判**，
所以亮照片砸进黑场依旧是一道红，不会被豁免掩盖。修法只在钩子这一路：**段尾淡出到黑**，
让片头自己的黑起幅接在一个已经在变暗的画面上。

这个文件钉四件事，缺一条这条装置就会在某个后续改动里悄悄失效：

  T1 lib 里的长度必须**够长**：`hook_fade_out_seconds ≥ 判据④的时间尺度 FLICKER_LEG_MAX_S`。
     够长才是「换气」，够短就是「频闪」——同一个装置，只差一个数。它也必须短到不吃掉钩子
     本体（≤1.0s，而钩子配音实测 6~8s）。
  T2 把门禁**自己的** `flickers()` 灌进三种同深度形状：按现长度**单调淡到黑** → 0 处；
     同样 42 级但在钩子里 0.12s 掉下去再 0.12s 爬回来 → ≥1 处（非空转证明）；
     腿长放到 1.5×判据④尺度 → 0 处（证明这条判据约束的是**时间尺度**，不是「画面不许变暗」，
     否则 T1 的下限就成了要求钩子末帧不许淡出）。
  T3 接缝那一对（淡出末帧 → 片头全黑第一帧）是回滚之后**唯一还被门禁判的接缝对**：片头内部
     的对按 `intro_pair` 豁免，爬回 171 的那 0.47s 本来就不参与判决，所以夹具只数到片头第一帧。
     有淡出时它 ≤ 判据① 的 40 且 ≤ 判据③ 的允许线（实测 42 档 3.5 级、90 档 7.5 级）；
     去掉淡出就是一帧 42 / 90 级 → 判据① 红。
  T4 两条渲染路径都必须**真的画它**，而且都从同一份 lib 取长度。这条是静态扫源码（先去整行
     注释，否则回滚说明里那句「不再有 tpad 克隆末帧 + 等长 xfade」会被当成代码报警）：
     `create-episode-preview.mjs` 之类改一次不会让成片变暗，但渲染器少写一个 `fade=t=out`
     就会——那是装配层缺陷，前九道门全绿也照样烧进成片。同时扫**反面**：回滚撤掉的
     `tpad=stop_mode=clone` / `xfade=transition=fade` / `junctionD=` 不许在片头那一路复活，
     片头仍然是 `trim=0:INTRO_TRIM` 一帧不裁。

整片级别的同一对照（真实跑门禁 `main()`：有淡出绿、没淡出红在判据①）在
`scripts/tests/test-flicker-rules.py` 的 R0 / R8 那一对里，本文件不重复实现一遍判决。

退出码：0 全部通过；1 有任何一条失败。
跑法：python3 scripts/tests/test-hook-seam-fade.py
"""
import importlib.util
import json
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
GATE_PATH = os.path.join(ROOT, "scripts", "check-luminance-flicker.py")
LIB_PATH = os.path.join(ROOT, "scripts", "lib", "intro-template.json")
PY_RENDERER = os.path.join(ROOT, "scripts", "render_scene_map_template_fallback.py")
JS_RENDERER = os.path.join(ROOT, "scripts", "render-episode-final.mjs")


def _load_gate():
    spec = importlib.util.spec_from_file_location("flicker_gate", GATE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = _load_gate()
FPS = 30.0
HOOK_PLATEAU = 42.0        # 钩子照片的实测均亮度量级（《嫌疑人X的献身》那片是 42.4）
HOOK_PLATEAU_BRIGHT = 90.0  # 最亮的那批（白天雪地、浅色室内）


def seam(fade_seconds, plateau=HOOK_PLATEAU):
    """接缝两侧的逐帧亮度：钩子平台 →（可选）段尾淡出到黑 → 片头的全黑第一帧。

    只数到片头第一帧为止：片头内部的对按门禁口径（`judged_segments` / 判据①的 `intro_pair`）
    豁免，把它们夹进夹具会把「淡出没用」误判成「淡出有用」——爬回 171 的那 0.47s 是全片最大
    的单帧步长，而它本来就不参与判决。
    """
    n = max(1, int(round(fade_seconds * FPS)))
    fade_frames = [plateau * (1.0 - (k + 1) / n) for k in range(n)] if fade_seconds > 0 else []
    frames = [plateau] * 2 + fade_frames + [0.0]      # 0.0 = 片头自带的黑起幅第一帧
    return np.asarray(frames, dtype=float)


def dip(plateau, leg_seconds):
    """钩子**本体**里的一次下探再爬回（不是淡出到黑，是掉下去又回来）。"""
    n = max(1, int(round(leg_seconds * FPS)))
    frames = [plateau] * 2
    frames += [plateau * (1.0 - (k + 1) / n) for k in range(n)]
    frames += [plateau * (k + 1) / n for k in range(n)]
    frames += [plateau] * 2
    return np.asarray(frames, dtype=float)


def strip_comments(src):
    """去掉整行注释，只留代码。

    T4 扫的是「装配层有没有真的这么渲染」，而两条渲染路径的 rollback 说明里就写着
    `xfade=transition=fade`「等价」和「不再有 tpad 克隆末帧」——拿原文扫会把解释当成代码，
    把守门人变成假警报制造机。
    """
    keep = []
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("#") or s.startswith("//") or s.startswith("/*") or s.startswith("*"):
            continue
        keep.append(line)
    return "\n".join(keep)


def main():
    fails = []
    lib = json.load(open(LIB_PATH, encoding="utf-8"))
    fade = float(lib.get("hook_fade_out_seconds", 0.0))
    trim = float(lib["trim_seconds"])
    leg_max = gate.FLICKER_LEG_MAX_S
    slam = gate.DEFAULT_MAX_DELTA

    print(f"hook_fade_out_seconds={fade:g}s   trim_seconds={trim:g}s   "
          f"判据④时间尺度 {leg_max:g}s   判据①闸刀线 {slam:g}   "
          f"判据②下限 {gate.OPENING_LUMA_FLOOR:g}")

    # ---------------------------------------------------------------- T1 长度本身
    if not fade > 0.0:
        fails.append(f"T1 lib 的 hook_fade_out_seconds={fade} —— 装置被摘掉了，"
                     f"接缝重新变成亮照片一帧砸进黑场")
    if fade < leg_max:
        fails.append(f"T1 淡出 {fade:g}s 短于判据④的时间尺度 {leg_max:g}s —— "
                     f"掉下去又回来的两条腿都够短，会被门禁读成「去了又回」"
                     f"（换气 vs 频闪只差这一个数，见 T2 的对照）")
    if fade > 1.0:
        fails.append(f"T1 淡出 {fade:g}s 超过 1.0s —— 钩子本体实测 6~8s，"
                     f"再长就是在裁本期内容而不是处理接缝")

    # ------------------------------------------------------ T2 门禁自己的 flickers()
    # 淡出必须是**单调到黑**：判决只看钩子本体（段尾淡出与片头窗一起豁免），所以真正的失效
    # 形状是「在钩子里掉下去又爬回来」。同一个深度，只由腿长决定它是换气还是频闪。
    if fade > 0.0:
        monotone = gate.flickers(seam(fade), FPS)
        fast_dip = gate.flickers(dip(HOOK_PLATEAU, 0.12), FPS)
        slow_dip = gate.flickers(dip(HOOK_PLATEAU, leg_max * 1.5), FPS)
        print(f"  T2 现长度的单调淡出：flickers() = {len(monotone)} 处"
              f"   对照①同深度但 0.12s 去+0.12s 回 = {len(fast_dip)} 处"
              f"   对照②{leg_max * 1.5:.2f}s 去+{leg_max * 1.5:.2f}s 回 = {len(slow_dip)} 处")
        if len(monotone):
            fails.append(f"T2 现长度的淡出被判据④数到 {len(monotone)} 处「去了又回」—— "
                         f"段尾不再是单调到黑（腿长：{monotone[0][3]:.2f}s/{monotone[0][4]:.2f}s）")
        # 非空转：更短的来回必须真的翻红，否则 T2 的 0 处什么都没证明。
        if not fast_dip:
            fails.append("T2 0.12s 的去回也没被判据④抓到 —— flickers() 这条断言是空的，"
                         "T2 的绿没有任何含义")
        # 反向：同深度、腿长超过判据④的时间尺度就不该算闪烁，否则 T1 那句「≥ leg_max 才安全」
        # 是在要求画面不许变暗，而不是在约束时间尺度。
        if slow_dip:
            fails.append(f"T2 {leg_max * 1.5:.2f}s 的慢去回也被抓了 —— 判据④的时间尺度失效，"
                         f"那么任何淡出都会红，T1 的长度下限就没有意义")

    # ------------------------------------------------ T3 接缝那一对待在闸刀线以下
    for plateau in (HOOK_PLATEAU, HOOK_PLATEAU_BRIGHT):
        walked = seam(fade, plateau)
        step = float(np.abs(np.diff(walked)).max())
        hard = seam(0.0, plateau)
        cut = float(np.abs(np.diff(hard)).max())
        allowance = gate.rate_allowance(4.9)          # 正文峰值 4.9 的实测档
        print(f"  T3 钩子平台 {plateau:.0f} → 淡出后接缝最大单帧 {step:.1f} 级 / "
              f"去掉淡出 {cut:.1f} 级（①线 {slam:g}、③允许 {allowance:.1f}）")
        if step > slam:
            fails.append(f"T3 {plateau:.0f} 档的淡出单帧跳 {step:.1f} 级，够上了闸刀线 "
                         f"{slam:g} —— 淡出本身成了闪烁")
        if step > allowance:
            fails.append(f"T3 {plateau:.0f} 档的淡出单帧跳 {step:.1f} 级，超过判据③允许线 "
                         f"{allowance:.1f} —— 摊得不够开")
        if not cut > slam:
            fails.append(f"T3 去掉淡出之后接缝只有 {cut:.1f} 级，够不上闸刀线 —— "
                         f"夹具没做出真实缺陷，T3 的绿是空的")

    # ----------------------------------------- T4 两条渲染路径都真的画它，且只从 lib 取
    srcs = {}
    for label, path in (("render_scene_map_template_fallback.py", PY_RENDERER),
                        ("render-episode-final.mjs", JS_RENDERER)):
        if not os.path.exists(path):
            fails.append(f"T4 渲染路径不见了：{path}")
            continue
        srcs[label] = strip_comments(open(path, encoding="utf-8").read())

    for label, src in srcs.items():
        if "hook_fade_out_seconds" not in src:
            fails.append(f"T4 {label} 的**代码**里不再读 lib 的 hook_fade_out_seconds —— "
                         f"长度会变成各写一遍的第二份真源")
        if "intro-template.json" not in src:
            fails.append(f"T4 {label} 的**代码**里不再从 intro-template.json 取开场时刻表")
        if "fade=t=out" not in src:
            fails.append(f"T4 {label} 的**代码**里不再画 fade=t=out —— 钩子段尾淡出是回滚之后"
                         f"唯一的抗闪屏装置，少画一次就是亮照片一帧砸进片头的黑场起幅")
        # 回滚撤掉的片头侧装置不许复活
        for bad_token, why in ((r"tpad\s*=\s*stop_mode\s*=\s*clone",
                                "钩子末帧原地垫帧（回滚已删）"),
                               (r"xfade\s*=\s*transition\s*=\s*fade",
                                "钩子→片头等长溶解（回滚已删）"),
                               (r"junction(?:D|Hold)\s*=",
                                "接缝溶解常量（回滚已删）")):
            if re.search(bad_token, src):
                fails.append(f"T4 {label} 的代码里又出现了 {bad_token} —— {why}；共享片头按 "
                             f"2026-10-03 原样交付，只许在钩子这一路动手")
        # 片头那一路必须仍然是整段搬用、一帧不裁、不做合成
        if not re.search(r"trim=0:(?:\{INTRO_TRIM\}|\$\{INTRO_TRIM_SECONDS\})", src):
            fails.append(f"T4 {label} 的片头输入不再是 trim=0:INTRO_TRIM —— 片头不许再被"
                         f"裁、也不许再被合成器改写")

    print()
    if fails:
        for f in fails:
            print(f"❌ {f}")
        sys.exit(1)
    print(f"✅ 钩子段尾淡出回归通过：lib 长度 {fade:g}s ≥ 判据④尺度 {leg_max:g}s、"
          f"单调淡出 flickers() 实测 0 处而同深度 0.12s 来回 {len(gate.flickers(dip(HOOK_PLATEAU, 0.12), FPS))} 处"
          f"（非空转，慢来回 {len(gate.flickers(dip(HOOK_PLATEAU, leg_max * 1.5), FPS))} 处）、"
          f"接缝单帧 {float(np.abs(np.diff(seam(fade, HOOK_PLATEAU_BRIGHT))).max()):.1f} 级"
          f"≤①线 {slam:g}（去掉淡出是 {HOOK_PLATEAU_BRIGHT:.0f} 级）、"
          f"两条渲染路径的代码都从 lib 取长度并真的画 fade=t=out，片头仍是 trim=0 整段搬用")
    sys.exit(0)


if __name__ == "__main__":
    main()
