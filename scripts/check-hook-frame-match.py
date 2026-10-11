#!/usr/bin/env python3
"""钩子段氛围图上屏核对：`images-hook/` 里每张已锁的图，必须真的在成片的钩子窗里出现过。

为什么需要这一步（2026-10-03）：溯源门禁 `check-image-provenance.py` 量的是**磁盘上的文件**
——尺寸、亮度、photo id 溯源、跨期撞图。它看不见成片。而成片拼的是
`tmp/preview-<slug>/hook/renders/hook.mp4` 这个**渲好的文件**：《恶意》2026-10-01 已经证明
「改了生成器没重渲，旧像素照样上片」，八道门全绿。换图批次正好是这个缺陷的高发场景——
一次换 8 期 × 每期 1 张钩子图，只要有一期重拷了 `images-hook/` 却没重渲钩子，观众看到的
还是那张和别的期撞了的旧照片，而所有量化门禁都会说没问题。

判据是画面本身，不是文件名：把成片钩子窗等分采样若干帧，每帧和该期全部钩子图比中band
平均绝对差（MAD），取 argmin 当这一帧归属的层。要求**每张图至少被 `MIN_SAMPLES_PER_STILL`
帧认出来**。

比对必须带上**推近变换**，否则这道门在自己身上打红。钩子组合的动画是
`scale: 1.035 → 1.095~1.105` 加 ±16px 平移（`tmp/preview-*/hook/index.html`），所以成片里
第 3 秒那一帧本身就是参考图中心被放大后的一块——拿未缩放的参考图去比，自我匹配会随推近
一路变差，而对手那张只是「同样差」，MAD 差被压平。《偏跟山过不去》2026-10-03 第一次跑就
踩在这上面：换图后 hook.mp4 明明是 14:19 新渲的，1.4s/2.5s/3.6s 三帧仍被判近 tie 丢弃，
atmosphere-17 只剩 1 票而报「没上屏」。实测同一批帧（band MAD，赢家/亚军）：
未缩放 2.5s 是 27.7/33.3（差 5.6 → tie）、3.6s 是 28.8/33.3（差 4.5 → tie）；
按 1.00/1.04/1.08/1.12 放大阶梯取最小后是 17.8/30.8（差 13.0）和 20.1/30.9（差 10.8）——
两张照片本来完全不同，只是原来那口径看不见。因此参考侧取「放大阶梯 × 小平移」的最小 MAD，
让近 tie 只可能发生在真正的淡入交叉窗口里（4.8s 那一帧差 5.9，正是两段落的接缝）。

两条口径是从字幕边界压测那里学来的：
1. **近tie 不判红**。同一期两张钩子图可能同色系（都是暗窗），MAD 差几档说明不了问题，
   只说明分辨率不够。赢家与亚军的差 < `MIN_MARGIN` 时记 `inconclusive`，不点名缺失。
2. **门禁必须能证伪**。证伪靠 `--final` 换一部不相干的成片：图对不上片，就该报「没上屏」
   （实测拿《三体1·地球往事》的两张钩子图去核《三体3·死神永生》，atmosphere-19 认出 0 帧、
   退出码 1）。`--only` 只是调试用的候选收缩，**不是**证伪手段——被剔掉的那张不进候选，
   自然也不会被判缺失，拿它当反例会得到一个空转的绿灯。

用法：
  python3 scripts/check-hook-frame-match.py "<书名>" [--final 路径] [--samples N]
                                                   [--only atmosphere-N] [--json]
退出码：0 = 每张钩子图都在成片里出现过（可能有 inconclusive 提示）；1 = 有图没上屏。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from typing import Dict, List, Tuple

from PIL import Image, ImageChops

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

THUMB = (90, 120)          # 比对尺寸：推近动画会改变构图，比到这么粗就够分辨是哪张图
BAND = (0.28, 0.88)        # 只比画面中段，避开顶部常驻书名带和底部字幕带
MIN_MARGIN = 8.0           # 赢家比亚军至少好这么多才算「认出来了」，否则近 tie
EDGE_PAD = 0.30            # 钩子窗两端各留的采样余量（秒）
MIN_SAMPLES_PER_STILL = 2  # 每张图至少被几帧认出（1 帧可能正好落在淡入过渡里）
# 参考侧的变换阶梯：钩子组合把 .photo 从 scale 1.035 推到 1.095~1.105，并带 ±16px 平移，
# 所以成片帧是参考图中心被放大后的一块。720x960 → 90x120 的换算下 ±16px 就是 ±2px。
# 不在参考侧补这一刀，推近越久自我匹配越差、与对手那张的 MAD 差被压平，换过图的新成片
# 也会被误判成「旧像素没重渲」（《偏跟山过不去》2026-10-03 实测：3.6s 未缩放差 4.5，
# 带阶梯差 10.8）。
REF_ZOOMS = (1.00, 1.04, 1.08, 1.12)
PAN_OFFSETS = ((0, 0), (2, 0), (-2, 0), (0, 2), (0, -2))


def probe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
        capture_output=True, text=True).stdout
    return float(json.loads(out)["format"]["duration"])


def hook_window(ep_dir: str) -> Tuple[float, str]:
    """钩子段在成片里占 0..D。D 优先取轴文件，取不到退回配音本身。"""
    timings = os.path.join(ep_dir, "audio", "hook-timings.json")
    if os.path.isfile(timings):
        try:
            d = float(json.load(open(timings, encoding="utf-8")).get("duration") or 0)
            if d > 0:
                return d, "audio/hook-timings.json"
        except Exception:
            pass
    for cand in ("audio/hook-voiceover.mp3", "钩子.mp3"):
        p = os.path.join(ep_dir, cand)
        if os.path.isfile(p):
            return probe_duration(p), cand
    raise SystemExit(f"{ep_dir} 找不到钩子时长（hook-timings.json / hook-voiceover.mp3 / 钩子.mp3 都没有）")


def load_gray(path: str) -> Image.Image:
    return Image.open(path).convert("L").resize(THUMB)


def band(img: Image.Image) -> Image.Image:
    top = int(THUMB[1] * BAND[0])
    bottom = int(THUMB[1] * BAND[1])
    return img.crop((0, top, THUMB[0], bottom))


def band_variants(img: Image.Image) -> List[Image.Image]:
    """一张参考图在成片里可能长成的样子：中心放大各档 + 小平移，取各自的中band。"""
    w, h = THUMB
    out: List[Image.Image] = []
    for z in REF_ZOOMS:
        cw, ch = max(2, int(round(w / z))), max(2, int(round(h / z)))
        left, top = (w - cw) // 2, (h - ch) // 2
        zoomed = img.crop((left, top, left + cw, top + ch)).resize((w, h), Image.LANCZOS)
        b = band(zoomed)
        for dx, dy in PAN_OFFSETS:
            out.append(ImageChops.offset(b, dx, dy).crop((0, 0, b.width, b.height)))
    return out


def best_mad(frame_band: Image.Image, variants: List[Image.Image]) -> float:
    n = (frame_band.width * frame_band.height) or 1
    best = 1e9
    for ref_band in variants:
        hist = ImageChops.difference(frame_band, ref_band).histogram()
        v = sum(i * hist[i] for i in range(256)) / n
        if v < best:
            best = v
    return best


def frame_at(video: str, t: float) -> Image.Image:
    tmp = tempfile.mktemp(suffix=".png")
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", video,
                        "-frames:v", "1", "-y", tmp], check=True)
        return load_gray(tmp)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("--final", default="")
    ap.add_argument("--samples", type=int, default=0, help="默认 每张图 3 帧、下限 8")
    ap.add_argument("--only", action="append", default=[],
                    help="只把指定图名当候选（自证伪用：其余图必然报没上屏）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    ep_dir = args.episode if os.path.isdir(args.episode) else os.path.join(ROOT, "episodes", args.episode)
    name = os.path.basename(ep_dir.rstrip("/"))
    final = args.final or os.path.join(ep_dir, "renders", f"{name}-final.mp4")
    if not os.path.isfile(final):
        raise SystemExit(f"找不到成片：{final}")

    still_dir = os.path.join(ep_dir, "images-hook")
    if not os.path.isdir(still_dir):
        still_dir = os.path.join(ep_dir, "images")
    stills = sorted(f for f in os.listdir(still_dir)
                    if f.lower().endswith((".png", ".jpg", ".jpeg")) and not f.startswith("."))
    if args.only:
        stills = [s for s in stills if os.path.splitext(s)[0] in set(args.only)]
    if not stills:
        raise SystemExit(f"{still_dir} 里没有钩子氛围图，这个核对没有意义")

    dur, src = hook_window(ep_dir)
    n = args.samples or max(8, 3 * len(stills))
    lo, hi = EDGE_PAD, max(EDGE_PAD + 0.2, dur - EDGE_PAD)
    times = [lo + (hi - lo) * i / (n - 1) for i in range(n)]

    refs = {os.path.splitext(s)[0]: band_variants(load_gray(os.path.join(still_dir, s)))
            for s in stills}
    votes: Dict[str, List[float]] = {k: [] for k in refs}
    inconclusive: List[float] = []
    for t in times:
        fb = band(frame_at(final, t))
        scores = sorted(((best_mad(fb, variants), k) for k, variants in refs.items()))
        best_score, best = scores[0]
        runner = scores[1][0] if len(scores) > 1 else best_score + MIN_MARGIN * 2
        if runner - best_score >= MIN_MARGIN:
            votes[best].append(t)
        else:
            inconclusive.append(t)

    missing = [k for k, v in votes.items() if len(v) < MIN_SAMPLES_PER_STILL]
    payload = {
        "episode": name, "final": final, "hookSeconds": round(dur, 2), "durationFrom": src,
        "stills": {k: {"hits": len(v), "at": [round(x, 2) for x in v]} for k, v in votes.items()},
        "inconclusive": [round(x, 2) for x in inconclusive],
        "missing": missing,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"钩子图上屏核对 ｜ 《{name}》 钩子窗 0–{dur:.2f}s（{src}）"
              f" ｜ 采样 {n} 帧 ｜ 候选 {len(refs)} 张")
        for k in sorted(refs):
            at = ", ".join(f"{x:.1f}s" for x in votes[k]) or "—"
            flag = "❌" if k in missing else "✅"
            print(f"  {flag} {k:<16s} 认出 {len(votes[k]):2d} 帧  {at}")
        if inconclusive:
            print(f"  · 近 tie（赢家与亚军 MAD 差 < {MIN_MARGIN}）不计票：{len(inconclusive)} 帧 "
                  f"@ {', '.join(f'{x:.1f}s' for x in inconclusive[:6])}")
        if missing:
            print(f"未通过：{len(missing)} 张已锁钩子图在成片钩子窗里被认出的帧数不足"
                  f"（需要 ≥{MIN_SAMPLES_PER_STILL} 帧）→ "
                  + "、".join(f"{k}（认出 {len(votes[k])} 帧）" for k in missing)
                  + "。多半是换图后没重渲 hook.mp4，也可能是两张钩子图太像、采样全落在近 tie 上"
                    "（看上面那行的近 tie 计数）")
        else:
            print("钩子图核对：全部通过")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
