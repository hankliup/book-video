#!/usr/bin/env python3
"""Pexels 氛围图统一调色：亮度归一 + 去饱和 + 暗角 + 上下压暗。

为什么需要这一步：
  Pexels 检索到的图来自不同摄影师，亮度/色温差异极大（实测同一批图 lum 25~107），
  直接成片会在场景切换时明显跳色。
  更重要的是：body 模板没有暗角/遮罩层，字幕是纯白 56px + text-shadow，
  书名/作者是常驻浮层（.book-mark 全程 opacity:1）。亮的图会把白字吃掉。

做法：
  1. 亮度线性归一到 [LUM_MIN, LUM_MAX] 目标带（带内不动）
  2. 轻微去饱和，压掉 Pexels 常见的过艳色调
  3. 径向暗角（四角最重）
  4. 顶部压暗（承载常驻书名） + 底部压暗（承载字幕）

自检指标（每帧打印，超阈值打 ⚠️）：
  - 书名区 = 顶部 0~20%     目标 < 58
  - 字幕区 = 70%~92%        目标 < 58
  - 主体区 = 15%~60%        仅报告，用于确认画面没被压死

用法：
  python3 scripts/grade-atmosphere-images.py <episode-dir-or-name> [--dry]

  # 例
  python3 scripts/grade-atmosphere-images.py episodes/变形记 --dry
  python3 scripts/grade-atmosphere-images.py 变形记

原始未调色图会自动备份到 <images>/_raw/，可随时重跑。
需要 Pillow（系统 python 通常已装：/opt/homebrew/bin/python3）。
"""
import os
import sys

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageStat

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from episode_paths import body_images_subdir  # noqa: E402

# 目标亮度带（0-255 灰度加权亮度）
LUM_MIN, LUM_MAX = 46.0, 78.0
# 轻微去饱和：向灰度靠拢的比例
DESATURATE = 0.12
# 四角暗角最大强度
VIGNETTE_MAX = 0.28
# 底部压暗：起始高度 / 最大强度（字幕区）
SCRIM_START = 0.52
SCRIM_MAX = 0.58
# 顶部压暗：衰减到 0 的高度 / 最大强度（常驻书名区）
TOP_SCRIM_END = 0.34
TOP_SCRIM_MAX = 0.62
# 自检阈值：区域亮度超过该值会打 ⚠️
REGION_WARN = 58


def _is_atmosphere(f):
    """正文氛围图 + 片头结果桥接图。

    result-bridge 同时是片头定格页和正文首帧，书名区压不达标白字照样被吃掉
    （《爱玛》实测 196.2），只挑 atmosphere-* 会把它整个漏掉。
    """
    return f.endswith(".png") and (f.startswith("atmosphere-") or f == "result-bridge.png")


def _img_key(f):
    """atmosphere-N 按序号排，result-bridge 排在最前。"""
    if f.startswith("atmosphere-"):
        return (1, int(f.split("-")[1].split(".")[0]), f)
    return (0, 0, f)


def luminance(img):
    r, g, b = ImageStat.Stat(img.convert("RGB")).mean
    return 0.299 * r + 0.587 * g + 0.114 * b


def normalize_brightness(img, target_min=LUM_MIN, target_max=LUM_MAX):
    """线性提亮/压暗，把亮度拉进目标带；带内原样返回。"""
    lum = luminance(img)
    if target_min <= lum <= target_max:
        return img, lum, lum
    target = target_min if lum < target_min else target_max
    # 乘性调整会同时抬升暗部噪声，用 0.9 的衰减系数做保守逼近
    factor = 1.0 + (target / lum - 1.0) * 0.9
    return ImageEnhance.Brightness(img).enhance(factor), lum, lum * factor


def desaturate(img, amount=DESATURATE):
    gray = img.convert("L").convert("RGB")
    return Image.blend(img, gray, amount)


def build_vignette(size, strength=VIGNETTE_MAX):
    """径向暗角：中心 0（不压暗），四角 strength。返回 L 模式遮罩。"""
    w, h = size
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    # 用同心椭圆堆叠近似径向渐变，边缘柔化
    steps = 60
    for i in range(steps):
        t = i / (steps - 1)                      # 0=最外 1=最内
        alpha = int(255 * strength * (1.0 - t) ** 2.2)
        inset_x = int(w * 0.5 * t * 0.92)
        inset_y = int(h * 0.5 * t * 0.92)
        draw.ellipse(
            [inset_x, inset_y, w - 1 - inset_x, h - 1 - inset_y],
            fill=alpha,
        )
    return mask.filter(ImageFilter.GaussianBlur(w * 0.06))


def build_bottom_scrim(size, start=SCRIM_START, strength=SCRIM_MAX):
    """底部线性压暗遮罩：start 高度处为 0，底部为 strength。"""
    w, h = size
    scrim = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(scrim)
    y0 = int(h * start)
    for y in range(y0, h):
        t = (y - y0) / max(1, h - 1 - y0)
        # 二次曲线，让压暗更集中在实际字幕区（y≈73%~87%）
        draw.line([(0, y), (w, y)], fill=int(255 * strength * (t ** 1.6)))
    return scrim


def build_top_scrim(size, end=TOP_SCRIM_END, strength=TOP_SCRIM_MAX):
    """顶部压暗遮罩：顶部为 strength，end 高度处衰减到 0。"""
    w, h = size
    scrim = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(scrim)
    y1 = int(h * end)
    for y in range(0, y1 + 1):
        t = 1.0 - (y / max(1, y1))               # 1=最顶 0=end 处
        draw.line([(0, y), (w, y)], fill=int(255 * strength * (t ** 1.5)))
    return scrim


def apply_mask(img, mask):
    """用 L 遮罩把黑色压到图上（遮罩值越大压得越狠）。"""
    black = Image.new("RGB", img.size, (0, 0, 0))
    return Image.composite(black, img, mask)


def grade(path, dry=False):
    img = Image.open(path).convert("RGB")
    before = luminance(img)

    img, raw_lum, adj_lum = normalize_brightness(img)
    img = desaturate(img)

    w, h = img.size
    vign = build_vignette((w, h))
    top = build_top_scrim((w, h))
    scrim = build_bottom_scrim((w, h))
    # 依次叠加：暗角 → 顶部压暗（书名） → 底部压暗（字幕）
    img = apply_mask(img, vign)
    img = apply_mask(img, top)
    img = apply_mask(img, scrim)

    after = luminance(img)
    # 分区亮度：字幕区决定白字可读性，主体区确认画面没被压死
    cap = luminance(img.crop((0, int(h * 0.70), w, int(h * 0.92))))
    subj = luminance(img.crop((0, int(h * 0.15), w, int(h * 0.60))))
    title = luminance(img.crop((0, 0, w, int(h * 0.20))))

    flag = ""
    if cap > REGION_WARN:
        flag += "  ⚠️ 字幕区偏亮"
    if title > REGION_WARN:
        flag += "  ⚠️ 书名区偏亮"
    print(
        f"  {os.path.basename(path):20} 整体 {before:5.1f}->{after:5.1f}"
        f" | 主体 {subj:5.1f} 书名区 {title:5.1f} 字幕区 {cap:5.1f}{flag}"
    )
    if not dry:
        img.save(path, "PNG", optimize=True)
    return cap <= REGION_WARN and title <= REGION_WARN


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry" in sys.argv
    restore = "--restore" in sys.argv
    if not args:
        sys.exit(__doc__)
    target = args[0]
    # 允许传剧集名或目录
    episode_dir = target if os.path.isdir(target) else os.path.join("episodes", target)
    # 三段结构：钩子段的图在 images-hook/，正文在 images-body/（历史剧集 images/）。
    # IMAGES_SUBDIR 切换，两段各自跑一遍同一条调色口径，切镜才不会跳色；
    # 不显式给时按 lib/episode_paths.py 探测正文段目录名，改名后不必每期都记得带开关。
    subdir = body_images_subdir(episode_dir)
    images_dir = os.path.join(episode_dir, subdir)
    if not os.path.isdir(images_dir):
        sys.exit(f"找不到图片目录：{images_dir}")

    raw_dir = os.path.join(images_dir, "_raw")

    # 调色是原地覆盖的，重复跑会叠加。改参数或换图前先 --restore 还原。
    if restore:
        if not os.path.isdir(raw_dir):
            sys.exit(f"没有备份可还原：{raw_dir}")
        restored = 0
        for f in sorted(os.listdir(raw_dir)):
            if _is_atmosphere(f):
                with open(os.path.join(raw_dir, f), "rb") as src:
                    data = src.read()
                with open(os.path.join(images_dir, f), "wb") as out:
                    out.write(data)
                restored += 1
        print(f"已从 {raw_dir}/ 还原 {restored} 张原始图。")
        return

    files = sorted(
        (f for f in os.listdir(images_dir) if _is_atmosphere(f)),
        key=_img_key,
    )
    if not files:
        sys.exit(f"未找到 atmosphere-*.png / result-bridge.png：{images_dir}")

    if not dry:
        os.makedirs(raw_dir, exist_ok=True)
        for f in files:
            dst = os.path.join(raw_dir, f)
            if not os.path.exists(dst):
                with open(os.path.join(images_dir, f), "rb") as src, open(dst, "wb") as out:
                    out.write(src.read())

    print(f"调色 {len(files)} 张{'（dry run）' if dry else ''}  ← {images_dir}")
    ok = True
    for f in files:
        ok = grade(os.path.join(images_dir, f), dry=dry) and ok
    print("完成。" + ("" if dry else f" 原始图已备份于 {raw_dir}/"))
    if not ok:
        print("⚠️ 有区域仍偏亮，建议换图后重跑。")
        sys.exit(1)


if __name__ == "__main__":
    main()
