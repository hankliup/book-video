#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对单张氛围图追加一道底部压暗，补 fix-bright-regions.py 量不到的字幕首行带。

为什么需要：check-caption-legibility.py 量两条带（y760–930 和两行字幕的首行带 y660–770），
而 fix-bright-regions.py 只压源图 0.70–0.92 与 0.00–0.20 两段。中间隔着渲染器的浮点取景
（cover × z + drift_y_px），首行带落在源图 0.64–0.79，正好压在两段之间的空档上。
《秋园》atmosphere-21 第 87 条「一九八〇年 / 从湖北回湖南」首行带 163.0（阈值 162）就是这么漏的：
fix-bright-regions 报「字幕区 47.4 ✅」，成片上白字压在纸条下半张白纸上。

补压参数不能凭手感：先用 tmp 里的投影探针把成片带映射回源图行，再选 build_bottom_scrim 的
start / strength，让**最坏那条字幕**的带落到阈值以下且留有余量。本脚本只负责把选定的那道
scrim 落到图上并记账，不负责替你选参数。

叠加性是这脚本的主要风险（见 grade-atmosphere-images.py 的同类坑）：scrim 是原地往黑里压，
对同一张图跑两遍就压两遍，而两道门禁都不会变红。所以每次补压都记进 images/_pressed.json，
默认拒绝重复施加同一道，要重做先 --restore 到 _raw/ + grade + fix-bright-regions 的链上。

用法：
  python3 scripts/press-caption-band.py <episode> --image atmosphere-21 --start 0.35 --strength 0.80
  python3 scripts/press-caption-band.py <episode> --list          # 只看账本
"""
import argparse
import importlib.util
import json
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "grade_mod", os.path.join(HERE, "grade-atmosphere-images.py"))
G = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(G)

MARKER = "_pressed.json"


def region_lum(img, top, bottom):
    w, h = img.size
    return G.luminance(img.crop((0, int(h * top), w, int(h * bottom))))


def report(path, label):
    img = Image.open(path).convert("RGB")
    print(f"  {label:8} 书名区 {region_lum(img, 0.0, 0.20):5.1f}"
          f"  首行带区 {region_lum(img, 0.62, 0.80):5.1f}"
          f"  字幕区 {region_lum(img, 0.70, 0.92):5.1f}"
          f"  主体 {region_lum(img, 0.15, 0.60):5.1f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("--image")
    ap.add_argument("--start", type=float)
    ap.add_argument("--strength", type=float)
    ap.add_argument("--note", default="", help="为什么补压（写进门禁读不到的地方，至少写进账本）")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--force", action="store_true", help="允许对同一张图重复施加同一道 scrim")
    a = ap.parse_args()

    images_dir = os.path.join("episodes", a.episode, "images")
    if not os.path.isdir(images_dir):
        sys.exit(f"找不到图片目录：{images_dir}")
    marker = os.path.join(images_dir, MARKER)
    log = json.load(open(marker, encoding="utf-8")) if os.path.exists(marker) else {}

    if a.list:
        print(json.dumps(log, ensure_ascii=False, indent=1) or "{}")
        return
    if not (a.image and a.start is not None and a.strength is not None):
        sys.exit(__doc__)

    path = os.path.join(images_dir, a.image if a.image.endswith(".png") else f"{a.image}.png")
    if not os.path.exists(path):
        sys.exit(f"图不存在：{path}")
    key = os.path.basename(path)
    want = {"start": a.start, "strength": a.strength}
    done = [e for e in log.get(key, []) if e["params"] == want]
    if done and not a.force:
        print(f"{key} 已经压过这一道（{done[0]['params']}，{done[0]['when']}），"
              f"scrim 是原地叠加的，再跑一遍会压两遍。确认要叠加就加 --force。")
        return

    report(path, "补压前")
    img = Image.open(path).convert("RGB")
    img = G.apply_mask(img, G.build_bottom_scrim(img.size, start=a.start, strength=a.strength))
    img.save(path, "PNG", optimize=True)
    report(path, "补压后")

    import datetime
    log.setdefault(key, []).append(
        {"params": want, "when": datetime.date.today().isoformat(), "note": a.note})
    json.dump(log, open(marker, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"记账 → {marker}")


if __name__ == "__main__":
    main()
