#!/usr/bin/env python3
"""交付门禁第 10 道（亮度闪屏）的**判据形状 + 判决范围**回归。

为什么必须用合成序列：真片只有一份产物，它绿了可能只是判据没醒。2026-10-04 退役的旧判据⑤
（开场里 |Δ|>18 的**帧数** >3）就是反面教材——它对两处真实缺陷全盲（图层连乘凹陷、翻页来回，
单帧都只有 13~15 级，够不上 18），而它报红的理由又是「跨过了 18 这个手写数」，跟「跨度该摊在
时间上」毫无关系。最省事修法永远是压暗画面，而亮度本身是需求。所以这个文件只改判据的形状，
不改产物，并且每条断言都跑在**门禁本体的 main()** 上。

★ 2026-10-05 用户裁决「恢复原来的固定开场白效果，只改动钩子视频的闪屏问题」之后，
  这个文件多了一条**必须**证明的东西：判决范围与判据形状是两件事。
    · 判据的形状一个都没动：①闸刀、②眨眼、③摊不开、④去了又回、⑤换页、⑥漏底，
      阈值、允许线、参照物（本片自己的正文）全部照旧。
    · 被**判**的范围缩小到钩子段本体 + 正文起幅；共享片头窗只报告不判红。
  于是这个文件的核心构造是**同一个形状摆在两个位置**：摆在钩子段里必须红（R1/R3/R5），
  原样搬进片头窗必须只报告（R7）。少了这一对，「豁免」就退化成「这道门不判开场」，
  而那种门比没有门更危险——它会连钩子段的新闪烁一起放过去。
  （判据⑥的形状不在这里钉：它要用**真实像素**才立得住——两帧之间不跳、却慢慢漏进黑底，
   ①~⑤ 全盲。那一组由 scripts/tests/test-dissolve-undershoot.py 用 ffmpeg 真的渲两种溶解
   来跑，包括「同比压暗之后照样红」和「取证窗口含进字幕行才红」。）

亮度与内容两条序列由**同一张事件表**铺出来（Seq.trans 一次同时给 luma 和 MAD），不许各写一遍：
分开写就等于让夹具自己决定「这两页长得像不像」，那正是判据①~④看不见整幅换页的盲区在测试里的
复现。像素差取门禁自己用的 36x48 灰度口径上的量级，正文那档（DIFF_BODY_STILL）反过来标定：
让合成正文的 MAD 峰值落在《李尔王》成片实测的 5.9 上，否则每一条 ⑤ 的允许线都是浮空的。

用例：
  R0 健康钩子段（两张亮度相近的照片 + 一次凸组合溶解）…………… 全绿
       ★ 它的开场里含有一次 71 级、时长 0.84s 的合法大跨度，仍然必须绿：「够短才是闪」这条
         时间尺度唯一的机器可读形式就是这个绿。
  R1 钩子段里一记砸黑（26→0→26，摊在 3+2+3 帧上）……… ②眨眼 + ④来回 + ⑥漏底 红，①③⑤ 绿
  R2 与 R1 同一形状搬进**共享片头窗**………………………… 全绿（只报告，本会翻 ≥3 项红）
  R3 钩子段里一次整幅换画而均值不动（两帧均值都是 59.0）……… 只有判据⑤红
       ★ 旧判据⑤（--legacy-strobe）在这一版实测 0 帧 —— ①~④ 对整幅换页天生全盲。
  R4 钩子段里一次 120 级跨度只摊在 4 帧（0.133s）上……… 只有判据③红（④看不见单调大跨度）
  R5 与 R3/R4 同样的开场，配**正文更跳**的参照物……………… 全绿
       ★ 判据③⑤ 的允许线跟着本片正文自己抬高，所以它们不是绝对天花板；写死绝对数会在换一条
         亮度带时把全库判红。
  R6 正文段里一次单帧硬切（50→110 砸在一帧上）……………… 只有判据①红——闪屏不只发生在开场
  R7 回滚后的**共享片头真实形状**（0.13s 全黑起幅 + 六张整幅书封 + 那一拍砸黑）… 全绿，
       且报告值必须很大（峰值 ~130、最暗 0.0、MAD ~140、来回多处）——数字小才说明有人在
       片头上又动了一次刀。
  R8 同一处接缝**去掉钩子段尾淡出**（亮照片 60 一帧砸进片头的黑起幅）… 判据①红
       R0 与 R8 只差装配层那 0.4s 淡出：这条钉的是「回滚之后唯一保留的抗闪屏装置」真的在
       判决里起作用（另一份证据在 scripts/tests/test-hook-seam-fade.py，它同时静态扫两条
       渲染路径）。

退出码：0 全部断言通过；1 有任何一条红。跑法：python3 scripts/tests/test-flicker-rules.py
"""
import contextlib
import importlib.util
import io
import json
import math
import os
import re
import sys

import numpy as np

FPS = 30.0
HERE = os.path.dirname(os.path.abspath(__file__))              # …/scripts/tests
ROOT = os.path.dirname(os.path.dirname(HERE))                   # 仓库根
GATE_PATH = os.path.join(ROOT, "scripts", "check-luminance-flicker.py")


def _load_gate():
    spec = importlib.util.spec_from_file_location("flicker_gate", GATE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = _load_gate()

LINEAR = lambda p: p                                    # noqa: E731
SINE = lambda p: (1.0 - math.cos(math.pi * p)) / 2.0    # sine.inOut：正文切点用的缓动
P2OUT = lambda p: 1.0 - (1.0 - p) ** 2.0                # power2.out：整幅换页的淡入

HOOK_DUR = 6.65        # 钩子段时长（假件里的本期结构，和真片一个量级）
TAIL = 1.0             # OPENING_TAIL：正文起幅也算开场
BODY_REF = 6.0         # 正文参照段（不参与判决，只当参照物）

_LIB = json.load(open(os.path.join(ROOT, "scripts", "lib", "intro-template.json"),
                      encoding="utf-8"))
TRIM = float(_LIB["trim_seconds"])               # 共享片头窗长：唯一真源
SINK = float(_LIB.get("hook_fade_out_seconds", 0.0))   # 钩子段尾淡出：唯一真源

HOOK_LUMA = 42.0       # 钩子那张氛围照片的实测量级
BLINK_LUMA = 26.0      # 一记砸黑的**起点**取正文带里最暗的那档（归一照片实测 25.5~31.1）。
                       # 下探摊在 3 帧上 → 单帧 8.7 级：够不上 ①的 40 级闸刀线，也够不上
                       # ③的允许线（正文 4.9 → max(16, 4×4.9)=19.8），但整段 26 级、0.1s 的
                       # 一去一回同时点亮 ②（最暗 0.0 < 18）、④（两条腿都 ≥8 级且 ≤0.35s）
                       # 和 ⑥（黑底从两层之间漏进来）。R1 的红只该来自这三条形状。
PLATEAU = 171.0        # 回滚后片头的亮度平台（书封整幅，实测均值 95~230 里的常见档）
RESULT_LUMA = 50.0     # 结果页是本期 result-bridge 的副本 → 正文带

# 一次整幅换画在一帧里换掉的像素平均差（36x48 灰度口径的量级）。
DIFF_FROM_HOOK = 139.8
DIFF_PLATE = 47.9
DIFF_TO_RESULT = 143.3
DIFF_LUMA_ONLY = 20.0      # 两张**构图相近、只差曝光**的照片：亮度大跨度而内容几乎不换
# 正文两张氛围图的像素差没有单独量过，反过来标定：正文每个切点 0.64s=19 帧 sine，峰值
# alpha 步长 π/(2·19)=0.0827，要让它落在《李尔王》成片正文实测的 MAD 峰值 5.9 上。
DIFF_BODY_STILL = 5.9 / (math.pi / (2.0 * max(1, int(round(0.64 * FPS)))))
DIFF_BODY_SNAPPY = 120.0


class Seq:
    """一条合成胶片：每个事件同时给亮度**和**内容差，两者共用同一个 alpha。"""

    def __init__(self):
        self.luma = []
        self.content = []

    def hold(self, value, seconds):
        for _ in range(max(0, int(round(seconds * FPS)))):
            self.luma.append(float(value))
            self.content.append(0.0)
        return self

    def trans(self, start, end, diff, seconds, ease=LINEAR):
        n = max(1, int(round(seconds * FPS)))
        prev_alpha = 0.0
        for k in range(1, n + 1):
            alpha = ease(k / n)
            self.luma.append(start + (end - start) * alpha)
            self.content.append(diff * abs(alpha - prev_alpha))
            prev_alpha = alpha
        return self

    def mad(self):
        return self.content[1:]        # mad[i] 是第 i→i+1 帧的差，和 np.diff 同下标

    def seconds(self):
        return len(self.luma) / FPS


def build_hook(shape="healthy", sink=SINK):
    """钩子段本体（被判）+ 段尾淡出（豁免）。整段长度固定为 HOOK_DUR。

    shape="healthy" 两张亮度相近的照片，切点是 0.72s 的**凸组合**溶解（.scene 只淡入上一层），
        末段按装配层淡出到黑；
    shape="blink"   照片之间一记单帧砸黑（观众说的「闪一下」）；
    shape="swap"    照片之间整幅换画而均值不动（判据⑤ 唯一能看见的形状）；
    shape="span"    一次 120 级跨度只摊在 0.26s 上（判据③ 唯一能看见的形状）。
    """
    body = HOOK_DUR - sink
    s = Seq()
    if shape == "healthy":
        s.hold(59.0, body * 0.45)
        s.trans(59.0, 60.2, 12.0, 0.72, SINE)          # 凸组合：中点 59.6，不越带
        s.hold(60.2, body * 0.55 - 0.72)
    elif shape == "blink":
        s.hold(BLINK_LUMA, body * 0.5)
        s.trans(BLINK_LUMA, 0.0, DIFF_LUMA_ONLY, 3.0 / FPS, LINEAR)
        s.hold(0.0, 2.0 / FPS)
        s.trans(0.0, BLINK_LUMA, DIFF_LUMA_ONLY, 3.0 / FPS, LINEAR)
        s.hold(BLINK_LUMA, body * 0.5 - 8.0 / FPS)
    elif shape == "swap":
        s.hold(59.0, body * 0.5)
        # 均值一样（59.0 → 59.0），但一整幅像素换了：①②③④⑥ 全盲，只有 ⑤ 看得见。
        # 34.0 = 《李尔王》成片那记整幅换页在 36x48 口径上的实测 MAD 峰值。
        s.trans(59.0, 59.0, 34.0, 1.0 / FPS, LINEAR)
        s.hold(59.0, body * 0.5 - 1.0 / FPS)
    elif shape == "span":
        # 一次 120 级的**单调**大跨度只摊在 4 帧（0.133s）上：单帧 30 级够不上 ①的 40，
        # 一条腿也构不成 ④的「去了又回」，内容几乎不换（两张同构图、只差曝光的照片）——
        # 能看见它的只有 ③「跨度必须摊在时间上」。
        s.hold(30.0, body * 0.5)
        s.trans(30.0, 150.0, DIFF_LUMA_ONLY, 4.0 / FPS, LINEAR)
        s.hold(150.0, body * 0.5 - 4.0 / FPS)
    else:
        raise ValueError(f"未知钩子形状 {shape}")
    if sink:
        s.trans(s.luma[-1], 0.0, 20.0, sink, LINEAR)          # 淡出到黑 = 片头黑起幅的钩子一侧
    return s


def build_intro_original():
    """回滚后的共享片头真实形状（原样交付，只报告不判红）。

    数值来自 2026-10-03 那版模板本身：0.13s 纯黑起幅 + ~0.47s 飞到平台，六张**整幅**书封
    一页盖一页（可见窗均亮度 95/208/101/230/221/108/210 里的量级），每页 0.175s，
    末页交给结果页那一拍先用 `.template-blackout` 下探全黑（86→0→44，233ms）。
    """
    pages = [95.0, 208.0, 101.0, 230.0, 221.0, 108.0]
    s = Seq()
    s.hold(0.0, 0.13)
    s.trans(0.0, pages[0], DIFF_PLATE, 0.47, P2OUT)
    prev = pages[0]
    for value in pages[1:] + [210.0]:
        s.trans(prev, value, DIFF_PLATE, 1.0 / FPS, LINEAR)      # 整幅换页 = 一帧
        s.hold(value, 0.175 - 1.0 / FPS)
        prev = value
    # 书单→结果页那一拍：砸黑（设计），然后结果页在 2.38s 出现
    s.trans(prev, 0.0, DIFF_TO_RESULT, 0.04, LINEAR)
    s.hold(0.0, 0.08)
    s.trans(0.0, RESULT_LUMA, DIFF_TO_RESULT, 0.08, P2OUT)
    s.hold(RESULT_LUMA, max(0.0, TRIM - s.seconds()))
    return s


def build_tail():
    """正文起幅（被判）：片头定格的那一期 result-bridge 副本，正文参照段也从这一档起步。

    必须是**平的**并且和正文参照的第一帧同值：否则这条边界会被算进正文参照的峰值，
    把 ③⑤ 的允许线整体抬高，用例就成了夹具的产物而不是判据的产物。
    """
    return Seq().hold(RESULT_LUMA, TAIL)


def build_body(scenes, diff=None, slam_at=None):
    """正文参照段：每个切点都是 0.64s 交叉溶解；slam_at=n 时把第 n 个切点换成单帧硬切。"""
    diff = DIFF_BODY_STILL if diff is None else diff
    s = Seq()
    s.hold(scenes[0], 0.9)
    for k, (prev, nxt) in enumerate(zip(scenes, scenes[1:])):
        if k == slam_at:
            s.trans(prev, nxt, diff, 1.0 / FPS, LINEAR)
        else:
            s.trans(prev, nxt, diff, 0.64, SINE)
        s.hold(nxt, 0.9)
    need = BODY_REF - s.seconds()
    if need > 0:
        s.hold(scenes[-1], need)
    return s


SCENES = [50.0, 110.0, 62.0, 122.0, 78.0, 120.0]
SCENES_SNAPPY = [50.0, 158.0, 60.0, 168.0, 58.0, 166.0]


def run_case(hook, intro, tail, body, sink=SINK, legacy=False):
    """把合成胶片交给门禁本体的 main()，返回门禁自己打印出来的判决数值。

    只换掉四个外部依赖（frame_curves / probe / media_duration / opening_window）当假件，
    main()、全部阈值、flickers()、rate_allowance()、content_allowance()、
    dissolve_excursions() 都是门禁本体。断言的是门禁打印出来的结论与数值，不是这里重新
    实现一遍的比较——重新实现的比较会跟着门禁一起漂移。
    `sink` 是**假件里**的段尾淡出长度：传 0 就得到「本期没画淡出」的那版接缝。
    """
    saved = {k: getattr(gate, k) for k in
             ("frame_curves", "probe", "media_duration", "opening_window")}
    saved_argv = list(sys.argv)
    parts = [hook, intro, tail, body]
    means = np.concatenate([np.asarray(p.luma, dtype=float) for p in parts])
    mad = np.concatenate([np.asarray(p.mad(), dtype=float) for p in parts])
    total = len(means)
    hook_dur = hook.seconds()
    trim = intro.seconds()
    intro_sink = sink

    def fake_frame_curves(path, limit=None, band_rows=None):
        n = total if limit is None else min(total, int(round(limit * FPS)))
        return means[:n], mad[:max(0, n - 1)], means[:n]

    def fake_opening_window(path, trim_seconds, hook_fade=0.0):
        return dict(end=round(hook_dur + trim + TAIL, 2), floor_start=0.0,
                    hook_dur=round(hook_dur, 2), resolved=True, episode="synthetic",
                    intro=(round(hook_dur, 2), round(hook_dur + trim, 2)),
                    hook_sink=(round(hook_dur - intro_sink, 2) if intro_sink else None))

    gate.frame_curves = fake_frame_curves
    gate.opening_window = fake_opening_window
    gate.probe = lambda path, key: f"{int(FPS)}/1"
    gate.media_duration = lambda path: total / FPS
    buf = io.StringIO()
    try:
        sys.argv = [GATE_PATH, GATE_PATH] + (["--legacy-strobe"] if legacy else [])
        with contextlib.redirect_stdout(buf):
            try:
                gate.main()
                rc = 0
            except SystemExit as exc:
                rc = exc.code if isinstance(exc.code, int) else 1
    finally:
        for k, v in saved.items():
            setattr(gate, k, v)
        sys.argv = saved_argv

    out = buf.getvalue()
    if "一帧都没解出来" in out or "这道门没看到任何画面" in out:
        raise AssertionError("假件没接上：门禁一帧都没看到，这个绿是空的\n" + out)

    rate = re.search(r"峰值每帧跳变：开场 ([\d.]+) / 正文 ([\d.]+)（[^）]*）→ 判据③允许 ([\d.]+)", out)
    flick = re.search(r"判据④来回：(\d+) 处", out)
    content = re.search(r"判据⑤换页：内容跳变峰值 开场 ([\d.]+) / 正文 ([\d.]+)"
                        r"（[^）]*）→ 允许 ([\d.]+)＝[^\n]*?\s*超过 (\d+) 帧", out)
    exc_m = re.search(r"判据⑥漏底：越带 (\d+) 处", out)
    judged_m = re.search(r"被判 (\d+) 帧", out)
    exempt_m = re.search(r"豁免窗.*?([\d.]+)~[\d.]+s\s+(\d+) 帧：峰值 ([\d.]+)、最暗 ([\d.]+)"
                         r"、来回 (\d+) 处、MAD 峰值 ([\d.]+)、越带 (\d+) 处"
                         r"——按同一套判据本会翻 (\d+) 项红", out, re.S)
    legacy_m = re.search(r"旧频闪判据对照：\|Δ\|>[\d.]+ 共 (\d+) 帧", out)
    slam_m = re.search(r"亮度闪屏：(\d+) 帧单帧亮度跳变超过", out)
    if not (rate and flick and content and exc_m and judged_m and exempt_m):
        raise AssertionError("门禁的输出形状变了，假件读不到判决数值——这条绿不可信\n" + out)
    reds = set()
    for token, tag in (("❌ 眨眼：", "眨眼"), ("❌ 摊不开：", "摊不开"),
                       ("❌ 来回：", "来回"), ("❌ 换页：", "换页"),
                       ("❌ 漏底：", "漏底")):
        if token in out:
            reds.add(tag)
    if slam_m:
        reds.add("闸刀")
    return dict(rc=rc, out=out, reds=reds,
                opening_peak=float(rate.group(1)), body_peak=float(rate.group(2)),
                allowance=float(rate.group(3)), n_flick=int(flick.group(1)),
                mad_opening=float(content.group(1)), mad_body=float(content.group(2)),
                mad_allowance=float(content.group(3)), n_swap=int(content.group(4)),
                n_exc=int(exc_m.group(1)), n_judged=int(judged_m.group(1)),
                n_slam=int(slam_m.group(1)) if slam_m else 0,
                n_legacy=int(legacy_m.group(1)) if legacy_m else None,
                ex_frames=int(exempt_m.group(2)), ex_peak=float(exempt_m.group(3)),
                ex_dark=float(exempt_m.group(4)), ex_flick=int(exempt_m.group(5)),
                ex_mad=float(exempt_m.group(6)), ex_exc=int(exempt_m.group(7)),
                ex_would=int(exempt_m.group(8)))


def max_monotone_span(seq):
    """序列里最大的一次**单调**亮度跨度（同向帧变化累加，噪声帧不计）。

    这是**夹具的属性**，不是门禁的结论：用它断言「健康的开场里本来就有一次上百级的亮度事件，
    而门禁必须放过它」，否则绿可能只是因为夹具根本没做跨度。
    """
    d = np.diff(np.asarray(seq, dtype=float))
    best = cur = 0.0
    for v in d:
        if abs(v) < gate.FLICKER_LEG_EPS:
            continue
        if (v > 0) == (cur > 0) or cur == 0.0:
            cur += v
        else:
            best = max(best, abs(cur))
            cur = v
        best = max(best, abs(cur))
    return best


def expect(name, res, want_reds, extra=()):
    fails = []
    if res["reds"] != want_reds:
        fails.append(f"{name}: 判据红集合实际 {sorted(res['reds'])} 期望 {sorted(want_reds)}")
    want_rc = 1 if want_reds else 0
    if res["rc"] != want_rc:
        fails.append(f"{name}: 退出码实际 {res['rc']} 期望 {want_rc}")
    for label, ok, detail in extra:
        if not ok:
            fails.append(f"{name}: {label} —— {detail}")
    return fails


def describe(res):
    leg = "" if res["n_legacy"] is None else f"  旧⑤ {res['n_legacy']}帧"
    tags = "/".join(sorted(res["reds"])) or "无"
    return (f"红={tags:<12s} 亮度峰值 {res['opening_peak']:5.1f}/允许 {res['allowance']:5.1f}"
            f"  MAD {res['mad_opening']:5.1f}/允许 {res['mad_allowance']:5.1f}"
            f"  来回 {res['n_flick']}处  被判 {res['n_judged']}帧{leg}")


def main():
    fails = []
    print(f"判据①闸刀线 {gate.DEFAULT_MAX_DELTA:g}  ②眨眼下限 {gate.OPENING_LUMA_FLOOR:g}"
          f"  ③允许＝max({gate.RATE_FLOOR:g}, {gate.RATE_MULTIPLE:g}×正文亮度峰值)"
          f"  ④来回：腿 ≥{gate.FLICKER_LEG_MIN:g} 级且 ≤{gate.FLICKER_LEG_MAX_S:g}s"
          f"  ⑤换页：允许＝max({gate.CONTENT_FLOOR:g}, {gate.CONTENT_MULTIPLE:g}×正文MAD)")
    print(f"判决范围：钩子段 [0, {HOOK_DUR - SINK:.2f}s) + 正文起幅 "
          f"[{HOOK_DUR + TRIM:.2f}, {HOOK_DUR + TRIM + TAIL:.2f}s)；豁免窗 "
          f"[{HOOK_DUR - SINK:.2f}, {HOOK_DUR + TRIM:.2f}s)（段尾淡出 {SINK:g}s + 片头 "
          f"{TRIM:g}s，都只报告）")

    intro = build_intro_original()
    tail = build_tail()
    body = build_body(SCENES)
    body_snappy = build_body(SCENES_SNAPPY, diff=DIFF_BODY_SNAPPY)
    # 正文参照必须先落在实测上，否则每一条 ⑤ 的允许线都是浮空的
    if abs(max(body.mad()) - 5.9) > 0.3:
        fails.append(f"合成正文 MAD 峰值 {max(body.mad()):.1f}，没标定到《李尔王》实测的 5.9")

    healthy = build_hook("healthy")
    blink = build_hook("blink")
    swap = build_hook("swap")
    span = build_hook("span")

    R0 = run_case(healthy, intro, tail, body, legacy=True)
    R1 = run_case(blink, intro, tail, body, legacy=True)
    R2 = run_case(healthy, build_intro_blink_copy(), tail, body, legacy=True)
    R3 = run_case(swap, intro, tail, body, legacy=True)
    R4 = run_case(span, intro, tail, body, legacy=True)
    R5 = run_case(swap, intro, tail, body_snappy, legacy=True)
    slam_body = build_body(SCENES, slam_at=1)
    R6 = run_case(healthy, intro, tail, slam_body, legacy=True)
    R7 = R0   # 同一条胶片：这里只钉它对豁免窗**印出来**的数
    R8 = run_case(build_hook("healthy", sink=0.0), intro, tail, body, sink=0.0, legacy=True)

    # R2 用「把 R1 那一记砸黑原样搬进片头窗」的夹具：形状、幅度、时长逐字节相同，
    # 只有位置不同——这就是豁免的机器可读形式。
    print()
    cases = [
        ("R0 健康钩子段（凸组合溶解 + 段尾淡出）", R0, set(),
         [("开场确实含一次 ≥60 级、时长 >0.7s 的合法大跨度（这个绿确实测到了大跨度）",
           max_monotone_span(healthy.luma) >= 60.0,
           f"{max_monotone_span(healthy.luma):.1f} 级"),
          ("来回必须 0 处", R0["n_flick"] == 0, f"{R0['n_flick']} 处"),
          ("段尾淡出与片头整段都不在判决帧里",
           R0["n_judged"] == int(round((HOOK_DUR - SINK) * FPS))
           + int(round(TAIL * FPS)),
           f"被判 {R0['n_judged']} 帧，应为钩子 {int(round((HOOK_DUR - SINK) * FPS))} "
           f"+ 正文起幅 {int(round(TAIL * FPS))}"),
          ("开场窗总帧数明显大于被判帧数（豁免确实摘掉了一大段）",
           R0["n_judged"] < int(round((HOOK_DUR + TRIM + TAIL) * FPS)) - 60,
           f"被判 {R0['n_judged']} / 开场窗 {int(round((HOOK_DUR + TRIM + TAIL) * FPS))}")]),
        ("R1 钩子段里一记砸黑 26→0→26", R1, {"眨眼", "来回", "漏底"},
         [("够不上闸刀线（②④⑥ 的红不是因为 ①）", R1["n_slam"] == 0, f"{R1['n_slam']} 帧"),
          ("③ 看不见它（摊在 3 帧上的 26 级 < 允许线）", R1["opening_peak"] <= R1["allowance"],
           f"{R1['opening_peak']:.1f} vs {R1['allowance']:.1f}"),
          ("⑥ 漏底也抓到它（黑底从两层之间漏进来）", R1["n_exc"] >= 1,
           f"{R1['n_exc']} 处"),
          ("旧判据⑤在这版只数到 ≤3 帧：它靠魔法数报红，而这里报红的理由只有下探与腿长",
           R1["n_legacy"] is not None and R1["n_legacy"] <= gate.STROBE_MAX_FRAMES,
           f"{R1['n_legacy']} 帧")]),
        ("R2 与 R1 **同一个形状**搬进共享片头窗", R2, set(),
         [("同形状、只换位置：判决必须从红翻成绿（豁免的是范围不是阈值）",
           R1["reds"] and not R2["reds"], f"R1={sorted(R1['reds'])} R2={sorted(R2['reds'])}"),
          ("报告窗里必须把这几项红**印出来**（不印就是暗账）",
           R2["ex_would"] >= 3, f"本会翻 {R2['ex_would']} 项红"),
          ("豁免窗里确实量到了那一记砸黑（最暗 0.0、来回 ≥1 处），不是窗口空着",
           R2["ex_dark"] <= 0.05 and R2["ex_flick"] >= 1,
           f"最暗 {R2['ex_dark']:.1f}、来回 {R2['ex_flick']} 处"),
          ("被判帧数与 R1 完全相同（豁免没有把钩子一起摘掉）",
           R2["n_judged"] == R1["n_judged"], f"{R2['n_judged']} vs {R1['n_judged']}")]),
        ("R3 钩子段里整幅换画而均值不动", R3, {"换页"},
         [("①②③④⑥ 全部沉默（均值曲线一点没动）",
           R3["opening_peak"] <= R3["allowance"] and R3["n_flick"] == 0
           and R3["n_slam"] == 0 and R3["n_exc"] == 0,
           f"峰值 {R3['opening_peak']:.1f} 来回 {R3['n_flick']} 闸刀 {R3['n_slam']} 越带 {R3['n_exc']}"),
          ("旧判据⑤在这一版必须 0 帧——它正是被这个形状退役的",
           R3["n_legacy"] == 0, f"{R3['n_legacy']} 帧")]),
        ("R4 钩子段里 120 级跨度只摊在 4 帧上", R4, {"摊不开"},
         [("判据④必须看不见它（单调大跨度不是去了又回）", R4["n_flick"] == 0,
           f"{R4['n_flick']} 处"),
          ("亮度峰值确实超过允许线", R4["opening_peak"] > R4["allowance"],
           f"{R4['opening_peak']:.1f} > {R4['allowance']:.1f}")]),
        ("R5 与 R3 同开场，配**正文更跳**的参照物", R5, set(),
         [("同一份开场序列换参照物之后判决翻面 → ③⑤ 是相对量，不是绝对天花板",
           R3["reds"] == {"换页"} and not R5["reds"],
           f"R3={sorted(R3['reds'])} R5={sorted(R5['reds'])}"),
          ("允许线确实跟着正文抬到 R3 的峰值之上", R5["mad_allowance"] > R3["mad_opening"],
           f"{R5['mad_allowance']:.1f} > {R3['mad_opening']:.1f}")]),
        ("R6 正文段里一次单帧硬切", R6, {"闸刀"},
         [("开场窗口本身没有新缺陷（红来自正文那 60 级）", R6["n_flick"] == 0,
           f"{R6['n_flick']} 处"),
          ("闪屏不只会在开场发生：① 是全片口径", R6["n_slam"] >= 1, f"{R6['n_slam']} 帧")]),
        ("R7 回滚后的共享片头真实形状（整幅换页 + 那一拍砸黑）", R7, set(),
         [("片头内部那几拍必须**印**出很大的数：峰值 ≥40（闸刀量级）、最暗 0.0、来回 ≥1 处",
           R7["ex_peak"] >= 40.0 and R7["ex_dark"] <= 0.05 and R7["ex_flick"] >= 1,
           f"峰值 {R7['ex_peak']:.1f} 最暗 {R7['ex_dark']:.1f} 来回 {R7['ex_flick']} 处"),
          ("按同一套判据本会翻 ≥3 项红，而门禁必须只报告", R7["ex_would"] >= 3,
           f"本会翻 {R7['ex_would']} 项"),
          ("闸刀线只让开片头内部的帧对：全片 |Δ|>40 一帧不判（这就是回滚的代价，写在范围内）",
           R7["n_slam"] == 0, f"{R7['n_slam']} 帧")]),
        ("R8 同一处接缝**去掉**钩子段尾淡出", R8, {"闸刀"},
         [("接缝那一对（钩子末帧→片头首帧）不在豁免里：亮照片一帧砸进黑起幅照样判红",
           R8["n_slam"] >= 1, f"{R8['n_slam']} 帧"),
          ("与 R0 的差别只有装配层那 0.4s 淡出 → 淡出是唯一保留的抗闪屏装置",
           not R0["reds"], f"R0 红={sorted(R0['reds'])}")]),
    ]

    for name, res, want, extra in cases:
        fails.extend(expect(name, res, want, extra))
        print(f"  {'✅' if not expect(name, res, want, extra) else '❌'} {name}"
              f"  {describe(res)}")

    # 同一个形状在两个位置的对照必须有第二份证据：豁免窗里的帧数确实跟着 lib 走
    if SINK > 0:
        no_sink = run_case(build_hook("healthy", sink=0.0), intro, tail, body, sink=0.0)
        with_sink = run_case(build_hook("healthy", sink=SINK), intro, tail, body, sink=SINK)
        if no_sink["n_judged"] - with_sink["n_judged"] != int(round(SINK * FPS)):
            fails.append(f"豁免窗没有真的按 lib 的 hook_fade_out_seconds 扩张："
                         f"被判帧数差 {no_sink['n_judged'] - with_sink['n_judged']}，"
                         f"应为 {int(round(SINK * FPS))} 帧")

    print()
    if fails:
        for f in fails:
            print(f"❌ {f}")
        sys.exit(1)
    print(f"✅ 亮度闪屏判据回归通过：{len(cases)} 个用例，"
          f"①~⑥ 各自抓到该抓的、放过该放的，且**同一个形状摆在钩子段红、摆进片头窗只报告**")
    sys.exit(0)


def build_intro_blink_copy():
    """把 R1 那一记砸黑原样搬进片头窗：同幅度、同帧数、同缓动，只有**位置**不同。

    起幅必须从 0.0 开始（真实片头的第一帧就是全黑），否则段尾淡出的末帧与它之间会多出
    一记人为硬切，那条接缝本来就该判红，用例就不再是「同一个形状换个位置」了。
    """
    s = Seq()
    s.hold(0.0, 0.13)
    s.trans(0.0, BLINK_LUMA, DIFF_PLATE, 0.47, P2OUT)
    s.hold(BLINK_LUMA, 0.40)
    s.trans(BLINK_LUMA, 0.0, DIFF_LUMA_ONLY, 3.0 / FPS, LINEAR)   # 与 R1 逐帧相同
    s.hold(0.0, 2.0 / FPS)
    s.trans(0.0, BLINK_LUMA, DIFF_LUMA_ONLY, 3.0 / FPS, LINEAR)
    s.hold(BLINK_LUMA, max(0.0, TRIM - s.seconds() - 1.0 / FPS))
    s.trans(BLINK_LUMA, RESULT_LUMA, DIFF_TO_RESULT, 1.0 / FPS, LINEAR)
    s.hold(RESULT_LUMA, max(0.0, TRIM - s.seconds()))
    return s


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
