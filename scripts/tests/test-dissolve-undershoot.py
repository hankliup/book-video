#!/usr/bin/env python3
"""判据⑥「漏底」回归：溶解必须是凸组合，越带即黑底从两层之间漏进来。

这条判据的**本体**是合成形状，所以断言全部打在 ffmpeg 生成的片色对照上，而不是某一期交付片
——真片只有一份产物，它绿了可能只是判据没醒（这一课在 2026-10-04 那条退役的旧判据⑤上已经交过
学费）。每个对照都是**跑门禁自己的 main()**（子进程 `check-luminance-flicker.py --opening-only`），
不在这里重新实现一遍比较：常量当天可能被改过，抄一份就会跟着漂移（同 test-flicker-rules.py 的
理由）。

凸组合 v = f·B + (1−f)·A 的权重恒为 1，永远越不出两端电平夹出的那条带；退场层被淡了第二次
就变成 v = f·B + (1−f)²·A，它越出带宽的最深点是
    depth = B²/(4A)，带宽 = A−B
两者在**同比例压暗**下都乘同一个 k，所以 depth/带宽 是不变量 —— 这就是判据⑥的深度地板
取 max(绝对噪声地板, 比例×带宽) 的原因：只写绝对数的话，把两张底图一起调暗到谷底只剩 2 级
就能洗绿，而那是明令禁止的修法（never-fix-by-dimming）。

六个用例（都是量出来的，不是推算）：
  A 凸组合溶解            → ⑥ 0 处，整道门绿。这是「正确写法」的物证。
  B 退场层被淡了第二次     → ⑥ 1 处红，且**只有**⑥红（②不眨眼、③摊得开、④无反向短腿、
                            ⑤平色板内容 MAD≈0）。这一对 A/B 是判据⑥的正反对照：两份片子
                            时长、电平、缓动全同，只差退场层那一次多余的 fade。
  C 把 B 同比压暗 ×0.4     → ⑥ 仍然红（depth/带宽 不变）。这一条就是「不许靠压暗过门」的断言。
  D 再把 C 压暗到 ② 的量级 → ②眨眼接手判红，判据不该也不需要在此放行。
  E 凸组合 + 字幕墨迹在中途入场 → 默认取证窗口（字幕以上）0 处且整道门绿；`--band-rows 0:48`
                            整幅口径判红。这一对钉住的是**取证口径**：字幕是独立相加的一层 gain，
                            不是两层照片的凸组合，拿整幅均值判它就是《丧钟为谁而鸣》
                            「上冲 3.2 级」和《一个叫欧维的男人决定去死》「下探 3.9 级」两处
                            假阳的来由。修法是把窗口挪对，不是把 UNDERSHOOT_TOL 抬到 5。
                            两条正对照保证这个绿不是空转：A 用例量「换页真的走完了、每一帧都
                            夹在两侧电平之间」，E 用例量「字幕真的上过屏（整幅比取证窗口高
                            ≥10 级）」——两趟 alpha 在时间上一重叠，净结果就是没上过屏。
  F 静态守卫               → 门禁必须**一次解码**拿到三个逐帧量（frame_curves），判决必须调
                            dissolve_excursions，且比例地板 UNDERSHOOT_REL 不能被删成 0。

窗口口径注意：tmp/ 下的对照片段不在 episodes/ 里，门禁认不出三段结构，会退到保守口径
（窗口 13s、下限从前 FADE_IN_GRACE=0.75s 起判）。所以片头那层平台必须留够 2s，否则 0.75s 的
宽限会把第一个电平切成不足 LEVEL_MIN_FRAMES 的碎片，电平配不成对、判据直接失明——2026-10-05
实测就踩过一次（3s 版双层淡出对照在门禁下报 0 处）。

用法：python3 scripts/tests/test-dissolve-undershoot.py
退出码：0 全部通过；1 有用例失败。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GATE = os.path.join(ROOT, "scripts", "check-luminance-flicker.py")
OUT_DIR = os.path.join(ROOT, "tmp", "dissolve-undershoot-control")

T0 = 2.0          # 溶解开始时刻（秒）：留 2s 平台给第一个电平，见 docstring 的窗口口径
# 溶解时长取**真钩子换页的实测长度 0.72s**，不是为了"让⑥好看"：把夹具拉长到 1.6s，溶解谷底
# 自己就会平够 15 帧、极差 ≤3 级，被 held_levels 登记成第三个电平，判据⑥当场在自己的夹具上
# 失明（2026-10-05 实测：1.6s 版 double 报 0 处）。这同时是这条判据的**已知盲区**——慢于约 1s
# 的下探再爬回来看不见；而那种形状本仓库没有任何渲染路径会画（正文切点 0.64s、钩子换页 0.72s、
# 段尾淡出 0.4s），且判据④对它的两条腿（都 >0.35s）也故意放行：一次慢换气不是频闪。
DUR = 0.72
TOTAL = T0 + DUR + 2.0


def hexg(v):
    """把 0~255 的**亮度**写成 R=G=B 的十六进制色值（Y=0.299R+0.587G+0.114B=x）。"""
    x = max(0, min(255, int(round(v))))
    return f"0x{x:02X}{x:02X}{x:02X}"


def build(name, a_luma, b_luma, double=False, caption=False):
    """生成一段 720x960@30 的片色对照：黑底 → A（退场层）→ B（进场层，线性淡入）。

    double=True 时 A  additionally 淡出 —— 这就是被判据⑥抓住的那个形状（f·B + (1−f)²·A）。
    caption=True 时在 y=700~840（探针的行 35~42）叠一层白色字幕墨迹，淡入淡出走同样的线性。
    """
    path = os.path.join(OUT_DIR, f"{name}.mp4")
    inputs = [
        "-f", "lavfi", "-i", f"color=c=black:s=720x960:r=30:d={TOTAL:.3f}",
        "-f", "lavfi", "-i", f"color=c={hexg(a_luma)}:s=720x960:r=30:d={TOTAL:.3f}",
        "-f", "lavfi", "-i", f"color=c={hexg(b_luma)}:s=720x960:r=30:d={TOTAL:.3f}",
    ]
    fade_out = f",fade=t=out:st={T0}:d={DUR}:alpha=1" if double else ""
    fg = [
        f"[1:v]format=rgba{fade_out}[a0]",
        f"[2:v]format=rgba,fade=t=in:st={T0}:d={DUR}:alpha=1[b0]",
        "[0:v][a0]overlay=0:0[m]",
        "[m][b0]overlay=0:0[m2]",
    ]
    label = "m2"
    if caption:
        # 字幕层：y=700 起、高 140px（行 35~42，正落在取证窗口之外），在溶解中段淡入、
        # **溶解收完之后**才淡出——两趟 alpha 一旦在时间上重叠，净结果就是一条近 0 的曲线，
        # 字幕根本没上过屏（夹具自己假绿，2026-10-05 实测踩过：0.72s 版 E 用例两处都读 0 处）。
        inputs += ["-f", "lavfi", "-i",
                   f"color=c=white:s=720x140:r=30:d={TOTAL:.3f}"]
        fg += [f"[3:v]format=rgba,fade=t=in:st={T0 + 0.15:.3f}:d=0.3:alpha=1,"
               f"fade=t=out:st={T0 + DUR + 0.05:.3f}:d=0.3:alpha=1[cap]",
               "[m2][cap]overlay=0:700[v]"]
        label = "v"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + inputs + \
          ["-filter_complex", ";".join(fg), "-map", f"[{label}]",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"对照片段生成失败 {name}：{r.stderr[-500:]}")
    return path


_GATE = None


def gate_module():
    """把门禁本体当模块拿一次（正对照要用它的 frame_curves 量"字幕真的上过屏"）。"""
    global _GATE
    if _GATE is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("flicker_gate", GATE)
        _GATE = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_GATE)
    return _GATE


def curves(path, rows=None):
    g = gate_module()
    return g.frame_curves(path, None, rows or g.LUMA_BAND_ROWS)[2]


def run_gate(path, extra=None):
    cmd = ["python3", GATE, path, "--opening-only"] + (extra or [])
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    out = r.stdout
    m = re.search(r"判据⑥漏底：越带 (\d+) 处", out)
    exc = int(m.group(1)) if m else None
    depth = None
    # 深度从**判决行**读（那里门禁自己写了「最深 下探 X 级」），而不是从报告行里猜格式
    dm = re.search(r"漏底：\d+ 处越带（最深 (?:下探|上冲) ([\d.]+) 级", out)
    if dm:
        depth = float(dm.group(1))
    # 每条红都单独占一行（`    ❌ 眨眼：…`），所以按行认名字，不去拆那句用「；」连接的总结——
    # 判据正文里本来就有分号，拆它会跟着文案漂移。
    names = [kind for kind in ("眨眼", "摊不开", "来回", "换页", "漏底", "旧频闪判据")
             if re.search(rf"❌ {kind}：", out)]
    slam = re.search(r"❌ 亮度闪屏：(\d+) 帧单帧亮度跳变超过", out)
    if slam:
        names.append("闸刀")
    return dict(exc=exc, depth=depth, names=names, exit=r.returncode, out=out)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    fails = []

    def check(label, want, got, detail=""):
        ok = want == got
        print(f"  {'✅' if ok else '❌'} {label}：期望 {want}，实际 {got}{detail}")
        if not ok:
            fails.append(label)

    # A/B 这对用同一组电平：A=190（退场层）→ B=95（进场层），双层淡出的理论越带深度
    # depth = B²/(4A) = 95²/760 = 11.9 级，带宽 95 → 地板 max(3, 0.08×95)=7.6 → 必红
    print("A 凸组合溶解（正确写法）")
    ca = build("convex", 190, 95)
    ra = run_gate(ca)
    # 正对照：换页必须真的发生（曲线确实从 190 走到 95），否则 A 的绿只是"画面没动"
    ca_curve = curves(ca, (0, gate_module().PROBE_H))
    d0, d1 = int(round(T0 * 30)), int(round((T0 + DUR) * 30))
    check("A 确实走完一次 190→95 的换页（首尾差 ≥80 级）", True,
          float(ca_curve[d0 - 10]) - float(ca_curve[d1 + 10]) >= 80.0,
          f"（{float(ca_curve[d0 - 10]):.1f} → {float(ca_curve[d1 + 10]):.1f}）")
    check("凸组合中间每一帧都夹在两侧电平之间（这条绿不是靠容差放宽来的）", True,
          float(ca_curve[d0:d1].min()) >= 95.0 - 3.0
          and float(ca_curve[d0:d1].max()) <= 190.0 + 3.0,
          f"（段内 {float(ca_curve[d0:d1].min()):.1f}~{float(ca_curve[d0:d1].max()):.1f}）")
    check("⑥越带处数", 0, ra["exc"])
    check("整道门绿", [], ra["names"])

    print("B 退场层被淡了第二次（同一个溶解，只多一次 fade）")
    cb = build("double", 190, 95, double=True)
    rb = run_gate(cb)
    check("⑥越带处数", 1, rb["exc"])
    check("只有⑥红（②③④⑤都不许抢）", ["漏底"], rb["names"])
    if rb["depth"] is not None:
        check("深度落在模型 11.9±3 内", True, 8.9 <= rb["depth"] <= 14.9,
              f"（实测 {rb['depth']:.1f}）")
    else:
        check("深度可读", "有", "无")

    print("C 把 B 同比压暗 ×0.4（A=76/B=38）——压暗不能洗绿")
    cc = build("double-dim", 76, 38, double=True)
    rc = run_gate(cc)
    check("⑥仍然红", 1, rc["exc"])
    check("红的还是只有⑥", ["漏底"], rc["names"])
    if rb["depth"] and rc["depth"]:
        # depth/带宽 是不变量：两份片子的比值必须接近
        ratio_b = rb["depth"] / (190 - 95)
        ratio_c = rc["depth"] / (76 - 38)
        check("depth/带宽 在压暗下不变（|Δ|<2%）", True,
              abs(ratio_b - ratio_c) < 0.02,
              f"（亮 {ratio_b * 100:.1f}% vs 暗 {ratio_c * 100:.1f}%）")

    print("D 继续压暗到 A=38/B=19 —— 该由②眨眼接手，不是⑥放行")
    cd = build("double-dark", 38, 19, double=True)
    rd = run_gate(cd)
    check("②眨眼判红", True, "眨眼" in rd["names"])

    print("E 凸组合 + 中途入场的字幕墨迹 —— 取证窗口决定判决")
    ce = build("convex-caption", 90, 120, caption=True)
    # 正对照：字幕必须**真的上过屏**，否则"默认窗口 0 处"只是夹具没画出东西的空绿
    band_only = curves(ce)
    whole = curves(ce, (0, gate_module().PROBE_H))
    check("字幕确实上过屏（整幅比取证窗口高 ≥10 级）", True, whole.max() - band_only.max() >= 10.0,
          f"（整幅峰值 {whole.max():.1f} vs 取证窗口峰值 {band_only.max():.1f}）")
    re_ = run_gate(ce)
    re_whole = run_gate(ce, ["--band-rows", "0:48"])
    check("默认窗口（字幕以上）0 处", 0, re_["exc"])
    check("整幅口径必须判红（否则这条断言是空的）", 1, re_whole["exc"])
    check("默认窗口那一版整道门绿（取证口径不是把红藏起来）", [], re_["names"])
    print("   ↑ 整幅口径把字幕入场报成越带，正是《丧钟为谁而鸣》3.2 级 / 《欧维》3.9 级"
          "两处假阳的机制；窗口挪到字幕以上就消失，而 B/C 的真缺陷不受影响。")

    print("F 静态守卫：一次解码、真的调用、比例地板不许删")
    gate_src = open(GATE, encoding="utf-8").read()
    for token, why in [
        ("luma, mad, band = frame_curves(",
         "三个逐帧量必须出自同一次解码——分两次解码就是两份会互相矛盾的证词"),
        ("def dissolve_excursions(", "判据⑥的实现被删了，这条门就成了空转"),
        ("dissolve_excursions(curves[s:e]", "⑥必须判在**被判片段**上、量在字幕以上的取证窗口，"
         "不是整窗也不是整幅均值（否则回滚后的片头砸黑又会把它判红，而唯一修法又变成改片头）"),
        ("measure(luma, mad, band)", "②~⑥ 都走同一个片段聚合器，判决范围只有一份口径"),
        ("UNDERSHOOT_REL = 0.08", "比例地板被删成绝对数，压暗就能洗绿"),
        ("floor = max(tol, rel * (hi - lo))", "深度地板不再是「绝对噪声 + 相对比例」两者取大"),
        ("EXCURSION_MAX = 0", "凸组合一次都不该越带，上限被抬就不是这条判据了"),
        ("held_levels(curve, range_tol, level_frames)",
         "电平仍按「整段极差 + 够长」两条切——只看逐帧容差会把溶解谷底登记成电平，"
         "一次回头被拆成两条单调腿，判据当场失效（2026-10-05《李尔王》实测踩过）"),
    ]:
        check(f"门禁仍在 {token[:34]}…", True, token in gate_src, f" —— {why}")
    if "luma, mad = frame_probe(args.final" in gate_src:
        check("main() 不许退回只拿两个量的 frame_probe", True, False,
              "（否则⑥读不到取证窗口）")

    print()
    if fails:
        print(f"❌ 判据⑥漏底回归：{len(fails)} 项失败 —— {' / '.join(fails)}")
        sys.exit(1)
    print("✅ 判据⑥漏底回归：凸组合绿、双层淡出红、压暗不翻面、字幕窗口两处对照、静态守卫齐")


if __name__ == "__main__":
    main()
