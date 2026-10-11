#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""常驻声明门（scripts/check-disclaimer-overlay.py）的正反对照：合成几种正文/钩子形状，
要求判决跟着形状翻面。

为什么必须有它（2026-10-05）：这条规则的全部内容是「正文段底部那一行小灰字在不在像素里」，
而它恰好是这个仓库最容易失真的一类断言——文案写在 lib、层画在渲染器里，两边都改了，
成片却是改之前渲的（《恶意》2026-10-01 的 `showBookMark: false` 早就在生成器里，
成片拼的还是旧 hook.mp4，七道门全绿）。所以这道门的绿灯必须**只可能**来自交付像素，
而测试不许信任何一条「源文件里有」的证据。

每个判据都配一对：
  · 证人 A（帧差 vs body-bg）：有层绿 / 没层红 / 只在一半时间有层红（常驻=全程）；
  · 颜色项：灰字绿 / **白字红** / 黑字红——「偏暗灰」被换成高对比度保可读性，
    是这条线最容易被悄悄改掉的性质，而它必须不能拿亮度换绿灯（见 [[never-fix-by-dimming]]）；
  · 证人 B（时间 std）：无背景中间产物时，有层绿 / 没层红（证明它不是装饰）；
  · 钩子段：钩子带层红 / 钩子干净绿（同形状只翻面一半，证明红的来源是那层而不是钩子本身）；
  · 叠层对账：盘上 png 与 lib 现算墨迹不符 → 红（改了文案没重渲）；
  · 爆炸半径：中间产物里没有 disclaimer.png 的存量期 → ⚠️ 无从证明且退出 0，
    --require 才判红。
另外 lib 自己的保留带断言也在这里复验一次：字号写大一号把声明爬进字幕带（y 680..850）
必须当场抛错，因为那会让第四道门把这行灰字当成字幕来量。

渲染变换照真的走：叠层用 ffmpeg overlay + libx264 重编码，不是内存里合成，
否则测的是「Pillow 画得对不对」而不是「压进 h264 之后还在不在」。
用法：python3 scripts/tests/test-disclaimer-overlay.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts", "lib"))
import disclaimer as D  # noqa: E402

TMP = os.path.join(ROOT, "tmp", "test-disclaimer-overlay")
GATE = os.path.join(ROOT, "scripts", "check-disclaimer-overlay.py")
W, H, FPS = 720, 960, 30
BODY = 6.0
HOOK = 4.0

failures: list[str] = []


def ok(cond, msg):
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        failures.append(msg)


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"命令失败：{' '.join(cmd)}\n{r.stdout[-800:]}\n{r.stderr[-800:]}")
    return r.stdout


def encode_from_frames(path, seconds, phase0):
    """画一段「在动的底图」：上亮下暗的渐变 + 两个不同周期的正弦，
    声明带（y 885..924）落在暗部且逐帧变化——照片在缓推时带内就是这个形状。

    底图在带内的亮度刻意压在 0..80：常驻声明是灰 150，真实素材的底部（暗角）
    也是这个反差量级。要是把带画成全量程正弦，某些时刻背景正好等于字色，
    对照会变成「测编码器而不是测叠层」。
    """
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float64)
    base = 200.0 - 180.0 * (ys / H)
    n = int(round(seconds * FPS))
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-an", "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "18", "-g", str(FPS), "-pix_fmt", "yuv420p", path],
        stdin=subprocess.PIPE)
    assert proc.stdin is not None
    for i in range(n):
        t = i / FPS + phase0
        lum = base + 26.0 * np.sin(2 * np.pi * (ys / 230.0 + t * 0.63)) \
                  + 18.0 * np.sin(2 * np.pi * (xs / 410.0 - t * 0.41))
        frame = np.repeat(np.clip(lum, 0, 255)[:, :, None].astype(np.uint8), 3, axis=2)
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    if proc.wait():
        raise SystemExit("ffmpeg 合成底图失败")


def overlay_png(dst, cfg):
    D.overlay(cfg, (W, H)).save(dst)


X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-g", str(FPS),
        "-pix_fmt", "yuv420p", "-an"]


def compose(dst, base, png=None, enable=None):
    """把叠层压进底图：与兜底渲染器同一条路径（overlay + libx264 重编码）。
    png=None 就是「这期没画这一层」，但仍走一次同样的编码，两侧只差叠层这一项。"""
    if png is None:
        run(["ffmpeg", "-y", "-v", "error", "-i", base, "-vf", "format=yuv420p", *X264, dst])
        return
    cond = f":enable='{enable}'" if enable else ""
    run(["ffmpeg", "-y", "-v", "error", "-i", base, "-i", png, "-filter_complex",
         f"[0:v][1:v]overlay=0:0:format=auto{cond},format=yuv420p[v]",
         "-map", "[v]", *X264, dst])


def concat(dst, parts):
    ins, chain = [], []
    for i, p in enumerate(parts):
        ins += ["-i", p]
    chain = "".join(f"[{i}:v]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=0[v]"
    run(["ffmpeg", "-y", "-v", "error", *ins, "-filter_complex", chain, "-map", "[v]", *X264, dst])


def gate(tmp_dir, final, body_at=0.0, hook_dur=None, require=True, samples=8):
    cmd = [sys.executable, GATE, "", "--final", final, "--tmp", tmp_dir,
           "--body-at", str(body_at), "--samples", str(samples)]
    if hook_dur is not None:
        cmd += ["--hook-dur", str(hook_dur)]
    if require:
        cmd.append("--require")
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    return r.returncode, r.stdout + r.stderr


def main():
    if os.path.isdir(TMP):
        shutil.rmtree(TMP)
    os.makedirs(TMP)
    CFG = D.load()

    tmp_body = os.path.join(TMP, "with-bg")           # 有 body-bg.mp4：证人 A 的主场
    tmp_nobg = os.path.join(TMP, "no-bg")             # 只有叠层 png：只剩证人 B
    tmp_legacy = os.path.join(TMP, "legacy")          # 什么都没有：加这层之前的渲染器
    for d in (tmp_body, tmp_nobg, tmp_legacy):
        os.makedirs(d)

    lib_png = os.path.join(tmp_body, "disclaimer.png")
    overlay_png(lib_png, CFG)
    shutil.copy(lib_png, os.path.join(tmp_nobg, "disclaimer.png"))

    body_bg = os.path.join(tmp_body, "body-bg.mp4")
    encode_from_frames(body_bg, BODY, 0.0)
    hook_bg = os.path.join(TMP, "hook-bg.mp4")
    encode_from_frames(hook_bg, HOOK, 7.3)

    final_body = os.path.join(TMP, "final-body.mp4")
    compose(final_body, body_bg, lib_png)
    final_nolayer = os.path.join(TMP, "final-nolayer.mp4")
    compose(final_nolayer, body_bg, None)
    final_part = os.path.join(TMP, "final-partial.mp4")
    compose(final_part, body_bg, lib_png, f"lt(t,{BODY / 2 - 1.0:.2f})")

    print("〔对照 1〕健康：正文段全程带 lib 那一层 → 必须绿")
    rc, out = gate(tmp_body, final_body)
    ok(rc == 0, f"退出码 {rc}（期望 0）\n{out if rc else ''}")
    ok("全部通过" in out, "打印 ✅ 常驻声明门禁：全部通过")

    print("〔对照 2〕缺失：正文段一行都没画 → 必须红")
    rc, out = gate(tmp_body, final_nolayer)
    ok(rc == 1, "退出码 1")
    ok("一行都没上屏" in out, f"点名「一行都没上屏」\n----\n{out}")

    print("〔对照 3〕不常驻：只在前半段出现 → 必须红（它要像书名层那样全程在）")
    rc, out = gate(tmp_body, final_part)
    ok(rc == 1, "退出码 1")
    ok("只在部分时刻出现" in out, f"点名「只在部分时刻出现」\n----\n{out}")

    print("〔对照 4〕证人 B 独立成立：没有 body-bg.mp4 时，有层必须绿、没层必须红")
    rc, out = gate(tmp_nobg, final_body)
    ok(rc == 0 and "证人 B 成立" in out, f"无背景中间产物 + 有层 → 绿\n----\n{out}")
    rc, out = gate(tmp_nobg, final_nolayer)
    ok(rc == 1 and "找不到静止墨迹证据" in out, f"无背景中间产物 + 没层 → 红\n----\n{out}")

    print("〔对照 5〕钩子段：带层必须红，干净必须绿（同一份正文，只翻钩子那半边）")
    hook_dirty = os.path.join(TMP, "hook-dirty.mp4")
    compose(hook_dirty, hook_bg, lib_png)
    final_hook_dirty = os.path.join(TMP, "final-hook-dirty.mp4")
    concat(final_hook_dirty, [hook_dirty, final_body])
    rc, out = gate(tmp_body, final_hook_dirty, body_at=HOOK, hook_dur=HOOK)
    ok(rc == 1 and "钩子段" in out and "压着常驻声明" in out,
       f"钩子带层 → 红\n----\n{out}")
    final_hook_clean = os.path.join(TMP, "final-hook-clean.mp4")
    concat(final_hook_clean, [hook_bg, final_body])
    rc, out = gate(tmp_body, final_hook_clean, body_at=HOOK, hook_dur=HOOK)
    ok(rc == 0, f"钩子干净（只有正文带层）→ 绿\n----\n{out}")

    print("〔对照 6〕改过文案没重渲：盘上 png 与 lib 不符 → 必须红")
    stale = json.load(open(D.LIB_PATH, encoding="utf-8"))
    stale["parts"] = ["内容为个人书评", "素材来源网络"]
    stale_json = os.path.join(TMP, "stale-wording.json")
    with open(stale_json, "w", encoding="utf-8") as f:
        json.dump(stale, f, ensure_ascii=False)
    stale_dir = os.path.join(TMP, "stale")
    os.makedirs(stale_dir)
    stale_png = os.path.join(stale_dir, "disclaimer.png")
    overlay_png(stale_png, D.load(stale_json))
    shutil.copy(body_bg, os.path.join(stale_dir, "body-bg.mp4"))
    final_stale = os.path.join(TMP, "final-stale.mp4")
    compose(final_stale, body_bg, stale_png)
    rc, out = gate(stale_dir, final_stale)
    ok(rc == 1, "退出码 1")
    ok("与 lib 文案/几何不符" in out, f"点名「盘上 disclaimer.png 与 lib 文案/几何不符」\n----\n{out}")

    print("〔对照 7〕颜色不许换成白字/黑字（判据独立于文案与几何：IoU 照样满分）")
    # alpha 取自**当前 lib**而不是写死 0.9：门的「字面亮度」是反推出来的
    # （est = (帧 − (1−a)·背景)/a，a 读 scripts/lib/disclaimer.json），控制样本要是画在
    # 0.9 而门按 lib 的 a 去除，250 的白字会被算成 ~150 的灰，这条对照就悄悄变成永真。
    # 2026-10-06 用户要求「底部水印显示波动、颜色变化」把声明改成不透明（a=1.0），
    # 写死的 0.9 正是那样失效的——绿了，而它什么也没看。
    lib_alpha = D.load()["color_rgba"][3]
    for name, over, expect in (
            ("白字", {"color_rgba": [250, 250, 250, lib_alpha]}, "字面亮度"),
            ("黑字", {"color_rgba": [25, 25, 25, lib_alpha]}, None)):
        bad = json.load(open(D.LIB_PATH, encoding="utf-8"))
        bad.update(over)
        bad_json = os.path.join(TMP, f"color-{name}.json")
        with open(bad_json, "w", encoding="utf-8") as f:
            json.dump(bad, f, ensure_ascii=False)
        d = os.path.join(TMP, f"color-{name}")
        os.makedirs(d, exist_ok=True)
        png = os.path.join(d, "disclaimer.png")
        overlay_png(png, D.load(bad_json))
        shutil.copy(body_bg, os.path.join(d, "body-bg.mp4"))
        fin = os.path.join(TMP, f"final-color-{name}.mp4")
        compose(fin, body_bg, png)
        rc, out = gate(d, fin)
        ok(rc == 1, f"{name} → 红")
        if expect:
            ok(expect in out, f"{name} 的红由「{expect}」判出，不是靠 IoU 或叠层对账\n----\n{out}")
            ok("与 lib 文案/几何不符" not in out, f"{name} 的几何/文案项不被牵连（alpha 形状一致）")

    print("〔对照 8〕存量不炸：中间产物里没有 disclaimer.png")
    rc, out = gate(tmp_legacy, final_nolayer, require=False)
    ok(rc == 0 and "无从证明" in out, f"不 --require → ⚠️ 无从证明、退出 0\n----\n{out}")
    rc, out = gate(tmp_legacy, final_body)
    ok(rc == 1 and "渲染器没画这一层" in out, f"--require → 红并点名「渲染器没画这一层」\n----\n{out}")

    print("〔对照 9〕lib 的保留带断言：字号写大一号把声明爬进字幕带（y 680..850）必须当场抛")
    for key, val in (("font_size_px", 96), ("bottom_px", 300)):
        collide = json.load(open(D.LIB_PATH, encoding="utf-8"))
        collide[key] = val
        p = os.path.join(TMP, f"collide-{key}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(collide, f, ensure_ascii=False)
        try:
            D.load(p)
            ok(False, f"{key}={val} 让声明进入保留带却没能抛错")
        except ValueError as exc:
            ok("保留带" in str(exc), f"{key}={val} 抛错并点名保留带：{str(exc)[:80]}")

    print("〔对照 10〕两条渲染路径同一条线：JS 侧 CSS 必须报出与 Python 侧几何一致的数字")
    node = subprocess.run(
        ["node", "--input-type=module", "-e",
         "import {disclaimerCss, disclaimerHtml, disclaimerText} from "
         "'./scripts/lib/disclaimer.mjs';"
         "process.stdout.write(JSON.stringify({text: disclaimerText(), "
         "css: disclaimerCss(), html: disclaimerHtml((s) => s)}))"],
        capture_output=True, text=True, cwd=ROOT)
    ok(node.returncode == 0, f"lib/disclaimer.mjs 能被 import（{node.stderr[-200:]}）")
    if node.returncode == 0:
        js = json.loads(node.stdout)
        geo = D.geometry(CFG, (W, H))
        ok(js["text"] == D.display_text(CFG), "JS 侧文案 == Python 侧文案（同一份 json）")
        # 按 JS 的写法比：String(1.0) 是 "1" 而 repr(1.0) 是 "1.0"，是同一个数两种拼法。
        # lib 里 alpha 从 1 写成 1.0（2026-10-06 改不透明时 JSON 序列化的产物）不该把这条
        # 「两条路径同一条线」的断言判红——它问的是 CSS 有没有跟 lib 一致，不是小数点位。
        js_num = lambda v: str(int(float(v))) if float(v).is_integer() else repr(float(v))
        rgba = ", ".join(js_num(v) for v in CFG["color_rgba"])
        for frag in (f"font-size: {geo['font_size']}px", f"bottom: {geo['bottom']}px",
                     "z-index: 7", f"rgba({rgba})"):
            ok(frag in js["css"], f"CSS 里是 {frag}")
        for part in CFG["parts"]:
            ok(part in js["html"], f"HTML 里有点名「{part}」")
        ok('class="disclaimer"' in js["html"], "HTML 挂的是 .disclaimer 层")

    print(f"\n{'❌ ' + str(len(failures)) + ' 项失败' if failures else '✅ 常驻声明门对照全部通过'}")
    for f_ in failures:
        print("   · " + f_[:200])
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
