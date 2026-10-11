#!/usr/bin/env python3
"""水波门禁的「封面同窗取证口径」回归（2026-10-08，《荒凉山庄》候选判红之后立的这一套）。

缺陷本体：用户当天要求「本书封面出现时增加放大的动态效果」，于是这一层在**整个涟漪窗口**
里既盖住涟漪中心、自己又在动。`check-water-ripple.py` 量的正是「径向逐帧差质心向外跑」，
卡片那份靠中心的、向外扩的位移就被读成涟漪的一部分——实测不排的时候质心**向内**收
（drift −80px / ρ −0.96），于是水波明明在画面上一圈圈外扩，门却报「只加了声音没加画面」。
第一版修法（一张静态并框 + 整环分母）把被卡片盖住的那几个环按整环面积稀释，实测吃掉
20~60px 外移量（真片 +15/ρ0.26 仍红）。采用的口径是：逐帧真实足迹（lib 的 overlay +
zoom_at 现算，含投影裙边）+ 每环按**自己可见的像素数**归一。

这些断言用 ffmpeg/PIL 造三段合成片，把三个方向一次钉死：
  A 有水波 + 有封面 → 新口径绿；不排红（那条 −80px 的假红）；静态并框的外移量显著低于新口径。
  B 只有封面动效、没有水波 → 排除之后**必须仍然红**（窗内逐帧差掉到 0.0001），
    而同一批像素不排时「有动效 + 向外 + ρ 达标」三条全中——这条就是「排除不是把门禁调松」
    的反证：卡片自己就能伪装出水，把它摘掉之后门必须还能看见「没有水」。
  C 有水波、画面里没有封面 → 按全画面判决，且「多排一张并不存在的卡片」的代价是有限的外移量
    缩水，不是翻红（兜底线索那一级的存在理由）。
  D 证人（渲染期 book-cover.json）：motion 合法时按证人、非法时退回 lib 并喊出来、
    封面图缺失时降级为全画面判决而不是崩。
  E 没有掩码时 `motion_series()` 与改动前那份表达式**逐位相同**（136 期无封面成片的判决
    不许因为这次改动而动）。

真实成片上的对照（新口径 ✅ +74px/ρ0.81、静态并框 ❌ +15px/ρ0.26）在 A4 里做条件断言：
tmp/ 是渲染中间产物，会被下一期清掉，缺席时打印 ⏭ 而不是把这条测试变成假绿。
"""
import json
import math
import os
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
LIB = os.path.join(ROOT, "scripts", "lib")
sys.path.insert(0, LIB)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import book_cover as B                                     # noqa: E402
from water_ripple import WATER_DUR, WATER_OFFSET, ripple, water_nframes  # noqa: E402

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "cwr", os.path.join(ROOT, "scripts", "check-water-ripple.py"))
CWR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CWR)

GATE = os.path.join(ROOT, "scripts", "check-water-ripple.py")
WORK = os.path.join(ROOT, "tmp", "water-cover-exclusion-test")
FPS = 30
TRIM = 1.0                    # 合成片的「正文起幅」在 1.0s，门用 --intro-trim 1.0 去问
DUR = 3.0
W, H = 720, 960
REQUIRED_DRIFT = CWR.DRIFT_PX * math.hypot(W, H) / math.hypot(720, 960)


# ---------------------------------------------------------------- fixtures
def base_plate(path):
    """低频径向渐变 + 细颗粒：像一张压暗的氛围图，涟漪位移在上面读得出逐帧差。"""
    rng = np.random.default_rng(20261008)
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float64)
    r = np.hypot(xs - W / 2, ys - H * 0.52) / math.hypot(W / 2, H / 2)
    lum = (92 - 34 * r + 10 * np.sin(3.1 * r)
           + 12 * np.sin(xs / 90.0) + 9 * np.cos(ys / 70.0))
    img = np.clip(lum[..., None] + rng.normal(0, 7, (H, W, 1)), 0, 255)
    Image.fromarray(np.repeat(img, 3, axis=2).astype(np.uint8)).save(path)
    return path


def jacket(path, w=600, h=900):
    """一张「像封面」的源图：硬边框 + 大块色，卡框里 alpha 铺满（真实微信读书封面就是这个形状）。"""
    a = np.zeros((h, w, 3), dtype=np.float64) + np.array((28, 44, 96), dtype=np.float64)
    yy, xx = np.mgrid[0:h, 0:w]
    a[..., 0] += 60 * (xx / w)
    a[..., 1] += 40 * (yy / h)
    a[(yy < 26) | (yy > h - 26) | (xx < 22) | (xx > w - 22)] = 235
    Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).save(path)
    return path


def cover_frames(jk, cfg):
    """正文起幅之后每一帧的封面叠层（走 lib 的 zoom_at + zoomed_overlay，同渲染器那条公式）。"""
    timing = B.timing(round(WATER_OFFSET + WATER_DUR + 0.6, 3), 0.64, cfg)
    ov0 = B.overlay(source=jk, cfg=cfg, canvas=(W, H))
    fade_in = float(timing["fade_in_seconds"])
    out_at = float(timing["fade_out_at"])
    out_span = float(timing["fade_out_seconds"])
    gone = float(timing["gone_at"])
    frames = {}
    for j in range(int(round(DUR * FPS)) + 2):
        t = j / FPS - TRIM
        if t < 0 or t >= gone - 1e-9:
            continue
        if t < fade_in:
            a = t / max(fade_in, 1e-9)
        elif t < out_at:
            a = 1.0
        else:
            a = max(0.0, 1.0 - (t - out_at) / out_span)
        fr = B.zoomed_overlay(ov0, B.zoom_at(t, timing, cfg))
        if a < 1.0:
            fr.putalpha(fr.getchannel("A").point(lambda v: int(round(v * a))))
        frames[j] = fr
    return frames, timing


def build_film(name, plate, covers, with_ripple, timing):
    d = os.path.join(WORK, name)
    seq = os.path.join(d, "f")
    os.makedirs(seq, exist_ok=True)
    base = Image.open(plate).convert("RGB")
    n_rip = water_nframes(FPS)
    for j in range(int(round(DUR * FPS))):
        tau = j / FPS - TRIM
        fr = base
        if with_ripple and 0.0 <= tau < WATER_DUR:
            k = int(round(tau * FPS))
            if k < n_rip:
                fr = ripple(base, (k + 1) / n_rip)
        out = fr.convert("RGBA")
        c = covers.get(j)
        if c is not None:
            out = Image.alpha_composite(out, c)
        out.convert("RGB").save(os.path.join(seq, f"f_{j:04d}.png"))
    mp4 = os.path.join(d, f"{name}.mp4")
    delay = int(round((TRIM + WATER_OFFSET) * 1000))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", str(FPS),
                    "-i", os.path.join(seq, "f_%04d.png"),
                    "-i", os.path.join(ROOT, "assets", "sfx", "water.mp3"),
                    "-filter_complex",
                    f"[1:a]adelay={delay}|{delay},apad=whole_dur={DUR}[a]",
                    "-map", "0:v", "-map", "[a]", "-t", f"{DUR}",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", mp4], check=True)
    return mp4


def witness(path, timing, jk, motion=None, source=None, box=None):
    json.dump({"timing": timing, "card_box_px": list(box or B.card_box(source=jk, canvas=(W, H))),
               "motion": B.motion() if motion is None else motion,
               "source": jk if source is None else source,
               "overlay_sha256": "test", "scene_xfade_seconds": 0.64,
               "bridge_exit_seconds": timing["bridge_exit"]},
              open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return path


def run_gate(film, extra=()):
    out = os.path.join(WORK, f"j-{abs(hash((film, tuple(extra))))}.json")
    p = subprocess.run([sys.executable, GATE, film, "--intro-trim", str(TRIM),
                        "--fps", str(FPS), "--json", out, *extra],
                       capture_output=True, text=True)
    r = json.load(open(out, encoding="utf-8"))
    v, ce = r["visual"], r["cover_exclusion"]
    return {"rc": p.returncode, "pass": r["pass"], "drift": v["drift_px"],
            "rho": v["radial_spearman"], "motion": v["motion_gray"],
            "required": v["required_drift_px"], "ok_audio": r["audio"]["ok"],
            "caliber": ce["caliber"], "area": ce["area_fraction"], "note": ce["evidence"],
            "stdout": p.stdout, "stderr": p.stderr}


# ---------------------------------------------------------------- checks
def check(fails, label, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {label}{('  ' + detail) if detail else ''}")
    if not cond:
        fails.append(label + (f"：{detail}" if detail else ""))


def main():
    if os.path.isdir(WORK):
        shutil.rmtree(WORK)
    os.makedirs(WORK)
    cfg = B.load()
    plate = base_plate(os.path.join(WORK, "plate.png"))
    jk = jacket(os.path.join(WORK, "book-cover.jpg"))
    covers, timing = cover_frames(jk, cfg)
    ew = ["--cover-witness", witness(os.path.join(WORK, "book-cover.json"), timing, jk),
          "--exclude-cover"]
    print("造三段合成片（每段 90 帧，走 lib 的 ripple() 与 zoomed_overlay()）…")
    f_rc = build_film("ripple-cover", plate, covers, True, timing)
    f_co = build_film("cover-only", plate, covers, False, timing)
    f_ro = build_film("ripple-only", plate, {}, True, timing)
    fails = []

    print("\nA 有水波 + 有封面（《荒凉山庄》那一版的形状）")
    a1 = run_gate(f_rc, ew)
    a2 = run_gate(f_rc)
    a3 = run_gate(f_rc, ew + ["--legacy-static-box"])
    check(fails, "A1 新口径（逐帧足迹 + 可见分母）绿",
          a1["pass"] and a1["drift"] >= a1["required"],
          f"drift={a1['drift']}（需 ≥{a1['required']:.0f}） ρ={a1['rho']} "
          f"面积={a1['area']}")
    check(fails, "A2 不排 → 假红：质心被卡片读成**向内**收",
          (not a2["pass"]) and a2["drift"] < -10 and a2["caliber"] == "none",
          f"drift={a2['drift']} ρ={a2['rho']}")
    check(fails, "A3 静态并框口径系统性吃掉外移量（不是并列的另一种正确答案）",
          a1["drift"] - a3["drift"] >= 15.0 and a1["rho"] - a3["rho"] >= 0.15,
          f"新 {a1['drift']}/ρ{a1['rho']} vs 并框 {a3['drift']}/ρ{a3['rho']}")
    real = os.path.join(ROOT, "tmp", "荒凉山庄_tpl", "荒凉山庄-final.mp4")
    if os.path.exists(real):
        r1 = subprocess.run([sys.executable, GATE, real, "--intro-trim", "9.72", "--fps", "30",
                             "--json", os.path.join(WORK, "real-new.json")],
                            capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, GATE, real, "--intro-trim", "9.72", "--fps", "30",
                             "--legacy-static-box",
                             "--json", os.path.join(WORK, "real-legacy.json")],
                            capture_output=True, text=True)
        jn = json.load(open(os.path.join(WORK, "real-new.json"), encoding="utf-8"))
        jl = json.load(open(os.path.join(WORK, "real-legacy.json"), encoding="utf-8"))
        check(fails, "A4 真片《荒凉山庄》：新口径绿、静态并框红",
              jn["pass"] and not jl["pass"],
              f"新 {jn['visual']['drift_px']}/ρ{jn['visual']['radial_spearman']} vs "
              f"并框 {jl['visual']['drift_px']}/ρ{jl['visual']['radial_spearman']}")
    else:
        print("  ⏭ A4 真片不在盘上（tmp/ 会被下一期清掉），只跑合成对照")

    print("\nB 只有封面动效、没有水波 —— 排除不许把缺陷洗成绿灯")
    b1 = run_gate(f_co, ew)
    b2 = run_gate(f_co)
    check(fails, "B1 排除卡片之后窗内逐帧差见底 → 红",
          (not b1["pass"]) and b1["motion"] < CWR.MIN_MOTION,
          f"motion={b1['motion']}（下限 {CWR.MIN_MOTION}） drift={b1['drift']}")
    check(fails, "B2 不排时卡片自己就伪装出「有动效 + 向外」两条（反证不空转）",
          b2["motion"] >= CWR.MIN_MOTION and b2["rho"] >= CWR.DRIFT_RHO,
          f"motion={b2['motion']} ρ={b2['rho']} drift={b2['drift']}（只差 DRIFT_PX 没中）")
    check(fails, "B3 两种口径都红：修的是取证，不是把门调松",
          not b1["pass"] and not b2["pass"])

    print("\nC 有水波、画面里没有封面 —— 多排的代价必须有限")
    c1 = run_gate(f_ro)
    c3 = run_gate(f_ro, ew)
    c2 = run_gate(f_ro, ["--exclude-cover"])
    check(fails, "C1 无证据 → 按全画面判决且绿",
          c1["pass"] and c1["caliber"] == "none" and "未检出封面层" in c1["note"],
          f"drift={c1['drift']} ρ={c1['rho']}")
    check(fails, "C2 只声明、算不出足迹 → 喊 warn 并退回全画面，判决逐位不变",
          "[warn]" in c2["stdout"] and c2["drift"] == c1["drift"] and c2["pass"],
          f"drift={c2['drift']}")
    check(fails, "C3 多排一张并不存在的卡片：仍绿，外移量缩水小于一个 DRIFT_PX",
          c3["pass"] and 0 < c1["drift"] - c3["drift"] < REQUIRED_DRIFT,
          f"{c1['drift']} → {c3['drift']}（代价 {c1['drift'] - c3['drift']:.1f}px）")

    print("\nD 渲染期证人（tmp/<书名>_tpl/book-cover.json）")
    d1 = run_gate(f_rc, ["--cover-witness",
                         witness(os.path.join(WORK, "wit-070.json"), timing, jk,
                                 motion={"type": "zoom_in", "zoom_from": 0.70,
                                         "zoom_to": 1.0, "device": "float_affine_per_frame",
                                         "ease": "cosine", "pace_min_px_per_second": 0}),
                         "--exclude-cover"])
    check(fails, "D1 证人写了合法 motion → 按证人算足迹（面积与 lib 口径不同）",
          "旁证 motion" in d1["note"] and d1["area"] != a1["area"] and d1["pass"],
          f"面积 {d1['area']} vs lib 口径 {a1['area']}")
    d2 = run_gate(f_rc, ["--cover-witness",
                         witness(os.path.join(WORK, "wit-112.json"), timing, jk,
                                 motion={"type": "zoom_in", "zoom_from": 1.0, "zoom_to": 1.12,
                                         "device": "float_affine_per_frame", "ease": "cosine"}),
                         "--exclude-cover"])
    check(fails, "D2 证人写了被 lib 否决的 1.12 → 喊出来并退回 lib 现值（逐位相同，不是崩）",
          "未通过 lib 校验" in d2["stdout"] and d2["area"] == a1["area"] and d2["pass"],
          f"面积={d2['area']}")
    d3 = run_gate(f_rc, ["--cover-witness",
                         witness(os.path.join(WORK, "wit-gone.json"), timing, jk,
                                 source=os.path.join(WORK, "no-such-jacket.jpg")),
                         "--exclude-cover"])
    check(fails, "D3 证人指向的封面图不在盘上 → warn + 全画面判决，不 traceback",
          "找不到封面图" in d3["stdout"] and d3["caliber"] == "none"
          and "Traceback" not in d3["stderr"], f"rc={d3['rc']}")

    print("\nE 无掩码路径与改动前那份表达式逐位相同（136 期无封面成片不许被这次改动碰到）")
    w_, h_, vf = CWR.probe_video(f_ro)
    n = water_nframes(int(round(vf)))
    pad = round(0.5 * vf)
    f0 = round((TRIM + WATER_OFFSET) * vf)
    fr = CWR.frames_at(f_ro, f0 - pad, n + 2 * pad, w_, h_)
    e_new, c_new, _ = CWR.motion_series(fr, w_, h_)
    idx, edges, counts = _reference_bins(w_, h_)
    lum = np.array([0.299, 0.587, 0.114])
    g = fr @ lum
    ref_e, ref_c = [], []
    for k in range(1, len(g)):
        diff = np.abs(g[k] - g[k - 1])
        prof = np.bincount(idx, weights=diff.ravel(), minlength=CWR.RAD_BINS) / counts
        ref_e.append(float(prof.sum() / CWR.RAD_BINS))
        ref_c.append(float((prof * edges).sum() / max(prof.sum(), 1e-9)))
    check(fails, "E1 exclude=None 的 energy/centroid 与旧式**逐位相同**",
          np.array_equal(e_new, np.array(ref_e)) and np.array_equal(c_new, np.array(ref_c)),
          f"{len(e_new)} 帧")
    zero = np.zeros((h_, w_), dtype=bool)
    zero[:, : w_ // 2] = True                  # 任一块**已知**叠层：左半幅不计
    e_vis, _, _ = CWR.motion_series(fr, w_, h_, per_frame_masks=[zero] * (len(fr) - 1),
                                   visible_denominator=True)
    e_full, _, _ = CWR.motion_series(fr, w_, h_, per_frame_masks=[zero] * (len(fr) - 1),
                                    visible_denominator=False)
    check(fails, "E2 分母开关是活的：同一块掩码下两种归一给的就是两个数",
          (not np.array_equal(e_new, e_vis)) and (not np.array_equal(e_vis, e_full))
          and not np.array_equal(e_new, e_full),
          f"整幅 {e_new.mean():.6f} / 可见分母 {e_vis.mean():.6f} / 整环分母 {e_full.mean():.6f}")

    print("\n" + ("❌ %d 条断言失败" % len(fails) if fails else "✅ 全部断言通过"))
    for f in fails:
        print("   - " + f)
    print(f"（夹具留在 {os.path.relpath(WORK, ROOT)}/，可逐帧目检）")
    return 1 if fails else 0


def _reference_bins(w, h):
    """照抄门自己的环形分箱（R_MIN/R_MAX/RAD_BINS/圆心），用来独立复算旧口径。"""
    cx, cy = w * 0.5, h * 0.52
    rmax = math.hypot(cx, cy)
    ys, xs = np.mgrid[0:h, 0:w]
    r = np.hypot(xs - cx, ys - cy) / rmax
    bins = np.linspace(CWR.R_MIN, CWR.R_MAX, CWR.RAD_BINS + 1)
    idx = np.clip(np.digitize(r.ravel(), bins) - 1, 0, CWR.RAD_BINS - 1)
    edges = 0.5 * (bins[:-1] + bins[1:])
    counts = np.bincount(idx, minlength=CWR.RAD_BINS).astype(np.float64)
    counts[counts == 0] = 1.0          # 与门里同一句（空环分子本来就是 0）
    return idx, edges, counts


if __name__ == "__main__":
    raise SystemExit(main())
