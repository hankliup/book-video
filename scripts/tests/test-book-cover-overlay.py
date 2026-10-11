#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本书封面门（scripts/check-book-cover-overlay.py）的正反对照：合成几种起幅形状，
要求判决跟着形状翻面。

为什么必须有它（2026-10-07）：这条规则的内容是「正文起幅那 1.4 秒里，画面中间长出的是
**这本书**的封面，而且它走了」。三段话三段都可能被悄悄改掉，而本仓库已经量过太多次同一件事
——规则写在 lib、层画在渲染器里，两边都改了，成片却是改之前渲的（《恶意》2026-10-01 的
`showBookMark: false` 早就在生成器里，成片拼的还是旧 hook.mp4，七道门全绿；《偏跟山过不去》
2026-10-03 取图脚本打印「完成 1/1」而什么都没写，重渲把**旧照片**原样搬回屏幕，八道门全绿）。
所以这道门的绿灯必须只可能来自交付像素，测试也不许信任何一条「源文件里有」的证据。

每个判据都配对：
  · 证 A（在）：有层绿 / 没层红 / **叠错图**红（残差在而 RGB 不符）/ **烤进底板**红
    （RGB 相符而残差为零）——后两种是同一块像素的两种反面，只测一种的话另一条分支可以整段删掉
    而没人发现；
  · 证 B（走）：按时淡出绿 / 常驻整段红；
  · 证 C（钩子）：钩子带卡红 / 钩子干净绿（同一份正文只翻钩子那半边）；
  · 证 0a：盘上 png 的形状与 lib 现算不符（slot 挪了 60px）→ 红；
  · 证 0b：没有时序证人 → 红；背景切镜表与证人记的桥接卡退场不符 → 红；
    证人记的时刻与 lib 现算不符（改过 lib 没重渲）→ 红；
    盘上源图换了而叠层字节 sha 没重记 → 红，**并且此时证 0a 的 IoU 照样 1.000**
    （这条断言是整个 sha 对账存在的理由：任何两张封面图的内接形状一模一样）；
  · 证 0c：像素全绿但旁证 sha256 对不上 → 红（溯源独立于像素）；
  · 证 D（动效轨迹）：**正确**轨迹绿并打印「轨迹与 lib 相符」/ 被否决的整数取整 1.00→1.12
    红「动效轨迹与 lib 不符」＋红「越出了 slot 允许的那条带」（叠层字节与 lib 逐字节相同，
    所以这一对照同时钉住「lib 改了 ≠ 像素改了」那一类）/ 有叠层但静止的卡红「没在成片里长开」
    而足迹那条不红 / **纯色装帧**（仪器失盲）打印「顶在搜索网格端点 → 不判」而整片必须绿——
    最后这一条是防止「量不到被写成一个红」，它和防止「量不到被写成一个绿」同样要紧；
  · 爆炸半径：期目录没图 + 中间产物没 png → ⚠️ 无从证明、退出 0；`--require` 判红。
另外 lib 自己的三条断言也在这里复验：slot 爬进字幕取样带必须 import 期抛错，
淡出规则改回被否决的「跟着桥接卡」必须抛错，桥接卡比水波起振还早必须 raise。

渲染变换照真的走：叠层用 ffmpeg overlay + libx264 重编码，不是内存里合成，
否则测的是「Pillow 画得对不对」而不是「压进 h264 之后还在不在」。
用法：python3 scripts/tests/test-book-cover-overlay.py
"""
from __future__ import annotations

import glob
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts", "lib"))
import book_cover as B  # noqa: E402

TMP = os.path.join(ROOT, "tmp", "test-book-cover-overlay")
GATE = os.path.join(ROOT, "scripts", "check-book-cover-overlay.py")
W, H, FPS = 720, 960, 30
BODY = 8.0
HOOK = 4.0
BRIDGE_EXIT = 2.18          # 与本期同量级：桥接卡比涟漪收口晚，淡整个走完在静止卡上

failures: list[str] = []
CFG_FOR_SEQ = None


def ok(cond, msg):
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        failures.append(msg)


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"命令失败：{' '.join(cmd)}\n{r.stdout[-800:]}\n{r.stderr[-800:]}")
    return r.stdout


X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-g", str(FPS),
        "-pix_fmt", "yuv420p", "-an"]


def encode_base(path, seconds, phase0):
    """一段「在缓推的照片」：上亮下暗的渐变 + 两个不同周期的正弦，位移铺满整幅，
    包括卡框（y 235..665）。证 C 的静止层证据要求带内背景本身在动（MOTION_FLOOR），
    证 B 要求「封面走了之后卡框就是背景」——底图静止的话这两半都无从判定。"""
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float64)
    base = 205.0 - 170.0 * (ys / H)
    n = int(round(seconds * FPS))
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-an", *X264, path],
        stdin=subprocess.PIPE)
    assert proc.stdin is not None
    for i in range(n):
        t = i / FPS + phase0
        lum = base + 30.0 * np.sin(2 * np.pi * (ys / 210.0 + t * 0.63)) \
                  + 22.0 * np.sin(2 * np.pi * (xs / 380.0 - t * 0.41))
        frame = np.repeat(np.clip(lum, 0, 255)[:, :, None].astype(np.uint8), 3, axis=2)
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    if proc.wait():
        raise SystemExit("ffmpeg 合成底图失败")


def make_cover(path, variant):
    """几种**互不相同**的合成封面，各自的形状都是某一条对照要造的东西：
      A 暖底斜条 3:4（健康基准：卡芯宽 300px＝slot 的宽度上限）；
      B 冷底圆点 3:4（另一本书，测「叠错图」）；
      T 高幅斜条 1:2（卡芯只有 215px 宽、430px 高，把 slot 的高度用满——被否决的
        1.00→1.12 在真实期数上把卡芯顶进《书名》＋作者层 19px 靠的就是这种装帧，
        3:4 的卡芯同一比例只越 1px，用它测越带等于没测）；
      S 纯色 3:4（卡芯没有任何可对齐的纹理，测的是 ŝ 仪器的**失盲**分支）。
    宽均 > lib 的 min_source_width 600。"""
    wh = {"T": (700, 1400)}.get(variant, (750, 1000))
    im = Image.new("RGB", wh)
    d = ImageDraw.Draw(im)
    if variant == "A":
        d.rectangle([0, 0, 750, 1000], fill=(176, 42, 28))
        for k in range(-4, 12):
            d.polygon([(60 * k, 0), (60 * k + 44, 0), (60 * k + 44 + 300, 1000),
                       (60 * k + 300, 1000)], fill=(244, 208, 60))
        d.rectangle([70, 120, 680, 300], fill=(28, 20, 18))
    elif variant == "T":
        d.rectangle([0, 0, 700, 1400], fill=(24, 96, 138))
        for k in range(-12, 14):
            d.polygon([(56 * k, 0), (56 * k + 30, 0), (56 * k + 30 + 420, 1400),
                       (56 * k + 420, 1400)], fill=(238, 226, 196))
        d.rectangle([60, 170, 640, 430], fill=(18, 16, 22))
        d.rectangle([60, 900, 640, 1010], fill=(196, 58, 40))
    elif variant == "S":
        d.rectangle([0, 0, 750, 1000], fill=(176, 42, 28))
    else:
        im = Image.new("RGB", wh, (18, 118, 132))
        d = ImageDraw.Draw(im)
        for yy in range(0, 1000, 140):
            for xx in range(0, 750, 150):
                d.ellipse([xx + 24, yy + 24, xx + 120, yy + 120], fill=(214, 46, 168))
        d.rectangle([40, 640, 710, 880], fill=(250, 250, 240))
    im.save(path, quality=92)
    return path


def write_provenance(ep_dir, jpg, cfg, sha=None, width=None):
    raw = open(jpg, "rb").read()
    prov = {"source": "weread", "url": "https://weread.qq.com/testing/cover",
            "sha256": (sha or hashlib.sha256(raw).hexdigest()),
            "query_title": "对照书名", "hit_title": "对照书名",
            "width": (width or Image.open(jpg).width)}
    with open(os.path.join(ep_dir, cfg["asset"]["provenance"]), "w", encoding="utf-8") as f:
        json.dump(prov, f, ensure_ascii=False, indent=2)


def write_witness(dirp, cfg, source, timing=None, sha=None, bridge_exit=BRIDGE_EXIT,
                  xfade=0.64):
    t = timing if timing is not None else B.timing(bridge_exit, xfade, cfg)
    w = {"bridge_exit_seconds": round(float(bridge_exit), 3),
         "scene_xfade_seconds": float(xfade), "timing": t,
         "card_box_px": list(B.card_box(source, cfg, (W, H))),
         "overlay_sha256": (sha if sha is not None else
                            hashlib.sha256(B.overlay(source, cfg, (W, H))
                                           .convert("RGBA").tobytes()).hexdigest()),
         "source": os.path.basename(source)}
    with open(os.path.join(dirp, "book-cover.json"), "w", encoding="utf-8") as f:
        json.dump(w, f, ensure_ascii=False, indent=2)


def write_bg_sig(dirp, layer_start):
    with open(os.path.join(dirp, "body-bg.json"), "w", encoding="utf-8") as f:
        json.dump({"layer_start": list(layer_start)}, f, ensure_ascii=False)


def compose_anim(dst, base, cov_t, overlay_im, body_dur=BODY, kill_at=None):
    """封面帧序列叠进底图：与兜底渲染器的 PIL 打帧路径同一条（cover_frame_sequence 是 lib 的）。
    kill_at 秒后把封面压没（把「封面只出现一半」的脏形状按帧截掉）。"""
    seq_dir = dst + "-cover-seq"
    seq = B.cover_frame_sequence(overlay_im, cov_t, FPS, body_dur, seq_dir, CFG_FOR_SEQ)
    if kill_at is not None:
        import glob as _g
        blank = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        for fr in _g.glob(os.path.join(seq["dir"], "f_*.png")):
            idx = int(os.path.basename(fr)[2:6])
            if idx / FPS >= kill_at:
                blank.save(fr)
    run(["ffmpeg", "-y", "-v", "error", "-i", base, "-framerate", str(FPS),
         "-i", os.path.join(seq["dir"], "f_%04d.png"), "-filter_complex",
         "[0:v][1:v]overlay=0:0:format=auto,format=yuv420p[v]", "-map", "[v]", *X264, dst])


def integer_overlay(im, s):
    """被否决过的那台装置：缩放到**整数**像素、再贴回**整数**偏移。
    《第六病室》2026-10-08 那句「放大过程不流畅，抖动得厉害」量到的就是它——卡芯中心每帧
    被量化 ±0.5px，实测 41 步里 24 步方向反转（同一张图走 lib 的逐帧浮点仿射是 0 步）。
    与 `scripts/tests/test-cover-motion-smoothness.py` 里那份是同一台装置：那一侧量 lib
    画出来的帧，这一侧把它烘进成片像素，让交付门自己把它认出来。"""
    bw, bh = max(1, int(round(W * s))), max(1, int(round(H * s)))
    big = im.resize((bw, bh), Image.LANCZOS)
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.alpha_composite(big, (int(round((W - bw) / 2.0)), int(round((H - bh) / 2.0))))
    return out


def compose_ramp(dst, base, overlay_im, cov_t, zoom_from, zoom_to,
                 device="float", ease="linear", body_dur=BODY):
    """把**指定的一条缩放轨迹**烘进底图，而不是 lib 现算的那一条。

    淡入／平台／淡出的 alpha 仍按 lib 的 timing 走，所以这一层照样「该走就走」——证 B 不被牵连，
    红的只有轨迹与足迹。device／ease／端点这三样正是 `lib/book_cover.motion()` 在 import 期拒绝
    的五种形状；测试必须能在渲染器之外把它们造出来，否则 lib 的断言只是在守一份从没被违反过的
    配置，而本仓库付账付的一直是「配置对了、像素是旧的」。
    缩放窗与 lib 的 `zoom_at` 对齐（0 → fade_out_at），所以「静止」与「走错端点」都独立得开。
    """
    fi = float(cov_t["fade_in_seconds"])
    fo = float(cov_t["fade_out_at"])
    fs = max(1e-9, float(cov_t["fade_out_seconds"]))
    gone = float(cov_t["gone_at"])
    seq_dir = dst + "-ramp-seq"
    os.makedirs(seq_dir, exist_ok=True)
    for stale in glob.glob(os.path.join(seq_dir, "f_*.png")):
        os.unlink(stale)
    blank = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for i in range(int(round(body_dur * FPS))):
        t = i / float(FPS)
        path = os.path.join(seq_dir, f"f_{i:04d}.png")
        if t >= gone - 1e-9:
            blank.save(path)
            continue
        u = min(t, fo) / fo
        k = u if ease == "linear" else 0.5 - 0.5 * math.cos(math.pi * u)
        s = zoom_from + (zoom_to - zoom_from) * k
        im = (integer_overlay(overlay_im, s) if device == "integer"
              else B.zoomed_overlay(overlay_im, s))
        a = (t / fi if t < fi else (1.0 if t < fo else max(0.0, 1.0 - (t - fo) / fs)))
        if a < 1.0:
            im = im.copy()
            im.putalpha(im.getchannel("A").point(lambda v: int(round(v * a))))
        im.save(path)
    run(["ffmpeg", "-y", "-v", "error", "-i", base, "-framerate", str(FPS),
         "-i", os.path.join(seq_dir, "f_%04d.png"), "-filter_complex",
         "[0:v][1:v]overlay=0:0:format=auto,format=yuv420p[v]", "-map", "[v]", *X264, dst])


def compose(dst, base, png=None, enable=None):
    """把叠层压进底图：与兜底渲染器同一条路径（overlay + libx264 重编码）。
    png=None 就是「这期没画这一层」，两侧只差叠层这一项。"""
    if png is None:
        run(["ffmpeg", "-y", "-v", "error", "-i", base, "-vf", "format=yuv420p", *X264, dst])
        return
    cond = f":enable='{enable}'" if enable else ""
    run(["ffmpeg", "-y", "-v", "error", "-i", base, "-i", png, "-filter_complex",
         f"[0:v][1:v]overlay=0:0:format=auto{cond},format=yuv420p[v]",
         "-map", "[v]", *X264, dst])


def concat(dst, parts):
    ins = []
    for p in parts:
        ins += ["-i", p]
    chain = "".join(f"[{i}:v]" for i in range(len(parts))) + \
            f"concat=n={len(parts)}:v=1:a=0[v]"
    run(["ffmpeg", "-y", "-v", "error", *ins, "-filter_complex", chain, "-map", "[v]",
         *X264, dst])


def gate(ep_dir, tmp_dir, final, require=True, body_at=HOOK, hook_dur=HOOK):
    cmd = [sys.executable, GATE, "", "--ep-dir", ep_dir, "--tmp", tmp_dir,
           "--final", final, "--body-at", str(body_at), "--hook-dur", str(hook_dur)]
    if require:
        cmd.append("--require")
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    return r.returncode, r.stdout + r.stderr


def red_lines(out):
    return [l.strip() for l in out.split("\n") if l.strip().startswith("·")]


def main():
    if os.path.isdir(TMP):
        shutil.rmtree(TMP)
    os.makedirs(TMP)
    CFG = B.load()
    global CFG_FOR_SEQ
    CFG_FOR_SEQ = CFG
    T_OK = B.timing(BRIDGE_EXIT, 0.64, CFG)
    GONE = T_OK["gone_at"]

    ep = os.path.join(TMP, "ep")
    os.makedirs(ep)
    jpg_a = make_cover(os.path.join(ep, "book-cover.jpg"), "A")
    jpg_b = make_cover(os.path.join(TMP, "other-book.jpg"), "B")
    write_provenance(ep, jpg_a, CFG)

    tpl = os.path.join(TMP, "tpl")
    os.makedirs(tpl)
    png_a = os.path.join(tpl, "book-cover.png")
    B.overlay(jpg_a, CFG, (W, H)).save(png_a)
    png_b = os.path.join(TMP, "cover-b.png")
    B.overlay(jpg_b, CFG, (W, H)).save(png_b)
    write_witness(tpl, CFG, jpg_a)
    write_bg_sig(tpl, [0.0, BRIDGE_EXIT, 30.2])

    body_bg = os.path.join(tpl, "body-bg.mp4")
    encode_base(body_bg, BODY, 0.0)
    hook_bg = os.path.join(TMP, "hook-bg.mp4")
    encode_base(hook_bg, HOOK, 7.3)

    # 健康成片：正文起幅带卡、gone_at 起没有、钩子段干净
    body_ok = os.path.join(TMP, "body-ok.mp4")
    compose_anim(body_ok, body_bg, T_OK, B.overlay(jpg_a, CFG, (W, H)))
    final_ok = os.path.join(TMP, "final-ok.mp4")
    concat(final_ok, [hook_bg, body_ok])

    print("〔对照 1〕健康：起幅带这本书的封面、2.02s 淡出、钩子干净 → 必须绿")
    rc, out = gate(ep, tpl, final_ok)
    ok(rc == 0, f"退出码 {rc}（期望 0）\n{out if rc else ''}")
    ok("全部通过" in out, "打印 ✅ 本书封面门禁：全部通过")
    ok("[证 A] 平台期 4 点" in out, "平台期采到 4 个点（淡入收口与淡出起振两头都在取样范围内）")
    ok("轨迹与 lib 相符" in out,
       f"证 D 在健康成片上必须自己报「轨迹与 lib 相符、行程长开、足迹留在 slot 允许的那条带里」"
       f"——绿只可能来自像素，不是来自「今天没有反解出东西」\n----\n{out[-900:]}")

    print("〔对照 2〕缺失：正文一段都没叠这层 → 必须红")
    body_none = os.path.join(TMP, "body-none.mp4")
    compose(body_none, body_bg, None)
    final_none = os.path.join(TMP, "final-none.mp4")
    concat(final_none, [hook_bg, body_none])
    rc, out = gate(ep, tpl, final_none)
    ok(rc == 1, "退出码 1")
    ok(any("找不到这本书的封面" in r for r in red_lines(out)),
       f"点名「卡框里找不到这本书的封面」\n----\n{out}")

    print("〔对照 3〕叠错图：长出的是另一本书的封面 → 必须红，且红的话不能说成「没上屏」")
    body_wrong = os.path.join(TMP, "body-wrong.mp4")
    compose_anim(body_wrong, body_bg, T_OK, B.overlay(jpg_b, CFG, (W, H)))
    final_wrong = os.path.join(TMP, "final-wrong.mp4")
    concat(final_wrong, [hook_bg, body_wrong])
    rc, out = gate(ep, tpl, final_wrong)
    ok(rc == 1, "退出码 1")
    ok(any("不是这本书的封面" in r for r in red_lines(out)),
       f"点名「长出了一层东西，但它不是这本书的封面」\n----\n{out}")
    ok(not any("烤进 body-bg 底板" in r for r in red_lines(out)), "没被误判成烤进底板")

    print("〔对照 4〕烤进底板：卡框像素对得上，但残差为零（层在 body-bg 里）→ 必须红")
    baked_bg = os.path.join(TMP, "baked", )
    os.makedirs(baked_bg)
    body_baked_bg = os.path.join(baked_bg, "body-bg.mp4")
    compose_anim(body_baked_bg, body_bg, T_OK, B.overlay(jpg_a, CFG, (W, H)))  # 封面进了**背景**
    tpl_baked = os.path.join(baked_bg, "tpl")
    os.makedirs(tpl_baked)
    shutil.copy(png_a, os.path.join(tpl_baked, "book-cover.png"))
    write_witness(tpl_baked, CFG, jpg_a)
    write_bg_sig(tpl_baked, [0.0, BRIDGE_EXIT, 30.2])
    shutil.copy(body_baked_bg, os.path.join(tpl_baked, "body-bg.mp4"))
    body_baked_fin = os.path.join(baked_bg, "body-fin.mp4")
    compose(body_baked_fin, body_baked_bg, None)    # 叠层这一遍什么都不加
    final_baked = os.path.join(baked_bg, "final.mp4")
    concat(final_baked, [hook_bg, body_baked_fin])
    rc, out = gate(ep, tpl_baked, final_baked)
    ok(rc == 1, "退出码 1")
    ok(any("烤进 body-bg 底板" in r for r in red_lines(out)),
       f"点名「烤进 body-bg 底板，不是合成上去的一层」\n----\n{out}")

    print("〔对照 5〕不离开：封面常驻整段 → 证 B 必须红")
    body_stays = os.path.join(TMP, "body-stays.mp4")
    compose(body_stays, body_bg, png_a)
    final_stays = os.path.join(TMP, "final-stays.mp4")
    concat(final_stays, [hook_bg, body_stays])
    rc, out = gate(ep, tpl, final_stays)
    ok(rc == 1, "退出码 1")
    ok(any("仍然出现在卡框里" in r for r in red_lines(out)),
       f"点名「{GONE:.2f}s 之后仍然出现在卡框里」\n----\n{out}")

    print("〔对照 6〕钩子段压着这张卡 → 必须红；钩子干净 → 绿（正文半边不动）")
    hook_dirty = os.path.join(TMP, "hook-dirty.mp4")
    compose(hook_dirty, hook_bg, png_a)
    final_hook_dirty = os.path.join(TMP, "final-hook-dirty.mp4")
    concat(final_hook_dirty, [hook_dirty, body_ok])
    rc, out = gate(ep, tpl, final_hook_dirty)
    ok(rc == 1 and any("钩子段" in r and "压着本书封面" in r for r in red_lines(out)),
       f"钩子带卡 → 红\n----\n{out}")
    rc, out = gate(ep, tpl, final_hook_dirty, require=False)
    ok(rc == 1, "同一条红不靠 --require（证 C 自己判得出）")

    print("〔对照 7〕盘上 png 形状与 lib 不符（slot 挪了 60px 没重渲）→ 证 0a 必须红")
    cfg_shift = json.load(open(B.LIB_PATH, encoding="utf-8"))
    cfg_shift["slot"]["y0_px"] += 60
    cfg_shift["slot"]["y1_px"] += 60
    tpl_shift = os.path.join(TMP, "tpl-shift")
    os.makedirs(tpl_shift)
    B.overlay(jpg_a, cfg_shift, (W, H)).save(os.path.join(tpl_shift, "book-cover.png"))
    write_witness(tpl_shift, CFG, jpg_a)
    write_bg_sig(tpl_shift, [0.0, BRIDGE_EXIT, 30.2])
    shutil.copy(body_bg, os.path.join(tpl_shift, "body-bg.mp4"))
    rc, out = gate(ep, tpl_shift, final_ok)
    ok(rc == 1, "退出码 1")
    ok(any("与 lib 的 slot/封面图不符" in r for r in red_lines(out)),
       f"点名「盘上 book-cover.png 与 lib 的 slot/封面图不符」\n----\n{out}")
    ok(not any("找不到这本书的封面" in r for r in red_lines(out)), "像素两半不被牵连（成片是健康的）")

    print("〔对照 8〕证人链三缺：没有时序证人 / 背景重切过 / 改过 lib 没重渲 → 各自必须红")
    tpl_nowit = os.path.join(TMP, "tpl-nowit")
    os.makedirs(tpl_nowit)
    shutil.copy(png_a, os.path.join(tpl_nowit, "book-cover.png"))
    shutil.copy(body_bg, os.path.join(tpl_nowit, "body-bg.mp4"))
    write_bg_sig(tpl_nowit, [0.0, BRIDGE_EXIT, 30.2])
    rc, out = gate(ep, tpl_nowit, final_ok)
    ok(rc == 1 and any("没有 book-cover.json 时序证人" in r for r in red_lines(out)),
       f"png 在而证人不在 → 红\n----\n{out}")

    tpl_reslice = os.path.join(TMP, "tpl-reslice")
    os.makedirs(tpl_reslice)
    shutil.copy(png_a, os.path.join(tpl_reslice, "book-cover.png"))
    shutil.copy(body_bg, os.path.join(tpl_reslice, "body-bg.mp4"))
    write_witness(tpl_reslice, CFG, jpg_a)
    write_bg_sig(tpl_reslice, [0.0, 3.40, 30.2])      # 背景切镜表现在把首镜定在 3.40s
    rc, out = gate(ep, tpl_reslice, final_ok)
    ok(rc == 1 and any("背景切镜表现在是" in r for r in red_lines(out)),
       f"背景重切过、封面用的还是上一版时刻 → 红\n----\n{out}")

    tpl_stale = os.path.join(TMP, "tpl-stale")
    os.makedirs(tpl_stale)
    shutil.copy(png_a, os.path.join(tpl_stale, "book-cover.png"))
    shutil.copy(body_bg, os.path.join(tpl_stale, "body-bg.mp4"))
    write_bg_sig(tpl_stale, [0.0, BRIDGE_EXIT, 30.2])
    # 被否决的旧规则留下的证人：淡出跟着桥接卡走（2.18 → 2.82）
    write_witness(tpl_stale, CFG, jpg_a,
                  timing={"fade_in_seconds": 0.32, "fade_out_at": BRIDGE_EXIT,
                          "fade_out_seconds": 0.64, "gone_at": round(BRIDGE_EXIT + 0.64, 2)})
    rc, out = gate(ep, tpl_stale, final_ok)
    ok(rc == 1 and any("与 lib 现算" in r for r in red_lines(out)),
       f"证人记的时刻复现不出来（改过 lib 没重渲）→ 红\n----\n{out}")

    print("〔对照 9〕换图没重渲：盘上源图换成另一本，叠层字节 sha 对不上 → 必须红，"
          "而证 0a 的 IoU 照样满分（这正是 sha 存在的理由）")
    ep_swap = os.path.join(TMP, "ep-swap")
    os.makedirs(ep_swap)
    shutil.copy(jpg_b, os.path.join(ep_swap, "book-cover.jpg"))
    write_provenance(ep_swap, os.path.join(ep_swap, "book-cover.jpg"), CFG)
    rc, out = gate(ep_swap, tpl, final_ok)
    ok(rc == 1, "退出码 1")
    ok(any("叠层 sha256" in r or "overlay_sha256" in r for r in red_lines(out)),
       f"点名「渲染时记下的封面叠层 sha256 与 lib 现算不符」\n----\n{out}")
    iou_line = [l for l in out.split("\n") if "vs lib 现算墨迹" in l]
    ok(bool(iou_line) and "IoU=1.00" in iou_line[0],
       f"证 0a 的 alpha 形状 IoU 依然 ≈1.000（{iou_line[0].strip() if iou_line else '未打印'}）"
       f"——两张不同封面的内接形状一模一样，只比形状看不出换图")

    print("〔对照 10〕像素全绿但旁证对不上 → 溯源必须单独判红")
    ep_prov = os.path.join(TMP, "ep-prov")
    os.makedirs(ep_prov)
    shutil.copy(jpg_a, os.path.join(ep_prov, "book-cover.jpg"))
    write_provenance(ep_prov, os.path.join(ep_prov, "book-cover.jpg"), CFG,
                     sha="0" * 64)                    # 换过图、旁证没重写
    rc, out = gate(ep_prov, tpl, final_ok)
    ok(rc == 1, "退出码 1")
    ok(any("溯源不通过" in r for r in red_lines(out)), f"点名「封面图溯源不通过」\n----\n{out}")
    ok("[证 A] 平台期 4 点" in out and "　❌" not in out.split("[证 B]")[0],
       "像素两半全绿——红只来自旁证")

    print("〔对照 11〕存量不炸：期目录没图 + 中间产物没 png")
    ep_legacy = os.path.join(TMP, "ep-legacy")
    tpl_legacy = os.path.join(TMP, "tpl-legacy")
    os.makedirs(ep_legacy)
    os.makedirs(tpl_legacy)
    rc, out = gate(ep_legacy, tpl_legacy, final_none, require=False)
    ok(rc == 0 and "无从证明" in out, f"不 --require → ⚠️ 无从证明、退出 0\n----\n{out}")
    rc, out = gate(ep_legacy, tpl_legacy, final_none)
    ok(rc == 1, f"--require → 红（退出码 {rc}）")
    ok("book-cover.jpg" in out, "红的话点名「期目录下没有 book-cover.jpg」并给出取图命令")

    print("〔对照 12〕lib 的三条 import 期断言：slot 爬进字幕带 / 淡出改回桥接卡 / 桥接卡比水波早")
    for patch, expect in (
            ((("slot", "y1_px"), 700), "保留带"),
            ((("slot", "y0_px"), 200), "保留带")):
        bad = json.load(open(B.LIB_PATH, encoding="utf-8"))
        bad[patch[0][0]][patch[0][1]] = patch[1]
        p = os.path.join(TMP, f"lib-{patch[0][1]}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(bad, f, ensure_ascii=False)
        try:
            B.load(p)
            ok(False, f"{patch[0]}={patch[1]} 让卡框进入保留带却没能抛错")
        except ValueError as exc:
            ok(expect in str(exc), f"{patch[0]}={patch[1]} 抛错并点名{expect}：{str(exc)[:70]}")

    bad_t = json.load(open(B.LIB_PATH, encoding="utf-8"))
    bad_t["timing"]["fade_out_from"] = "bridge_exit"
    p = os.path.join(TMP, "lib-timing-bridge.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(bad_t, f, ensure_ascii=False)
    try:
        # 绕过 load() 直接喂字典：load() 只钉 fade_in_from，淡出规则由 timing() 自己认，
        # 这样这条对照测的才是「规则被改回被否决的那一版」而不是 JSON 校验顺序。
        B.timing(BRIDGE_EXIT, 0.64, json.load(open(p, encoding="utf-8")))
        ok(False, "淡出规则退回被否决的「跟着桥接卡」却没能抛错")
    except ValueError as exc:
        ok("涟漪收口" in str(exc), f"被否决的旧规则当场抛错：{str(exc)[:70]}")
    try:
        B.timing(0.2, 0.64, CFG)
        ok(False, "桥接卡 0.20s 就切走（比水波起振还早）却没能 raise")
    except SystemExit as exc:
        ok("比水波起振" in str(exc), f"下限自检 raise：{str(exc)[:70]}")

    print("〔对照 13〕源图太小不许上屏（微信读书 70×101 缩略图拉成 300px 卡是糊图）")
    tiny = os.path.join(TMP, "tiny.jpg")
    Image.new("RGB", (300, 400), (200, 30, 30)).save(tiny, quality=92)
    ep_tiny = os.path.join(TMP, "ep-tiny")
    os.makedirs(ep_tiny)
    shutil.copy(tiny, os.path.join(ep_tiny, "book-cover.jpg"))
    write_provenance(ep_tiny, os.path.join(ep_tiny, "book-cover.jpg"), CFG)
    good, note = B.provenance_ok(ep_tiny, CFG)
    ok(not good and "低于下限" in note, f"provenance_ok 拒绝 300px 源图：{note[:80]}")
    rc, out = gate(ep_tiny, tpl, final_ok)
    ok(rc == 1 and any("溯源不通过" in r for r in red_lines(out)),
       f"门也把它判红（不是只打印）\n----\n{out[-600:]}")

    print("〔对照 14〕平台期缺一半：封面只在水波前半段出现 → 必须红（容到 50% 就是放行）")
    body_half = os.path.join(TMP, "body-half.mp4")
    compose_anim(body_half, body_bg, T_OK, B.overlay(jpg_a, CFG, (W, H)), kill_at=0.9)
    final_half = os.path.join(TMP, "final-half.mp4")
    concat(final_half, [hook_bg, body_half])
    rc, out = gate(ep, tpl, final_half)
    miss_line = [r for r in red_lines(out) if "平台期" in r and "点不是这张封面" in r]
    ok(rc == 1 and bool(miss_line), f"部分时刻出现必须红\n----\n{out[-700:]}")
    ok(bool(miss_line) and "2 点" in miss_line[0],
       f"红话报出的是「4 点里有 2 点」（{miss_line[0][:110] if miss_line else '—'}）："
       "旧口径 good*2<len 会把这个形状放成绿，而它正是「淡入被吃掉一半」的形状")
    # 这一句红话有三种相反的红，手指必须只指向对的那一边：这一支红的是**平台期后半**那几点
    # （层被 kill 掉了），而静止卡/旧 ramp 红的是**淡入**那几点。少了这条负断言，
    # 「读下面证 D 的 ŝ 轨迹」那一句会同时挂在两种红上，查的人先去查缩放、再查 body-at，
    # 两头都不落地。
    ok("读下面证 D" not in miss_line[0],
       f"「只在部分时刻出现」这一支不被引到缩放上去（{miss_line[0][-90:] if miss_line else '—'}）")
    ok(not any("仍然出现在卡框里" in r for r in red_lines(out)),
       "证 B 不被牵连（该走之后确实没有封面，红的只有「该在的时候不在」）")

    print("〔对照 15〕正文起点过期 1.0s：有 body-bg 时按帧自愈，没有时那半边必须红")
    stale = round(HOOK + 1.0, 3)          # 采样的最后一点跨过 gone_at，前几点仍在封面里
    rc, out = gate(ep, tpl, final_ok, body_at=stale)
    ok(rc == 0 and "退回按帧换算" in out,
       f"有背景中间产物 → 门自己按「成片帧数 − 背景帧数」修正坐标并绿\n----\n{out[-500:]}")
    ok("点不是这张封面" not in out, f"自愈之后平台期四点全对账\n----\n{out[-400:]}")
    tpl_nobg = os.path.join(TMP, "tpl-nobg")
    os.makedirs(tpl_nobg, exist_ok=True)
    for fn in ("book-cover.png", "book-cover.json", "body-bg.json"):
        shutil.copy(os.path.join(tpl, fn), os.path.join(tpl_nobg, fn))
    rc, out = gate(ep, tpl_nobg, final_ok, body_at=stale)
    ok(rc == 1 and any(("点不是这张封面" in r) or ("找不到这本书的封面" in r)
                       for r in red_lines(out)),
       f"没有背景（无从按帧换算）→ 同一份过期坐标判红，旧口径 3/4 会被放行\n----\n{out[-700:]}")

    print("〔对照 16〕被否决的整数取整 1.00→1.12 烘进成片（叠层字节与 lib 逐字节相同）："
          "证 D 必须同时红「轨迹」与「越带」两条")
    # 这一版正是《第六病室》2026-10-08 被用户退回的那一版：`overlay()` 那张静态 PNG 一个字节
    # 都没变（证 0b 对它全盲），卡芯在四个采样点上甚至还是这本书的封面（证 A 只偶然红），
    # 而十四道门里没有任何一条会问「它按哪条 ramp 长开的」。装帧必须用 1:2 的高卡：3:4 的卡芯
    # 在同一比例下只越 1px（量不到那条 3.0px 的线），真实期数里把卡芯顶进书名层 19px 的都是
    # 把 slot 高度用满的装帧。
    ep_t = os.path.join(TMP, "ep-tall")
    os.makedirs(ep_t)
    jpg_t = make_cover(os.path.join(ep_t, "book-cover.jpg"), "T")
    write_provenance(ep_t, jpg_t, CFG)
    tpl_t = os.path.join(TMP, "tpl-tall")
    os.makedirs(tpl_t)
    B.overlay(jpg_t, CFG, (W, H)).save(os.path.join(tpl_t, "book-cover.png"))
    write_witness(tpl_t, CFG, jpg_t)
    write_bg_sig(tpl_t, [0.0, BRIDGE_EXIT, 30.2])
    shutil.copy(body_bg, os.path.join(tpl_t, "body-bg.mp4"))
    body_v1 = os.path.join(TMP, "body-v1integer.mp4")
    compose_ramp(body_v1, body_bg, B.overlay(jpg_t, CFG, (W, H)), T_OK,
                 1.00, 1.12, device="integer", ease="linear")
    final_v1 = os.path.join(TMP, "final-v1integer.mp4")
    concat(final_v1, [hook_bg, body_v1])
    rc, out = gate(ep_t, tpl_t, final_v1)
    ok(rc == 1, "退出码 1")
    traj = [r for r in red_lines(out) if "动效轨迹与 lib 不符" in r]
    ok(bool(traj), f"点名「本书封面的动效轨迹与 lib 不符」\n----\n{out[-900:]}")
    ok(bool(traj) and "上一版动效形状" in traj[0],
       "红话说的是「证 0b 的叠层字节逐字节相同，所以这一版烘的是上一版动效形状」："
       "这一条的存在理由就是 lib 改了而像素是旧的")
    ok(bool(traj) and "另一条 ramp" in traj[0],
       f"形状分类器把它读成「另一条 ramp」而不是「根本没有缩放」（{traj[0][:150] if traj else '—'}）")
    band = [r for r in red_lines(out) if "越出了 slot 允许的那条带" in r]
    ok(bool(band) and "书名》＋作者层" in band[0],
       f"按成片反解的真实足迹点名顶进《书名》＋作者层（{band[0][:170] if band else '—'}）："
       "`_assert_clear_of_reserved_bands()` 量的是静态 slot，1.00→1.12 那一版全库绿就是缺了这一条")
    ok(not any("根本没有缩放" in r for r in red_lines(out)),
       "静止那一支不被牵连（它确实走了 16px，只是走的是错的那条）")
    ok(not any("仍然出现在卡框里" in r for r in red_lines(out)),
       "证 B 不被牵连（淡入淡出仍按 lib 的 timing 走）")

    print("〔对照 17〕有叠层、没动效：静止的卡照样按时淡出 → 必须红「没在成片里长开」")
    body_static = os.path.join(TMP, "body-static.mp4")
    compose_ramp(body_static, body_bg, B.overlay(jpg_a, CFG, (W, H)), T_OK,
                 1.00, 1.00, device="float", ease="linear")
    final_static = os.path.join(TMP, "final-static.mp4")
    concat(final_static, [hook_bg, body_static])
    rc, out = gate(ep, tpl, final_static)
    ok(rc == 1, "退出码 1")
    ok(any("没在成片里长开" in r for r in red_lines(out)),
       f"行程那条单独红：静止卡在四个采样点上 RGB 可以很像，但 ŝ 首末帧之差是 0px\n----\n{out[-900:]}")
    ok(any("根本没有缩放" in r for r in red_lines(out)),
       "红话点名「这一层根本没有缩放」（cover-seq 没被合成／被静态 PNG 顶掉）")
    ok(not any("越出了 slot 允许的那条带" in r for r in red_lines(out)),
       "足迹那条不红：静止卡的真实足迹就是 slot，越带 0px")
    # 静止卡红的那两点是**淡入**里的（lib 在那里预测 ×0.858/×0.915，卡却停在 ×1.000），
    # 对上的两点是平台期末尾的。这正好和「层被砍了一半」长得一样（那边红的是后半那几点），
    # 所以证 A 的平台期红话必须按红点落在哪一半分岔——否则人被派去查 --body-at，
    # 查回来还是红的。对照 14 断言这一句**不**出现在砍半那一支上，两条一起才算对上。
    miss_static = [r for r in red_lines(out) if "点不是这张封面" in r]
    ok(bool(miss_static) and "读下面证 D" in miss_static[0],
       f"静止卡这一支被引到缩放上去，而不是留在「起点取错」里"
       f"（{miss_static[0][-120:] if miss_static else '没有平台期红'}）")

    print("〔对照 18〕纯色装帧（卡芯没有可对齐的纹理）走的是**正确**轨迹 → 只能「不判」，不许判红")
    # 仪器的失盲分支必须配对：ŝ 靠的是卡芯里能对上的字与图纹，一张纯色装帧在**任何**缩放下都
    # 一模一样（实测谷高 0.00、误差单调降到网格下界），于是 ŝ 恒等于搜索区间的左端点 0.600，
    # 折出来 101px 的偏差。如果这一支也红，送人去查的是一张没有任何缺陷的封面——量不到写成
    # 一个红，和量不到写成一个绿，是同一类空转。这一条同时钉住「不许把 FIT_S_LO 往下调来放行」。
    ep_s = os.path.join(TMP, "ep-solid")
    os.makedirs(ep_s)
    jpg_s = make_cover(os.path.join(ep_s, "book-cover.jpg"), "S")
    write_provenance(ep_s, jpg_s, CFG)
    tpl_s = os.path.join(TMP, "tpl-solid")
    os.makedirs(tpl_s)
    B.overlay(jpg_s, CFG, (W, H)).save(os.path.join(tpl_s, "book-cover.png"))
    write_witness(tpl_s, CFG, jpg_s)
    write_bg_sig(tpl_s, [0.0, BRIDGE_EXIT, 30.2])
    shutil.copy(body_bg, os.path.join(tpl_s, "body-bg.mp4"))
    body_solid = os.path.join(TMP, "body-solid.mp4")
    compose_anim(body_solid, body_bg, T_OK, B.overlay(jpg_s, CFG, (W, H)))
    final_solid = os.path.join(TMP, "final-solid.mp4")
    concat(final_solid, [hook_bg, body_solid])
    rc, out = gate(ep_s, tpl_s, final_solid)
    ok("顶在搜索网格端点" in out,
       f"ŝ 顶在网格端点时打印「动效轨迹**不判**（不是通过）」\n----\n{out[-1200:]}")
    ok(rc == 0 and "动效轨迹与 lib 不符" not in out,
       f"轨迹那条不红，整片仍绿（尺度这一维交回证 A 的残差形状与证 0b 的叠层字节）\n----\n{out[-700:]}")

    print(f"\n{'❌ ' + str(len(failures)) + ' 项失败' if failures else '✅ 本书封面门对照全部通过'}")
    for f_ in failures:
        print("   · " + f_[:200])
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
