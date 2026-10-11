# -*- coding: utf-8 -*-
"""正文段起幅「本书封面图」层的唯一真源。

规则本体在 `scripts/lib/book-cover.json`：可用矩形（slot）、最大宽度、投影、时序推导、
以及这一层不许进入的三条保留带。和 `disclaimer.py` / `title_font.py` 一样的约定——
几何与时刻只写一次，谁要用谁 import：

  · Pillow 兜底渲染器（本机实际交付路径）用 `overlay()` 画 `TMP/book-cover.png`，
    用 `timing()` 决定 ffmpeg 的淡入/淡出时刻；
  · `scripts/check-book-cover-overlay.py` 用**同一个** `overlay()`/`mask()` 当预期墨迹验收；
  · `scripts/fetch-book-cover.py` 用 `source_path()`/`provenance_ok()` 决定图落在哪、
    旁证记什么。

为什么把「装得下任何比例」做成内接而不是让调用方各写一次缩放：微信读书的封面有 3:4
（译林、上海译文常见）也有 2:3（多数精装），渲染器里写死 300×400 的话，2:3 的封面要么
被拉扁、要么超出卡框爬进常驻书名层。所以 `fit()` 只在 slot 里等比内接，slot 自己由
`_assert_clear_of_reserved_bands()` 保证不与三条保留带相交——**几何不可能画错，
只有 slot 可能被改错**，而改错 slot 会在 import 期就抛错，不会等到门禁误报。

出场动效（`motion()` / `zoom_at()` / `zoomed_overlay()`）同一条道理，2026-10-08 二次要求
「放大过程不流畅，抖动得厉害，可以参考氛围图的动态效果，通过增强动态节奏速度来达到效果」
之后加了四道 import 期断言：装置必须是逐帧浮点仿射（整数取整把中心量化到整像素，实测
41 步里 24 步方向回退，那就是抖）、缓动名在册、`zoom_to` 恰为 1.0（缩放终态就是被保留带
校过的 slot；从 slot 再往外放大就是把这层的落点交回每期自己决定）、卡框边缘平均速度
过 `pace_min_px_per_second` 下限（比身后正在推的氛围图还慢就读成「一张静止的卡片在抖」）。
`_assert_clear_of_reserved_bands()` 因此量的是 `motion_envelope()` 的缩放包络而不是静态
slot——旧版只量静态 slot，1.12 那一版门禁全绿而卡体已压进书名层 19px、字幕取样带 6px。

时刻全部推导，不写秒数：淡入长度 = 水波音效相对切点的偏移（lib/water-ripple.json），
淡出从涟漪收口起、按场景交叉溶解的时长走。理由见 JSON 的 timing._comment。
"""
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
LIB_PATH = os.path.join(HERE, "book-cover.json")

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def load(path=LIB_PATH):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    slot = cfg.get("slot")
    if not isinstance(slot, dict):
        raise ValueError("book-cover.json: slot 必须是对象（可用矩形只有一个真源）")
    for key in ("y0_px", "y1_px", "max_width_px"):
        if not isinstance(slot.get(key), (int, float)) or slot[key] <= 0:
            raise ValueError(f"book-cover.json: slot.{key} 必须是正数")
    if slot["y1_px"] <= slot["y0_px"]:
        raise ValueError("book-cover.json: slot.y1_px 必须大于 y0_px")
    if not (0.0 < float(slot.get("x_center_em", 0.5)) < 1.0):
        raise ValueError("book-cover.json: slot.x_center_em 取值范围是 0..1")
    if cfg.get("segment") != "body":
        raise ValueError('book-cover.json: segment 只能是 "body"——封面属于正文段起幅，'
                         '钩子段带它就等于把书目卡片搬回第一屏（同 check-hook-no-bookmark 的理由）')
    for key in ("match_min_iou", "max_box_rgb_diff", "max_absent_box_rgb_diff"):
        if not isinstance(cfg.get(key), (int, float)) or cfg[key] <= 0:
            raise ValueError(f"book-cover.json: {key} 必须是正数")
    if cfg.get("timing", {}).get("fade_in_from") != "water_offset":
        raise ValueError("book-cover.json: timing.fade_in_from 必须是 water_offset——"
                         "淡入长度跟着水波音效的起播偏移走，写死秒数就会和水波分叉")
    _assert_clear_of_reserved_bands(cfg, *(cfg.get("canvas") or [720, 960]))
    return cfg


def geometry(cfg=None, canvas=None):
    """卡框可用矩形：slot 换算到画幅像素。封面在这里内接，不在别处。"""
    cfg = cfg or load()
    w, h = canvas or cfg.get("canvas", [720, 960])
    slot = cfg["slot"]
    x0, x1 = int(round(slot["y0_px"])), int(round(slot["y1_px"]))
    if x1 > h:
        raise ValueError(f"book-cover.json: slot.y1_px {x1} 超出画幅高 {h}")
    half = int(round(slot["max_width_px"] / 2.0))
    cx = int(round(w * float(slot["x_center_em"])))
    return {
        "canvas": (w, h),
        "slot": (max(0, cx - half), x0, min(w, cx + half), x1),
        "slot_center": (cx, (x0 + x1) // 2),
        "max_width": int(round(slot["max_width_px"])),
        "slot_height": x1 - x0,
    }


def reserved_bands(cfg=None):
    """[{name, y0, y1, owner, constant}]：这一层不许进入的区域。"""
    cfg = cfg or load()
    out = []
    for name, item in (cfg.get("reserved_bands") or {}).items():
        if "y0" not in item or "y1" not in item:
            continue
        out.append({"name": name, "y0": float(item["y0"]), "y1": float(item["y1"]),
                    "owner": item.get("owner", ""), "constant": item.get("constant", "")})
    return out


def _ramp_end_seconds():
    """动效期的长度（秒）= 涟漪收口时刻 = 水波偏移 + 水波时长。

    和 timing() 的 fade_out_at 同一个式子、同一对常量，不另写一遍秒数。
    """
    import sys
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    from water_ripple import WATER_DUR, WATER_OFFSET
    return float(WATER_OFFSET) + float(WATER_DUR)


def motion_envelope(cfg=None, canvas=None):
    """动效全程卡框的**纵向包络** (y0, y1)：slot 按 zoom_from/zoom_to 两端取并集。

    缩放原点按 `zoomed_overlay` 是画幅中心 (W/2, H/2)，而 slot 的放大对 t 单调，所以
    「中途经过的 y」恰好落在两个端点之间——取端点并集就是完整足迹，不需要逐帧采样。
    这里量的是 slot 而不是真卡框：真卡按源图比例只会比 slot 窄、比 slot 矮，slot 是
    这一层可能占到的最大矩形，用它判越带是**保守**方向（宁可多判不可漏判）。

    故意读 cfg 的原始 zoom、不调 `motion()`：这是一个纯几何问题，把校验夹进来就没法
    单独问「1.12 那一版的足迹到底越了哪条带」——`test-cover-motion-smoothness.py` 正是
    要分开证这两件事（包络算术看得见缩放，`motion()` 另外拒绝终态离开 1.0）。
    """
    cfg = cfg or load()
    w, h = canvas or cfg.get("canvas", [720, 960])
    slot = geometry(cfg, (w, h))["slot"]
    sy0, sy1 = slot[1], slot[3]
    m = cfg.get("motion") or {}
    typ = str(m.get("type", "none"))
    zs = [1.0] if typ in ("", "none") else [float(m.get("zoom_from", 1.0)),
                                            float(m.get("zoom_to", 1.0))]
    cy = h / 2.0
    ys = [cy + z * (y - cy) for z in zs for y in (sy0, sy1)]
    return (min(ys), max(ys))


def _assert_clear_of_reserved_bands(cfg, w, h):
    geo = geometry(cfg, (w, h))
    sy0, sy1 = geo["slot"][1], geo["slot"][3]
    # 关键：量的是**动效包络**而不是静态 slot。2026-10-08 那一版把 zoom_to 写成 1.12，
    # 静态 slot 干净、门禁全绿，成片里卡体却顶到 y206（书名层下沿 225）、底到 y686
    # （字幕取样带上沿 680）——因为「缩放后的足迹」从来没有任何一方检查过。
    ey0, ey1 = motion_envelope(cfg, (w, h))
    for rb in reserved_bands(cfg):
        if ey0 < rb["y1"] and ey1 > rb["y0"]:
            grew = "" if (round(ey0), round(ey1)) == (sy0, sy1) else \
                f"（静态 slot 是 y {sy0}..{sy1}，动效把足迹放大到 y {ey0:.0f}..{ey1:.0f}）"
            raise ValueError(
                f"book-cover.json 的卡框足迹与保留带 {rb['name']}"
                f"（y {rb['y0']:.0f}..{rb['y1']:.0f}，真源 {rb['owner']} 的 {rb['constant']}）"
                f"相交：现在占 y {ey0:.0f}..{ey1:.0f}{grew}。封面一旦压进字幕取样带，"
                f"字幕边界压测会把封面的白字当成该出现的字幕来量；压进书名层，"
                f"两层在切点上互相抢字。要么收窄 slot，要么把 motion 的缩放终态收回 1.0"
                f"（长进标定卡框，而不是从标定卡框再放大），要么先改那道门自己的取样区"
                f"——两边各改一次会造出假绿灯。")


def source_path(ep_dir, cfg=None):
    """本期封面图的路径（episode 目录根，语义名）。找不到返回 None，由调用方决定红还是 ⚠️。

    故意**不**在 images-body/ 里找：那个目录归 Pexels 溯源门禁管，一张非 Pexels 的图进去
    就是把 check-image-provenance.py 判红。
    """
    cfg = cfg or load()
    name = cfg["asset"]["filename"]
    stem = os.path.splitext(name)[0]
    for ext in IMAGE_EXTS:
        p = os.path.join(ep_dir, stem + ext)
        if os.path.exists(p):
            return p
    return None


def provenance_path(ep_dir, cfg=None):
    cfg = cfg or load()
    return os.path.join(ep_dir, cfg["asset"]["provenance"])


def provenance(ep_dir, cfg=None):
    """读旁证（图从哪来）。没有文件返回 None。"""
    p = provenance_path(ep_dir, cfg)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def provenance_ok(ep_dir, cfg=None):
    """图与旁证是否成对且对得上：返回 (是否合格, 说明)。

    为什么要有这一条：AGENTS.md 对整个仓库的要求是「每一张上屏的图都能溯源」，
    Pexels 那套靠 manifest + _provenance.json + sha256 标记链，本层是同一件事的最小版：
    旁证记下来源 URL 与图字节 sha256，换图不重写旁证 = 红。
    """
    import hashlib
    cfg = cfg or load()
    src = source_path(ep_dir, cfg)
    if not src:
        return False, "本期没有封面图"
    prov = provenance(ep_dir, cfg)
    if not prov:
        return False, f"缺旁证 {os.path.basename(provenance_path(ep_dir, cfg))}"
    for key in ("source", "url", "sha256"):
        if not prov.get(key):
            return False, f"旁证缺字段 {key}"
    want = str(prov["sha256"]).strip().lower()
    got = hashlib.sha256(open(src, "rb").read()).hexdigest()
    if want != got:
        return False, (f"旁证 sha256 与盘上图不符（{want[:12]} vs {got[:12]}）："
                       f"换过图就得重新记旁证")
    from PIL import Image
    with Image.open(src) as im:
        w = im.size[0]
    if w < int(cfg["asset"]["min_source_width"]):
        return False, (f"封面源图只有 {w}px 宽，低于下限 "
                       f"{cfg['asset']['min_source_width']}px：拉成卡框会糊")
    return True, (f"{prov['source']} · {w}px · "
                  f"{prov.get('hit_title') or prov.get('query_title') or ''}")


def fit(src_size, cfg=None, canvas=None):
    """把源图等比内接进 slot：返回 {box, size, scale}。"""
    cfg = cfg or load()
    geo = geometry(cfg, canvas)
    sx0, sy0, sx1, sy1 = geo["slot"]
    sw, sh = sx1 - sx0, sy1 - sy0
    iw, ih = int(src_size[0]), int(src_size[1])
    if iw <= 0 or ih <= 0:
        raise ValueError(f"fit: 源图尺寸非法 {src_size}")
    scale = min(sw / iw, sh / ih, 1.0) if max(iw, ih) else 1.0
    # 小图允许放大到卡框（微信读书 t9_ 档 428×612 内接 300 宽是缩小；只有源图比 slot 还小
    # 才会 >1），但 min_source_width 已经在 provenance_ok() 挡掉「拿缩略图充高清」。
    w = max(1, int(round(iw * scale)))
    h = max(1, int(round(ih * scale)))
    cx, cy = geo["slot_center"]
    return {"box": (cx - w // 2, cy - h // 2, cx - w // 2 + w, cy - h // 2 + h),
            "size": (w, h), "scale": scale}


def overlay(source=None, cfg=None, canvas=None, ep_dir=None, draw_shadow=True):
    """画出这一层的全幅 RGBA 叠层（透明底 + 柔和落影 + 封面图）。

    渲染器与门禁共用这一个函数：任何一方自己重画一遍，就又是「渲染读一份、门禁读另一份」
    的分叉口。`source` 不给时按 `ep_dir` 去 lib 规定的语义名找。
    """
    cfg = cfg or load()
    from PIL import Image, ImageDraw, ImageFilter
    if source is None:
        if not ep_dir:
            raise ValueError("overlay(): 需要 source 或 ep_dir 之一")
        source = source_path(ep_dir, cfg)
        if not source:
            raise FileNotFoundError(f"{ep_dir} 下没有 {cfg['asset']['filename']}")
    geo = geometry(cfg, canvas)
    w, h = geo["canvas"]
    with Image.open(source) as raw:
        im = raw.convert("RGBA")
    f = fit(im.size, cfg, geo["canvas"])
    card = im.resize(f["size"], Image.LANCZOS)
    bx0, by0, bx1, by1 = f["box"]

    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sh = cfg["shadow"]
    if draw_shadow and int(sh.get("blur_px", 0)) > 0:
        sr, sg, sb, sa = sh["rgba"]
        sfill = (int(sr), int(sg), int(sb),
                 int(round(255 * (sa if sa <= 1 else sa / 255.0))))
        sil = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(sil).rectangle(
            (bx0, by0 + int(sh.get("offset_y_px", 0)), bx1, by1 + int(sh.get("offset_y_px", 0))),
            fill=sfill)
        out.alpha_composite(sil.filter(ImageFilter.GaussianBlur(int(sh["blur_px"]))))
    out.alpha_composite(card, (bx0, by0))
    return out


def mask(source=None, cfg=None, canvas=None, ep_dir=None, threshold=128):
    """预期墨迹（numpy 布尔阵）：alpha 高于 threshold 的像素（含投影裙边）。"""
    import numpy as np
    cfg = cfg or load()
    a = np.asarray(overlay(source, cfg, canvas, ep_dir).convert("RGBA"))[..., 3]
    return a > threshold


def core_mask(source=None, cfg=None, canvas=None, ep_dir=None):
    """封面图本体（不含投影）：RGB 对账只在这里取样。

    与 disclaimer.core_mask() 同一个理由：把投影一起放进样本，量到的就不是那一层自己。
    """
    import numpy as np
    cfg = cfg or load()
    a = np.asarray(overlay(source, cfg, canvas, ep_dir, draw_shadow=False)
                   .convert("RGBA"))[..., 3]
    return a >= 250


def card_box(source=None, cfg=None, canvas=None, ep_dir=None):
    cfg = cfg or load()
    if source is None:
        source = source_path(ep_dir, cfg)
    if not source:
        return geometry(cfg, canvas)["slot"]
    from PIL import Image
    with Image.open(source) as im:
        return fit(im.size, cfg, canvas or cfg.get("canvas", [720, 960]))["box"]


def timing(bridge_exit, xfade, cfg=None):
    """这一层的三个时刻（正文段相对秒）。全部由既有常量推导，不接受手写的秒数。

    `bridge_exit` 是桥接卡切到第一张氛围图的时刻（渲染器的 `layer_start[1]`，它已按 lib
    常量保证 ≥ 涟漪收口），只用来做**下限自检**；退场起点是涟漪收口本身：

        淡入 0 → WATER_OFFSET（水声起播那一帧到不透明）
        平台 WATER_OFFSET → WATER_OFFSET + WATER_DUR（水波整段）
        淡出 (WATER_OFFSET + WATER_DUR) → +XFADE

    为什么退场跟着涟漪而不是跟着桥接卡：用户 2026-10-07 在三个停留时长里选的是
    「只跟水波那 1.4 秒」，而不是「跟着桥接卡」。桥接卡有多长是**本期切镜表**的事
    （《乞力马扎罗的雪》2.18s，别期可能是 0.8s 或 4s），跟着它就成了每期一个时长、
    并且会往「常驻整段」那个被否决的方向漂。跟着涟漪收口，这一层在全库是同一个形状：
    出现在水声起播那一帧，水波一收就跟着走，走的时间就是场景溶解的那 0.64s。

    代价说清楚：当 `bridge_exit` 落在淡出中途（例如切镜表把首镜定在 1.6s），封面会带着
    残余 alpha 多存在最多 XFADE 秒，**叠在第一张氛围图上淡掉**。那是一次淡出，不是一次
    内容交换，而且卡框（y 235..665）与字幕取样带（680..850）、声明行（885..924）都不相交，
    所以既不抢字也不改第 ⑥ 判据读到的取证窗口；反过来若强行要求 `gone_at ≤ bridge_exit`，
    就得把淡出压到涟漪窗口以内，那才会把水波门禁量的径向逐帧差质心拽偏。
    """
    cfg = cfg or load()
    import sys
    if HERE not in sys.path:
        sys.path.insert(0, HERE)          # 单独 import 本 lib 时也要能拿到水波常量
    from water_ripple import WATER_DUR, WATER_OFFSET
    t = cfg["timing"]
    if t.get("fade_out_from") != "ripple_end" or t.get("fade_out_span") != "scene_xfade":
        raise ValueError("book-cover.json: 淡出必须从涟漪收口起、按场景溶解时长走"
                         "（fade_out_from=ripple_end / fade_out_span=scene_xfade）")
    fade_in = float(WATER_OFFSET)
    out_start = fade_in + float(WATER_DUR)
    out_span = float(xfade)
    if fade_in <= 0:
        raise ValueError("水波偏移必须 >0：封面淡入的跑道就是水声起播前的那段静默")
    if out_span <= 0:
        raise ValueError("场景交叉溶解时长必须 >0：封面的退场用的就是那个时长")
    if float(bridge_exit) <= fade_in:
        raise SystemExit(
            f"桥接卡 {float(bridge_exit):.3f}s 就切走了，比水波起振（{fade_in:.2f}s）还早："
            f"封面还没有一个静止的底可以站在上面。渲染器本就把首镜切点推到 ≥ 涟漪收口，"
            f"这里出现相交说明调用方传错了桥接时长。")
    return {"fade_in_seconds": round(fade_in, 4),
            "full_from": round(fade_in, 4),
            "fade_out_at": round(out_start, 4),
            "fade_out_seconds": round(out_span, 4),
            "gone_at": round(out_start + out_span, 4),
            "bridge_exit": round(float(bridge_exit), 4)}


_DEVICE = "float_affine_per_frame"
_EASES = ("cosine",)


def motion(cfg=None):
    """封面出场动效参数（唯一真源在 lib/book-cover.json 的 motion 键）。

    2026-10-08 用户先要求「视频中的本书封面出现时增加放大的动态效果」，同日二次要求
    「放大过程不流畅，抖动得厉害，可以参考氛围图的动态效果，通过增强本书封面图的动态
    节奏速度来达到效果」。四条判据都是量出来的（数字与量法见 JSON 的 motion._comment）：

      · **device**：逐帧浮点仿射，和正文氛围镜头 `zoom_frame()` 同一条矩阵。整数取整
        （resize 到整宽再按整数偏移贴回、或 ffmpeg zoompan）把中心量化到整像素，实测
        41 步里 24 步方向回退 —— 那是「抖」，不是「动」。
      · **ease**：cosine 缓入缓出，与氛围镜头的 `ease_in_out` 同款。
      · **zoom_to 必须是 1.0**：动效终态就是被三条保留带校过的那个 slot。从 slot 再往外
        放大（1.12）会把卡体推进书名层与字幕取样带，而 `_assert_clear_of_reserved_bands()`
        现在按动效包络判，改回去会在 import 期直接抛错。
      · **pace_min_px_per_second**：卡框边缘的平均速度下限，用来钉住「比身后正在推的
        实拍氛围图还慢」这种读起来不动的形状。

    动效期为 t=0（正文起幅）→ 涟漪收口 fade_out_at，过 fade_out_at 保持终态直到淡出结束。
    缩放原点取**画幅中心**：slot 几何是唯一真源，动效算法不该为每期卡框另开一套。"""
    cfg = cfg or load()
    m = cfg.get("motion") or {}
    typ = str(m.get("type", "none"))
    if typ in ("", "none"):
        return {"type": "none", "zoom_from": 1.0, "zoom_to": 1.0,
                "device": _DEVICE, "ease": "cosine"}
    if typ != "zoom_in":
        raise ValueError(f"book-cover.json: 未知 motion.type {typ!r}")
    device = str(m.get("device", _DEVICE))
    if device != _DEVICE:
        raise ValueError(
            f"book-cover.json: motion.device={device!r} 不是 {_DEVICE!r}。"
            f"整数取整的两条路都被实测否掉：`resize(int(round(W*s))) + 整数偏移贴回` 与 "
            f"ffmpeg zoompan 都会把卡片中心量化到整像素，《第六病室》2026-10-08 量到 "
            f"41 步里 24 步方向回退（净位移 17.4px、路径 30.2px），观众读作「抖动得厉害」。"
            f"逐帧浮点仿射是正文氛围镜头一直在用的那条矩阵。")
    ease = str(m.get("ease", "cosine"))
    if ease not in _EASES:
        raise ValueError(f"book-cover.json: 未知 motion.ease {ease!r}（可选：{', '.join(_EASES)}）")
    zf = float(m.get("zoom_from", 1.0))
    zt = float(m.get("zoom_to", 1.0))
    if zf <= 0 or zt < zf:
        raise ValueError(f"book-cover.json: motion.zoom_from/zoom_to 非法（{zf}→{zt}），"
                         f"要求 0 < zoom_from ≤ zoom_to")
    if zf < 0.70:
        raise ValueError(
            f"book-cover.json: motion.zoom_from={zf} 太小（下限 0.70）。这一层的动效是"
            f"「一张卡片长到它的标定卡框」，不是「卡片从远处飞进画面」：起幅小于 0.7 倍"
            f"就是 18% 以上行程花在最初的呼吸里，读作甩镜头。嫌慢应该去核对"
            f"pace_min_px_per_second 那条速度判据，而不是把行程拉长。")
    if abs(zt - 1.0) > 1e-6:
        raise ValueError(
            f"book-cover.json: motion.zoom_to 必须等于 1.0（现在是 {zt}）——"
            f"这一层的卡框 slot 是按三条保留带校出来的唯一矩形，缩放终态离开 1.0 就等于"
            f"每期重新决定一次封面能压到哪里：1.12 那一版把卡体顶沿推到 y206（书名层下沿 "
            f"225）、底沿推到 y686（字幕取样带上沿 680）。要「放大」就把起幅做小（0.82→1.00 "
            f"长进标定卡框），而不是从标定卡框再往外放大。")
    if zt == zf:
        return {"type": "zoom_in", "zoom_from": zf, "zoom_to": zt,
                "device": device, "ease": ease, "px_per_second": 0.0}
    half_w = float(cfg["slot"]["max_width_px"]) / 2.0
    ramp = _ramp_end_seconds()
    px_s = (zt - zf) * half_w / max(ramp, 1e-9)
    floor = float(m.get("pace_min_px_per_second", 0) or 0)
    if floor and px_s < floor:
        raise ValueError(
            f"book-cover.json: 封面动效太慢——卡框边缘平均 {px_s:.2f}px/s，低于下限 "
            f"{floor:.2f}px/s（{zf}→{zt} 在 {ramp:.2f}s 动效期里只走 "
            f"{(zt - zf) * half_w:.1f}px）。下限的量法见 motion._comment：本期氛围镜头"
            f"最短那一镜的画面边缘就有 14.25px/s，封面比它慢就读成「一张静止的卡片在抖」。"
            f"要么加大行程，要么缩短动效期——不许改这个数来放行。")
    return {"type": "zoom_in", "zoom_from": zf, "zoom_to": zt,
            "device": device, "ease": ease, "px_per_second": round(px_s, 3)}


def _ease_fn(name):
    if name == "cosine":
        return lambda u: 0.5 - 0.5 * math.cos(math.pi * u)
    raise ValueError(f"未知的缓动曲线 {name!r}")


def zoom_at(t, timing_dict, cfg=None):
    """正文相对时刻 t 处的封面缩放系数。渲染器、门禁、测试三方共用。

    节奏与正文镜头同款：cosine 缓入缓出（同 `ease_in_out`），终态恰好落在标定卡框
    （zoom_to=1.0），所以整个行程都在 slot 内、不可能进保留带。
    """
    m = motion(cfg)
    if m["type"] != "zoom_in" or m["zoom_to"] <= m["zoom_from"]:
        return m["zoom_from"] if m["type"] == "zoom_in" else 1.0
    end = float(timing_dict.get("fade_out_at") or 0) or 1e-9
    u = min(max(float(t), 0.0), end) / end
    return m["zoom_from"] + (m["zoom_to"] - m["zoom_from"]) * _ease_fn(m["ease"])(u)


def zoomed_overlay(overlay_im, s):
    """把 lib overlay 叠层整体按 s 从画幅中心放大（s=1 原样返回拷贝）。

    与正文镜头同一条变换：AFFINE + BICUBIC、浮点系数，**不做整数取整/跳像素**——
    之前的「缩放整幅再按整数偏移贴回」会让卡框中心在偶/奇像素之间来回跳半像素，
    LANCZOS 重采样还带来边缘闪动，就是「放大过程抖动」的来源。
    """
    from PIL import Image
    W, H = overlay_im.size
    if abs(s - 1.0) < 1e-6:
        return overlay_im.copy()
    inv = 1.0 / s
    cw, ch = W * inv, H * inv
    return overlay_im.transform(
        (W, H), Image.AFFINE,
        (inv, 0.0, W / 2.0 - cw / 2.0,
         0.0, inv, H / 2.0 - ch / 2.0),
        resample=Image.BICUBIC)


def cover_frame_sequence(overlay_im, cov_t, fps, body_dur, out_dir, cfg=None):
    """封面层「逐帧画好了、含动效与淡入淡出」的帧序列（渲染器与测试共用）。

    ffmpeg 的 scale 滤镜在 init eval_mode 下拒绝 t/n 表达式（实测 2026-10-08：
    「Expressions with frame variables 'n', 't', 'pos' are not valid in init eval_mode」），
    所以「封面随时间放大」不能直接写在 filter_complex 里——那条路一旦走通，门就是白的。
    这里在 PIL 侧按与 lib zoom_at 同一条公式画出每一帧：缩放系数按 zoom_at(t)、
    透明度按 timing 的淡入/平台/淡出三段，只落在封面可见窗 [0, gone_at) 内的帧上，
    其余帧全部是一张共享的全透明底。

    **先清目录、每帧必写。** `tmp/<书名>_tpl/` 按设计跨天保留，而这一目录里的帧是按序号
    被 image2 demuxer 连续读进去的：2026-10-08 复核复现过，旧版对 `gone_at` 之后的帧
    写了 `if not os.path.exists(path)` 的跳过——把 `fade_out_at` 从 2.5s 改成 1.0s 重跑一遍，
    f_0080 的不透明卡芯 bbox 与上一版**逐字节相同**，也就是「封面窗改小了」这次重渲把
    上一代的不透明帧原样留在盘上、下一期照原样搬回屏幕。这正是 AGENTS.md 点名的
    「换图没真的落盘」缺陷类在封面层的那一份，所以这里不留任何「文件在就算成功」的分支。
    """
    import glob
    import os
    cfg = cfg or load()
    m = motion(cfg)
    fade_in = float(cov_t["fade_in_seconds"])
    fade_out_at = float(cov_t["fade_out_at"])
    fade_out_seconds = max(1e-9, float(cov_t["fade_out_seconds"]))
    gone_at = float(cov_t["gone_at"])
    n = max(1, int(round(float(body_dur) * float(fps))))
    os.makedirs(out_dir, exist_ok=True)
    for stale in glob.glob(os.path.join(out_dir, "f_*.png")):
        os.unlink(stale)
    from PIL import Image
    blank = Image.new("RGBA", overlay_im.size, (0, 0, 0, 0))
    base = os.path.join(out_dir, "_blank.png")
    blank.save(base)
    paths = []
    for i in range(n):
        t = i / float(fps)
        path = os.path.join(out_dir, f"f_{i:04d}.png")
        if t < 0 or t >= gone_at - 1e-9:
            if os.path.exists(path):
                os.unlink(path)
            try:
                os.link(base, path)
            except OSError:
                blank.save(path)
            paths.append(path)
            continue
        if t < fade_in:
            a = min(max(t / max(fade_in, 1e-9), 0.0), 1.0)
        elif t < fade_out_at:
            a = 1.0
        else:
            a = max(0.0, 1.0 - (t - fade_out_at) / fade_out_seconds)
        s = zoom_at(t, cov_t, cfg) if m["type"] == "zoom_in" else 1.0
        frame = zoomed_overlay(overlay_im, s)
        if a < 1.0:
            alpha = frame.getchannel("A").point(lambda v: int(round(v * a)))
            frame.putalpha(alpha)
        frame.save(path)
        paths.append(path)
    return {"dir": out_dir, "count": n, "pattern": os.path.join(out_dir, "f_%04d.png")}


__all__ = ["LIB_PATH", "load", "geometry", "reserved_bands", "source_path",
           "provenance_path", "provenance", "provenance_ok", "fit", "overlay",
           "mask", "core_mask", "card_box", "timing", "motion", "motion_envelope",
           "zoom_at", "zoomed_overlay", "cover_frame_sequence"]
