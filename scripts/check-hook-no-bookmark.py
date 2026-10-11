#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""钩子段不得压常驻《书名》/ 作者层 —— 交付门禁第八道。

规则本身早就写进 AGENTS.md（"The persistent 《书名》 + author overlay belongs to the body
segment only"）和生成器（`create-episode-preview.mjs` 的 `showBookMark: false`），
可《恶意》2026-10-01 首版还是把书名层带进了钩子段：成片拼的是
`tmp/preview-<slug>/hook/renders/hook.mp4`，那是**改标志之前**渲的旧文件，
生成器说没有、画面上有，七道门禁全绿。所以这道门只看成片像素，不信任何上游声明。

判据：拿正文段常驻书名层的真实几何（渲染中间产物 title.png 的 alpha）当模板，逐帧算

    contrast = 字形像素里「亮(≥200 灰阶)」的比例 − 书名外接框内非字形像素里「亮」的比例

  · 钩子窗（0 .. 钩子配音末尾）任一点 contrast > HOOK_MAX → 红：钩子画面上压着书名层；
  · 正文窗至少一点 contrast ≥ BODY_MIN（阳性对照）→ 否则这道门是空转的，同样判红。
    正文都没有书名层，只可能是模板/几何没对上，而「空转的绿灯」比红灯危险。

为什么做差而不是只看字形亮度：钩子第一张氛围图可能整片是亮的（夜市长明的灯、雪地、天空），
单看字形会误报；「字形亮 + 字缝暗」这个反差才是叠层文字独有的结构，整块都亮时差值仍然小。
实测《恶意》：带叠层的旧钩子帧 contrast=+0.585，去掉叠层的新钩子帧 −0.018~0.000，
正文帧 +0.838~+0.848。

另外两条只警告不判红的线索（它们解释「为什么会有旧文件」，但结论仍以像素为准）：
  · 钩子 composition 的 HTML 里出现 book-mark / book-title → 生成器侧的回归；
  · hook.mp4 比 hook/index.html 旧 → 本期钩子画面是上一版 composition 渲的。

用法：
  python3 scripts/check-hook-no-bookmark.py "<书名>" [--tmp <渲染中间目录>]
      [--final <成片.mp4>] [--intro-trim 1.83]
退出码：0 通过；1 钩子段带了书籍信息（或这道门空转）；2 缺文件，无从判。
"""
import argparse
import hashlib
import os
import re
import subprocess
import sys
import unicodedata

from PIL import Image
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

HOOK_MAX = float(os.environ.get("HOOK_OVERLAY_MAX", "0.20"))   # 钩子窗允许的字形反差上限
BODY_MIN = float(os.environ.get("BODY_OVERLAY_MIN", "0.40"))   # 正文窗的阳性对照下限
BRIGHT = 200                                                   # 「亮」的灰阶门槛
N_HOOK = 5
N_BODY = 3


def episode_slug(name):
    """与 scripts/lib/episode-slug.mjs / render_scene_map_template_fallback.py 同一套算法：
    预览目录名三边必须一致，否则这道门会去看别期的钩子。"""
    ascii_part = re.sub(r"[^A-Za-z0-9_.-]+", "-", unicodedata.normalize("NFKD", name)).strip("-").lower()
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
    return f"{ascii_part}-{digest}" if ascii_part else f"book-{digest}"


def probe_dur(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", path],
                       capture_output=True, text=True)
    return float(r.stdout.strip())


def grab(video, t, out):
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1", out], check=True)


def glyph_masks(title_png):
    """书名层的字形掩码 + 「框内非字形」对照掩码（都按 720x960 成片画幅）。"""
    a = np.array(Image.open(title_png).convert("RGBA"))[..., 3]
    glyph = a >= 200
    ys, xs = np.nonzero(glyph)
    if not len(ys):
        return None, None
    box = np.zeros_like(glyph)
    y0, y1 = max(0, ys.min() - 10), min(glyph.shape[0], ys.max() + 11)
    x0, x1 = max(0, xs.min() - 10), min(glyph.shape[1], xs.max() + 11)
    box[y0:y1, x0:x1] = True
    return glyph, box & ~glyph


def contrast(frame, glyph, ctrl):
    im = Image.open(frame).convert("L")
    if im.size != (glyph.shape[1], glyph.shape[0]):
        im = im.resize((glyph.shape[1], glyph.shape[0]))
    lum = np.asarray(im, dtype=np.uint8)
    return (lum[glyph] >= BRIGHT).mean() - (lum[ctrl] >= BRIGHT).mean()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("--final", default="")
    ap.add_argument("--tmp", default="")
    ap.add_argument("--intro-trim", type=float, default=float(os.environ.get("INTRO_TRIM_SECONDS", "3.0")))
    args = ap.parse_args()

    ep_dir = os.path.join(ROOT, "episodes", args.episode)
    if not os.path.isdir(ep_dir):
        print(f"❌ episode 目录不存在：{ep_dir}")
        return 2
    final = args.final or os.path.join(ep_dir, "renders", f"{args.episode}-final.mp4")
    if not os.path.exists(final):
        print(f"❌ 找不到成片：{final}")
        return 2

    hook_voice = os.path.join(ep_dir, "audio", "hook-voiceover-story.mp3")
    if not os.path.exists(hook_voice):
        print(f"ℹ️ 没有钩子配音（{hook_voice}）：本期是两段结构，无钩子段可查")
        print("✅ 钩子段书名层检查通过（不适用）")
        return 0
    hook_dur = round(probe_dur(hook_voice), 2)
    pre_body = round(hook_dur + args.intro_trim, 2)

    tmp = args.tmp or next((d for d in (os.environ.get("FALLBACK_TMP"),
                                        os.path.join(ROOT, "tmp", f"{args.episode}_tpl"),
                                        f"/tmp/{args.episode}_tpl")
                            if d and os.path.isdir(d)), None)
    title_png = os.path.join(tmp, "title.png") if tmp else ""
    if not title_png or not os.path.exists(title_png):
        print(f"❌ 找不到正文书名层模板 {title_png}：这道门需要渲染中间产物（--tmp 指定），"
              f"没有模板就等于没检查")
        return 2
    glyph, ctrl = glyph_masks(title_png)
    if glyph is None:
        print(f"❌ {title_png} 里没有字形像素，模板是空的")
        return 2

    work = os.path.join("/tmp", f"hook-bookmark-{args.episode}")
    os.makedirs(work, exist_ok=True)
    dur = probe_dur(final)
    hook_pts = [round(hook_dur * k / (N_HOOK + 1) + 0.15, 3) for k in range(1, N_HOOK + 1)]
    body_span = max(1.0, dur - pre_body - 2.0)
    body_pts = [round(pre_body + 1.0 + body_span * k / (N_BODY + 1), 3) for k in range(1, N_BODY + 1)]

    print(f"episode={args.episode}  成片 {dur:.2f}s  钩子 {hook_dur:.2f}s  正文起点 {pre_body:.2f}s")
    print(f"模板 {os.path.relpath(title_png, ROOT)}：字形 {int(glyph.sum())}px / 对照 {int(ctrl.sum())}px")
    print(f"判据：钩子窗反差 ≤{HOOK_MAX:.2f}（>则钩子压了书名层）  正文窗至少一点 ≥{BODY_MIN:.2f}（阳性对照）\n")

    rows, bad = [], []
    for label, pts in (("钩子", hook_pts), ("正文", body_pts)):
        for i, t in enumerate(pts, 1):
            f = os.path.join(work, f"{label}{i}_{t}.png")
            grab(final, t, f)
            c = contrast(f, glyph, ctrl)
            rows.append((label, t, c))
            if label == "钩子" and c > HOOK_MAX:
                bad.append(f"钩子 {t:.2f}s 画面上有常驻书名层（反差 {c:+.3f} > {HOOK_MAX}）")
    for label, t, c in rows:
        print(f"  {label} {t:7.2f}s  contrast={c:+.3f}")

    body_max = max((c for lb, _, c in rows if lb == "正文"), default=-1.0)
    if body_max < BODY_MIN:
        bad.append(f"正文段也没测到书名层（最大反差 {body_max:+.3f} < {BODY_MIN}）："
                   f"模板或几何没对上，这道门在空转")

    prev = os.path.join(ROOT, "tmp", f"preview-{episode_slug(args.episode)}", "hook")
    html = os.path.join(prev, "index.html")
    mp4 = os.path.join(prev, "renders", "hook.mp4")
    if os.path.exists(html):
        if re.search(r"book-mark|book-title", open(html, encoding="utf-8").read()):
            print("\n⚠️ 钩子 composition 里仍有 book-mark/书名层 —— create-episode-preview.mjs 侧的回归")
    if os.path.exists(html) and os.path.exists(mp4) and os.path.getmtime(mp4) < os.path.getmtime(html):
        print("⚠️ hook.mp4 比 hook/index.html 旧：成片拼的是上一版 composition 渲的钩子画面，重渲钩子段")

    if bad:
        for b in bad:
            print(f"   ❌ {b}")
        print("\n❌ 钩子段不得压《书名》/ 作者，不可交付")
        return 1
    print("\n✅ 钩子段没有书籍信息层，正文段的常驻书名层在位（第八道门通过）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
