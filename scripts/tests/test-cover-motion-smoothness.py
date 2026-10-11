#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本书封面「出场放大」的正反对照：装置换对没有、缩放足迹越没越带、节奏够不够快。

为什么必须有它（2026-08 二次要求）：用户 2026-10-08 第一次说「增加放大的动态效果」，
lib 加的是 `resize(int(round(W*s)))` + 整数偏移贴回；同一天第二次说「放大过程不流畅，
**抖动得厉害**，可以参考氛围图的动态效果，通过增强本书封面图的动态节奏速度来达到效果」。
量出来（tmp/ward6-cover-motion-measure.py，本期 42 帧，沿卡体中部四行在 alpha≥240 上做
线性插值取亚像素边）：整数装置 41 步里 **24 步方向回退**，净位移 17.4px 而路径 30.2px
——七成位移花在来回上，观众读到的不是「在放大」而是「在抖」；而 `zoom_to=1.12` 那一版
即使装置已换成逐帧浮点仿射（回退 0 处），卡体顶沿仍推到 y206（书名层下沿 225）、底沿
推到 y686（字幕取样带上沿 680），越带 19px / 6px，而**没有任何一方检查过缩放后的足迹**
——`_assert_clear_of_reserved_bands()` 当年只量静态 slot。

所以这里每个判据都配一个反面，而且反面是**本期成片真烘进去过的那个形状**：

  · 包络算术必须看得见缩放：`motion_envelope` 在 1.12 那版给出 205.8..686.8（不是静态
    slot 的 235..665），`_assert_clear_of_reserved_bands()` 必须因此点名两条带抛错；
  · `motion()` 的四道拒绝各配一个「只违反这一条」的配置：整数装置 / 未知缓动 /
    缩放终态离开 1.0 / 边缘速度低于下限（0.95→1.00 只有 5.4px/s）/ 起幅小于 0.7；
  · 真像素上「抖不抖」必须量出来：现 lib 形状回退 **0** 处，被否决的整数形状回退 **≥8**
    处（实测 24）。少了这后一半，前者就只是「量不到东西」的同义反复；
  · 「节奏更快」也要落在像素上：现 lib 形状每帧净位移 ≥ 被否决形状的 1.3 倍；
  · 三方（渲染器、封面门、水波门的排除掩码）必须继续调 lib 的 `zoom_at`/`zoomed_overlay`，
    而不是各自抄一份缩放——「渲染读一份、门禁读另一份」是本仓库反复付账的那个分叉。

用法：python3 scripts/tests/test-cover-motion-smoothness.py
"""
from __future__ import annotations

import copy
import os
import sys
import tempfile

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts", "lib"))
import book_cover as B  # noqa: E402

failures = []


def ok(cond, msg):
    if cond:
        print(f"  ✅ {msg}")
    else:
        print(f"  ❌ {msg}")
        failures.append(msg)


def raises(fn, needle, label):
    """必须抛错，且红话要说出**这一条**自己的修法（几条挤成一句话就没法指路）。"""
    try:
        fn()
    except (ValueError, SystemExit) as e:
        text = str(e)
        ok(needle in text, f"{label} 被拒绝，红话点名「{needle}」")
        return text
    ok(False, f"{label} 必须抛错，却安静通过了")
    return ""


def edge_pair(frame, rows):
    """一帧叠层 → (左沿, 右沿) 的亚像素位置：alpha≥240 的穿越点做线性插值。

    阈值 240 而不是 8：8 会把柔和落影的糊边算进来，而落影本来就伸出卡框，不是「卡」。
    """
    a = np.asarray(frame.getchannel("A")).astype(np.float64)

    def crossing(profile, rising):
        idx = np.nonzero(profile >= 240.0)[0]
        if idx.size == 0:
            return float("nan")
        i = int(idx[0]) if rising else int(idx[-1])
        lo, hi = (i - 1, i) if rising else (i, i + 1)
        if not (0 <= lo < profile.size and 0 <= hi < profile.size) or profile[lo] == profile[hi]:
            return float(i)
        return lo + (240.0 - profile[lo]) / (profile[hi] - profile[lo])

    return (float(np.mean([crossing(a[y, :], True) for y in rows])),
            float(np.mean([crossing(a[y, :], False) for y in rows])))


def integer_overlay(im, s):
    """被否决的旧形状：缩放整幅到**整数**尺寸、再按**整数**偏移贴回。

    照抄 2026-10-08 那次实现，不做任何「改进」——它是本期成片实际烘进去的东西，
    本测试要靠它证明量法能看见抖，不是靠它证明旧写法有多糟。
    """
    W, H = im.size
    if abs(s - 1.0) < 1e-6:
        return im.copy()
    bw, bh = max(1, int(round(W * s))), max(1, int(round(H * s)))
    big = im.resize((bw, bh), Image.LANCZOS)
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.alpha_composite(big, (int(round((W - bw) / 2.0)), int(round((H - bh) / 2.0))))
    return out


def main():
    cfg = B.load()
    canvas = tuple(cfg["canvas"])
    w, h = canvas
    fps = 30
    cov_t = B.timing(1.75, 0.64, cfg)
    ramp = float(cov_t["fade_out_at"])
    n = int(round(ramp * fps)) + 1
    rows = (400, 430, 460, 490)          # 卡体中部，避开文字行与落影偏下区
    slot = B.geometry(cfg, canvas)["slot"]
    bands = {rb["name"]: rb for rb in B.reserved_bands(cfg)}

    # ── 1. 现在的形状：必须绿，且每一项都对得上「流畅 / 不越带 / 更快」
    print("\n[1] 现 lib 的 motion：绿的方向")
    m = B.motion(cfg)
    ok(m["type"] == "zoom_in" and abs(m["zoom_to"] - 1.0) < 1e-6,
       f"缩放终态落在标定卡框（zoom_to={m['zoom_to']}）")
    ok(m["device"] == "float_affine_per_frame",
       f"装置是逐帧浮点仿射（device={m['device']}）")
    ey0, ey1 = B.motion_envelope(cfg, canvas)
    ok(abs(ey0 - slot[1]) < 1e-6 and abs(ey1 - slot[3]) < 1e-6,
       f"动效包络 = 静态 slot（y {ey0:.1f}..{ey1:.1f} vs slot y {slot[1]}..{slot[3]}）")
    for rb in B.reserved_bands(cfg):
        clear = not (ey0 < rb["y1"] and ey1 > rb["y0"])
        ok(clear, f"动效全程不碰保留带 {rb['name']}（y {rb['y0']:.0f}..{rb['y1']:.0f}）")
    floor = float((cfg.get("motion") or {}).get("pace_min_px_per_second", 0))
    ok(floor > 0 and m["px_per_second"] >= floor,
       f"卡框边缘平均 {m['px_per_second']:.2f}px/s ≥ 下限 {floor:.2f}px/s")
    ok(abs(B.zoom_at(0.0, cov_t, cfg) - m["zoom_from"]) < 1e-9
       and abs(B.zoom_at(ramp, cov_t, cfg) - 1.0) < 1e-9,
       "缩放行程 = t0 起幅、涟漪收口那一帧正好落到 slot（动效不拖过 fade_out_at）")

    # ── 2. 包络算术必须真的看得见缩放（不是只把 slot 抄一遍）
    print("\n[2] 被否决的 zoom_to=1.12：包络与保留带的方向")
    c112 = copy.deepcopy(cfg)
    c112["motion"]["zoom_from"] = 1.0
    c112["motion"]["zoom_to"] = 1.12
    by0, by1 = B.motion_envelope(c112, canvas)
    cy = h / 2.0
    ok(abs(by0 - (cy + 1.12 * (slot[1] - cy))) < 1e-6
       and abs(by1 - (cy + 1.12 * (slot[3] - cy))) < 1e-6,
       f"包络按画幅中心放大：1.12 那版给出 y {by0:.1f}..{by1:.1f}（不是静态 slot）")
    crossed = [rb["name"] for rb in B.reserved_bands(c112)
               if by0 < rb["y1"] and by1 > rb["y0"]]
    ok("book_mark_layer" in crossed and "caption_sweep" in crossed,
       f"1.12 的足迹确实压进书名层与字幕取样带（实测越带 19px / 6px）→ {crossed}")
    msg = raises(lambda: B._assert_clear_of_reserved_bands(c112, w, h),
                 "动效把足迹放大", "缩放终态离开 1.0（1.00→1.12）")
    ok("book_mark_layer" in msg or "caption_sweep" in msg,
       "那条抛错点名了被压进的保留带，而不只是说「相交」")
    raises(lambda: B.motion(c112), "motion.zoom_to 必须等于 1.0",
           "缩放终态离开标定卡框")

    # ── 3. 四道拒绝各配一个「只违反这一条」的配置
    print("\n[3] motion() 的装置 / 缓动 / 行程 / 速度四道断言")
    cdev = copy.deepcopy(cfg); cdev["motion"]["device"] = "resize_round_paste"
    raises(lambda: B.motion(cdev), "不是 'float_affine_per_frame'", "整数取整装置")
    ceze = copy.deepcopy(cfg); ceze["motion"]["ease"] = "spring"
    raises(lambda: B.motion(ceze), "未知 motion.ease", "未在册的缓动曲线")
    cslow = copy.deepcopy(cfg); cslow["motion"]["zoom_from"] = 0.95
    txt = raises(lambda: B.motion(cslow), "封面动效太慢", "0.95→1.00（5.4px/s）")
    ok("5.43" in txt or "5.4" in txt, f"红话报出实测速度而不是只说「非法」：{txt[:1]}…")
    ctiny = copy.deepcopy(cfg); ctiny["motion"]["zoom_from"] = 0.5
    raises(lambda: B.motion(ctiny), "motion.zoom_from=0.5 太小", "起幅小于 0.7（甩镜头）")
    # 反面的反面：下限不许是被调来放行某个形状的数——它必须刚好低于采纳值、
    # 且高于被否决值，两头都有余量，否则这条判据是装饰。
    withdrawn_px_s = (1.12 - 1.0) * (cfg["slot"]["max_width_px"] / 2.0) / max(ramp, 1e-9)
    ok(floor > withdrawn_px_s and m["px_per_second"] > floor,
       f"下限 {floor:.1f} 卡在被否决速度 {withdrawn_px_s:.2f} 与采纳速度 "
       f"{m['px_per_second']:.2f} 之间（两头都有余量，不是量身放的门槛）")

    # ── 4. 真像素：抖不抖、快不快，必须由画面本身说了算
    print("\n[4] 逐帧像素上的「抖」与「节奏」")
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "cover.jpg")
        im = Image.new("RGB", (600, 800), (40, 60, 90))
        d = ImageDraw.Draw(im)
        d.rectangle((20, 20, 580, 780), outline=(230, 225, 210), width=6)
        for i in range(10):                    # 内部条纹：重采样后仍是一张有内容的卡
            d.line((30, 60 + i * 70, 570, 60 + i * 70), fill=(200, 190, 170), width=3)
        im.save(src, quality=95)
        ov = B.overlay(src, cfg, canvas)

        def series(zoom_of, make):
            return [make(zoom_of(i / fps)) for i in range(n)]

        lib_frames = series(lambda t: B.zoom_at(t, cov_t, cfg),
                            lambda s: B.zoomed_overlay(ov, s))
        old_lin = lambda t: 1.0 + 0.12 * min(max(t, 0.0), ramp) / ramp
        withdrawn_frames = series(old_lin, lambda s: integer_overlay(ov, s))

        def motion_stats(frames):
            edges = [edge_pair(f, rows) for f in frames]
            L = np.array([e[0] for e in edges]); R = np.array([e[1] for e in edges])
            dl, dr = np.diff(L), np.diff(R)
            rev = int(((dl[:-1] * dl[1:] < -0.02) | (dr[:-1] * dr[1:] < -0.02)).sum())
            spread = float(max(np.abs(dl).max(), np.abs(dr).max()))
            net = float(max(abs(L[-1] - L[0]), abs(R[-1] - R[0])))
            return {"rev": rev, "peak": spread, "net_per_frame": net / (len(frames) - 1),
                    "monotone": bool((dl <= 0.02).all() and (dr >= -0.02).all())}

        a = motion_stats(lib_frames)
        b = motion_stats(withdrawn_frames)
        ok(a["rev"] == 0 and a["monotone"],
           f"现 lib（浮点仿射 cosine 0.82→1.00）：回退 {a['rev']} 处、单调外扩")
        ok(a["peak"] < 1.6,
           f"现 lib 逐帧最大位移 {a['peak']:.3f}px < 1.6px（没有一帧跳一整像素以上）")
        ok(b["rev"] >= 8,
           f"被否决（整数取整 + 线性 1.00→1.12）：回退 {b['rev']} 处 ≥ 8 —— "
           f"量法确实能看见抖，前一条的 0 才不是「量不到东西」")
        ok(b["rev"] > a["rev"],
           f"同一把尺子下旧形状 {b['rev']} 处 vs 新形状 {a['rev']} 处（判据随形状翻面）")
        ok(a["net_per_frame"] >= 1.3 * b["net_per_frame"],
           f"节奏确实更快：每帧净位移 {a['net_per_frame']:.3f}px ≥ "
           f"1.3×{b['net_per_frame']:.3f}px")

    # ── 5. 三方共读同一套缩放（静态扫源码，防「渲染读一份、门禁读另一份」）
    print("\n[5] 渲染器与两道门必须继续调 lib 的缩放")
    def read(rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            return f.read()

    renderer = read("scripts/render_scene_map_template_fallback.py")
    cover_gate = read("scripts/check-book-cover-overlay.py")
    water_gate = read("scripts/check-water-ripple.py")
    lib_src = read("scripts/lib/book_cover.py")
    ok("cover_frame_sequence" in renderer and "_cover_timing" in renderer,
       "兜底渲染器仍按 lib 的时序+帧序列画封面（不自己算缩放）")
    ok("B.zoom_at(" in cover_gate and "B.zoomed_overlay(" in cover_gate,
       "本书封面门（14）的预期帧仍用 lib 的 zoom_at + zoomed_overlay")
    ok("B.zoom_at(t, ctx[\"timing\"], ctx[\"cfg\"])" in water_gate
       or ("zoom_at" in water_gate and "zoomed_overlay" in water_gate),
       "水波门（3）的逐帧排除掩码仍用 lib 的缩放，而不是自己抄一个静态框")
    # 只看 `zoomed_overlay` 的**函数体**：lib 的错误信息里必须写出被否决的那条形状
    # （`resize(int(round(W*s)))`），拿全文做子串断言会把自己写的历史当成回流的缺陷。
    import inspect
    zoom_src = inspect.getsource(B.zoomed_overlay)
    ok("Image.AFFINE" in zoom_src and "Image.BICUBIC" in zoom_src
       and "resize(" not in zoom_src and "int(round" not in zoom_src,
       "lib 的 zoomed_overlay 函数体里只有浮点仿射（AFFINE+BICUBIC），"
       "没有整数 resize/取整贴回回流")
    ok("_MOTION" not in lib_src,
       "封面动效不借用正文氛围镜头的 scene-map motion 块（两套行程各有自己的下限，"
       "共用一个块就会一期一个时长）")

    # ── 6. 帧序列必须幂等：改小时窗之后不许留下上一代的不透明帧
    print("\n[6] cover_frame_sequence 的「换图没真的落盘」对照")
    with tempfile.TemporaryDirectory() as td2:
        seq_dir = os.path.join(td2, "cover-seq")
        wide = {"fade_in_seconds": 0.32, "full_from": 0.32, "fade_out_at": 2.50,
                "fade_out_seconds": 0.64, "gone_at": 3.14, "bridge_exit": 3.50}
        B.cover_frame_sequence(ov, wide, fps, 4.0, seq_dir, cfg)
        # f_0070 = t 2.333s：宽窗里它还在平台期（fade_out_at 2.50 之前，α 满），
        # 窄窗里它已经过了 gone_at 2.02 —— 正是「窗改小」这一次会留下的那颗脏帧。
        probe = os.path.join(seq_dir, "f_0070.png")
        with Image.open(probe) as im:
            before = int(np.asarray(im.getchannel("A")).max())
        B.cover_frame_sequence(ov, cov_t, fps, 4.0, seq_dir, cfg)   # 同一目录，窗改小
        with Image.open(probe) as im:
            after = int(np.asarray(im.getchannel("A")).max())
        ok(before == 255, f"宽窗（gone_at={wide['gone_at']}）那一版在 t=2.33s 确实画出不透明卡芯"
                          f"（α 峰 {before}）—— 没有它下面的对照就是空转")
        ok(after == 0, f"改窄（gone_at={cov_t['gone_at']}）重跑后同一序号必须全透明"
                       f"（α 峰 {after}）：旧版 `if not os.path.exists(path)` 的跳过"
                       "会把上一代的帧留在盘上搬回屏幕")

    print(f"\n{'❌ ' + str(len(failures)) + ' 项失败' if failures else '✅ 封面动效流畅度／越带／节奏对照全部通过'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
