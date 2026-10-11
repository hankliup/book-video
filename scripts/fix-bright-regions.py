#!/usr/bin/env python3
"""对 grade-atmosphere-images.py 调色后仍偏亮的区域做针对性补压。

为什么需要：批量调色用固定强度的顶/底 scrim，遇到「顶部大片亮天空/亮水面」
的图仍会超过 REGION_WARN=58，白字会被吃掉。此脚本只对超标图迭代追加
scrim，直到区域亮度达标，避免整张图被压死。

用法：
  python3 scripts/fix-bright-regions.py <episode>
"""
import importlib.util
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
from episode_paths import body_images_subdir  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "grade_mod", os.path.join(HERE, "grade-atmosphere-images.py")
)
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)

WARN = G.REGION_WARN
MAX_PASS = 8


def region_lum(img, top, bottom):
    w, h = img.size
    return G.luminance(img.crop((0, int(h * top), w, int(h * bottom))))


def fix(path):
    img = Image.open(path).convert("RGB")
    changed = False
    for _ in range(MAX_PASS):
        title = region_lum(img, 0.0, 0.20)
        cap = region_lum(img, 0.70, 0.92)
        if title <= WARN and cap <= WARN:
            break
        if title > WARN:
            # 顶部再压一层：范围略收窄（0.28），强度按超出量自适应
            over = (title - WARN) / WARN
            strength = min(0.55, 0.18 + over * 0.9)
            img = G.apply_mask(img, G.build_top_scrim(img.size, end=0.28, strength=strength))
            changed = True
        if cap > WARN:
            over = (cap - WARN) / WARN
            strength = min(0.55, 0.18 + over * 0.9)
            img = G.apply_mask(img, G.build_bottom_scrim(img.size, start=0.58, strength=strength))
            changed = True
    title = region_lum(img, 0.0, 0.20)
    cap = region_lum(img, 0.70, 0.92)
    subj = region_lum(img, 0.15, 0.60)
    ok = title <= WARN and cap <= WARN
    print(
        f"  {os.path.basename(path):20} 书名区 {title:5.1f} 字幕区 {cap:5.1f}"
        f" 主体 {subj:5.1f} {'✅' if ok else '⚠️'}{' (补压)' if changed else ''}"
    )
    if changed:
        img.save(path, "PNG", optimize=True)
    return ok


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    target = sys.argv[1]
    episode_dir = target if os.path.isdir(target) else os.path.join("episodes", target)
    # 与 grade-atmosphere-images.py 共用 lib/episode_paths.py 的解析：同一个 IMAGES_SUBDIR
    # 开关、同一套正文/钩子目录名，两段口径一致。
    images_dir = os.path.join(episode_dir, body_images_subdir(episode_dir))
    if not os.path.isdir(images_dir):
        sys.exit(f"找不到图片目录：{images_dir}")
    # 选择逻辑与 grade-atmosphere-images.py 共用，避免两个脚本对 result-bridge 口径不一致
    files = sorted((f for f in os.listdir(images_dir) if G._is_atmosphere(f)), key=G._img_key)
    print(f"补压检查 {len(files)} 张 ← {images_dir}")
    ok = all(fix(os.path.join(images_dir, f)) for f in files)
    print("全部达标。" if ok else "⚠️ 仍有图未达标，建议换图。")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
