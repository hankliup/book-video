#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""渲后单帧伪影扫描的双向对照（2026-10-07，判决口径＝组合声明的**单帧刀口**）。

要钉住的东西，每一条都对应一次真实事故或一次真实的判错：

1) **能看见伪影**：底带单帧塌陷（≥10 级、下一帧回落）必须 rc=1。这就是用户 2026-10-06
   第①条规则要防的缺陷——《一颗简单的心》2026-10-07 那一记 38.3→27.5→38.3（钩子第
   202/203 帧）让 13 道交付门禁全绿，因为第 10 道门问的是峰值/去了又回/MAD，一记 11 级
   的单帧下探三条判据都够不上线。
2) **豁免只能来自组合自己**：本仓库的设计语言里能造出「跳一帧又立刻跳回」的只有单帧刀口
   （`tl.set`、`tl.call`、写了 `duration` 且 ≤ 一帧半的 tween——共享片头的砸黑一拍就是它），
   加上 `revealCaption` 的**收场**。落在刀口 ±KNIFE_PAD 之外的脉冲一律是伪影。
3) **可复现性不是豁免**（第一版假绿灯）：同一份钩子连渲三次是「有 / 干净 / 有」，两次落在
   同一帧、连像素值都一样。所以只给 baseline、不给 composition 时必须仍然 rc=1。
4) **渐变不是豁免**（第二版假绿灯，当天就被真像素证伪）：字幕**出场**是一条 0.16s 的
   `fromTo`，照片推镜是 4~5.5s。单调的 ramp 在帧序列上给不出回头，可把这类时刻按窗长展开
   之后，r3 那次渲染 t=5.03s 的底带塌陷（r1/r2 同一帧干净）就被 4.69s 的字幕淡入买成了
   「设计内」——第 6 组七个方向（四个渐变必须红、三个刀口必须绿）钉住这一条。
5) **时刻表不能读宽**：revealCaption 的第三个实参是 hold 秒数不是时刻。最早一版把花括号外
   的数字都当时刻，1.74s 凭空多出一个豁免窗，放过了第 49 帧底带那一记真伪影——第 5 组是它
   的对照；没写 `duration` 的 `to` 按 GSAP 默认 0.5s 算（也是渐变），不能当成「时长为 0 的刀口」。
6) 接线：共享段渲染必须真的调用扫描器、真的把本工程 index.html 传进去，且发生在落
   provenance 账本**之前**。

跑法：python3 scripts/tests/test-render-artifacts.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCANNER = os.path.join("scripts", "check-render-artifacts.py")
DRIVER = os.path.join("scripts", "render-hook-batch.sh")

failures = []
notes = []


def ok(cond, msg):
    (notes if cond else failures).append(("PASS " if cond else "FAIL ") + msg)
    return cond


def ff(args):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + args,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r


def box_clip(path, frames, y0, h, color, base="0x323232", seconds=6.0):
    """纯色底 + 指定帧上的一记整块填充；-g 1 全 intra，保住单帧形状不被帧间压缩抹平。"""
    if not frames:                       # 健康对照：不注入任何帧
        ff(["-f", "lavfi", "-i", f"color=c={base}:s=720x960:r=30:d={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "1", "-crf", "10", path])
        return
    enable = "+".join(f"eq(n\\,{f})" for f in frames)
    vf = f"drawbox=x=0:y={y0}:w=720:h={h}:color={color}:t=fill:enable='{enable}'"
    ff(["-f", "lavfi", "-i", f"color=c={base}:s=720x960:r=30:d={seconds}",
        "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "1", "-crf", "10", path])


def composition(tmp, name, lines):
    """一份最小 index.html：只放真要读的 GSAP 调用行。"""
    path = os.path.join(tmp, name)
    body = "\n".join(lines)
    open(path, "w", encoding="utf-8").write(
        "<html><body><script>\nvar tl = gsap.timeline();\n" + body + "\n</script></body></html>\n")
    return path


def scan(*args):
    p = subprocess.run([sys.executable, SCANNER] + list(args),
                       capture_output=True, text=True, cwd=ROOT)
    out = p.stdout + p.stderr
    payload = {}
    for line in reversed(out.strip().splitlines()):
        try:
            payload = json.loads(line)
            break
        except Exception:
            continue
    return p.returncode, out, payload


DIP_FRAME = 100          # drawbox 落在 n=100 → 扫描器报 frame 99（t≈3.30s）
DIP_T = 3.30


def main():
    tmp = tempfile.mkdtemp(prefix="render-artifacts-test-")
    p = lambda name: os.path.join(tmp, name)

    clean = p("clean.mp4")
    dip = p("dip.mp4")                        # 底带单帧塌陷
    dip_prev = p("dip-prev.mp4")              # 与 dip 同一帧同一区带同一方向
    dip_far = p("dip-far.mp4")                # 帧位差 50
    dip_top = p("dip-top.mp4")                # 区带不同
    rise = p("rise.mp4")                      # 同帧但抬起
    weak = p("weak.mp4")                      # 6 级抖动
    box_clip(clean, [], 834, 126, "black")
    box_clip(dip, [DIP_FRAME], 834, 126, "black")
    box_clip(dip_prev, [DIP_FRAME], 834, 126, "black")
    box_clip(dip_far, [DIP_FRAME + 50], 834, 126, "black")
    box_clip(dip_top, [DIP_FRAME], 60, 120, "black")
    box_clip(rise, [DIP_FRAME], 834, 126, "white")
    box_clip(weak, [DIP_FRAME], 834, 126, "0x383838")

    cap = composition(tmp, "no-events.html", [
        'tl.to(".s1 .photo", {scale: 1.1, duration: 4.0}, 0);',
        'revealCaption(".c1 .seg0", 0.20, 1.20);',      # 唯一的刀口在 1.40（收场），离 3.30 很远
    ])
    cap_at = composition(tmp, "event-at-dip.html", [
        'tl.set(".c2 .seg0", {opacity: 0, y: -10}, %.2f);' % DIP_T,   # 一帧收场＝设计内
    ])
    cap_hold = composition(tmp, "hold-only.html", [
        # 真事件在 2.40（出场）与 5.70（收场）。塌陷那帧的 3.30 只是 hold 的**长度**。
        # 第一版把每个花括号外的数字都当时刻，于是 3.30 凭空成为豁免中心。
        'revealCaption(".c1 .seg0", 2.40, %.2f);' % DIP_T,
    ])
    cap_fade = composition(tmp, "fade-at-dip.html", [
        # 字幕出场那一类的 0.72s 渐变：单调 ramp，给不出「跳一帧又立刻跳回」，不许豁免。
        'tl.fromTo(".s2", {opacity: 0}, {opacity: 1, duration: 0.72, ease: "sine.inOut"}, %.2f);' % DIP_T,
    ])
    cap_pan = composition(tmp, "long-pan.html", [
        # 一个横跨全片的推镜：同一类渐变，方向上比 fade 更宽松，也必须仍然红。
        'tl.to(".s1 .photo", {scale: 1.1, duration: 5.5, ease: "sine.inOut"}, 0);',
    ])
    cap_blackout = composition(tmp, "knife-blackout.html", [
        # 共享片头的砸黑一拍：声明时长 0.04s（一帧半以内）→ 这一记真的是单帧跳变又跳回。
        'tl.to(".template-blackout", {opacity: 1, duration: 0.04, ease: "none"}, %.2f);' % DIP_T,
    ])
    cap_call = composition(tmp, "knife-call.html", [
        'tl.call(function () { swapLayer(); }, [], %.2f);' % DIP_T,
    ])
    cap_nokey = composition(tmp, "to-without-duration.html", [
        # 没写 duration 的 to：GSAP 默认 0.5s，是渐变。按「缺字段＝0」处理会把它当成刀口，
        # 于是任何一份带匿名 tween 的组成都能就地买通豁免。
        'tl.to(".s1 .photo", {x: 120, ease: "none"}, %.2f);' % DIP_T,
    ])
    cap_show = composition(tmp, "reveal-at-dip.html", [
        'revealCaption(".c1 .seg0", %.2f, 1.00);' % DIP_T,     # 落在 DIP_T 的是**出场**（0.16s 渐变）
    ])
    cap_hide = composition(tmp, "reveal-hide-at-dip.html", [
        'revealCaption(".c1 .seg0", 2.30, %.2f);' % (DIP_T - 2.30),   # 收场时刻 start+hold＝DIP_T
    ])

    # --- 1) 健康产物跑绿 ---------------------------------------------------------------
    rc, out, _ = scan(clean, "--composition", cap)
    ok(rc == 0 and "clean" in out, f"纯色无脉冲＝干净（rc={rc}）")

    # --- 2) 伪影跑红，且明细能指到帧 ----------------------------------------------------
    rc, out, payload = scan(dip, "--composition", cap)
    ok(rc == 1 and "ARTIFACT" in out, f"底带单帧塌陷判伪影（rc={rc}）")
    m = re.search(r"ARTIFACT \S+ frame (\d+)", out)
    ok(bool(m) and abs(int(m.group(1)) - (DIP_FRAME - 1)) <= 2,
       f"伪影帧号落在注入处附近（读出 {m.group(1) if m else '无'}）")

    # --- 3) 没有组成声明时，一个脉冲都不放过（严格默认） ---------------------------------
    rc, out, _ = scan(dip)
    ok(rc == 1, "不传 --composition 时仍判伪影（默认严格，不靠调用方记得传）")

    # --- 4) 组成里真有这一时刻 → 设计内，仍然打印 -----------------------------------------
    rc, out, payload = scan(dip, "--json", "--composition", cap_at)
    ok(rc == 0 and not payload.get("pulses"), f"声明在 %.2fs 的 tl.set 豁免这一帧（rc={rc}）" % DIP_T)
    ok(len(payload.get("design_events_in_film") or []) == 1,
       "设计内那条仍被打印（不是静默放过）")
    rc, out, _ = scan(dip, "--composition", cap_at)
    ok("设计内" in out, "人读的那一行把设计内的事件连时刻一起打出来")

    # --- 5) hold 长度不是时刻：这里必须仍然红（昨天读宽的那一条） --------------------------
    rc, out, _ = scan(dip, "--composition", cap_hold)
    ok(rc == 1, "revealCaption 的 hold 秒数不能被当成事件时刻（1.74s 那个误豁免的形状）")

    # --- 6) 只有单帧刀口能豁免：渐变一律不算设计 -------------------------------------------
    for label, capx, want in [
        ("0.72s 的淡入", cap_fade, 1),
        ("5.5s 的推镜", cap_pan, 1),
        ("revealCaption 的出场（0.16s 渐变）", cap_show, 1),
        ("没写 duration 的 to（GSAP 默认 0.5s）", cap_nokey, 1),
        ("0.04s 的砸黑一拍", cap_blackout, 0),
        ("tl.call 的一帧回调", cap_call, 0),
        ("revealCaption 的收场（tl.set 那一刀）", cap_hide, 0),
    ]:
        rc, out, _ = scan(dip, "--composition", capx)
        ok(rc == want, f"{label}→{'判伪影' if want else '算设计内'}（期望 rc={want}，实得 {rc}）")

    # --- 7) 可复现性不是豁免（昨天的假绿灯） ---------------------------------------------
    rc, out, payload = scan(dip, "--json", "--baseline", dip_prev)
    ok(rc == 1 and payload.get("pulses"), "同一帧可复现但无组成声明时仍判伪影")
    ok(len(payload.get("deterministic") or []) == 1,
       "同帧复现作为取证被记录（deterministic）")
    for label, other in [("帧位差 50", dip_far), ("区带不同", dip_top), ("方向相反", rise)]:
        rc, _, payload = scan(dip, "--json", "--baseline", other)
        ok(rc == 1 and not payload.get("deterministic"), f"baseline 不匹配（{label}）时记为不可复现")
    rc, out, payload = scan(dip, "--json", "--composition", cap_at, "--baseline", dip_prev)
    ok(rc == 0 and payload.get("design_events_in_film"),
       "组成豁免 + baseline 取证可以同时成立（记录也进 JSON，不静默）")

    # --- 8) 阈值不松：6 级抖动不是伪影 ----------------------------------------------------
    rc, out, _ = scan(weak, "--composition", cap)
    ok(rc == 0, f"6 级抖动不判伪影（阈值 10 级，rc={rc}）")

    # --- 9) 旧死表必须已经消失 -----------------------------------------------------------
    src = open(os.path.join(ROOT, SCANNER), encoding="utf-8").read()
    ok("(120,132)" not in src and "(276,289)" not in src,
       "扫描器里不再有钉死某一条钩子的豁免帧表")
    ok("def design_events" in src and "--composition" in src,
       "扫描器实现了「读组成声明的单帧刀口」这条判据")
    ok("KNIFE_SPAN_MAX" in src and "has_dur" in src,
       "刀口靠**声明时长 ≤ 一帧半且写了 duration** 认定，不是靠截断窗长（EVENT_SPAN_CAP 那版已废）")

    # --- 10) 接线：共享段渲染必须真的调用它，且在落账本之前 -------------------------------
    drv = open(os.path.join(ROOT, DRIVER), encoding="utf-8").read()
    ok("check-render-artifacts.py" in drv, "render-hook-batch.sh 调用扫描器（规则不再只有文档）")
    ok("--composition" in drv and "$dir/index.html" in drv,
       "驱动把**本期工程的 index.html** 传进去（豁免来自组成，不来自第二次渲染）")
    ok(re.search(r"for attempt in 1 2 3 4 5 6\b", drv) is not None,
       "伪影要重掷骰子（六轮：实测十分之三四的渲染带这一记，四轮会放过脏的那一份）")
    ok(drv.index("check-render-artifacts.py") < drv.index("composition-digest.py record"),
       "扫描发生在落账本**之前**——伪影产物不许留下 provenance")

    # --- 11) 真实提交在册的共享片头：砸黑一拍必须仍被读成刀口 -------------------------------
    # 这条不依赖 tmp/（预览目录会被下一次生成整片 rm -rf），量的是 templates/ 里那份组成。
    # 它钉的是「豁免与 10-05 裁决仍然对得上」：片头若被改成溶解（砸黑消失），design_events
    # 就数不出刀口，下一次 intro.mp4 扫描会红——而不是让一个已经回滚的形状悄悄混过去。
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "artifact_scanner", os.path.join(ROOT, SCANNER))
    sca = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sca)
    tmpl = os.path.join("templates", "shared-video-template", "intro", "index.html")
    knives = sca.design_events(os.path.join(ROOT, tmpl)) if os.path.exists(tmpl) else []
    ok(len(knives) >= 1, f"共享片头组成仍声明 {len(knives)} 记单帧刀口（砸黑一拍）")
    ok(all(span <= sca.KNIFE_SPAN_MAX for _, span in knives),
       "片头声明的刀口全在一帧半以内（渐变混进刀口表＝豁免又开始吃整段）")

    print("\n".join(notes))
    if failures:
        print("\n".join(failures))
        print(f"\n伪影扫描断言：{len(notes)} 通过 / {len(failures)} 失败")
        return 1
    print(f"\n伪影扫描断言：{len(notes)} 通过 / 0 失败")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
