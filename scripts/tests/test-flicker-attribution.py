#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""闪屏归因的反空转测试：喂合成帧序列，要求「红落在哪一段、归哪一层」跟着形状翻面。

为什么必须有它（2026-10-05）：check-flicker-attribution.py 把开场窗口切成五段，每段单独和
门禁的允许线比，再报这一段的修法归属。归因表一旦算错段落边界或读错允许线，**它依然会输出
一张看起来很专业的表**，只是把「改共享模板」的红报成「重渲本期就行」——那比没有归因更糟，
因为人会照着它去重渲一遍没用的。所以每一类形状都得有正反对照：健康必须全绿，而每种缺陷必须
只把它那一段的判决翻红、并把归属报对。判据⑥「漏底」另有一对**跨段**对照（同一形状摆在钩子段
红、摆在共享片头段只报告），因为它的判决范围是这次回滚里被用户裁决划走的那一段。

口径不重写：合成的是 frame_curves 的返回（逐帧均亮度 + 相邻帧 MAD + ⑥ 的取证窗口曲线），
跑的是脚本自己的 main()。
用法：python3 scripts/tests/test-flicker-attribution.py
"""
import importlib.util
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


def load(mod_name, rel):
    spec = importlib.util.spec_from_file_location(mod_name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ATTR = load("flicker_attribution", "scripts/check-flicker-attribution.py")
LIB = ATTR.load_lib()

FPS = 30.0
HOOK = 6.17                      # 新配音实测 6.17s（旧的 6.30 是上一版）
FADE = float(LIB["hook_fade_out_seconds"])
TRIM = float(LIB["trim_seconds"])
SCROLL_A = float(LIB["scroll_start"])
RESULT_AT = float(LIB["result_page_at"])
TAIL = ATTR.GATE.OPENING_TAIL
BODY_REF = ATTR.GATE.BODY_REF_SECONDS
PLATEAU = 171.0        # 片头亮度平台（未归一的原效果，量级照旧）
TAIL_LUMA = 55.0       # 照片量级
RESULT_LUMA = 45.0     # 结果页 = 本期 result-bridge 副本
HOOK_LUMA = 60.0
# 段界（与 check-flicker-attribution.segments() 同一套算法，不在这里另抄一遍秒数）
HOOK_END = HOOK - FADE                 # 钩子本体
JUNCTION_END = max(HOOK_END + 0.05, HOOK + min(SCROLL_A, 1.0))


def synth(page_mad=3.3, seam_black=False, hook_step=0.0, hook_swap=None,
          hook_dip=False, intro_dip=False):
    """造一条成片：luma 逐帧均亮度，mad 相邻帧内容差（两者独立给，缺陷各自只动一个）。

    hook_swap 只动**内容**、不动亮度（均亮度全程 59.5 附近），用来把「钩子段换页 MAD 超标」
    拆成两种修法：'hard' = 一帧之内整幅换画（生成器没给溶解），'soft' = 同样的内容差摊在
    0.72s ≈ 21 帧的溶解里（溶解在，是这两张照片本身差太大）。
    hook_dip / intro_dip 造的是判据⑥「漏底」的**同一个形状**，只是摆在不同的段里：
    两个电平之间那 20 帧越出两侧电平夹出的带（黑底从两层之间漏进来）。摆进钩子段必须红，
    摆进共享片头段必须**只报告不判红**——这一对正反对照就是「豁免的是范围，不是阈值」那句
    裁决在 ⑥ 上的可量形式。
    """
    total = int(round((HOOK + TRIM + TAIL + BODY_REF) * FPS))
    luma = np.full(total, HOOK_LUMA, dtype=float)
    mad = np.full(total - 1, 2.0, dtype=float)

    def seg(a, b):
        return int(round(a * FPS)), min(total, int(round(b * FPS)))

    # 钩子本体：慢推到接近照片亮度；段尾 FADE 秒是钩子侧淡出（下沉到黑）
    i0, i1 = seg(0, HOOK_END - 0.1)
    luma[i0:i1] = HOOK_LUMA + (TAIL_LUMA - HOOK_LUMA) * (np.arange(i1 - i0) / max(1, i1 - i0))
    if hook_dip:
        # 判据⑥「漏底」摆进**钩子段**：电平 A=60（前 60 帧）→ 20 帧里掉到 53 再回到
        # 电平 B=58（剩下的平台）。深度 58−53=5.0 > 地板 max(3, 0.08×带宽 2)=3.0 ⇒ ⑥ 红；
        # 而单腿幅度 7 < 判据④的腿下限 8、每帧 |Δ|≈0.9 < 判据③的下限 16、最暗 53 ≥ 18
        # ⇒ ①②③④⑤ 一条都不许跟着红。这条用例的全部意义就是「⑥ 能单独翻面」。
        a = i0 + 60
        luma[i0:a] = 60.0
        luma[a:a + 8] = np.linspace(60.0, 53.0, 8)
        luma[a + 8:a + 12] = 53.0
        luma[a + 12:a + 20] = np.linspace(53.0, 58.0, 8)
        luma[a + 20:i1] = 58.0
    if hook_swap:
        k = int(round(3.0 * FPS))
        n = 1 if hook_swap == "hard" else int(round(0.72 * FPS))
        mad[k - 1:k - 1 + n] = 57.0                # 内容整幅换，亮度一帧都没动
    if hook_step:
        k = int(round(3.0 * FPS))
        n = int(round(FPS))
        luma[k:k + n] -= hook_step                 # 一帧之内的硬跳（本期素材缺陷）
        mad[k - 1:k] = 55.0                        # 换到那张暗照片的那一帧，内容也整幅换
        mad[k + n - 1:k + n] = 55.0                # 切回来，同一缺陷的第二拍
    # 钩子侧淡出是**淡到黑**，让片头自己的全黑起幅接在一个已经在变暗的画面上（hook_fade_out_seconds）
    i0, i1 = seg(HOOK_END, HOOK)
    luma[i0:i1] = np.linspace(TAIL_LUMA, 0.0, i1 - i0)
    mad[i0:i1 - 1] = 3.0
    # 接缝：钩子淡出之后硬切到片头自己的 0.13s 全黑 + 约 0.5s 飞向平台（单调上升）
    i0, i1 = seg(HOOK, HOOK + 0.13)
    luma[i0:i1] = 0.0
    mad[i0:i1 - 1] = 45.0
    i0, i1 = seg(HOOK + 0.13, JUNCTION_END)
    n = max(1, i1 - i0)
    luma[i0:i1] = np.linspace(0.0, PLATEAU, n)
    mad[i0:i1 - 1] = 25.0
    if seam_black:
        # hook_fade_out_seconds 没起作用：钩子那张照片（50~60）原地保持到接缝才砸到 0.0，
        # 一次眨眼。缺陷落在 hook_fade 段——那一段就是为它准备的。
        i0, i1 = seg(HOOK_END, HOOK + 0.13)
        luma[i0:i1] = TAIL_LUMA
        mad[i0:i1 - 1] = 20.0
    # 共享片头（不判红的一整段）：滚动书单整幅换页 + 书单→结果页砸黑
    i0, i1 = seg(JUNCTION_END, HOOK + RESULT_AT)
    n = max(1, i1 - i0)
    luma[i0:i1] = PLATEAU
    step = (RESULT_AT - SCROLL_A) / 6
    for p in range(6):
        at = HOOK + SCROLL_A + p * step
        i0, i1 = seg(at, at + 0.13)
        mad[i0:i1] = page_mad
        luma[i0:i1] = PLATEAU
    i0, i1 = seg(HOOK + RESULT_AT - 0.26, HOOK + RESULT_AT)
    luma[i0:i1] = np.linspace(PLATEAU, RESULT_LUMA, i1 - i0)
    if intro_dip:
        # **同一个**越带形状摆进共享片头：那一拍「书单→结果页」砸黑，两侧电平 171 / 45，
        # 中间掉到 20 ⇒ 深度 25 > 地板 max(3, 0.08×带宽 126)=10.1，越带成立。
        # 而这一段整段豁免（用户 2026-10-05 裁决固定开场白按原样交付），所以归因表必须
        # 把越带**印出来**却不许判红——这就是「豁免的是范围，不是阈值」在 ⑥ 上的正反对照。
        i0, i1 = seg(HOOK + RESULT_AT - 0.10, HOOK + RESULT_AT)
        luma[i0:i1] = 20.0
    # 结果页定格 → 正文起幅
    i0, i1 = seg(HOOK + RESULT_AT, HOOK + TRIM)
    luma[i0:i1] = RESULT_LUMA
    mad[i0:i1 - 1] = 1.5
    i0, i1 = seg(HOOK + TRIM, HOOK + TRIM + 0.64)
    luma[i0:i1] = np.linspace(RESULT_LUMA, TAIL_LUMA, i1 - i0)
    mad[i0:i1 - 1] = 6.0
    # 正文：0.64s 交叉溶解，量级 p99 2.6 / max 4.4
    i0 = min(total, int(round((HOOK + TRIM + 0.64) * FPS)))
    body_n = total - i0
    luma[i0:] = TAIL_LUMA + 4.0 * np.sin(np.arange(body_n) / 40.0)
    mad[i0:] = 2.4
    mad[total - 8:] = 4.4
    return luma, mad


def run(**flags):
    luma, mad = synth(**flags)
    win_end = HOOK + TRIM + TAIL
    # 判据⑥读的是门禁 frame_curves() 的**第三条**曲线（取证窗口=字幕以上）。合成片里没有字幕，
    # 所以 band 就等于 luma：形状一致，而归因脚本必须走 frame_curves 这个调用本身——它一旦退回
    # frame_probe（只有两条曲线），⑥ 那一列会静默消失，这条测试当场红。
    ATTR.GATE.frame_curves = lambda path, limit=None, band_rows=None: (
        luma, mad, luma.copy())
    ATTR.GATE.opening_window = lambda path, trim: dict(
        end=round(win_end, 2), floor_start=0.0, hook_dur=HOOK, resolved=True, episode="测试期")
    ATTR.GATE.probe = lambda path, key: "30/1" if key == "r_frame_rate" else "300.0"

    def once(*extra):
        import io
        import contextlib
        import json as _json

        buf = io.StringIO()
        sys.argv = ["check-flicker-attribution.py", "测试期-final.mp4", *extra]
        with contextlib.redirect_stdout(buf):
            rc = ATTR.main()
        return rc, buf.getvalue()

    # 同一份合成片跑两遍：--json 拿结构化判决，另一遍拿**给人看的那张表**。判决行有几行、归属
    # 印的是哪一层，只有表里才算数（交付汇总和人都读表），而红/绿必须和 JSON 完全一致。
    import json as _json
    rc, js = once("--json")
    rc_t, table = once()
    data = _json.loads(js)
    assert rc == rc_t, f"同一份像素两种输出退出码不同：{rc} vs {rc_t}"
    return rc, {r["key"]: r for r in data["segments"]}, table


def check(label, cond, detail=""):
    if not cond:
        print(f"❌ {label} {detail}")
        sys.exit(1)
    print(f"✅ {label}")


def main():
    src = open(os.path.join(ROOT, "scripts", "check-flicker-attribution.py"),
               encoding="utf-8").read()
    check("段落边界读的是唯一真源 scripts/lib/intro-template.json",
          os.path.basename(ATTR.LIB_PATH) == "intro-template.json"
          and os.path.isfile(ATTR.LIB_PATH))
    check("归因读门禁自己的口径（不另写一份判据）",
          "check-luminance-flicker.py" in src and "GATE.content_allowance" in src
          and "GATE.rate_allowance" in src and "GATE.OPENING_LUMA_FLOOR" in src)
    check("段落边界全部取自 intro-template.json（回滚后仍在的键）",
          'lib["trim_seconds"]' in src
          and "hook_fade_out_seconds" in src and "JUNCTION_SPAN" in src,
          "段界必须继续读 lib，hook_fade_out_seconds 回滚后仍然定义")
    dl = open(os.path.join(ROOT, "scripts", "check-episode-delivery.py"),
              encoding="utf-8").read()
    check("交付汇总是真的接了归因（闪屏红就跑）",
          "check-flicker-attribution.py" in dl and "if not flicker[0]" in dl)

    rc, segs, _ = run()
    check("健康成片：全部段落绿、退出 0",
          rc == 0 and not any(s["red"] for s in segs.values()),
          f"rc={rc} red={[k for k, s in segs.items() if s['red']]}")
    bands = [(s["span"][0], s["span"][1], s["key"]) for s in segs.values()]
    check("段落互斥且铺满开场窗口（一处红只能有一个归属）",
          bands[0][0] == 0.0 and abs(bands[-1][1] - (HOOK + TRIM + TAIL)) < 0.05
          and all(abs(bands[i][1] - bands[i + 1][0]) < 0.05 for i in range(len(bands) - 1)),
          str(bands))
    check("五段各有归属层，且装配层两段都标「重渲即可消」",
          len({s["owner"] for s in segs.values()}) >= 4
          and [k for k, s in segs.items() if s["rerender_clears"]] == ["hook_fade", "junction"])
    # 回滚后片头重新自带 0.13s 全黑起幅 + 约 0.5s 飞行（要保留的原效果）。它落在
    # hook_fade / junction 两段里：最暗必然是 0，且有一次内容突变。这两处**不许**判红——
    # 否则一次按原样交付的合法成片会被自己的共享片头判红（实测 2026-10-05 就是如此）。
    check("钩子淡出淡到黑是最暗 0.0，但不判红（这台装置在正常工作）",
          segs["hook_fade"]["luma_min"] < ATTR.GATE.OPENING_LUMA_FLOOR
          and not segs["hook_fade"]["red"] and not segs["hook_fade"]["red_blink"])
    check("接缝紧跟片头黑起幅（内容突变 + 一次亮度跳）也不判红",
          segs["junction"]["mad_peak"] > ATTR.GATE.content_allowance(0.0)
          and segs["junction"]["luma_min"] < ATTR.GATE.OPENING_LUMA_FLOOR
          and not segs["junction"]["red"])
    check("接缝段只看淡出结束那一跳（seam_step），不看窗口里的飞行段",
          # 健康形状：钩子已淡到 0.0，所以接缝那一跳本来就该接近 0；
          # 窗口里最大跳变（黑起幅起步那几帧）反而比 seam_step 大，且**不判红**。
          segs["junction"]["seam_step"] < 5.0
          and segs["junction"]["delta_peak"] >= segs["junction"]["seam_step"]
          and not segs["junction"]["red"],
          f"delta_peak={segs['junction']['delta_peak']} seam_step={segs['junction']['seam_step']}")

    # ★ 2026-10-05 回滚后共享片头整段**豁免**：整幅换页 / 砸黑 / 全黑起幅都发生在它里面，
    # 而固定开场白按原样交付，所以它量出来但**不判红**、不进结论（intro-template.json 的
    # _flicker_scope_comment）。没有这条豁免，一次合法的交付会被自己的共享片头判红。
    rc, segs, _ = run(page_mad=57.0)
    red = [k for k, s in segs.items() if s["red"]]
    check("共享片头整幅换页（57 MAD + 砸黑到 0）全部豁免，无红段",
          red == [] and segs["intro"]["exempt"] and segs["intro"]["red_mad"], f"red={red}")

    rc, segs, _ = run(seam_black=True)
    red = sorted(k for k, s in segs.items() if s["red"])
    # 下沉腿没生效只红 hook_fade 一段——那一段就是为它准备的（淡出区间 [hook_dur−fade,
    # hook_dur]），归属装配层、重渲即消。接缝段不跟着红，因为接缝只看「淡出结束那一跳」，
    # 而这里那一跳正是它该判的东西，只是被 hook_fade 段先认领了。
    check("下沉腿没生效：只红 hook_fade 一段（那一段就是为它准备的）",
          red == ["hook_fade"], f"red={red}")
    check("hook_fade 红归属装配层且重渲本期即可消",
          "装配层" in segs["hook_fade"]["owner"]
          and segs["hook_fade"]["rerender_clears"] is True
          and segs["hook_fade"]["red_mad"], f"{red}")

    rc, segs, _ = run(hook_step=55.0)
    red = [k for k, s in segs.items() if s["red"]]
    check("本期钩子硬切：只有钩子自身段红", red == ["hook"], f"red={red}")
    check("钩子红归属本期素材，重渲不回共享层",
          "本期素材" in segs["hook"]["owner"] and segs["hook"]["rerender_clears"] is False)

    # 同一句「钩子段换页 MAD 超标」的两种真源（2026-10-05《人间草木》实测逼出来的）：
    # 单帧硬切 = 生成器没给溶解，重渲本期即消；摊在 0.72s 溶解里还超标 = 两张照片差太大。
    rc, segs, _ = run(hook_swap="hard")
    h = segs["hook"]
    red = [k for k, s in segs.items() if s["red"]]
    check("钩子单帧硬切：只有钩子自身段红，且亮度/眨眼两条不背锅",
          red == ["hook"] and h["red_mad"] and not h["red_rate"] and not h["red_blink"],
          f"red={red} {[(k, h[k]) for k in ('red_mad', 'red_rate', 'red_blink')]}")
    check("硬切被认成硬切（mad_run ≤ 3 帧）", h["mad_run"] <= 3, f"mad_run={h['mad_run']}")
    check("钩子硬切归属共享预览生成器，重渲本期即可消",
          "共享预览生成器" in h["owner"] and h["rerender_clears"] is True, h["owner"])

    rc, segs, _ = run(hook_swap="soft")
    h = segs["hook"]
    red = [k for k, s in segs.items() if s["red"]]
    check("同样的内容差摊在 0.72s 溶解里：mad_run 一眼变长",
          red == ["hook"] and h["mad_run"] > 10, f"mad_run={h['mad_run']}")
    check("溶解在、差太大 → 归属仍是本期素材，重渲不消",
          "本期素材" in h["owner"] and h["rerender_clears"] is False, h["owner"])

    # ───────────────── 判据⑥「漏底」：同一形状摆在两段里，判决跟着**范围**翻面 ─────────
    # 2026-10-05 全库重量 125 部：32 部红，其中 27 部带 ⑥ 越带，而这份归因表当时只量 ①~⑤，
    # 于是《李尔王》门禁报「❌ 漏底（⑥最深下探 5.6 级）」、归因表回一句「闪屏归因：无红段」。
    # 多数红没有归属 = 这张表对积压里的绝大多数什么都不说。⑥ 必须有自己的一列。
    rc, segs, _ = run()
    check("健康成片：门禁判决的那两段 0 处越带",
          all(len(segs[k]["exc"]) == 0 for k in ATTR.EXC_JUDGED_BANDS),
          {k: segs[k]["exc"] for k in ATTR.EXC_JUDGED_BANDS})
    check("⑥不另写一份口径：一次解码 frame_curves、判决用门禁自己的 dissolve_excursions",
          "GATE.frame_curves" in src and "GATE.dissolve_excursions" in src
          and "EXC_JUDGED_BANDS" in src)
    # 判决范围必须和门禁 judged_segments() 一模一样（差一帧是边界帧归属，容忍）：
    # 两处各划一次豁免窗，就是「归因说这段红、门禁说整片绿」的那个分歧。
    n_frames = int(round((HOOK + TRIM + TAIL + BODY_REF) * FPS))
    jsegs, jexempt = ATTR.GATE.judged_segments(
        dict(hook_dur=HOOK, floor_start=0.0, hook_sink=round(HOOK - FADE, 2),
             intro=(HOOK, round(HOOK + TRIM, 2)), end=round(HOOK + TRIM + TAIL, 2),
             resolved=True), FPS, n_frames)
    spans = {s["key"]: s["span"] for s in segs.values()}
    check("⑥的判决范围＝门禁 judged_segments 的两段（豁免窗只划一次）",
          len(jsegs) == 2 and abs(jsegs[0][0] / FPS - spans["hook"][0]) < 0.05
          and abs(jsegs[0][1] / FPS - spans["hook"][1]) < 0.05
          and abs(jsegs[1][0] / FPS - spans["to_body"][0]) < 0.06
          and abs(jsegs[1][1] / FPS - spans["to_body"][1]) < 0.06,
          f"gate={[(round(a / FPS, 2), round(b / FPS, 2)) for a, b in jsegs]} {spans}")

    rc, segs, out = run(hook_dip=True)
    h = segs["hook"]
    red = [k for k, s in segs.items() if s["red"]]
    check("钩子里的漏底：判据⑥单独翻面（①②③④⑤ 一条都不跟着红）",
          rc == 1 and red == ["hook"] and h["red_exc"] and not h["red_mad"]
          and not h["red_rate"] and not h["red_blink"],
          f"red={red} " + str({k: h[k] for k in ("red_exc", "red_mad", "red_rate", "red_blink")}))
    check("越带的深度和帧数量出来就是那 5 级 / ≥2 帧（同门禁一份算法，不是抄的数）",
          len(h["exc"]) == 1 and abs(h["exc"][0]["depth"] - 5.0) < 0.8
          and h["exc"][0]["frames"] >= ATTR.GATE.EXCURSION_MIN_FRAMES
          and h["exc"][0]["kind"] == "下探", str(h["exc"]))
    check("钩子漏底归属**共享生成器的淡出形状**、重渲本期即消（不是「去重挑照片」）",
          "生成器" in h["owner_exc"] and h["exc_clears"] is True, str(h["owner_exc"]))
    check("只有 ⑥ 一条红时不再印「归属：本期素材」那行",
          "漏底归属：" in out and "       归属：" not in out,
          " | ".join(l for l in out.splitlines() if "归属" in l))

    rc, segs, out = run(intro_dip=True)
    i6 = segs["intro"]
    red = [k for k, s in segs.items() if s["red"]]
    check("同一个越带形状摆进共享片头：印出越带、**不判红**（豁免的是范围不是阈值）",
          rc == 0 and red == [] and len(i6["exc"]) >= 1 and i6["exempt"]
          and not i6["red_exc"]
          and "不在门禁判决范围，只报告" in out,
          f"red={red} 越带={len(i6['exc'])} 最深={i6['exc_depth']}")

    rc, segs, out = run(hook_dip=True, hook_swap="soft")
    h = segs["hook"]
    red = sorted(k for k, s in segs.items() if s["red"])
    check("同一段里两种缺陷（照片差太大 + 淡出漏底）→ 两行归属各说各的，不许合成一行",
          red == ["hook"] and h["red_exc"] and h["red_mad"]
          and "本期素材" in h["owner"] and "生成器" in h["owner_exc"]
          and out.count("归属：") == 2, f"red={red} {[(k, h[k]) for k in ('red_exc', 'red_mad')]}")

    rc, segs, _ = run(page_mad=57.0, seam_black=True, hook_step=55.0)
    red = sorted(k for k, s in segs.items() if s["red"])
    check("三种缺陷同时在场：钩子段 + 装配层各红一段，共享片头豁免不误伤",
          red == ["hook", "hook_fade"], f"red={red}")
    print("\n✅ 闪屏归因测试全部通过")


if __name__ == "__main__":
    main()
