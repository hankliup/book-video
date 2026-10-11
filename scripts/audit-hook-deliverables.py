#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘点 episodes/ 下每一期的三段结构交付物是否齐全。

只读盘点，不改任何文件。输出 TSV，便于后续按缺口分组处理。
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EP = os.path.join(ROOT, "episodes")

SKIP_DIRS = {"README.md"}


def dur(path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True)
        return round(float(r.stdout.strip()), 2)
    except Exception:
        return None


def list_ep():
    out = []
    for name in sorted(os.listdir(EP)):
        p = os.path.join(EP, name)
        if not os.path.isdir(p):
            continue
        if name in SKIP_DIRS or name.startswith("."):
            continue
        out.append(name)
    return out


def main():
    rows = []
    for name in list_ep():
        d = os.path.join(EP, name)
        files = os.listdir(d)
        # 钩子文案
        hook_csv = os.path.exists(os.path.join(d, "script-hook.csv"))
        # 钩子配音
        hook_vo = None
        for cand in ("audio/hook-voiceover-story.mp3", "audio/hook-voiceover.mp3",
                     "钩子配音.mp3", "audio/钩子配音.mp3"):
            fp = os.path.join(d, cand)
            if os.path.exists(fp):
                hook_vo = cand
                break
        # 钩子氛围图
        hook_img_dir = os.path.isdir(os.path.join(d, "images-hook"))
        hook_img_n = 0
        if hook_img_dir:
            hook_img_n = len([f for f in os.listdir(os.path.join(d, "images-hook"))
                              if f.lower().endswith((".png", ".jpg", ".jpeg"))])
        # 正文脚本 / 正文配音
        body_csv = os.path.exists(os.path.join(d, "script-body.csv")) or os.path.exists(
            os.path.join(d, "script-body.csv"))
        body_vo = None
        for cand in ("audio/body-voiceover-story.mp3", "audio/body-voiceover.mp3",
                     "正文配音.mp3", "配音.mp3"):
            fp = os.path.join(d, cand)
            if os.path.exists(fp):
                body_vo = cand
                break
        # 封面
        rnd = os.path.join(d, "renders")
        cover = None
        final = None
        others = []
        if os.path.isdir(rnd):
            for f in sorted(os.listdir(rnd)):
                low = f.lower()
                if f.startswith(".") or "uploading" in low:
                    continue
                if low.endswith((".png", ".jpg", ".jpeg")) and "封面" in f:
                    cover = f
                elif low.endswith(".mp4") and "final" in low:
                    final = f
                elif low.endswith((".mp4", ".mov")):
                    others.append(f)
        # brief 里的钩子设计字段
        brief = {}
        bp = os.path.join(d, "brief.json")
        if os.path.exists(bp):
            try:
                brief = json.load(open(bp, encoding="utf-8"))
            except Exception:
                brief = {"_error": True}
        cover_hook = brief.get("cover_hook") or brief.get("hook") or ""
        # 钩子 composition
        slug_hint = ""
        hook_mp4 = None
        tdir = os.path.join(ROOT, "tmp")
        if os.path.isdir(tdir):
            for t in os.listdir(tdir):
                if name in t or re.sub(r"[·\s]", "", name) in re.sub(r"[·\s]", "", t):
                    cand = os.path.join(tdir, t, "hook", "renders", "hook.mp4")
                    if os.path.exists(cand):
                        hook_mp4 = os.path.relpath(cand, ROOT)
                        slug_hint = t
                        break
        rows.append({
            "book": name,
            "hook_csv": "Y" if hook_csv else "-",
            "hook_vo": hook_vo or "-",
            "hook_imgs": (f"{hook_img_dir and 'Y'}/{hook_img_n}") if hook_img_dir else "-",
            "hook_mp4": hook_mp4 or "-",
            "cover": cover or "-",
            "final": final or "-",
            "final_dur": dur(os.path.join(rnd, final)) if final else "",
            "body_vo": body_vo or "-",
            "cover_hook_field": ("Y" if cover_hook else "-"),
            "other_render": ";".join(others) if others else "",
        })

    hdr = list(rows[0].keys()) if rows else []
    print("\t".join(hdr))
    for r in rows:
        print("\t".join(str(r[h]) for h in hdr))


if __name__ == "__main__":
    main()
