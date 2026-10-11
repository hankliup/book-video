#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「已经印在旧封面上、却没进 brief.json」的钩子行落进记录，并顺手复核转写。

为什么需要这一步：历史期的 COVER_HOOK 当年是渲染时从命令行临时传的，从没落盘。
只查 brief.json 会得出「已交付封面都没有钩子行」的错误结论，而重跑 make_episode_cover.py
就会把当期实际发出去的文案悄悄抹掉。所以先把存量认下来，再决定换不换。

复核不是走过场：转写是人眼读封面裁出来的，读错一个字没人发现。这里用「亮像素横向跨度 ≈
字数 × 字高」这一条几何关系反查——CJK 字是方的，多一个字少一个字都会把比值推出容差。
对不上就是转写错了或者封面根本不是那张，直接判红，不写 brief.json。

用法：
  python3 scripts/record-legacy-cover-hooks.py            # 复核 + 写入 brief.json
  python3 scripts/record-legacy-cover-hooks.py --check    # 只复核，不写盘
"""
import json
import os
import re
import sys

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "封面钩子-历史存量.json")
# 与 scripts/make_episode_cover.py 的 HOOK_CHARS 同源，别手抄数字
_cover_src = open(os.path.join(ROOT, "scripts", "make_episode_cover.py"), encoding="utf-8").read()
m = re.search(r"^HOOK_CHARS\s*=\s*(\d+)", _cover_src, re.M)
HOOK_CHARS = int(m.group(1)) if m else 22

# 旧版式钩子行所在的横向文字带（底部式 1600-1700，顶部式沉底 1690-1760）
BAND = (1590, 1770)
RATIO_LO, RATIO_HI = 0.78, 1.18


def measure(path):
    """返回 (横向跨度 px, 带高 px)；找不到文字带返回 (None, None)。"""
    a = np.asarray(Image.open(path).convert("L"), dtype=np.float32)
    y0, y1 = BAND
    band = a[y0:y1] > 200
    rows = band.sum(axis=1)
    keep = rows >= max(12, a.shape[1] * 0.012)
    if not keep.any():
        return None, None
    ys = np.where(keep)[0]
    sub = band[ys.min():ys.max() + 1]
    cols = np.where(sub.any(axis=0))[0]
    return int(cols.max() - cols.min() + 1), int(ys.max() - ys.min() + 1)


def main():
    check_only = "--check" in sys.argv
    data = json.load(open(DATA, encoding="utf-8"))
    hooks = data["hooks"]
    written, kept, reds = 0, 0, []
    for name, line in sorted(hooks.items()):
        cover = os.path.join(ROOT, "episodes", name, "renders", f"封面-{name}.png")
        bp = os.path.join(ROOT, "episodes", name, "brief.json")
        if not os.path.exists(cover):
            reds.append(f"{name}: 找不到 {os.path.relpath(cover, ROOT)}，转写无从复核")
            continue
        if not os.path.exists(bp):
            reds.append(f"{name}: 没有 brief.json")
            continue
        span, height = measure(cover)
        if span is None:
            reds.append(f"{name}: 封面 y={BAND[0]}-{BAND[1]} 区间没有文字带，本表记的钩子行对不上图")
            continue
        ratio = span / (len(line) * height)
        flag = "" if RATIO_LO <= ratio <= RATIO_HI else "  <-- 跨度/字数不符"
        print(f"{name:22s} {len(line):2d}字 带高{height:3d}px 跨度{span:4d}px 比值{ratio:.2f}{flag}")
        if not (RATIO_LO <= ratio <= RATIO_HI):
            reds.append(f"{name}: 几何复核不过（比值 {ratio:.2f}，容差 {RATIO_LO}-{RATIO_HI}），"
                        f"转写「{line}」与封面文字带不匹配")
            continue
        brief = json.load(open(bp, encoding="utf-8"))
        if brief.get("cover_hook"):
            kept += 1
            print(f"    已有本期审批过的 cover_hook，历史存量只作备注保留")
        if check_only:
            continue
        if brief.get("cover_hook_legacy") == line:
            continue
        brief["cover_hook_legacy"] = line
        brief["cover_hook_legacy_note"] = (
            f"旧版式封面（{BAND[0]}-{BAND[1]} 文字带，字高 {height}px）上实际印着的钩子行，"
            f"当年从命令行传入未落盘，2026-10-02 目检转写并做几何复核。"
            f"回补批到本期时先读这行，再决定沿用、改写还是另起。")
        with open(bp, "w", encoding="utf-8") as fh:
            json.dump(brief, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        written += 1
    over = [n for n, l in hooks.items() if len(l) > HOOK_CHARS]
    print(f"\n历史存量 {len(hooks)} 期 | 写入 {written} | 已有新审批 {kept} | 判红 {len(reds)}")
    if over:
        print(f"其中 {len(over)} 行超过现行封面钩子上限 {HOOK_CHARS} 字，换用前必须改写："
              f"{'、'.join(over)}")
    for r in reds:
        print("RED:", r)
    return 1 if reds else 0


if __name__ == "__main__":
    sys.exit(main())
