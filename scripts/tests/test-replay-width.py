#!/usr/bin/env python3
"""重放核对的**缩放档位**回归：证明的是「这张照片」，不是「这一次缩放」。

为什么钉这一条（2026-10-06《猎人笔记》atmosphere-5 / Pexels #32176786）：取图脚本
`curl_cdn_widths()` 的宽度阶梯在 1600 档被 curl 18（transfer closed）打断后会降到 1280，
而 `verify-pexels-fetch.py` 无条件按 1600 重放。Pexels CDN 的 1600 与 1280 是同一张主图的
**两种渲染**，`crop_3x4()` 再各自 LANCZOS 到 1024x1365，像素自然不等。实测三档：

    按 1280 重放（取图当时那一档）：平均差 0.000｜峰值 0｜过冲 0.000%
    按 1600 重放（门禁当时的做法）：平均差 5.0｜峰值 71        → 判成「不符」
    按 1024 重放：                  平均差 10.80｜峰值 136｜过冲 3.689%

也就是说「不符」这个判决量的其实是档位，而这条门禁要回答的只有一个问题：**盘上这张图是不是
清单声称的那张照片**。假红有实打实的代价——它会把人推去换掉一张没有错的选型（EDGE_FRAC_TOL
那段注释就是为同一个陷阱写的），而这一次换图的成本是整期正文集重取、重调色、重渲。

修法两层，这里两层都要判，而且两个方向都要判：
  ① `fetch-pexels-by-id.py` 的 `certify()` 把取图**真正落地的源图像素宽**写进
     `_provenance.json`（不是请求的档位：主图不足 1600 时请求 1600 回的可能是 1280）。
  ② `verify-pexels-fetch.py` 的 `replay_width()` 按「标记记的宽度 → `_original/` 的像素宽
     反推 → 默认 1600」三级取证决定重放档位；任何一档不足 TARGET_W 一律报**无从核对**，
     因为那时裁切是放大，比出来的数字既不能算相符也不能算不符。
  ③ `fetch_bytes()` 拿回的宽度与所求不一致时报**未能证明**，绝不报「不符」——
     这条是整件事的镜像：档位不同 ≠ 照片不同。

判据不靠网络：下载用 stub 顶掉，喂进去的是合成图，所以「健康跑绿 / 错照片跑红 / 换档跑
无从证明」三个方向都能当场复现，不必等 CDN 抽风。
"""
import io
import json
import os
import shutil
import sys
import types

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "scripts", "lib"))
from pexels_crop import TARGET_H, TARGET_W, crop_3x4  # noqa: E402

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "verify_pexels_fetch", os.path.join(ROOT, "scripts", "verify-pexels-fetch.py"))
V = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(V)

FAILS = []
TMP = os.path.join(ROOT, "tmp", "replay-width-test")
# 合成 id 要一眼假：data/*.json 会被 lib/pexels_index 全库扫，测试期绝不能撞上真实选型
FAKE_ID = 9000999
EP = os.path.join(TMP, "episodes", "RWTEST-重放档位")
MAN = os.path.join(ROOT, "data", "RWTEST-重放档位_pexels_ids.json")


def _cleanup():
    """夹具必须清干净：data/*.json 是跨期去重索引的输入，留一份假的会让以后的批次少一张图。
    中途抛异常也要清（atexit），所以进来先删一次、出去再删一次。"""
    shutil.rmtree(TMP, ignore_errors=True)
    if os.path.exists(MAN):
        os.remove(MAN)


_atexit_cleanup = __import__("atexit").register(_cleanup)
_cleanup()


def check(label, got, want):
    if got != want:
        FAILS.append(f"{label}: 期望 {want!r}，实得 {got!r}")
        print(f"  ❌ {label}: 期望 {want!r}，实得 {got!r}")
    else:
        print(f"  ✅ {label}")


def expect_true(label, cond, detail=""):
    if not cond:
        FAILS.append(f"{label} 不成立 {detail}")
        print(f"  ❌ {label} {detail}")
    else:
        print(f"  ✅ {label}")


def synth(width, seed):
    """确定性合成一张 3:4 竖图：低频渐变底 + 若干硬边色块。

    形状是刻意选的，因为这条测试要的是**分离度**：
      * 同一张图过一遍 JPEG 再裁回 1024x1365 —— 平均差 0.56、过冲 0.11%（在容差内，绿）；
      * 换一张照片 —— 平均差 45、过冲 57%（红得没有歧义）；
      * 同一图案换一档缩放 —— 平均差 10、过冲 12%（这正是 2026-10-06 那次假红的量级，
        实测真图是 5.0/71，同数量级）。
    逐像素高噪声的合成图做不到这一点：JPEG 本身就能把它抖出 7 的平均差，健康用例自己先红。
    """
    rng = np.random.default_rng(seed)
    h = int(round(width * 1.5))
    yy, xx = np.mgrid[0:h, 0:width]
    base = 90 + 50 * np.sin(xx / (width / 6.0)) + 30 * np.cos(yy / (h / 8.0))
    arr = np.stack([base] * 3, axis=-1).astype(np.float32)
    for _ in range(40):
        x0 = int(rng.integers(0, max(width - 80, 1)))
        y0 = int(rng.integers(0, max(h - 80, 1)))
        w = int(rng.integers(40, 260))
        hh = int(rng.integers(40, 300))
        v = rng.integers(0, 256, size=3)
        arr[y0:min(h, y0 + hh), x0:min(width, x0 + w)] = v
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def jpeg_bytes(img):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def write_fixture(source_w, with_marker_width=True, write_original=True):
    """按 fetch 的口径造一份「正文段只有一张图」的期目录 + 清单 + 溯源标记。"""
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(os.path.join(EP, "images-body", "_raw"))
    if write_original:
        os.makedirs(os.path.join(EP, "images-body", "_original"))
    src = synth(source_w, seed=7)
    crop = crop_3x4(src, None)
    crop.save(os.path.join(EP, "images-body", "_raw", "atmosphere-1.png"))
    if write_original:
        src.save(os.path.join(EP, "images-body", "_original", "atmosphere-1.png"))
    ent = {"name": "atmosphere-1", "photo_id": FAKE_ID, "segment": "body",
           "dir": "images-body",
           "image_url": f"https://images.pexels.com/photos/{FAKE_ID}/pexels-photo-{FAKE_ID}.jpeg",
           "alt": "合成测试图，不是真实选型"}
    json.dump([ent], open(MAN, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    marker = {"note": "test fixture", "version": 1, "entries": {"atmosphere-1": {
        "photo_id": FAKE_ID, "crop_box": None,
        "raw_sha256": "0" * 64,
        "source_url": ent["image_url"],
        "fetched_at": "2026-10-06T00:00:00"}}}
    if with_marker_width:
        marker["entries"]["atmosphere-1"]["source_width"] = source_w
    json.dump(marker, open(os.path.join(EP, "images-body", "_provenance.json"), "w",
                           encoding="utf-8"), ensure_ascii=False, indent=1)
    return src


def run_verify(cdn):
    """跑真 main()：cdn(w) 决定「CDN 这次给回什么」，返回 (exit_code, 全部输出)。"""
    asked = []
    real_run = V.subprocess.run

    def fake_run(cmd, *a, **kw):
        url = cmd[-1]
        w = int(url.split("w=")[1].split("&")[0])
        asked.append(w)
        img = cdn(w)
        if img is None:
            return types.SimpleNamespace(returncode=56, stdout=b"", stderr=b"")
        return types.SimpleNamespace(returncode=0, stdout=jpeg_bytes(img), stderr=b"")

    V.subprocess.run = fake_run
    out = io.StringIO()
    real_stdout, sys.stdout = sys.stdout, out
    try:
        sys.argv = ["verify-pexels-fetch.py", EP]
        try:
            V.main()
            code = 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        V.subprocess.run = real_run
        sys.stdout = real_stdout
    return code, out.getvalue(), asked


print("=== ① 取证宽度的三级来源 ===")
src1280 = write_fixture(1280, with_marker_width=True)
seg_dir = os.path.join(EP, "images-body")
mark = json.load(open(os.path.join(seg_dir, "_provenance.json")))["entries"]["atmosphere-1"]
w, why = V.replay_width(seg_dir, "atmosphere-1", mark)
check("标记记了 1280 → 按 1280 重放（而不是硬编码的 1600）", (w, "标记" in why), (1280, True))
expect_true("这条断言不是空的：结果必须区别于旧门禁的固定 1600", w != V.MIN_SRC_W, f"实得 {w}")

w2, why2 = V.replay_width(seg_dir, "atmosphere-1", {})
check("历史标记没有宽度 → 按 _original 的像素宽反推", (w2, "_original" in why2), (1280, True))

os.remove(os.path.join(seg_dir, "_original", "atmosphere-1.png"))
w3, why3 = V.replay_width(seg_dir, "atmosphere-1", {})
check("既无宽度也无 _original → 回默认档 1600", (w3, "默认档" in why3), (1600, True))

w4, why4 = V.replay_width(seg_dir, "atmosphere-1", {"source_width": 800})
check("标记记的宽度不足 TARGET_W → 无从核对（不许悄悄改用 1600 硬比）", (w4, w4 is None), (None, True))
expect_true("800 档被拒时给出的理由提到放大", "放大" in (why4 or ""), f"理由：{why4}")
src1280.save(os.path.join(seg_dir, "_original", "atmosphere-1.png"))

print("\n=== ② 端到端：健康 / 错照片 / 换档 三个方向 ===")
write_fixture(1280, with_marker_width=True)
code, out, asked = run_verify(lambda w: synth(w, seed=7) if w == 1280 else None)
expect_true("按取图那一档重放 → 跑绿", code == 0 and "✅" in out,
            f"rc={code}\n{out[-400:]}")
check("重放只请求了记录的那一档（没有多打一次 1600）", asked, [1280])

code, out, asked = run_verify(lambda w: synth(w, seed=99) if w == 1280 else None)
expect_true("同档位但换了照片 → 判红「不符」", code == 1 and "不符" in out,
            f"rc={code}\n{out[-400:]}")

code, out, asked = run_verify(lambda w: synth(1600, seed=7) if w == 1280 else None)
expect_true("同一张照片但 CDN 回了另一档缩放 → 未能证明", code == 1 and "未能证明" in out,
            f"rc={code}\n{out[-400:]}")
expect_true("缩放不同绝不能被判成「不符」（这条是整起事故的反面）", "不符" not in out,
            out[-400:])
# 非空断言：不同档位的裁切结果本身确实差很多（实测真图 5.0/71，合成图同一量级），
# 所以「按错档重放」一定会翻红——这条测试测的不是恒等，而是档位选择。
diff_rung = float(np.abs(np.asarray(crop_3x4(synth(1600, 7), None), dtype=np.int16)
                         - np.asarray(crop_3x4(synth(1280, 7), None), dtype=np.int16)).mean())
expect_true("换档的差异量级远大于 MEAN_TOL（否则整个档位问题测不出来的）",
            diff_rung > V.MEAN_TOL * 3, f"平均差 {diff_rung:.2f} vs 容差 {V.MEAN_TOL}")

print("\n=== ③ 历史期（标记无宽度）也能走通 ===")
write_fixture(1280, with_marker_width=False)
code, out, asked = run_verify(lambda w: synth(w, seed=7) if w == 1280 else None)
expect_true("靠 _original 反推档位 → 跑绿且按 1280 取", code == 0 and asked == [1280],
            f"rc={code} asked={asked}\n{out[-400:]}")
expect_true("输出里看得见档位是从 _original 反推的", "_original" in out, out[-300:])

print("\n=== ④ 静态接线：判据只有一份 ===")
fetch_src = open(os.path.join(ROOT, "scripts", "fetch-pexels-by-id.py"), encoding="utf-8").read()
ver_src = open(os.path.join(ROOT, "scripts", "verify-pexels-fetch.py"), encoding="utf-8").read()
for tok, where in [('"source_width": int(source_width)', "fetcher certify 写宽度"),
                   ("source_width=w0", "fetcher 把真正落地的源图宽传给 certify"),
                   ("return raw, w", "curl_cdn_widths 把落到的档位交回调用方")]:
    expect_true(f"fetch-pexels-by-id.py 仍有 {where}", tok in fetch_src, tok)
for tok in ["def replay_width(", "want_w=MIN_SRC_W", "img.width != want_w",
            "replay_width(seg_dir, name", "不是同一次缩放"]:
    expect_true(f"verify-pexels-fetch.py 仍有 {tok}", tok in ver_src, tok)
expect_true("verify 不再用「只比下限」的旧判据（img.width < MIN_SRC_W）",
            "img.width < MIN_SRC_W" not in ver_src)
expect_true("verify 仍从同一份 lib 拿裁切（没有第二份实现）",
            "from pexels_crop import" in ver_src and "crop_3x4" in ver_src)
expect_true("这条测试自己引用了 replay_width（否则门禁换了地方测试就成了空断言）",
            "replay_width" in open(os.path.abspath(__file__), encoding="utf-8").read())

shutil.rmtree(TMP, ignore_errors=True)
if os.path.exists(MAN):
    os.remove(MAN)
if FAILS:
    print(f"\n❌ {len(FAILS)} 项不成立")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("\n✅ 重放档位回归全部通过")
