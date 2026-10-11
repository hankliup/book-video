#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""水波同窗门禁：交付前量化证明「水波视觉效果与水波音效同时出现」。

规则（AGENTS.md / docs/book-video-playbook.md）：`assets/sfx/water.mp3` 在片头→正文切点后
WATER_OFFSET 秒起播、持续 WATER_DUR 秒，同一段时间轴上画面必须出现一圈自中心外扩的涟漪。
只有声音没有画面 = 不合格；有画面但声音没到 / 两者不同窗 = 同样不合格。

常数真源是 scripts/lib/water_ripple.py（WATER_OFFSET / WATER_DUR）。本脚本只 import、
绝不另抄一份秒数——否则改了 lib 而门禁还按旧窗口判，就成了假绿。

两项独立测量
  A 声音：把成片窗口与 water.mp3 做归一化互相关（FFT 加速），得到相关峰值与音效实际起点。
          混音里还有旁白/BGM/翻页音，NCC 对增益不敏感，只对「形状在不在」敏感。
  B 画面：涟漪的签名是「逐帧差能量沿半径外移」。把每帧与前一帧的灰度差按半径分环取均值，
          得到运动能量剖面；涟漪波前会让该剖面的质心从画面中心一路推向四周。
          慢推镜与交叉溶解是各向同性的缓变，造不出这种「向外跑的质心」，
          所以用质心的 Spearman ρ（随帧号单调外移）+ 全程外移像素距离判定。
          亮度残差（波峰高光）不做判据：暗底氛围图上它被画面结构淹没，
          实测有波/无波都是 37.6 灰阶，分不开。

判据标定（2026-09-27，《小公务员之死》，motion 单位=环均值和/灰阶）
  有波成片  motion 1.99（窗口外对照 0.0027）/ drift +145px / ρ +1.00
  无波成片  motion 0.0008 —— 水波窗口整段落在静止 result-bridge 层，没有涟漪就等于完全不动
            drift +23px / ρ +0.28
  合成样片  motion 1.52 / drift +70px / ρ +0.87

封面同窗取证口径（2026-10-08，《荒凉山庄》，阈值一律未动）
  正文起幅那 1.4 秒里还叠着「本书封面」（lib/book-cover.json 的 slot），并按 lib 的 motion
  逐帧放大。卡片自己的逐帧差因此成了径向剖面里最大的一块能量，而它不是波。实测同一版候选帧、
  同一套阈值下的四种口径：
      不排（装配门禁当时的样子）        drift  -9.1px / ρ -0.25  ❌
      静态并框 + 整环分母（第一版）     drift +37.1px / ρ  0.58  ❌
      逐帧足迹 + 整环分母              drift +44.2px / ρ  0.67  ❌
      逐帧足迹 + 可见像素分母          drift +65.8px / ρ  0.71  ✅  ← 本文件采用的形状
  所以两个缺陷要一起修：掩码必须**逐帧**（卡片在放大，每刻盖住的地方都不一样），
  分母必须只数**这一环此刻可见**的像素（否则「可见像素的均值」被整环面积稀释成「整环的均值」，
  涟漪内环被截掉一半时外移量直接吃掉 20~30px）。
  代价不对称，决定了证据可以宽松：该排不排是 -9px / ρ-0.25 的**假红**（《一个青年艺术家的画像》
  成片同样 -18.4/ρ-0.61），而不该排却排了只是把 +113 降到 +80 —— 无封面的那版成片在三种口径下
  全绿。宁可用一张过宽的掩码，不可留一个会翻红的漏排；但掩码只排「lib 画得出来的那一层」，
  不是任意区域。
  反证（test-water-cover-exclusion.py 的对照）：静止底图 + 真实 cover-seq + 水波音效 = 只有卡片
  动效没有涟漪，逐帧排除后窗口能量掉到 MIN_MOTION 以下 → 红。排除不会把「没有波」洗成绿。

用法：
  python3 scripts/check-water-ripple.py "episodes/<书名>/renders/<书名>-final.mp4"
  ... [--intro-trim 3.0] [--fps 30] [--json /tmp/water.json] [--verbose]
      [--cover-witness tmp/<书名>_tpl/book-cover.json] [--exclude-cover] [--legacy-static-box]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts", "lib"))
from water_ripple import WATER_DUR, WATER_OFFSET, water_nframes  # noqa: E402  常数真源

SR = 48000
RAD_BINS = 48            # 逐帧差能量的半径分环数
R_MIN, R_MAX = 0.05, 1.25
SMOOTH = 5               # 质心序列滑动均值帧数（压掉编码噪声）
MIN_MOTION = 0.015       # 窗口内平均逐帧差灰阶下限：低于此=画面根本没动
DRIFT_PX = 45.0          # 质心全程外移下限（720x960 基准，按画幅对角线缩放）
DRIFT_RHO = 0.60         # 质心随帧号外移的 Spearman ρ 下限
ONSET_FRAC = 0.25        # 涟漪起振判据：达到窗口运动峰值 25% 的第一帧
TOL_FRAMES = 2.0         # 声画起点容差（帧）


def run(cmd):
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise SystemExit(f"CMD failed ({r.returncode}): {' '.join(map(str, cmd))}\n"
                         f"{r.stderr.decode('utf8', 'replace')[-800:]}")
    return r.stdout


def probe_video(path):
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=width,height,r_frame_rate", "-of", "json", path])
    st = json.loads(out)["streams"][0]
    num, den = st["r_frame_rate"].split("/")
    return int(st["width"]), int(st["height"]), float(num) / float(den)


def has_audio(path):
    out = run(["ffprobe", "-v", "error", "-select_streams", "a:0",
               "-show_entries", "stream=codec_name", "-of", "csv=p=0", path])
    return bool(out.strip())


def audio_at(path, t0, dur):
    """成片某段音频 → mono float64 @48k。"""
    raw = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t0:.4f}",
               "-t", f"{dur:.4f}", "-i", path, "-vn", "-ac", "1", "-ar", str(SR),
               "-f", "f32le", "pipe:1"])
    a = np.frombuffer(raw, dtype=np.float32).astype(np.float64)
    return a if a.size else np.zeros(1)


def load_mono(path, sr=SR):
    raw = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path,
               "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "pipe:1"])
    return np.frombuffer(raw, dtype=np.float32).astype(np.float64)


def ncc(mix, ref):
    """ref 在 mix 上滑动的归一化互相关 → (峰值, 峰值相对 mix 起点的秒偏移)。

    必须走 FFT：water.mp3 ≈5 万样本、窗口 ≈9 万样本，np.correlate 是 5e9 次乘加。
    """
    n = ref.size
    if mix.size < n + 2:
        return 0.0, float("nan")
    m = mix - mix.mean()
    r = ref - ref.mean()
    L = 1 << int(mix.size + n).bit_length()
    cc = np.fft.irfft(np.fft.rfft(m, L) * np.conj(np.fft.rfft(r, L)), L)[: mix.size - n + 1]
    e = np.concatenate([[0.0], np.cumsum(m * m)])
    win_energy = e[n: n + cc.size] - e[: cc.size]
    rn = float(np.sqrt((r * r).sum())) or 1.0
    corr = cc / (np.sqrt(np.maximum(win_energy, 1e-12)) * rn)
    k = int(np.argmax(corr))
    return float(corr[k]), k / SR


def frames_at(path, first, count, w, h):
    """按绝对帧号取帧（不加 -ss，避免抽到陈旧帧）。"""
    raw = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path,
               "-vf", f"select='between(n\\,{first}\\,{first + count - 1})'",
               "-vsync", "0", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"])
    need = count * w * h * 3
    if len(raw) < need:
        raise SystemExit(
            f"抽帧不足：需要 {need}B 只拿到 {len(raw)}B（帧 {first}..{first + count - 1}）")
    return np.frombuffer(raw[:need], dtype=np.uint8).reshape(count, h, w, 3).astype(np.float64)


def _rank(a):
    order = np.argsort(a)
    rr = np.empty(len(a), dtype=np.float64)
    rr[order] = np.arange(len(a), dtype=np.float64)
    return rr


def spearman(x, y):
    rx, ry = _rank(x), _rank(y)
    d, e = rx - rx.mean(), ry - ry.mean()
    if (d * d).sum() == 0 or (e * e).sum() == 0:
        return 0.0
    return float((d * e).sum() / math.sqrt((d * d).sum() * (e * e).sum()))


def _movavg(v, k):
    half = k // 2
    p = np.pad(v, half, mode="edge")
    return np.convolve(p, np.ones(k) / k, mode="valid")[: len(v)]


def motion_series(frames, w, h, exclude=None, per_frame_masks=None,
                  visible_denominator=True):
    """逐帧灰度差按半径分环取均值 → (环均值和=运动能量, 剖面质心, 归一化半径基准)。

    环心与 lib/water_ripple.py 的涟漪中心一致（cx=0.5W, cy=0.52H），
    否则量到的就不是同一个「波」。

    封面（或任何同期已知叠层）压在涟漪中心上时传 **per_frame_masks**：长度 len(frames)-1，
    第 k 项是「第 k+1 帧与第 k 帧之差该排掉的地方」，None 表示那一帧差不排。卡片按 lib 的
    motion 逐帧放大，每刻盖住的地方都不一样，所以掩码只能逐帧给；一张静态并框会把涟漪内环
    整段截掉。同时 visible_denominator=True 把每一环的分母改成**这一环此刻可见**的像素数，
    量到的才是「可见像素的平均半径」，而不是「被整环面积稀释过的半径」——两个缺陷要一起修，
    实测只修其一仍然翻红（见文件头那张四口径表）。

    exclude（单张静态掩码）只服务 --legacy-static-box 的对照，配 visible_denominator=False
    复现 2026-10-08 14:10 的第一版口径。三个参数都不传时分毫不差是原来那条算式
    （bincount(diff) / 整环像素数），没有封面层的期不受任何影响。
    """
    cx, cy = w * 0.5, h * 0.52
    rmax = math.hypot(cx, cy)
    ys, xs = np.mgrid[0:h, 0:w]
    r = np.hypot(xs - cx, ys - cy) / rmax
    bins = np.linspace(R_MIN, R_MAX, RAD_BINS + 1)
    idx = np.clip(np.digitize(r.ravel(), bins) - 1, 0, RAD_BINS - 1)
    counts = np.bincount(idx, minlength=RAD_BINS).astype(np.float64)
    counts[counts == 0] = 1.0
    edges = 0.5 * (bins[:-1] + bins[1:])
    lum = np.array([0.299, 0.587, 0.114])
    g = frames @ lum
    energy, centroid = [], []
    for k in range(1, len(frames)):
        diff = np.abs(g[k] - g[k - 1])
        mask = per_frame_masks[k - 1] if per_frame_masks is not None else exclude
        if mask is None:
            prof = np.bincount(idx, weights=diff.ravel(),
                               minlength=RAD_BINS) / counts
        else:
            keep = (~mask).ravel()
            num = np.bincount(idx, weights=diff.ravel() * keep,
                              minlength=RAD_BINS)
            if visible_denominator:
                den = np.bincount(idx, weights=keep.astype(np.float64),
                                  minlength=RAD_BINS)
                den[den == 0] = np.inf
            else:
                den = counts
            prof = num / den
        energy.append(float(prof.sum() / RAD_BINS))
        centroid.append(float((prof * edges).sum() / max(prof.sum(), 1e-9)))
    return np.array(energy), np.array(centroid), rmax


def episode_from_path(video_path):
    """从成片路径认本期书名：episodes/<书名>/… → tmp/<书名>_tpl/… → <书名>-final.mp4。

    这是「期」的身份，不是「像素里有没有封面层」的证据 —— 后者由 cover_layer() 的
    旁证/声明/兜底线索四级决定，两级各管一件事，别把它们混成一次猜测。
    """
    parts = video_path.replace("\\", "/").split("/")
    for i, p in enumerate(parts):
        if p == "episodes" and i + 1 < len(parts):
            return parts[i + 1]
        if p.endswith("_tpl"):
            return p[:-4]
    base = os.path.basename(video_path)
    stem = os.path.splitext(base)[0]
    for tail in ("-final", "-candidate", "-body", "-with-cover"):
        if stem.endswith(tail):
            stem = stem[:-len(tail)]
            break
    return stem or None


def cover_layer(video_path, a, ep_name):
    """这版成片像素里有没有「本书封面」层？有就带回画它需要的叠层、时刻与证据来源。

    几何与动效一律走 lib/book_cover.py（渲染器用的就是同一个函数），门不另抄坐标。
    时刻优先用**渲染期旁证**里的 timing —— 淡出长度是本期切镜表的事，不该由门猜。

    证据优先级（高→低）：
      1 `--cover-witness PATH`  调用方给定的渲染期旁证，最硬；
      2 `tmp/<书名>_tpl/book-cover.json`  按期名推得的那份旁证（渲染器落下的）；
      3 `--exclude-cover`       渲染器的显式声明（它刚把这层叠进背景层）；
      4 episodes/<书名>/book-cover.jpg 在场  ← **兜底线索，不是事实**：素材在盘上不等于
         这一版成片里有它。留下它是因为代价不对称（见文件头）：漏排是 -9px 的假红，
         多排只把 +113 降到 +80。命中兜底时打印出来，口径从哪来要看得见。
    四条都没有 → None，按全画面判决（宁可多判不可漏判）。
    """
    sys.path.insert(0, os.path.join(ROOT, "scripts", "lib"))
    import book_cover as B
    cfg = B.load()
    ep_dir = os.path.join(ROOT, "episodes", ep_name) if ep_name else None

    wit_path = a.cover_witness or ""
    evidence = "cover-witness" if a.cover_witness else ""
    if not wit_path and ep_name:
        guess = os.path.join(ROOT, "tmp", f"{ep_name}_tpl", "book-cover.json")
        if os.path.exists(guess):
            wit_path, evidence = guess, "tmp 旁证（按期名推得）"
    asset = B.source_path(ep_dir, cfg) if ep_dir else None
    if not evidence and a.exclude_cover:
        evidence = "exclude-cover（渲染器声明）"
    if not evidence and asset:
        evidence = "book-cover.jpg（兜底线索）"
    if not evidence:
        return None

    src = asset
    if src is None and wit_path and os.path.exists(wit_path):
        s = (json.load(open(wit_path, encoding="utf-8")) or {}).get("source") or ""
        src = next((c for c in (os.path.join(ROOT, s),
                                os.path.join(ep_dir, s) if ep_dir else "")
                    if c and os.path.exists(c)), None)
    if src is None:
        print(f"  [warn] 声明了封面层（{evidence}）但找不到封面图，无法算逐帧足迹 —— "
              f"按全画面判决（这多半是假红：先确认 episodes/<书名>/book-cover.jpg 在不在）")
        return None

    timing = None
    cfg_m = cfg
    canvas = tuple(cfg.get("canvas") or [720, 960])
    if wit_path and os.path.exists(wit_path):
        wit = json.load(open(wit_path, encoding="utf-8")) or {}
        timing = wit.get("timing")
        # 旁证若记了渲染时用的 motion，就按旁证走：lib 的 motion 是 2026-10-08 一天里改过
        # 两次的东西（1.0→1.12，15:00 又改成 0.82→1.0），而掩码要盖的是**那一版像素**里的卡片。
        wm = wit.get("motion")
        if isinstance(wm, dict) and wm.get("zoom_from") is not None:
            cfg_m = dict(cfg)
            cfg_m["motion"] = dict(cfg.get("motion") or {})
            cfg_m["motion"].update({k: wm[k] for k in
                                    ("type", "zoom_from", "zoom_to") if k in wm})
            try:
                B.motion(cfg_m)                      # 只用 lib 的校验函数探一遍
                evidence += " + 旁证 motion"
            except Exception as exc:                 # 旁证写了个 lib 认不出的动效
                print(f"  [warn] 旁证里的 motion {wm} 未通过 lib 校验（{exc}）："
                      f"掩码改用 lib 此刻的动效 —— 这版足迹可能与像素不符，先修证人")
                cfg_m = cfg
        # 卡框对不上 = 「lib 改了、成片还是旧几何」的正面证据：喊出来，不默默用错掩码
        box_wit = wit.get("card_box_px")
        if box_wit and len(box_wit) == 4:
            box_lib = B.card_box(source=src, cfg=cfg_m, canvas=canvas)
            gap_px = max(abs(float(x) - float(y)) for x, y in zip(box_wit, box_lib))
            if gap_px > 2.0:
                print(f"  [warn] 旁证卡框 {tuple(box_wit)} 与当前 lib 推出的 {tuple(box_lib)}"
                      f" 差 {gap_px:.0f}px：掩码按 lib 现算，可能对不上这版像素里的实际卡片。"
                      f"重渲一次让旁证与 lib 同源（本门不因此翻红，但这是待办）")
    if not timing:
        fade_out_at = round(WATER_OFFSET + WATER_DUR, 4)
        timing = {"fade_in_seconds": WATER_OFFSET, "full_from": WATER_OFFSET,
                  "fade_out_at": fade_out_at, "fade_out_seconds": 0.64,
                  "gone_at": round(fade_out_at + 0.64, 4)}
        # 兜底的淡出长度取 0.64（正文交叉溶解的常用值）。它在本门里其实一次都到不了：
        # 取样数组最晚只到 t≈1.85s，而 gone_at ≥2.02s，所以 gone_at 只给逐帧足迹画上界，
        # 不进判决；真正要紧的 fade_out_at = 涟漪收口，由 lib 的水波常量推导，不是猜的。
    return {"overlay": B.overlay(source=src, cfg=cfg_m), "timing": timing,
            "cfg": cfg_m, "evidence": evidence, "source": src, "canvas": canvas}


def per_frame_cover_masks(ctx, count, pad, fps, h, w, first_body_offset=0.0):
    """每个逐帧差一条掩码：第 k 项 = 帧 k-1 与帧 k 上封面墨迹（alpha>0）的并集。

    正文相对时刻按门自己的窗口映射推：t(j) = WATER_OFFSET + (j - pad)/fps，
    所以掩码和涟漪窗口共用同一个时间基，不引入第二个正文起点口径。
    """
    import book_cover as B
    import numpy as _np
    from PIL import Image as _Image
    gone = float(ctx["timing"]["gone_at"])
    zero = _np.zeros((h, w), dtype=bool)

    def alpha(i):
        t = WATER_OFFSET + (i - pad) / fps + first_body_offset
        if t < 0.0 or t >= gone - 1e-9:
            return zero
        s = B.zoom_at(t, ctx["timing"], ctx["cfg"])
        frame = B.zoomed_overlay(ctx["overlay"], s)
        if frame.size != (w, h):
            frame = frame.resize((w, h), _Image.NEAREST)
        return _np.asarray(frame.convert("RGBA"))[..., 3] > 0

    out = []
    prev = alpha(0)
    for k in range(1, count):
        cur = alpha(k)
        out.append(prev | cur)
        prev = cur
    return out


def legacy_union_mask(w, h):
    """2026-10-08 14:10 第一版的形状：一张静态并框（含 -20px 余量），配整环分母。
    只留给 --legacy-static-box 做 A/B 与回归对照，生产路径不再走它。
    并框按 lib **此刻**的 motion 上界取，所以它复现的是「形状」而不是当年那个数字：
    motion 在同一天 14:35 是 1.0→1.12，15:00 又改成 0.82→1.0，同一个开关在两个时刻
    量出的百分比本来就不同。取 max(1.0, zoom_to) 是因为 scale=1.0 就是 card_box 本身，
    对照口径不该因为「本期缩着出场」而比卡片还小——那会让它少排、把红显得比实际更红。"""
    import book_cover as B
    cfg = B.load()
    slot = cfg["slot"]
    y0, y1 = int(slot["y0_px"]), int(slot["y1_px"])
    zmax = max(1.0, float(B.motion(cfg)["zoom_to"]))
    ccy, ccx = (y0 + y1) / 2.0, w * 0.5
    dy = (y1 - y0) / 2.0 * zmax
    dx = dy * 0.75
    m = np.zeros((h, w), dtype=bool)
    m[max(0, int(ccy - dy - 20)):min(h, int(ccy + dy + 20)),
       max(0, int(ccx - dx - 20)):min(w, int(ccx + dx + 20))] = True
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--intro-trim", type=float, default=3.0)
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--sfx", default=os.path.join(ROOT, "assets", "sfx", "water.mp3"))
    ap.add_argument("--audio-min", type=float, default=0.35, help="NCC 峰值下限")
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--exclude-cover", action="store_true",
                    help="调用方（渲染器）声明本期成片的正文起幅里叠了本书封面层；"
                         "取证按逐帧可见足迹排除，配 --cover-witness 最准")
    ap.add_argument("--cover-witness", default="",
                    help="渲染期旁证 tmp/<书名>_tpl/book-cover.json：带着这一版实际用的 timing")
    ap.add_argument("--episode", default="",
                    help="书名。成片在 episodes/ 下时能自己推出来；渲染中间产物在 tmp/ 下时请显式传")
    ap.add_argument("--legacy-static-box", action="store_true",
                    help="退回 2026-10-08 14:10 第一版口径（一张静态并框 + 整环分母）做 A/B 对照，"
                         "生产不要用：它把涟漪内环整段截掉，实测吃掉 20~30px 外移量")
    a = ap.parse_args()

    video = os.path.abspath(a.video)
    w, h, vfps = probe_video(video)
    fps = a.fps if abs(a.fps - vfps) < 0.5 else vfps
    start = round(a.intro_trim + WATER_OFFSET, 3)            # 成片时间轴上的水波起点
    n = water_nframes(int(round(fps)))
    f0 = round(start * fps)
    scale = math.hypot(w, h) / math.hypot(720, 960)

    # ---- A 声音：窗口里有没有 water.mp3 的形状
    guard = 0.45
    if has_audio(video):
        mix = audio_at(video, start - guard, WATER_DUR + 2 * guard)
        corr, lag = ncc(mix, load_mono(a.sfx))
        sfx_onset = (start - guard) + lag                    # 音效实际起点（成片秒）
    else:
        corr, sfx_onset = 0.0, float("nan")
    sfx_off = (sfx_onset - start) * fps

    # ---- B 画面：逐帧差质心有没有向外跑
    pad = round(0.5 * fps)
    fr = frames_at(video, f0 - pad, n + 2 * pad, w, h)
    # 封面同窗：正文起幅那张本书封面正好压在涟漪中心上，还按 lib 的 motion 逐帧放大。
    # 它的逐帧差不是波，却比波更靠中心，于是质心被拽回去 ——《荒凉山庄》候选实测
    # 不排 -9.1px/ρ-0.25，静态并框 +37.1/0.58，逐帧足迹+可见分母 +65.8/0.71（详见文件头）。
    ep_name = a.episode or episode_from_path(video)
    ctx = cover_layer(video, a, ep_name) if (
        a.cover_witness or a.exclude_cover or ep_name) else None
    masks, mask_note, mask_area = None, "未检出封面层：按全画面判决（宁可多判不可漏判）", 0.0
    if a.legacy_static_box:
        box = legacy_union_mask(w, h)
        masks, mask_area = [box] * (len(fr) - 1), float(box.mean())
        mask_note = (f"⚠️ --legacy-static-box 对照口径：静态并框 {mask_area * 100:.1f}% 画面"
                     f" + 整环分母（14:10 第一版，会把涟漪内环稀释掉）")
    elif ctx is not None:
        masks = per_frame_cover_masks(ctx, len(fr), pad, fps, h, w)
        mask_area = float(np.mean([m.mean() for m in masks]))
        mask_note = (f"逐帧排除封面可见足迹 {mask_area * 100:.1f}% 画面，每环按可见像素归一"
                     f"（依据：{ctx['evidence']}）")
    print(f"  [water-cover] {mask_note}")
    energy, centroid, rmax = motion_series(
        fr, w, h, per_frame_masks=masks,
        visible_denominator=not a.legacy_static_box)
    i0 = pad - 1                       # energy[k] 是帧 (f0-pad+k) 与前一帧之差
    win_e = energy[i0: i0 + n - 1]
    out_e = np.concatenate([energy[: max(0, i0 - pad + 1)], energy[i0 + n - 1:]])
    cen = _movavg(centroid[i0: i0 + n - 1], SMOOTH)
    third = max(1, (n - 1) // 3)
    motion = float(win_e.mean()) if win_e.size else 0.0
    ctrl = float(np.median(out_e)) if out_e.size else 0.0
    drift = float((cen[-third:].mean() - cen[:third].mean()) * rmax) if cen.size else 0.0
    rho = spearman(np.arange(len(cen)), cen) if cen.size else 0.0
    onset_rel = None
    if win_e.size and win_e.max() > 0:
        for j, v in enumerate(win_e):
            if v >= ONSET_FRAC * win_e.max():
                onset_rel = j
                break
    vis_onset = (f0 + onset_rel) / fps if onset_rel is not None else float("nan")
    gap = (vis_onset - sfx_onset) * fps if onset_rel is not None else float("nan")

    ok_audio = corr >= a.audio_min
    ok_visual = motion >= MIN_MOTION and drift >= DRIFT_PX * scale and rho >= DRIFT_RHO
    ok_coincident = onset_rel is not None and abs(gap) <= TOL_FRAMES

    def _r(v, nd=3):
        return None if v is None or not np.isfinite(v) else round(float(v), nd)

    res = {
        "video": video, "fps": fps, "intro_trim": a.intro_trim,
        "water_start": start, "water_dur": WATER_DUR, "water_frames": n,
        "constants_source": "scripts/lib/water_ripple.py",
        "audio": {"ncc": _r(corr, 4), "onset_s": _r(sfx_onset), "offset_frames": _r(sfx_off, 2),
                  "threshold": a.audio_min, "ok": bool(ok_audio)},
        "visual": {"motion_gray": _r(motion, 4), "control_gray": _r(ctrl, 4),
                   "min_motion": MIN_MOTION, "drift_px": _r(drift, 1),
                   "required_drift_px": _r(DRIFT_PX * scale, 1),
                   "radial_spearman": _r(rho, 3), "required_spearman": DRIFT_RHO,
                   "onset_frame_in_window": onset_rel, "onset_s": _r(vis_onset),
                   "ok": bool(ok_visual)},
        "coincidence": {"visual_minus_audio_frames": _r(gap, 2),
                        "tolerance_frames": TOL_FRAMES, "ok": bool(ok_coincident)},
        "cover_exclusion": {"applied": masks is not None,
                            "caliber": ("legacy-static-box" if a.legacy_static_box
                                        else ("per-frame-visible" if masks is not None
                                              else "none")),
                            "area_fraction": _r(mask_area, 4), "evidence": mask_note},
        "pass": bool(ok_audio and ok_visual and ok_coincident),
    }
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=2)
    if a.verbose:
        print(json.dumps({"energy": [round(float(v), 4) for v in win_e],
                          "centroid_r": [round(float(v), 3) for v in cen]}, ensure_ascii=False))

    tag = "✅" if res["pass"] else "❌"
    print(f"[water-audio] NCC={corr:.3f}（阈值 {a.audio_min}） 音效起点 {sfx_onset:.3f}s "
          f"偏离设计点 {sfx_off:+.2f} 帧  " + ("✅" if ok_audio else "❌"))
    print(f"[water-video ] 逐帧差 {motion:.4f} 灰阶（需 ≥{MIN_MOTION}，窗口外对照 {ctrl:.4f}）"
          f" 质心外移 {drift:+.0f}px（需 ≥{DRIFT_PX * scale:.0f}） ρ={rho:+.2f}（需 ≥{DRIFT_RHO}）  "
          + ("✅" if ok_visual else "❌"))
    if onset_rel is None:
        print("[water-coinc] ❌ 水波窗口内没有起振的涟漪 —— 只有音效没有画面，违反常驻规则")
    else:
        print(f"[water-coinc] 画面涟漪起点 {vis_onset:.3f}s（窗口第 {onset_rel} 帧）"
              f" vs 音效起点 {sfx_onset:.3f}s → 相差 {gap:+.2f} 帧  "
              + ("✅" if ok_coincident else "❌"))
    print(f"{tag} {os.path.relpath(video, ROOT)}  水波窗口 {start:.2f}–{start + WATER_DUR:.2f}s"
          f"（常数来自 scripts/lib/water_ripple.py：offset={WATER_OFFSET} dur={WATER_DUR}）")
    if res["pass"]:
        return 0
    if not ok_audio and not ok_visual:
        print("   成因：水波音效与涟漪都不在 —— 混音没引 water.mp3，渲染器也没走 lib/water_ripple.py")
    elif not ok_visual:
        print("   成因：只加了水波音效、画面没有涟漪（或涟漪没外扩）。兜底渲染器必须在**背景层**"
              "调 ripple()；已叠字幕的成片不能事后补（会扭糊字幕），必须重渲正文背景。")
        if masks is None and ep_name and os.path.exists(
                os.path.join(ROOT, "episodes", ep_name, "book-cover.jpg")):
            print(f"   但本期有 episodes/{ep_name}/book-cover.jpg —— 如果这一版确实把封面叠进了"
                  f"正文起幅，红的是取证口径而不是画面：把渲染期旁证传进来"
                  f"（--cover-witness tmp/{ep_name}_tpl/book-cover.json）或重渲留证。")
    elif not ok_audio:
        print("   成因：画面有涟漪但水波音效没响/形状对不上 —— 检查 amix 里的 water.mp3 与 adelay。")
    else:
        print(f"   成因：声画起点差 {gap:+.2f} 帧，超出 ±{TOL_FRAMES} 容差 —— 两侧必须共用 "
              "lib/water_ripple.py 的 WATER_OFFSET，不要在渲染器里硬编码秒数。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
