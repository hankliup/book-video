#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐条字幕亮度审计：找出「白字压亮图」这种技术上全绿的缺陷。

为什么需要：`check-caption-punctuation.py` 只看标点，`qc-caption-alignment.py` 只看对位，
`qc-final-av.py` 只看音画——字幕零标点、对齐正确，压在雾坡/日落上照样读不出来。
《当代英雄》v1 就是这样：所有门禁通过，两条字幕分别压在亮度 203/205 的绿崖雾坡上几乎不可读。
这道审计把「能不能读」变成一个数：对**每条**字幕取其中帧，量字幕带的亮度中位数。

判据（经验阈值，来自 2026-09-27 起的期数对照）：
  - 字幕带 y760–930 中位数 >162 → 真缺陷，必须换图或压暗（《当代英雄》换完 160.3 达标）
  - **两行字幕另量首行带 y660–770，同一 162 阈值**（见下方 CAPTION_UPPER_BAND 注释）
  - 常驻书名区 y60–210 中位数 >175 → 只告警：书名是白字加纯黑投影，223 目视仍可读

时间轴：`caption-track.json` 是**正文**时间轴，成片时间轴在它后面，所以取帧必须再加
`--intro-trim`（默认 3.0s）。漏加偏移会把片头抽帧当成第 1 条字幕，报出「band 254」的假缺陷。

用法：
  python3 scripts/check-caption-legibility.py episodes/<书名>/renders/<书名>-final.mp4 \\
      episodes/<书名>/caption-track.json [--intro-trim 3.0] [--json out.json]

退出码：0 = 字幕区全部达标；1 = 有字幕压亮图（列出来）；2 = 参数/素材不对。
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np

CAPTION_BAND = (760, 930)     # 成片像素 y 区间：字幕实际落在这里
CAPTION_UPPER_BAND = (660, 770)  # 两行字幕的**首行**落在这里，单带口径量不到
TITLE_BAND = (60, 210)        # 常驻《书名》/作者浮层
CAP_LIMIT = 162.0             # 字幕区中位数上限：超过即判缺陷
TITLE_LIMIT = 175.0           # 书名区中位数上限：超过只告警

# 为什么必须补上带：CAPTION_BAND 是「一条字幕只有一行」时代定的。line-break@2026-09-27 之后
# 一条字幕可到 2 视觉行（渲染器 bottom 126px、line_h=round(size*1.16)），首行落在
#   56px → y704–769，50px → y718–776
# 只量 y760–930 等于**只量第二行**：首行压在雾坡/日落上照样报绿。《教父》62/97 条是两行，
# 补量上带最大 74.7（阈值 162），《当代英雄》71.9、《病隙碎笔》65.6，扩带不会点亮已交付期数。


def mid_frame_gray(video, t):
    """取成片 t 秒处的一帧，返回 (H, W) 亮度数组。"""
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", video,
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True)
    if not p.stdout:
        raise SystemExit(f"抽帧失败 t={t:.2f}s：{p.stderr.decode('utf8', 'replace')[-300:]}")
    side = len(p.stdout) // 3 // 960
    if side != 720:
        raise SystemExit(f"预期 720x960，实际每帧宽 {side}px（本期不是竖屏 3:4？）")
    arr = np.frombuffer(p.stdout, dtype=np.uint8).reshape(960, 720, 3)
    return arr @ np.array([0.299, 0.587, 0.114])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("caption_track")
    ap.add_argument("--intro-trim", type=float, default=3.0,
                    help="caption-track 是正文时间轴，成片要加这个偏移")
    ap.add_argument("--cap-limit", type=float, default=CAP_LIMIT)
    ap.add_argument("--title-limit", type=float, default=TITLE_LIMIT)
    ap.add_argument("--json", dest="json_out")
    a = ap.parse_args()

    if not os.path.exists(a.video):
        raise SystemExit(f"成片不存在：{a.video}")
    if not os.path.exists(a.caption_track):
        raise SystemExit(f"caption-track 不存在：{a.caption_track}")

    caps = json.load(open(a.caption_track, encoding="utf-8"))
    if isinstance(caps, dict):
        caps = caps.get("captions") or caps.get("pieces") or []
    if not caps:
        raise SystemExit("caption-track 里没有字幕条目")

    rows = []
    for c in caps:
        t = (c["start"] + c["end"]) / 2 + a.intro_trim
        g = mid_frame_gray(a.video, t)
        disp = (c.get("display") or "")
        n_lines = len([x for x in disp.replace("\n", " / ").split(" / ") if x.strip()])
        row = {
            "order": c.get("order"),
            "film_seconds": round(t, 2),
            "lines": n_lines,
            "title_band": float(np.median(g[TITLE_BAND[0]:TITLE_BAND[1], :])),
            "caption_band": float(np.median(g[CAPTION_BAND[0]:CAPTION_BAND[1], :])),
            "display": disp[:24],
        }
        if n_lines > 1:
            row["upper_band"] = float(np.median(
                g[CAPTION_UPPER_BAND[0]:CAPTION_UPPER_BAND[1], :]))
        rows.append(row)

    cap_max = max(r["caption_band"] for r in rows)
    upper_rows = [r for r in rows if "upper_band" in r]
    upper_max = max((r["upper_band"] for r in upper_rows), default=0.0)
    title_max = max(r["title_band"] for r in rows)
    bad = [r for r in rows if r["caption_band"] > a.cap_limit]
    bad += [r for r in upper_rows if r["upper_band"] > a.cap_limit]
    warn = [r for r in rows if r["title_band"] > a.title_limit]

    print(f"{len(rows)} 条字幕（其中 {len(upper_rows)} 条为多行），"
          f"成片时间轴 = 正文 + {a.intro_trim}s 片头偏移")
    print(f"字幕带 y{CAPTION_BAND[0]}–{CAPTION_BAND[1]} median 最大 {cap_max:.1f}（阈值 {a.cap_limit}）")
    if upper_rows:
        print(f"首行带 y{CAPTION_UPPER_BAND[0]}–{CAPTION_UPPER_BAND[1]} median 最大 "
              f"{upper_max:.1f}（阈值 {a.cap_limit}，只量多行字幕）")
    else:
        print(f"首行带：本期无多行字幕，跳过")
    print(f"书名带 y{TITLE_BAND[0]}–{TITLE_BAND[1]} median 最大 {title_max:.1f}（阈值 {a.title_limit}，仅告警）")
    for r in bad:
        band = "首行带" if r.get("upper_band", 0) > a.cap_limit else "字幕带"
        val = r.get("upper_band") if band == "首行带" else r["caption_band"]
        print(f"   ❌ 第 {r['order']} 条 @{r['film_seconds']:.2f}s {band} {val:.1f}  {r['display']}")
    for r in warn:
        print(f"   ⚠️ 第 {r['order']} 条 @{r['film_seconds']:.2f}s 书名带 {r['title_band']:.1f}  "
              f"{r['display']}")
    if a.json_out:
        json.dump({"video": a.video, "intro_trim": a.intro_trim,
                   "caption_band": CAPTION_BAND, "upper_band": CAPTION_UPPER_BAND,
                   "title_band": TITLE_BAND,
                   "cap_max": round(cap_max, 1), "upper_max": round(upper_max, 1),
                   "title_max": round(title_max, 1),
                   "cap_limit": a.cap_limit, "title_limit": a.title_limit,
                   "failures": bad, "warnings": warn, "rows": rows},
                  open(a.json_out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    if bad:
        print(f"❌ {len(bad)} 条字幕压在亮图上，白字读不出来 —— 换图（或 fix-bright-regions.py 压暗）后重渲")
        return 1
    print(f"✅ 全部字幕可读（字幕区最大 {cap_max:.1f}"
          + (f"／首行区最大 {upper_max:.1f}" if upper_rows else "")
          + f" ≤ {a.cap_limit}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
