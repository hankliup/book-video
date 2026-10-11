# -*- coding: utf-8 -*-
"""正文段常驻声明（「内容为书评与感悟　素材来源于网络」）的唯一真源。

规则本体在 `scripts/lib/disclaimer.json`：文案、字号、下边距、灰度、投影、画幅，
以及**这条线不许压到哪两块区域**。约定和 `subtitle-punctuation.json` / `caption_display.py`
一样——文案与几何只写一次，谁要用谁 import：

  · Pillow 兜底渲染器（本机实际交付路径）用 `overlay()` 画出 `disclaimer.png` 叠层；
  · `scripts/create-episode-preview.mjs` 用 `lib/disclaimer.mjs` 出同口径的 CSS/HTML；
  · `scripts/check-disclaimer-overlay.py` 用同一个 `overlay()`/`mask()` 当预期墨迹验收。

为什么位置要当数据管起来，而不是随手写在渲染器里：这一行落在画面底部，而底部已经有
**两道门各自的地盘**——字幕边界压测 `qc-caption-alignment.py` 的 `BAND = (0, 680, 720, 850)`
按 y 680..850 取「画面长出来的字幕墨迹」，亮度闪屏判据⑥的取证窗口 `LUMA_BAND_ROWS = (0, 35)`
（36×48 探针，48 行 = 960px）只看 y < 700。声明要是写大一号、下边距写小一号就会爬进字幕带：
边界压测会把这行灰字当成「此刻该出现的字幕」量，判据⑥会在场景淡入淡出时把它当画面墨迹。
所以下面 `geometry()` 会**当场拒绝**与保留带相交的配置，而不是等门禁误报。
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
LIB_PATH = os.path.join(HERE, "disclaimer.json")

# STHeiti 量不到时的兜底 ascent（em）：CJK 常规值，只影响 band 上沿几个像素，
# 门禁按 mask 比对而比对用同一函数，两侧不会因此分叉。
_FALLBACK_ASCENT_EM = 0.86


def load(path=LIB_PATH):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    parts = cfg.get("parts")
    if not isinstance(parts, list) or len(parts) < 2 or not all(
            isinstance(p, str) and p.strip() for p in parts):
        raise ValueError("disclaimer.json: parts 必须是 ≥2 条非空字符串（声明文案只有一个真源）")
    if "".join(parts).strip() != "".join(p.strip() for p in parts):
        raise ValueError("disclaimer.json: parts 里不许夹空格/换行，段间距只由 gap_em 决定")
    for key in ("font_size_px", "bottom_px", "gap_em"):
        if not isinstance(cfg.get(key), (int, float)) or cfg[key] <= 0:
            raise ValueError(f"disclaimer.json: {key} 必须是正数")
    rgba = cfg.get("color_rgba")
    if not isinstance(rgba, list) or len(rgba) != 4:
        raise ValueError("disclaimer.json: color_rgba 必须是 [r,g,b,a] 四项")
    if not (0 <= rgba[3] <= 1):
        raise ValueError("disclaimer.json: color_rgba 的 alpha 取值范围是 0..1")
    lo, hi = cfg.get("ink_luma_range", [0, 255])
    if not (0 <= lo < hi <= 255):
        raise ValueError("disclaimer.json: ink_luma_range 必须是升序的 0..255 区间")
    if cfg.get("segment") != "body":
        raise ValueError('disclaimer.json: segment 只能是 "body"——常驻声明属于正文段，'
                         '钩子段带它就等于把书目卡片搬回第一屏（同 check-hook-no-bookmark 的理由）')
    w, h = geometry(cfg)["canvas"]
    _assert_clear_of_reserved_bands(cfg, w, h)
    return cfg


def display_text(cfg=None):
    cfg = cfg or load()
    return "　".join(cfg["parts"])


def _ascent(cfg, font_size):
    try:
        from PIL import ImageFont
        return ImageFont.truetype(cfg["font_file"], font_size).getmetrics()[0]
    except Exception:
        return round(font_size * _FALLBACK_ASCENT_EM)


def geometry(cfg=None, canvas=None):
    """这条声明的画字几何：字号、下边距、墨迹上/下沿、门禁取样的 band。"""
    cfg = cfg or load()
    w, h = canvas or cfg.get("canvas", [720, 960])
    size = int(round(cfg["font_size_px"]))
    bottom = int(round(cfg["bottom_px"]))
    ascent = _ascent(cfg, size)
    top = h - bottom - ascent                      # 墨迹框上沿（PIL 的 top 坐标）
    box_bottom = h - bottom                        # 墨迹框下沿
    pad = int(cfg.get("band_pad_px", 8))
    band = (0, max(0, top - pad), w, min(h, box_bottom + pad))
    return {
        "canvas": (w, h),
        "font_size": size,
        "bottom": bottom,
        "gap_px": round(size * cfg["gap_em"]),
        "ascent": ascent,
        "top": top,
        "box_bottom": box_bottom,
        "band": band,
        "parts": list(cfg["parts"]),
        "color_rgba": list(cfg["color_rgba"]),
        "shadow": dict(cfg["shadow"]),
    }


def reserved_bands(cfg=None):
    """[{name, y0, y1, owner, constant}]：声明这行不许进入的区域。"""
    cfg = cfg or load()
    out = []
    for name, item in (cfg.get("reserved_bands") or {}).items():
        if "y0" in item:
            y0, y1 = float(item["y0"]), float(item["y1"])
        elif "rows" in item:
            # 探针行 → 像素：48 行 = 960px 那种口径，换算系数也由 lib 自己声明
            scale = float(item["canvas_height"]) / float(item["probe_rows"])
            y0, y1 = item["rows"][0] * scale, item["rows"][1] * scale
        else:
            continue
        out.append({"name": name, "y0": y0, "y1": y1,
                    "owner": item.get("owner", ""), "constant": item.get("constant", "")})
    return out


def _assert_clear_of_reserved_bands(cfg, w, h):
    geo = geometry(cfg, (w, h))
    for rb in reserved_bands(cfg):
        if geo["band"][1] < rb["y1"] and geo["band"][3] > rb["y0"]:
            raise ValueError(
                f"disclaimer.json 的字号/下边距把常驻声明画进了保留带 "
                f"{rb['name']}（y {rb['y0']:.0f}..{rb['y1']:.0f}，真源 "
                f"{rb['owner']} 的 {rb['constant']}）：声明 band 现在占 y "
                f"{geo['band'][1]}..{geo['band'][3]}。要么把字改小/下边距加大，"
                f"要么先改那道门自己的取样区——两边都改一次会造出假绿灯。")


def overlay(cfg=None, canvas=None, font_file=None, draw_shadow=True):
    """画出这条声明的全幅 RGBA 叠层（透明底 + 投影 + 灰字）。

    渲染器和门禁共用这一个函数：渲染器把它合成进正文段，门禁拿它的 alpha 当预期墨迹。
    任何一方自己重画一遍，就又是一个「渲染读一份、门禁读另一份」的分叉口。

    `draw_shadow=False` 只给 `core_mask()` 用：字形本体那一层单独画出来，颜色判据才量得到
    「字自己的灰度」。投影那层不能一起进样本，理由见 core_mask 的注释。
    """
    cfg = cfg or load()
    from PIL import Image, ImageDraw, ImageFilter
    geo = geometry(cfg, canvas)
    w, h = geo["canvas"]
    font_file = font_file or cfg["font_file"]
    try:
        from PIL import ImageFont
        font = ImageFont.truetype(font_file, geo["font_size"])
    except Exception as exc:                                    # 换机器/缺字体：不许静默画空
        raise RuntimeError(f"常驻声明字体不可用：{font_file}（{exc}）") from exc

    def _widest(text):
        return font.getlength(text)

    widths = [_widest(t) for t in geo["parts"]]
    total = sum(widths) + geo["gap_px"] * (len(widths) - 1)
    x0 = (w - total) / 2.0
    r, g, b, a = cfg["color_rgba"]
    fill = (int(r), int(g), int(b), int(round(255 * a)))
    sh = cfg["shadow"]
    sr, sg, sb, sa = sh["rgba"]
    sfill = (int(sr), int(sg), int(sb), int(round(255 * (sa if sa <= 1 else sa / 255.0))))

    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    pen = x0
    xs = []
    for t, wd in zip(geo["parts"], widths):
        xs.append((t, pen))
        pen += wd + geo["gap_px"]
    if draw_shadow and int(sh.get("blur_px", 0)) > 0:
        sil = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sil)
        for t, x in xs:
            sd.text((x, geo["top"] + int(sh.get("offset_y_px", 0))), t, font=font, fill=sfill)
        im.alpha_composite(sil.filter(ImageFilter.GaussianBlur(int(sh["blur_px"]))))
    elif draw_shadow:
        # 不透明硬投影（2026-10-06 用户要求水印「状态保持固定」）：模糊投影的软裙边
        # 是部分 alpha，会随背景推近/换景呼吸（实测边缘均值亮背景 153.7 / 暗背景 20.1）；
        # 硬投影逐像素不透明，合成结果与背景无关。
        sil = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sil)
        for t, x in xs:
            sd.text((x, geo["top"] + int(sh.get("offset_y_px", 0))), t, font=font, fill=sfill)
        im.alpha_composite(sil)
    dr = ImageDraw.Draw(im)
    for t, x in xs:
        dr.text((x, geo["top"]), t, font=font, fill=fill)
    return im


def mask(cfg=None, canvas=None, threshold=128):
    """预期墨迹（numpy 布尔阵）：alpha 高于 threshold 的像素。"""
    import numpy as np
    cfg = cfg or load()
    a = np.asarray(overlay(cfg, canvas).convert("RGBA"))[..., 3]
    return a > threshold


def core_mask(cfg=None, canvas=None):
    """笔画实心部分（不含投影与半透明边缘），供颜色判据取样。

    样本必须来自**只画字、不画投影**的那一层：2026-10-06 投影按用户要求改成不透明硬投影后，
    它的 alpha 和字面一样是 255，旧的「整张叠层里 alpha ≥ 0.92·a」把 3px 之下的黑投影一起放进了
    样本，字面亮度于是被黑投影稀释——实测把颜色换成白字（250）只算出 163，落在
    `ink_luma_range` [45,205] 之内，「这条线不许换成白字」那条判据就此失明。
    门自己写的注释是「不含投影」，取样就得真的不含投影。
    """
    import numpy as np
    cfg = cfg or load()
    a = float(cfg["color_rgba"][3]) * 255.0
    alpha = np.asarray(overlay(cfg, canvas, draw_shadow=False).convert("RGBA"))[..., 3]
    return alpha >= a * 0.92 if a > 0 else alpha > 0


__all__ = ["LIB_PATH", "load", "display_text", "geometry", "reserved_bands",
           "overlay", "mask", "core_mask"]
