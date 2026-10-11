#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第十二道门：正文段那一行常驻声明「内容为书评与感悟　素材来源于网络」真的在像素里。

规则本身很轻——底部一行小灰字。它需要一道门的原因是这个仓库已经反复量到同一件事：
**声明在源文件里，不等于声明在交付像素里**。《恶意》2026-10-01 的 `showBookMark: false`
早就写进生成器，成片拼的却是改标志之前渲的 `hook.mp4`，书名层在钩子段上活了 6.77 秒，
七道门全绿；《偏跟山过不去》2026-10-03 重锁了一张钩子图，取图脚本打印「完成 1/1」而
什么都没写，重渲把**旧照片**原样搬回屏幕，八道门全绿。所以这道门不看 CSS、不看
`disclaimer.png` 存不存在，只看成片：

  证人 A（有 `body-bg.mp4` 时，主证）：同一时刻的成片帧减正文背景帧，得到画面上
  「长出来」的墨迹，与 `lib/disclaimer.py` 现算的预期墨迹在声明带内作 IoU。
  这是 `qc-caption-alignment.py` 用同一套取样口径验字幕的写法，差别只在带的位置：
  声明带被 lib 钉在 y 850 以下，正好落在字幕取样带 (680..850) 之外，
  所以这里的墨迹只可能是这一行灰字，不可能是字幕。
  墨迹在带内却对不上预期 = 文案或几何与 lib 不是一份（改了 lib 没重渲）。
  顺带用 core 墨迹反推字的实际亮度，落在 `ink_luma_range` 之外判红——「偏暗灰」被
  人换成白字保可读性，是这条规则最容易被悄悄改掉的性质。

  证人 B（没有背景中间产物、或核对钩子段那半边时的备用证）：把正文窗内相隔远的若干帧
  按像素求时间标准差，再比「lib 那行字的墨迹那片」和「带内其余像素」。底下是一张在缓推
  的照片（两帧之间带内必然有位移），静止层让墨迹那片的 std 塌到近乎零，而带内其余仍是
  背景自己的 std。用比值而不是「两帧差没差」，是因为半透明叠层本身会漏下一部分背景运动
  （alpha 0.9 = 10% 的 bg 透过去）：按二值 changed 图判，背景摆得够大时连干净画面都会
  被当成有层，摆得不够大时有层的画面又会被当成干净——比值把「漏多少」归一掉了，
  只要带内背景真在动，两种形状都分得开。
  这个证人只能证明「有一层静止墨迹」，证明不了文案，所以它单独成立时只放行、不判红文案。

该有而没有 → 红；钩子段出现 → 红（常驻声明属于正文段，理由同《书名》层不进钩子）；
盘上 `disclaimer.png` 与 lib 现算的墨迹对不上 → 红（改过文案没重渲）。

存量不炸：只有当该期的渲染中间产物里已经有 `disclaimer.png`（= 由加了这层的渲染器出片）
才判红，否则打印 ⚠️ 无从证明、退出 0。和 Pexels 溯源标记同一套「只在已经有标记的目录里
要求标记」的爆炸半径控制。`--require` 强制判（回归测试用它证明这道门能看见缺失）。

用法：
  python3 scripts/check-disclaimer-overlay.py <书名>
  python3 scripts/check-disclaimer-overlay.py <书名> --final x.mp4 --tmp DIR \\
      --body-at 9.77 --hook-dur 6.77 --require
退出码：0 通过或无从证明；1 至少一项判红。
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np
from PIL import Image  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))
import disclaimer as D  # noqa: E402

W, H = 720, 960
INK_THRESH = 24          # 帧差算墨迹的阈值：与 qc-caption-alignment.py 的 28 同量级
MOTION_FLOOR = 5.0       # 证人 B 的前置：带内「墨迹以外」的时间 std 至少这么多，否则画面本来就静止
STATIC_RATIO = 0.40      # 墨迹那片 std / 其余 std 低于这个比，才算有一层静止的东西压在上面
EDGE_GRACE = 0.8         # 正文起幅让开的秒数：切点后第一帧可能还带着片头定格


def ff_probe_dur(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return round(float(r.stdout.strip()), 3)
    except ValueError:
        return 0.0


def grab(path, t, out):
    """抽一帧。必须带 -y：同名输出已存在时 ffmpeg 会弹「Overwrite?」，非交互下放弃写文件，
    留下的上一轮旧帧会把缺失报成通过（qc-caption-alignment.py 同一条注释）。"""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-ss", f"{t:.4f}", "-i", path, "-frames:v", "1",
                        "-pix_fmt", "rgb24", out],
                       capture_output=True, stdin=subprocess.DEVNULL)
    if r.returncode != 0 or not os.path.exists(out):
        raise SystemExit(f"取帧失败 {path}@{t}: {r.stderr.decode()[-300:]}")
    return np.asarray(Image.open(out).convert("RGB"), dtype=np.int16)


def iou(a, b):
    union = int((a | b).sum())
    return 0.0 if union == 0 else int((a & b).sum()) / union


def to_luma(rgb):
    """Rec.601 灰度：这道门只在自己内部比静止性，取一个加权一致的量就行，
    不与 check-luminance-flicker.py 的 36x48 探针共用口径（那个探针量整帧均值）。"""
    return np.dot(rgb.astype(np.float64), [0.299, 0.587, 0.114])


def find_tmp_dir(ep_name, explicit):
    cands = [explicit] if explicit else []
    if os.environ.get("FALLBACK_TMP"):
        cands.append(os.environ["FALLBACK_TMP"])
    if ep_name:
        cands += [os.path.join(ROOT, "tmp", f"{ep_name}_tpl"), f"/tmp/{ep_name}_tpl"]
    for c in cands:
        if c and os.path.isdir(c):
            return c
    return None


def body_start_default(ep_dir, intro_trim):
    """正文在成片时间轴上的起点 = 钩子配音长度 + 模板裁切段。

    与 check-episode-delivery.py 同一个信号（有 script-hook.csv 才算有钩子段），
    两边不一致时这道门会跑到钩子段里去验正文，或者反过来。
    """
    hook = hook_dur_default(ep_dir)
    return round(hook + intro_trim, 3), hook


def hook_dur_default(ep_dir):
    if not ep_dir or not os.path.exists(os.path.join(ep_dir, "script-hook.csv")):
        return 0.0
    for name in ("hook-voiceover-story.mp3", "hook-voiceover.mp3", "钩子配音.mp3"):
        p = os.path.join(ep_dir, "audio", name)
        if os.path.exists(p):
            return ff_probe_dur(p)
    return 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episode", nargs="?", default="")
    ap.add_argument("--final", default="")
    ap.add_argument("--tmp", default="")
    ap.add_argument("--body-at", type=float, default=-1.0,
                    help="正文段在成片时间轴上的起点秒；默认按钩子配音 + 片头裁切点算")
    ap.add_argument("--hook-dur", type=float, default=-1.0)
    ap.add_argument("--samples", type=int, default=8)
    ap.add_argument("--require", action="store_true",
                    help="即使中间产物里没有 disclaimer.png 也强制判（回归测试用）")
    args = ap.parse_args()

    cfg = D.load()
    geo = D.geometry(cfg, (W, H))
    band = geo["band"]
    exp_full = np.asarray(D.overlay(cfg, (W, H)).convert("RGBA"))[..., 3] > 128
    exp_core = D.core_mask(cfg, (W, H))
    exp_band = exp_full[band[1]:band[3], band[0]:band[2]]
    core_band = exp_core[band[1]:band[3], band[0]:band[2]]
    exp_px = int(exp_band.sum())

    ep_dir = os.path.join(ROOT, "episodes", args.episode) if args.episode else None
    if args.episode and not os.path.isdir(ep_dir):
        print(f"⚠️ 期目录不存在（{ep_dir}）：按外部成片核对，钩子段那半边不做判决")
        ep_dir = None
    final = args.final or (os.path.join(ep_dir, "renders", f"{args.episode}-final.mp4")
                           if ep_dir else "")
    if not final or not os.path.exists(final):
        raise SystemExit(f"找不到成片：{final or '（未给 --final 且期目录下没有 renders）'}")
    tmp = find_tmp_dir(args.episode, args.tmp)
    png = os.path.join(tmp, "disclaimer.png") if tmp else ""

    with open(os.path.join(HERE, "lib", "intro-template.json"), encoding="utf-8") as f:
        intro = json.load(f)
    auto_body_at, hook_dur = body_start_default(ep_dir, float(intro["trim_seconds"]))
    body_at = args.body_at if args.body_at >= 0 else auto_body_at
    if args.hook_dur >= 0:
        hook_dur = args.hook_dur
    final_dur = ff_probe_dur(final)
    body_end = round(final_dur - 0.4, 3)

    print(f"常驻声明门禁  {os.path.relpath(final, ROOT) if final.startswith(ROOT + os.sep) else final}")
    print(f"  文案「{D.display_text(cfg)}」 {geo['font_size']}px / 灰度 {cfg['color_rgba'][:3]}"
          f" / 声明带 y {band[1]}..{band[3]}（预期墨迹 {exp_px}px）")
    print(f"  正文窗 {body_at:.2f}..{body_end:.2f}s"
          + (f"　钩子窗 0..{hook_dur:.2f}s" if hook_dur else "　（无钩子段）"))

    # 爆炸半径：这层是渲染器写进中间产物的，没有这张 png = 该期由加这层之前的渲染器出片。
    demanded = bool(args.require or (png and os.path.exists(png)))
    if not demanded:
        print(f"\n⚠️ 无从证明：{png or '（没有 --tmp）'} 里没有 disclaimer.png，"
              f"这一期由加常驻声明层之前的渲染器出片，不判红（重渲即纳入判决）")
        sys.exit(0)

    reds = []

    # 证人 0：盘上叠层 vs lib 现算墨迹。png 是渲染器要合成进画面的那一层，
    # 它和 lib 对不上就是「改了文案/几何没重渲」——这时像素再怎么对也没意义。
    if not png or not os.path.exists(png):
        # --require 点名要判，可这期连叠层都没画：缺失本身就是结论，不必绕到像素。
        print(f"\n❌ 常驻声明门禁：{png or '--tmp 目录'} 里没有 disclaimer.png，"
              f"而 --require 指定了这一期必须带常驻声明")
        print("   → 渲染器没画这一层：正文段缺常驻声明")
        sys.exit(1)
    disk = np.asarray(Image.open(png).convert("RGBA"))[..., 3] > 128
    disk_band = disk[band[1]:band[3], band[0]:band[2]]
    iou_png = iou(disk_band, exp_band)
    print(f"\n[叠层对账] {os.path.relpath(png, ROOT)} vs lib 现算墨迹：IoU={iou_png:.3f}")
    if iou_png < 0.90:
        reds.append(f"盘上 disclaimer.png 与 lib 文案/几何不符（IoU={iou_png:.3f}）："
                    f"改过 scripts/lib/disclaimer.json 就得重渲")

    times = [round(body_at + EDGE_GRACE + (body_end - body_at - EDGE_GRACE) * k / args.samples, 3)
             for k in range(args.samples)]
    times = [t for t in times if body_at < t < body_end]
    bg = os.path.join(tmp, "body-bg.mp4") if tmp else ""

    hits = []
    lumas = []
    if bg and os.path.exists(bg):
        print(f"\n[证人 A] 成片帧 − 正文背景帧（{os.path.relpath(bg, ROOT)}）在声明带内取墨迹")
        work = os.path.join(tmp, "_disclaimer_qc")
        os.makedirs(work, exist_ok=True)
        for t in times:
            frame = grab(final, t, os.path.join(work, "f.png"))
            bgf = grab(bg, max(0.0, t - body_at), os.path.join(work, "b.png"))
            fr = frame[band[1]:band[3], band[0]:band[2]]
            bb = bgf[band[1]:band[3], band[0]:band[2]]
            ink = (np.abs(fr - bb).max(axis=2) > INK_THRESH)
            v = iou(ink, exp_band)
            core = ink & core_band
            luma = ""
            if int(core.sum()) > 40:
                # 反推字自己的灰度：final = (1−a)·bg + a·text，a 取 lib 声明的 alpha。
                # core_band 筛出来的是 (像素数, 3)，均值沿**像素**轴求，
                # 再交给 to_luma 加权：轴写反了就是 (2047,)·(3,) 的 shape 崩溃。
                a = float(cfg["color_rgba"][3])
                est = (fr[core_band].astype(np.float64)
                       - (1 - a) * bb[core_band].astype(np.float64)) / max(a, 1e-6)
                level = float(to_luma(est.mean(axis=0)))
                lumas.append(level)
                luma = f" 字面亮度={level:.0f}"
            hits.append(v)
            print(f"  {t:8.2f}s  IoU={v:.3f}  墨迹={int(ink.sum()):>5}px{luma}")
        lo, hi = cfg["ink_luma_range"]
        if lumas and not (lo <= np.median(lumas) <= hi):
            reds.append(f"常驻声明的字面亮度中位 {np.median(lumas):.0f} 不在"
                        f"「偏暗灰」区间 [{lo},{hi}]：这条线要退在画面之后，不许换成白字")
    else:
        print(f"\n[证人 A] 无 {bg or '（没有 --tmp）'}：改用证人 B（静止层证据），文案与颜色不作判决")

    # 证人 B：静止层证据。若干帧之间，lib 墨迹那片的时间 std 明显低于带内其余
    # = 有一层不动的东西压在动画面之上。
    def static_layer_evidence(times_, work, tag):
        """按像素求时间 std，比「墨迹那片」与「带内其余」。
        返回 None 表示带内背景本身没动（静止无从判定，不等于「没有层」）。"""
        if len(times_) < 3:
            return None
        crops = []
        for t in times_:
            frame = grab(final, t, os.path.join(work, f"{tag}.png"))
            crops.append(to_luma(frame[band[1]:band[3], band[0]:band[2]]))
        std = np.asarray(crops, dtype=np.float64).std(axis=0)
        outside = float(std[~core_band].mean())
        inside = float(std[core_band].mean())
        if outside < MOTION_FLOOR:
            return None
        return inside, outside, inside <= STATIC_RATIO * outside

    work = os.path.join(tmp or "/tmp", "_disclaimer_qc")
    os.makedirs(work, exist_ok=True)
    ev_body = static_layer_evidence(times, work, "b")
    if ev_body:
        inside, outside, ok = ev_body
        print("\n[证人 B] 静止层证据（时间 std：lib 墨迹那片 / 带内其余）")
        print(f"  {len(times)} 帧  墨迹 std={inside:.2f}  其余 std={outside:.2f}"
              f"  比值={inside / outside:.2f} → {'静止层成立' if ok else '没有静止层'}"
              f"（判红线 ≤{STATIC_RATIO:.2f}）")
    else:
        print("\n[证人 B] 无从判定（带内背景本身没有动，或采样点不足）")

    if not hits:
        # 证人 A 没跑：静止层证据能证明「有一层东西压在正文上」，证明不了文案。
        if not (ev_body and ev_body[2]):
            reds.append("正文窗内找不到静止墨迹证据（证人 A 无背景中间产物、证人 B 也不成立）："
                        "这一行常驻声明没上屏")
        else:
            print("  → 证人 B 成立，但文案与颜色无从核对")

    if hits:
        good = sum(1 for v in hits if v >= cfg["match_min_iou"])
        print(f"\n[正文窗] {good}/{len(hits)} 点墨迹与 lib 预期一致"
              f"（判红线 IoU≥{cfg['match_min_iou']}）")
        if good == 0:
            reds.append(f"正文段画面上找不到常驻声明：{len(hits)} 个采样点 IoU 全部为 "
                        f"{max(hits):.3f} 以下，一行都没上屏")
        elif good * 2 < len(hits):
            reds.append(f"常驻声明只在部分时刻出现（{good}/{len(hits)}）："
                        f"它必须像书名层那样全段常驻，跟着场景出现/消失就是缺陷")

    # 钩子段不许带这层：位置在底部，与顶部书名层门（check-hook-no-bookmark.py）互不遮挡，
    # 但同一条理由——钩子那几秒还没把「这是谁的书」交给观众，先压一行来源说明等于自降搬运。
    if hook_dur:
        # 钩子段末帧会被克隆 junction_dissolve_seconds 秒当接缝底板（旧版是钩子尾
        # hook_fade_out_seconds 淡出到黑），那一段画面正在和片头互溶，取样会拿到混合帧。
        # 两个键都读、按当前 lib 里存在的那个让开，不凭记忆写死秒数。
        seam = float(intro.get("junction_dissolve_seconds")
                     or intro.get("hook_fade_out_seconds") or 0.0)
        h_hi = hook_dur - seam - 0.15
        h_times = [round(0.4 + (h_hi - 0.4) * k / 3, 3) for k in range(4)]
        h_times = [t for t in h_times if 0.2 < t < h_hi]
        ev_hook = static_layer_evidence(h_times, work, "h")
        if ev_hook:
            inside, outside, ok = ev_hook
            print(f"\n[钩子窗] 静止层证据（{len(h_times)} 帧）"
                  f"  墨迹 std={inside:.2f}  其余 std={outside:.2f}"
                  f"  比值={inside / outside:.2f} → {'有静止层' if ok else '干净'}")
        elif h_times:
            print(f"\n[钩子窗] 无从判定（钩子画面在 {h_times[0]:.2f}..{h_times[-1]:.2f}s 之间"
                  f"本身没有足够位移）")
        else:
            print(f"\n[钩子窗] 钩子太短（{hook_dur:.2f}s 让开接缝后不足 3 帧），不判")
        if ev_hook and ev_hook[2]:
            reds.append(f"钩子段（0..{hook_dur:.2f}s）画面上压着常驻声明：这层属于正文段，"
                        f"钩子段要 showDisclaimer:false 并重渲 hook.mp4")

    if reds:
        print(f"\n❌ 常驻声明门禁：{len(reds)} 项判红")
        for r in reds:
            print(f"   · {r}")
        sys.exit(1)
    print("\n✅ 常驻声明门禁：全部通过")


if __name__ == "__main__":
    main()
