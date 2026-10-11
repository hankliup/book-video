#!/usr/bin/env python3
"""场景交叉溶解的**合成形状**回归：叠在黑底上的两层同时跑反向 opacity，中点必然掉坑。

为什么需要它（2026-10-04《人间草木》第四批重渲）：钩子→片头那道接缝的下探修完之后（见
scripts/tests/test-junction-runway.py），十期里有四期仍然红在第 10 道门的判据④「去了又回」，
而缺陷不在装配层——它在**钩子段自己的换页**里，量 standalone hook.mp4 就能看见：
    《人间草木》  两张底图均亮度 59.0 / 60.2，溶解中点最低 45.1
    《一个陌生女人的来信》 54.4 附近，中点最低 40.0
    《一个叫欧维的男人决定去死》 68.0 / 60.6，中点最低 49.8
单帧只走 3~4 级（正文量级），够不上判据①的闸刀线 40，也不眨眼（判据②下界 18），
但一条腿掉 12~17 级、另一条腿爬 13~17 级，两条腿都在 0.35s 以内 → 判据④红。
用户嘴里这叫「钩子那段在闪」。

真源是 create-episode-preview.mjs 里这对同时刻的反向动画：
    tl.fromTo(".s"+k,     {opacity:0}, {opacity:1, duration:0.72, ease:"sine.inOut"}, startK)
    tl.to  (".s"+(k-1),   {opacity:1}, {opacity:0, duration:0.72, ease:"sine.inOut"}, startK)
两层各自算 alpha 再往黑底上合成，得到的是
    out = f·上层 + (1−f)²·下层        （少了一层 (1−f)·下层）
而不是真溶解的 f·上层 + (1−f)·下层。f=0.5 时中点比真溶解少 25%·下层——
45.1 ≈ 0.5×59.0 + 0.25×60.2 = 44.5，49.8 ≈ 0.5×68.0 + 0.25×60.6 = 49.2，全都对得上。
所以它跟底图有多亮有关：东京八平米（46.7/30.0）、人性的枷锁、丧钟为谁而鸣 那三期同一处代码
一次都不红，因为 25%·下层本来就不到 8 级。一条只在亮底图上才炸的规则，靠"换暗一点的图"
过门就是压暗过门（见 feedback-never-fix-by-dimming），改的必须是合成形状。

修法有两半，少一半就从「下探的溶解」变成「一帧硬切」：
 ① 退场层不淡出，保持 opacity:1 被进场层逐渐盖住 —— out = f·上层 + (1−f)·下层，
    与 scene-map 兜底渲染器里正文切点用的 ffmpeg xfade **逐帧相同**（用例 E 就是钉这一条：
    两条渲染路径同一语言，不是各写一套近似）。下层永远盖不满的**几何**风险不存在：.photo 是
    inset:-22px 且 scale≥1.035，平移 ±16px 仍在画幅里。
 ② .scene 必须有显式 z-index。只改 ① 之后实测《丧钟为谁而鸣》hook.mp4：溶解窗口 3.73~4.45s
    画面**一动不动**，4.47s 单帧从 60.7 跳到 39.9（判据③红 20.8 > 17.7）。原因是 .photo 带着
    z-index:1，而 .scene 不写 z-index 时**只有 opacity<1 才生成层叠上下文**：退场层不再淡出就
    永远是 opacity:1，它的 .photo 逃逸到 root 的正 z 组，把整个还在淡入的进场层（z=0 组的单元）
    盖住；等进场层 opacity 正好 ==1，它不再是层叠上下文，自己的 .photo 才按 DOM 顺序压上去——
    于是 0.72s 的溶解在画面上只剩最后那一帧。旧形状之所以"看起来是溶解"，恰恰是因为退场层
    当时也在淡出，顺手把自己降级成了层叠上下文：**那个下探和这个硬切是同一个 bug 的两面**。
    写上 z-index:1 之后每层永远是层叠上下文、整层作为一个单元 paint，同值按 DOM 顺序 →
    后进场的一直压着前一层，①的凸组合才真的成立。用例 G 静态钉这条，用例 H 拿真像素钉。

八个用例（判决全部走**门禁自己的** flickers()，这里不重新实现判据④）：
  A 实测《人间草木》那一对底图 + 旧形状 …………………… 判据④必须红（不相干产物跑红：形状没修时对），
     且模型的溶解中点必须对上盘上那版 hook.mp4 量到的最低值——凭空的公式不算证据
  B 同一对底图 + 新形状 ……………………………………… 判据④绿，中点不低于两端较暗的那个，峰值不降
  C 实测《东京八平米》那一对（暗）+ 旧形状 ……………… 本来就绿——钉住"这不是绝对天花板"，
     一条在暗底图上永远不炸的判据不会因为这次改动突然全库红
  D 机理：旧形状比凸组合少 f(1−f)·下层，最大 25%·下层 …… 五对实测/构造底图逐个对上，
     含「下层全黑时不凹」（那正是片头黑场能被等长溶解盖住、而亮底图换页盖不住的原因）。
     凹陷只跟**下层有多亮**有关，所以"挑暗一点的图"能洗绿这一条判据——那叫压暗过门，
     用例 D 最后一行钉的是新形状在更亮的一对（118/120）上同样不凹。
  E 新形状 == 同一 alpha 下的凸组合，逐帧落在两端之间 …… 与正文切点的 ffmpeg xfade 同一族
     （p 的推进速度不同：xfade 线性、GSAP 走 sine.inOut，那是判据③量的东西）
  F 时间轴不动：两种形状帧数相同、首末帧相同、进场时长仍是 0.72s
     （防止"把溶解拉长/缩短来摊开"这种把判据洗绿而画面变味的改法）
  G 静态守源码：进场淡入还在、退场淡出**不许回来**（含 .bridge 那一处）

★ 顺带量到的一条**门禁局限**（不改别人的阈值，只记录）：判据④的 `leg_max_s = 0.35` 对
  0.72s 的双向淡入淡出是**非单调**的——凹陷 15 级时两条腿 0.33s（红），凹陷 28~41 级时
  sine.inOut 两端的慢速段也越过 eps=0.4，腿变成 0.37s（绿）。也就是说同样一个坏形状，
  中等深度抓得住、更深反而抓不住（更深到 ≥40 级会由判据①兜住，中间 8~40 级这段是漏的）。
  本次修法把这个机理从共享构图里彻底摘掉，所以不需要为了这一期去动门禁；但共享片头
  那七张底图在 [155,185] 带里，将来若有别的双层反向 alpha 动画，这条会静默放过。
  用例 D 里 (118,120) 那一行断言的是**凹陷深度 = 25%·下层**这条机理，不是判据④的红绿，
  正是因为它在判据④下是绿的——把它写成"必须红"就是拿测试替门禁改阈值。

退出码：0 全部断言通过；1 有任何一条红。跑法：python3 scripts/tests/test-dissolve-dip.py
"""
import importlib.util
import math
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPTS = os.path.join(ROOT, "scripts")


def _load(mod_name, rel_path):
    spec = importlib.util.spec_from_file_location(mod_name, os.path.join(ROOT, rel_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 判据④用门禁本体，不在这里抄一份：抄的那份会跟着门禁漂移，而门禁的腿长/深度阈值
# 正是这次要满足的约束（2026-10-04 那天阈值被改过两轮，抄的必错）。
FLK = _load("flicker_gate", "scripts/check-luminance-flicker.py")
FPS = 30.0
DISSOLVE_S = 0.72


def sine_in_out(t):
    """GSAP 的 ease:"sine.inOut"：-(cos(PI·t)−1)/2。"""
    return -(math.cos(math.pi * t) - 1.0) / 2.0


def dissolve_trace(bottom, top, duration_s=DISSOLVE_S, fade_out_bottom=True,
                   ease=sine_in_out):
    """按 30fps 逐帧算「两层叠在黑底上的均亮度」。只算 alpha 合成，不含推镜位移。

    画家顺序：bottom 在下、top 在上。out = a_top·top + (1−a_top)·(a_bottom·bottom)。
    fade_out_bottom=True 是**旧**形状（a_bottom = 1−f），False 是修好的形状（a_bottom = 1）。
    """
    n = int(round(duration_s * FPS))
    trace = []
    for i in range(n + 1):
        f = ease(min(1.0, max(0.0, i / float(n))))
        a_bottom = (1.0 - f) if fade_out_bottom else 1.0
        trace.append(f * top + (1.0 - f) * (a_bottom * bottom))
    return np.asarray(trace)


def xfade_trace(bottom, top, duration_s=DISSOLVE_S, ease=lambda x: x):
    """ffmpeg xfade=transition=fade 的定义：凸组合 (1−p)·A + p·B，线性、不经过黑。

    p 的推进速度两条路径不同（正文 xfade 线性、钩子层 GSAP 走 sine.inOut），那是判据③
    量的东西；这一族共同的、也是旧形状违反的性质是**结果永远落在两端之间**。
    """
    n = int(round(duration_s * FPS))
    return np.asarray([(1.0 - ease(i / float(n))) * bottom + ease(i / float(n)) * top
                       for i in range(n + 1)])


def excursions(trace):
    return FLK.flickers(trace, FPS)


fails = []


def check(ok, label):
    print(("  ✅ " if ok else "  ❌ ") + label)
    if not ok:
        fails.append(label)


def scene_rule(src):
    """取 .scene 那条 CSS 规则的花括号内容（判断层叠上下文有没有被显式写死）。"""
    m = re.search(r"\.scene \{([^}]*)\}", src)
    return m.group(1) if m else ""


def boundary_shape(path, t_start, dur_s=DISSOLVE_S):
    """量一次换页：返回 (跨度, 前平台, 后平台, 溶解窗口里落在两端**中间**的帧数) 或 None。

    None 有两种：几何不够量（窗口越界），以及**两张图一样亮** —— 跨度 <8 级时渐变与硬切
    在均亮度上分不开，那种换页不该判，判了就是拿一条测不出来的东西冒充断言。
    """
    got = np.asarray(FLK.frame_luma(path), dtype=float)
    lo = int(round(t_start * FPS))
    hi = int(round((t_start + dur_s) * FPS))
    b = got[max(0, int(round((t_start - 0.30) * FPS))):max(0, int(round((t_start - 0.06) * FPS)))]
    a = got[int(round((t_start + 0.85) * FPS)):int(round((t_start + 1.30) * FPS))]
    if len(b) < 3 or len(a) < 3 or hi >= len(got):
        return None
    before, after = float(np.median(b)), float(np.median(a))
    span = abs(after - before)
    if span < 8.0:
        return None
    mid = got[lo:min(hi + 1, len(got))]
    between = int(np.sum((np.abs(mid - before) >= 2.0) & (np.abs(mid - after) >= 2.0)))
    return span, before, after, between


def synth_clip(out_path, mode):
    """造一段只换亮度的 30fps 片段：step = 一帧硬切，ramp = 0.72s 渐变。
    给 boundary_shape 做**反空断言**：真像素那一段可能因为盘上没产物而整段跳过，
    而这两段永远在，量到的必须分别是「0 帧中间」和「≥4 帧中间」，否则 H 是在自我证明。

    两段都做满 2.5s：boundary_shape 要在切点**两侧**各取一段平台（前 [−0.30,−0.06)、
    后 [+0.85,+1.30)），拿 0.9+0.72=1.62s 的短片做控制段，后平台直接越界，于是它
    返回 None —— 2026-10-05 第一版就是这么"控制段自己失效"的。"""
    import subprocess
    if mode == "step":
        cmd = ["ffmpeg", "-y", "-v", "error",
               "-f", "lavfi", "-i", "color=c=0xaaaaaa:s=720x960:r=30:d=0.9",
               "-f", "lavfi", "-i", "color=c=0x282828:s=720x960:r=30:d=1.6",
               "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p[v]",
               "-map", "[v]", "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", out_path]
    else:
        cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
               "-i", "color=c=0xaaaaaa:s=720x960:r=30:d=2.5",
               "-vf", "fade=t=out:st=0.9:d=0.72:c=0x282828,format=yuv420p",
               "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", out_path]
    subprocess.run(cmd, check=True, capture_output=True)


def main():
    # ---- A 实测《人间草木》底图 + 旧形状：判据④必须红（不相干/未修的产物跑红）----
    print("A 旧形状 · 实测《人间草木》两张钩子底图 59.0 / 60.2")
    old_rb = dissolve_trace(59.0, 60.2, fade_out_bottom=True)
    ex_a = excursions(old_rb)
    check(len(ex_a) >= 1, f"判据④抓到 {len(ex_a)} 处去了又回（中点最低 {old_rb.min():.1f}，"
                          f"两端 59.0/60.2）")
    # 模型必须对得上量出来的像素，否则整套断言是在验一个凭空想的公式。
    # 但不钉在某一个 hook.mp4 上：那文件每次重渲都会被换掉（本仓库的中间产物不是归档）。
    # 改成扫盘上现存的钩子段，任何一段还带这个凹陷的，都拿来对账；全部修完就打印跳过，
    # 历史实测值仍留在上面的 docstring 里（《人间草木》59.0/60.2 → 最低 45.1，模型 44.8）。
    import glob
    hits = found = 0
    for hp in sorted(glob.glob(os.path.join(ROOT, "tmp", "preview-book-*", "hook", "renders", "hook.mp4"))):
        try:
            got = np.asarray(FLK.frame_luma(hp), dtype=float)
        except Exception:
            continue
        for (fr, a0, a1, t0, t1) in excursions(got):
            found += 1
            # flickers() 的 fr 是**第二条腿**的起点，也就是谷底；下坡在 [fr−t0·fps, fr]，
            # 上坡在 [fr, fr+t1·fps]，两端的平台各取腿起点外一帧。
            b_i = max(0, fr - int(round(t0 * FPS)) - 1)
            a_i = min(len(got) - 1, fr + int(round(t1 * FPS)) + 1)
            before, after = float(got[b_i]), float(got[a_i])
            depth = min(before, after) - float(got[fr:min(len(got), fr + 3)].min())
            if abs(depth - 0.25 * min(before, after)) <= 2.5 and depth >= 8.0:
                hits += 1
            print(f"  · 实测 {os.path.relpath(hp, ROOT)} t={fr / FPS:.2f}s 凹 {depth:.1f} 级"
                  f"（两端 {before:.1f}/{after:.1f}，25%×较暗端 {0.25 * min(before, after):.1f}"
                  f"{'，对上机理' if abs(depth - 0.25 * min(before, after)) <= 2.5 else '，另有原因：推镜/换页'}）")
    check(found == 0 or hits >= 1,
          f"盘上现存钩子段 {found} 处凹陷，其中 {hits} 处与「25%×下层」对得上"
          + ("" if found else "（十期重渲完后这一段会是 0：形状已从共享层摘掉，属预期）"))

    # ---- B 同一对底图 + 新形状：判据④绿、中点不落坑 ----
    print("B 新形状 · 同一对底图（退场层不淡出）")
    new_rb = dissolve_trace(59.0, 60.2, fade_out_bottom=False)
    ex_b = excursions(new_rb)
    check(len(ex_b) == 0, f"判据④绿：{len(ex_b)} 处")
    check(new_rb.min() >= min(59.0, 60.2) - 0.05,
          f"中点 {new_rb.min():.1f} 不低于两端较暗的 59.0（真溶解的凸组合性质）")
    check(new_rb.max() >= old_rb.max() - 1e-9,
          f"峰值不降：新 {new_rb.max():.1f} ≥ 旧 {old_rb.max():.1f}（不是靠压暗过门）")

    # ---- C 暗底图对照：旧形状本来就不红 ----
    print("C 旧形状 · 实测《东京八平米》那一对 46.7 / 30.0")
    ex_c = excursions(dissolve_trace(30.0, 46.7, fade_out_bottom=True))
    check(len(ex_c) == 0, f"暗底图本来就绿（{len(ex_c)} 处）——判据④不是绝对天花板，"
                          f"这次改动也不会把已经绿的期数判红")

    # ---- D 机理：凹陷深度 = f(1−f)·下层，最大 25%·下层，所以它只跟底图有多亮有关 ----
    print("D 机理 · 旧形状比凸组合少 f(1−f)·下层（f=0.5 处 25%·下层）")
    for (b, t) in [(59.0, 60.2), (68.0, 60.6), (30.0, 46.7), (118.0, 120.0), (0.0, 174.2)]:
        old = dissolve_trace(b, t, fade_out_bottom=True)
        lin = xfade_trace(b, t, ease=sine_in_out)
        deficit = lin - old
        check(abs(deficit.max() - 0.25 * b) < 0.35 * b / float(len(old)) + 0.35,
              f"底 {b}/{t}：最大凹陷 {deficit.max():.1f} == 25%×下层 {0.25 * b:.1f}")
        check(old.min() < min(b, t) if b > 0 else True,
              f"底 {b}/{t}：旧形状中点 {old.min():.1f} 掉到两端较暗值 {min(b, t):.1f} 之下"
              if b > 0 else f"底 {b}/{t}：下层全黑时不凹（这就是片头黑场能被溶解盖住的原因）")
    check(dissolve_trace(118.0, 120.0, fade_out_bottom=False).min() >= 118.0 - 1e-9,
          "更亮的一对在新形状里同样不凹——不是「压暗才绿」")

    # ---- E 新形状 == 凸组合：与正文切点的 ffmpeg xfade 同一族 ----
    print("E 新形状 == 凸组合（正文 xfade 的定义），逐帧不越两端")
    for (b, t) in [(59.0, 60.2), (30.0, 46.7), (118.0, 120.0), (0.0, 174.2), (74.0, 41.0)]:
        new = dissolve_trace(b, t, fade_out_bottom=False)
        lin = xfade_trace(b, t, ease=sine_in_out)
        d = float(np.abs(new - lin).max())
        check(d < 1e-9, f"底图 {b}/{t}：与同一 alpha 的凸组合最大差 {d:.1e}")
        check(new.min() >= min(b, t) - 1e-9 and new.max() <= max(b, t) + 1e-9,
              f"底图 {b}/{t}：全程落在 [{min(b, t):.1f}, {max(b, t):.1f}] 之内")

    # ---- F 时间轴不动 ----
    print("F 帧数与首末帧不变（不是把溶解拉长摊开）")
    check(len(old_rb) == len(new_rb) == len(xfade_trace(59.0, 60.2)),
          f"三个形状都是 {len(new_rb)} 帧 / {DISSOLVE_S}s")
    check(abs(new_rb[0] - old_rb[0]) < 1e-9 and abs(new_rb[-1] - old_rb[-1]) < 1e-9,
          f"首末帧相同：{new_rb[0]:.1f}->{new_rb[-1]:.1f}")

    # ---- G 静态守源码 ----
    print("G create-episode-preview.mjs 的合成形状")
    src_path = os.path.join(SCRIPTS, "create-episode-preview.mjs")
    src = open(src_path, encoding="utf-8").read()
    loop = re.search(r"for \(var k = 1; k <= sceneCount; k\+\+\) \{(.*?)\n      \}", src, re.S)
    check(loop is not None, "还在按场景循环搭时间轴（结构没被换掉，否则下面三条是空断言）")
    body = loop.group(1) if loop else ""
    check(re.search(r'tl\.fromTo\("\.s" \+ k, \{ opacity: 0 \}, \{ opacity: 1, duration: 0\.72', body)
          is not None, "进场层仍然 0.72s 淡入（不是把溶解删掉来洗绿）")
    check(re.search(r'tl\.to\("\.s" \+ \(k - 1\), \{ opacity: 0', src) is None,
          "全文件不存在 .s(k−1) 的 opacity 淡出（旧形状复发即红）")
    check(re.search(r'tl\.to\("\.bridge", \{ opacity: 0', src) is None,
          "全文件不存在 .bridge 的 opacity 淡出（同一处机理）")
    # ★ 只删掉退场层的淡出**不够**：那样会从一个「下探的溶解」变成「一帧硬切」。
    # .photo 是 position:absolute + z-index:1，而 .scene 不写 z-index 时只有 opacity<1
    # 才生成层叠上下文。退场层不再淡出 → 它 opacity 恒为 1 → 不是层叠上下文 → 它的
    # .photo 逃逸到 root 的正 z 组；进场层（opacity<1）整个是 z=0 组的单元，被压在下面，
    # 直到 opacity 正好 ==1 才不再是层叠上下文、自己的 .photo 才按 DOM 顺序盖上去。
    # 实测 2026-10-04《丧钟为谁而鸣》hook.mp4：溶解窗口 3.73~4.45s 画面一动不动（60.7），
    # 4.47s 单帧跳到 39.9 —— 判据③红 20.8 > 17.7。所以 .scene 必须显式 z-index。
    for name in sorted(n for n in os.listdir(SCRIPTS)
                       if re.match(r"^create-episode-preview.*\.mjs$", n)):
        gsrc = open(os.path.join(SCRIPTS, name), encoding="utf-8").read()
        rule = re.search(r"\.scene \{([^}]*)\}", gsrc)
        check(rule is not None and "z-index" in rule.group(1),
              f"{name} 的 .scene 规则里有显式 z-index（缺了就是一帧硬切）")

    # ---- H 真像素：换页必须是「摊在时间上的溶解」，不许是 1 帧硬切 ----
    # H1 先拿两段合成片段证明 boundary_shape 自己不是空断言（硬切必须量到 0 帧中间、
    # 渐变必须量到 ≥4 帧），否则 H2 只要盘上没产物就"永远绿"。
    print("H1 boundary_shape 的反空断言（合成硬切 vs 合成渐变）")
    tmp_dir = os.path.join(ROOT, "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    for mode, expect_ramp in (("step", False), ("ramp", True)):
        p = os.path.join(tmp_dir, f"dissolve-dip-control-{mode}.mp4")
        try:
            synth_clip(p, mode)
            shape = boundary_shape(p, 0.9)
        except Exception as e:  # ffmpeg 缺失等环境问题不该把整条测试判死
            shape = None
            print(f"  · 合成 {mode} 失败：{e}")
        if shape is None:
            check(False, f"合成 {mode} 片段没能给出可量的换页（控制段自己失效）")
            continue
        span, before, after, between = shape
        check((between >= 4) == expect_ramp,
              f"{mode}：跨度 {span:.1f} 级（{before:.1f}→{after:.1f}）量到中间帧 {between} 帧 —— "
              f"{'渐变，判为溶解' if between >= 4 else '一帧到底，判为硬切'}")
        try:
            os.remove(p)
        except OSError:
            pass

    # H2 再看真实产物：几何从各期 index.html 里读（sceneCount / safeDur / 是否桥接起幅），
    # 与生成器同一个公式。只判**已经带上 ② 的层叠上下文修法**的构图——历史产物带硬切是
    # 已知的、只能靠重渲退掉（跟片头闪屏那 113 期一个道理），拿它们点灯只会把"待重渲"
    # 混成"共享层有缺陷"。
    print("H2 真像素 · 盘上现存钩子段的每一次换页")
    checked = stepped = legacy = 0
    for html in sorted(glob.glob(os.path.join(ROOT, "tmp", "preview-book-*", "hook", "index.html"))):
        hp = os.path.join(os.path.dirname(html), "renders", "hook.mp4")
        if not os.path.exists(hp):
            continue
        hsrc = open(html, encoding="utf-8").read()
        if "z-index" not in scene_rule(hsrc):
            legacy += 1
            continue
        m_cnt = re.search(r"var sceneCount = (\d+);", hsrc)
        m_dur = re.search(r"var safeDur = ([\d.]+);", hsrc)
        if not (m_cnt and m_dur):
            continue
        count, safe_dur = int(m_cnt.group(1)), float(m_dur.group(1))
        bridge = 'var sceneStarts = [0.32' in hsrc
        starts = ([0.32] if bridge else [0.0]) + [
            round(safe_dur * k / count, 2) for k in range(1, count)]
        for idx in range(1, len(starts)):
            shape = boundary_shape(hp, starts[idx])
            if shape is None:
                continue
            span, before, after, between = shape
            checked += 1
            if between < 4:
                stepped += 1
            print(f"  · {os.path.relpath(hp, ROOT)} t={starts[idx]:.2f}s 跨度 {span:.1f} 级"
                  f"（{before:.1f}→{after:.1f}），0.72s 窗口里落在两端的**中间**的帧 {between} 帧"
                  + ("  ← 硬切" if between < 4 else ""))
    check(checked == 0 or stepped == 0,
          f"新构图钩子段 {checked} 次可判换页中 {stepped} 次是 1 帧硬切"
          + ("" if checked else f"（盘上 {legacy} 份还是旧构图的产物，改完必须重渲才算；这一段暂无可判产物）"))
    if checked == 0:
        print(f"  · 注意：盘上没有新构图的钩子段产物（旧构图 {legacy} 份），H2 这一轮只由 H1 兜住非空断言")

    if fails:
        for line in fails:
            print(f"❌ {line}")
        print(f"❌ 场景交叉溶解合成形状回归：{len(fails)} 条不符")
        sys.exit(1)
    print("✅ 场景交叉溶解合成形状回归：黑底双层反向淡入淡出会掉 25%·下层（实测对得上），"
          "退场层保持不透明后成为凸组合、与正文 xfade 同族、判据④绿、峰值不降、0.72s 时长与时间轴不动")
    sys.exit(0)


if __name__ == "__main__":
    main()
