#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""闪屏归因：把第 10 道门的红，落到「该改哪一层」上。

为什么需要它（2026-10-05《等待戈多》）：判据①~⑤ 报的是**整片**的数，而修法的真源分处三层
——共享片头模板（index.html + 那七张底图）、装配层（钩子末帧保持 + 等长溶解 + INTRO_TRIM）、
本期素材（钩子照片本身与钩子内切点）。一部红成片只写「4 项判据红」时，下一步动作只能靠猜：
2026-10-04 那天量了 119 部，每一部都要重渲、再逐帧重看一次，才知道红是旧像素还是当前代码。
这份脚本读**同一个 36x48 口径**的逐帧亮度和 MAD，把开场窗口按段落切开，每段单独和允许线比，
再直接说出这一段归谁修、重渲本期能不能消。

段落边界全部取自 scripts/lib/intro-template.json 与门禁自己的 opening_window()，这里不写秒数。

★ 2026-10-05 补充（《人间草木》跑真片时暴露的第四种归属）：钩子段的「换页 MAD 超标」不能一口
  气报成「本期素材」，因为同一句红有两种真源，而修法相反。《人间草木》成片 4.50s 单帧 MAD 57.0、
  均亮度 59.5→59.5（|Δ| 0.0）——内容整幅换掉而亮度一帧没动，且**只占一帧**，说明那两个钩子镜头
  之间根本没有溶解；而当前生成器给的就是 0.72s（≈21 帧）交叉溶解，所以这种红**重渲本期就消**。
  反过来，同样的内容差摊在 21 帧上还超允许线，就是这两张照片本身差太大，重渲一遍还是 57。
  区分它们只需要 mad_run（峰值那一拍占几个连续帧），所以钩子段的归属按它翻面，其余段不受影响。

用法：
  python3 scripts/check-flicker-attribution.py <成片>
  python3 scripts/check-flicker-attribution.py episodes/等待戈多/renders/等待戈多-final.mp4 --json
退出码：0 无红段；1 至少一段红。最后一行是结论，交付汇总读它。

★ 2026-10-05 补判据⑥（《李尔王》暴露的盲区）：第 10 道门按六条判据报红，而归因表只量 ①②③④⑤
  那五条的数，于是「❌ 漏底（⑥最深下探 5.6 级）」这类红在这里打出一句「闪屏归因：无红段」——
  一处红没有任何归属，而这条红恰恰是全库积压里的多数（2026-10-05 重量 125 部：32 部红，27 部带
  ⑥越带）。AGENTS.md 写的是「红必须先归因再动手，因为三个归属层要三种不同动作」，所以 ⑥ 必须有
  自己的那一列。它落在**黑底从两层之间漏进来**的合成形状上（f·上层+(1−f)²·下层），与照片亮度无关，
  所以它的归属天然不是「本期素材」而是**画这两层的那段代码**。
"""
import argparse
import importlib.util
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 门禁是本文件的唯一口径来源：判据、允许线、开场窗口都必须和 check-luminance-flicker.py 算出
# 同一批帧，否则「归因说这段绿、门禁说整片红」又是一次两边各写一遍的分歧。
_spec = importlib.util.spec_from_file_location(
    "flicker_gate", os.path.join(ROOT, "scripts", "check-luminance-flicker.py"))
GATE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(GATE)

LIB_PATH = os.path.join(ROOT, "scripts", "lib", "intro-template.json")

# 「单帧硬切」的判定线：峰值那一拍占 ≤3 帧（0.1s）就算没溶解。3 而不是 1，是因为编码器的
# 运动补偿会把一次真硬切的内容差抹到相邻 1~2 帧上；而当前生成器的钩子溶解是 0.72s ≈ 21 帧，
# 两边差一个数量级，这条线怎么挪都不影响判决（《人间草木》实测 mad_run=1）。
HARD_CUT_RUN = 3

# 接缝段只看「接缝那一跳」的跨度（秒）。0.30s = 9 帧：够量到 50~60 一帧砸到 0.0 的硬切，
# 又不会吃进片头自己的 0.13s 全黑起幅与约 0.5s 飞行段（回滚后那两拍是保留的设计）。
JUNCTION_SPAN = 0.20        # 接缝只看「钩子淡出结束 → 片头自己的黑起幅开始」这一小段
# 接缝那一跳（钩子末帧砸到 0.0）由 hook_fade 段判，所以接缝段不判眨眼、不判内容突变——
# 它要判的是「钩子淡出结束时是不是还停在亮处」：淡出没生效时，那一帧仍是 50~60，
# 接下来片头自己的 0.13s 全黑起幅紧跟着，一次眨眼就成型了。
JUNCTION_SPAN = 0.20

# 段内**不参与判红**的两处，各自一个真源（回滚后重新划界，2026-10-05）：
#   · hook_fade：钩子侧淡出本来就是**淡到黑**，「最暗 < 18」在这里是这台装置在正常工作，
#     不是缺陷——它治的正是接缝那一次眨眼。
#   · junction：它紧跟片头自己的 0.13s 全黑起幅（回滚后保留的原效果），所以窗口里一定
#     出现低亮度帧与一次内容突变；接缝要判的是**那一跳**（①闸刀 / ③摊不开），
#     不是「窗口里有没有黑帧」。
NO_BLINK_BANDS = ("hook_fade", "junction")

# 判据⑥「漏底」在哪几段**参与判红**：必须和门禁 judged_segments() 的判决范围一模一样
# （②~⑥ 只量「钩子段本体 [0, hook_dur−hook_fade) + 正文起幅 [intro_end, end)」，
# 钩子段尾淡出与共享片头只报告不判红——用户 2026-10-05 裁决「豁免是范围，不是把阈值放宽」）。
# 归因表比门禁多切了 hook_fade / junction 两段好把修法分开，所以这里要把 ⑥ 的红**收拢**回
# 门禁真正会判红的两段，否则会出现「归因说这段红、门禁说整片绿」——那正是本文件开头禁止的
# 两边各写一遍的分歧。
EXC_JUDGED_BANDS = ("hook", "to_body")

# 判据⑥的归属：凸组合 f·上层+(1−f)·下层 越不出两侧电平夹出的带，所以越带**只能**来自「退场层
# 被淡了第二次」或画面上还有一层在同时淡出——那是**合成形状**，不是这两张照片本身差太大，
# 也不是亮度带选错了（压暗时 depth 与带宽同比缩小，depth/带宽 是不变量）。所以这一段红不许
# 报成「本期素材 · 光重渲不消」：那会把人派去重挑照片。
#   · hook 段：钩子两个镜头之间那两层是 create-episode-preview.mjs 画的 .scene。当前生成器
#     只淡入上一层、退场层保持 opacity:1（且 .scene 有显式 z-index:1，否则不是凸组合而是一帧硬切），
#     所以**磁盘上的生成器已经不给这个形状了**——成片里量到越带，就带的是改前的像素，
#     重渲本期即消（实测：11:59 回滚之后重渲的 7 部 ⑥ 全 0 处，未重渲的 27 部全在越带）。
#   · to_body 段：片头→正文那一刀的溶解在装配层（两条渲染路径的 xfade / overlay）。
EXC_OWNER = {
    "hook": ("共享预览生成器（钩子 .scene 淡出形状：退场层被淡了第二次，黑底漏进两层之间；"
             "当前 create-episode-preview.mjs 只淡入上一层 ⇒ 成片里的越带是改前像素）", True),
    "to_body": ("装配层（片头→正文切点的溶解形状：两层同时反向淡入淡出）", True),
}


def load_lib():
    with open(LIB_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def segments(hook_dur, lib):
    """按**成片时间轴**切段，段与段首尾相接、互不重叠。

    ★ 2026-10-05 回滚后的形状：共享片头恢复成 2026-10-03 的原效果（0.13s 全黑起幅 + 约
    0.5s 飞到平台、书单整幅换页、书单→结果页砸黑），抗闪屏只剩钩子侧的
    `hook_fade_out_seconds` 淡出。所以本表的段界改读**回滚后仍然存在的键**：
      · 钩子段本体 [0, hook_dur)，段尾 hook_fade_out_seconds 秒是钩子侧淡出（下沉腿），
        仍归钩子这一路——判据②的眨眼就是它没起作用时出现在这里的。
      · 接缝 = 钩子淡出结束 → 片头自己的黑起幅走完（scroll_start）。这一段仍然是
        **装配层**：片头不再排在第一位，它前面是本期那张照片，硬切过去就是一次眨眼。
      · 片头 [hook_dur, hook_dur+trim) 整段合并成一段「共享片头（设计如此，不判红）」——
        门禁的判据范围已把它排除（intro-template.json 的 _flicker_scope_comment），
        这里保留这一段是为了让表**铺满**开场窗口，红段仍然能落回某一层，而不是凭空消失。
      · 正文起幅 [hook_dur+trim, +TAIL) 收尾。
    为什么必须互斥（2026-10-05 测试逼出来的）：段界重叠时一处红会被两段同时认领，
    归因表给出两个互相冲突的修法——人照着表会先去重渲装配层，白渲一遍。
    """
    trim = float(lib["trim_seconds"])
    fade = float(lib.get("hook_fade_out_seconds", 0.0))
    tail = GATE.OPENING_TAIL
    hook_end = max(0.0, hook_dur - fade)
    # 接缝只看**接缝本身**：片头自己的 0.13s 全黑起幅紧接着就开始了，那是回滚后要保留的原效果，
    # 把整段黑起幅圈进接缝段，一次合法交付就会被自己的片头判红。真正的接缝缺陷是**那一跳**：
    # 钩子有 hook_fade_out_seconds 下沉时，钩子末帧已经在 6 左右，接上去只差几级；下沉没了，
    # 就是 50~60 一帧砸到 0.0——判据①③抓的正是这一跳。JUNCTION_SPAN 取 0.30s（9 帧），
    # 够量到这一跳，又不吃进片头自己的飞行段。
    jspan = JUNCTION_SPAN
    # 接缝段判的是「黑起幅之后片头自己的飞行有没有把亮度又拉回来」，段**边界**仍然是
    # [hook_dur, hook_dur+jspan)（五段首尾相接、铺满窗口），只是量的时候跳过最前面
    # JUNCTION_LEAD 秒：接缝那一跳本身归 hook_fade 段判（那才是 hook_fade_out_seconds
    # 该起作用的地方）。同一处物理跳不记两个归属。
    return [
        # (键名, 显示名, 起, 止, 归属层, 重渲本期能不能消)
        ("hook", "钩子自身（照片 + 内切点）", 0.0, hook_end,
         "本期素材（钩子照片 + 钩子内切点）", False),
        ("hook_fade", "钩子段尾淡出", hook_end, hook_dur,
         "装配层（hook_fade_out_seconds：钩子侧下沉腿）", True),
        ("junction", "片头黑起幅后的爬升", hook_dur, hook_dur + jspan,
         "装配层（片头不再排第一位，接缝必须是渐变而不是硬切）", True),
        ("intro", "共享片头（设计如此，门禁不判红）",
         hook_dur + jspan, hook_dur + trim,
         "共享片头模板（回滚到 10-03 原效果，不许在这里动刀）", False),
        ("to_body", "片头→正文切点", hook_dur + trim, hook_dur + trim + tail,
         "本期正文起幅 + 装配切点", False),
    ]


def measure(luma, delta, mad, fps, lo, hi, skip=0.0, band=None):
    """段落 [lo, hi) 内的度量。**帧归属按目的帧**：第 n 帧属于它落进的那一段，
    所以 n 那一跳（mad[n−1] / delta[n−1]）也属于那一段——按段界两端各算一次的话，
    一处红会被两段同时认领，归因表给出两个互相冲突的修法（测试逼出来的，见 segments()）。

    mad_run 是「峰值那一拍占了几个连续帧」（从峰值向两边扩到 MAD < 峰值一半为止）。
    它把同一句「钩子段内容跳变超标」拆成两个不同的修法：
      · 1~3 帧 = **根本没有溶解**（硬切）。这是共享预览生成器的形状，不是照片的问题——
        2026-10-05 量《人间草木》：4.50s 单帧 MAD 57.0，而那一帧前后均亮度都是 59.5（|Δ| 0.0），
        即整幅换了画而亮度没动，且只换了一帧。当前生成器在钩子两个镜头之间给的是
        create-episode-preview.mjs 里那条 0.72s（≈21 帧）交叉溶解，所以**重渲本期就消**。
      · 摊在 ~20 帧上还超标 = 溶解本身没问题，是**这两张照片的内容差太大**（本期素材），
        重渲一遍还是 57，只能换图或改切点。
    没有这一项，归因表会把前者也报成「本期素材 · 光重渲不消」，人就跑去重挑照片。

    exc 是这一段的判据⑥「漏底」清单（深度、时间、两侧电平、越带帧数），出自门禁自己的
    `dissolve_excursions()` + 门禁自己的取证窗口曲线，见文件开头的 ★ 说明。
    """
    i = max(0, int(round(lo * fps)))
    j = min(len(mad), int(round(hi * fps)))
    if skip:
        i = max(i, min(j - 2, int(round(skip * fps))))
    if j - i < 2:
        return None
    t0 = max(0, i - 1)
    t1 = max(t0, j - 1)
    seg_mad = mad[t0:t1]
    seg_delta = delta[t0:t1]
    if len(seg_mad) == 0 or len(seg_delta) == 0:
        return None
    k = t0 + int(np.argmax(seg_mad))
    dk = t0 + int(np.argmax(seg_delta))
    peak = float(seg_mad.max())
    run = 0
    if peak > 0:
        lo_i = k
        while lo_i > 0 and mad[lo_i - 1] >= peak * 0.5:
            lo_i -= 1
        hi_i = k
        while hi_i < len(mad) - 1 and mad[hi_i + 1] >= peak * 0.5:
            hi_i += 1
        run = int(hi_i - lo_i + 1)
    exc = []
    if band is not None and len(band) > j:
        for item in GATE.dissolve_excursions(band[i:j], fps):
            exc.append(dict(item, t=round(i / fps + item["t"], 2),
                            depth=round(float(item["depth"]), 1),
                            level=round(float(item["level"]), 1),
                            floor=round(float(item["floor"]), 1),
                            band=(round(float(item["band"][0]), 1),
                                  round(float(item["band"][1]), 1)),
                            span_s=round(float(item["span_s"]), 2)))
    return dict(
        frames=int(j - i),
        mad_peak=round(peak, 1),
        mad_p99=round(float(np.percentile(seg_mad, 99)), 1),
        mad_run=run,
        delta_peak=round(float(seg_delta.max()), 1),
        luma_min=round(float(luma[i:j].min()), 1),
        luma_max=round(float(luma[i:j].max()), 1),
        mad_at=round((k + 1) / fps, 2),
        seam_step=round(float(seg_delta[0]) if len(seg_delta) else 0.0, 1),
        delta_at=round((dk + 1) / fps, 2),
        # 判据⑥「漏底」：**读门禁自己的 band 曲线**（取证窗口 = 字幕以上那块画面，
        # LUMA_BAND_ROWS）和它自己的 dissolve_excursions()，一个容差都不在这里重抄。
        # 段落就是被切开的曲线：门禁按 judged_segments() 逐段跑同样的切片，所以两边
        # 量到的是同一批帧、同一批电平。
        exc=sorted(exc, key=lambda x: -x["depth"]),
        exc_depth=round(max((e["depth"] for e in exc), default=0.0), 1),
    )


def main():
    ap = argparse.ArgumentParser(description="闪屏归因：红落在开场窗口的哪一段、归哪一层修")
    ap.add_argument("video")
    ap.add_argument("--trim-seconds", type=float, default=None,
                    help="片头裁切长度，默认读 scripts/lib/intro-template.json")
    ap.add_argument("--body-ref-seconds", type=float, default=GATE.BODY_REF_SECONDS)
    ap.add_argument("--full", action="store_true",
                    help="解整片，额外给出正文段（慢，用于全库积压表）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    lib = load_lib()
    trim = args.trim_seconds if args.trim_seconds is not None else float(lib["trim_seconds"])
    rw = GATE.probe(args.video, "r_frame_rate").split("/")
    fps = float(rw[0]) / float(rw[1])
    win = GATE.opening_window(args.video, trim)
    hook = win["hook_dur"]
    limit = None if args.full else win["end"] + args.body_ref_seconds
    # 一次解码拿三条曲线（整幅均亮度 / 相邻帧内容差 MAD / 取证窗口亮度），和门禁**同一个调用**：
    # 判据⑥必须在「字幕以上那块画面」上量（整幅口径会把字幕入场那一帧报成上冲，实测两处假阳），
    # 而这里若自己再解一遍，同一处画面就会有两份互相矛盾的证词。
    luma, mad, band = GATE.frame_curves(args.video, limit)
    if len(mad) < 8:
        print("闪屏归因：解码不足，无法归因（先查渲染是否真的出片）")
        return 1

    i_end = min(len(luma), int(round(win["end"] * fps)) + 1)
    delta = np.abs(np.diff(luma))
    body_mad = mad[i_end:]
    body_delta = delta[i_end:]
    allow_mad = GATE.content_allowance(float(body_mad.max()) if len(body_mad) else 0.0)
    allow_rate = GATE.rate_allowance(float(body_delta.max()) if len(body_delta) else 0.0)

    rows = []
    for key, name, lo, hi, owner, rerender_clears in segments(hook, lib):
        m = measure(luma, delta, mad, fps, lo, hi, band=band)
        if m is None:
            continue
        m.update(key=key, name=name, owner=owner, rerender_clears=rerender_clears,
                 span=[round(lo, 2), round(hi, 2)])
        m["red_mad"] = m["mad_peak"] > allow_mad
        m["red_rate"] = m["delta_peak"] > allow_rate
        if key == "junction":
            # 接缝只判「淡出结束后那**一帧**是否还亮」：片头黑起幅本身的内容突变与飞行段
            # 都属豁免范围。seam_step = 钩子淡出结束那一帧相对它前面的跳变。
            m["red_mad"] = False   # 接缝不判内容突变（片头黑起幅是设计）
            m["red_rate"] = m["seam_step"] > allow_rate
        # 接缝段不判眨眼：它后面紧跟片头自己的全黑起幅，最暗必然很低，而那不是接缝的缺陷。
        # 接缝的判据是那一跳（①闸刀 / ③摊不开），不是窗口里有没有黑帧。
        m["red_blink"] = (m["luma_min"] < GATE.OPENING_LUMA_FLOOR
                          and key not in NO_BLINK_BANDS)
        # 共享片头窗（片头→片头+trim）在 2026-10-05 回滚后**不判红**：固定开场白按原样
        # 交付，它内部的黑场起幅与书单→结果页砸黑都是设计（intro-template.json 的
        # _flicker_scope_comment）。这一段仍然量、仍然显示，是为了让表铺满开场窗口、
        # 让红段能落回某一层；只是它不参与结论，也不进「红段」列表。
        m["exempt"] = key == "intro"
        # 判据⑥只在门禁真正判它的那两段（EXC_JUDGED_BANDS = judged_segments 的两段）参与判决；
        # 钩子段尾淡出与共享片头里的越带照量照印，但和门禁一样不翻红（豁免的是范围）。
        m["red_exc"] = bool(m["exc"]) and key in EXC_JUDGED_BANDS and not m["exempt"]
        m["red"] = ((not m["exempt"]) and (m["red_mad"] or m["red_rate"] or m["red_blink"]
                                           or m["red_exc"]))
        # 钩子段的内容红要再分一次：单帧硬切是**共享预览生成器**的形状（当前生成器给 0.72s
        # 溶解，重渲本期即消），摊在溶解里还超标才是**这两张照片差太大**（重渲不消）。
        # 见 measure() 的 mad_run 注释与 2026-10-05《人间草木》实测。
        m["hard_cut"] = (key == "hook" and m["red_mad"] and not m["red_rate"]
                         and not m["red_blink"] and 0 < m["mad_run"] <= HARD_CUT_RUN)
        if m["hard_cut"]:
            m["owner"] = ("共享预览生成器（钩子内切点形状："
                          "create-episode-preview.mjs 的 0.72s 交叉溶解）")
            m["rerender_clears"] = True
        # 判据⑥红：把「越带」这一条单独挂一个归属，因为它和 MAD/亮度跳的真源不是同一处代码，
        # 一句话也不该共用一行——同一段里「换页 MAD 摊在溶解里（本期照片）」＋「漏底（淡出形状）」
        # 是两种修法，只报一个就会修错地方。
        if m["red_exc"] and key in EXC_OWNER:
            m["owner_exc"], m["exc_clears"] = EXC_OWNER[key]
        elif m["red_exc"]:
            m["owner_exc"] = f"{owner}（溶解越带：黑底从两层之间漏进来）"
            m["exc_clears"] = rerender_clears
        else:
            m["owner_exc"] = None
            m["exc_clears"] = None
        rows.append(m)

    if args.full:
        m = measure(luma, delta, mad, fps, win["end"], len(mad) / fps, band=band)
        if m:
            m.update(key="body", name="正文（窗口之外）", owner="本期正文切点 / 场景图亮度差",
                     rerender_clears=False, span=[round(win["end"], 2), round(mad.shape[0] / fps, 2)])
            m["red_mad"] = m["mad_peak"] > allow_mad
            m["red_rate"] = m["delta_peak"] > GATE.DEFAULT_MAX_DELTA
            m["red_blink"] = False
            m["hard_cut"] = False
            m["owner_exc"] = None
            m["exc_clears"] = None
            # 门禁的 ②~⑥ 只判开场窗口（正文是参照物）；这里把 ⑥ 的数**照量照印**，
            # 但不判红——否则归因表会凭空多出一条门禁没有的判决。
            m["red_exc"] = False
            m["red"] = m["red_mad"] or m["red_rate"]
            rows.append(m)

    reds = [r for r in rows if r["red"]]
    if args.json:
        print(json.dumps(dict(video=os.path.basename(args.video), hook_dur=hook,
                              window_end=win["end"], allow_mad=round(allow_mad, 1),
                              allow_rate=round(allow_rate, 1), segments=rows),
                         ensure_ascii=False, indent=1))
        return 1 if reds else 0

    print(f"闪屏归因  {os.path.basename(args.video)}  钩子 {hook:.2f}s + 片头 {trim:g}s"
          f"  窗口 {win['end']:.2f}s")
    print(f"  允许线（门禁同一口径）：内容 MAD ≤ {allow_mad:.1f}   单帧亮度 |Δ| ≤ {allow_rate:.1f}"
          f"   最暗 ≥ {GATE.OPENING_LUMA_FLOOR:g}   越带 ≤ {GATE.EXCURSION_MAX}"
          f"（⑥取证窗口＝字幕以上行 {GATE.LUMA_BAND_ROWS[0]}~{GATE.LUMA_BAND_ROWS[1] - 1}，"
          f"地板 max({GATE.UNDERSHOOT_TOL:g}, {GATE.UNDERSHOOT_REL:g}×带宽)，"
          f"判决范围＝门禁 judged_segments 的两段）")
    for r in rows:
        flag = "⚪" if r.get("exempt") else ("🔴" if r["red"] else "🟢")
        why = []
        if r["red_mad"]:
            # 「硬切 / 摊在溶解里」这套解读只对钩子段成立（那两个归属层是生成器 vs 本期素材）；
            # 片头段的换页形状本来就归共享模板，套这句话会给出一个不属于它的修法。
            shape = (f"，{'硬切：没有溶解' if r['hard_cut'] else '摊在溶解里'}"
                     if r["key"] == "hook" else "")
            why.append(f"换页 MAD {r['mad_peak']} 占 {r['mad_run']} 帧"
                       f"（@{r['mad_at']:.2f}s{shape}）")
        if r["red_rate"]:
            why.append(f"亮度跳 {r['delta_peak']}（@{r['delta_at']:.2f}s）")
        if r["red_blink"]:
            why.append(f"眨眼 最暗 {r['luma_min']}")
        if r["red_exc"]:
            d = r["exc"][0]
            why.append(f"漏底 越带 {len(r['exc'])} 处，最深 {d['kind']} {d['depth']} 级"
                       f"@t={d['t']:.2f}s（两侧电平 {d['band'][0]}/{d['band'][1]} 却读到 "
                       f"{d['level']}，连续 {d['frames']} 帧，地板 {d['floor']}）")
        elif r["exc"]:
            # 豁免段/正文段里的越带**照印**：不印就是暗账，红了多少没人知道（同门禁那句
            # 「按同一套判据本会翻 N 项红」）。
            d = r["exc"][0]
            why.append(f"漏底 {len(r['exc'])} 处最深 {d['depth']} 级@t={d['t']:.2f}s"
                       f"（不在门禁判决范围，只报告）")
        print(f"  {flag} {r['name']:<26} {r['span'][0]:5.2f}~{r['span'][1]:5.2f}s  "
              f"{r['frames']:3d}帧  MAD {r['mad_peak']:5.1f}/p99 {r['mad_p99']:5.1f}  "
              f"|Δ| {r['delta_peak']:5.1f}  亮度 {r['luma_min']:5.1f}~{r['luma_max']:5.1f}"
              f"  越带 {len(r['exc']):d} 处"
              + ("   " + "、".join(why) if why else ""))
        if r["red"]:
            # 一条红只由「漏底」引起时**只**报漏底的归属：这一行的作用是告诉人下一步改哪儿，
            # 把「本期素材（钩子照片）」也印出来就会有人去重挑照片，而越带和照片亮度无关。
            only_exc = (r["red_exc"] and not r["red_mad"] and not r["red_rate"]
                        and not r["red_blink"])
            if not only_exc:
                print(f"       归属：{r['owner']}"
                      + ("（重渲本期即可消）" if r["rerender_clears"]
                         else "（改共享层/本期素材之后才谈得上重渲，光重渲不消）"))
        if r.get("owner_exc"):
            print(f"       漏底归属：{r['owner_exc']}"
                  + ("（重渲本期即可消）" if r["exc_clears"]
                     else "（改共享层之后才谈得上重渲，光重渲不消）"))

    if not reds:
        print("闪屏归因：无红段")
        return 0
    named = []
    for r in reds:
        # 一条红只由「漏底」引起时，结论行必须报出漏底的归属层，而不是那段本来的归属层——
        # 「本期素材」会把人派去重挑照片，而这里的修法是重渲（生成器早已不给这个形状）。
        only_exc = (r["red_exc"] and not r["red_mad"] and not r["red_rate"]
                    and not r["red_blink"])
        named.append(f"{r['name']}→{r['owner_exc'] if only_exc else r['owner']}")
    print("闪屏归因：红落在 " + " ｜ ".join(named))
    return 1


if __name__ == "__main__":
    sys.exit(main())
