#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""水波视觉效果：与水波音效同时出现的「入水」涟漪。

规则（2026-09-27 起为系列常驻规则）：`assets/sfx/water.mp3` 在片头→正文切点后
WATER_OFFSET 秒起播、时长 WATER_DUR 秒，画面上必须同时出现一圈自中心向外扩散的
水波涟漪。音效与视觉共用同一对常数，任何一条渲染路径都不得只加声音不加画面。

用法：
  · 逐帧合成器（scripts/render_scene_map_template_fallback.py）在正文背景循环里
    调 `ripple(img, u)`，u 由 `water_u(frame, fps, start)` 给出，窗口外返回 None。
  · 已经渲好的成片/片头用 `scripts/add-water-ripple.py` 补这道效果。

实现：numpy 双线性重采样做径向位移 + 波峰高光，只依赖 Pillow/numpy。
顶部书名带权重压到 0.45，保证常驻《书名》层在涟漪里仍然可读；四边 5% 内幅度收敛到 0，
避免整幅画面被「推走」的错觉。

自检：python3 scripts/lib/water_ripple.py <png> [u1,u2,...]  → 打印到 /tmp/water-preview.png
"""
from __future__ import annotations

import math
import sys

import numpy as np
from PIL import Image

# 水波音效相对「片头→正文切点」的起点与时长：与渲染器里的 adelay / atrim 同源。
# 数值不写在这里——唯一真源是 lib/water-ripple.json，JS 路径（scripts/lib/water-ripple.mjs）
# 与门禁 scripts/check-water-ripple.py 读同一份文件，改一次两端同时生效。
import json as _json
import os as _os

_CONSTS = _json.load(open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                        "water-ripple.json"), encoding="utf-8"))
WATER_OFFSET = float(_CONSTS["offsetSeconds"])
WATER_DUR = float(_CONSTS["durationSeconds"])
if WATER_OFFSET < 0 or WATER_DUR <= 0:
    raise SystemExit("water-ripple.json: offsetSeconds 必须 ≥0 且 durationSeconds 必须 >0")

LAMBDA = 0.17          # 归一化半径下的波纹波长（约 6 圈）
TRAVEL = 1.70          # 全程向外推进的波长数
FRONT_SPAN = 1.30      # 波前扫过的归一化半径
BAND_SIGMA = 0.40      # 波前后涟漪带的宽度
DECAY = 2.40           # 时间指数衰减
ATTACK = 0.10          # 起振占比
# 桥接图是近黑丝绸，纯位移在暗底上几乎看不出来，读得出「水」靠的是波峰高光：
# 位移 22px + 20% 的 cos 环状高光，实测在丝绸/深色氛围图上都清晰，又不至于像故障。
AMP_PX = 22.0          # 峰值位移（720x960 基准，按画幅对角线缩放）
CAUSTIC = 0.20         # 波峰高光强度（cos 相位，明暗成环）
CAUSTIC_FLOOR = 0.35   # 高光下限：只留亮环，暗环压一半，避免暗底上「吃掉」画面
TITLE_TOP, TITLE_W = 0.06, 0.34   # 书名带保护：y<TITLE_TOP 起、到 TITLE_W 恢复满幅
TITLE_GAIN = 0.45
TYPE_GUARD = 0.30      # protect_type 时：y<TYPE_GUARD 整段不动（字已烧进画面）
EDGE = 0.05


def water_start_frame(fps: int, intro_trim: float) -> int:
    """水波在成片时间轴上的起始帧（= 切点 + WATER_OFFSET）。"""
    return round((intro_trim + WATER_OFFSET) * fps)


def water_nframes(fps: int) -> int:
    return round(WATER_DUR * fps)


def water_u(frame: int, fps: int, start_frame: int, n: int | None = None) -> float | None:
    """帧号 → 涟漪进度 u∈(0,1]；不在窗口内返回 None。"""
    n = n if n is not None else water_nframes(fps)
    k = frame - start_frame
    if k < 0 or k >= n:
        return None
    return (k + 1) / n


_GRIDS: dict[tuple[int, int], tuple] = {}


def _grids(W: int, H: int, protect_type: bool):
    key = (W, H, protect_type)
    if key not in _GRIDS:
        xs, ys = np.meshgrid(np.arange(W, dtype=np.float64),
                             np.arange(H, dtype=np.float64))
        cx, cy = W * 0.5, H * 0.52
        dx, dy = xs - cx, ys - cy
        R = math.hypot(cx, cy)
        r = np.hypot(dx, dy) / R
        theta = np.arctan2(dy, dx)
        # 顶部书名带保护：0→TITLE_TOP 之间满保护，到 TITLE_W 恢复满幅
        v = np.clip((ys / H - TITLE_TOP) / (TITLE_W - TITLE_TOP), 0.0, 1.0)
        wv = TITLE_GAIN + (1.0 - TITLE_GAIN) * (v * v * (3.0 - 2.0 * v))
        if protect_type:
            # 无法分层（字已烧进画面）时，书名带整段不动，字才不会糊
            p = np.clip((ys / H - TYPE_GUARD) / 0.06, 0.0, 1.0)
            wv = wv * (p * p * (3.0 - 2.0 * p))
        # 四边收敛，画面边框本身不动
        d_edge = np.minimum(np.minimum(xs, W - 1 - xs) / W,
                            np.minimum(ys, H - 1 - ys) / H) / EDGE
        te = np.clip(d_edge, 0.0, 1.0)
        _GRIDS[key] = (xs, ys, r, theta, wv * te, R)
    return _GRIDS[key]


def _bilinear(img_f: np.ndarray, sx: np.ndarray, sy: np.ndarray) -> np.ndarray:
    """越界按边缘像素钳制（等价 PIL BILINEAR + clamp）。"""
    H, W, _ = img_f.shape
    x0 = np.clip(np.floor(sx), 0, W - 1).astype(np.int32)
    y0 = np.clip(np.floor(sy), 0, H - 1).astype(np.int32)
    x1 = np.minimum(x0 + 1, W - 1)
    y1 = np.minimum(y0 + 1, H - 1)
    fx = (sx - x0)[..., None]
    fy = (sy - y0)[..., None]
    return ((1 - fy) * ((1 - fx) * img_f[y0, x0] + fx * img_f[y0, x1])
            + fy * ((1 - fx) * img_f[y1, x0] + fx * img_f[y1, x1]))


def ripple(img: Image.Image, u: float, protect_type: bool = False) -> Image.Image:
    """对单帧施加水波涟漪。u∈(0,1]，1 为涟漪消散。

    protect_type=True 用于字已烧进画面的成片（书名/字幕整段不动）；
    逐帧合成器走 False——涟漪只作用在背景层，书名与字幕在其后绘制，天然不受影响。
    """
    if not 0.0 < u <= 1.0:
        return img
    W, H = img.size
    scale = math.hypot(W, H) / math.hypot(720, 960)
    xs, ys, r, theta, wm, _ = _grids(W, H, protect_type)

    attack = min(1.0, u / ATTACK)
    decay = math.exp(-DECAY * u)
    front = FRONT_SPAN * (u ** 0.65)
    band = np.exp(-((r - front) / BAND_SIGMA) ** 2)
    phase = 2.0 * math.pi * (r / LAMBDA - TRAVEL * u)
    amp = AMP_PX * scale * attack * decay * band * wm
    disp = amp * np.sin(phase)

    # 径向外扩 + 一点切向涡动，比纯径向更像水面
    sx = xs + disp * (np.cos(theta) - 0.35 * np.sin(theta))
    sy = ys + disp * (np.sin(theta) + 0.35 * np.cos(theta))

    a = np.asarray(img.convert("RGB"), dtype=np.float64)
    out = _bilinear(a, sx, sy)
    # 明环全给、暗环打折：近黑背景上「压暗」是看不见的，只有提亮才读得出水波
    crest = np.clip(np.cos(phase), 0.0, 1.0)
    trough = CAUSTIC_FLOOR * np.clip(-np.cos(phase), 0.0, 1.0)
    glow = CAUSTIC * attack * decay * band * (crest - trough)
    out *= (1.0 + glow)[..., None]
    return Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8))


def _preview(path: str, us: list[float]) -> str:
    src = Image.open(path).convert("RGB")
    W, H = src.size
    tw, th = 300, int(300 * H / W)
    sheet = Image.new("RGB", (tw * len(us) + 10 * (len(us) + 1), th + 26), (10, 10, 10))
    from PIL import ImageDraw
    d = ImageDraw.Draw(sheet)
    for i, u in enumerate(us):
        f = ripple(src.resize((tw, th)), u)
        x = 10 + i * (tw + 10)
        sheet.paste(f, (x, 26))
        d.text((x + 4, 8), f"u={u:.2f}", fill=(255, 255, 255))
    out = "/tmp/water-preview.png"
    sheet.save(out)
    return out


if __name__ == "__main__":
    if len(sys.argv) > 1:
        vals = [float(v) for v in (sys.argv[2].split(",") if len(sys.argv) > 2
                                   else "0.05,0.15,0.3,0.5,0.7,0.9".split(","))]
        print(_preview(sys.argv[1], vals))
    else:
        print(__doc__)
